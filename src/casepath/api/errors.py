"""统一错误响应；保留 v1.1 错误枚举，通过 details.reason 区分会话错误。"""

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
        request_id=str(uuid4()),
    )
    return JSONResponse(status_code=status, content=payload.model_dump(mode="json"))


def register_error_handlers(app: FastAPI) -> None:
    async def application_error(request: Request, exc: Exception) -> JSONResponse:
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
        # 不暴露堆栈、连接信息或组件异常中的用户原文。
        return error_response(500, ErrorCode.INTERNAL_ERROR, "服务处理失败", "internal_error")
