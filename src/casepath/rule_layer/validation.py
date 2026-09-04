from __future__ import annotations

import re
from pathlib import Path

from pydantic import Field

from casepath.contracts import (
    ContractModel,
    LegalSourceRecord,
    MaturityLevel,
    ProvisionRecord,
    RuleRecord,
    SourceSpan,
)
from casepath.ingestion.laws.civil_code import (
    CIVIL_CODE_SOURCE_ID,
    EXPECTED_ARTICLE_COUNT,
    EXPECTED_HIERARCHY_REPAIR_COUNT,
    EXPECTED_SOURCE_SHA256,
    EXPECTED_STATS_SHA256,
    EXPECTED_UPSTREAM_REVISION,
    OFFICIAL_SOURCE_URL,
    full_span_id,
    provision_id,
)
from casepath.ingestion.laws.jsonl import read_jsonl, sha256_file, sha256_text
from casepath.ingestion.laws.manifest import CivilCodeManifest
from casepath.rule_layer.ids import (
    HUMAN_VERIFIED_L3_RULE_IDS,
    RULE_SERVICE_TERMINATION_REFUND,
)

EXPECTED_ARTICLE_CONTENT_HASHES = {
    509: "3f9009a6ce151a1c390523496b98f7af0998d98ce267434647c6c53ceb3e6a0d",
    563: "b2843b53de36c81bc34294ef794a3d394fe7203d710349523327350acb71f99e",
    565: "b3fa76ca698207895546886e484687576594404d5bc1aae451c5339d44a46319",
    566: "787b167a1446d83375fb8e0181a2c3bd777026535dad4c74c5682fa9862e843b",
}
EXPECTED_NORMALIZED_ARTICLE_HASHES = {
    509: "6092893c19fc13285fba96971bc601984509b92c926f45e1275e61e0fa2f12af",
    563: "6f8865de12e8358f28a6530d1d76bc1809ea1f6583a4976cd71816c6f67047e9",
    565: "52b64f120319276e2a3d437f5d73e6e67383c1eefcf2821f7fe43aa1c54e2c5c",
    566: "256a6da55401e696895bc77b22ab5fee4c7ecf253507f8c2d07d12be21e41dc3",
}
EXPECTED_UPSTREAM_REPOSITORY_URL = "https://github.com/litunan/legal-rag"
EXPECTED_AUTHORITY_URLS = {
    "https://flk.npc.gov.cn/detail?id=ff808081729d1efe01729d50b5c500bf",
    "https://www.npc.gov.cn/wxzlhgb/c27214/gb2020/202006/P020230313538731037747.pdf",
    OFFICIAL_SOURCE_URL,
}
EXPECTED_CANONICAL_OUTPUT_HASHES = {
    "legal_sources.jsonl": "20f6ad08e07b7c67b2f9a2ac35692de0d9912e7c39191226be9bf298258373c4",
    "provisions.jsonl": "6a84ab569c5415f0fb595d90f615c9b2d5206ca3f96b93fa85a918d747094c93",
    "rules.jsonl": "e2e32282d01564eaed710aeef731ded2011a1dd85c94cb7518204bc0f708a07d",
    "source_spans.jsonl": "4bd95c7ba5f8a6f4598c15511999939537e77314ac5830702ac4581f71ff05f2",
}


class DatasetValidationReport(ContractModel):
    legal_source_count: int = Field(ge=0)
    provision_count: int = Field(ge=0)
    rule_count: int = Field(ge=0)
    l3_rule_count: int = Field(ge=0)
    source_span_count: int = Field(ge=0)
    checked_article_numbers: list[int]
    status: str


def _require(condition: bool, message: str, errors: list[str]) -> None:
    if not condition:
        errors.append(message)


