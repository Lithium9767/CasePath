import json
from pathlib import Path

import pytest

from casepath.contracts import (
    CaseRecord,
    ExplanationPlan,
    QueryState,
    RetrievalBundle,
    RuleRecord,
)

EXAMPLES = Path("contracts/examples")


@pytest.mark.parametrize(
    ("filename", "model"),
    [
        ("rule-record.json", RuleRecord),
        ("case-record.json", CaseRecord),
        ("query-state.json", QueryState),
        ("retrieval-bundle.json", RetrievalBundle),
        ("explanation-plan.json", ExplanationPlan),
    ],
)
def test_examples_match_frozen_contracts(filename, model):
    payload = json.loads((EXAMPLES / filename).read_text(encoding="utf-8"))
    parsed = model.model_validate(payload)
    assert parsed.contract_version == "1.0"


def test_contracts_reject_unknown_fields():
    payload = json.loads((EXAMPLES / "query-state.json").read_text(encoding="utf-8"))
    payload["invented_field"] = "must fail"
    with pytest.raises(ValueError):
        QueryState.model_validate(payload)
