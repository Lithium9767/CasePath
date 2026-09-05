# CasePath公共合同变更记录

## v1.1（2026-09-04）

### 新增合同

- `LegalSourceRecord`：整部法律或其他规范性法律来源；
- `ProvisionRecord`：可检索、可引用的完整法条；
- `AnswerRequest`：用户对高价值追问的原始回答；
- `ErrorResponse`：正式API统一错误结构；
- `CapabilityStatus`：组件真实、演示、内存降级或关闭状态；
- `WorkflowSnapshot`：P1返回给P5的完整工作流快照。

### 破坏性变化

- 所有顶层公共合同的`contract_version`严格限制为`"1.1"`；
- `RuleCondition`删除`operator`和`required`；
- 新增`ConditionGroup`，由`ALL`或`ANY`表达原子条件组合；
- `DialogueTurn`新增必填`question_id`；
- `QuestionCandidate.utility`限制在0至1；
- `SourceSpan`删除`content_hash`，来源版本校验延期放入数据Manifest；
- `WorkflowSnapshot`从工作流实现模块移动到公共合同模块。

### 新增一致性校验

- `RuleRecord`：检查条件、条件组以及组内成员引用；
- `CaseRecord`：检查请求、法院认定、推理、裁判结果和原文引用；
- `QueryState`：检查事实、条件状态、对话轮次和问题引用；
- `ExplanationPlan`：检查解释分支与引用关系；
- `WorkflowSnapshot`：检查会话ID和下一追问条件引用；
- `ProvisionRecord`：检查来源ID和法条有效日期。

### v1.0数据迁移

1. 将顶层`contract_version`改为`"1.1"`；
2. 从每个`RuleCondition`删除`operator`和`required`；
3. 为规则增加`condition_groups`并覆盖全部`condition_id`；
4. 为每个`DialogueTurn`增加对应的`question_id`；
5. 从`SourceSpan`删除`content_hash`；
6. 重新通过v1.1 JSON Schema和Pydantic模型验证。
