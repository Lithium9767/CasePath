# P4 HybridCypher 实现与验收说明

## 定位与当前状态

CasePath 使用 HybridCypher：全文/向量混合召回＋固定 Cypher 图扩展，并保留规则条件
驱动的独立图召回通道。这里是方法名称，不代表已安装或实现 Neo4j 同名 SDK 类。
不做四选一选择器，不将 A/B/C 分别硬分配给四种检索模式。

新增 adapters/retrieval/hybrid_cypher.py 只提供 A/C 框架和内部索引端口；调用未完成算法会
抛 NotImplementedError。没有装配到 Demo 或 Live，没有宣称 LIVE 能力可用。
KG 已有离线投影与写入器，但真实 Neo4j 入库仍需本机写账号配置并完成验证。

## 先读的代码

1. contracts/rule.py、case.py、query.py、retrieval.py、explanation.py、workflow.py。
2. ports/interfaces.py：A/B/C/D/E 和回答解释器的实际签名。
3. workflow/engine.py：规则→投影→案例→分化→追问→解释。
4. bootstrap.py：P4ComponentFactory/P4Components 与依赖注入。
5. ingestion/graph/projection.py、adapters/infrastructure/neo4j_writer.py，以及 KG两层图谱接入.md。
6. ingestion/cases/validation.py、rule_layer/validation.py：已发布数据的限制。

公共版本按具体对象处理：RuleRecord/CaseIndexRecord 默认 1.3，CaseRecord 1.1；
新 WorkflowSnapshot 默认 1.3。禁止批量替换版本。condition_id 使用已有下划线命名。

## 模块实现分工（P4 编写算法，P1 维护公共边界）

|模块|实现位置建议|输入 → 输出|核心工作|
|---|---|---|---|
|A|adapters/retrieval/hybrid_cypher.py|QueryState → ScoredReference[]|规则全文/向量融合，请求权匹配、图扩展|
|B|adapters/conditions/semantic_condition_projector.py|QueryState＋rule_refs → QueryState|候选条件获取、语义判定、多事实聚合|
|C|adapters/retrieval/hybrid_cypher.py|投影后的 QueryState＋rule_refs → RetrievalBundle|案例混合召回、独立条件图召回、重排与角色组织|
|D|adapters/conditions/condition_comparator.py|QueryState＋RetrievalBundle → ComparisonBundle|计算一次共享分化指标|
|E|adapters/questioning/information_gain_policy.py|状态＋检索＋比较 → QuestionCandidate或None|回答模拟、收益/成本、停止策略|
|回答解释|adapters/conditions/semantic_answer_interpreter.py|状态＋问题＋回答 → AnswerInterpretation|仅摘取回答原话、条件增量|
|解释|adapters/explanation/evidence_explanation_planner.py|状态＋检索＋比较 → ExplanationPlan|有引用的条件化解释|

除 A/C 框架外，上表文件尚待创建。不新建 p4 目录。每个文件顶部写明 P4 算法所有权。

## 实施顺序

### 第一步：索引和版本上下文（先解决，再接真实召回）

- 分别为规则、案例构造检索文档；全文和向量必须索引同一组对象 ID。
- 规则文档：标题、请求权、条件标签/命题、法条正文；生活语言扩写记录来源。
- 案例检索文档必须从允许的事实内容构建。facts_text 可能混有判决主文，不能直接
  全字段拼接；不把结果标签、最终裁判金额、支持/限制角色用于初始召回。
- 单独注入 embedding 客户端，记录 model/dimension/text_hash/index_version。
- 明确全文实现：自建 BM25 或 Neo4j fulltext。使用后者不能自动声称实现了 BM25F。
- 当前 KG 保留多个 release；索引应按发布隔离或在召回阶段严格限定发布。
  Top-K 后过滤旧版本会损失召回，不是充分的版本隔离实现。
- 每轮 A/B/C 必须共享同一个 RetrievalContext。现有公共接口没有该参数：P4 先提出
  请求级上下文传递方案，P1 定稿后接线。不得把版本写进共享 retriever 可变属性，
  不得分别读取 active_release 导致一轮混用版本，也不得塞进 score_components。
- 数据量小可先使用版本固定的内存全文/向量索引＋LegalGraphGateway 图查询。

### 第二步：A 与 B

A 初始算法：各通道召回 → 去重 → 加权 RRF（sum w/(60+rank)，缺席贡献0）→
请求权匹配与规则去冗余 → 候选规则。记录原通道与分数，不直接相加不同量纲。
规则只有5条时不要强制保留10条，也不必强行按40%家族配额裁剪。

