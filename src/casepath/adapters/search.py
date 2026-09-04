from __future__ import annotations

from collections.abc import Iterable

from casepath.algorithms import (
    BM25Index,
    SearchDocument,
    calculate_case_rerank_score,
    normalize_positive_scores,
    tokenize_zh_bigrams,
)
from casepath.contracts import (
    CaseRole,
    CaseRecord,
    ConditionStatus,
    DecisionStatus,
    MaturityLevel,
    QueryState,
    RetrievalBundle,
    RuleRecord,
    ScoreComponent,
    ScoredReference,
)


def _join_search_fields(fields: Iterable[str | None]) -> str:
    """清理并连接检索字段，忽略空值但不改变原有字段顺序。"""

    return " ".join(field.strip() for field in fields if field and field.strip())


def build_rule_search_text(rule: RuleRecord) -> str:
    """按照字段白名单构造规则召回文本。

    当前合同还没有独立的ProvisionRecord正文，因此第一版使用规则标题、
    请求权类型、法条引用信息、条件、例外、法律后果和规则原文跨度。
    P1补充ProvisionRecord后，可以在此白名单中显式加入正文，而不必修改
    BM25算法本身。
    """

    fields: list[str | None] = [rule.title]
    fields.extend(rule.claim_types)

    for provision in rule.provisions:
        fields.extend(
            [
                provision.article_no,
                provision.title,
            ]
        )

    for condition in rule.conditions:
        fields.extend(
            [
                condition.label,
                condition.predicate,
                *condition.evidence_types,
            ]
        )

    for exception in rule.exceptions:
        fields.extend(
            [
                exception.label,
                exception.predicate,
                exception.effect,
            ]
        )

    for consequence in rule.consequences:
        fields.extend(
            [
                consequence.consequence_type,
                consequence.description,
            ]
        )

    # RuleRecord中的SourceSpan只承载规范规则来源，可以安全参与规则召回。
    fields.extend(span.quote for span in rule.source_spans)
    return _join_search_fields(fields)


def build_case_search_text(case: CaseRecord) -> str:
    """按照防泄漏白名单构造案例召回文本。

    召回阶段只能使用案件标题、案由、诉讼请求以及事实认定等“问题侧”字段。
    `DecisionItem.status`、裁判结果描述和金额被有意排除，否则检索模型可能
    在看到答案后再挑选案例，导致离线评测虚高。
    """

    fields: list[str | None] = [case.title, case.cause]

    for claim in case.claims:
        fields.extend(
            [
                claim.claim_type,
                claim.requested_remedy,
                *claim.invoked_rule_ids,
            ]
        )

    for finding in case.findings:
        # CourtFinding.predicate描述法院认定的事实，不包含最终裁判结果。
        fields.append(finding.predicate)

    for condition_finding in case.condition_findings:
        # 只加入条件ID，不加入status，避免把条件判断标签作为召回答案。
        fields.append(condition_finding.condition_id)

    # 不直接加入case.source_spans：其中可能同时混有裁判结果原文。后续如果
    # P3为跨度补充类型，应只显式纳入事实类跨度，不能取消这一白名单约束。
    return _join_search_fields(fields)


def build_query_text(state: QueryState) -> str:
    """合并初始问题和用户后续回答，生成当前轮次的检索文本。

    内部状态枚举、候选规则ID和模型推断不会拼入查询，避免系统自己的判断
    反过来强化召回结果。只有用户实际提供的自然语言进入BM25查询。
    """

    fields: list[str | None] = [state.initial_query]
    fields.extend(
        turn.answer
        for turn in state.dialogue_history
        if turn.answer and turn.answer.strip()
    )
    return _join_search_fields(fields)


