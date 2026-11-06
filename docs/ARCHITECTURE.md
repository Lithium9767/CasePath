# 架构与代码阅读

[返回首页](../README.md) · [团队分工](TEAM.md)

## 1. 离线知识生产

法条保存原文；规则把原文组织为条件、条件组、例外与后果。案例的轻量记录用于检索，
结构化记录进一步承载请求、法院事实认定、条件映射、理由与结果。

阅读顺序：
[规则合同](../src/casepath/contracts/rule.py) →
[案例合同](../src/casepath/contracts/case.py) →
[法条处理](../src/casepath/ingestion/laws/) / [规则构造](../src/casepath/rule_layer/) /
[案例处理](../src/casepath/ingestion/cases/) →
[图投影](../src/casepath/ingestion/graph/projection.py)。

图谱通过固定字段和显式 ID 引用投影，并非 embedding 自动生成实体关系。
跨层关联包括案例引用法条、请求关联规则、裁判理由适用规则、条件认定指向规则条件；
这些关系表达不同证据强度，不能互相替代。

## 2. 启动与单轮调用

```text
api/configured.py:create_configured_app
  → api/main.py:create_live_app
  → bootstrap.py:build_live_runtime
  → adapters/p4_factory.py:HybridCypherP4Factory.build
  → 装配基础设施与算法组件，等待 HTTP 请求

POST /v1/sessions
  → api/sessions.py:create_session
  → application/session_service.py:SessionService.create_session
  → SessionService._run
  → workflow/engine.py:CasePathWorkflow.run
  → 返回 WorkflowSnapshot、校验并保存会话
```

入口：[configured.py](../src/casepath/api/configured.py)、[工厂](../src/casepath/adapters/p4_factory.py)、
[会话服务](../src/casepath/application/session_service.py)、[工作流](../src/casepath/workflow/engine.py)。

补充回答走 POST answers → submit_answer → AnswerInterpreter → 合并事实/运行元数据 →
同一个单轮工作流。会话服务负责失败回滚、版本检查和成功请求重放；工作流负责单轮分析。

## 3. A–E 的当前实现

| 阶段 | 实际处理 | 主要入口 |
| --- | --- | --- |
| A 规则召回 | BM25/向量、请求类型标签匹配、共享条件图扩展、RRF；外层相关性与用途划分 | [hybrid_cypher.py](../src/casepath/adapters/retrieval/hybrid_cypher.py) 中 HybridCypherRuleRetriever；[rule_relevance.py](../src/casepath/adapters/retrieval/rule_relevance.py) |
| B 条件投影 | 活动条件与历史证据；按事实—条件及版本账本增量处理；LLM 映射后校验与聚合 | [semantic_condition_projector.py](../src/casepath/adapters/conditions/semantic_condition_projector.py) 中 project |
| C 案例检索 | 文本/向量、条件认定、适用规则、法条引用；融合后判断角色并分类保留 | [hybrid_cypher.py](../src/casepath/adapters/retrieval/hybrid_cypher.py) 中 HybridCypherCaseRetriever |
| D 条件比较 | 共享聚合函数统计状态数量、条件熵、分歧及未知覆盖；区分无证据/单例/多例 | [condition_comparator.py](../src/casepath/adapters/conditions/condition_comparator.py) 中 compare |
| E 缺失事实追问 | 候选条件 → 历史检查 → 缺失事实识别 → 校验、过滤、排序 → 生成一个问题 | [key_condition_policy.py](../src/casepath/adapters/questioning/key_condition_policy.py) 中 select；[missing_facts.py](../src/casepath/adapters/questioning/missing_facts.py) |

查询扩展在 [query_expansion.py](../src/casepath/adapters/retrieval/query_expansion.py)，
固定图模板在 [graph_navigator.py](../src/casepath/adapters/retrieval/graph_navigator.py)。
图/向量/模型是否真正启用取决于配置，不是固定四选一检索器，也没有使用 GNN 或 PageRank。

C 不再通过正负相容性分惩罚不同条件；差异案例可以有比较价值，但确定角色仍需要证据。
E 不进行回答模拟，当前 Live 也不以固定问题卡作为适用领域白名单。
D 的 expected_information_gain 是熵乘覆盖率的兼容字段，不是模拟得到的信息增益；互信息等仍有占位。

## 4. 解释与前端

[EvidenceExplanationPlanner](../src/casepath/adapters/explanation/evidence_explanation_planner.py)
根据状态决定澄清短说明或完整解释；完整生成见
[grounded_explanation.py](../src/casepath/adapters/explanation/grounded_explanation.py)。
规则、条件、案例和原文证据组织为上下文，而不是让模型自行补充用户事实。

快照包含 query_state、retrieval_bundle、comparison_bundle、next_question、explanation_plan 和 trace。
[前端 API](../frontend/src/api/client.ts) 消费快照；[右侧面板](../frontend/src/components/RightPanel/)
展示对应信息。全图接口读取当前发布版本，页面按返回 graph_paths 匹配有向关系高亮，不新增法律关系。

## 5. HTTP 接口

| 方法与路径 | 职责 |
| --- | --- |
| POST /v1/sessions | 创建会话并运行第一轮 |
| GET /v1/sessions/{session_id} | 读保存的快照，不重新分析 |
| POST /v1/sessions/{session_id}/answers | 提交回答并运行下一轮 |
| GET /v1/capabilities | 能力配置/探测状态，不保证每次业务调用成功 |
| GET /v1/graph | 当前图版本分页浏览，kind=nodes/edges；本地新增接口 |
| GET /health | HTTP 存活检查 |
| GET /v1/contracts/{contract_name}/schema | 合同 Schema |

定义：[sessions.py](../src/casepath/api/sessions.py)、[main.py](../src/casepath/api/main.py)、
[graph.py](../src/casepath/api/graph.py)。数据库凭据和模型密钥只保存在后端配置中。
