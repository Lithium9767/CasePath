"""Build relation-level qrels for the pinned civil-case retrieval corpus."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from casepath.contracts import CaseIndexRecord
from casepath.ingestion.laws.jsonl import read_jsonl

QUERY_SPECS = (
    ("loan", "借款交付后借款人未按期还本付息", "借款合同关系"),
    ("guarantee", "保证人是否应对债务承担保证责任", "保证合同关系"),
    ("marriage", "夫妻婚姻关系及共同财产债务争议", "夫妻关系"),
    ("traffic", "机动车交通事故造成损害后的侵权赔偿责任", "机动车交通事故侵权责任法律关系"),
    ("property_insurance", "保险事故发生后财产保险合同理赔争议", "财产保险合同关系"),
    ("sale", "买卖合同交货付款及违约责任争议", "除房屋外的买卖合同关系"),
    ("mortgage_contract", "抵押合同设立及抵押担保责任争议", "抵押合同关系"),
    ("house_sale", "房屋买卖合同履行过户付款争议", "房屋买卖合同关系"),
    ("recourse", "承担清偿责任后向责任人追偿", "追偿权关系"),
    ("labor_service", "提供劳务后报酬支付和损害责任争议", "劳务合同关系"),
    ("lease", "租赁物使用、租金支付及合同解除争议", "租赁合同关系"),
    ("property_service", "物业服务费支付及物业服务义务争议", "物业服务合同关系"),
    ("service", "服务合同履行、解除与费用返还争议", "服务合同关系"),
)


def build_qrels(records: list[CaseIndexRecord]) -> list[dict[str, object]]:
    qrels: list[dict[str, object]] = []
    for suffix, query_text, relation_type in QUERY_SPECS:
        relevant = sorted(
            item.case_id
            for item in records
            if relation_type in item.keywords and "LexRel" in item.keywords
        )
        if not relevant:
            raise ValueError(f"no relevant cases for {relation_type}")
        qrels.append(
            {
                "query_id": f"lexrel.{suffix}",
                "query_text": query_text,
                "relation_type": relation_type,
                "relevant_case_ids": relevant,
            }
        )
    return qrels


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--cases",
        type=Path,
        default=Path("data/canonical/cases/cases_l1.jsonl"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/evaluation/case_retrieval_qrels.jsonl"),
    )
    args = parser.parse_args()
    records = read_jsonl(args.cases, CaseIndexRecord)
    qrels = build_qrels(records)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8", newline="\n") as stream:
        for item in qrels:
            stream.write(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n")
    print(f"wrote {len(qrels)} queries to {args.output}")


if __name__ == "__main__":
    main()