def _validate_span(
    span: SourceSpan,
    provision_by_number: dict[int, ProvisionRecord],
    errors: list[str],
) -> None:
    match = re.fullmatch(r"第(\d+)条", span.section or "")
    if not match:
        errors.append(f"span {span.span_id} has no parseable article section")
        return
    article_number = int(match.group(1))
    provision = provision_by_number.get(article_number)
    if provision is None:
        errors.append(f"span {span.span_id} references missing article {article_number}")
        return
    _require(
        span.source_id == provision.source_id,
        f"span {span.span_id} source_id differs from article {article_number}",
        errors,
    )
    if span.end_offset > len(provision.text):
        errors.append(f"span {span.span_id} ends outside article text")
        return
    actual_quote = provision.text[span.start_offset : span.end_offset]
    _require(actual_quote == span.quote, f"span {span.span_id} quote/offset mismatch", errors)
    _require(
        span.content_hash == sha256_text(span.quote),
        f"span {span.span_id} content hash mismatch",
        errors,
    )


def validate_records(
    legal_sources: list[LegalSourceRecord],
    provisions: list[ProvisionRecord],
    rules: list[RuleRecord],
    source_spans: list[SourceSpan],
) -> DatasetValidationReport:
    errors: list[str] = []
    _require(len(legal_sources) == 1, "exactly one LegalSourceRecord is required", errors)
    _require(
        len(provisions) == EXPECTED_ARTICLE_COUNT,
        f"expected {EXPECTED_ARTICLE_COUNT} provisions, received {len(provisions)}",
        errors,
    )

    provision_ids = [record.provision_id for record in provisions]
    article_numbers = [int(record.article_no) for record in provisions]
    span_ids = [span.span_id for span in source_spans]
    rule_ids = [rule.rule_id for rule in rules]
    _require(len(provision_ids) == len(set(provision_ids)), "duplicate provision_id", errors)
    _require(len(span_ids) == len(set(span_ids)), "duplicate source span ID", errors)
    _require(len(rule_ids) == len(set(rule_ids)), "duplicate rule_id", errors)
    _require(
        article_numbers == list(range(1, EXPECTED_ARTICLE_COUNT + 1)),
        "article numbers must be ordered, unique, and contiguous from 1 to 1260",
        errors,
    )
    _require(3 <= len(rules) <= 5, "P2 must publish between three and five rules", errors)
    _require(RULE_SERVICE_TERMINATION_REFUND in rule_ids, "demo rule ID is missing", errors)
    l3_rule_ids = {rule.rule_id for rule in rules if rule.maturity == MaturityLevel.L3}
    _require(len(l3_rule_ids) >= 3, "P2 must publish at least three L3 rules", errors)
    _require(
        l3_rule_ids == HUMAN_VERIFIED_L3_RULE_IDS,
        "L3 rule set differs from the explicit reviewed allowlist",
        errors,
    )

    provision_by_number = {int(record.article_no): record for record in provisions}
    provision_by_id = {record.provision_id: record for record in provisions}
    span_by_id = {span.span_id: span for span in source_spans}
    if legal_sources:
        legal_source = legal_sources[0]
        canonical_content = "\n".join(
            f"{record.article_no}\t{record.text}" for record in provisions
        )
        _require(
            legal_source.content_hash == sha256_text(canonical_content),
            "LegalSourceRecord content hash mismatch",
            errors,
        )
        _require(
            legal_source.source_id == CIVIL_CODE_SOURCE_ID,
            "unexpected LegalSourceRecord source_id",
            errors,
        )
        expected_source_metadata = {
            "title": "中华人民共和国民法典",
            "authority": "全国人民代表大会",
            "document_type": "法律",
            "promulgated_on": "2020-05-28",
            "valid_from": "2021-01-01",
            "valid_to": None,
            "effective_status": "effective",
            "jurisdiction": "中华人民共和国",
            "official_source_url": OFFICIAL_SOURCE_URL,
        }
        actual_source_metadata = legal_source.model_dump(mode="json", exclude={"content_hash"})
        actual_source_metadata.pop("contract_version", None)
        actual_source_metadata.pop("source_id", None)
        _require(
            actual_source_metadata == expected_source_metadata,
            "LegalSourceRecord metadata differs from the verified Civil Code source",
            errors,
        )
    for number, provision in provision_by_number.items():
        _require(
            provision.provision_id == provision_id(number), "non-canonical provision ID", errors
        )
        _require(
            provision.source_id == CIVIL_CODE_SOURCE_ID,
            f"article {number} has unexpected source_id",
            errors,
        )
        _require(bool(provision.text.strip()), f"article {number} has empty text", errors)
        _require(
            provision.title == f"中华人民共和国民法典第{number}条",
            f"article {number} has non-canonical title",
            errors,
        )
        _require(
            provision.valid_from.isoformat() == "2021-01-01"
            and provision.valid_to is None
            and provision.effective_status == "effective"
            and provision.jurisdiction == "中华人民共和国"
            and provision.maturity == MaturityLevel.L0,
            f"article {number} has inconsistent version metadata",
            errors,
        )
        _require(
            provision.content_hash == sha256_text(provision.text),
            f"article {number} content hash mismatch",
            errors,
        )
        expected_full_span_id = full_span_id(number)
        _require(
            provision.source_span_ids == [expected_full_span_id],
            f"article {number} must reference its full source span",
            errors,
        )
        full_span = span_by_id.get(expected_full_span_id)
        _require(full_span is not None, f"article {number} full source span is missing", errors)
        if full_span is not None:
            _require(
                full_span.quote == provision.text, f"article {number} full span mismatch", errors
            )

    for span in source_spans:
        _validate_span(span, provision_by_number, errors)

    for article_number, expected_hash in EXPECTED_ARTICLE_CONTENT_HASHES.items():
        provision = provision_by_number.get(article_number)
        _require(provision is not None, f"article {article_number} is missing", errors)
        if provision is not None:
            _require(
                sha256_text(provision.text) == expected_hash,
                f"article {article_number} differs from the verified text",
                errors,
            )

    # The manifest's review status applies to these exact curated semantics, not merely
    # to a stable rule_id. Rebuild the deterministic definitions to prevent a caller from
    # changing a rule and blessing it by recomputing only the file hash.
    if {509, 563, 565, 566} <= provision_by_number.keys():
        from casepath.rule_layer.civil_code import build_civil_code_rules

        expected_rule_build = build_civil_code_rules(provisions)
        _require(
            rules == expected_rule_build.rules,
            "rules differ from the reviewed deterministic generator output",
            errors,
        )
        expected_span_ids = {
            *(full_span_id(number) for number in provision_by_number),
            *(span.span_id for span in expected_rule_build.source_spans),
        }
        _require(
            set(span_by_id) == expected_span_ids,
            "source span set differs from the deterministic generator output",
            errors,
        )

    expected_book_boundaries = {
        204: "第一编 总则",
        205: "第二编 物权",
        462: "第二编 物权",
        463: "第三编 合同",
        988: "第三编 合同",
        989: "第四编 人格权",
        1039: "第四编 人格权",
        1040: "第五编 婚姻家庭",
        1118: "第五编 婚姻家庭",
        1119: "第六编 继承",
        1163: "第六编 继承",
        1164: "第七编 侵权责任",
        1258: "第七编 侵权责任",
        1259: "附则",
    }
    for article_number, expected_book in expected_book_boundaries.items():
        provision = provision_by_number.get(article_number)
        if provision is not None:
            _require(
                provision.book == expected_book,
                f"article {article_number} has incorrect book hierarchy",
                errors,
            )

    condition_by_id = {}
    for rule in rules:
        _require(bool(rule.source_spans), f"rule {rule.rule_id} has no embedded spans", errors)
        if rule.maturity == MaturityLevel.L3:
            _require(bool(rule.conditions), f"L3 rule {rule.rule_id} has no conditions", errors)
            _require(bool(rule.consequences), f"L3 rule {rule.rule_id} has no consequences", errors)
        embedded_span_ids = {span.span_id for span in rule.source_spans}
        referenced_provision_ids = {reference.provision_id for reference in rule.provisions}
        for reference in rule.provisions:
            target = provision_by_id.get(reference.provision_id)
            _require(
                target is not None,
                f"rule {rule.rule_id} references missing provision {reference.provision_id}",
                errors,
            )
            if target is not None:
                expected_reference = {
                    "source_id": target.source_id,
                    "provision_id": target.provision_id,
                    "article_no": target.article_no,
                    "title": target.title,
                    "valid_from": target.valid_from,
                    "valid_to": target.valid_to,
                }
                _require(
                    reference.model_dump() == expected_reference,
                    f"rule {rule.rule_id} has inconsistent ProvisionRef {reference.provision_id}",
                    errors,
                )
        referenced_span_ids = {
            span_id
            for item in [*rule.conditions, *rule.exceptions, *rule.consequences]
            for span_id in item.source_span_ids
        }
        for item in [*rule.conditions, *rule.exceptions, *rule.consequences]:
            _require(
                bool(item.source_span_ids),
                f"rule {rule.rule_id} contains an item without a source span",
                errors,
            )
        for condition in rule.conditions:
            if rule.maturity == MaturityLevel.L3:
                _require(
                    bool(condition.evidence_types),
                    f"L3 rule {rule.rule_id} condition {condition.condition_id} has no evidence types",
                    errors,
                )
            existing = condition_by_id.get(condition.condition_id)
            _require(
                existing is None or existing == condition,
                f"condition {condition.condition_id} has conflicting definitions across rules",
                errors,
            )
            condition_by_id.setdefault(condition.condition_id, condition)
        for span_id in referenced_span_ids:
            _require(
                span_id in span_by_id,
                f"rule {rule.rule_id} references missing global span {span_id}",
                errors,
            )
            _require(
                span_id in embedded_span_ids,
                f"rule {rule.rule_id} does not embed referenced span {span_id}",
                errors,
            )
        for embedded_span in rule.source_spans:
            global_span = span_by_id.get(embedded_span.span_id)
            _require(
                global_span == embedded_span,
                f"rule {rule.rule_id} embedded span differs from canonical span",
                errors,
            )
            match = re.fullmatch(r"第(\d+)条", embedded_span.section or "")
            if match:
                expected_provision_id = provision_id(int(match.group(1)))
                _require(
                    expected_provision_id in referenced_provision_ids,
                    f"rule {rule.rule_id} embeds a span without ProvisionRef {expected_provision_id}",
                    errors,
                )

    if errors:
        raise ValueError("P2 dataset validation failed:\n- " + "\n- ".join(errors))
    return DatasetValidationReport(
        legal_source_count=len(legal_sources),
        provision_count=len(provisions),
        rule_count=len(rules),
        l3_rule_count=sum(rule.maturity == MaturityLevel.L3 for rule in rules),
        source_span_count=len(source_spans),
        checked_article_numbers=sorted(EXPECTED_ARTICLE_CONTENT_HASHES),
        status="passed",
    )


