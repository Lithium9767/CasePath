"""Validate canonical P3 case files against contracts, P2 IDs, and the manifest."""

from __future__ import annotations

import re
from pathlib import Path

from pydantic import Field

from casepath.contracts import (
    CaseIndexRecord,
    CaseRecord,
    ContractModel,
    DecisionStatus,
    MaturityLevel,
    ProvisionRecord,
    RuleRecord,
    SourceSpan,
)
from casepath.ingestion.laws.civil_code import CIVIL_CODE_SOURCE_ID
from casepath.ingestion.laws.jsonl import read_jsonl, sha256_file
from casepath.rule_layer.ids import (
    COND_ALTERNATIVE_PERFORMANCE,
    RULE_SERVICE_TERMINATION_REFUND,
)

from .ids import (
    DATASET_ID,
    EXPECTED_CANONICAL_OUTPUT_HASHES,
    EXPECTED_CIVIL_CASE_COUNT,
    EXPECTED_DEEP_CASE_COUNT,
    EXPECTED_LEXREL_REPOSITORY_URL,
    EXPECTED_LEXREL_REVISION,
    EXPECTED_LEXREL_SERVICE_PATH,
    EXPECTED_LEXREL_SERVICE_RECORD_COUNT,
    EXPECTED_LEXREL_SERVICE_SHA256,
    EXPECTED_PROCESSED_CASES_PATH,
    EXPECTED_PROCESSED_CASES_SHA256,
    EXPECTED_UPSTREAM_RECORD_COUNT,
    EXPECTED_UPSTREAM_REPOSITORY_URL,
    EXPECTED_UPSTREAM_REVISION,
    GENERATOR,
    REVIEWED_L2_CASE_IDS,
)
from .manifest import CaseDatasetManifest
from .spc_supplement import (
    CASE_IDS as SPC_CASE_IDS,
)
from .spc_supplement import (
    COUNT as SPC_COUNT,
)
from .spc_supplement import (
    INPUT_PATH as SPC_INPUT_PATH,
)
from .spc_supplement import (
    INPUT_REVISION as SPC_INPUT_REVISION,
)
from .spc_supplement import (
    INPUT_SHA256 as SPC_INPUT_SHA256,
)
from .spc_supplement import (
    SOURCE_URL as SPC_SOURCE_URL,
)


class CaseDatasetValidationReport(ContractModel):
    l1_case_count: int = Field(ge=0)
    deep_case_count: int = Field(ge=0)
    resolved_civil_code_citation_count: int = Field(ge=0)
    status: str


def _require(condition: bool, message: str, errors: list[str]) -> None:
    if not condition:
        errors.append(message)


def _span_document(span: SourceSpan, index: CaseIndexRecord | None) -> str | None:
    if index is None:
        return None
    preferred = (
        [index.reasoning_text, index.facts_text]
        if span.section == "裁判理由"
        else [index.facts_text, index.reasoning_text]
    )
    for document in preferred:
        if span.quote and span.quote in document:
            return document
    return None


def _validate_span(span: SourceSpan, index: CaseIndexRecord | None, errors: list[str]) -> None:
    _require(
        span.end_offset - span.start_offset == len(span.quote),
        f"span {span.span_id} quote length does not match offsets",
        errors,
    )
    document = _span_document(span, index)
    if document is None:
        errors.append(f"span {span.span_id} cannot be located in the canonical case text")
        return
    actual = document[span.start_offset : span.end_offset]
    _require(actual == span.quote, f"span {span.span_id} quote/offset mismatch", errors)


