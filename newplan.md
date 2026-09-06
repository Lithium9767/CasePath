现在合同 v1.1 已经相当于确定了统一的数据格式，所以 P2、P3、P4 可以同时开工，不必等彼此全部完成。

最核心的数据流是：

```
P2生产 RuleRecord
        ↓ condition_id（统一条件编号）
P3生产 CaseRecord，并把裁判事实映射到 condition_id
        ↓
P4读取 RuleRecord + CaseRecord + QueryState
        ↓
检索规则 → 检索案例 → 条件投影 → 选择追问
        ↓
输出 RetrievalBundle + QuestionCandidate + ExplanationPlan
```

## P2：开始建设规则层

P2现在可以做两类工作。

### 1. 导入1260条民法典法条

不要马上把1260条法条都人工拆成完整规则，先将原始法条导入：

- `LegalSourceRecord`：整部《民法典》的来源信息；
- `ProvisionRecord`：每一条法条的原文、条号、效力时间和原文定位。

也就是说：

```
民法典
  └─ 第509条
  └─ 第563条
  └─ 第566条
  └─……
```

### 2. 精细构建重点规则

先把第509、563、566条加工成 `RuleRecord`：

```
RuleRecord
├─ provisions：来自哪些法条
├─ claim_types：适用于什么请求
├─ conditions：有哪些原子条件
├─ condition_groups：条件之间如何组合
├─ exceptions：有哪些例外
├─ consequences：满足后产生什么法律后果
└─ source_spans：每个判断来自哪段原文
```

P2最重要的交付物不是图数据库本身，而是“统一条件词典”，例如：

```
cond.contract.exists
cond.payment.made
cond.performance.impossible
cond.termination.notice_given
cond.unused_fee.exists
```

这个词典必须尽早交给P3和P4。P3不能自己另外发明一套条件名称。

P2今日可交付：

- 1260条 `ProvisionRecord`；
- 509、563、566条对应的 `RuleRecord`；
- 第一版条件编号表；
- 规则、条件、法条之间的关系表；
- 通过合同校验的JSON数据。

------

## P3：开始建设案例层

P3可以开始清洗45件民事案例，并按照 `CaseRecord` 填写。

每个案例需要拆成：

```
CaseRecord
├─ claims：当事人提出了什么请求
├─ findings：法院认定了哪些事实
├─ condition_findings：这些事实满足哪些规则条件
├─ reasoning_steps：法院如何从事实和规则推到结论
├─ decisions：最终支持或驳回了什么
└─ source_spans：上述内容在裁判文书中的原文位置
```

关键工作是建立：

```
CourtFinding → RuleCondition
```

例如：

```
法院原文：
“被告经营场所已经关闭，无法继续提供健身服务”

CourtFinding：
predicate = "经营者已经停止营业"

映射：
condition_id = "cond.performance.impossible"
status = "SATISFIED"
```

另一个案例：

```
法院原文：
“健身房仍正常营业，原告因个人原因不再消费”

映射：
condition_id = "cond.performance.impossible"
status = "NOT_SATISFIED"
```

这样两个案例才真正投影到同一个比较维度。

P3今日可交付：

- 45件案例的原始材料目录；
- 案例来源与可用性清单；
- 健身房案例的完整 `CaseRecord`；
- 至少一个支持退款案例；
- 至少一个限制退款案例；
- 案例事实到P2条件编号的映射；
- 所有关键判断对应的真实原文跨度。

------

## P4：开始写核心算法

P4现在不需要等待P2、P3完成全部数据，因为已经有合同示例，可以先用示例数据开发。

P4的输入是：

```
RuleRecord
CaseRecord
QueryState
```

P4的输出是：

```
RetrievalBundle
QuestionCandidate
ExplanationPlan
```

### 第一部分：规则召回

根据用户输入提取候选请求：

```
用户：健身房关门了，不退剩余费用
        ↓
候选请求：解除服务合同、返还剩余预付款
        ↓
BM25检索相关 RuleRecord
```

先完成BM25即可，向量检索可以稍后增加。

### 第二部分：案例召回

根据这些信息检索案例：

- 用户事实；
- 候选请求；
- 命中的规则；
- 规则包含的条件。

案例检索不能只比较整篇文本相似度，还要提高“适用同一规则、涉及相同条件”的案例分数。

### 第三部分：条件投影

将三种信息全部映射到同一组 `condition_id`：

```
用户事实 ───────────────┐
支持案例的法院认定 ──────┼→ RuleCondition
限制案例的法院认定 ──────┘
```

形成矩阵：

| 条件             | 用户 | 支持案例 | 限制案例 |
| ---------------- | ---- | -------- | -------- |
| 合同成立         | 满足 | 满足     | 满足     |
| 已经付款         | 满足 | 满足     | 满足     |
| 商家无法继续履约 | 未知 | 满足     | 不满足   |
| 存在阻却退款例外 | 未知 | 不满足   | 不满足   |

### 第四部分：选择高价值追问

P4发现：

```
“商家是否确实无法继续履约”
```

在支持案例和限制案例中产生了明显分化，而用户没有说清楚，因此生成：

```
QuestionCandidate(
    condition_id="cond.performance.impossible",
    question="健身房是永久停业，还是暂时关闭后仍可能恢复营业？",
    why_asked="这一事实会影响合同是否能够继续履行以及是否支持解除退款。",
)
```

这就是 CasePath 核心算法的第一版。

### 第五部分：生成最终解释计划

收到用户回答后：

```
更新 QueryState
    ↓
重新判断条件
    ↓
必要时继续追问
    ↓
输出 ExplanationPlan
```

最终解释包括：

- 当前可能成立的法律请求；
- 对应规则；
- 已满足条件；
- 尚不确定条件；
- 支持案例；
- 限制或边界案例；
- 不同事实情况下的可能结论；
- 法条和案例原文证据；
- 用户下一步可以准备什么材料。

------

## 三个人现在如何配合

| 人员 | 主要生产内容                             | 必须提供给谁                 |
| ---- | ---------------------------------------- | ---------------------------- |
| P2   | 法条、规则、条件和条件组                 | 把条件编号提供给P3、P4       |
| P3   | 案例事实、裁判理由、条件映射和结果       | 把案例数据提供给P4           |
| P4   | 检索、条件投影、案例比较、追问和解释算法 | 把结果提供给P1工作流和P5前端 |

最重要的接口约定是：

```
P2定义 condition_id
P3使用 condition_id 标注案例
P4使用同一 condition_id 理解用户事实
```

如果三方都使用同一个条件编号，规则层和案例层就能真正连接起来。否则即使全部导入Neo4j，也只是两堆没有语义连接的数据。

因此，下一项最优先的共同任务是：P2先发布509、563、566条对应的第一版“条件编号表”，P3和P4当天即可据此分别开始案例标注和算法开发。