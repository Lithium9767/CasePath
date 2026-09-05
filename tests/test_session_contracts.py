"""增量合同与已有 v1.1 合同共存，示例、导出 Schema 和运行模型必须一致。"""

import json
from pathlib import Path

import pytest

from casepath.contracts import AnswerInterpretation, CreateSessionRequest
from casepath.contracts.registry import CONTRACTS


@pytest.mark.parametrize("name", ["create-session-request", "answer-interpretation"])
def test_new_examples_and_versions(name):
    model = CONTRACTS[name]
    payload = json.loads(Path(f"contracts/examples/{name}.json").read_text(encoding="utf-8"))
    assert model.model_validate(payload).contract_version == "1.2"
    payload["contract_version"] = "1.1"
    with pytest.raises(ValueError):
        model.model_validate(payload)


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
