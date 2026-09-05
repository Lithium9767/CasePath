from __future__ import annotations

from dataclasses import dataclass

from casepath.contracts import (
    ConditionStatus,
    DialogueTurn,
    QueryConditionState,
    QueryState,
    SessionStatus,
    WorkflowSnapshot,
)
from casepath.ports import (
    CaseRetriever,
    ConditionProjector,
    ExplanationPlanner,
    QuestionPolicy,
    RuleRetriever,
)


@dataclass(frozen=True)
class WorkflowDependencies:
    rule_retriever: RuleRetriever
    case_retriever: CaseRetriever
    condition_projector: ConditionProjector
    question_policy: QuestionPolicy
    explanation_planner: ExplanationPlanner


class CasePathWorkflow:
    """Deterministic orchestration; adapters may use databases or LLMs behind ports."""

    def __init__(self, dependencies: WorkflowDependencies) -> None:
        self.dependencies = dependencies

    def run(self, state: QueryState) -> WorkflowSnapshot:
        trace = ["PARSE_QUERY"]
        rule_refs = self.dependencies.rule_retriever.retrieve(state)
        trace.append("RETRIEVE_RULES")

        bundle = self.dependencies.case_retriever.retrieve(state, rule_refs)
        trace.extend(["RETRIEVE_CASES", "BUILD_CONTRAST_PANEL"])

        projected = self.dependencies.condition_projector.project(state, bundle)
        trace.append("PROJECT_QUERY")

        question = self.dependencies.question_policy.select(projected, bundle)
        if question is None:
            projected = projected.model_copy(update={"status": SessionStatus.READY_TO_EXPLAIN})
            trace.append("STOP_CLARIFICATION")
        else:
            projected = projected.model_copy(update={"status": SessionStatus.NEEDS_CLARIFICATION})
            trace.append("SCORE_QUESTIONS")

        plan = self.dependencies.explanation_planner.build(projected, bundle)
        trace.extend(["BUILD_EXPLANATION_PLAN", "VERIFY_CITATIONS"])
        return WorkflowSnapshot(
            query_state=projected,
            retrieval_bundle=bundle,
            next_question=question,
            explanation_plan=plan,
            trace=trace,
        )

    def apply_answer(
        self,
        state: QueryState,
        *,
        question_id: str,
        question: str,
        condition_id: str,
        answer: str,
        status: ConditionStatus,
    ) -> WorkflowSnapshot:
        """Update one selected condition and rerun the deterministic workflow."""

        turn_id = len(state.dialogue_history) + 1
        new_history = [
            *state.dialogue_history,
            DialogueTurn(
                turn_id=turn_id,
                question_id=question_id,
                condition_id=condition_id,
                question=question,
                answer=answer,
            ),
        ]
        existing = {item.condition_id: item for item in state.condition_states}
        existing[condition_id] = QueryConditionState(
            condition_id=condition_id,
            status=status,
            last_updated_turn=turn_id,
        )
        updated = QueryState.model_validate(
            {
                **state.model_dump(),
                "condition_states": list(existing.values()),
                "dialogue_history": new_history,
            }
        )
        return self.run(updated)
