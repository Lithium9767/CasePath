"""P5 端到端验收：正式会话接口上的完整事实、追问、不清楚、否定/假设、故障与案例不足。"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi.testclient import TestClient

from casepath.adapters.p4_factory import HybridCypherP4Factory
from casepath.api.main import create_live_app
from casepath.config import RuntimeMode, RuntimeSettings
from casepath.contracts import WorkflowSnapshot

ROOT = Path(__file__).resolve().parents[1]
COMPLETE = "我在健身房办了卡，已经付款，还有未消费余额，对方已经无法继续履行，合同尚未解除，能否退费？"
INCOMPLETE = "健身卡办了，已经付款，能不能退？"
HYPOTHETICAL = "如果健身房关门了，我想退费，但现在还没关门。"


def _app(settings: RuntimeSettings | None = None) -> TestClient:
    return TestClient(
        create_live_app(HybridCypherP4Factory(data_root=ROOT / "data"), settings=settings)
    )


def _status(snapshot: WorkflowSnapshot, condition_id: str) -> str:
    for item in snapshot.query_state.condition_states:
        if item.condition_id == condition_id:
            return item.status.value
    return "MISSING"


def _shortage(text: str) -> bool:
    return "案例证据不足" in text and "没有相关法律依据" in text


def main() -> None:
    os.chdir(ROOT)
    settings = RuntimeSettings.from_env().model_copy(update={"mode": RuntimeMode.LIVE})
    with _app(settings) as client:
        caps = client.get("/v1/capabilities")
        assert caps.status_code == 200, caps.text
        names = {item["capability"] for item in caps.json()}
        assert {"llm", "legal_graph", "condition_projector"} <= names

        complete = client.post("/v1/sessions", json={"query": COMPLETE})
        assert complete.status_code == 201, complete.text
        snap = WorkflowSnapshot.model_validate(complete.json())
        assert snap.retrieval_bundle.rule_refs, "complete facts produced no rules"
        assert _status(snap, "cond.contract_exists") == "SATISFIED"
        assert _status(snap, "cond.payment_made") == "SATISFIED"
        case_n = (
            len(snap.retrieval_bundle.support_case_refs)
            + len(snap.retrieval_bundle.limiting_case_refs)
            + len(snap.retrieval_bundle.boundary_case_refs)
        )
        if case_n == 0:
            assert _shortage(snap.explanation_plan.main_explanation), snap.explanation_plan.main_explanation
        print(
            "complete:",
            f"rules={len(snap.retrieval_bundle.rule_refs)}",
            f"cases={case_n}",
            f"question={bool(snap.next_question)}",
        )

        asking = snap
        if asking.next_question is None:
            first = client.post("/v1/sessions", json={"query": INCOMPLETE})
            assert first.status_code == 201, first.text
            asking = WorkflowSnapshot.model_validate(first.json())
        if asking.next_question is None:
            first = client.post("/v1/sessions", json={"query": "想退健身卡的钱"})
            assert first.status_code == 201, first.text
            asking = WorkflowSnapshot.model_validate(first.json())
        session_id = asking.query_state.session_id
        assert asking.next_question is not None, "baseline should produce a follow-up question"
        unknown_id = asking.next_question.condition_id
        before = _status(asking, unknown_id)
        unclear = client.post(
            f"/v1/sessions/{session_id}/answers",
            json={
                "question_id": asking.next_question.question_id,
                "condition_id": unknown_id,
                "answer": "不清楚",
                "selected_option": "不清楚",
            },
        )
        assert unclear.status_code == 200, unclear.text
        after = WorkflowSnapshot.model_validate(unclear.json())
        assert _status(after, unknown_id) == "UNKNOWN"
        assert after.query_state.session_id == session_id
        print(f"unclear: {unknown_id} stayed {before}->{_status(after, unknown_id)}")

        hypo = client.post("/v1/sessions", json={"query": HYPOTHETICAL})
        assert hypo.status_code == 201, hypo.text
        hypo_snap = WorkflowSnapshot.model_validate(hypo.json())
        assert _status(hypo_snap, "cond.performance_impossible") != "SATISFIED"
        print("hypothetical: performance_impossible not flipped to SATISFIED")

        broken = client.post(
            f"/v1/sessions/{session_id}/answers",
            json={
                "question_id": "question.wrong",
                "condition_id": unknown_id,
                "answer": "是",
            },
        )
        assert broken.status_code == 409, broken.text
        replay = client.get(f"/v1/sessions/{session_id}")
        assert replay.status_code == 200
        assert replay.json()["query_state"]["session_id"] == session_id
        print("conflict: session preserved")

    offline = RuntimeSettings(mode="live", llm_required=False, neo4j_password=None)
    with _app(offline) as client:
        response = client.post("/v1/sessions", json={"query": COMPLETE})
        assert response.status_code == 201, response.text
        snap = WorkflowSnapshot.model_validate(response.json())
        assert snap.retrieval_bundle.rule_refs
        print("offline graph/llm: session still created with rule basis")

    print("p5 e2e scenarios passed")


if __name__ == "__main__":
    main()