def validate_records(
    lightweight: list[CaseIndexRecord],
    deep: list[CaseRecord],
    provisions: list[ProvisionRecord],
    rules: list[RuleRecord],
) -> CaseDatasetValidationReport:
    errors: list[str] = []
    provision_ids = {item.provision_id for item in provisions}
    rule_ids = {item.rule_id for item in rules}
    condition_ids = {condition.condition_id for rule in rules for condition in rule.conditions}
    l1_ids = [item.case_id for item in lightweight]
    _require(len(l1_ids) == len(set(l1_ids)), "L1 case_id values must be unique", errors)
    l1_by_id = {item.case_id: item for item in lightweight}
    deep_ids = [item.case_id for item in deep]
    _require(len(deep_ids) == len(set(deep_ids)), "deep case_id values must be unique", errors)

    child_ids: dict[str, str] = {}
    resolved_citations = 0
    normalized_facts: set[str] = set()
    for record in lightweight:
        facts = re.sub(r"\s+", "", record.facts_text)
        _require(bool(facts), f"{record.case_id} has empty facts_text", errors)
        _require(facts not in normalized_facts, "duplicate normalized case facts", errors)
        normalized_facts.add(facts)
        _require(
            len(record.keywords) == len(set(record.keywords)),
            f"{record.case_id} has duplicate keywords",
            errors,
        )
        _require(
            record.contract_version == "1.3",
            f"{record.case_id} is not CaseIndexRecord v1.3",
            errors,
        )
        for provision_id in record.related_provision_ids:
            resolved_citations += 1
            _require(
                provision_id in provision_ids,
                f"{record.case_id} cites missing provision {provision_id}",
                errors,
            )
            _require(
                provision_id.startswith(f"{CIVIL_CODE_SOURCE_ID}.article_"),
                f"{record.case_id} has a non-civil-code provision id {provision_id}",
                errors,
            )

    for record in deep:
        _require(record.case_id in l1_by_id, f"deep case {record.case_id} is not in L1", errors)
        _require(
            record.contract_version == "1.1", f"{record.case_id} is not CaseRecord v1.1", errors
        )
        if record.case_id not in REVIEWED_L2_CASE_IDS:
            _require(
                record.maturity == MaturityLevel.L1,
                f"unreviewed case {record.case_id} must remain L1",
                errors,
            )
        else:
            _require(
                record.maturity == MaturityLevel.L2,
                f"reviewed case {record.case_id} must be L2",
                errors,
            )
        for item in record.condition_findings:
            if record.case_id not in REVIEWED_L2_CASE_IDS:
                _require(
                    item.human_verified is False,
                    f"{record.case_id} machine bridge {item.condition_id} is marked verified",
                    errors,
                )
            _require(
                item.condition_id in condition_ids,
                f"{record.case_id} uses unknown condition {item.condition_id}",
                errors,
            )
        for claim in record.claims:
            for rule_id in claim.invoked_rule_ids:
                _require(
                    rule_id in rule_ids, f"{record.case_id} invokes unknown rule {rule_id}", errors
                )
            if RULE_SERVICE_TERMINATION_REFUND in claim.invoked_rule_ids:
                _require(
                    claim.claim_type == "服务合同解除与返还",
                    f"{record.case_id} attached the refund rule to a non-refund claim",
                    errors,
                )
        if record.case_id in REVIEWED_L2_CASE_IDS:
            for decision in record.decisions:
                _require(
                    decision.status != DecisionStatus.UNKNOWN,
                    f"reviewed case {record.case_id} has an unexplained UNKNOWN decision",
                    errors,
                )
        index = l1_by_id.get(record.case_id)
        for span in record.source_spans:
            _validate_span(span, index, errors)
        for owner_id in (
            [claim.claim_id for claim in record.claims]
            + [finding.finding_id for finding in record.findings]
            + [step.reasoning_id for step in record.reasoning_steps]
            + [decision.decision_id for decision in record.decisions]
            + [span.span_id for span in record.source_spans]
        ):
            previous = child_ids.get(owner_id)
            if previous is not None:
                errors.append(f"child id {owner_id} is reused by {previous} and {record.case_id}")
            else:
                child_ids[owner_id] = record.case_id

    if errors:
        raise ValueError("P3 dataset validation failed:\n- " + "\n- ".join(errors))
    return CaseDatasetValidationReport(
        l1_case_count=len(lightweight),
        deep_case_count=len(deep),
        resolved_civil_code_citation_count=resolved_citations,
        status="passed",
    )


# 校验案例数据合法性
def validate_canonical_cases(data_root: Path) -> CaseDatasetValidationReport:
    cases_root = data_root / "canonical" / "cases"
    rules_root = data_root / "canonical" / "rules"
    manifest_path = data_root / "manifests" / "cases.manifest.json"
    lightweight = read_jsonl(cases_root / "cases_l1.jsonl", CaseIndexRecord)
    deep = read_jsonl(cases_root / "cases_deep.jsonl", CaseRecord)
    provisions = read_jsonl(rules_root / "provisions.jsonl", ProvisionRecord)
    rules = read_jsonl(rules_root / "rules.jsonl", RuleRecord)
    report = validate_records(lightweight, deep, provisions, rules)
    light_ids = {record.case_id for record in lightweight}
    deep_ids = {record.case_id for record in deep}
    if light_ids - deep_ids != SPC_CASE_IDS or deep_ids & SPC_CASE_IDS:
        raise ValueError("only the six SPC editorial summaries may remain L1-only")

    manifest = CaseDatasetManifest.model_validate_json(manifest_path.read_bytes())
    _require_manifest_identity(manifest)
    if (
        manifest.l1_case_count != len(lightweight)
        or manifest.deep_case_count != len(deep)
        or manifest.l1_case_count != EXPECTED_CIVIL_CASE_COUNT
        or manifest.deep_case_count != EXPECTED_DEEP_CASE_COUNT
    ):
        raise ValueError("manifest case counts do not match the published files")
    if set(manifest.reviewed_l2_case_ids) != set(REVIEWED_L2_CASE_IDS):
        raise ValueError("manifest review allowlist does not match REVIEWED_L2_CASE_IDS")

    manifest_root = data_root.parent
    _validate_manifest_input(manifest, manifest_root)
    expected_outputs = {
        (cases_root / "cases_l1.jsonl").relative_to(manifest_root).as_posix(): len(lightweight),
        (cases_root / "cases_deep.jsonl").relative_to(manifest_root).as_posix(): len(deep),
    }
    manifest_outputs = {Path(item.path).as_posix(): item for item in manifest.outputs}
    if set(manifest_outputs) != set(expected_outputs):
        raise ValueError("manifest output list is incomplete or contains unexpected files")
    for output in manifest.outputs:
        output_path = manifest_root / output.path
        if not output_path.is_file():
            raise ValueError(f"manifest output is missing: {output.path}")
        digest = sha256_file(output_path)
        if digest != output.sha256:
            raise ValueError(f"manifest hash mismatch for {output.path}")
        pinned = EXPECTED_CANONICAL_OUTPUT_HASHES.get(output_path.name)
        if pinned is not None and digest != pinned:
            raise ValueError(f"pinned release hash mismatch for {output.path}")
        if output.record_count != expected_outputs[Path(output.path).as_posix()]:
            raise ValueError(f"manifest record count mismatch for {output.path}")
        if output.repository_url is not None or output.revision is not None:
            raise ValueError(f"manifest output must not claim upstream identity: {output.path}")
    refund_cases = [
        record
        for record in deep
        if any(RULE_SERVICE_TERMINATION_REFUND in claim.invoked_rule_ids for claim in record.claims)
    ]
    if refund_cases and any(
        COND_ALTERNATIVE_PERFORMANCE
        not in {item.condition_id for item in record.condition_findings}
        for record in refund_cases
    ):
        raise ValueError("refund deep cases must keep cond.alternative_performance")
    return report


