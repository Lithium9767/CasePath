"""P4 实现区：有引用的条件化解释。未核验原文不得标成 verified。

主解释只写本案已检索到的规则、已知条件和案例证据。算法未完成项留在
comparison_bundle.degradation_reason，不拼进用户可见正文。
"""

from __future__ import annotations

from casepath.adapters.retrieval.retrieval_catalog import RetrievalCatalog
from casepath.contracts import (
    CitationRecord,
    ComparisonBundle,
    ConditionStatus,
    EvidenceAction,
    ExplanationBranch,
    ExplanationPlan,
    QueryState,
    RetrievalBundle,
)

_KNOWN = {
    ConditionStatus.SATISFIED,
    ConditionStatus.NOT_SATISFIED,
    ConditionStatus.CONFLICTING,
}


def _label(catalog: RetrievalCatalog, condition_id: str) -> str:
    item = catalog.conditions.get(condition_id)
    return item.label if item is not None else condition_id


def _known_facts(state: QueryState, catalog: RetrievalCatalog) -> list[str]:
    lines: list[str] = []
    for item in state.condition_states:
        if item.status not in _KNOWN:
            continue
        name = _label(catalog, item.condition_id)
        if item.status is ConditionStatus.SATISFIED:
            lines.append(f"「{name}」已有用户原话支持")
        elif item.status is ConditionStatus.NOT_SATISFIED:
            lines.append(f"「{name}」按现有原话更接近不满足")
        else:
            lines.append(f"「{name}」存在互相冲突的原话")
        if len(lines) >= 5:
            break
    return lines


def _rule_titles(bundle: RetrievalBundle, catalog: RetrievalCatalog) -> list[str]:
    titles: list[str] = []
    for ref in bundle.rule_refs:
        rule = catalog.rules.get(ref.object_id)
        if rule is None:
            continue
        titles.append(rule.title)
        if len(titles) >= 3:
            break
    return titles


def _case_pool_summary(bundle: RetrievalBundle) -> dict[str, object]:
    from casepath.adapters.retrieval.retrieval_catalog import current_round
    from casepath.application.runtime_meta import current_runtime_meta

    round_evidence = current_round()
    role_n = (
        len(bundle.support_case_refs)
        + len(bundle.limiting_case_refs)
        + len(bundle.boundary_case_refs)
    )
    undetermined_n = bundle.undetermined_case_count
    if undetermined_n == 0 and round_evidence is not None:
        undetermined_n = len(round_evidence.undetermined_case_ids)
    fused_n = bundle.fused_case_count
    if fused_n == 0 and round_evidence is not None:
        fused_n = len(round_evidence.fused_case_ids)
    reasons = list(bundle.undetermined_reason_summary)
    if not reasons and round_evidence is not None:
        reasons = sorted(set(round_evidence.undetermined_reasons.values()))
    meta = current_runtime_meta()
    stop = meta.last_stop_reason if meta is not None else None
    return {
        "fused_case_count": fused_n,
        "undetermined_case_count": undetermined_n,
        "role_assigned_case_count": role_n,
        "undetermined_reason_summary": reasons,
        "question_stop_reason": stop,
    }


def _main_explanation(
    state: QueryState,
    bundle: RetrievalBundle,
    catalog: RetrievalCatalog,
) -> str:
    claims = "、".join(item.claim_type for item in state.candidate_claims) or "民事咨询"
    parts = [f"根据你描述的情况，当前按「{claims}」检索相关规则。"]
    known = _known_facts(state, catalog)
    if known:
        parts.append("已经落到规则条件上的事实包括：" + "；".join(known) + "。")
    else:
        parts.append("目前还没有足够可定位的原话，把关键条件标成满足或不满足。")
    titles = _rule_titles(bundle, catalog)
    if titles:
        parts.append("正在对照的规则包括：" + "；".join(titles) + "。")
    elif bundle.rule_refs:
        parts.append("已召回候选规则，但本地目录尚未解析到标题。")
    else:
        parts.append("这一轮没有稳定召回可用规则，不能把检索空结果写成没有法律依据。")
    summary = _case_pool_summary(bundle)
    case_n = int(summary["role_assigned_case_count"])
    undetermined_n = int(summary["undetermined_case_count"])
    if case_n:
        parts.append(
            f"检索到{case_n}件可对照案例，分别用于支持、限制或划清边界，不是对本案的预判。"
        )
    elif undetermined_n:
        parts.append(
            f"检索到{undetermined_n}件相关候选案例，但条件证据尚不足以确定支持/限制角色；"
            "这不等于没有案例。"
        )
    else:
        parts.append(
            "现有规则依据仍可讨论，但可比对的案例证据不足；缺少案例不等于没有相关法律依据。"
        )
    stop = summary["question_stop_reason"]
    if stop == "USER_UNABLE_TO_ANSWER":
        parts.append("部分关键事实你暂时无法补充，解释将保留相应未知条件。")
    elif stop == "USER_DECLINED":
        parts.append("你已表示暂不回答部分问题，相关条件将保持未知。")
    elif stop == "ALL_ALREADY_ANSWERED":
        parts.append("当前没有新的关键事实需要追问；这不等于证据已经充分。")
    elif stop == "NO_KEY_MISSING_FACT":
        parts.append("当前没有识别到需要追问的关键缺失事实。")
    elif stop == "QUESTION_BUDGET_REACHED":
        parts.append("本轮追问预算已用尽，解释基于当前已收集事实。")
    elif stop == "GENERATION_FAILED":
        parts.append("追问生成未能完成，不能据此认为信息已经充分。")
    unresolved = [
        _label(catalog, item.condition_id)
        for item in state.condition_states
        if item.status in {ConditionStatus.UNKNOWN, ConditionStatus.CONFLICTING}
    ]
    if unresolved:
        parts.append("要收窄解释，还需要确认：" + "、".join(unresolved[:3]) + "。")
    return "".join(parts)


