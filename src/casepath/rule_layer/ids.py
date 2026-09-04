"""IDs shared with the demo workflow and downstream P3/P4 modules."""

RULE_GOOD_FAITH = "rule.contract.performance.good_faith.v1"
RULE_DELAY_AFTER_DEMAND = "rule.contract.termination.delay_after_demand.v1"
RULE_NONPERFORMANCE_TERMINATION = "rule.contract.termination.nonperformance.v1"
RULE_TERMINATION_RESTITUTION = "rule.contract.termination.restitution.v1"
RULE_SERVICE_TERMINATION_REFUND = "rule.service_contract.termination_refund.v1"

# This explicit allowlist prevents the build from upgrading every generated rule to L3/reviewed.
HUMAN_VERIFIED_L3_RULE_IDS = frozenset(
    {
        RULE_GOOD_FAITH,
        RULE_DELAY_AFTER_DEMAND,
        RULE_NONPERFORMANCE_TERMINATION,
        RULE_TERMINATION_RESTITUTION,
    }
)

COND_CONTRACT_EXISTS = "cond.contract_exists"
COND_OBLIGATION_UNPERFORMED = "cond.obligation_unperformed"
COND_PAYMENT_MADE = "cond.payment_made"
COND_UNPERFORMED_BALANCE = "cond.unperformed_balance"
COND_PERFORMANCE_IMPOSSIBLE = "cond.performance_impossible"
COND_ALTERNATIVE_PERFORMANCE = "cond.alternative_performance"
COND_CONTRACT_TERMINATED = "cond.contract_terminated"
COND_RESTITUTION_CONTEXT = "cond.restitution_context"
COND_MAIN_OBLIGATION_DELAYED = "cond.main_obligation_delayed"
COND_DEMAND_DELIVERED = "cond.demand_delivered"
COND_REASONABLE_PERIOD_EXPIRED = "cond.reasonable_period_expired"
