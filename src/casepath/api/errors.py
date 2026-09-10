"""统一错误响应；保留 v1.1 错误枚举，通过 details.reason 区分会话错误。"""

import logging
from contextvars import ContextVar
from pathlib import PurePath
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from casepath.application.errors import (
    AnswerInterpreterUnavailable,
    GraphUnavailable,
    InvalidAnswer,
    InvalidComponentOutput,
    LanguageModelUnavailable,
    RetrieverUnavailable,
    SessionConflict,
    SessionNotFound,
)
from casepath.contracts import ErrorCode, ErrorResponse

# 请求级上下文隔离并发请求；不采用客户端传来的 ID，避免日志注入。
_request_id: ContextVar[str | None] = ContextVar("diagnostic_request_id", default=None)


def log_failure(exc: Exception) -> None:
    """仅记录异常类型和代码位置，不记录异常正文、局部变量、提示词或密钥。

    业务校验失败通常没有 SDK 警告，因此也必须在 HTTP 异常边界记录。
    cause 链可以区分网络错误和 Pydantic 校验错误；位置用于定位具体校验分支。
    """
    request_id = _request_id.get() or str(uuid4())
    _request_id.set(request_id)
    chain = []
    locations = []
    stage = "unknown"
    stage_files = {
        "query_expansion.py": "query_expansion",
        "semantic_condition_projector.py": "projection",
        "semantic_answer_interpreter.py": "answer",
        "key_condition_policy.py": "question",
        "grounded_explanation.py": "explanation",
    }
    seen = set()
    current = exc
    while current is not None and id(current) not in seen and len(chain) < 8:
        seen.add(id(current))
        chain.append(type(current).__name__)
        tb = current.__traceback__
        while tb is not None:
            filename = tb.tb_frame.f_code.co_filename
            stage = stage_files.get(PurePath(filename).name, stage)
            if "casepath" in filename.lower():
                locations.append(f"{PurePath(filename).name}:{tb.tb_lineno}")
            tb = tb.tb_next
        current = current.__cause__ or current.__context__
    # 使用 uvicorn.error 的既有控制台处理器，无需另设日志级别即可看到错误。
    logging.getLogger("uvicorn.error").error(
        "CasePath request_id=%s stage=%s errors=%s locations=%s",
        request_id, stage, " <- ".join(chain), " > ".join(locations[-20:]),
    )


def error_response(
    status: int,
    code: ErrorCode,
    message: str,
    reason: str,
    *,
    retryable: bool = False,
) -> JSONResponse:
    payload = ErrorResponse(
        code=code,
        message=message,
        details={"reason": reason},
        retryable=retryable,
        request_id=_request_id.get() or str(uuid4()),
    )
    return JSONResponse(status_code=status, content=payload.model_dump(mode="json"))


def register_error_handlers(app: FastAPI) -> None:
    @app.middleware("http")
    async def diagnostic_context(request: Request, call_next):
        token = _request_id.set(str(uuid4()))
        try:
            response = await call_next(request)
            response.headers["X-Request-ID"] = _request_id.get() or ""
            return response
        finally:
            _request_id.reset(token)

    async def application_error(request: Request, exc: Exception) -> JSONResponse:
        log_failure(exc)
        if isinstance(exc, SessionNotFound):
            return error_response(404, ErrorCode.SESSION_NOT_FOUND, "会话不存在", "session_missing")
        if isinstance(exc, SessionConflict):
            return error_response(409, ErrorCode.INVALID_REQUEST, str(exc), "session_conflict")
        if isinstance(exc, InvalidAnswer):
            return error_response(422, ErrorCode.INVALID_REQUEST, str(exc), "invalid_answer")
        if isinstance(exc, AnswerInterpreterUnavailable):
            return error_response(
                503,
                ErrorCode.INTERNAL_ERROR,
                "回答解析能力暂不可用，原会话未修改",
                "answer_interpreter_unavailable",
                retryable=True,
            )
        if isinstance(exc, GraphUnavailable):
            return error_response(
                503,
                ErrorCode.GRAPH_UNAVAILABLE,
                "法律图检索能力暂不可用",
                "legal_graph_unavailable",
                retryable=True,
            )
        if isinstance(exc, RetrieverUnavailable):
            return error_response(
                503,
                ErrorCode.RETRIEVER_UNAVAILABLE,
                "法律检索能力暂不可用",
                "retriever_unavailable",
                retryable=True,
            )
        if isinstance(exc, LanguageModelUnavailable):
            return error_response(
                503,
                ErrorCode.INTERNAL_ERROR,
                "结构化语言模型能力暂不可用",
                "language_model_unavailable",
                retryable=True,
            )
        return error_response(
            500,
            ErrorCode.INTERNAL_ERROR,
            "组件输出未通过校验，原会话未修改",
            "invalid_component_output",
        )

    for error_type in (
        SessionNotFound,
        SessionConflict,
        InvalidAnswer,
        AnswerInterpreterUnavailable,
        InvalidComponentOutput,
        RetrieverUnavailable,
        GraphUnavailable,
        LanguageModelUnavailable,
    ):
        app.add_exception_handler(error_type, application_error)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        version_error = any("contract_version" in item["loc"] for item in exc.errors())
        code = ErrorCode.CONTRACT_MISMATCH if version_error else ErrorCode.INVALID_REQUEST
        # 不回显 Pydantic 的 input 字段，避免把用户完整事实写进错误响应。
        return error_response(422, code, "请求字段或合同版本不合法", "request_validation_failed")

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException) -> JSONResponse:
        response = error_response(
            exc.status_code, ErrorCode.INVALID_REQUEST, "请求路径或方法不可用", "http_error"
        )
        if exc.headers:
            response.headers.update(exc.headers)
        return response

    @app.exception_handler(Exception)
    async def unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        log_failure(exc)
        # 不暴露堆栈、连接信息或组件异常中的用户原文。
        return error_response(500, ErrorCode.INTERNAL_ERROR, "服务处理失败", "internal_error")
