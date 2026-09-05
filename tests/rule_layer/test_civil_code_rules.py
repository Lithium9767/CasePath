from __future__ import annotations

from datetime import date

import pytest

from casepath.contracts import (
    ConditionGroupOperator,
    MaturityLevel,
    ProvisionRecord,
    SourceSpan,
)
from casepath.ingestion.laws.civil_code import (
    CIVIL_CODE_SOURCE_ID,
    full_span_id,
    provision_id,
)
from casepath.ingestion.laws.jsonl import sha256_text
from casepath.rule_layer.civil_code import build_civil_code_rules
from casepath.rule_layer.ids import (
    COND_ALTERNATIVE_PERFORMANCE,
    COND_CONTRACT_EXISTS,
    COND_PAYMENT_MADE,
    COND_PERFORMANCE_IMPOSSIBLE,
    COND_UNPERFORMED_BALANCE,
    RULE_DELAY_AFTER_DEMAND,
    RULE_GOOD_FAITH,
    RULE_NONPERFORMANCE_TERMINATION,
    RULE_SERVICE_TERMINATION_REFUND,
    RULE_TERMINATION_RESTITUTION,
)

ARTICLE_TEXTS = {
    509: (
        "当事人应当按照约定全面履行自己的义务。 "
        "当事人应当遵循诚信原则，根据合同的性质、目的和交易习惯履行通知、协助、保密等义务。 "
        "当事人在履行合同过程中，应当避免浪费资源、污染环境和破坏生态。"
    ),
    563: (
        "有下列情形之一的，当事人可以解除合同： "
        "（一）因不可抗力致使不能实现合同目的； "
        "（二）在履行期限届满前，当事人一方明确表示或者以自己的行为表明不履行主要债务； "
        "（三）当事人一方迟延履行主要债务，经催告后在合理期限内仍未履行； "
        "（四）当事人一方迟延履行债务或者有其他违约行为致使不能实现合同目的； "
        "（五）法律规定的其他情形。 "
        "以持续履行的债务为内容的不定期合同，当事人可以随时解除合同，但是应当在合理期限之前通知对方。"
    ),
    565: (
        "当事人一方依法主张解除合同的，应当通知对方。合同自通知到达对方时解除；"
        "通知载明债务人在一定期限内不履行债务则合同自动解除，债务人在该期限内未履行债务的，"
        "合同自通知载明的期限届满时解除。对方对解除合同有异议的，任何一方当事人均可以请求"
        "人民法院或者仲裁机构确认解除行为的效力。 当事人一方未通知对方，直接以提起诉讼或者"
        "申请仲裁的方式依法主张解除合同，人民法院或者仲裁机构确认该主张的，合同自起诉状副本"
        "或者仲裁申请书副本送达对方时解除。"
    ),
    566: (
        "合同解除后，尚未履行的，终止履行；已经履行的，根据履行情况和合同性质，当事人可以请求恢复原状"
        "或者采取其他补救措施，并有权请求赔偿损失。 "
        "合同因违约解除的，解除权人可以请求违约方承担违约责任，但是当事人另有约定的除外。 "
        "主合同解除后，担保人对债务人应当承担的民事责任仍应当承担担保责任，但是担保合同另有约定的除外。"
    ),
}

EXPECTED_TEXT_HASHES = {
    509: "3f9009a6ce151a1c390523496b98f7af0998d98ce267434647c6c53ceb3e6a0d",
    563: "b2843b53de36c81bc34294ef794a3d394fe7203d710349523327350acb71f99e",
    565: "b3fa76ca698207895546886e484687576594404d5bc1aae451c5339d44a46319",
    566: "787b167a1446d83375fb8e0181a2c3bd777026535dad4c74c5682fa9862e843b",
}

EXPECTED_RULE_IDS = {
    RULE_GOOD_FAITH,
    RULE_DELAY_AFTER_DEMAND,
    RULE_NONPERFORMANCE_TERMINATION,
    RULE_TERMINATION_RESTITUTION,
    RULE_SERVICE_TERMINATION_REFUND,
}


def _provision(article_number: int) -> ProvisionRecord:
    text = ARTICLE_TEXTS[article_number]
    return ProvisionRecord(
        provision_id=provision_id(article_number),
        source_id=CIVIL_CODE_SOURCE_ID,
        article_no=str(article_number),
        title=f"中华人民共和国民法典第{article_number}条",
        text=text,
        effective_from=date(2021, 1, 1),
        source_spans=[
            SourceSpan(
                span_id=full_span_id(article_number),
                source_id=CIVIL_CODE_SOURCE_ID,
                section=f"第{article_number}条",
                paragraph_id=f"article-{article_number:04d}-full",
                start_offset=0,
                end_offset=len(text),
                quote=text,
            )
        ],
    )


def _required_provisions() -> list[ProvisionRecord]:
    return [_provision(number) for number in (509, 563, 565, 566)]