class BM25RuleRetriever:
    """使用本地BM25索引召回候选规范规则。"""

    def __init__(
        self,
        rules: list[RuleRecord],
        *,
        top_k: int = 5,
    ) -> None:
        """构造规则索引，并保存生成检索解释所需的词元快照。"""

        if top_k <= 0:
            raise ValueError("规则检索的top_k必须是正整数")

        self._top_k = top_k
        self._rules_by_id = {rule.rule_id: rule for rule in rules}

        # 如果规则ID重复，字典长度会缩短。提前报错比静默覆盖规则更安全。
        if len(self._rules_by_id) != len(rules):
            raise ValueError("规则ID不能重复")

        self._document_tokens: dict[str, tuple[str, ...]] = {}
        documents: list[SearchDocument] = []
        for rule in rules:
            tokens = tuple(tokenize_zh_bigrams(build_rule_search_text(rule)))
            self._document_tokens[rule.rule_id] = tokens
            documents.append(SearchDocument(object_id=rule.rule_id, tokens=tokens))

        self._index = BM25Index(documents)

    def retrieve(self, state: QueryState) -> list[ScoredReference]:
        """返回规则Top-K，并为每个结果提供可检查的中文召回原因。"""

        query_tokens = tokenize_zh_bigrams(build_query_text(state))
        ranked_results = self._index.search(query_tokens, top_k=self._top_k)

        references: list[ScoredReference] = []
        for rule_id, score in ranked_results:
            matched_tokens = self._find_matched_tokens(
                query_tokens=query_tokens,
                document_tokens=self._document_tokens[rule_id],
            )
            match_summary = "、".join(matched_tokens[:8])
            if len(matched_tokens) > 8:
                match_summary += "等"

            references.append(
                ScoredReference(
                    object_id=rule_id,
                    score=score,
                    reasons=[
                        f"BM25规则召回，原始得分为{score:.6f}",
                        f"命中词元：{match_summary}",
                    ],
                )
            )

        return references

    @staticmethod
    def _find_matched_tokens(
        *,
        query_tokens: list[str],
        document_tokens: tuple[str, ...],
    ) -> list[str]:
        """按查询中的首次出现顺序返回去重后的命中词元。"""

        document_token_set = set(document_tokens)
        matched: list[str] = []
        seen: set[str] = set()
        for token in query_tokens:
            if token in document_token_set and token not in seen:
                matched.append(token)
                seen.add(token)
        return matched


