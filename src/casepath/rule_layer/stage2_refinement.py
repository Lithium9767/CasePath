"""Small, replayable data corrections over the team's immutable 771-rule input.

No new legal rules, maturity promotions, or P4 execution policy are introduced.
The recipe references the original input instead of duplicating its entire text.
"""

from __future__ import annotations

import argparse
import copy
import json
import re
import tempfile
from pathlib import Path

from casepath.contracts import ProvisionRecord, RuleRecord
from casepath.ingestion.laws.jsonl import sha256_file, sha256_text, write_json

VERSION = "p2-data-refinement.1"
BASE_SHA256 = "0de3517f83eb917b316e9579a87be8244ed55b27124127ccef62e3166f60d68a"
BASE_PATH = f"inputs/rules/{BASE_SHA256}.json"
ALIASES = (
    "cond.contract.not_or_badly_performed_583",
    "cond.contract.not_or_badly_performed_584",
)
CANONICAL_CONDITION = "cond.contract.nonperformance_or_nonconformity"
SOURCE_URL = "https://www.court.gov.cn/zixun/xiangqing/233181.html"
CHECKED_ON = "2026-09-11"
SPAN_SPECS = (
    (583, "breach", 0, 27, "当事人一方不履行合同义务或者履行合同义务不符合约定的，"),
    (
        584,
        "breach-loss",
        0,
        34,
        "当事人一方不履行合同义务或者履行合同义务不符合约定，造成对方损失的，",
    ),
    (570, "deposit-ground", 0, 28, "有下列情形之一，难以履行债务的，债务人可以将标的物提存："),
)


def recipe() -> dict:
    return {
        "recipe_version": VERSION,
        "base_input_path": BASE_PATH,
        "base_input_sha256": BASE_SHA256,
        "operations": [
            "merge_exact_583_584_condition_definitions",
            "anchor_570_583_584_conditions_to_actual_premises",
            "declare_all_provisions_referenced_by_shared_source_spans",
        ],
    }


def load_base(data_root: Path) -> dict:
    path = data_root / BASE_PATH
    if sha256_file(path) != BASE_SHA256:
        raise ValueError("第二阶段保留输入哈希不匹配")
    return json.loads(path.read_text("utf-8"))


def _new_spans(provisions: list[ProvisionRecord]) -> dict:
    by_number = {int(p.article_no): p for p in provisions}
    result = {}
    for number, suffix, start, end, quote in SPAN_SPECS:
        provision = by_number[number]
        if provision.text[start:end] != quote:
            raise ValueError(f"第{number}条核对片段已漂移，须重新核对原文")
        span_id = f"span.civil-code.{number}.{suffix}"
        result[span_id] = {
            "span_id": span_id,
            "source_id": provision.source_id,
            "section": f"第{number}条",
            "paragraph_id": f"article-{number:04d}-{suffix}",
            "start_offset": start,
            "end_offset": end,
            "quote": quote,
        }
    return result


def materialize(payload: dict, provisions: list[ProvisionRecord], data_root: Path) -> dict:
    """Reject modified recipes and replay only the explicit, fixed correction set."""
    if payload != recipe():
        raise ValueError("未知或被修改的第二阶段数据修订配方")
    batch = copy.deepcopy(load_base(data_root))
    spans = _new_spans(provisions)
    duplicates = [
        {k: v for k, v in condition.items() if k not in {"condition_id", "source_span_ids"}}
        for rule in batch["rules"]
        for condition in rule["conditions"]
        if condition["condition_id"] in ALIASES
    ]
    if len(duplicates) != 2 or duplicates[0] != duplicates[1]:
        raise ValueError("583/584条件不是完全同义定义，拒绝去重")
    for rule in batch["rules"]:
        for condition in rule["conditions"]:
            key = condition["condition_id"]
            if key in ALIASES:
                condition["condition_id"] = CANONICAL_CONDITION
                condition["source_span_ids"] = [
                    "span.civil-code.583.breach",
                    "span.civil-code.584.breach-loss",
                ]
            elif key == "cond.breach_caused_loss":
                condition["source_span_ids"] = sorted(
                    {
                        "span.civil-code.584.breach-loss",
                        *(
                            s
                            for s in condition["source_span_ids"]
                            if s != "span.civil-code.584.expectancy"
                        ),
                    }
                )
            elif key == "cond.contract.performance_difficult":
                condition["source_span_ids"] = ["span.civil-code.570.deposit-ground"]
            embedded = {s["span_id"] for s in rule["source_spans"]}
            for span_id in condition["source_span_ids"]:
                if span_id in spans and span_id not in embedded:
                    rule["source_spans"].append(copy.deepcopy(spans[span_id]))
                    embedded.add(span_id)
        for group in rule["condition_groups"]:
            group["member_condition_ids"] = [
                CANONICAL_CONDITION if key in ALIASES else key
                for key in group["member_condition_ids"]
            ]
    batch["data_refinement_version"] = VERSION
    return batch


