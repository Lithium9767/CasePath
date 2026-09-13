"""P2 L1 扩展发布：保留确定性基线，规范共享证据，不生成新的人工审核。"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import tempfile
from collections import Counter
from pathlib import Path

from casepath.contracts import LegalSourceRecord, ProvisionRecord, RuleRecord
from casepath.ingestion.laws.jsonl import (
    normalise_lf_bytes,
    read_jsonl,
    render_jsonl,
    sha256_bytes,
    sha256_file,
    sha256_text,
    write_json,
    write_jsonl,
)
from casepath.rule_layer.civil_code import build_civil_code_rules
from casepath.rule_layer.source_review import (
    EXPECTED_CANONICAL_OUTPUT_HASHES,
    authority_verification_snapshot,
)

DATASET_ID = "dataset.civil_code.rules.expanded.v1"
RULES_ROOT = Path("canonical/rules")


def normalize(batch: dict, provisions: list[ProvisionRecord]):
    """只合并相同定义的证据列表；定义差异、断链和非法原文一律阻止发布。"""
    base = build_civil_code_rules(provisions)
    # 【a 规则来源】基线由 civil_code.py 构造，扩展从 batch['rules'] 读取并校验。
    # 此处不调用 LLM/embedding 抽取三元组；批次 JSON 的上游生成方式需查交付记录。
    additions = [RuleRecord.model_validate(item) for item in batch["rules"]]
    if any(r.contract_version != "1.3" or r.maturity.value != "L1" for r in additions):
        raise ValueError("新增规则必须保持 v1.3 / L1")
    rules = [*base.rules, *additions]
    if len({r.rule_id for r in rules}) != len(rules):
        raise ValueError("新增规则不得覆盖已有 rule_id 或重复发布")
    if set(batch.get("deleted_rule_ids", [])) & {r.rule_id for r in rules}:
        raise ValueError("删除列表与保留/新增规则冲突")
    spans = {}
    for span in [
        *(s for p in provisions for s in p.source_spans),
        *base.source_spans,
        *(s for r in rules for s in r.source_spans),
    ]:
        if span.span_id in spans and spans[span.span_id] != span:
            raise ValueError(f"同 ID 原文冲突: {span.span_id}")
        spans[span.span_id] = span
    by_number = {int(p.article_no): p for p in provisions}
    by_id = {p.provision_id: p for p in provisions}
    for span in spans.values():
        match = re.fullmatch(r"第(\d+)条", span.section or "")
        p = by_number.get(int(match[1])) if match else None
        if (
            p is None
            or p.source_id != span.source_id
            or not span.quote.strip()
            or p.text[span.start_offset : span.end_offset] != span.quote
        ):
            raise ValueError(f"原文无法锚定: {span.span_id}")
    shared = {}
    # 同一 condition_id 必须有一致定义，后续据此共享图节点，不按向量相似度合并实体。
    for rule in rules:
        for attr in ("conditions", "exceptions", "consequences"):
            field = {
                "conditions": "condition_id",
                "exceptions": "exception_id",
                "consequences": "consequence_id",
            }[attr]
            for entity in getattr(rule, attr):
                key = (attr, getattr(entity, field))
                definition = entity.model_dump(exclude={"source_span_ids"})
                if key in shared and shared[key][0] != definition:
                    raise ValueError(f"共享实体定义冲突: {key}")
                shared.setdefault(key, (definition, set()))[1].update(entity.source_span_ids)
    normalized = []
    for rule in rules:
        payload = rule.model_dump(mode="json")
        required = {s.span_id for s in rule.source_spans}
        for attr, field in (
            ("conditions", "condition_id"),
            ("exceptions", "exception_id"),
            ("consequences", "consequence_id"),
        ):
            for entity in payload[attr]:
                ids = shared[(attr, entity[field])][1]
                if not ids <= spans.keys():
                    raise ValueError(f"缺失原文引用: {sorted(ids - spans.keys())}")
                # 基线记录逐字保留；新增记录使用确定性并集。
                if rule.rule_id not in {r.rule_id for r in base.rules}:
                    entity["source_span_ids"] = sorted(ids)
                elif set(entity["source_span_ids"]) != ids:
                    raise ValueError("不得通过共享证据合并修改已审核的基线条件")
                required.update(ids)
        if rule.rule_id not in {r.rule_id for r in base.rules}:
            payload["source_spans"] = [
                spans[key].model_dump(mode="json") for key in sorted(required)
            ]
            if batch.get("data_refinement_version") == "p2-data-refinement.1":
                from casepath.rule_layer.stage2_refinement import declare_span_provisions

                declare_span_provisions(payload, provisions)
        for ref in rule.provisions:
            p = by_id.get(ref.provision_id)
            if p is None or (
                ref.source_id,
                ref.article_no,
                ref.title,
                ref.valid_from,
                ref.valid_to,
            ) != (p.source_id, p.article_no, p.title, p.effective_from, p.effective_to):
                raise ValueError(f"法条引用不一致: {rule.rule_id}")
        result = RuleRecord.model_validate(payload)
        core_ids = {
            key
            for group in result.condition_groups
            if group.role.value == "CORE"
            for key in group.member_condition_ids
        }
        for entity in [
            *result.exceptions,
            *result.consequences,
            *(c for c in result.conditions if c.condition_id in core_ids),
        ]:
            if not entity.source_span_ids:
                raise ValueError(f"核心条件/例外/后果缺少原文: {result.rule_id}")
        normalized.append(result)
    return sorted(normalized, key=lambda r: r.rule_id), [spans[key] for key in sorted(spans)]


def coverage(batch, rules):
    """保留原始 skip 选择器；已存在规则优先，不把历史 skip 当排除规则。"""
    referenced = {int(p.article_no) for r in rules for p in r.provisions}
    skipped = set()
    for item in batch["skipped"]:
        selector = str(item.get("article_no", ""))
        if not str(item.get("reason", "")).strip():
            raise ValueError("skip 必须说明原因")
        if re.fullmatch(r"\d+", selector):
            numbers = [int(selector)]
        elif re.fullmatch(r"\d+-\d+", selector):
            start, end = map(int, selector.split("-"))
            numbers = range(start, end + 1) if start <= end else [start]
        elif re.fullmatch(r"\d+-(def|ref)", selector):
            numbers = [int(selector.split("-")[0])]
        else:
            raise ValueError(f"无法识别 skip 选择器: {selector}")
        skipped.update(numbers)
    expected = set(range(1, 1261))
    if (referenced | skipped) != expected:
        raise ValueError("法条覆盖不完整或条号超出范围")
    return {
        "rule_referenced_articles": len(referenced),
        "skip_only_articles": sorted(skipped - referenced),
        "rule_and_historical_skip_articles": sorted(skipped & referenced),
        "remaining": 0,
    }


def validate_expanded(data_root: Path):
    """按保留的输入重新构建，不能仅更新输出哈希就给修改后的规则放行。"""
    from casepath.rule_layer.validation import DatasetValidationReport, validate_records

    manifest = json.loads((data_root / "manifests/civil_code.manifest.json").read_text("utf-8"))
    if manifest.get("dataset_id") != DATASET_ID or manifest.get("manifest_version") != "2.0":
        raise ValueError("未知扩展发布格式")
    input_path = Path(manifest["input_path"])
    digest = manifest["input_sha256"]
    if (
        not re.fullmatch(r"[0-9a-f]{64}", digest)
        or input_path.as_posix() != f"inputs/rules/{digest}.json"
    ):
        raise ValueError("扩展输入必须使用内容哈希定位")
    source = data_root / input_path
    if sha256_file(source) != digest:
        raise ValueError("扩展输入哈希不匹配")
    root = data_root / RULES_ROOT
    sources = read_jsonl(root / "legal_sources.jsonl", LegalSourceRecord)
    provisions = read_jsonl(root / "provisions.jsonl", ProvisionRecord)
    for name in ("legal_sources.jsonl", "provisions.jsonl"):
        if sha256_file(root / name) != EXPECTED_CANONICAL_OUTPUT_HASHES[name]:
            raise ValueError(f"既有法条发布被修改: {name}")
    base = build_civil_code_rules(provisions)
    base_spans = [*(s for p in provisions for s in p.source_spans), *base.source_spans]
    validate_records(sources, provisions, base.rules, base_spans)
    if manifest.get("retained_baseline_review") != authority_verification_snapshot().model_dump(
        mode="json"
    ):
        raise ValueError("保留基线的审核记录不匹配")
    from casepath.ingestion.laws.manifest import CivilCodeManifest

    previous = CivilCodeManifest.model_validate(manifest["baseline_manifest"])
    if (
        {Path(item.path).name: item.sha256 for item in previous.outputs}
        != EXPECTED_CANONICAL_OUTPUT_HASHES
        or previous.authority_verification != authority_verification_snapshot()
    ):
        raise ValueError("归档基线回执不匹配")
    input_payload = json.loads(source.read_text("utf-8"))
    batch = input_payload
    if "recipe_version" in input_payload:
        from casepath.rule_layer.stage2_refinement import materialize

        batch = materialize(input_payload, provisions, data_root)
    rules, spans = normalize(batch, provisions)
    expected_outputs = {
        "legal_sources.jsonl": sha256_file(root / "legal_sources.jsonl"),
        "provisions.jsonl": sha256_file(root / "provisions.jsonl"),
        "rules.jsonl": sha256_text(render_jsonl(rules)),
        "source_spans.jsonl": sha256_text(render_jsonl(spans)),
    }
    if manifest.get("outputs") != expected_outputs:
        raise ValueError("manifest 与确定性构建结果不匹配")
    for name, expected_hash in expected_outputs.items():
        if sha256_file(root / name) != expected_hash:
            raise ValueError(f"扩展输出哈希不匹配: {name}")
    if manifest.get("coverage") != coverage(batch, rules):
        raise ValueError("覆盖回执不匹配")
    expected_refinement = None
    if "recipe_version" in input_payload:
        from casepath.rule_layer.stage2_refinement import receipt

        expected_refinement = receipt(input_payload, provisions, rules, data_root)
    if manifest.get("data_refinement") != expected_refinement:
        raise ValueError("第二阶段修订回执不匹配")
    if manifest.get("new_rule_review") != "L1_unreviewed" or manifest.get("rule_count") != len(
        rules
    ):
        raise ValueError("新增规则审核或数量声明不匹配")
    return DatasetValidationReport(
        legal_source_count=len(sources),
        provision_count=len(provisions),
        rule_count=len(rules),
        l3_rule_count=sum(r.maturity.value == "L3" for r in rules),
        source_span_count=len(spans),
        checked_article_numbers=list(range(1, 1261)),
        status="passed",
    )


def publish(source: Path, data_root: Path, *, check_only: bool = False):
    from casepath.ingestion.graph.projection import load_projection, report
    from casepath.rule_layer.build import _publish_staged_outputs
    from casepath.rule_layer.validation import validate_canonical_dataset

    validate_canonical_dataset(data_root)
    old_manifest = json.loads((data_root / "manifests/civil_code.manifest.json").read_text("utf-8"))
    previous_manifest = old_manifest.get("baseline_manifest", old_manifest)
    before = {
        p: sha256_file(p)
        for p in [
            *(data_root / "canonical").rglob("*.jsonl"),
            *(data_root / "manifests").glob("*.json"),
            *(data_root / "inputs/rules").rglob("*.json"),
        ]
    }
    raw = source.read_bytes()
    batch = json.loads(raw)
    digest = sha256_bytes(normalise_lf_bytes(raw))
    relative_input = Path(f"inputs/rules/{digest}.json")
    with tempfile.TemporaryDirectory(prefix=".rules-expansion-", dir=data_root.parent) as temp:
        stage = Path(temp) / "data"
        # 原有基线、案例及回执一起参与候选验证，但只发布 P2 的五个正式文件。
        for name in ("canonical", "manifests", "inputs"):
            if (data_root / name).exists():
                shutil.copytree(data_root / name, stage / name)
        (stage / relative_input).parent.mkdir(parents=True, exist_ok=True)
        (stage / relative_input).write_bytes(raw)
        provisions = read_jsonl(stage / RULES_ROOT / "provisions.jsonl", ProvisionRecord)
        materialized = batch
        if "recipe_version" in batch:
            from casepath.rule_layer.stage2_refinement import materialize

            materialized = materialize(batch, provisions, stage)
        rules, spans = normalize(materialized, provisions)
        write_jsonl(stage / RULES_ROOT / "rules.jsonl", rules)
        write_jsonl(stage / RULES_ROOT / "source_spans.jsonl", spans)
        manifest = {
            "manifest_version": "2.0",
            "dataset_id": DATASET_ID,
            "input_path": relative_input.as_posix(),
            "input_sha256": digest,
            "rule_count": len(rules),
            "new_rule_review": "L1_unreviewed",
            "retained_baseline_review": authority_verification_snapshot().model_dump(mode="json"),
            "baseline_manifest": previous_manifest,
            "review_scope": "仅保留的五条规则沿用旧审核；新增规则不继承人工审核",
            "outputs": {
                name: sha256_file(stage / RULES_ROOT / name)
                for name in (
                    "legal_sources.jsonl",
                    "provisions.jsonl",
                    "rules.jsonl",
                    "source_spans.jsonl",
                )
            },
            "coverage": coverage(materialized, rules),
        }
        if "recipe_version" in batch:
            from casepath.rule_layer.stage2_refinement import receipt

            manifest["data_refinement"] = receipt(batch, provisions, rules, stage)
        write_json(stage / "manifests/civil_code.manifest.json", manifest)
        bundle = load_projection(stage)  # 同时验证 P3 桥接和图节点/边完整性。
        result = {
            "rules": len(rules),
            "maturities": dict(Counter(r.maturity.value for r in rules)),
            "graph": report(bundle),
            "coverage": manifest["coverage"],
        }
        if check_only:
            return result
        if any(not path.is_file() or sha256_file(path) != value for path, value in before.items()):
            raise ValueError("构建期间输入发生变化，拒绝发布")
        target = data_root / relative_input
        if target.resolve() != target.absolute():
            raise ValueError("拒绝重定向的输入发布路径")
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() and sha256_file(target) != digest:
            raise ValueError("同哈希输入路径内容异常")
        if not target.exists():
            shutil.copyfile(stage / relative_input, target)
        _publish_staged_outputs(stage, data_root)
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--data-root", type=Path, default=Path("data"))
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args()
    print(
        json.dumps(
            publish(args.source, args.data_root.resolve(), check_only=args.check_only),
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
