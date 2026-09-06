"""P4 实现区：HybridCypher 组件工厂。默认 Demo 不被本文件替换。

资源由 P1 注入。占位能力不得报告 available=True。向量通道未实现，必须单独降级。
"""

from __future__ import annotations

from pathlib import Path

from casepath.adapters.condition_comparator import ConditionComparator
from casepath.adapters.evidence_explanation_planner import EvidenceExplanationPlanner
from casepath.adapters.graph_navigator import MemoryGraphNavigator, Neo4jGraphNavigator
from casepath.adapters.hybrid_cypher import HybridCypherCaseRetriever, HybridCypherRuleRetriever
from casepath.adapters.information_gain_policy import InformationGainPolicy
from casepath.adapters.retrieval_catalog import (
    MemoryHybridSearch,
    RetrievalCatalog,
    load_canonical_catalog,
)
from casepath.adapters.semantic_answer_interpreter import SemanticAnswerInterpreter
from casepath.adapters.semantic_condition_projector import SemanticConditionProjector
from casepath.bootstrap import P4Components
from casepath.contracts import CapabilityMode, CapabilityStatus
from casepath.ports import LegalGraphGateway, StructuredLanguageModel
from casepath.workflow import WorkflowDependencies


def _capability(
    name: str,
    *,
    available: bool,
    mode: CapabilityMode,
    degraded: bool,
    reason: str,
) -> CapabilityStatus:
    return CapabilityStatus(
        capability=name,
        available=available,
        mode=mode,
        degraded=degraded,
        reason=reason,
    )


class HybridCypherP4Factory:
    def __init__(
        self, catalog: RetrievalCatalog | None = None, data_root: Path | None = None
    ) -> None:
        self._catalog = catalog
        self._data_root = data_root or Path("data")

    def _catalog_or_load(self) -> RetrievalCatalog:
        if self._catalog is None:
            self._catalog = load_canonical_catalog(self._data_root)
        return self._catalog

    def build(
        self,
        *,
        graph_gateway: LegalGraphGateway | None,
        language_model: StructuredLanguageModel | None,
    ) -> P4Components:
        catalog = self._catalog_or_load()
        search = MemoryHybridSearch(catalog)
        navigator = (
            Neo4jGraphNavigator(graph_gateway)
            if graph_gateway is not None
            else MemoryGraphNavigator(catalog)
        )
        rules = HybridCypherRuleRetriever(navigator, search, catalog=catalog, navigator=navigator)
        cases = HybridCypherCaseRetriever(navigator, search, catalog=catalog, navigator=navigator)
        projector = SemanticConditionProjector(catalog, language_model)
        dependencies = WorkflowDependencies(
            rule_retriever=rules,
            condition_projector=projector,
            case_retriever=cases,
            case_comparator=ConditionComparator(catalog),
            question_policy=InformationGainPolicy(catalog),
            explanation_planner=EvidenceExplanationPlanner(catalog),
        )
        graph_mode = CapabilityMode.LIVE if graph_gateway is not None else CapabilityMode.MEMORY
        retriever_reason = "BM25＋图召回基线；向量通道未实现，检索结果降级。"
        if graph_gateway is None:
            retriever_reason += "未注入图网关，图扩展走与投影一致的内存邻接。"
        capabilities = (
            _capability(
                "rule_retriever",
                available=True,
                mode=graph_mode,
                degraded=True,
                reason=retriever_reason,
            ),
            _capability(
                "case_retriever",
                available=True,
                mode=graph_mode,
                degraded=True,
                reason=retriever_reason,
            ),
            _capability(
                "vector_search",
                available=False,
                mode=CapabilityMode.DISABLED,
                degraded=True,
                reason="向量 embedding 通道尚未实现；不得因图网关或LLM已注入而报告向量可用。",
            ),
            _capability(
                "condition_projector",
                available=True,
                mode=CapabilityMode.LIVE if language_model is not None else CapabilityMode.MEMORY,
                degraded=language_model is None,
                reason=(
                    "词法候选＋受约束LLM判定。"
                    if language_model is not None
                    else "未注入LLM，仅使用保守词法候选。"
                ),
            ),
            _capability(
                "case_comparator",
                available=True,
                mode=CapabilityMode.MEMORY,
                degraded=True,
                reason="互信息始终为0；expected_information_gain 为熵×覆盖率启发式。",
            ),
            _capability(
                "question_policy",
                available=True,
                mode=CapabilityMode.MEMORY,
                degraded=True,
                reason="当前计算规则候选缩减，尚未建立条件化解释分支。",
            ),
            _capability(
                "explanation_planner",
                available=True,
                mode=CapabilityMode.MEMORY,
                degraded=True,
                reason="解释引用未核验；条件化解释分支仍是规则标题占位。",
            ),
            _capability(
                "answer_interpreter",
                available=True,
                mode=CapabilityMode.MEMORY,
                degraded=False,
                reason="选项按 condition_id+完整选项映射；自由回答先处理否定作用范围。",
            ),
        )
        return P4Components(
            workflow_dependencies=dependencies,
            answer_interpreter=SemanticAnswerInterpreter(),
            capabilities=capabilities,
        )
