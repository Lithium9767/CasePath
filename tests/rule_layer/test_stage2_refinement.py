"""Data corrections preserve the original legal semantics and replayable release."""

import copy
import json
import shutil
from pathlib import Path

import pytest

from casepath.contracts import ProvisionRecord, RuleRecord
from casepath.ingestion.laws.jsonl import (
    read_jsonl,
    render_jsonl,
    sha256_file,
    sha256_text,
    write_json,
)
from casepath.rule_layer.civil_code import build_civil_code_rules
from casepath.rule_layer.expansion import normalize, validate_expanded
from casepath.rule_layer.stage2_refinement import (
    ALIASES,
    BASE_PATH,
    CANONICAL_CONDITION,
    SPAN_SPECS,
    load_base,
    materialize,
    receipt,
    recipe,
)

ROOT = Path(__file__).resolve().parents[2] / "data"


@pytest.fixture(scope="module")
def correction():
    provisions = read_jsonl(ROOT / "canonical/rules/provisions.jsonl", ProvisionRecord)
    original, _ = normalize(load_base(ROOT), provisions)
    batch = materialize(recipe(), provisions, ROOT)
    rules, spans = normalize(batch, provisions)
    return provisions, original, rules, spans


def test_exact_dedup_does_not_delete_rules_or_upgrade_maturity(correction):
    provisions, original, rules, _ = correction
    assert len(original) == len(rules) == 776
    assert {r.rule_id: r.maturity for r in original} == {r.rule_id: r.maturity for r in rules}
    previous = {c.condition_id for r in original for c in r.conditions}
    current = {c.condition_id for r in rules for c in r.conditions}
    assert len(previous) == 1929 and len(current) == 1928
    assert not set(ALIASES) & current
    assert CANONICAL_CONDITION in current
    actual = {r.rule_id: r for r in rules}
    for baseline in build_civil_code_rules(provisions).rules:
        assert actual[baseline.rule_id] == baseline
    shared = [c for r in rules for c in r.conditions if c.condition_id == CANONICAL_CONDITION]
    assert len(shared) == 2 and shared[0] == shared[1]
    assert len(shared[0].source_span_ids) == 2


@pytest.mark.parametrize("number,suffix,start,end,quote", SPAN_SPECS)
def test_corrected_evidence_quotes_the_premise(correction, number, suffix, start, end, quote):
    provisions, _, rules, spans = correction
    span = next(s for s in spans if s.span_id == f"span.civil-code.{number}.{suffix}")
    provision = next(p for p in provisions if p.article_no == str(number))
    assert span.quote == quote == provision.text[start:end]
    assert span.start_offset == start and span.end_offset == end
    assert any(span.span_id in c.source_span_ids for r in rules for c in r.conditions)


def test_all_embedded_law_spans_have_declared_provisions(correction):
    _, original, rules, _ = correction

    def omissions(records):
        return [
            (r.rule_id, s.span_id)
            for r in records
            for s in r.source_spans
            if s.section.removeprefix("第").removesuffix("条")
            not in {p.article_no for p in r.provisions}
        ]

    assert omissions(original)  # The previous shared-evidence normalization missed parent links.
    assert not omissions(rules)
    for rule in rules:
        for group in rule.condition_groups:
            assert not set(ALIASES) & set(group.member_condition_ids)


def test_every_rule_preserves_semantics_except_declared_condition_aliases(correction):
    _, original, rules, _ = correction

    def semantic_payload(rule):
        payload = rule.model_dump(mode="json", exclude={"provisions", "source_spans"})
        for field in ("conditions", "exceptions", "consequences"):
            for entity in payload[field]:
                entity.pop("source_span_ids")
                if entity.get("condition_id") in ALIASES:
                    entity["condition_id"] = CANONICAL_CONDITION
        for group in payload["condition_groups"]:
            group["member_condition_ids"] = [
                CANONICAL_CONDITION if key in ALIASES else key
                for key in group["member_condition_ids"]
            ]
        return payload

    assert {r.rule_id: semantic_payload(r) for r in rules} == {
        r.rule_id: semantic_payload(r) for r in original
    }


def test_compact_recipe_keeps_original_batch_immutable(correction):
    provisions, _, rules, _ = correction
    assert "rules" not in recipe()
    assert len(json.dumps(recipe())) < 1000
    assert recipe()["base_input_path"] == BASE_PATH
    audit = receipt(recipe(), provisions, rules, ROOT)
    assert audit["maturity_upgraded"] is False
    assert audit["deep_human_review"] is False
    assert audit["unique_conditions_after"] == audit["unique_conditions_before"] - 1
    for field, value in (("base_input_path", "../outside.json"), ("operations", [])):
        forged = copy.deepcopy(recipe())
        forged[field] = value
        with pytest.raises(ValueError, match="配方"):
            materialize(forged, provisions, ROOT)


def test_changed_authority_text_rejected(correction):
    provisions, _, _, _ = correction
    modified = copy.deepcopy(provisions)
    next(p for p in modified if p.article_no == "583").text = "篡改原文"
    with pytest.raises(ValueError, match="原文"):
        materialize(recipe(), modified, ROOT)


def test_published_recipe_replays_exactly(correction):
    _, _, expected, spans = correction
    actual = read_jsonl(ROOT / "canonical/rules/rules.jsonl", RuleRecord)
    assert actual == expected
    manifest = json.loads((ROOT / "manifests/civil_code.manifest.json").read_text("utf-8"))
    assert json.loads((ROOT / manifest["input_path"]).read_text("utf-8")) == recipe()
    assert manifest["outputs"]["rules.jsonl"] == sha256_text(render_jsonl(expected))
    assert manifest["outputs"]["source_spans.jsonl"] == sha256_text(render_jsonl(spans))
    assert validate_expanded(ROOT).rule_count == 776


def test_refinement_receipt_cannot_be_forged(tmp_path):
    root = tmp_path / "data"
    for part in ("canonical/rules", "manifests", "inputs"):
        shutil.copytree(ROOT / part, root / part)
    path = root / "manifests/civil_code.manifest.json"
    payload = json.loads(path.read_text("utf-8"))
    payload["data_refinement"]["deep_human_review"] = True
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValueError, match="回执"):
        validate_expanded(root)


def test_publish_rejects_base_input_changed_during_candidate_validation(tmp_path, monkeypatch):
    from casepath.ingestion.graph import projection
    from casepath.rule_layer.expansion import publish

    root = tmp_path / "data"
    for part in ("canonical", "manifests", "inputs"):
        shutil.copytree(ROOT / part, root / part)
    source = tmp_path / "recipe.json"
    write_json(source, recipe())
    before = {
        p: sha256_file(p)
        for p in [
            *(root / "canonical").rglob("*.jsonl"),
            *(root / "manifests").glob("*.json"),
        ]
    }
    original_load = projection.load_projection

    def mutate_original_input(stage):
        result = original_load(stage)
        path = root / BASE_PATH
        path.write_text(path.read_text("utf-8") + " ", encoding="utf-8")
        return result

    monkeypatch.setattr(projection, "load_projection", mutate_original_input)
    with pytest.raises(ValueError, match="构建期间输入发生变化"):
        publish(source, root)
    assert before == {p: sha256_file(p) for p in before}
