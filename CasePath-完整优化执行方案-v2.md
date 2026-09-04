# CasePath：规则—案例对比驱动的交互式民事法律解释框架

> 版本：v2.0（架构确认稿）  
> 范围：中国民法领域；第一阶段搭建全域框架，效果不足时再收缩到单一争议类型  
> 核心贡献候选：规则条件与正反案例共同驱动的高价值追问，并据此生成可溯源的最终法律解释  
> 本文性质：技术执行方案，不构成法律意见

---

## 摘要

本文提出 CasePath，一种面向普通用户不完整、口语化民事问题的交互式法律解释框架。系统采用两层层级知识结构：规范规则层表示法律规则的适用条件、例外、法律后果、举证责任与证据要求；案例适用层表示历史案件中的请求权、当事人主张、证据、法院认定、裁判理由与分项裁判结果。两层通过“案例事实是否满足规则条件”“裁判理由适用何种规则”“裁判结果实现何种法律后果”等关系连接。

系统不把案例比较作为最终输出，而是利用同一规则下正向、反向及边界案例的裁判分化，识别用户描述中最可能改变法律解释的未知条件，主动向用户追问。用户补充事实后，系统更新规则条件状态、重新检索和比较案例，最终输出请求权、规则适用、条件化解释、支持与限制性案例、证据提示及原文引用。

与 LegalGraphRAG 的固定输入法律裁判任务不同，CasePath 将研究问题定义为：**在用户事实不完整的情况下，如何利用规则结构与案例分化联合估计信息价值，通过有限轮追问最大程度缩小可能法律解释集合。**

---

## 1. 研究问题与系统边界

### 1.1 输入与输出

输入为用户初始描述：

\[
q_0=\{u_1,u_2,\ldots,u_n\}
\]

其中通常只包含部分生活事实，不具备完整的法律要素、请求权结构或证据说明。

系统通过多轮交互获得：

\[
q_t=q_0\cup\{(CQ_1,a_1),\ldots,(CQ_t,a_t)\}
\]

最终输出不是单一判决预测值，而是法律解释集合：

\[
E_t=\{Claim,Rule,ConditionState,Reasoning,Consequence,CaseSupport,Citation,Uncertainty\}
\]

输出必须包含：

1. 可能的法律关系与请求权；
2. 可能适用的规则及其效力版本；
3. 规则条件的满足、未满足、未知或冲突状态；
4. 当前事实支持的主要解释；
5. 仍然成立的条件化解释分支；
6. 支持案例、限制性案例与关键差异；
7. 需要补充或保存的证据；
8. 可回放到法条、司法解释和裁判文书原文的引用。

### 1.2 核心研究问题

- **RQ1：规则建模。** 如何将跨法条、司法解释和裁判规则转化为可计算的条件—例外—后果结构？
- **RQ2：跨层投影。** 如何把历史案例和用户事实统一投影到同一规则条件空间？
- **RQ3：高价值追问。** 如何利用规则重要性与正反案例分化，选择最可能改变最终解释的追问？
- **RQ4：最终解释。** 如何在事实仍不完全时，生成可溯源、条件化且不夸大确定性的民事法律解释？

### 1.3 明确排除

第一阶段不把以下能力作为核心目标：

- 胜诉率或赔偿金额概率预测；
- 模拟法官作出自动裁判；
- 由多个大模型角色模拟法庭辩论；
- 端到端训练大型法律模型；
- 仅凭语义相似度给出“案例相似率”；
- 将模型生成的推理链当成法院真实理由。

系统可以给出“在当前事实下更支持何种解释”，但必须说明条件、依据和不确定性，不能把历史案例统计直接表述为用户案件结果。

---

## 2. 与相关工作的重合边界

### 2.1 LegalGraphRAG 已经覆盖的内容

[LegalGraphRAG 论文文本](</C:/Users/37948/.codex/attachments/0268cd0f-88b6-41f8-88dd-c4e75425ebc6/pasted-text.txt:69>)已经提出：

- 事实图、本体图、规则图构成的层级法律图；
- 法条与司法解释关联；
- 将法条拆解为诊断检查清单；
- 对查询事实逐项检查规则条件；
- Researcher—Auditor—Adjudicator 多智能体流程；
- 输出带法条依据的罪名、法条和刑期等裁判结果。

因此，以下表述不能再作为 CasePath 的独立创新：

- “第一次构建规则层”；
- “第一次把法条拆成条件”；
- “第一次联合检索法条和案例”；
- “第一次通过追溯原文提高可靠性”。