def test_verified_article_text_hashes_are_fixed() -> None:
    assert {
        article_number: sha256_text(text) for article_number, text in ARTICLE_TEXTS.items()
    } == EXPECTED_TEXT_HASHES


def test_rule_builder_publishes_four_reviewed_l3_rules_and_one_bounded_l2_rule() -> None:
    result = build_civil_code_rules(_required_provisions())

    assert len(result.rules) == 5
    assert {rule.rule_id for rule in result.rules} == EXPECTED_RULE_IDS
    assert {rule.contract_version for rule in result.rules} == {"1.1"}
    assert sum(rule.maturity == MaturityLevel.L3 for rule in result.rules) == 4
    demo_rule = next(
        rule for rule in result.rules if rule.rule_id == RULE_SERVICE_TERMINATION_REFUND
    )
    assert demo_rule.maturity == MaturityLevel.L2
    assert all(rule.conditions for rule in result.rules)
    assert all(rule.consequences for rule in result.rules)
    assert all(condition.evidence_types for rule in result.rules for condition in rule.conditions)


def test_every_condition_is_covered_by_an_all_group() -> None:
    result = build_civil_code_rules(_required_provisions())

    for rule in result.rules:
        assert len(rule.condition_groups) == 1
        group = rule.condition_groups[0]
        assert group.operator == ConditionGroupOperator.ALL
        assert group.member_condition_ids == [
            condition.condition_id for condition in rule.conditions
        ]


def test_demo_rule_preserves_frozen_cross_team_ids_and_padded_provision_ids() -> None:
    result = build_civil_code_rules(_required_provisions())
    demo_rule = next(
        rule for rule in result.rules if rule.rule_id == RULE_SERVICE_TERMINATION_REFUND
    )

    condition_ids = {condition.condition_id for condition in demo_rule.conditions}
    assert {
        COND_CONTRACT_EXISTS,
        COND_PAYMENT_MADE,
        COND_UNPERFORMED_BALANCE,
        COND_PERFORMANCE_IMPOSSIBLE,
    } <= condition_ids
    assert COND_ALTERNATIVE_PERFORMANCE not in condition_ids
    assert "cond.performance.impossible" not in condition_ids
    assert len(demo_rule.exceptions) == 1
    alternative = demo_rule.exceptions[0]
    assert alternative.exception_id == COND_ALTERNATIVE_PERFORMANCE
    assert alternative.predicate == "替代服务是否符合约定并仍足以实现原合同目的"
    assert alternative.effect == "例外成立时，不能仅以原履行方案变化认定合同目的不能实现。"
    assert {reference.provision_id for reference in demo_rule.provisions} == {
        f"{CIVIL_CODE_SOURCE_ID}.article_0509",
        f"{CIVIL_CODE_SOURCE_ID}.article_0563",
        f"{CIVIL_CODE_SOURCE_ID}.article_0565",
        f"{CIVIL_CODE_SOURCE_ID}.article_0566",
    }
    assert {reference.valid_from for reference in demo_rule.provisions} == {date(2021, 1, 1)}
    assert all(reference.valid_to is None for reference in demo_rule.provisions)


def test_every_rule_span_replays_against_the_referenced_article() -> None:
    provisions = _required_provisions()
    result = build_civil_code_rules(provisions)
    text_by_article = {record.article_no: record.text for record in provisions}
    global_spans = {span.span_id: span for span in result.source_spans}

    for span in result.source_spans:
        article_number = (span.section or "").removeprefix("第").removesuffix("条")
        article_text = text_by_article[article_number]
        assert 0 <= span.start_offset < span.end_offset <= len(article_text)
        assert article_text[span.start_offset : span.end_offset] == span.quote

    for rule in result.rules:
        embedded = {span.span_id: span for span in rule.source_spans}
        referenced = {
            span_id
            for item in [*rule.conditions, *rule.exceptions, *rule.consequences]
            for span_id in item.source_span_ids
        }
        assert referenced <= embedded.keys()
        assert all(embedded[span_id] == global_spans[span_id] for span_id in referenced)


def test_reused_condition_ids_have_one_canonical_definition() -> None:
    result = build_civil_code_rules(_required_provisions())
    conditions_by_id = {}
    exceptions_by_id = {}

    for rule in result.rules:
        for condition in rule.conditions:
            assert (
                condition.condition_id not in conditions_by_id
                or conditions_by_id[condition.condition_id] == condition
            )
            conditions_by_id.setdefault(condition.condition_id, condition)
        for exception in rule.exceptions:
            assert (
                exception.exception_id not in exceptions_by_id
                or exceptions_by_id[exception.exception_id] == exception
            )
            exceptions_by_id.setdefault(exception.exception_id, exception)


def test_rule_builder_requires_articles_509_563_565_and_566() -> None:
    with pytest.raises(ValueError, match="missing required Civil Code articles"):
        build_civil_code_rules(_required_provisions()[:-1])
