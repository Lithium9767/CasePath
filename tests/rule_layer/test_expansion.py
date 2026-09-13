"""扩展发布回归：证据守卫、基线保留、失败回滚和跨领域全文索引。"""

import json
import shutil
from pathlib import Path

import pytest

from casepath.contracts import ProvisionRecord, RuleRecord
from casepath.ingestion.laws.jsonl import read_jsonl, sha256_file, write_json, write_jsonl
from casepath.rule_layer.civil_code import build_civil_code_rules
from casepath.rule_layer.expansion import normalize, publish, validate_expanded

ROOT = Path(__file__).resolve().parents[2] / "data"


def small_batch():
    provisions = read_jsonl(ROOT / "canonical/rules/provisions.jsonl", ProvisionRecord)
    rule = build_civil_code_rules(provisions).rules[0].model_dump(mode="json")
    rule.update(rule_id="rule.test.l1.v1", maturity="L1")
    return {
        "rules": [rule],
        "skipped": [{"article_no": "1-1260", "reason": "测试覆盖"}],
    }, provisions


def test_normalizer_preserves_baseline_and_rejects_fake_evidence():
    batch, provisions = small_batch()
    rules, _ = normalize(batch, provisions)
    actual = {r.rule_id: r for r in rules}
    for rule in build_civil_code_rules(provisions).rules:
        assert actual[rule.rule_id] == rule
    batch["rules"][0]["source_spans"][0]["quote"] = "虚构法条引句"
    with pytest.raises(ValueError):
        normalize(batch, provisions)


def test_normalizer_rejects_semantic_id_collision():
    batch, provisions = small_batch()
    batch["rules"][0]["conditions"][0]["predicate"] = "改变已有条件含义"
    with pytest.raises(ValueError, match="定义冲突"):
        normalize(batch, provisions)


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_first_expansion_without_input_directory(tmp_path, newline):
    root = tmp_path / "data"
    for part in ("canonical", "manifests"):
        shutil.copytree(ROOT / part, root / part)
    manifest_path = root / "manifests/civil_code.manifest.json"
    manifest = json.loads(manifest_path.read_text("utf-8"))
    batch, provisions = small_batch()
    base = build_civil_code_rules(provisions)
    write_jsonl(root / "canonical/rules/rules.jsonl", base.rules)
    write_jsonl(
        root / "canonical/rules/source_spans.jsonl",
        [
            *(s for p in provisions for s in p.source_spans),
            *base.source_spans,
        ],
    )
    write_json(manifest_path, manifest["baseline_manifest"])
    source = tmp_path / "batch.json"
    source.write_text(
        json.dumps(batch, ensure_ascii=False, indent=2), encoding="utf-8", newline=newline
    )
    assert not (root / "inputs").exists()
    assert publish(source, root)["rules"] == 6
    assert validate_expanded(root).rule_count == 6


def test_publish_roundtrip_and_failure_preserves_files(tmp_path, monkeypatch):
    root = tmp_path / "data"
    for name in ("canonical", "manifests", "inputs"):
        if (ROOT / name).exists():
            shutil.copytree(ROOT / name, root / name)
    batch, _ = small_batch()
    source = tmp_path / "batch.json"
    source.write_text(json.dumps(batch, ensure_ascii=False), encoding="utf-8")
    publish(source, root)
    assert validate_expanded(root).rule_count == 6
    assert publish(source, root)["rules"] == 6
    before = {p: sha256_file(p) for p in root.rglob("*.json*")}

    from casepath.ingestion.graph import projection

    def fail(*args):
        raise ValueError("模拟最终图验收失败")

    monkeypatch.setattr(projection, "load_projection", fail)
    with pytest.raises(ValueError, match="最终图"):
        publish(source, root)
    assert before == {p: sha256_file(p) for p in before}
    path = root / "canonical/rules/rules.jsonl"
    lines = path.read_text("utf-8").splitlines()
    tampered = json.loads(lines[0])
    tampered["title"] = "未经输入构建的标题修改"
    lines[0] = json.dumps(tampered, ensure_ascii=False)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="哈希"):
        validate_expanded(root)


def test_published_expansion_and_cross_domain_index():
    from casepath.adapters.retrieval.retrieval_catalog import (
        MemoryHybridSearch,
        load_canonical_catalog,
    )

    report = validate_expanded(ROOT)
    assert report.rule_count == 776
    catalog = load_canonical_catalog(ROOT)
    index = MemoryHybridSearch(catalog).rule_index
    for text, article in (
        ("不动产登记", 209),
        ("肖像权", 1019),
        ("夫妻共同财产", 1062),
        ("遗嘱继承", 1133),
        ("产品缺陷", 1202),
    ):
        hits = index.search(text, 20)
        assert hits, text
        assert all(key in catalog.rules for key, _ in hits)
        assert any(
            any(p.endswith(f"article_{article:04d}") for p in catalog.rules[key].provision_ids)
            for key, _ in hits
        ), text
    published = read_jsonl(ROOT / "canonical/rules/rules.jsonl", RuleRecord)
    assert sum(r.maturity.value == "L1" for r in published) == 771
