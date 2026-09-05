import json
from pathlib import Path

import pytest

from casepath.contracts import (
    AnswerRequest,
    CapabilityStatus,
    CaseRecord,
    ErrorResponse,
    ExplanationPlan,
    LegalSourceRecord,
    ProvisionRecord,
    QueryState,
    RetrievalBundle,
    RuleRecord,
    WorkflowSnapshot,
)

EXAMPLES = Path("contracts/examples")


@pytest.mark.parametrize(
    ("filename", "model"),
    [
        ("legal-source-record.json", LegalSourceRecord),
        ("provision-record.json", ProvisionRecord),
        ("rule-record.json", RuleRecord),
        ("case-record.json", CaseRecord),
        ("query-state.json", QueryState),
        ("retrieval-bundle.json", RetrievalBundle),
        ("explanation-plan.json", ExplanationPlan),
        ("answer-request.json", AnswerRequest),
        ("error-response.json", ErrorResponse),
        ("capability-status.json", CapabilityStatus),
        ("workflow-snapshot.json", WorkflowSnapshot),
    ],
)
def test_examples_match_frozen_contracts(filename, model):
    payload = json.loads((EXAMPLES / filename).read_text(encoding="utf-8"))
    parsed = model.model_validate(payload)
    assert parsed.contract_version == payload["contract_version"]


def test_contracts_reject_unknown_fields():
    payload = json.loads((EXAMPLES / "query-state.json").read_text(encoding="utf-8"))
    payload["invented_field"] = "must fail"
    with pytest.raises(ValueError):
        QueryState.model_validate(payload)


def test_rule_contract_rejects_unknown_group_members():
    payload = json.loads((EXAMPLES / "rule-record.json").read_text(encoding="utf-8"))
    payload["condition_groups"][0]["member_condition_ids"] = ["cond.does_not_exist"]
    with pytest.raises(ValueError, match="unknown conditions"):
        RuleRecord.model_validate(payload)


def test_rule_contract_rejects_ungrouped_conditions():
    payload = json.loads((EXAMPLES / "rule-record.json").read_text(encoding="utf-8"))
    payload["condition_groups"] = []
    with pytest.raises(ValueError, match="not assigned to a group"):
        RuleRecord.model_validate(payload)


@pytest.mark.parametrize(
    ("filename", "model"),
    [
        ("legal-source-record.json", LegalSourceRecord),
        ("provision-record.json", ProvisionRecord),
        ("rule-record.json", RuleRecord),
        ("case-record.json", CaseRecord),
        ("query-state.json", QueryState),
        ("retrieval-bundle.json", RetrievalBundle),
        ("explanation-plan.json", ExplanationPlan),
        ("answer-request.json", AnswerRequest),
        ("error-response.json", ErrorResponse),
        ("capability-status.json", CapabilityStatus),
        ("workflow-snapshot.json", WorkflowSnapshot),
    ],
)
def test_v1_1_contracts_reject_wrong_version(filename, model):
    payload = json.loads((EXAMPLES / filename).read_text(encoding="utf-8"))
    payload["contract_version"] = "9.9"
    with pytest.raises(ValueError):
        model.model_validate(payload)


def test_case_contract_rejects_unknown_claim_reference():
    payload = json.loads((EXAMPLES / "case-record.json").read_text(encoding="utf-8"))
    payload["decisions"][0]["claim_id"] = "claim.does_not_exist"
    with pytest.raises(ValueError, match="unknown claim"):
        CaseRecord.model_validate(payload)


def test_query_contract_rejects_unknown_fact_reference():
    payload = json.loads((EXAMPLES / "query-state.json").read_text(encoding="utf-8"))
    payload["condition_states"] = [
        {
            "condition_id": "cond.performance_impossible",
            "status": "SATISFIED",
            "supporting_fact_ids": ["fact.does_not_exist"],
            "last_updated_turn": 0,
        }
    ]
    with pytest.raises(ValueError, match="unknown user facts"):
        QueryState.model_validate(payload)


def test_workflow_snapshot_rejects_mismatched_session():
    payload = json.loads((EXAMPLES / "workflow-snapshot.json").read_text(encoding="utf-8"))
    payload["explanation_plan"]["session_id"] = "another-session"
    with pytest.raises(ValueError, match="same session_id"):
        WorkflowSnapshot.model_validate(payload)


def test_answer_request_rejects_frontend_condition_status():
    payload = json.loads((EXAMPLES / "answer-request.json").read_text(encoding="utf-8"))
    payload["status"] = "SATISFIED"
    with pytest.raises(ValueError):
        AnswerRequest.model_validate(payload)