class EvidenceExplanationPlanner:
    def __init__(self, catalog: RetrievalCatalog, language_model=None) -> None:
        self.catalog = catalog
        self.language_model = language_model

    def build(
        self,
        state: QueryState,
        bundle: RetrievalBundle,
        comparison: ComparisonBundle,
    ) -> ExplanationPlan:
        if state.status.value == "NEEDS_CLARIFICATION":
            # 【Live 正常分支】短说明不是模型失效；有待回答问题时有意跳过完整解释生成。
            # 澄清轮只给事实回顾，不等待长解释，也不伪装为生成失败后的成功降级。
            # 问题由 workflow.next_question 返回；完整法律解释留到澄清结束。
            fact = state.user_facts[-1].text if state.user_facts else state.initial_query
            summary = _case_pool_summary(bundle)
            return ExplanationPlan(
                session_id=state.session_id,
                main_explanation="我看到你说：“"
                + fact[:180]
                + ("……”" if len(fact) > 180 else "”。")
                + "我先确认下面这个细节，再结合你的回答说明。现在还没有形成完整解释。",
                candidate_claims=[claim.claim_type for claim in state.candidate_claims],
                applicable_rule_ids=[ref.object_id for ref in bundle.rule_refs],
                unresolved_condition_ids=[
                    item.condition_id
                    for item in state.condition_states
                    if item.status.value in {"UNKNOWN", "CONFLICTING"}
                ],
                fused_case_count=int(summary["fused_case_count"]),
                undetermined_case_count=int(summary["undetermined_case_count"]),
                role_assigned_case_count=int(summary["role_assigned_case_count"]),
                undetermined_reason_summary=list(summary["undetermined_reason_summary"]),
                question_stop_reason=summary["question_stop_reason"],
            )
        if self.language_model is not None:
            from casepath.adapters.explanation.grounded_explanation import generate_explanation

            generated = generate_explanation(
                self.language_model, self.catalog, state, bundle, comparison
            )
            if generated is not None:
                return generated
        statuses = {item.condition_id: item.status for item in state.condition_states}
        unresolved = [
            key
            for key, value in statuses.items()
            if value in {ConditionStatus.UNKNOWN, ConditionStatus.CONFLICTING}
        ]
        citations = []
        branches = []
        for ref in bundle.rule_refs:
            rule = self.catalog.rules.get(ref.object_id)
            if rule is None or not rule.span_ids:
                continue
            citation_id = f"citation.{ref.object_id}"
            citations.append(
                CitationRecord(
                    citation_id=citation_id,
                    source_span_ids=list(rule.span_ids[:2]),
                    supports=rule.title,
                    verified=False,
                )
            )
            branches.append(
                ExplanationBranch(
                    branch_id=f"branch.{ref.object_id}",
                    condition=f"若规则 {rule.title} 的核心条件成立",
                    explanation=rule.title + " 提供一条候选解释路径，不是裁判结论。",
                    citation_ids=[citation_id],
                )
            )
        for ref in (
            *bundle.support_case_refs,
            *bundle.limiting_case_refs,
            *bundle.boundary_case_refs,
        ):
            if not ref.source_span_ids:
                continue
            citation_id = f"citation.{ref.object_id}"
            citations.append(
                CitationRecord(
                    citation_id=citation_id,
                    source_span_ids=list(ref.source_span_ids[:2]),
                    supports="案例条件认定与原文",
                    verified=False,
                )
            )
        seen = set()
        unique_citations = []
        for item in citations:
            if item.citation_id in seen:
                continue
            seen.add(item.citation_id)
            unique_citations.append(item)
        actions = [
            EvidenceAction(
                action_id=f"evidence.{condition_id}",
                description=self.catalog.conditions[condition_id].predicate
                if condition_id in self.catalog.conditions
                else "补充可核验的事实材料。",
                related_condition_ids=[condition_id],
            )
            for condition_id in unresolved[:4]
        ]
        summary = _case_pool_summary(bundle)
        return ExplanationPlan(
            session_id=state.session_id,
            main_explanation=_main_explanation(state, bundle, self.catalog),
            candidate_claims=[claim.claim_type for claim in state.candidate_claims],
            applicable_rule_ids=[item.object_id for item in bundle.rule_refs],
            support_case_ids=[item.object_id for item in bundle.support_case_refs],
            limiting_case_ids=[item.object_id for item in bundle.limiting_case_refs],
            boundary_case_ids=[item.object_id for item in bundle.boundary_case_refs],
            conditional_branches=branches[:3],
            unresolved_condition_ids=unresolved,
            evidence_actions=actions,
            citations=unique_citations,
            fused_case_count=int(summary["fused_case_count"]),
            undetermined_case_count=int(summary["undetermined_case_count"]),
            role_assigned_case_count=int(summary["role_assigned_case_count"]),
            undetermined_reason_summary=list(summary["undetermined_reason_summary"]),
            question_stop_reason=summary["question_stop_reason"],
        )
