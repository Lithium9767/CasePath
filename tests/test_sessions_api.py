"""P5 仅通过 HTTP 完成闭环；同时验证错误结构和演示能力标识。"""

import pytest
from fastapi.testclient import TestClient

from casepath.api.main import create_app
from casepath.bootstrap import build_demo_session_service
from casepath.contracts import ErrorResponse, WorkflowSnapshot


@pytest.fixture
def client():
    with TestClient(create_app(), raise_server_exceptions=False) as instance:
        yield instance


def create(client):
    response = client.post("/v1/sessions", json={"query": "健身房关门了，还有余额"})
    assert response.status_code == 201
    snapshot = response.json()
    WorkflowSnapshot.model_validate(snapshot)
    question = snapshot["next_question"]
    return snapshot["query_state"]["session_id"], {
        "contract_version": "1.1",
        "question_id": question["question_id"],
        "condition_id": question["condition_id"],
        "answer": "不清楚",
        "selected_option": "不清楚",
    }


def assert_error(response, status):
    assert response.status_code == status
    payload = ErrorResponse.model_validate(response.json())
    assert payload.request_id
    return payload


def test_http_create_answer_refresh_and_replay(client):
    session_id, answer = create(client)
    path = f"/v1/sessions/{session_id}"
    first = client.post(f"{path}/answers", json=answer)
    assert first.status_code == 200
    assert client.get(path).json() == first.json()
    assert client.post(f"{path}/answers", json=answer).json() == first.json()
    snapshot = WorkflowSnapshot.model_validate(first.json())
    assert len(snapshot.query_state.dialogue_history) == 1
    assert snapshot.query_state.dialogue_history[0].answer == "不清楚"
    assert snapshot.next_question is None
    assert snapshot.retrieval_bundle.degraded
    assert not any(citation.verified for citation in snapshot.explanation_plan.citations)


@pytest.mark.parametrize(
    "body", [{}, {"query": ""}, {"query": "  "}, {"query": "问题", "session_id": "fake"}]
)
def test_create_validation(client, body):
    assert_error(client.post("/v1/sessions", json=body), 422)


def test_missing_session_and_wrong_version(client):
    assert_error(client.get("/v1/sessions/missing"), 404)
    error = assert_error(
        client.post(
            "/v1/sessions",
            json={
                "query": "问题",
                "contract_version": "9.9",
            },
        ),
        422,
    )
    assert error.code == "CASEPATH_CONTRACT_MISMATCH"


def test_answer_rejects_frontend_status_and_preserves_session(client):
    session_id, answer = create(client)
    path = f"/v1/sessions/{session_id}"
    before = client.get(path).json()
    assert_error(client.post(f"{path}/answers", json={**answer, "status": "SATISFIED"}), 422)
    assert_error(client.post(f"{path}/answers", json={**answer, "answer": "  "}), 422)
    assert client.get(path).json() == before


def test_wrong_question_and_changed_duplicate_are_conflicts(client):
    session_id, answer = create(client)
    path = f"/v1/sessions/{session_id}/answers"
    assert_error(client.post(path, json={**answer, "question_id": "wrong"}), 409)
    assert client.post(path, json=answer).status_code == 200
    assert_error(client.post(path, json={**answer, "answer": "换一条回答"}), 409)


def test_unavailable_interpreter_returns_503_without_saving():
    service = build_demo_session_service()
    service.answer_interpreter = None
    with TestClient(create_app(service), raise_server_exceptions=False) as client:
        session_id, answer = create(client)
        path = f"/v1/sessions/{session_id}"
        before = client.get(path).json()
        error = assert_error(client.post(f"{path}/answers", json=answer), 503)
        assert error.details["reason"] == "answer_interpreter_unavailable"
        assert client.get(path).json() == before


def test_internal_error_does_not_leak_component_message(client):
    session_id, answer = create(client)
    path = f"/v1/sessions/{session_id}"
    before = client.get(path).json()

    class BrokenWorkflow:
        def run(self, state):
            raise RuntimeError("secret-connection-password")

    client.app.state.session_service.workflow = BrokenWorkflow()
    response = client.post(f"{path}/answers", json=answer)
    assert_error(response, 500)
    assert "secret-connection-password" not in response.text
    assert client.get(path).json() == before


def test_app_instances_do_not_share_sessions(client):
    session_id, _ = create(client)
    with TestClient(create_app()) as other:
        assert_error(other.get(f"/v1/sessions/{session_id}"), 404)


def test_capabilities_schema_and_legacy_demo(client):
    assert client.get("/health").status_code == 200
    capabilities = {item["capability"]: item for item in client.get("/v1/capabilities").json()}
    assert capabilities["session_repository"]["mode"] == "MEMORY"
    assert not capabilities["session_repository"]["degraded"]
    assert capabilities["answer_interpreter"]["mode"] == "DEMO"
    assert not capabilities["citation_verification"]["available"]
    assert client.get("/v1/contracts/create-session-request/schema").status_code == 200
    assert client.get("/v1/contracts/answer-interpretation/schema").status_code == 200
    assert_error(client.get("/v1/contracts/missing/schema"), 404)
    assert_error(client.delete("/v1/sessions/missing"), 405)
    result = client.post("/v1/demo/analyze", json={"session_id": "legacy", "query": "健身房"})
    assert result.status_code == 200
    assert_error(client.get("/v1/sessions/legacy"), 404)
    openapi = client.get("/openapi.json")
    assert openapi.status_code == 200
    assert openapi.json()["paths"]["/v1/demo/analyze"]["post"]["deprecated"] is True
