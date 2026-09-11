"""正式 P4 算法装配入口（HybridCypher）。

不替代 `adapters.demo` 离线基线。图/LLM/向量由 P1 注入；未注入的通道须降级报告。
"""

from __future__ import annotations

from pathlib import Path

from casepath.adapters.conditions.condition_comparator import ConditionComparator
from casepath.adapters.conditions.semantic_answer_interpreter import SemanticAnswerInterpreter
from casepath.adapters.conditions.semantic_condition_projector import SemanticConditionProjector
from casepath.adapters.explanation.evidence_explanation_planner import EvidenceExplanationPlanner
from casepath.adapters.questioning.key_condition_policy import KeyConditionQuestionPolicy
from casepath.adapters.retrieval.graph_navigator import MemoryGraphNavigator, Neo4jGraphNavigator
from casepath.adapters.retrieval.hybrid_cypher import (
    HybridCypherCaseRetriever,
    HybridCypherRuleRetriever,
)
from casepath.adapters.retrieval.query_expansion import ExpandedQuerySearch
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


# 组装算法结构
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
        # 把查询文本转化成向量的embedding模型
        self._embedding_model = embedding_model
        # 向量索引已有规则
        self._vector_index = vector_index
        self._calibrator = calibrator
        # 【默认入口未启用】configured.py 不传此参数；这只是可注入的校准扩展点。
        # 不注入时不会执行 transform，LLM 原始置信度也不能解释成统计正确率。

    # 懒加载（需要时加载），catalog磁盘上的"规则/法条/案例"四个 jsonl 文件，定义再retrivel_catalog中
    def _catalog_or_load(self) -> RetrievalCatalog:
        if self._catalog is None:
            self._catalog = load_canonical_catalog(self._data_root)
        return self._catalog


    # 组装算法结构
    def build(
        self,
        *,
        graph_gateway: LegalGraphGateway | None,
        language_model: StructuredLanguageModel | None,
    ) -> P4Components:
        # 【启动 4 的内部】调用方 bootstrap.build_live_runtime 的 p4_factory.build(...)。
        # 顺序：加载目录 → 选择文本/向量搜索器 → 选择图访问器 → 创建各算法对象。
        # 以下构造函数用于准备依赖；实际用户输入到来后才执行 retrieve/project/select。
        # 取得catalog
        catalog = self._catalog_or_load()
        # 默认检索方式没有向量就先用内存/BM25。
        search = MemoryHybridSearch(catalog)
        # 【不是废代码】无向量时直接使用；VectorHybridSearch 内部也使用它做 BM25。
        # 如果有embedding，使用向量检索
        vector_enabled = self._embedding_model is not None
        if vector_enabled:
            # 使用向量检索
            search = VectorHybridSearch(catalog, self._embedding_model, self._vector_index)
        # A先生成检索术语，C复用；原始查询保留，扩展绝不写入用户事实。
        if language_model is not None:
            search = ExpandedQuerySearch(search, language_model)
        # 如果graph_gateway不为空，使用图检索
        navigator = (
            # 【二选一装配】有图网关使用 Neo4j；无网关时保留内存邻接降级路径。
            # 选择的是图访问实现，不是课程示例中的四种检索器动态选择。
            # 使用图检索
            Neo4jGraphNavigator(graph_gateway)
            if graph_gateway is not None
            # 使用内存检索，一批关系数据加载到内存中，进行字典图遍历
            else MemoryGraphNavigator(catalog)
        )
        rules = HybridCypherRuleRetriever(navigator, search, catalog=catalog, navigator=navigator)
        # 如果有大语言模型
        if language_model is not None:
            # 只增加主题相关性筛选；这里的模型调用不负责最终解释或法律结论。
            # 再包裹一层LLM，去掉主题明显不对的规则
            rules = RelevantRuleRetriever(rules, catalog, language_model)
        # 案例检索
        cases = HybridCypherCaseRetriever(navigator, search, catalog=catalog, navigator=navigator)
        # 条件投影
        projector = SemanticConditionProjector(catalog, language_model, calibrator=self._calibrator)
        dependencies = WorkflowDependencies(
            # 【下一站 workflow/engine.py】这些对象分别对应 A/B/C/D/E 与解释阶段。
            rule_retriever=rules,
            condition_projector=projector,
            case_retriever=cases,
            case_comparator=ConditionComparator(catalog),
            question_policy=KeyConditionQuestionPolicy(catalog, language_model),
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
                "query_expansion", available=language_model is not None,
                mode=CapabilityMode.LIVE if language_model is not None else CapabilityMode.DISABLED,
                degraded=language_model is None,
                reason="A/C原始查询＋受约束术语扩展，单轮复用；不作为用户事实。"
                if language_model is not None else "无LLM，仅原始查询检索。",
            ),
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
                    "受约束LLM直接映射及原文校验。"
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
                mode=CapabilityMode.LIVE if language_model is not None else CapabilityMode.MEMORY,
                degraded=True,
                reason="关键未知条件优先级＋LLM简短事实追问；无回答模拟，生成质量尚需评测。"
                if language_model is not None else "无LLM：仅人工问题模板基线，跨领域覆盖受限。",
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
                reason="动态事实追问仅保存回答原话，交B结合对话解释；旧模板保留选项映射。",
            ),
        )
        return P4Components(
            # 【回到 bootstrap】workflow_dependencies 交给 CasePathWorkflow，
            # answer_interpreter 交给 SessionService；两者承担单轮计算与回答接入的不同职责。
            # capabilities 供能力接口报告，不作为一次用户提问的检索结果。
            # 回答解释器另交给 SessionService；它不在首次提问的 A-E 顺序内。
            # 当前 SemanticAnswerInterpreter 不注入 LLM；重跑投影时才可再次调用 LLM。
            workflow_dependencies=dependencies,
            answer_interpreter=SemanticAnswerInterpreter(),
            capabilities=capabilities,
        )