LegalGraphRAG 的公开语料主要是刑事数据：14,049 个案例、452 条法条和 656 份司法解释，因此也不能直接作为民法系统的主要案例库。[官方仓库](https://github.com/XMUDeepLIT/LegalGraphRAG)提供的是重要基线和工程参考，而不是本项目的民事主数据。

### 2.2 其他已存在的重合工作

- [LeClari](https://doi.org/10.1145/3583780.3614953)已经使用法律事件模式生成面向案例检索的澄清问题；
- [Intelligent Legal Assistant](https://arxiv.org/abs/2502.07904)已经实现“信息缺失检测—澄清问题与选项—补全后生成法律回答”；
- [NS-LCR](https://aclanthology.org/2024.lrec-main.939/)已经使用案例级和法条级逻辑规则解释法律案例检索；
- 法律案例推理领域长期存在基于因素的案例比较与对比解释方法。

因此，“主动追问”“案例对比”“规则逻辑解释”分别都不是空白研究点。

### 2.3 CasePath 的贡献候选

CasePath 的方法贡献应严格表述为：

> 构建规则条件与案例适用结果之间的跨层投影，以同一规则下正向、反向和边界案例的分化程度估计未知事实的解释影响，选择能够最大程度缩小法律解释集合的澄清问题，并在有限轮交互后生成带条件、案例和原文依据的最终解释。

该贡献包含三个不可拆开的组成部分：

1. **Rule-conditioned case projection**：查询案和历史案例都投影到同一规则条件空间；
2. **Case-contrastive question utility**：追问价值由规则结构和案例分化共同决定；
3. **Explanation-space reduction**：追问优化目标是缩小最终解释集合，而不是单纯补全文本或提高检索召回。

在完成系统性文献检索和实验前，应称为“贡献候选”，不应宣称绝对首创。

---

## 3. 两层层级知识构建

总知识图定义为：

\[
\mathcal G=\mathcal G_{rule}\cup\mathcal G_{case}\cup\mathcal E_{bridge}
\]

其中：

- \(\mathcal G_{rule}\)：规范规则层；
- \(\mathcal G_{case}\)：案例适用层；
- \(\mathcal E_{bridge}\)：规则条件与具体裁判事实之间的跨层关系。

用户会话和来源溯源属于运行数据与保障机制，不构成第三、第四知识层。

### 3.1 第一层：规范规则层

规范规则层回答“法律要求什么”，不能只保存法条文本。

#### 3.1.1 节点类型

| 节点 | 作用 | 核心字段 |
|---|---|---|
| `LegalSource` | 法律、司法解释、规范性文件 | source_id、title、authority、valid_from、valid_to |
| `ProvisionVersion` | 某一时点有效的具体条文 | article_no、text、effective_status、jurisdiction |
| `LegalRule` | 可独立适用的规则命题 | rule_id、rule_type、scope、maturity_level |
| `RuleCondition` | 成立所需条件 | condition_id、predicate、necessity、operator |
| `ExceptionCondition` | 阻却、排除或限制条件 | exception_id、predicate、effect |
| `LegalConsequence` | 解除、返还、赔偿等后果 | consequence_type、object、calculation |
| `BurdenRule` | 举证责任分配 | bearer、object、standard |
| `EvidenceRequirement` | 可支持条件的证据类型 | evidence_type、strength、availability |

#### 3.1.2 关系类型

```text
(LegalSource)-[:CONTAINS]->(ProvisionVersion)
(ProvisionVersion)-[:EXPRESSES]->(LegalRule)
(LegalRule)-[:REQUIRES]->(RuleCondition)
(LegalRule)-[:HAS_EXCEPTION]->(ExceptionCondition)
(LegalRule)-[:LEADS_TO]->(LegalConsequence)
(LegalRule)-[:ALLOCATES_BURDEN]->(BurdenRule)
(RuleCondition)-[:MAY_BE_PROVED_BY]->(EvidenceRequirement)
(LegalRule)-[:DEPENDS_ON]->(LegalRule)
(ProvisionVersion)-[:AMENDS|REPLACES]->(ProvisionVersion)
```

#### 3.1.3 规则逻辑表达

一条规则不能默认表示为所有条件的简单合取。需要支持：

```text
ALL(C1, C2, ANY(C3a, C3b), UNLESS(E1)) -> Consequence R1
```

规则定义至少包含：

- `ALL`：全部满足；
- `ANY`：至少一个满足；
- `UNLESS`：例外成立则阻却；
- `THRESHOLD`：达到数量或金额阈值；
- `TEMPORAL`：先后关系、期间、时效；
- `REFERENCE`：依赖另一规则的成立。

一个法条可以表达多条规则，一条规则也可以由多个法条和司法解释共同确定。

### 3.2 第二层：案例适用层

案例适用层回答“法院在具体案件中如何认定和适用”。建模单位以请求权为中心，而不是以整案单一标签为中心。

#### 3.2.1 节点类型

| 节点 | 作用 | 核心字段 |
|---|---|---|
| `Case` | 案例元数据 | case_id、case_no、court、date、cause、procedure |
| `Claim` | 当事人的一项具体请求 | claim_type、claimant、respondent、amount |
| `AllegedFact` | 当事人主张但未必被认定的事实 | text、party、polarity |
| `EvidenceItem` | 当事人提供的证据 | evidence_type、submitted_by、admissibility |
| `CourtFinding` | 法院认定的事实 | predicate、polarity、confidence |
| `ConditionFinding` | 法院事实对某规则条件的状态 | status、basis、review_status |
| `ReasoningStep` | 文书明确表达的推理步骤 | premise、inference、conclusion |
| `DecisionItem` | 对一项请求的分项处理 | granted、partially_granted、rejected、amount |

#### 3.2.2 案例内部关系

```text
(Case)-[:HAS_CLAIM]->(Claim)
(Claim)-[:ALLEGES]->(AllegedFact)
(EvidenceItem)-[:SUPPORTS|CONTRADICTS]->(AllegedFact)
(CourtFinding)-[:SUPPORTED_BY]->(EvidenceItem)
(Claim)-[:HAS_FINDING]->(CourtFinding)
(ReasoningStep)-[:USES_FINDING]->(CourtFinding)
(ReasoningStep)-[:JUSTIFIES]->(DecisionItem)
(Claim)-[:RESOLVED_BY]->(DecisionItem)
```

必须区分：

- `AllegedFact` 与 `CourtFinding`；
- `submitted`、`admitted`、`accepted`、`rejected` 等证据状态；
- 支持、部分支持和驳回的不同 `DecisionItem`；
- 法院原文理由与模型推测理由。

### 3.3 两层桥接关系

```text
(Claim)-[:INVOKES]->(LegalRule)
(CourtFinding)-[:SATISFIES]->(RuleCondition)
(CourtFinding)-[:NOT_SATISFY]->(RuleCondition)
(ConditionFinding)-[:INSTANTIATES]->(RuleCondition)
(ReasoningStep)-[:APPLIES|DISTINGUISHES]->(LegalRule)
(DecisionItem)-[:REALIZES]->(LegalConsequence)
```

桥接关系是整个框架的计算核心。每条桥接边必须附带：

```json
{
  "source_span_id": "span_xxx",
  "status": "SATISFIED",
  "confidence": 0.91,
  "extractor_version": "casepath-extractor-v1",
  "review_status": "human_verified"
}
```

### 3.4 用户查询的动态投影

运行时生成临时查询图 \(\mathcal G_q^t\)，但不把它作为离线知识层：

```text
QueryMatter
 ├─ CandidateClaim
 ├─ UserFact
 ├─ UserEvidence
 ├─ UnknownCondition
 ├─ ClarificationQuestion
 └─ UserAnswer
```

规则条件状态采用五值语义：

```text
SATISFIED
NOT_SATISFIED
UNKNOWN
CONFLICTING
NOT_APPLICABLE
```

用户未提及某事实时必须标记为 `UNKNOWN`，不能推断为 `NOT_SATISFIED`。

### 3.5 溯源不是独立知识层

每个可展示节点和关系必须指向 `SourceSpan`：

```text
source_id + section + paragraph_id + start_offset + end_offset
```

Neo4j、向量库和关键词索引都是派生视图。规范化 JSONL/Parquet 与数据清单是唯一事实来源，保证可以重建全部索引。

---

## 4. 离线知识构建流程

### 4.1 总流程

```text
数据登记
  → 原文固化与哈希
  → 文档类型识别
  → 法律版本规范化
  → 章节切分
  → 规则/案例结构抽取
  → 两层桥接
  → 约束校验
  → 人工抽检或复核
  → 规范化数据发布
  → 图索引、稀疏索引、向量索引构建
```

### 4.2 规则层构建

#### 阶段 R1：法源规范化

1. 为法律、司法解释和规范性文件建立唯一 `source_id`；
2. 保存发布机关、公布日期、生效日期、失效日期和效力层级；
3. 同一条文的不同版本分别建模；
4. 统一中文、阿拉伯数字条号，消除“第一千二百五十七条”和“1257条”重复；
5. 建立条、款、项、目层级。

#### 阶段 R2：候选规则抽取

抽取器输出受约束 JSON：

```json
{
  "rule_id": "rule_contract_termination_xxx",
  "claim_type": "解除合同并返还预付款",
  "conditions": [],
  "exceptions": [],
  "consequences": [],
  "burden_rules": [],
  "source_spans": [],
  "confidence": 0.0
}
```

#### 阶段 R3：规则一致性验证

- 每个规则必须至少有一个有效法源；
- 每个条件必须有原文或权威解释依据；
- 逻辑表达必须通过模式校验；
- 不允许模型补写不存在的期限、金额或例外；
- 司法解释和法条冲突时不得自动合并；
- 低置信度规则只能进入实验区，不能作为确定性回答依据。

### 4.3 案例层构建

#### 阶段 C1：文书分区

优先按原文标题和段落标记切分：

```text
基本案情 / 诉讼请求 / 答辩意见 / 证据与质证
/ 法院查明 / 法院认为 / 裁判结果
```

禁止把“基本案情中出现的判决摘要”当作用户事实，也禁止用裁判结果反向污染检索特征。

#### 阶段 C2：请求权中心抽取

对每项 `Claim` 分别抽取：

- 请求内容；
- 法律依据；
- 主张事实；
- 对方抗辩；
- 争议条件；
- 证据；
- 法院认定；
- 理由；
- 分项结果。

#### 阶段 C3：规则条件投影

只有在裁判原文提供依据时，才建立：

\[
Status(case_i,condition_j)\in
\{S,N,U,C,NA\}
\]

其中分别表示满足、不满足、未知、冲突和不适用。

#### 阶段 C4：质量检查

- `DecisionItem` 与 `Claim` 一一对应或显式说明合并处理；
- `CourtFinding` 必须有原文跨度；
- 所有展示用桥接边必须可回放；
- 不使用裁判结果作为查询案初始检索特征；
- 训练、开发和测试集按案件来源或时间隔离，避免相同案件变体泄漏。

### 4.4 全民法覆盖的成熟度分级

| 等级 | 覆盖能力 | 可执行能力 |
|---|---|---|
| `L0` | 全部民法典法条和基本元数据 | 法条检索、原文引用 |
| `L1` | 机器抽取的候选规则结构 | 带低置信度的规则导航，不执行确定性条件判断 |
| `L2` | 规则与代表案例经过抽检 | 条件投影、案例比较、有限追问 |
| `L3` | 规则、案例和桥接关系经过专家复核 | 完整追问与最终解释实验 |

这样可以在架构上覆盖整个民法，同时诚实表达不同领域的数据成熟度。系统必须根据规则成熟度选择能力，不得用 `L1` 冒充 `L3`。

---

## 5. 在线交互式解释流程

### 5.1 初始解析与候选请求权生成

从 \(q_0\) 抽取：

```text
主体、行为、标的、时间、金额、状态、用户目标、已有证据
```

生成候选解释假设：

\[
h=(claim,rule,consequence)
\]

并保留 Top-\(m\) 假设集合 \(H_t\)，不能在第一轮过早锁定单一案由。

### 5.2 规则检索

规则检索采用分层约束：

1. 请求权和法律关系召回；
2. 稀疏关键词与稠密语义混合检索；
3. 法律效力、时间和管辖过滤；
4. 规则依赖图扩展；
5. 规则成熟度和来源权威性重排。

建议以 Reciprocal Rank Fusion 合并 BM25 与向量排序：

\[
RRF(d)=\sum_{r\in\mathcal R}\frac{1}{k+rank_r(d)}
\]

权重与 \(k\) 通过开发集确定，不在代码中写死结论性比例。

### 5.3 案例检索

案例检索分两级：

1. **广召回**：基于事实文本、案由、请求权、规则引用检索 Top-N；
2. **结构重排**：只使用查询已经提供的事实、证据描述和条件状态进行重排。

禁止将候选案例的裁判结果与查询进行匹配，因为用户查询尚不存在真实裁判结果，会造成结果泄漏。

### 5.4 规则条件投影矩阵

对候选规则 \(r\) 构造：

\[
M^r_{ij}=Status(case_i,condition_j)
\]

并加入用户当前状态向量：

\[
v_q^r=[s_1,s_2,\ldots,s_k]
\]

案例集合不只取最相似案例，而是组织为：

- `SupportSet`：相同请求或法律后果获得支持；
- `LimitSet`：事实相近但未获得相同支持；
- `BoundarySet`：仅少数关键条件不同；
- `UncertainSet`：文书不足以确定条件状态。

### 5.5 分歧条件发现

对条件 \(c_j\) 计算案例分化程度：

\[
D(c_j)=I(Status(c_j);DecisionItem\mid Rule,Claim)
\]

其中 \(I\) 为条件状态与分项裁判结果之间的互信息。样本不足时，不报告统计因果关系，只使用经过规则约束的差异计数和专家标注。

“第一处分歧”只表示案例路径中最早观察到的结构差异，不自动等于裁判结果的因果原因。只有裁判理由明确连接该事实、规则和结果时，才能表述为法院采用的理由。

### 5.6 高价值追问算法

对所有未知条件 \(c\in U_t\) 计算：

\[
Utility(c)=
\alpha Gain(c)+
\beta Contrast(c)+
\gamma Centrality(c)+
\delta Answerability(c)+
\epsilon Evidenceability(c)-
\lambda Cost(c)-
\mu Risk(c)
\]

各项作用如下：

| 项 | 定义 | 作用 |
|---|---|---|
| `Gain` | 回答后候选解释集合的期望熵下降 | 直接优化最终解释确定性 |
| `Contrast` | 正反案例在该条件上的分化程度 | 找到实际裁判中的边界事实 |
| `Centrality` | 条件在规则逻辑中的必要性、层级与依赖中心性 | 优先询问法律上关键条件 |
| `Answerability` | 普通用户能否理解并回答 | 避免询问专业法律结论 |
| `Evidenceability` | 是否存在可提交、可保存的证据 | 将追问连接到实际行动 |
| `Cost` | 理解、回忆和输入成本 | 控制交互负担 |
| `Risk` | 隐私、诱导、歧义风险 | 避免不必要的敏感询问 |

解释空间信息增益定义为：

\[
Gain(c)=H(P(H_t))-\sum_{a\in A_c}P(a\mid c)H(P(H_t\mid a))
\]

第一阶段可采用可解释的加权规则实现；获得足够标注后再学习参数。不得使用语言模型自评作为唯一追问价值分数。

### 5.7 追问生成约束

问题必须从已选择的 `RuleCondition` 生成，而不是让模型自由寻找“还缺什么”。每个问题输出：

```json
{
  "condition_id": "cond_xxx",
  "question": "健身房是永久停止经营，还是暂时停业？",
  "why_asked": "该事实会影响是否属于无法继续履行",
  "options": ["永久停止经营", "暂时停业", "仍在其他门店履行", "不清楚"],
  "supporting_case_ids": ["case_a"],
  "limiting_case_ids": ["case_b"]
}
```

问题应使用生活语言，不能直接询问“是否构成根本违约”“是否满足法定解除权”等需要用户作法律判断的问题。

### 5.8 停止策略

满足以下任一条件即停止追问：

- 主要解释分支的后验状态稳定；
- 剩余问题的信息增益低于阈值；
- 剩余问题用户无法合理回答；
- 达到最大轮数；
- 用户选择立即查看当前解释。

停止不等于信息完整。未解决条件必须出现在最终解释中。

### 5.9 最终解释生成

生成器不直接读取杂乱检索文本，而是读取受约束的 `ExplanationPlan`：

```json
{
  "candidate_claims": [],
  "applicable_rules": [],
  "condition_states": [],
  "support_cases": [],
  "limiting_cases": [],
  "unresolved_branches": [],
  "evidence_actions": [],
  "citations": []
}
```

最终回答固定包含：

1. 当前问题可能涉及的法律关系和请求；
2. 已知事实下的主要解释；
3. 规则条件说明；
4. 正反案例及关键差异；
5. 仍可能改变解释的未知事实；
6. 证据和后续行动提示；
7. 法条与裁判原文引用；
8. 非法律意见及信息完整性提示。

---

## 6. 核心算法伪代码

```text
Algorithm: Rule-Case Contrastive Interactive Explanation
Input:
    initial user query q0
    rule graph G_rule
    case graph G_case
    maximum turns T
Output:
    grounded legal explanation E

1:  q <- Normalize(q0)
2:  H <- GenerateCandidateClaims(q)
3:  R <- RetrieveRules(q, H, G_rule)
4:  Vq <- ProjectQueryToConditions(q, R)

5:  for t = 1 ... T do
6:      C <- HybridRetrieveCases(q, H, R, G_case)
7:      C <- StructuralRerank(C, Vq)
8:      P <- BuildContrastiveCasePanel(C, R)
9:      U <- FindUnknownConditions(Vq)
10:     for each condition c in U do
11:         score[c] <- ExplanationGain(c, H, R)
12:                    + CaseContrast(c, P)
13:                    + RuleCentrality(c, R)
14:                    + Answerability(c)
15:                    + Evidenceability(c)
16:                    - InteractionCost(c)
17:                    - QuestionRisk(c)
18:     end for
19:     c* <- argmax score[c]
20:     if Stop(score[c*], H, t) then break
21:     CQ <- GenerateQuestionFromCondition(c*, P)
22:     a <- AskUser(CQ)
23:     q, Vq, H <- UpdateState(q, Vq, H, c*, a)
24: end for

25: Plan <- BuildExplanationPlan(q, H, R, P, Vq)
26: Plan <- VerifyEveryClaimAndCitation(Plan)
27: E <- ConstrainedGenerate(Plan)
28: return E
```

---

## 7. 数据来源与使用策略

### 7.1 本地 `legal-rag` 的准确定位

用户新增的 [legal-rag 仓库](https://github.com/litunan/legal-rag)确实以《民法典》和民事案例为主，但应区分于 ACL 论文的 LegalGraphRAG。本地默认将其与 `CasePath/` 同级克隆，以下本地路径均从 `CasePath/` 仓库根目录起算。

已核查数据：

- `../legal-rag/data/laws/民法典_统计.json` 包含 1,260 条法条；
- `../legal-rag/data/processed/processed_cases.json` 共 51 件；
- 其中民事 45 件、刑事 3 件、行政 1 件、执行 1 件、执行实施 1 件；
- 参考案例 48 件、指导性案例 3 件；
- 只有 11 件单独抽取了 `judgment_result`；
- 处理统计显示 52 个输入中成功 51 个、失败 1 个，并抽取 195 条法条引用；
- 部分 `parties` 字段混入整句事实，部分裁判结果位于 `case_facts` 而未进入 `judgment_result`；
- 当前图主要是“案例—法条—案由—法院—关键词”引用和元数据图，不是本方案所需的规则条件—案例适用图。

结论：

> 本地 `legal-rag` 适合作为民法法条种子库、首批高质量案例、已有模型与数据库适配器的代码底座；不适合作为全域民事检索主库，也不能不经清洗直接成为实验金标准。

在未确认许可证、原始抓取来源和可再分发条件前，只可用于本地研究验证，不能默认随项目公开发布其全部数据。

### 7.2 法律规则数据

优先级：

1. [国家法律法规数据库](https://flk.npc.gov.cn/search)：法律原文、版本和效力信息；
2. 最高人民法院公开司法解释和规范性文件；
3. 人民法院案例库中的裁判要旨和关联索引；
4. 本地 `legal-rag` 已整理的民法典文件，用于启动和格式转换。

每条规则必须建立 `source_manifest`，至少记录来源、获取时间、版本、哈希和使用限制。

### 7.3 案例数据

| 数据源 | 主要用途 | 限制 |
|---|---|---|
| 本地 `legal-rag` 45 件民事案例 | 种子案例、管线调试、人工建图样例 | 数量小、案由分散、字段需修复 |
| [人民法院案例库](https://www.court.gov.cn/zixun/xiangqing/431662.html)公开指导性/参考案例 | 高权威规则解释和深度案例图 | 应遵守公开查询和使用规则；不假设存在批量 API |
| [C3RD](https://github.com/YeFD/MileCut) | 大规模民事类案检索与重排评估 | 标签含启发式成分，不能直接当作规则条件真值 |
| [MUSER](https://github.com/THUlawtech/MUSER) | 民间借贷细粒度事实与争点评估 | 领域单一，但适合深度投影实验 |
| [CAIL2019-SCM](https://github.com/china-ai-law-challenge/CAIL2019/tree/master/scm) | 相似案例基线和三元组比较 | 主要集中于民间借贷、事实字段较简化 |
| [LexChain](https://github.com/thunlp/LexChain) | 侵权责任的结构化链条参考 | 需再次核验获取和使用条件 |

LegalGraphRAG 的刑事案例数据不进入民事主库，只用于复现对比方法或验证通用组件。

### 7.4 三种数据集角色

- **Broad Corpus**：大规模召回库，允许轻量字段和弱标注；
- **Deep Corpus**：规则条件、裁判事实、理由和结果完整的深度案例库；
- **Gold Evaluation Set**：由法律专业人员复核的查询—规则—条件—追问—解释测试集。

不得把弱标签数据同时用作训练和“专家金标准”。

---

## 8. 代码架构与现有仓库改造

### 8.1 目标目录

```text
casepath/
├─ configs/
├─ data/
│  ├─ raw/
│  ├─ canonical/
│  ├─ manifests/
│  └─ eval/
├─ src/casepath/
│  ├─ schemas/
│  ├─ ingestion/
│  ├─ rule_layer/
│  ├─ case_layer/
│  ├─ linking/
│  ├─ retrieval/
│  ├─ projection/
│  ├─ clarification/
│  ├─ explanation/
│  ├─ verification/
│  ├─ api/
│  └─ ui/
├─ tests/
├─ scripts/
└─ docs/
```

### 8.2 现有代码的处理方式

| 现有模块 | 决策 | 修改方式 |
|---|---|---|
| `case_models.py` | 部分复用 | 保留 Claim、Evidence、Citation 思路；合并重复状态模型，改为统一 Pydantic 模式，增加 SourceSpan、CourtFinding、DecisionItem |
| `ontology/legal_ontology.py` | 主要重写 | 当前是案例—法条元数据图；替换为两层本体与桥接关系 |
| `data_processing/legal_data_processor.py` | 改造 | 修复分区泄漏、当事人污染、裁判结果缺失、法条编号重复，增加原文偏移和版本信息 |
| `rag_modules/milvus_legal_index.py` | 适配复用 | 将规则、案例事实、裁判理由分集合索引；增加稀疏检索融合接口 |
| `rag_modules/graph_legal_retrieval.py` | 重写查询 | 从共同法条邻接检索改为规则条件投影和案例对照检索 |
| `workflows/`、`agents/` | 移出关键路径 | 第一阶段采用确定性状态机；多智能体不是创新点，也不应增加 46 秒级在线链路开销 |
| `evaluation/` | 扩展 | 新增追问价值、解释空间缩减、条件投影与引用支持度评价 |
| `frontend/` | 改造 | 增加条件状态、追问原因、正反案例和原文证据面板 |

### 8.3 服务划分

```text
IngestionService       原文导入、规范化、版本和哈希
RuleBuildService       规则条件抽取与校验
CaseBuildService       请求权中心案例结构化
LinkingService         两层桥接
HybridRetrievalService 规则与案例混合召回
ProjectionService      查询和案例条件投影
QuestionPolicyService  追问价值计算与停止策略
ExplanationService     解释计划与受约束生成
CitationVerifier       引用与主张逐项核验
```

初期可以部署为单体 FastAPI 应用，代码内部保持模块边界，不要提前拆成微服务。

### 8.4 状态机

```text
PARSE_QUERY
 → RETRIEVE_RULES
 → PROJECT_QUERY
 → RETRIEVE_CASES
 → BUILD_CONTRAST_PANEL
 → SCORE_QUESTIONS
 → ASK_OR_STOP
 → UPDATE_STATE
 → BUILD_EXPLANATION_PLAN
 → VERIFY_CITATIONS
 → GENERATE_FINAL_EXPLANATION
```

每个状态都输入、输出受约束对象，避免用自然语言消息在模块之间传递核心状态。

---

## 9. 实验设计

### 9.1 任务一：规则与条件检索

输入用户事实，评估候选请求权、规则和关键条件的召回。

指标：

- Recall@K、MRR、nDCG@K；
- 规则条件抽取 Precision、Recall、F1；
- 有效法版本选择准确率；
- 条件逻辑树完全匹配率与部分匹配率。

基线：

- BM25；
- Dense Retrieval；
- BM25 + Dense；
- 仅层级规则检索；
- 两层桥接检索。

### 9.2 任务二：条件投影与案例对比

指标：

- `SATISFIED/NOT_SATISFIED/UNKNOWN/...` 五分类 Macro-F1；
- 条件证据跨度定位 F1；
- 正向、反向、边界案例选择 nDCG；
- 分歧条件 Top-1/Top-3 Accuracy；
- 路径解释忠实度。

### 9.3 任务三：追问策略

构建“遮蔽关键事实”评估：从完整案例或专家问答中遮蔽一个或多个关键条件，系统通过追问恢复信息。

指标：

- `Key Question Hit@1/3`：是否优先问到专家标注的关键问题；
- `Explanation Set Reduction`：追问前后可行解释集合缩减比例；
- `Rule Uncertainty Reduction`：规则适用不确定性下降；
- `Retrieval Improvement`：用户回答后相关案例排名提升；
- `Turns to Stability`：解释稳定所需轮数；
- `Question Answerability`：普通用户可回答性；
- `Redundancy Rate`：重复、无新增信息问题比例。

### 9.4 任务四：最终解释

由法律专业评审人员评价：

- 法律规则正确性；
- 事实—规则对应正确性；
- 引用支持度；
- 条件化表达完整性；
- 是否误把未知事实当成已知；
- 是否超出材料作出确定性结论；
- 普通用户可理解性；
- 后续证据提示是否可执行。

### 9.5 追问基线

1. 不追问，直接回答；
2. 通用大模型自由追问；
3. 仅根据缺失字段追问；
4. 仅根据规则必要条件追问；
5. 仅根据案例分化追问；
6. CasePath 完整方法：规则 + 案例分化 + 解释信息增益；
7. 能够复现时，加入 LeClari 与 Intelligent Legal Assistant 风格方法。

### 9.6 消融实验

```text
w/o Rule Centrality
w/o Positive/Negative Case Contrast
w/o Explanation Gain
w/o Answerability
w/o Evidenceability
w/o Source Verification
w/o Multi-turn State Update
```

如果完整方法只改善案例召回，却不能改善最终解释正确性或减少交互轮次，则核心假设不成立，应收缩或重新定义贡献。

---

## 10. 分阶段执行计划

### 10.1 阶段 A：5 人一周可运行原型

按 5 个完整工作日、2 个缓冲日计算，总资源约为 25 人日。该阶段只能完成一条质量可控的纵向链路，不能完成全民法深度建图和论文级全量实验。

#### 10.1.1 本周范围

必须完成：

- 民法典 1,260 条法条的 L0 导入、编号规范化和基础检索；
- 两层图谱的通用 schema 和 Neo4j 约束；
- 合同解除与返还场景的 3—5 条 L3 规则；
- 8—12 件同一规则族的深度结构化案例；
- BM25 + Dense 的规则、案例召回；
- 用户事实到规则条件的投影；
- 一套可解释的追问价值公式；
- 至少一轮“追问—回答—状态更新—重新解释”；
- 最终 ExplanationPlan、原文引用和前端演示。

延期处理：

- 全部民法规则的 L2/L3 结构化；
- 100—300 件深度案例库；
- 参数学习、模型微调和复杂多智能体；
- 大规模人工用户实验；
- 论文级显著性检验；
- 生产级权限、监控和高可用部署。

首个演示场景固定为“服务合同履行地点变化、无法继续履行与解除返还”。本地数据已有“曾某诉武汉某健身管理有限公司服务合同纠纷案”，关联《民法典》第 509、563、566 条，可以直接作为支持案例种子。另选 3—5 件限制或边界案例，必须保留真实来源，不能为演示虚构裁判结果。

#### 10.1.2 五人职责

| 人员 | 主责 | 本周必须交付 | 不负责 |
|---|---|---|---|
| P1 架构与集成 | schema、状态机、FastAPI、配置、最终合并 | 五个接口对象、端到端 API、Docker/启动脚本 | 大量手工标注 |
| P2 规则层 | 民法典规范化、规则条件、版本和来源 | 1,260 条 L0；3—5 条 L3 规则；法条 SourceSpan | 前端和案例检索 |
| P3 案例层 | 案例清洗、Claim 中心抽取、图导入 | 45 件民事轻量数据；8—12 件 L3 案例；桥接边 | 最终回答生成 |
| P4 检索与追问算法 | 混合检索、条件投影、对比集、追问评分 | RuleRetriever、CaseRetriever、Projection、QuestionPolicy | 文书人工校正 |
| P5 前端与评测 | 交互页面、固定测试集、演示和错误记录 | 输入/追问/条件矩阵/证据面板；20 条测试；演示脚本 | 修改底层 schema |

P1 是唯一集成负责人；P2—P5 按冻结接口并行开发。任何 schema 修改先由 P1 更新版本，再由其他成员同步，禁止每个模块自定义字段。

#### 10.1.3 第一天必须冻结的接口

```text
RuleRecord.json
CaseRecord.json
QueryState.json
RetrievalBundle.json
ExplanationPlan.json
```

最小字段：

```text
RuleRecord:
  rule_id, source_ids, conditions, exceptions, consequences, maturity

CaseRecord:
  case_id, claims, findings, condition_findings, reasoning, decisions, spans

QueryState:
  session_id, user_facts, candidate_claims, condition_states, history

RetrievalBundle:
  rules, support_cases, limiting_cases, boundary_cases, cited_spans

ExplanationPlan:
  main_explanation, conditional_branches, unresolved_conditions,
  evidence_actions, citations
```

#### 10.1.4 按天执行

| 时间 | 全队目标 | 并行任务 | 当日完成定义 |
|---|---|---|---|
| D1 | 冻结架构和样例 | P1 定 schema/API；P2 定规则模式；P3 定案例模式；P4 定检索接口；P5 画页面和测试用例 | 五个 JSON 示例可通过 Schema 校验；示例数据能从规则层连接到案例层 |
| D2 | 产生可用数据 | P2 导入 1,260 条法条并手工校正演示规则；P3 清洗案例并完成 4—6 件深度标注；P4 建稀疏/向量索引；P5 完成静态页面；P1 建 API 骨架 | 法条可搜；至少一条规则和两个案例可回放原文；前端可显示固定数据 |
| D3 | 打通无追问链路 | P3 补足 8—12 件案例；P4 完成规则/案例检索与条件矩阵；P1 接入 QueryState；P5 建自动测试 | 用户输入能返回候选规则、正反案例和条件矩阵；不要求回答自然语言完善 |
| D4 | 实现核心创新 | P4 完成追问评分和停止策略；P1 完成状态更新；P5 接入追问交互；P2/P3 修复错误数据 | 系统能够选择一个有依据的关键问题；用户回答后条件状态和解释分支发生可见更新 |
| D5 | 完成最终解释 | P1 接 ExplanationPlan 和引用验证；P5 完成页面与演示；P2/P3/P4 完成测试和消融 | 一条端到端场景稳定运行；所有实质性结论有 citation_id；20 条固定测试执行完毕 |
| D6 | 集成缓冲 | 只修 P0 缺陷，不新增功能 | 清除阻断演示的问题，冻结演示数据和依赖版本 |
| D7 | 彩排与交付 | 双机启动测试、演示彩排、README、结果记录 | 新环境按 README 可启动；主演示连续运行 3 次；准备失败回退截图或录屏 |

#### 10.1.5 每日集成规则

- 每天中午和下班前各合并一次，禁止最后一天集中集成；
- 每个模块至少提供一个成功样例和一个失败样例；
- 大模型输出必须经过 Pydantic/JSON Schema 校验；
- 演示数据 D5 后冻结，不再临时替换；
- 任何未经原文支持的案例关系不得进入演示；
- 检索或图数据库失败时，系统必须返回明确降级状态，而不是让 LLM 自己补答案。

#### 10.1.6 一周期末验收

本周成功标准不是“覆盖全部民法”，而是以下链路真实运行：

```text
用户描述
 → 候选请求权
 → 检索规则及条件
 → 检索正向/限制/边界案例
 → 生成条件投影矩阵
 → 选择高价值追问
 → 用户回答
 → 更新状态
 → 生成带法条、案例和原文依据的最终解释
```

同时满足：

- 民法典全量 L0 可检索；
- 至少 3 条规则达到 L3；
- 至少 8 件案例达到 L3；
- 至少 1 个场景完成完整交互；
- 至少 20 条测试样例有执行记录；
- 最终输出不存在无来源法条、案例或法院认定。

该阶段“覆盖整个民法”只代表 L0/L1 框架覆盖，不代表全部规则都已达到可可靠追问的 L3。

### 10.2 阶段 B：4—6 周研究原型

1. 引入 C3RD 构建大规模案例召回基线；
2. 使用 MUSER/SCM 构建细粒度事实和案例对比实验；
3. 扩充 100—300 件深度结构化民事案例；
4. 标注 200—500 个规则条件投影；
5. 构建不少于 200 条“事实遮蔽—关键追问—最终解释”样本；
6. 完成基线、消融、误差分析和用户可理解性评价；
7. 根据效果决定继续全域扩展，或收缩到预付式消费、租赁、民间借贷等单一领域。

### 10.3 阶段 C：论文实验与可发布系统

- 冻结数据切分和版本；
- 专业人员复核金标准；
- 完整可复现实验配置；
- 记录模型、提示、索引和数据版本；
- 隐私与数据许可审计；
- 论文方法、系统和局限性描述分离；
- 对外展示时隐藏或脱敏当事人信息。

---

## 11. 验收标准

### 11.1 数据与图谱

- 全部展示节点和边可回放到原文跨度；
- 民法典条号重复率为 0；
- 非民事案例不进入默认民事索引；
- 不存在无来源规则条件；
- 不存在悬空 Claim、ConditionFinding 或 DecisionItem；
- 数据清单可以重建 Neo4j 与检索索引。

### 11.2 在线系统

- 用户未提供事实不会被系统标成“不满足”；
- 每个追问都能解释其对应规则条件及正反案例依据；
- 用户回答后只更新受影响状态；
- 达到停止条件后可以稳定生成最终解释；
- 引用验证失败的主张不会进入最终回答；
- 没有 L2/L3 数据时明确回退为规则检索或一般信息说明。

### 11.3 实验

- 混合检索优于至少一个稀疏或稠密单独基线；
- 完整追问策略优于自由追问和规则缺失字段基线；
- 追问后最终解释的专家正确性或完整性显著提升；
- 报告交互轮数、失败案例和统计不确定性；
- 完成至少一次跨争议类型迁移实验，验证框架而非单一模板记忆。

---

## 12. 主要风险与控制

| 风险 | 表现 | 控制方式 |
|---|---|---|
| 与既有工作重合 | 只强调规则层或追问 | 把贡献固定为两层投影、案例对比信息增益与解释空间缩减 |
| 全民法规模失控 | 1,260 条法条均需人工拆解 | L0—L3 成熟度分级，先框架覆盖再验证重点规则 |
| 案例稀疏 | 同一规则下缺少正反案例 | 引入大型检索集；不足时不计算统计分化，只展示规则依据 |
| 相关不等于因果 | 首个分歧被误写为裁判原因 | 只有裁判理由显式支持时才使用因果性语言 |
| 用户事实被过度推断 | 未说明被当成否定 | 五值状态与原文跨度约束 |
| 法律版本错误 | 用现行法解释旧案 | `ProvisionVersion` 与裁判日期联合过滤 |
| 数据许可不清 | 无法公开完整语料 | 数据与代码分离发布，保留 manifest，逐源核验许可 |
| 生成幻觉 | 引用不存在或过度结论 | ExplanationPlan + CitationVerifier + 失败降级 |
| 追问负担高 | 用户放弃交互 | 最大轮数、回答成本和“立即查看当前解释”机制 |

---

## 13. 端到端示例

用户输入：

> 我在健身房充了 5000 元，现在店关门了，还有 3000 元没消费，老板不退款。

系统处理：

```text
CandidateClaim:
  解除服务合同并返还未消费预付款

CandidateRule:
  服务合同履行不能/违约解除/返还规则

Known Conditions:
  C1 服务合同关系成立          SATISFIED
  C2 消费者已经支付费用        SATISFIED
  C3 存在未履行服务余额        SATISFIED

Unknown Conditions:
  C4 商家是否永久停止经营      UNKNOWN
  C5 是否能由其他门店继续履行  UNKNOWN
  C6 合同是否约定退款或转店    UNKNOWN
```

正反案例投影：

| 条件 | 支持案例 A | 限制案例 B | 用户案 |
|---|---|---|---|
| 合同成立 | 满足 | 满足 | 满足 |
| 已付款 | 满足 | 满足 | 满足 |
| 无法继续履行 | 满足 | 不满足 | 未知 |
| 存在替代履行 | 不满足 | 满足 | 未知 |

系统计算后优先追问：

> 健身房是永久停业，还是暂时关闭？合同或老板是否安排你到其他门店继续使用？

用户回答后更新 C4、C5，重新计算可行解释。最终回答不是只说“你与案例 A 相似”，而是：

```text
根据你补充的“门店永久停业且没有替代门店”，当前事实更支持经营者已经无法继续履行服务。
在检索到的支持案例中，法院将停止经营、剩余服务无法提供作为支持解除和返还余额的重要事实；
限制性案例未支持相同请求的关键区别是经营者仍可继续提供约定服务。

因此，你目前可以重点考虑解除服务合同并请求返还未消费余额这一解释路径。
是否能够获得支持仍会受到合同条款、付款及余额凭证、停业事实和经营主体状态等证据影响。
```

随后提供法条、支持案例认定原文、限制案例认定原文和证据清单。

---

## 14. 对原四份方案的具体修改

### 14.1 系统定位

旧定位：

```text
找到类案 → 比较路径 → 解释差异
```

修改为：

```text
识别请求权 → 检索规则 → 规则条件化
→ 正反案例投影 → 高价值追问
→ 更新事实 → 生成最终法律解释
```

### 14.2 图谱结构

旧结构以“证据—事实—争议因素—理由—结果”单层案例路径为中心。

修改为：

- 上层：规范规则层；
- 下层：案例适用层；
- 核心：规则条件与裁判认定之间的桥接；
- 案例路径改为每项 Claim 的有向无环图，不强制所有案件只有一条线性路径。

### 14.3 算法模块

旧四模块：Hybrid Retrieval、Case Reasoning Graph、Path Alignment、Evidence-grounded RAG。

修改为五模块：

1. Two-level Legal Knowledge Construction；
2. Hybrid Rule-and-Case Retrieval；
3. Rule-conditioned Case Projection；
4. Case-contrastive Question Utility；
5. Verified Conditional Explanation Generation。

### 14.4 用户产品形态

案例列表和路径对比保留为解释证据，但不再是主流程终点。主界面应围绕：

```text
用户描述
→ 系统当前理解
→ 为什么询问这个问题
→ 用户补充
→ 当前法律解释
→ 规则、案例和原文证据展开
```

---

## 15. 最终技术路线

> CasePath 首先构建由规范规则层和案例适用层组成的两层层级法律知识图；随后将普通用户的不完整民事描述转换为候选请求权和规则条件状态，并联合检索同一规则下的正向、反向及边界案例；系统依据规则中心性、案例结果分化和解释空间信息增益，选择最可能改变最终法律解释的未知条件向用户追问；用户补充事实后，系统更新条件投影并生成包含主要解释、条件化分支、正反案例、证据提示和原文引用的可溯源法律回答。

一句话压缩：

> **用规则定义比较维度，用正反案例发现关键缺口，用高价值追问补足事实，最后生成有条件、有案例、有原文依据的民事法律解释。**

---

## 参考资料

- [LegalGraphRAG 官方仓库](https://github.com/XMUDeepLIT/LegalGraphRAG)
- [LegalGraphRAG，ACL 2026](https://aclanthology.org/2026.acl-long.1738/)
- [LeClari：面向法律案例检索的澄清问题](https://doi.org/10.1145/3583780.3614953)
- [Intelligent Legal Assistant](https://arxiv.org/abs/2502.07904)
- [NS-LCR：使用逻辑规则解释法律案例检索](https://aclanthology.org/2024.lrec-main.939/)
- [Legal Case Retrieval: A Survey of the State of the Art](https://aclanthology.org/2024.acl-long.350/)
- [国家法律法规数据库](https://flk.npc.gov.cn/search)
- [最高人民法院关于人民法院案例库建设运行的意见](https://www.court.gov.cn/zixun/xiangqing/431662.html)
- [C3RD / MileCut](https://github.com/YeFD/MileCut)
- [MUSER](https://github.com/THUlawtech/MUSER)
- [CAIL2019-SCM](https://github.com/china-ai-law-challenge/CAIL2019/tree/master/scm)
- [LexChain](https://github.com/thunlp/LexChain)
