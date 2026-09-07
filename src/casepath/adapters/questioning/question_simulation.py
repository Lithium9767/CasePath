"""P4 E：固定候选池上的贝叶斯回答模拟，不执行网络调用或修改 QueryState。

概率为可替换的工程似然假设，不是法律结果概率；UNKNOWN 的似然对所有候选相同。
"""

import math

from casepath.contracts import ConditionStatus


def entropy(probabilities):
    return -sum(p * math.log2(p) for p in probabilities if p > 0)


def simulate_question(catalog, state, bundle, condition_id, compatible_rules):
    rules = [item.object_id for item in bundle.rule_refs]
    current = compatible_rules(catalog, state.condition_states, rules)
    hypotheses = []
    for item in bundle.rule_refs:
        if item.object_id not in current:
            continue
        simulated = [
            entry.model_copy(update={"status": ConditionStatus.NOT_SATISFIED})
            if entry.condition_id == condition_id
            else entry.model_copy(deep=True)
            for entry in state.condition_states
        ]
        retained = compatible_rules(catalog, simulated, [item.object_id])
        likelihood = (0.65, 0.05, 0.30) if not retained else (0.35, 0.35, 0.30)
        hypotheses.append(("rule:" + item.object_id, item.score, likelihood))
    refs = [*bundle.support_case_refs, *bundle.limiting_case_refs, *bundle.boundary_case_refs]
    seen = set()
    for item in refs:
        if item.object_id in seen:
            continue
        seen.add(item.object_id)
        case = catalog.cases.get(item.object_id)
        statuses = (
            {
                a.status
                for a in case.assessments
                if a.condition_id == condition_id and (a.span_ids or a.finding_ids)
            }
            if case
            else set()
        )
        likelihood = (0.35, 0.35, 0.30)
        if statuses == {ConditionStatus.SATISFIED}:
            likelihood = (0.65, 0.05, 0.30)
        elif statuses == {ConditionStatus.NOT_SATISFIED}:
            likelihood = (0.05, 0.65, 0.30)
        hypotheses.append(("case:" + item.object_id, item.score, likelihood))
    if len(hypotheses) < 2:
        return 0.0, 0.0
    # 同一候选池分数 softmax；采用稳定减最大值，避免溢出。
    peak = max(item[1] for item in hypotheses)
    weights = [math.exp(item[1] - peak) for item in hypotheses]
    prior = [value / sum(weights) for value in weights]
    original_rank = sorted(range(len(prior)), key=lambda i: (-prior[i], hypotheses[i][0]))
    ranks = {item: rank for rank, item in enumerate(original_rank)}
    expected_entropy = rank_change = 0.0
    for answer in range(3):
        weighted = [prior[i] * item[2][answer] for i, item in enumerate(hypotheses)]
        probability = sum(weighted)
        posterior = [weight / probability for weight in weighted]
        expected_entropy += probability * entropy(posterior)
        ranking = sorted(range(len(prior)), key=lambda i: (-posterior[i], hypotheses[i][0]))
        distance = sum(abs(rank - ranks[item]) for rank, item in enumerate(ranking))
        rank_change += probability * distance / max(1, len(prior) ** 2 // 2)
    gain = max(0.0, entropy(prior) - expected_entropy) / math.log2(len(prior))
    return gain, rank_change
