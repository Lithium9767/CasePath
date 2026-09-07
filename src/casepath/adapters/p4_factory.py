"""正式 P4 算法装配入口（HybridCypher）。

不替代 `adapters.demo` 离线基线。图/LLM/向量由 P1 注入；未注入的通道须降级报告。
"""

from __future__ import annotations

from pathlib import Path

from casepath.adapters.conditions.condition_comparator import ConditionComparator
from casepath.adapters.conditions.semantic_answer_interpreter import SemanticAnswerInterpreter
from casepath.adapters.conditions.semantic_condition_projector import SemanticConditionProjector
from casepath.adapters.explanation.evidence_explanation_planner import EvidenceExplanationPlanner
from casepath.adapters.questioning.information_gain_policy import InformationGainPolicy
from casepath.adapters.retrieval.graph_navigator import MemoryGraphNavigator, Neo4jGraphNavigator
from casepath.adapters.retrieval.hybrid_cypher import (
    HybridCypherCaseRetriever,
    HybridCypherRuleRetriever,
)
from casepath.adapters.retrieval.retrieval_catalog import (
    MemoryHybridSearch,
    RetrievalCatalog,
    load_canonical_catalog,
)
from casepath.adapters.retrieval.rule_relevance import RelevantRuleRetriever
from casepath.adapters.retrieval.vector_search import VectorHybridSearch, VectorIndex
from casepath.bootstrap import P4Components
from casepath.contracts import CapabilityMode, CapabilityStatus
from casepath.ports import LegalGraphGateway, StructuredLanguageModel
from casepath.ports.embedding import EmbeddingModel
from casepath.workflow import WorkflowDependencies


# 快捷构造工具函数
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
    # 保存运行时需要的资源
    def __init__(
        self,
        catalog: RetrievalCatalog | None = None,
        data_root: Path | None = None,
        *,
        embedding_model: EmbeddingModel | None = None,
        vector_index: VectorIndex | None = None,
        calibrator=None,
    ) -> None:
        self._catalog = catalog
        self._data_root = data_root or Path("data")
        if (embedding_model is None) != (vector_index is None):
            raise ValueError("embedding 客户端与已发布索引必须同时注入")
        self._embedding_model = embedding_model
        self._vector_index = vector_index
        self._calibrator = calibrator

    # 懒加载（需要时加载），catalog磁盘上的"规则/法条/案例"四个 jsonl 文件，定义再retrivel_catalog中
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
        # 取得catalog
        catalog = self._catalog_or_load()
        # 默认先用内存/BM25。
        search = MemoryHybridSearch(catalog)
        vector_enabled = self._embedding_model is not None
        if vector_enabled:
            search = VectorHybridSearch(catalog, self._embedding_model, self._vector_index)
        navigator = (
            Neo4jGraphNavigator(graph_gateway)
            if graph_gateway is not None
            else MemoryGraphNavigator(catalog)
        )
        rules = HybridCypherRuleRetriever(navigator, search, catalog=catalog, navigator=navigator)
        if language_model is not None:
            rules = RelevantRuleRetriever(rules, catalog, language_model)
        cases = HybridCypherCaseRetriever(navigator, search, catalog=catalog, navigator=navigator)
        projector = SemanticConditionProjector(catalog, language_model, calibrator=self._calibrator)
        dependencies = WorkflowDependencies(
            rule_retriever=rules,
            condition_projector=projector,
            case_retriever=cases,
            case_comparator=ConditionComparator(catalog),
            question_policy=InformationGainPolicy(catalog),
            explanation_planner=EvidenceExplanationPlanner(catalog, language_model),
        )
        graph_mode = CapabilityMode.LIVE if graph_gateway is not None else CapabilityMode.MEMORY
        retriever_reason = "BM25＋图召回基线；未注入 embedding/向量索引，向量通道降级。"
        if vector_enabled:
            retriever_reason = "BM25＋版本固定的向量召回＋图召回；向量索引校验通过。"
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
                available=vector_enabled,
                mode=CapabilityMode.LIVE if vector_enabled else CapabilityMode.DISABLED,
                degraded=not vector_enabled,
                reason="已注入模型与同版本索引"
                if vector_enabled
                else "未配置 embedding 与向量索引",
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
                reason="固定候选池三回答模拟：信息增益、期望名次变化与回答成本；似然尚需标注集验证。",
            ),
            _capability(
                "explanation_planner",
                available=True,
                mode=CapabilityMode.LIVE if language_model is not None else CapabilityMode.MEMORY,
                degraded=True,
                reason="证据包约束的LLM条件化解释，引用ID校验但未做语义核验。"
                if language_model is not None else "未注入LLM，使用确定性证据摘要。",
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
