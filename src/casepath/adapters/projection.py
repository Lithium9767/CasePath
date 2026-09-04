from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from casepath.contracts import (
    ConditionStatus,
    QueryConditionState,
    QueryState,
    RetrievalBundle,
    RuleRecord,
    UserFact,
)


@dataclass(frozen=True)
class ConditionProjectionPattern:
    """一个规则条件对应的确定性正反短语配置。

    `positive_phrases`表示该条件成立的语言证据，`negative_phrases`表示该条件
    不成立的语言证据。短语配置必须由人工检查，不能根据条件名称自动生成，
    否则法律语义中的否定、例外和时间关系很容易被错误简化。
    """

    positive_phrases: tuple[str, ...] = ()
    negative_phrases: tuple[str, ...] = ()


@dataclass(frozen=True)
class _ConditionEvidence:
    """投影器内部使用的条件证据，不作为跨模块合同暴露。"""

    fact: UserFact
    status: ConditionStatus


DEFAULT_PROJECTION_PATTERNS: dict[str, ConditionProjectionPattern] = {
    "cond.contract_exists": ConditionProjectionPattern(
        positive_phrases=("服务合同", "办理会员卡", "办了卡", "健身卡", "充值"),
        negative_phrases=("没有签订合同", "未签订合同", "没有办理会员卡"),
    ),
    "cond.payment_made": ConditionProjectionPattern(
        positive_phrases=("充了", "充值", "已经付款", "已经支付", "缴费", "交了"),
        negative_phrases=("没有付款", "未付款", "没有支付", "未支付"),
    ),
    "cond.unperformed_balance": ConditionProjectionPattern(
        positive_phrases=("未消费", "没消费", "剩余余额", "还有余额", "余额"),
        negative_phrases=("已经全部消费", "余额为零", "没有余额"),
    ),
    "cond.performance_impossible": ConditionProjectionPattern(
        positive_phrases=("永久停业", "全部门店关闭", "停止经营", "公司注销"),
        negative_phrases=(
            "暂时关闭",
            "仍在营业",
            "恢复营业",
            "不是永久停业",
            "并非永久停业",
        ),
    ),
    "cond.alternative_performance": ConditionProjectionPattern(
        # 当前条件ID表示“存在替代履行”，因此能转店属于肯定证据。
        positive_phrases=("可以转店", "可以转到其他门店", "仍可在其他门店使用"),
        negative_phrases=("没有其他门店", "不能转店", "没有替代门店"),
    ),
}