def declare_span_provisions(payload: dict, provisions: list[ProvisionRecord]) -> None:
    """A shared condition's evidence must also have a matching declared law reference."""
    by_number = {int(p.article_no): p for p in provisions}
    declared = {ref["provision_id"] for ref in payload["provisions"]}
    for span in payload["source_spans"]:
        match = re.fullmatch(r"第(\d+)条", span.get("section") or "")
        provision = by_number.get(int(match[1])) if match else None
        if provision is None or provision.source_id != span["source_id"]:
            raise ValueError("共享原文片段无法对应法条声明")
        if provision.provision_id in declared:
            continue
        payload["provisions"].append(
            {
                "source_id": provision.source_id,
                "provision_id": provision.provision_id,
                "article_no": provision.article_no,
                "title": provision.title,
                "valid_from": provision.effective_from.isoformat()
                if provision.effective_from
                else None,
                "valid_to": provision.effective_to.isoformat() if provision.effective_to else None,
            }
        )
        declared.add(provision.provision_id)


def receipt(
    payload: dict, provisions: list[ProvisionRecord], rules: list[RuleRecord], data_root: Path
) -> dict:
    from casepath.rule_layer.expansion import normalize

    if payload != recipe():
        raise ValueError("未知第二阶段数据修订配方")
    original, _ = normalize(load_base(data_root), provisions)
    before = {rule.rule_id: rule for rule in original}
    changed = [r.rule_id for r in rules if r != before[r.rule_id]]
    added_references = {
        rule.rule_id: sorted(
            {p.provision_id for p in rule.provisions}
            - {p.provision_id for p in before[rule.rule_id].provisions}
        )
        for rule in rules
    }
    return {
        "version": VERSION,
        "base_input_path": BASE_PATH,
        "base_input_sha256": BASE_SHA256,
        "source_url": SOURCE_URL,
        "checked_on": CHECKED_ON,
        "article_text_sha256": {
            p.article_no: sha256_text(p.text)
            for p in provisions
            if int(p.article_no) in {number for number, *_ in SPAN_SPECS}
        },
        "condition_aliases": [
            {
                "canonical_id": CANONICAL_CONDITION,
                "alias_ids": list(ALIASES),
                "reason": "定义逐字段一致，仅合并同一合同违约命题；其他损失与限制条件不合并",
            }
        ],
        "new_source_span_ids": sorted(_new_spans(provisions)),
        "changed_rule_ids": sorted(changed),
        "added_provision_references": {k: v for k, v in sorted(added_references.items()) if v},
        "rule_count_before": len(original),
        "rule_count_after": len(rules),
        "unique_conditions_before": len({c.condition_id for r in original for c in r.conditions}),
        "unique_conditions_after": len({c.condition_id for r in rules for c in r.conditions}),
        "maturity_upgraded": False,
        "deep_human_review": False,
        "semantic_exhaustiveness": "not_claimed",
    }


def rebuild(data_root: Path, *, check_only: bool = False):
    from casepath.rule_layer.expansion import publish

    with tempfile.TemporaryDirectory(prefix=".p2-data-recipe-", dir=data_root.parent) as temp:
        path = Path(temp) / "recipe.json"
        write_json(path, recipe())
        return publish(path, data_root, check_only=check_only)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, default=Path("data"))
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args()
    print(
        json.dumps(
            rebuild(args.data_root.resolve(), check_only=args.check_only),
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
