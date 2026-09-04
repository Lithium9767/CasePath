from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Mapping

from casepath.contracts import (
    CaseRecord,
    ConditionStatus,
    QueryState,
    QuestionCandidate,
    RetrievalBundle,
    RuleCondition,
    RuleRecord,
    ScoreComponent,
    ScoredReference,
)


@dataclass(frozen=True)
class QuestionTemplate:
    """经过业务审核的条件追问文本与用户可选答案。"""

    question: str
    options: tuple[str, ...]

    def __post_init__(self) -> None:
        """保证模板可以满足QuestionCandidate的数据合同。"""

        if not self.question.strip():
            raise ValueError("追问文本不能为空")
        if len(self.options) < 2 or any(not option.strip() for option in self.options):
            raise ValueError("追问模板必须提供至少两个非空选项")


@dataclass(frozen=True)
class _ScoredQuestion:
    """追问策略内部使用的完整评分结果。"""

    condition: RuleCondition
    utility: float
    rule_centrality: float
    score_components: tuple[ScoreComponent, ...]
    supporting_case_ids: tuple[str, ...]
    limiting_case_ids: tuple[str, ...]


DEFAULT_QUESTION_TEMPLATES: dict[str, QuestionTemplate] = {
    "cond.contract_exists": QuestionTemplate(
        question="你是否已经与经营者形成服务合同，例如办卡、充值或签署协议？",
        options=("已经形成合同", "尚未形成合同", "不清楚"),
    ),
    "cond.payment_made": QuestionTemplate(
        question="你是否已经向经营者支付相关费用？",
        options=("已经支付", "尚未支付", "不清楚"),
    ),
    "cond.unperformed_balance": QuestionTemplate(
        question="当前是否还有尚未消费或使用的余额？",
        options=("还有余额", "已经全部消费", "不清楚"),
    ),
    "cond.performance_impossible": QuestionTemplate(
        question="健身房是永久停止经营，还是暂时关闭？",
        options=("永久停止经营", "暂时关闭", "仍可在其他门店使用", "不清楚"),
    ),
    "cond.alternative_performance": QuestionTemplate(
        question="经营者是否安排了其他门店或其他方式继续提供服务？",
        options=("可以继续履行", "没有替代安排", "不清楚"),
    ),
}


