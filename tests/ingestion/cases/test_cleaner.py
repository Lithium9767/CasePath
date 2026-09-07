import json
from pathlib import Path

from casepath.contracts import CaseIndexRecord, DecisionStatus, MaturityLevel
from casepath.ingestion.cases.classify import (
    is_service_contract_dispute,
    is_service_termination_refund,
)
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


def test_deep_record_omits_unanchored_holdings():
    cleaner = CaseCleaner(Path("/tmp/nonexistent"))
    record = cleaner.deep_fitness(
        {
            "case_id": "x",
            "title": "健身案",
            "case_cause": "服务合同纠纷",
            "full_document": "## 基本案情\n事实\n## 裁判理由\n理由",
        }
    )
    assert record.decisions == []
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
    assert is_service_contract_dispute(case, case["case_facts"]) is False
    cleaner = CaseCleaner(Path("/tmp/nonexistent"))
    assert cleaner.is_service_termination_refund_case(case) is False
    record = cleaner.deep_fitness(case)
    assert RULE_SERVICE_TERMINATION_REFUND not in record.claims[0].invoked_rule_ids
    assert record.claims[0].requested_remedy != "解除合同并处理未履行服务费用"


def test_machine_bridges_are_not_human_verified():
    from casepath.ingestion.cases.ids import GYM_CASE_ID
    from casepath.rule_layer.ids import COND_PAYMENT_MADE, COND_UNPERFORMED_BALANCE

    cleaner = CaseCleaner(Path("/tmp/nonexistent"))
    record = cleaner.deep_fitness(
        {
            "case_id": GYM_CASE_ID,
            "title": "曾某诉武汉某健身管理有限公司服务合同纠纷案",
            "case_cause": "服务合同纠纷",
            "full_document": (
                "## 基本案情\n"
                "曾某诉称：请求判令解除《会员协议》并返还24560元。\n"
                "被告辩称：可安置到另一会所。\n"
                "法院经审理查明：2019年，曾某与某健身管理公司签订了《会员协议》，约定曾某购买某健身管理公司的会员会籍服务以及私人教练服务，成为某健身管理公司硚口店专属会员，期限为两年。"
                "后某健身管理公司因自身经营原因，名下所有门店相继关闭，在对会员进行安置过程中，未经曾某同意，而将其会员会籍服务及私教服务转让给另一健身会所。"
                "该健身会所距离曾某路途较远，不便于曾某日常锻炼。\n"
                "湖北省武汉市硚口区人民法院作出民事判决：一、解除曾某与某健身管理公司之间签订的《会员协议》；"
                "二、某健身管理公司于判决生效之日起十日内向原告曾某返还会籍费用以及私人教练课程费16370元。判决作出后各方均未提出上诉，一审判决已生效。\n"
                "## 裁判理由\n"
                "原告曾某根据《中华人民共和国民法典》第五百六十三条要求解除《会员协议》，符合法律规定。"
                "根据《中华人民共和国民法典》第五百六十六条规定，被告应予返还原告交纳的会籍费用及不能提供服务的私人教练课程费用。"
            ),
        }
    )
    assert record.maturity == MaturityLevel.L1
    assert all(item.human_verified is False for item in record.condition_findings)
    assert record.decisions[0].status == DecisionStatus.PARTIALLY_GRANTED
    assert record.decisions[0].amount == 16370
    by_cond = {item.condition_id: item for item in record.condition_findings}
    assert by_cond[COND_PAYMENT_MADE].status.value == "SATISFIED"
    assert "交纳" in next(
        span.quote for span in record.source_spans if span.span_id in by_cond[COND_PAYMENT_MADE].source_span_ids
    )
    assert by_cond[COND_UNPERFORMED_BALANCE].status.value == "UNKNOWN"
    assert record.reasoning_steps[0].applied_rule_ids == [RULE_SERVICE_TERMINATION_REFUND]
    assert "诉称" not in "".join(item.predicate for item in record.findings)
    assert "辩称" not in "".join(item.predicate for item in record.findings)
