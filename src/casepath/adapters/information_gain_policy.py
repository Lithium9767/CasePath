"""P4 实现区：以规则候选缩减为主的追问策略。

模拟回答只作用于状态副本。不知道是合法选项，不等于条件不满足。
CORE 条件组按 ALL/ANY 求相容性；当前指标是剩余规则数量缩减，不是解释分支缩减。
"""

from __future__ import annotations

from casepath.adapters.condition_logic import group_truth
from casepath.adapters.question_cards import QUESTION_CARDS
from casepath.adapters.retrieval_catalog import RetrievalCatalog
from casepath.contracts import (
    ComparisonBundle,
    ConditionStatus,
    QueryConditionState,
    QueryState,
    QuestionCandidate,
    RetrievalBundle,
    ScoreComponent,
)

_PRIORS = {
    ConditionStatus.SATISFIED: 0.35,
    ConditionStatus.NOT_SATISFIED: 0.35,
    ConditionStatus.UNKNOWN: 0.30,
}
_STOP = 0.04


def _card(catalog: RetrievalCatalog, condition_id: str) -> tuple[str, list[str], str]:
    if condition_id in QUESTION_CARDS:
        return QUESTION_CARDS[condition_id]
    item = catalog.conditions.get(condition_id)
    question = item.predicate if item is not None else f"{condition_id} 是否成立？"
    return question, ["是", "否", "不清楚"], "该条件会改变当前候选规则是否仍相容。"


def _group_compatible(operator: str, statuses: list[ConditionStatus]) -> bool:
    return group_truth(operator, statuses) is not False


def _compatible_rules(
    catalog: RetrievalCatalog, states: list[QueryConditionState], rule_ids: list[str]
) -> set[str]:
    by_id = {item.condition_id: item.status for item in states}
    kept: set[str] = set()
    for rule_id in rule_ids:
        rule = catalog.rules.get(rule_id)
        if rule is None:
            continue
        compatible = True
        for group in rule.groups:
            if group.role != "CORE":
                continue
            member_statuses = [
                by_id.get(condition_id, ConditionStatus.UNKNOWN)
                for condition_id in group.member_ids
            ]
            if not _group_compatible(group.operator, member_statuses):
                compatible = False
                break
        if compatible:
            kept.add(rule_id)
    return kept


def _is_core(catalog: RetrievalCatalog, condition_id: str, rule_ids: list[str]) -> bool:
    for rule_id in rule_ids:
        rule = catalog.rules.get(rule_id)
        if rule is None:
            continue
        for group in rule.groups:
            if group.role == "CORE" and condition_id in group.member_ids:
                return True
    return False


class InformationGainPolicy:
    def __init__(self, catalog: RetrievalCatalog, *, stop_threshold: float = _STOP) -> None:
        self.catalog = catalog
        self.stop_threshold = stop_threshold

    def select(
        self,
        state: QueryState,
        bundle: RetrievalBundle,
        comparison: ComparisonBundle,
    ) -> QuestionCandidate | None:
        asked_questions = {turn.question_id for turn in state.dialogue_history}
        asked_conditions = {turn.condition_id for turn in state.dialogue_history}
        rule_ids = [item.object_id for item in bundle.rule_refs]
        current = _compatible_rules(self.catalog, state.condition_states, rule_ids)
        if not current:
            return None
        by_comparison = {item.condition_id: item for item in comparison.condition_comparisons}
        ranked = comparison.ranked_condition_ids or [
            item.condition_id for item in state.condition_states
        ]
        best: QuestionCandidate | None = None
        for condition_id in ranked:
            condition = next(
                (item for item in state.condition_states if item.condition_id == condition_id),
                None,
            )
            if condition is None or condition.status is not ConditionStatus.UNKNOWN:
                continue
            question_id = f"question.{condition_id}.1"
            if question_id in asked_questions or condition_id in asked_conditions:
                continue
            card = self.catalog.conditions.get(condition_id)
            if card is not None and not card.user_answerable:
                continue
            ebr = self._rule_candidate_reduction(state, rule_ids, current, condition_id)
            record = by_comparison.get(condition_id)
            cost = 0.06 if not _is_core(self.catalog, condition_id, rule_ids) else 0.04
            entropy = record.condition_entropy if record else 0.0
            utility = max(
                0.0,
                min(
                    1.0,
                    0.65 * ebr
                    + 0.20 * min(1.0, entropy / 1.5)
                    + 0.15 * (record.case_disagreement_score if record else 0.0)
                    - cost,
                ),
            )
            if utility < self.stop_threshold:
                continue
            question, options, why = _card(self.catalog, condition_id)
            candidate = QuestionCandidate(
                question_id=question_id,
                condition_id=condition_id,
                question=question,
                why_asked=why,
                options=options,
                utility=utility,
                score_components=[
                    ScoreComponent(
                        name="expected_rule_candidate_reduction",
                        value=ebr,
                        explanation="模拟回答后的剩余相容规则数量缩减，不是解释分支缩减",
                    ),
                    ScoreComponent(
                        name="answer_cost", value=cost, explanation="回答成本；未叠加ERC"
                    ),
                    ScoreComponent(
                        name="case_disagreement",
                        value=record.case_disagreement_score if record else 0.0,
                        explanation="复用D的分化指标",
                    ),
                ],
                supporting_case_ids=list(record.support_case_ids) if record else [],
                limiting_case_ids=list(record.limiting_case_ids) if record else [],
                boundary_case_ids=list(record.boundary_case_ids) if record else [],
            )
            if best is None or candidate.utility > best.utility:
                best = candidate
        return best

    def _rule_candidate_reduction(
        self,
        state: QueryState,
        rule_ids: list[str],
        current: set[str],
        condition_id: str,
    ) -> float:
        expected = 0.0
        for status, prior in _PRIORS.items():
            clone = []
            for item in state.condition_states:
                if item.condition_id == condition_id:
                    clone.append(item.model_copy(update={"status": status}))
                else:
                    clone.append(item.model_copy(deep=True))
            remaining = _compatible_rules(self.catalog, clone, rule_ids)
            expected += prior * (len(remaining) / len(current))
        return 1.0 - expected
