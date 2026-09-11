"""会话 HTTP 路由：只负责传输，不在这里判断法律条件。"""

from typing import Annotated

from fastapi import APIRouter, Depends, Request

from casepath.application.session_service import SessionService
from casepath.contracts import AnswerRequest, CreateSessionRequest, WorkflowSnapshot

router = APIRouter(prefix="/v1/sessions", tags=["sessions"])


def get_session_service(request: Request) -> SessionService:
    # 【请求 1】Depends 让 FastAPI 在进入路由前调用本函数，
    # 取回启动时 main.create_app 保存的 service，而不是每个请求重新装配客户端。
    # 服务由应用工厂只创建一次，不能在每次请求中重新创建内存仓库。
    return request.app.state.session_service


Service = Annotated[SessionService, Depends(get_session_service)]


@router.post("", response_model=WorkflowSnapshot, status_code=201)
def create_session(request: CreateSessionRequest, service: Service) -> WorkflowSnapshot:
    # 【首次请求 01：HTTP 入口】路由前缀 /v1/sessions 加上这里的空路径，就是 POST /v1/sessions。
    # FastAPI 将请求 JSON 校验为 CreateSessionRequest，并通过 Depends 取得启动时的 service。
    # 【首次请求 02：进入服务】下一行跳到 application/session_service.py 的 create_session。
    # 注意：路由函数和服务方法虽然同名，但路由只负责传输，服务负责状态和调用工作流。
    # 【首次请求 14：HTTP 返回】下面的方法完成 03—13 后返回 WorkflowSnapshot；
    # FastAPI 按 response_model 验证/序列化为 JSON，以 201 状态返回前端，不在路由里手工 dumps。
    return service.create_session(request)


@router.get("/{session_id}", response_model=WorkflowSnapshot)
def get_session(session_id: str, service: Service) -> WorkflowSnapshot:
    # 【读取旧结果】仅取已保存快照，不运行检索，也不是“跳过当前追问”。
    return service.get_session(session_id)


@router.post("/{session_id}/answers", response_model=WorkflowSnapshot)
def submit_answer(session_id: str, request: AnswerRequest, service: Service) -> WorkflowSnapshot:
    # 【补充回答】下一站 SessionService.submit_answer：校验当前问题、解释回答、重跑单轮。
    return service.submit_answer(session_id, request)
