from __future__ import annotations

from dataclasses import dataclass

from casepath.contracts import (
    ConditionOperator,
    LegalConsequence,
    MaturityLevel,
    ProvisionRecord,
    ProvisionRef,
    RuleCondition,
    RuleRecord,
    SourceSpan,
)
from casepath.ingestion.laws.civil_code import CIVIL_CODE_SOURCE_ID
from casepath.ingestion.laws.jsonl import sha256_text

from .ids import (
    COND_ALTERNATIVE_PERFORMANCE,
    COND_CONTRACT_EXISTS,
    COND_CONTRACT_TERMINATED,
    COND_DEMAND_DELIVERED,
    COND_MAIN_OBLIGATION_DELAYED,
    COND_PAYMENT_MADE,
    COND_PERFORMANCE_IMPOSSIBLE,
    COND_REASONABLE_PERIOD_EXPIRED,
    COND_UNPERFORMED_BALANCE,
    RULE_DELAY_AFTER_DEMAND,
    RULE_GOOD_FAITH,
    RULE_NONPERFORMANCE_TERMINATION,
    RULE_SERVICE_TERMINATION_REFUND,
    RULE_TERMINATION_RESTITUTION,
)


@dataclass(frozen=True)
class RuleBuildResult:
    rules: list[RuleRecord]
    source_spans: list[SourceSpan]


def _by_article_number(provisions: list[ProvisionRecord]) -> dict[int, ProvisionRecord]:
    result = {int(provision.article_no): provision for provision in provisions}
    required = {509, 563, 565, 566}
    missing = sorted(required - result.keys())
    if missing:
        raise ValueError(f"missing required Civil Code articles: {missing}")
    return result


def _provision_ref(provision: ProvisionRecord) -> ProvisionRef:
    return ProvisionRef(
        source_id=provision.source_id,
        provision_id=provision.provision_id,
        article_no=provision.article_no,
        title=provision.title,
        valid_from=provision.valid_from,
        valid_to=provision.valid_to,
    )


def _clause_span(provision: ProvisionRecord, suffix: str, quote: str) -> SourceSpan:
    start_offset = provision.text.find(quote)
    if start_offset < 0:
        raise ValueError(f"article {provision.article_no} does not contain verified quote: {quote}")
    end_offset = start_offset + len(quote)
    return SourceSpan(
        span_id=f"span.civil-code.{provision.article_no}.{suffix}",
        source_id=CIVIL_CODE_SOURCE_ID,
        section=f"第{provision.article_no}条",
        paragraph_id=f"article-{int(provision.article_no):04d}-{suffix}",
        start_offset=start_offset,
        end_offset=end_offset,
        quote=quote,
        content_hash=sha256_text(quote),
    )