def _require_manifest_identity(manifest: CaseDatasetManifest) -> None:
    if (
        manifest.dataset_id != DATASET_ID
        or manifest.generator != GENERATOR
        or manifest.upstream_repository_url != EXPECTED_UPSTREAM_REPOSITORY_URL
        or manifest.upstream_revision != EXPECTED_UPSTREAM_REVISION
        or manifest.contract_versions != {"CaseIndexRecord": "1.3", "CaseRecord": "1.1"}
    ):
        raise ValueError("manifest dataset or upstream identity is inconsistent")


def _validate_manifest_input(manifest: CaseDatasetManifest, manifest_root: Path) -> None:
    expected_paths = {EXPECTED_PROCESSED_CASES_PATH, EXPECTED_LEXREL_SERVICE_PATH, SPC_INPUT_PATH}
    inputs = {Path(item.path).as_posix(): item for item in manifest.inputs}
    if len(inputs) != len(manifest.inputs) or set(inputs) != expected_paths:
        raise ValueError("manifest must record unique processed-cases, LexRel and SPC inputs")
    item = inputs[EXPECTED_PROCESSED_CASES_PATH]
    if (
        Path(item.path).as_posix() != EXPECTED_PROCESSED_CASES_PATH
        or item.sha256 != EXPECTED_PROCESSED_CASES_SHA256
        or item.record_count != EXPECTED_UPSTREAM_RECORD_COUNT
        or item.repository_url != EXPECTED_UPSTREAM_REPOSITORY_URL
        or item.revision != EXPECTED_UPSTREAM_REVISION
    ):
        raise ValueError("manifest input metadata mismatch for processed_cases.json")
    input_path = manifest_root / item.path
    if input_path.is_file() and sha256_file(input_path) != item.sha256:
        raise ValueError(f"manifest hash mismatch for {item.path}")
    lexrel = inputs[EXPECTED_LEXREL_SERVICE_PATH]
    if (
        Path(lexrel.path).as_posix() != EXPECTED_LEXREL_SERVICE_PATH
        or lexrel.sha256 != EXPECTED_LEXREL_SERVICE_SHA256
        or lexrel.record_count != EXPECTED_LEXREL_SERVICE_RECORD_COUNT
        or lexrel.repository_url != EXPECTED_LEXREL_REPOSITORY_URL
        or lexrel.revision != EXPECTED_LEXREL_REVISION
    ):
        raise ValueError("manifest input metadata mismatch for lexrel_service_contract.json")
    lexrel_path = manifest_root / lexrel.path
    if lexrel_path.is_file() and sha256_file(lexrel_path) != lexrel.sha256:
        raise ValueError(f"manifest hash mismatch for {lexrel.path}")
    spc = inputs[SPC_INPUT_PATH]
    if (
        spc.sha256 != SPC_INPUT_SHA256
        or spc.record_count != SPC_COUNT
        or spc.repository_url != SPC_SOURCE_URL
        or spc.revision != SPC_INPUT_REVISION
    ):
        raise ValueError("manifest input metadata mismatch for SPC summaries")
    spc_path = manifest_root / spc.path
    if spc_path.is_file() and sha256_file(spc_path) != spc.sha256:
        raise ValueError(f"manifest hash mismatch for {spc.path}")