class WeightedQuestionPolicy:
    """使用可解释启发式公式选择下一条高价值追问。"""

    _COMMON_EVIDENCE_TYPES = {
        "合同",
        "会员卡",
        "付款记录",
        "转账记录",
        "票据",
        "停业通知",
        "企业登记状态",
        "聊天记录",
    }

    def __init__(
        self,
        *,
        rules_by_id: Mapping[str, RuleRecord],
        cases_by_id: Mapping[str, CaseRecord],
        templates: Mapping[str, QuestionTemplate] | None = None,
        minimum_utility: float = 0.25,
        max_questions: int = 5,
    ) -> None:
        """保存规则、案例、问题模板以及停止策略配置。"""

        if not math.isfinite(minimum_utility):
            raise ValueError("最低追问效用必须是有限数")
        if max_questions < 0:
            raise ValueError("最大追问轮数不能小于0")

        self._rules_by_id = dict(rules_by_id)
        self._cases_by_id = dict(cases_by_id)
        self._templates = dict(DEFAULT_QUESTION_TEMPLATES)
        if templates is not None:
            self._templates.update(templates)
        self._minimum_utility = minimum_utility
        self._max_questions = max_questions

    def select(
        self,
        state: QueryState,
        bundle: RetrievalBundle,
    ) -> QuestionCandidate | None:
        """选择效用最高的问题；达到停止条件时返回None。"""

        answered_turns = [turn for turn in state.dialogue_history if turn.answer]
        if len(answered_turns) >= self._max_questions:
            return None

        answered_condition_ids = {turn.condition_id for turn in answered_turns}
        condition_states = {
            item.condition_id: item.status for item in state.condition_states
        }
        scored_questions: list[_ScoredQuestion] = []

        for condition in self._active_conditions(bundle):
            status = condition_states.get(condition.condition_id, ConditionStatus.UNKNOWN)
            if status not in {ConditionStatus.UNKNOWN, ConditionStatus.CONFLICTING}:
                continue
            if not condition.user_answerable:
                continue
            if condition.condition_id in answered_condition_ids:
                # 即使回答为“不清楚”，也不重复询问同一个条件，避免交互循环。
                continue

            scored_questions.append(
                self._score_condition(
                    state=state,
                    bundle=bundle,
                    condition=condition,
                    interaction_count=len(answered_turns),
                )
            )

        if not scored_questions:
            return None

        # 效用和规则中心性都相同时按condition_id排序，确保结果完全可复现。
        scored_questions.sort(
            key=lambda item: (
                -item.utility,
                -item.rule_centrality,
                item.condition.condition_id,
            )
        )
        selected = scored_questions[0]
        if selected.utility < self._minimum_utility:
            return None

        template = self._templates.get(
            selected.condition.condition_id,
            self._fallback_template(selected.condition),
        )
        previous_count = sum(
            turn.condition_id == selected.condition.condition_id
            for turn in state.dialogue_history
        )
        question_suffix = selected.condition.condition_id.removeprefix("cond.")
        status = condition_states.get(
            selected.condition.condition_id,
            ConditionStatus.UNKNOWN,
        )
        return QuestionCandidate(
            question_id=f"question.{question_suffix}.{previous_count + 1}",
            condition_id=selected.condition.condition_id,
            question=template.question,
            why_asked=self._build_why_asked(
                condition=selected.condition,
                status=status,
                has_case_contrast=self._component_value(
                    selected.score_components,
                    "case_contrast",
                )
                > 0.0,
            ),
            options=list(template.options),
            utility=selected.utility,
            score_components=list(selected.score_components),
            supporting_case_ids=list(selected.supporting_case_ids),
            limiting_case_ids=list(selected.limiting_case_ids),
        )

    def _active_conditions(self, bundle: RetrievalBundle) -> list[RuleCondition]:
        """按规则召回顺序展开条件，并对相同condition_id稳定去重。"""

        conditions: list[RuleCondition] = []
        seen_ids: set[str] = set()
        for reference in bundle.rule_refs:
            rule = self._rules_by_id.get(reference.object_id)
            if rule is None:
                continue
            for condition in rule.conditions:
                if condition.condition_id not in seen_ids:
                    conditions.append(condition)
                    seen_ids.add(condition.condition_id)
        return conditions

    def _score_condition(
        self,
        *,
        state: QueryState,
        bundle: RetrievalBundle,
        condition: RuleCondition,
        interaction_count: int,
    ) -> _ScoredQuestion:
        """计算一个候选条件的五项追问评分。"""

        rule_centrality = 1.0 if condition.required else 0.5
        case_contrast = self._calculate_case_contrast(bundle, condition.condition_id)
        answerability = 1.0 if condition.user_answerable else 0.0
        evidence_availability = self._calculate_evidence_availability(condition)
        interaction_cost = min(0.05 * interaction_count, 0.25)

        # 该公式来自D2-D7计划，是首版可解释启发式，不宣称为最优参数。
        utility = round(
            0.35 * rule_centrality
            + 0.30 * case_contrast
            + 0.20 * answerability
            + 0.15 * evidence_availability
            - interaction_cost,
            10,
        )
        supporting_case_ids = self._case_ids_with_condition(
            bundle.support_case_refs,
            condition.condition_id,
        )
        contrast_refs = [*bundle.limiting_case_refs, *bundle.boundary_case_refs]
        limiting_case_ids = self._case_ids_with_condition(
            contrast_refs,
            condition.condition_id,
        )
        components = (
            ScoreComponent(
                name="rule_centrality",
                value=rule_centrality,
                explanation=(
                    "该条件是候选规则的必要条件。"
                    if condition.required
                    else "该条件属于候选规则，但不是必要条件。"
                ),
            ),
            ScoreComponent(
                name="case_contrast",
                value=case_contrast,
                explanation="支持案例与限制或边界案例在该条件上的分化程度。",
            ),
            ScoreComponent(
                name="answerability",
                value=answerability,
                explanation="规则配置表明该条件可以由用户直接回答。",
            ),
            ScoreComponent(
                name="evidence_availability",
                value=evidence_availability,
                explanation="该条件对应证据在普通用户场景中的可获得程度。",
            ),
            ScoreComponent(
                name="interaction_cost",
                value=-interaction_cost,
                explanation="根据已经完成的交互轮数扣除追问成本。",
            ),
        )
        return _ScoredQuestion(
            condition=condition,
            utility=utility,
            rule_centrality=rule_centrality,
            score_components=components,
            supporting_case_ids=tuple(supporting_case_ids),
            limiting_case_ids=tuple(limiting_case_ids),
        )

    def _calculate_case_contrast(
        self,
        bundle: RetrievalBundle,
        condition_id: str,
    ) -> float:
        """使用小数据版本的离散规则计算正反案例分化度。"""

        support_statuses = self._condition_statuses(
            bundle.support_case_refs,
            condition_id,
        )
        contrast_statuses = self._condition_statuses(
            [*bundle.limiting_case_refs, *bundle.boundary_case_refs],
            condition_id,
        )
        if not support_statuses or not contrast_statuses:
            return 0.0

        for support_status in support_statuses:
            for contrast_status in contrast_statuses:
                if self._are_opposite(support_status, contrast_status):
                    return 1.0

        if set(support_statuses) != set(contrast_statuses):
            return 0.5
        return 0.0

    def _condition_statuses(
        self,
        references: list[ScoredReference],
        condition_id: str,
    ) -> list[ConditionStatus]:
        """读取一组案例对指定条件的有效认定状态。"""

        excluded = {ConditionStatus.UNKNOWN, ConditionStatus.CONFLICTING}
        statuses: list[ConditionStatus] = []
        for reference in references:
            case = self._cases_by_id.get(reference.object_id)
            if case is None:
                continue
            statuses.extend(
                finding.status
                for finding in case.condition_findings
                if finding.condition_id == condition_id and finding.status not in excluded
            )
        return statuses

    @staticmethod
    def _are_opposite(
        first: ConditionStatus,
        second: ConditionStatus,
    ) -> bool:
        """判断两个条件状态是否构成明确的成立与不成立对照。"""

        return {first, second} == {
            ConditionStatus.SATISFIED,
            ConditionStatus.NOT_SATISFIED,
        }

    @classmethod
    def _calculate_evidence_availability(cls, condition: RuleCondition) -> float:
        """根据规则配置的证据类型估计普通用户的取证可行性。"""

        if not condition.evidence_types:
            return 0.0
        if cls._COMMON_EVIDENCE_TYPES & set(condition.evidence_types):
            return 1.0
        return 0.5

    def _case_ids_with_condition(
        self,
        references: list[ScoredReference],
        condition_id: str,
    ) -> list[str]:
        """只返回确实包含当前条件认定的案例ID，并稳定去重。"""

        case_ids: list[str] = []
        seen_ids: set[str] = set()
        for reference in references:
            case = self._cases_by_id.get(reference.object_id)
            if case is None or reference.object_id in seen_ids:
                continue
            if any(
                finding.condition_id == condition_id
                for finding in case.condition_findings
            ):
                case_ids.append(reference.object_id)
                seen_ids.add(reference.object_id)
        return case_ids

    @staticmethod
    def _fallback_template(condition: RuleCondition) -> QuestionTemplate:
        """为尚未配置专用文案的新条件提供安全的通用问题。"""

        return QuestionTemplate(
            question=f"关于“{condition.label}”，实际情况是否满足这一条件？",
            options=("满足", "不满足", "不清楚"),
        )

    @staticmethod
    def _build_why_asked(
        *,
        condition: RuleCondition,
        status: ConditionStatus,
        has_case_contrast: bool,
    ) -> str:
        """根据当前不确定性和案例对照生成可解释的追问原因。"""

        reasons: list[str] = []
        if status == ConditionStatus.CONFLICTING:
            reasons.append("用户此前提供的信息在该条件上存在冲突")
        if condition.required:
            reasons.append("该条件是候选规则的必要条件")
        else:
            reasons.append("该条件可能改变最终解释分支")
        if has_case_contrast:
            reasons.append("支持案例与限制或边界案例在该条件上存在分歧")
        else:
            reasons.append("确认该事实可以减少当前解释的不确定性")
        return "；".join(reasons) + "。"

    @staticmethod
    def _component_value(
        components: tuple[ScoreComponent, ...],
        name: str,
    ) -> float:
        """读取指定评分组成；内部固定名称不存在时返回0。"""

        return next(
            (component.value for component in components if component.name == name),
            0.0,
        )