B 从 A 的规则取共享条件，输出当前合同支持的 SATISFIED/NOT_SATISFIED/UNKNOWN/
CONFLICTING/NOT_APPLICABLE。LLM 只选已有 ID；逐字证据校验、按事件去重、冲突保留。
不同轮次不是独立证据，不能重复 noisy-OR 增强置信度。
confidence 表示映射可靠性，不是胜诉概率。概率模型先内部试验，别将 UNKNOWN 与
事实真假概率混作同一隐变量。CORE/DIAGNOSTIC 角色仍由规则条件组决定。

### 第三步：C 与当前图对齐

混合案例候选 ∪ 直接图候选 → ID 去重 → 已知条件相容性与相关性重排。
独立图通道沿候选规则/条件出发，不能只扩展混合召回命中的案例。

允许的主路径：

```text
LegalRule -HAS_GROUP→ ConditionGroup -HAS_MEMBER→ RuleCondition
Case -HAS_ASSESSMENT→ ConditionFinding -ABOUT→ RuleCondition
ConditionFinding -BASED_ON→ CourtFinding
ConditionFinding -EVIDENCED_BY→ SourceSpan
Case -HAS_REASONING→ ReasoningStep -APPLIES→ LegalRule
ReasoningStep -USES_FINDING→ CourtFinding
Case -HAS_DECISION→ DecisionItem -DECIDES→ Claim
Case -CITES_PROVISION→ Provision ←BASED_ON- LegalRule
```

不存在 INSTANTIATES/JUSTIFIES/REALIZES；不要照旧算法文档编造路径。
没有 finding_ids 的桥接可返回原文证据，不能补造 CourtFinding。
先只按事实相关性排序，再取裁判结果组织支持/限制/边界；图连接不等于法律推论。
单个案例对多个假设的角色可内部保存，公共 RetrievalBundle 仍要求一个主要角色。
不确定候选不能冒充边界案例；公共输出暂不支持 uncertain_case_refs，需另行定稿。
当前输出大多只有 ID；P4 内部需要同轮案例/证据缓存供 D 和解释读取，不能让 D 重新
召回另一批案例。该缓存也不能是跨请求共享的可变“上一轮结果”。

### 第四步：D、E 和解释

- 先实现明确状态的条件熵、分歧率、未知覆盖率；小样本 MI 不作为可靠排名依据。
- 首版追问效用以预期解释分支缩减减去回答成本为主；ERC 辅助，避免重复叠加指标。
- 问题来自 condition card；“不知道”作为回答选项，不等于条件不满足。
- 模拟不同回答使用独立状态副本，禁止修改真实会话、递归进入完整追问循环。
- 回答先验用保守先验并显式保留不知道概率，案例频率不等于当前用户回答概率。
- 用 Top-K Jaccard 等衡量排序变化；没有相关性金标准时不用在线 ΔnDCG。
- 最多3轮、过滤已问条件、低收益停止。仅排序稳定不能证明所有关键事实明确。
- 解释保留未知条件、原文引用和数据成熟度，不把未核验引用标成 verified。

### 第五步：接线

P4 实现 P4ComponentFactory，返回完整 WorkflowDependencies、AnswerInterpreter 和
真实能力清单。占位模块不能报告 available=True。资源由 P1 注入，P4 不读取密钥。
通过 create_live_app(factory) 装配；运行不可用按已有稳定异常返回，不能偷偷回退 Demo。
正式算法未完成前，继续保留当前默认 Demo。

## 验收清单

1. 同一数据和查询的候选结果稳定；重复规则/案例 ID 去重；各通道能追溯。
2. 人为构造“仅图命中”案例，必须进入 C 候选池。
3. 同一节点存在新旧图版本时，只检索本轮固定版本；并发会话不串版本和证据。
4. 修改 B 的已知条件后 C 排序发生可解释变化；UNKNOWN 不按否定条件过滤。
5. DIAGNOSTIC 不满足不导致核心规则被直接排除。
6. 用户重复回答不会增加独立证据数量；虚构原话、无效 ID 被拒绝。
7. 支持/限制角色有条件与原文依据，不能由裁判结果单独决定。
8. D 的比较结果同时被 E/解释复用；模拟回答不修改真实 SessionRecord。
9. 失败不提交会话更新；Neo4j/embedding 不可用不报告成功空结果。
10. 运行针对性测试、全量 pytest、Ruff、git diff --check。

当前仅有1条深度案例，先验证工程路径；扩充同规则族案例后再比较 Hybrid 与
HybridCypher 的召回、路径有效率、关键追问命中及延迟。不能据此声称论文效果已验证。

## 本次交付边界

只交付可导入框架和本执行说明；没有实现索引、召回、投影判定、分化和追问算法，
没有新增依赖或修改公共合同，没有将框架接入 bootstrap。后续验收按上述清单逐项执行。
