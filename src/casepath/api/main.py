from fastapi import FastAPI
from pydantic import BaseModel, Field

from casepath.bootstrap import build_demo_workflow
from casepath.contracts import (
    CaseRecord,
    ExplanationPlan,
    QueryState,
    RetrievalBundle,
    RuleRecord,
)
from casepath.workflow import WorkflowSnapshot

app = FastAPI(title="CasePath API", version="0.1.0")
workflow = build_demo_workflow()


class AnalyzeRequest(BaseModel):
    session_id: str = Field(min_length=1)
    query: str = Field(min_length=1)


CONTRACTS = {
    "rule-record": RuleRecord,
    "case-record": CaseRecord,
    "query-state": QueryState,
    "retrieval-bundle": RetrievalBundle,
    "explanation-plan": ExplanationPlan,
}


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "version": "0.1.0"}


@app.post("/v1/demo/analyze", response_model=WorkflowSnapshot)
def analyze(request: AnalyzeRequest) -> WorkflowSnapshot:
    return workflow.run(QueryState(session_id=request.session_id, initial_query=request.query))


@app.get("/v1/contracts/{contract_name}/schema")
def contract_schema(contract_name: str) -> dict:
    model = CONTRACTS.get(contract_name)
    if model is None:
        return {"error": "unknown contract", "available": sorted(CONTRACTS)}
    return model.model_json_schema()
