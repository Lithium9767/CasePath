"""在 E 之前冻结 A-D 状态，供 E0/E1 配对实验使用。

输入是人工编写的合成对话规格 JSONL。脚本使用当前 Live 组件执行 A、B、C、D，
保存完整合同、规则范围、目录上下文和输入哈希；它从不调用 question_policy.select。
规格中的 facts / mappings 用于表达合成对话中已知事实，必须在发布前逐条 AI 审核。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from casepath.adapters.retrieval.retrieval_catalog import current_scope
from casepath.adapters.runtime.request_context import p4_request_context
from casepath.api.configured import create_configured_app
from casepath.application.runtime_meta import SessionRuntimeMeta
from casepath.contracts import (
    ComparisonBundle,
    ConditionEvidence,
    ConditionStatus,
    QueryConditionState,
    QueryState,
    RetrievalBundle,
    UserFact,
)

ROOT = Path(__file__).resolve().parents[1]


def encode(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if hasattr(value, "__dataclass_fields__"):
        return {name: encode(getattr(value, name)) for name in value.__dataclass_fields__}
    if isinstance(value, (list, tuple)):
        return [encode(item) for item in value]
    if isinstance(value, dict):
        return {key: encode(item) for key, item in value.items()}
    return value


def canonical_payload(state: QueryState, bundle: RetrievalBundle, comparison: ComparisonBundle, meta: SessionRuntimeMeta, scope: Any, catalog_context: dict[str, Any]) -> str:
    return json.dumps(
        {
            "query_state": encode(state),
            "retrieval_bundle": encode(bundle),
            "comparison_bundle": encode(comparison),
            "runtime_meta": encode(meta),
            "rule_scope": encode(scope),
            "catalog_context": catalog_context,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def apply_manual_mappings(state: QueryState, spec: dict[str, Any], catalog: Any) -> QueryState:
    """把合成对话中明确给出的事实映射写入状态，并保留原话证据。"""
    facts = {item.fact_id: item for item in state.user_facts}
    conditions = {item.condition_id: item for item in state.condition_states}
    for mapping in spec.get("mappings", []):
        fact = facts.get(mapping["fact_id"])
        if fact is None:
            raise ValueError(f"mapping 引用不存在的 fact_id: {mapping['fact_id']}")
        condition_id = mapping["condition_id"]
        if condition_id not in catalog.conditions:
            raise ValueError(f"mapping 引用不存在的 condition_id: {condition_id}")
        evidence = ConditionEvidence(
            fact_id=fact.fact_id,
            relation=mapping["relation"],
            confidence=mapping.get("confidence", 0.9),
            reason=mapping.get("reason", "合成对话中的明确事实"),
        )
        old = conditions.get(condition_id)
        evidences = [item for item in (old.evidence if old else []) if item.fact_id != fact.fact_id]
        evidences.append(evidence)
        relations = {item.relation for item in evidences}
        if "SUPPORTS" in relations and "OPPOSES" in relations:
            status = ConditionStatus.CONFLICTING
        elif "SUPPORTS" in relations:
            status = ConditionStatus.SATISFIED
        elif "OPPOSES" in relations:
            status = ConditionStatus.NOT_SATISFIED
        else:
            status = ConditionStatus.UNKNOWN
        conditions[condition_id] = QueryConditionState(
            condition_id=condition_id,
            status=status,
            supporting_fact_ids=[item.fact_id for item in evidences],
            confidence=max(item.confidence for item in evidences),
            evidence=evidences,
            mapping_reasons=["合成对话事实已由数据集作者明确标注"],
            last_updated_turn=max((fact.source_turn, old.last_updated_turn if old else 0)),
        )
    # “不记得/无法确认”不是 OPPOSES。若 Live 投影把这类表达误判成否定，
    # 数据集规格可以显式清除该条件的证据，恢复为 UNKNOWN，并保留原始事实。
    for condition_id in spec.get("clear_condition_ids", []):
        if condition_id not in catalog.conditions:
            raise ValueError(f"clear_condition_ids 引用不存在的 condition_id: {condition_id}")
        conditions[condition_id] = QueryConditionState(
            condition_id=condition_id,
            status=ConditionStatus.UNKNOWN,
            mapping_reasons=["原话表示不记得或无法确认，不能解释为明确否定"],
            last_updated_turn=0,
        )
    return state.model_copy(update={"condition_states": list(conditions.values())})


def catalog_context(catalog: Any, scope: Any) -> dict[str, Any]:
    rule_ids = [*scope.active_rule_ids, *scope.alternative_rule_ids, *scope.background_rule_ids]
    condition_ids = scope.active_condition_ids
    return {
        "release_id": catalog.release_id,
        "index_version": catalog.index_version,
        "embedding_model": catalog.embedding_model,
        "rules": {rid: encode(catalog.rules[rid]) for rid in dict.fromkeys(rule_ids) if rid in catalog.rules},
        "conditions": {cid: encode(catalog.conditions[cid]) for cid in dict.fromkeys(condition_ids) if cid in catalog.conditions},
    }


def prepare(spec_path: Path, output_path: Path) -> None:
    specs = [json.loads(line) for line in spec_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not specs:
        raise ValueError("规格文件为空")
    sample_ids = [item["sample_id"] for item in specs]
    if len(sample_ids) != len(set(sample_ids)):
        raise ValueError("sample_id 重复")
    app = create_configured_app()
    output: list[dict[str, Any]] = []
    with TestClient(app):
        runtime = getattr(app.state, "live_runtime", None)
        if runtime is None or runtime.language_model is None:
            raise RuntimeError("冻结数据采集要求 CASEPATH_MODE=live 且真实 LLM 已装配")
        deps = runtime.service.workflow.dependencies
        projector = deps.condition_projector
        catalog = projector.catalog
        for spec in specs:
            facts = [UserFact.model_validate(item) for item in spec.get("facts", [])]
            state = QueryState(
                session_id=f"experiment.{spec['sample_id']}",
                initial_query=spec["initial_query"],
                user_facts=facts,
            )
            meta = SessionRuntimeMeta(
                unable_condition_ids=spec.get("unable_condition_ids", []),
                declined_condition_ids=spec.get("declined_condition_ids", []),
            )
            with p4_request_context(meta):
                rule_refs = deps.rule_retriever.retrieve(state)
                projected = deps.condition_projector.project(state, rule_refs)
                projected = apply_manual_mappings(projected, spec, catalog)
                bundle = deps.case_retriever.retrieve(projected, rule_refs)
                comparison = deps.case_comparator.compare(projected, bundle)
                scope = current_scope()
                if scope is None:
                    raise RuntimeError("A-D 结束时 RuleScope 未绑定")
                context = catalog_context(catalog, scope)
                payload = canonical_payload(projected, bundle, comparison, meta, scope, context)
            row = {
                "dataset_version": "e-questioning.v1-candidate",
                "sample_id": spec["sample_id"],
                "scenario_group_id": spec["scenario_group_id"],
                "category": spec["category"],
                "source": {
                    "dialogue_type": "synthetic",
                    "scenario_spec_id": spec["sample_id"],
                    "construction_note": spec.get("construction_note", ""),
                },
                "query_state": encode(projected),
                "retrieval_bundle": encode(bundle),
                "comparison_bundle": encode(comparison),
                "runtime_meta": encode(meta),
                "rule_scope": encode(scope),
                "catalog_context": context,
                "gold": spec["gold"],
                "input_sha256": hashlib.sha256(payload.encode()).hexdigest(),
            }
            output.append(row)
            print(spec["sample_id"], spec["category"], len(rule_refs), len(bundle.support_case_refs) + len(bundle.limiting_case_refs) + len(bundle.boundary_case_refs))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(json.dumps(item, ensure_ascii=False) for item in output) + "\n", encoding="utf-8")
    manifest = output_path.with_name("manifest.json")
    manifest.write_text(
        json.dumps(
            {
                "dataset_version": "e-questioning.v1-candidate",
                "created_at": datetime.now(UTC).isoformat(),
                "git_head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
                "sample_count": len(output),
                "source_sha256": hashlib.sha256(spec_path.read_bytes()).hexdigest(),
                "status": "candidate_pending_ai_review",
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--specs", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    prepare(args.specs.resolve(), args.out.resolve())


if __name__ == "__main__":
    main()