class BM25CaseRetriever:
    """执行案例BM25召回、规则条件重排以及正反案例分组。"""

    _MATURITY_SCORES = {
        MaturityLevel.L0: 0.25,
        MaturityLevel.L1: 0.50,
        MaturityLevel.L2: 0.75,
        MaturityLevel.L3: 1.00,
    }

    def __init__(
        self,
        cases: list[CaseRecord],
        *,
        top_k: int = 20,
    ) -> None:
        """构建不含裁判结果字段的案例BM25索引。"""

        if top_k <= 0:
            raise ValueError("案例检索的top_k必须是正整数")

        # placeholder案例只用于接口演示，不能进入正式索引和解释结果。
        formal_cases = [case for case in cases if not self._is_placeholder_case(case)]
        self._cases_by_id = {case.case_id: case for case in formal_cases}
        if len(self._cases_by_id) != len(formal_cases):
            raise ValueError("案例ID不能重复")

        self._top_k = top_k
        documents = [
            SearchDocument(
                object_id=case.case_id,
                tokens=tuple(tokenize_zh_bigrams(build_case_search_text(case))),
            )
            for case in formal_cases
        ]
        self._index = BM25Index(documents)

    def retrieve(
        self,
        state: QueryState,
        rule_refs: list[ScoredReference],
    ) -> RetrievalBundle:
        """召回案例、计算四项重排特征并构建对照集合。"""

        query_tokens = tokenize_zh_bigrams(build_query_text(state))
        bm25_results = self._index.search(query_tokens, top_k=self._top_k)
        normalized_scores = normalize_positive_scores(
            [score for _, score in bm25_results]
        )
        candidate_rule_ids = {reference.object_id for reference in rule_refs}

        ranked_cases: list[tuple[CaseRecord, ScoredReference]] = []
        for (case_id, _), normalized_bm25 in zip(
            bm25_results,
            normalized_scores,
            strict=True,
        ):
            case = self._cases_by_id[case_id]
            condition_overlap = self._calculate_condition_overlap(state, case)
            rule_overlap = self._calculate_rule_overlap(case, candidate_rule_ids)
            source_quality = self._calculate_source_quality(case)
            final_score, components = calculate_case_rerank_score(
                bm25_score=normalized_bm25,
                condition_overlap=condition_overlap,
                rule_overlap=rule_overlap,
                source_quality=source_quality,
            )
            ranked_cases.append(
                (
                    case,
                    ScoredReference(
                        object_id=case.case_id,
                        score=final_score,
                        reasons=self._format_score_reasons(components),
                    ),
                )
            )

        # 最终分数相同时使用case_id稳定排序，确保Top-K和测试结果可复现。
        ranked_cases.sort(key=lambda item: (-item[1].score, item[0].case_id))
        support_refs: list[ScoredReference] = []
        limiting_refs: list[ScoredReference] = []
        boundary_refs: list[ScoredReference] = []
        selected_cases: list[CaseRecord] = []

        for case, reference in ranked_cases:
            role = self._classify_case(state, case)
            if role == CaseRole.SUPPORT:
                support_refs.append(reference)
            elif role == CaseRole.LIMITING:
                limiting_refs.append(reference)
            elif role == CaseRole.BOUNDARY:
                boundary_refs.append(reference)
            else:
                # 角色不明确的案例不能作为正式正反证据，但仍完成了内部候选评估。
                continue
            selected_cases.append(case)

        degraded, degradation_reason = self._build_degradation_status(
            support_refs=support_refs,
            limiting_refs=limiting_refs,
            boundary_refs=boundary_refs,
        )
        return RetrievalBundle(
            rule_refs=rule_refs,
            support_case_refs=support_refs,
            limiting_case_refs=limiting_refs,
            boundary_case_refs=boundary_refs,
            cited_span_ids=self._collect_cited_span_ids(selected_cases),
            degraded=degraded,
            degradation_reason=degradation_reason,
        )

    @staticmethod
    def _is_placeholder_case(case: CaseRecord) -> bool:
        """识别明确标记为占位数据的案例。"""

        return "placeholder" in case.case_id.lower()

    @staticmethod
    def _known_condition_statuses(state: QueryState) -> dict[str, ConditionStatus]:
        """提取用户已经明确、可以参与案例比较的条件状态。"""

        excluded = {ConditionStatus.UNKNOWN, ConditionStatus.CONFLICTING}
        return {
            item.condition_id: item.status
            for item in state.condition_states
            if item.status not in excluded
        }

    @classmethod
    def _calculate_condition_overlap(
        cls,
        state: QueryState,
        case: CaseRecord,
    ) -> float:
        """计算用户已知条件与案例条件在共同条件上的一致比例。"""

        user_statuses = cls._known_condition_statuses(state)
        excluded = {ConditionStatus.UNKNOWN, ConditionStatus.CONFLICTING}
        case_statuses = {
            finding.condition_id: finding.status
            for finding in case.condition_findings
            if finding.status not in excluded
        }
        shared_ids = set(user_statuses) & set(case_statuses)
        if not shared_ids:
            return 0.0

        matching_count = sum(
            user_statuses[condition_id] == case_statuses[condition_id]
            for condition_id in shared_ids
        )
        return matching_count / len(shared_ids)

    @staticmethod
    def _calculate_rule_overlap(
        case: CaseRecord,
        candidate_rule_ids: set[str],
    ) -> float:
        """计算案例实际引用规则覆盖当前候选规则的比例。"""

        if not candidate_rule_ids:
            return 0.0

        applied_rule_ids: set[str] = set()
        for claim in case.claims:
            applied_rule_ids.update(claim.invoked_rule_ids)
        for step in case.reasoning_steps:
            applied_rule_ids.update(step.applied_rule_ids)

        return len(applied_rule_ids & candidate_rule_ids) / len(candidate_rule_ids)

    @classmethod
    def _calculate_source_quality(cls, case: CaseRecord) -> float:
        """综合案例成熟度和原文可追溯性，得到来源质量分数。"""

        maturity_score = cls._MATURITY_SCORES[case.maturity]
        traceability_score = 1.0 if case.source_spans else 0.0

        # 成熟度反映结构化深度，原文跨度反映展示时能否核验，两者分别占80%和20%。
        return 0.80 * maturity_score + 0.20 * traceability_score

    @staticmethod
    def _format_score_reasons(components: list[ScoreComponent]) -> list[str]:
        """把结构化评分组成转换为ScoredReference支持的中文原因列表。"""

        labels = {
            "bm25": "BM25归一化分数",
            "condition_overlap": "条件重合分数",
            "rule_overlap": "规则重合分数",
            "source_quality": "来源质量分数",
        }
        return [
            f"{labels[component.name]}：{component.value:.4f}；{component.explanation}"
            for component in components
        ]

    @classmethod
    def _classify_case(cls, state: QueryState, case: CaseRecord) -> CaseRole | None:
        """在BM25召回完成后，依据相关请求的裁判结果确定案例角色。"""

        candidate_claim_types = {claim.claim_type for claim in state.candidate_claims}
        relevant_claim_ids = {
            claim.claim_id
            for claim in case.claims
            if not candidate_claim_types or claim.claim_type in candidate_claim_types
        }
        decision_statuses = {
            decision.status
            for decision in case.decisions
            if decision.claim_id in relevant_claim_ids
        }
        if decision_statuses & {
            DecisionStatus.GRANTED,
            DecisionStatus.PARTIALLY_GRANTED,
        }:
            return CaseRole.SUPPORT
        if DecisionStatus.REJECTED in decision_statuses:
            return CaseRole.LIMITING
        if cls._is_boundary_case(state, case):
            return CaseRole.BOUNDARY
        return None

    @classmethod
    def _is_boundary_case(cls, state: QueryState, case: CaseRecord) -> bool:
        """判断案例是否仅在一到两个可比较条件上形成边界差异。"""

        user_statuses = cls._known_condition_statuses(state)
        case_statuses = {
            finding.condition_id: finding.status for finding in case.condition_findings
        }
        shared_ids = set(user_statuses) & set(case_statuses)
        if not shared_ids:
            return False

        different_count = sum(
            user_statuses[condition_id] != case_statuses[condition_id]
            for condition_id in shared_ids
        )
        matching_count = len(shared_ids) - different_count

        # 边界案例既要存在少量差异，也要保证至少一半的可比较条件保持一致。
        return 1 <= different_count <= 2 and matching_count / len(shared_ids) >= 0.5

    @staticmethod
    def _collect_cited_span_ids(cases: list[CaseRecord]) -> list[str]:
        """收集非裁判结果字段引用的原文跨度ID，并保持稳定去重顺序。"""

        ordered_ids: list[str] = []
        seen_ids: set[str] = set()
        for case in cases:
            candidate_ids: list[str] = []
            for finding in case.findings:
                candidate_ids.extend(finding.source_span_ids)
            for finding in case.condition_findings:
                candidate_ids.extend(finding.source_span_ids)
            for step in case.reasoning_steps:
                candidate_ids.extend(step.source_span_ids)

            # DecisionItem引用的跨度不在此处加入，避免检索包携带答案侧引用。
            for span_id in candidate_ids:
                if span_id not in seen_ids:
                    ordered_ids.append(span_id)
                    seen_ids.add(span_id)
        return ordered_ids

    @staticmethod
    def _build_degradation_status(
        *,
        support_refs: list[ScoredReference],
        limiting_refs: list[ScoredReference],
        boundary_refs: list[ScoredReference],
    ) -> tuple[bool, str | None]:
        """检查是否同时具备真实支持案例和真实限制或边界案例。"""

        if not support_refs:
            return True, "当前没有召回真实支持案例，结果仅供降级分析。"
        if not limiting_refs and not boundary_refs:
            return True, "当前没有召回真实限制或边界案例，无法形成稳定对照。"
        return False, None