def validate_canonical_dataset(data_root: Path) -> DatasetValidationReport:
    canonical_root = data_root / "canonical" / "rules"
    manifest_path = data_root / "manifests" / "civil_code.manifest.json"
    legal_sources = read_jsonl(canonical_root / "legal_sources.jsonl", LegalSourceRecord)
    provisions = read_jsonl(canonical_root / "provisions.jsonl", ProvisionRecord)
    rules = read_jsonl(canonical_root / "rules.jsonl", RuleRecord)
    source_spans = read_jsonl(canonical_root / "source_spans.jsonl", SourceSpan)
    report = validate_records(legal_sources, provisions, rules, source_spans)

    manifest = CivilCodeManifest.model_validate_json(manifest_path.read_bytes())
    if (
        manifest.dataset_id != "dataset.civil_code.rules.v1"
        or manifest.source_id != CIVIL_CODE_SOURCE_ID
        or manifest.generator != "casepath.rule_layer.build:v1"
        or manifest.upstream_repository_url != EXPECTED_UPSTREAM_REPOSITORY_URL
        or manifest.upstream_revision != EXPECTED_UPSTREAM_REVISION
    ):
        raise ValueError("manifest dataset or upstream identity is inconsistent")
    manifest_inputs = {Path(item.path).as_posix(): item for item in manifest.inputs}
    expected_inputs = {
        "../legal-rag/data/laws/民法典_法条.json": (EXPECTED_SOURCE_SHA256, 1260),
        "../legal-rag/data/laws/民法典_统计.json": (EXPECTED_STATS_SHA256, 1),
    }
    if len(manifest.inputs) != len(expected_inputs) or set(manifest_inputs) != set(expected_inputs):
        raise ValueError("manifest input list is incomplete or contains unexpected files")
    for filename, (expected_hash, expected_count) in expected_inputs.items():
        manifest_input = manifest_inputs[filename]
        if (
            manifest_input.sha256 != expected_hash
            or manifest_input.record_count != expected_count
            or manifest_input.repository_url != EXPECTED_UPSTREAM_REPOSITORY_URL
            or manifest_input.revision != manifest.upstream_revision
        ):
            raise ValueError(f"manifest input metadata mismatch for {filename}")
    manifest_root = data_root.parent
    expected_outputs = {
        (canonical_root / "legal_sources.jsonl").relative_to(manifest_root).as_posix(): len(
            legal_sources
        ),
        (canonical_root / "provisions.jsonl").relative_to(manifest_root).as_posix(): len(
            provisions
        ),
        (canonical_root / "rules.jsonl").relative_to(manifest_root).as_posix(): len(rules),
        (canonical_root / "source_spans.jsonl").relative_to(manifest_root).as_posix(): len(
            source_spans
        ),
    }
    manifest_outputs = {Path(output.path).as_posix(): output for output in manifest.outputs}
    if len(manifest.outputs) != len(expected_outputs) or set(manifest_outputs) != set(
        expected_outputs
    ):
        raise ValueError("manifest output list is incomplete or contains unexpected files")
    for output in manifest.outputs:
        normalized_path = Path(output.path).as_posix()
        output_path = manifest_root / normalized_path
        if not output_path.exists():
            raise ValueError(f"manifest output is missing: {output.path}")
        if sha256_file(output_path) != output.sha256:
            raise ValueError(f"manifest hash mismatch for {output.path}")
        expected_release_hash = EXPECTED_CANONICAL_OUTPUT_HASHES[output_path.name]
        if output.sha256 != expected_release_hash:
            raise ValueError(f"pinned release hash mismatch for {output.path}")
        expected_record_count = expected_outputs[normalized_path]
        if output.record_count != expected_record_count:
            raise ValueError(f"manifest record count mismatch for {output.path}")
        if output.repository_url is not None or output.revision is not None:
            raise ValueError(f"manifest output must not claim upstream identity: {output.path}")

    expected_rule_statuses = {
        rule.rule_id: (
            "human_verified"
            if rule.rule_id in HUMAN_VERIFIED_L3_RULE_IDS
            else "needs_additional_authority"
        )
        for rule in rules
    }
    if manifest.rule_review_status != expected_rule_statuses:
        raise ValueError("manifest rule_review_status does not match rules.jsonl")
    if len(manifest.transformations) != 1:
        raise ValueError("manifest must contain exactly one hierarchy repair transformation")
    hierarchy_repair = manifest.transformations[0]
    if (
        hierarchy_repair.transformation_id != "repair.upstream_hierarchy.flush_order.v1"
        or hierarchy_repair.affected_records != EXPECTED_HIERARCHY_REPAIR_COUNT
        or hierarchy_repair.guard_sha256 != EXPECTED_SOURCE_SHA256
    ):
        raise ValueError("manifest hierarchy repair metadata is inconsistent")
    if manifest.authority_verification.verified_on != manifest.generated_on:
        raise ValueError("manifest verification date must match its generation date")
    if (
        len(manifest.authority_verification.source_urls) != len(EXPECTED_AUTHORITY_URLS)
        or set(manifest.authority_verification.source_urls) != EXPECTED_AUTHORITY_URLS
    ):
        raise ValueError("manifest authority source URLs are incomplete or unexpected")
    if set(manifest.authority_verification.checked_article_numbers) != {509, 563, 565, 566}:
        raise ValueError("manifest must record authority verification for articles 509/563/565/566")
    disk_provision_by_number = {int(item.article_no): item for item in provisions}
    expected_normalized_hashes = {
        str(article_number): sha256_text(
            "".join(disk_provision_by_number[article_number].text.split())
        )
        for article_number in EXPECTED_NORMALIZED_ARTICLE_HASHES
    }
    if expected_normalized_hashes != {
        str(key): value
        for key, value in manifest.authority_verification.whitespace_normalized_sha256.items()
    }:
        raise ValueError("manifest normalized article hashes do not match canonical provisions")
    if expected_normalized_hashes != {
        str(key): value for key, value in EXPECTED_NORMALIZED_ARTICLE_HASHES.items()
    }:
        raise ValueError("canonical provisions do not match authority-verified normalized hashes")
    return report
