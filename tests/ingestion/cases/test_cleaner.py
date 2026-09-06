import json
from pathlib import Path

from casepath.contracts import CaseIndexRecord, DecisionStatus, MaturityLevel
from casepath.ingestion.cases.classify import is_service_termination_refund
from casepath.ingestion.cases.cleaner import CaseCleaner, split_sections
from casepath.ingestion.cases.ids import PROPERTY_CASE_ID
from casepath.rule_layer.ids import RULE_SERVICE_TERMINATION_REFUND


def test_split_sections_preserves_headings():
    sections = split_sections("# x\n\n## 基本案情\n事实\n## 裁判理由\n理由")
    assert sections["基本案情"] == "事实"
    assert sections["裁判理由"] == "理由"


def test_filters_only_civil_and_writes_jsonl(tmp_path: Path):
    src = tmp_path / "cases.json"
    src.write_text(
        json.dumps(
            [
                {
                    "case_id": "civil.one",
                    "title": "民事案",
                    "case_category": "民事",
                    "case_cause": "合同",
                    "case_facts": "事实",
                },
                {"case_id": "criminal.one", "title": "刑事案", "case_category": "刑事"},
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    out = tmp_path / "cases.jsonl"
    assert CaseCleaner(src).write(out) == 1
    rows = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
    record = CaseIndexRecord.model_validate(rows[0])
    assert record.title == "民事案"
    assert record.cause == "合同"
    assert record.contract_version == "1.3"


def test_deep_record_uses_unknown_when_result_missing():
    cleaner = CaseCleaner(Path("/tmp/nonexistent"))
    record = cleaner.deep_fitness(
        {
            "case_id": "x",
            "title": "健身案",
            "case_cause": "服务合同纠纷",
            "full_document": "## 基本案情\n事实\n## 裁判理由\n理由",
        }
    )
    assert record.decisions[0].status == DecisionStatus.UNKNOWN
    assert record.maturity == MaturityLevel.L1
    assert all(item.human_verified is False for item in record.condition_findings)
    assert len({span.span_id for span in record.source_spans}) == len(record.source_spans)
    assert record.claims[0].claim_id == "x.claim.001"


def test_property_damages_case_does_not_use_refund_template():
    case = {
        "case_id": PROPERTY_CASE_ID,
        "title": "刘某建诉某福州市某物业管理有限责任公司物业服务合同纠纷案",
        "case_cause": "物业服务合同纠纷",
        "case_facts": (
            "刘某建主张物业未及时处理腐烂树木，维修费用人民币2750元，要求赔偿损失。"
        ),
    }
    assert is_service_termination_refund(case, case["case_facts"]) is False
    cleaner = CaseCleaner(Path("/tmp/nonexistent"))
    assert cleaner.is_service_termination_refund_case(case) is False
    record = cleaner.deep_fitness(case)
    assert RULE_SERVICE_TERMINATION_REFUND not in record.claims[0].invoked_rule_ids
    assert record.claims[0].requested_remedy != "解除合同并处理未履行服务费用"


def test_machine_bridges_are_not_human_verified():
    cleaner = CaseCleaner(Path("/tmp/nonexistent"))
    record = cleaner.deep_fitness(
        {
            "case_id": "gym.stub",
            "title": "曾某诉武汉某健身管理有限公司服务合同纠纷案",
            "case_cause": "服务合同纠纷",
            "full_document": (
                "## 基本案情\n"
                "曾某诉称：请求判令解除《会员协议》并返还24560元。\n"
                "被告辩称：可安置到另一会所。\n"
                "法院经审理查明：双方签订《会员协议》，曾某购买会籍服务。"
                "门店关闭。另一会所距离较远。\n"
                "湖北省武汉市硚口区人民法院作出民事判决：一、解除《会员协议》；"
                "二、返还16370元。\n"
                "## 裁判理由\n"
                "原告要求解除合同，符合法律规定，应返还不能提供服务的课程费用。"
            ),
        }
    )
    assert record.maturity == MaturityLevel.L1
    assert all(item.human_verified is False for item in record.condition_findings)
    assert record.decisions[0].status == DecisionStatus.PARTIALLY_GRANTED
    assert record.decisions[0].amount == 16370
    assert "诉称" not in "".join(item.predicate for item in record.findings)
    assert "辩称" not in "".join(item.predicate for item in record.findings)