class RuleConditionProjector:
    """把用户陈述投影到本轮候选规则的条件状态。"""

    def __init__(
        self,
        *,
        rules_by_id: Mapping[str, RuleRecord],
        patterns: Mapping[str, ConditionProjectionPattern] | None = None,
    ) -> None:
        """保存规则快照和人工核验的条件短语配置。"""

        self._rules_by_id = dict(rules_by_id)
        self._patterns = dict(DEFAULT_PROJECTION_PATTERNS)
        if patterns is not None:
            # 调用方配置覆盖默认值，便于P2为新规则提供更准确的领域短语。
            self._patterns.update(patterns)

    def project(
        self,
        state: QueryState,
        bundle: RetrievalBundle,
    ) -> QueryState:
        """生成条件状态及其事实来源，并保留其他模块写入的非活动条件。"""

        active_condition_ids = self._active_condition_ids(bundle)
        if not active_condition_ids:
            return state

        existing_states = {item.condition_id: item for item in state.condition_states}
        facts_by_id = {fact.fact_id: fact for fact in state.user_facts}
        projected_states: list[QueryConditionState] = []

        for condition_id in active_condition_ids:
            evidence_by_id = self._collect_condition_evidence(
                state=state,
                condition_id=condition_id,
                existing_state=existing_states.get(condition_id),
            )
            for evidence in evidence_by_id.values():
                # 相同fact_id直接覆盖为相同内容，使重复投影保持幂等。
                facts_by_id[evidence.fact.fact_id] = evidence.fact

            projected_states.append(
                self._build_condition_state(
                    condition_id=condition_id,
                    evidence_by_id=evidence_by_id,
                    existing_state=existing_states.get(condition_id),
                )
            )

        # 当前未命中规则的既有条件可能属于其他请求权，投影器不能擅自删除。
        active_id_set = set(active_condition_ids)
        projected_states.extend(
            item for item in state.condition_states if item.condition_id not in active_id_set
        )

        return state.model_copy(
            update={
                "user_facts": list(facts_by_id.values()),
                "condition_states": projected_states,
            }
        )

    def _active_condition_ids(self, bundle: RetrievalBundle) -> list[str]:
        """按规则召回顺序展开条件ID，并稳定去重。"""

        condition_ids: list[str] = []
        seen_ids: set[str] = set()
        for reference in bundle.rule_refs:
            rule = self._rules_by_id.get(reference.object_id)
            if rule is None:
                # 数据装配暂时不一致时跳过未知引用，交由上层能力状态报告降级。
                continue
            for condition in rule.conditions:
                if condition.condition_id not in seen_ids:
                    condition_ids.append(condition.condition_id)
                    seen_ids.add(condition.condition_id)
        return condition_ids

    def _collect_condition_evidence(
        self,
        *,
        state: QueryState,
        condition_id: str,
        existing_state: QueryConditionState | None,
    ) -> dict[str, _ConditionEvidence]:
        """合并结构化事实、初始描述和逐轮回答中的条件证据。"""

        evidence_by_id: dict[str, _ConditionEvidence] = {}

        # 已结构化的布尔事实具有明确谓词，可以直接映射为条件状态。
        for fact in state.user_facts:
            if fact.predicate != condition_id or not isinstance(fact.value, bool):
                continue
            evidence_by_id[fact.fact_id] = _ConditionEvidence(
                fact=fact,
                status=(
                    ConditionStatus.SATISFIED
                    if fact.value
                    else ConditionStatus.NOT_SATISFIED
                ),
            )

        pattern = self._patterns.get(condition_id)
        if pattern is not None:
            initial_evidence = self._extract_text_evidence(
                text=state.initial_query,
                condition_id=condition_id,
                source_turn=0,
                fact_prefix="fact.initial",
                pattern=pattern,
            )
            evidence_by_id.update(
                {evidence.fact.fact_id: evidence for evidence in initial_evidence}
            )

        turns_by_id = {
            turn.turn_id: turn
            for turn in state.dialogue_history
            if turn.condition_id == condition_id and turn.answer
        }
        for turn_id, turn in turns_by_id.items():
            turn_evidence = (
                self._extract_text_evidence(
                    text=turn.answer or "",
                    condition_id=condition_id,
                    source_turn=turn_id,
                    fact_prefix=f"fact.turn.{turn_id}",
                    pattern=pattern,
                )
                if pattern is not None
                else []
            )
            evidence_by_id.update(
                {evidence.fact.fact_id: evidence for evidence in turn_evidence}
            )

            # apply_answer会记录人工或界面确认后的状态。自由回答没有命中短语时，
            # 仍可使用该明确状态，但必须同时生成指向原回答的事实记录。
            if not turn_evidence and existing_state is not None:
                explicit = self._evidence_from_explicit_state(
                    existing_state=existing_state,
                    answer=turn.answer or "",
                    turn_id=turn_id,
                )
                if explicit is not None:
                    evidence_by_id[explicit.fact.fact_id] = explicit

        return evidence_by_id

    @classmethod
    def _extract_text_evidence(
        cls,
        *,
        text: str,
        condition_id: str,
        source_turn: int,
        fact_prefix: str,
        pattern: ConditionProjectionPattern,
    ) -> list[_ConditionEvidence]:
        """从一段用户原话中提取最多一条肯定和一条否定证据。"""

        polarities = cls._detect_polarities(text=text, pattern=pattern)
        evidence: list[_ConditionEvidence] = []
        for polarity in polarities:
            is_positive = polarity == "positive"
            status = (
                ConditionStatus.SATISFIED
                if is_positive
                else ConditionStatus.NOT_SATISFIED
            )
            fact = UserFact(
                fact_id=f"{fact_prefix}.{condition_id}.{polarity}",
                text=text,
                predicate=condition_id,
                value=is_positive,
                source_turn=source_turn,
            )
            evidence.append(_ConditionEvidence(fact=fact, status=status))
        return evidence

    @staticmethod
    def _detect_polarities(
        *,
        text: str,
        pattern: ConditionProjectionPattern,
    ) -> list[str]:
        """识别正反短语，并避免否定短语内部的肯定子串造成假冲突。"""

        negative_spans: list[tuple[int, int]] = []
        for phrase in pattern.negative_phrases:
            start = text.find(phrase)
            while start >= 0:
                negative_spans.append((start, start + len(phrase)))
                start = text.find(phrase, start + 1)

        positive_found = False
        for phrase in pattern.positive_phrases:
            start = text.find(phrase)
            while start >= 0:
                end = start + len(phrase)
                covered_by_negative = any(
                    negative_start <= start and end <= negative_end
                    for negative_start, negative_end in negative_spans
                )
                if not covered_by_negative:
                    positive_found = True
                    break
                start = text.find(phrase, start + 1)
            if positive_found:
                break

        polarities: list[str] = []
        if positive_found:
            polarities.append("positive")
        if negative_spans:
            polarities.append("negative")
        return polarities

    @staticmethod
    def _evidence_from_explicit_state(
        *,
        existing_state: QueryConditionState,
        answer: str,
        turn_id: int,
    ) -> _ConditionEvidence | None:
        """把工作流明确写入的回答状态转换为可追溯事实。"""

        if existing_state.last_updated_turn != turn_id:
            return None
        if existing_state.status not in {
            ConditionStatus.SATISFIED,
            ConditionStatus.NOT_SATISFIED,
        }:
            return None

        is_positive = existing_state.status == ConditionStatus.SATISFIED
        fact = UserFact(
            fact_id=(
                f"fact.turn.{turn_id}.{existing_state.condition_id}.explicit"
            ),
            text=answer,
            predicate=existing_state.condition_id,
            value=is_positive,
            source_turn=turn_id,
        )
        return _ConditionEvidence(fact=fact, status=existing_state.status)

    @staticmethod
    def _build_condition_state(
        *,
        condition_id: str,
        evidence_by_id: dict[str, _ConditionEvidence],
        existing_state: QueryConditionState | None,
    ) -> QueryConditionState:
        """根据证据极性生成SATISFIED、NOT_SATISFIED或CONFLICTING。"""

        statuses = {evidence.status for evidence in evidence_by_id.values()}
        if {
            ConditionStatus.SATISFIED,
            ConditionStatus.NOT_SATISFIED,
        }.issubset(statuses):
            status = ConditionStatus.CONFLICTING
        elif ConditionStatus.SATISFIED in statuses:
            status = ConditionStatus.SATISFIED
        elif ConditionStatus.NOT_SATISFIED in statuses:
            status = ConditionStatus.NOT_SATISFIED
        elif existing_state is not None and existing_state.status == ConditionStatus.NOT_APPLICABLE:
            # NOT_APPLICABLE通常来自规则结构判断，不应在没有新证据时被改回UNKNOWN。
            return existing_state
        else:
            status = ConditionStatus.UNKNOWN

        supporting_fact_ids = list(evidence_by_id)
        last_updated_turn = max(
            (evidence.fact.source_turn for evidence in evidence_by_id.values()),
            default=0,
        )
        return QueryConditionState(
            condition_id=condition_id,
            status=status,
            supporting_fact_ids=supporting_fact_ids,
            last_updated_turn=last_updated_turn,
        )
