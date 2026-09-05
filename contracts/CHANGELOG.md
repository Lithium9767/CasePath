# CasePath公共合同变更记录

## v1.2 增量会话合同（2026-09-05）

### 版本边界

- 仅新增 `CreateSessionRequest` 和 `AnswerInterpretation`，两者版本为 `"1.2"`。
- 原有十一种 v1.1 合同及其字段、枚举、版本约束不变，原示例无需迁移。
- 因此创建请求为 v1.2，回答请求仍为 v1.1，返回 `WorkflowSnapshot` 仍为 v1.1。
- 未传 `contract_version` 时使用对应模型默认值；显式传错版本返回 422。
- API 路径中的 `/v1/` 是路由主版本，不等同于每个数据模型的修订版本。

### 新增对象

- `CreateSessionRequest(query)`：由服务端分配会话 ID，拒绝空白问题和额外字段。
- `AnswerInterpretation(new_facts, condition_updates)`：P4 返回本次事实和条件更新。
  `new_facts` 逐字摘自本次回答，来源轮次等于当前最大 `turn_id + 1`；
  `condition_updates` 是对应条件记录的完整替换，P4 负责综合历史支持和冲突证据；
  每个非 UNKNOWN 更新必须引用已有或本批新建的用户事实。
- `SessionRecord` / `AnswerReceipt` 是后端私有存储模型，不属于公共合同注册表。

### HTTP 行为

- 增加创建、读取、回答三个会话接口及 `/v1/capabilities`。
- 成功返回已有 `WorkflowSnapshot`，会话 ID 位于 `query_state.session_id`，不额外复制顶层字段。
- 相同会话、相同 `question_id` 和相同回答内容重放当时成功结果；不同内容返回 409。
  重放旧答案不会使最新会话回退，读取最新结果必须使用 GET 会话接口。
- 统一使用已有 `ErrorResponse`：409 使用 `INVALID_REQUEST` 和
  `details.reason=session_conflict`；解析能力不可用返回 503、`INTERNAL_ERROR` 和
  `details.reason=answer_interpreter_unavailable`。未偷偷扩展已冻结错误枚举。
- 未知 Schema 名称原先返回 200 的非标准错误字典，现修正为 404 的 `ErrorResponse`。

### 行为限制

- 默认 Demo 会话链路只保存回答原文，不改变条件状态，不包含真实 P4 自然语言分析。
- 完整回答保存在 `DialogueTurn.answer`；`UserFact` 只保存 P4 提取的事实片段，
  不再由会话服务额外生成一条内容相同的原始事实。
- 默认最多追问 3 轮，QuestionPolicy 负责过滤已问条件；Workflow 只验证不变量。
  停止追问不意味着未知条件已经解决。
- 会话状态统一通过 `SessionService.submit_answer()` 修改，删除允许调用方直接提交
  `ConditionStatus` 的旧 `Workflow.apply_answer()`。
- `POST /v1/demo/analyze` 保留兼容但标记为弃用，P5 只使用正式会话接口。
- 未接入引用核验器，移除虚假的 `VERIFY_CITATIONS` 以及“未配置”占位日志；
  未核验状态由 `CitationRecord.verified=false` 和 `/v1/capabilities` 表达。

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
