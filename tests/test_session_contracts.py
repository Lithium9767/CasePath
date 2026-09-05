"""增量合同与已有 v1.1 合同共存，示例、导出 Schema 和运行模型必须一致。"""

import json
from pathlib import Path

import pytest

from casepath.contracts import AnswerInterpretation, CreateSessionRequest
from casepath.contracts.registry import CONTRACTS


def test_create_session_request_remains_v1_2():
    model = CONTRACTS["create-session-request"]
    payload = json.loads(
        Path("contracts/examples/create-session-request.json").read_text(encoding="utf-8")
    )
    assert model.model_validate(payload).contract_version == "1.2"
    payload["contract_version"] = "1.1"
    with pytest.raises(ValueError):
        model.model_validate(payload)


def test_answer_interpretation_defaults_to_v1_3_and_accepts_v1_2():
    payload = json.loads(
        Path("contracts/examples/answer-interpretation.json").read_text(encoding="utf-8")
    )
    assert AnswerInterpretation.model_validate(payload).contract_version == "1.3"
    legacy = {"contract_version": "1.2", "new_facts": [], "condition_updates": []}
    assert AnswerInterpretation.model_validate(legacy).contract_version == "1.2"


@pytest.mark.parametrize("name", list(CONTRACTS))
def test_exported_schema_matches_runtime(name):
    stored = json.loads(Path(f"contracts/schemas/{name}.schema.json").read_text(encoding="utf-8"))
    assert stored == CONTRACTS[name].model_json_schema()


def test_query_preserves_raw_text_and_rejects_blank():
    assert CreateSessionRequest(query=" 原始问题 ").query == " 原始问题 "
    with pytest.raises(ValueError):
        CreateSessionRequest(query="\n ")


def test_interpretation_rejects_duplicate_conditions():
    update = {"condition_id": "cond.test"}
    with pytest.raises(ValueError):
        AnswerInterpretation(condition_updates=[update, update])
