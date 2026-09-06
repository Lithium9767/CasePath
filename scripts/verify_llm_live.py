"""验收真实 LLM：一条成功请求进模型，一条失败路径稳定处理且不写会话。"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from casepath.adapters.retrieval_catalog import load_canonical_catalog
from casepath.contracts import ErrorResponse, WorkflowSnapshot

ROOT = Path(__file__).resolve().parents[1]
QUERY = "我在健身房办了卡，已经付款，教练一直没上课，还有未消费余额，能否解除合同退费？"
BASE = os.getenv("CASEPATH_VERIFY_API", "http://127.0.0.1:8000")


def _http(method: str, path: str, payload: dict | None = None, timeout: float = 90) -> tuple[int, dict]:
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = Request(
        f"{BASE}{path}",
        data=data,
        method=method,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8")
            return response.status, json.loads(body) if body else {}
    except HTTPError as exc:
        body = exc.read().decode("utf-8")
        return exc.code, json.loads(body) if body else {}


def verify_success() -> str:
    status, caps = _http("GET", "/v1/capabilities", timeout=10)
    if status != 200:
        raise SystemExit(f"capabilities http {status}")
    llm = next(item for item in caps if item["capability"] == "llm")
    projector = next(item for item in caps if item["capability"] == "condition_projector")
    if not llm["available"] or llm["mode"] != "LIVE":
        raise SystemExit("running app llm is not LIVE; this is not a real model call")
    if projector["mode"] != "LIVE":
        raise SystemExit("condition_projector is not LIVE")

    started = time.perf_counter()
    status, payload = _http("POST", "/v1/sessions", {"query": QUERY})
    elapsed = time.perf_counter() - started
    if status != 201:
        raise SystemExit(f"create session failed http {status} reason={payload.get('details')}")
    snapshot = WorkflowSnapshot.model_validate(payload)
    state = snapshot.query_state
    catalog = load_canonical_catalog(ROOT / "data")
    known_conditions = set(catalog.conditions)
    invented = [item.condition_id for item in state.condition_states if item.condition_id not in known_conditions]
    if invented:
        raise SystemExit(f"invented condition ids: {invented}")
    if elapsed < 0.4:
        raise SystemExit(f"create session returned too quickly ({elapsed:.3f}s); model may not have been called")
    reasons = sorted({reason for item in state.condition_states for reason in item.mapping_reasons})
    print("mapping_reasons:", " | ".join(reasons) if reasons else "(none)")

    query_text = state.initial_query
    for fact in state.user_facts:
        if fact.source_turn == 0 and fact.text not in query_text and fact.text != query_text:
            raise SystemExit("initial fact is not taken from the user query")
    fact_texts = {item.fact_id: item.text for item in state.user_facts}
    for condition in state.condition_states:
        if condition.status.value == "UNKNOWN":
            continue
        if not condition.supporting_fact_ids and not condition.evidence:
            raise SystemExit(f"{condition.condition_id} decided without user facts")
        for evidence in condition.evidence:
            owner = fact_texts.get(evidence.fact_id)
            if owner is None:
                raise SystemExit(f"{condition.condition_id} references missing fact")
            if evidence.fact_id.startswith("fact.") and owner not in query_text and owner != query_text:
                raise SystemExit(f"{condition.condition_id} quote is not in the user query")

    session_id = state.session_id
    again_status, again = _http("GET", f"/v1/sessions/{session_id}")
    if again_status != 200:
        raise SystemExit("successful session was not readable")
    if again["query_state"]["session_id"] != session_id:
        raise SystemExit("session identity changed after create")
    print(
        f"success: session={session_id} elapsed={elapsed:.2f}s "
        f"conditions={len(state.condition_states)} rules={len(snapshot.retrieval_bundle.rule_refs)} "
        f"decided={sum(item.status.value != 'UNKNOWN' for item in state.condition_states)}"
    )
    return session_id


def verify_auth_failure() -> None:
    from fastapi.testclient import TestClient

    from casepath.adapters.p4_factory import HybridCypherP4Factory
    from casepath.api.main import create_live_app
    from casepath.config import RuntimeSettings

    settings = RuntimeSettings(
        mode="live",
        llm_required=True,
        llm_api_key="sk-invalid-casepath-acceptance",
        llm_base_url="https://api.deepseek.com",
        llm_model="deepseek-v4-flash",
        llm_structured_output="json_object",
        llm_timeout_seconds=20,
        llm_max_retries=0,
        neo4j_password=None,
    )
    app = create_live_app(HybridCypherP4Factory(data_root=ROOT / "data"), settings=settings)
    try:
        with TestClient(app) as client:
            response = client.post("/v1/sessions", json={"query": QUERY})
            if response.status_code != 503:
                raise SystemExit(f"auth failure expected 503, got {response.status_code}")
            error = ErrorResponse.model_validate(response.json())
            if error.details.get("reason") != "language_model_unavailable":
                raise SystemExit(f"unstable failure reason: {error.details}")
            if "sk-invalid" in response.text or "deepseek" in response.text.lower():
                raise SystemExit("failure response leaked provider or key")
            leaked_session = client.get(f"/v1/sessions/{error.request_id}")
            if leaked_session.status_code != 404:
                raise SystemExit("failed create wrote a session")
            print("auth failure: 503 language_model_unavailable; no session written")
    finally:
        close = getattr(app.state, "live_runtime", None)
        if close is not None:
            close.close()


def verify_illegal_json_failure() -> None:
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from threading import Thread

    from casepath.adapters.semantic_condition_projector import ConditionLinkBatch
    from casepath.adapters.structured_llm import OpenAICompatibleStructuredLanguageModel
    from casepath.application.errors import InvalidComponentOutput, LanguageModelUnavailable
    from casepath.config import StructuredOutputMode

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"choices":[{"message":{"content":"{not-json"}}]}')

        def log_message(self, format: str, *args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    Thread(target=server.serve_forever, daemon=True).start()
    client = OpenAICompatibleStructuredLanguageModel.connect(
        api_key="sk-unused",
        base_url=f"http://127.0.0.1:{server.server_address[1]}/v1",
        model="stub",
        timeout_seconds=5,
        max_retries=0,
        max_completion_tokens=64,
        output_mode=StructuredOutputMode.JSON_OBJECT,
    )
    try:
        client.generate_structured(
            system_prompt="x",
            user_prompt="y",
            response_model=ConditionLinkBatch,
        )
    except (InvalidComponentOutput, LanguageModelUnavailable):
        print("illegal json: stable contract/model error; no session involved")
    else:
        raise SystemExit("illegal json was accepted")
    finally:
        client.close()
        server.shutdown()


def main() -> None:
    os.chdir(ROOT)
    mode = sys.argv[1] if len(sys.argv) > 1 else "all"
    if mode in {"all", "success"}:
        verify_success()
    if mode in {"all", "fail"}:
        verify_auth_failure()
        verify_illegal_json_failure()


if __name__ == "__main__":
    main()
