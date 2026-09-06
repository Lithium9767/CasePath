"""会话 HTTP 路由：只负责传输，不在这里判断法律条件。"""

from typing import Annotated

from fastapi import APIRouter, Depends, Request

from casepath.application.session_service import SessionService
from casepath.contracts import AnswerRequest, CreateSessionRequest, WorkflowSnapshot

router = APIRouter(prefix="/v1/sessions", tags=["sessions"])


def get_session_service(request: Request) -> SessionService:
    # 服务由应用工厂只创建一次，不能在每次请求中重新创建内存仓库。
    return request.app.state.session_service


Service = Annotated[SessionService, Depends(get_session_service)]


@router.post("", response_model=WorkflowSnapshot, status_code=201)
def create_session(request: CreateSessionRequest, service: Service) -> WorkflowSnapshot:
    return service.create_session(request)


@router.get("/{session_id}", response_model=WorkflowSnapshot)
def get_session(session_id: str, service: Service) -> WorkflowSnapshot:
    return service.get_session(session_id)


@router.post("/{session_id}/answers", response_model=WorkflowSnapshot)
def submit_answer(session_id: str, request: AnswerRequest, service: Service) -> WorkflowSnapshot:
    return service.submit_answer(session_id, request)
