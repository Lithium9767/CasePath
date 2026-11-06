"""校验 E 追问冻结候选/正式集的合同、引用、版本和输入哈希。"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

from casepath.adapters.retrieval.retrieval_catalog import RuleScope, load_canonical_catalog
from casepath.application.runtime_meta import SessionRuntimeMeta
from casepath.contracts import ComparisonBundle, QueryState, RetrievalBundle


def encode(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if hasattr(value, "__dataclass_fields__"):
        return {name: encode(getattr(value, name)) for name in value.__dataclass_fields__}
    if isinstance(value, (tuple, list)):
        return [encode(item) for item in value]
    if isinstance(value, dict):
        return {key: encode(item) for key, item in value.items()}
    return value


def payload(raw: dict[str, Any], state: QueryState, bundle: RetrievalBundle, comparison: ComparisonBundle, meta: SessionRuntimeMeta, scope: RuleScope) -> str:
    return json.dumps(
        {
            "query_state": encode(state),
            "retrieval_bundle": encode(bundle),
            "comparison_bundle": encode(comparison),
            "runtime_meta": encode(meta),
            "rule_scope": encode(scope),
            "catalog_context": raw.get("catalog_context", {}),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def validate(path: Path, *, formal: bool) -> dict[str, Any]:
    catalog = load_canonical_catalog(Path(__file__).resolve().parents[1] / "data")
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not rows:
        raise ValueError("数据集为空")
    ids = [row.get("sample_id") for row in rows]
    if any(not item for item in ids) or len(ids) != len(set(ids)):
        raise ValueError("sample_id 缺失或重复")
    for row in rows:
        state = QueryState.model_validate(row["query_state"])
        bundle = RetrievalBundle.model_validate(row["retrieval_bundle"])
        comparison = ComparisonBundle.model_validate(row["comparison_bundle"])
        meta = SessionRuntimeMeta.model_validate(row.get("runtime_meta", {}))
        scope = RuleScope(**row["rule_scope"])
        if state.contract_version != "1.3" or bundle.contract_version != "1.3" or comparison.contract_version != "1.3":
            raise ValueError(f"{row['sample_id']} 使用的合同不是 v1.3")
        all_rule_ids = set(scope.active_rule_ids) | set(scope.alternative_rule_ids) | set(scope.background_rule_ids)
        all_rule_ids |= {item.object_id for item in bundle.rule_refs}
        unknown_rules = all_rule_ids - set(catalog.rules)
        if unknown_rules:
            raise ValueError(f"{row['sample_id']} 存在未知规则: {sorted(unknown_rules)}")
        all_condition_ids = set(scope.active_condition_ids) | {item.condition_id for item in state.condition_states}
        all_condition_ids |= {item.condition_id for item in comparison.condition_comparisons}
        unknown_conditions = all_condition_ids - set(catalog.conditions)
        if unknown_conditions:
            raise ValueError(f"{row['sample_id']} 存在未知条件: {sorted(unknown_conditions)}")
        all_case_ids = {
            item.object_id
            for group in (bundle.support_case_refs, bundle.limiting_case_refs, bundle.boundary_case_refs)
            for item in group
        }
        all_case_ids |= set(bundle.undetermined_case_ids)
        unknown_cases = all_case_ids - set(catalog.cases)
        if unknown_cases:
            raise ValueError(f"{row['sample_id']} 存在未知案例: {sorted(unknown_cases)}")
        context = row.get("catalog_context", {})
        if context.get("release_id") != catalog.release_id or context.get("index_version") != catalog.index_version:
            raise ValueError(f"{row['sample_id']} 目录版本与当前发布不一致")
        expected = {"expected_action", "acceptable_missing_facts", "forbidden_reasks", "unable_or_declined_topics", "review_status"}
        missing = expected - set(row.get("gold", {}))
        if missing:
            raise ValueError(f"{row['sample_id']} 金标准缺字段: {sorted(missing)}")
        if row["gold"]["expected_action"] not in {"ASK", "STOP"}:
            raise ValueError(f"{row['sample_id']} expected_action 非法")
        actual = hashlib.sha256(payload(row, state, bundle, comparison, meta, scope).encode()).hexdigest()
        if row.get("input_sha256") != actual:
            raise ValueError(f"{row['sample_id']} input_sha256 不匹配")
    counts = Counter(row["category"] for row in rows)
    if formal and (len(rows) != 20 or counts != Counter({"critical_ask": 5, "refinement_ask": 5, "sufficient_stop": 5, "unable_stop": 5})):
        raise ValueError(f"正式集必须四类各5条，当前 {dict(counts)}")
    return {"sample_count": len(rows), "category_counts": dict(counts), "status": "pass"}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--formal", action="store_true")
    args = parser.parse_args()
    result = validate(args.dataset.resolve(), formal=args.formal)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