def build_civil_code_rules(provisions: list[ProvisionRecord]) -> RuleBuildResult:
    by_number = _by_article_number(provisions)
    article_509 = by_number[509]
    article_563 = by_number[563]
    article_565 = by_number[565]
    article_566 = by_number[566]

    span_509_performance = _clause_span(
        article_509,
        "paragraph-1",
        "当事人应当按照约定全面履行自己的义务。",
    )
    span_509_good_faith = _clause_span(
        article_509,
        "paragraph-2",
        "当事人应当遵循诚信原则，根据合同的性质、目的和交易习惯履行通知、协助、保密等义务。",
    )
    span_563_intro = _clause_span(
        article_563,
        "paragraph-1-intro",
        "有下列情形之一的，当事人可以解除合同：",
    )
    span_563_item_4 = _clause_span(
        article_563,
        "paragraph-1-item-4",
        "（四）当事人一方迟延履行债务或者有其他违约行为致使不能实现合同目的；",
    )
    span_563_item_3 = _clause_span(
        article_563,
        "paragraph-1-item-3",
        "（三）当事人一方迟延履行主要债务，经催告后在合理期限内仍未履行；",
    )
    span_565_procedure = _clause_span(
        article_565,
        "procedure",
        article_565.text,
    )
    span_566_unperformed = _clause_span(
        article_566,
        "paragraph-1-unperformed",
        "合同解除后，尚未履行的，终止履行；",
    )
    span_566_restitution = _clause_span(
        article_566,
        "paragraph-1-remedies",
        "已经履行的，根据履行情况和合同性质，当事人可以请求恢复原状或者采取其他补救措施，并有权请求赔偿损失。",
    )

    # Reused IDs are graph-level concepts, so their complete definitions must be identical
    # everywhere they occur. P3/P4 can therefore safely index conditions by condition_id.
    condition_contract_exists = RuleCondition(
        condition_id=COND_CONTRACT_EXISTS,
        label="合同关系依法成立并生效",
        predicate="待履行或解除前是否存在依法成立并生效的服务合同关系",
        evidence_types=["书面合同", "会员协议", "付款凭证", "聊天记录"],
        source_span_ids=[span_509_performance.span_id],
    )
    condition_performance_impossible = RuleCondition(
        condition_id=COND_PERFORMANCE_IMPOSSIBLE,
        label="违约行为致使服务合同目的不能实现",
        predicate="迟延履行或其他违约行为是否已使约定的主要服务目的不能实现",
        evidence_types=["永久停业通知", "主体注销信息", "拒绝履行记录", "履行能力证明"],
        source_span_ids=[span_563_item_4.span_id],
    )
    condition_alternative_performance = RuleCondition(
        condition_id=COND_ALTERNATIVE_PERFORMANCE,
        label="存在足以实现合同目的的替代履行",
        predicate="替代服务是否符合约定并仍足以实现原合同目的",
        operator=ConditionOperator.UNLESS,
        required=False,
        evidence_types=["转店方案", "替代门店信息", "补充协议", "服务能力证明"],
        source_span_ids=[span_563_item_4.span_id],
    )
    condition_contract_terminated = RuleCondition(
        condition_id=COND_CONTRACT_TERMINATED,
        label="合同已经依法解除",
        predicate="合同是否已经发生解除效力",
        evidence_types=["解除通知", "送达凭证", "解除协议", "裁判文书"],
        source_span_ids=[span_565_procedure.span_id, span_566_unperformed.span_id],
    )

    rule_good_faith = RuleRecord(
        rule_id=RULE_GOOD_FAITH,
        title="合同全面履行与诚信履行规则",
        claim_types=["服务合同履行"],
        provisions=[_provision_ref(article_509)],
        conditions=[condition_contract_exists],
        consequences=[
            LegalConsequence(
                consequence_id="consequence.perform_as_agreed",
                consequence_type="全面履行与附随义务",
                description="当事人应按约全面履行，并依诚信原则履行相应附随义务。",
                source_span_ids=[
                    span_509_performance.span_id,
                    span_509_good_faith.span_id,
                ],
            )
        ],
        maturity=MaturityLevel.L3,
        source_spans=[span_509_performance, span_509_good_faith],
    )

    rule_nonperformance = RuleRecord(
        rule_id=RULE_NONPERFORMANCE_TERMINATION,
        title="违约致使合同目的不能实现时的法定解除规则",
        claim_types=["服务合同解除与返还"],
        provisions=[
            _provision_ref(article_509),
            _provision_ref(article_563),
            _provision_ref(article_565),
        ],
        conditions=[
            condition_contract_exists,
            condition_performance_impossible,
            condition_alternative_performance,
        ],
        consequences=[
            LegalConsequence(
                consequence_id="consequence.statutory_termination",
                consequence_type="法定解除权",
                description=(
                    "相对方违约致使合同目的不能实现且不存在足以实现该目的的替代履行时，"
                    "解除权人取得解除权；替代履行状态未知时不得作确定判断，本规则也不表示"
                    "合同自动解除。未先通知而直接起诉或申请仲裁的，须经法院或仲裁机构确认"
                    "解除主张，合同才按第565条规定时点发生效力。本规则不判断第564条所涉"
                    "解除权行使期限。"
                ),
                source_span_ids=[
                    span_563_intro.span_id,
                    span_563_item_4.span_id,
                    span_565_procedure.span_id,
                ],
            )
        ],
        maturity=MaturityLevel.L3,
        source_spans=[
            span_509_performance,
            span_563_intro,
            span_563_item_4,
            span_565_procedure,
        ],
    )

    rule_delay_after_demand = RuleRecord(
        rule_id=RULE_DELAY_AFTER_DEMAND,
        title="迟延履行主要债务且经催告后仍未履行的法定解除规则",
        claim_types=["服务合同解除与返还"],
        provisions=[
            _provision_ref(article_509),
            _provision_ref(article_563),
            _provision_ref(article_565),
        ],
        conditions=[
            condition_contract_exists,
            RuleCondition(
                condition_id=COND_MAIN_OBLIGATION_DELAYED,
                label="主要债务迟延履行",
                predicate="经营者是否已经迟延履行合同的主要服务义务",
                evidence_types=["合同履行期限", "预约记录", "服务记录", "停业通知"],
                source_span_ids=[span_563_item_3.span_id],
            ),
            RuleCondition(
                condition_id=COND_DEMAND_DELIVERED,
                label="已经催告履行",
                predicate="用户是否已向经营者发出履行催告并能证明送达",
                evidence_types=["催告函", "快递回执", "短信", "聊天记录"],
                source_span_ids=[span_563_item_3.span_id],
            ),
            RuleCondition(
                condition_id=COND_REASONABLE_PERIOD_EXPIRED,
                label="合理期限届满后仍未履行",
                predicate="催告给予的合理期限是否已届满且经营者仍未履行",
                evidence_types=["催告内容", "送达时间", "届满后的服务记录"],
                source_span_ids=[span_563_item_3.span_id],
            ),
        ],
        consequences=[
            LegalConsequence(
                consequence_id="consequence.right_to_terminate_after_demand",
                consequence_type="法定解除权",
                description=(
                    "主要债务迟延且经催告后在合理期限内仍未履行时，解除权人取得解除权；"
                    "未先通知而直接起诉或申请仲裁的，须经法院或仲裁机构确认解除主张，合同"
                    "才按第565条规定时点发生效力。本规则不判断第564条所涉解除权行使期限。"
                ),
                source_span_ids=[
                    span_563_intro.span_id,
                    span_563_item_3.span_id,
                    span_565_procedure.span_id,
                ],
            )
        ],
        maturity=MaturityLevel.L3,
        source_spans=[
            span_509_performance,
            span_563_intro,
            span_563_item_3,
            span_565_procedure,
        ],
    )

    rule_restitution = RuleRecord(
        rule_id=RULE_TERMINATION_RESTITUTION,
        title="合同解除后的终止履行、恢复原状与补救规则",
        claim_types=["服务合同解除与返还"],
        provisions=[_provision_ref(article_565), _provision_ref(article_566)],
        conditions=[condition_contract_terminated],
        consequences=[
            LegalConsequence(
                consequence_id="consequence.stop_unperformed_obligations",
                consequence_type="终止履行",
                description="合同解除后，尚未履行的部分终止履行。",
                source_span_ids=[span_566_unperformed.span_id],
            ),
            LegalConsequence(
                consequence_id="consequence.restitution_or_remedy",
                consequence_type="恢复原状或其他补救",
                description=(
                    "已经履行的部分可根据履行情况和合同性质请求恢复原状或采取其他"
                    "补救措施，并有权请求赔偿损失；该规则不当然等于全额退款。"
                ),
                source_span_ids=[span_566_restitution.span_id],
            ),
        ],
        maturity=MaturityLevel.L3,
        source_spans=[span_565_procedure, span_566_unperformed, span_566_restitution],
    )

    aggregate_spans = [
        span_509_performance,
        span_563_intro,
        span_563_item_4,
        span_565_procedure,
        span_566_unperformed,
        span_566_restitution,
    ]
    rule_service_refund = RuleRecord(
        rule_id=RULE_SERVICE_TERMINATION_REFUND,
        title="民法典下一般服务合同解除与费用补救框架",
        claim_types=["服务合同解除与返还"],
        provisions=[
            _provision_ref(article_509),
            _provision_ref(article_563),
            _provision_ref(article_565),
            _provision_ref(article_566),
        ],
        conditions=[
            condition_contract_exists,
            RuleCondition(
                condition_id=COND_PAYMENT_MADE,
                label="用户已经支付服务费用",
                predicate="用户是否已为约定服务付款",
                evidence_types=["付款凭证", "发票", "会员账户记录"],
                source_span_ids=[span_566_restitution.span_id],
            ),
            RuleCondition(
                condition_id=COND_UNPERFORMED_BALANCE,
                label="存在未履行服务或未消费余额",
                predicate="是否仍有尚未提供的服务或未消费费用",
                evidence_types=["会员余额", "消费记录", "课时记录"],
                source_span_ids=[span_566_unperformed.span_id, span_566_restitution.span_id],
            ),
            condition_performance_impossible,
            condition_contract_terminated,
            condition_alternative_performance,
        ],
        consequences=[
            LegalConsequence(
                consequence_id="consequence.service_termination_and_balance_remedy",
                consequence_type="解除权与解除后的费用补救",
                description=(
                    "本规则仅覆盖第563条的一般解除路径：解除权成立不表示合同自动解除，应依"
                    "第565条主张并确定生效时点；未先通知而直接起诉或申请仲裁的，须经法院或"
                    "仲裁机构确认解除主张。存在足以实现合同目的的替代履行时本路径受阻，该"
                    "事实未知时不得输出确定解除结论。本规则不判断第564条所涉行使期限；有效"
                    "解除后，可依第566条按履行情况和合同性质处理费用。本规则未纳入预付式"
                    "消费专项司法解释，不能单独判断迁店等专项解除事由或计算退款金额。"
                ),
                source_span_ids=[
                    span_563_intro.span_id,
                    span_563_item_4.span_id,
                    span_565_procedure.span_id,
                    span_566_unperformed.span_id,
                    span_566_restitution.span_id,
                ],
            )
        ],
        maturity=MaturityLevel.L2,
        source_spans=aggregate_spans,
    )

    unique_spans = {
        span.span_id: span
        for span in (
            span_509_performance,
            span_509_good_faith,
            span_563_intro,
            span_563_item_3,
            span_563_item_4,
            span_565_procedure,
            span_566_unperformed,
            span_566_restitution,
        )
    }
    return RuleBuildResult(
        rules=[
            rule_good_faith,
            rule_delay_after_demand,
            rule_nonperformance,
            rule_restitution,
            rule_service_refund,
        ],
        source_spans=list(unique_spans.values()),
    )
