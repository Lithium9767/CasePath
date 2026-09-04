# P2 法源合同 v1.1 变更请求

## 原因

P2 需要分别表达整部法律与单条法条。现有 v1.0 只有 `RuleRecord` 中的轻量
`ProvisionRef`，无法验证 1,260 条法条的正文、版本、效力状态与来源跨度。

## 请求 P1 审核的新增合同

- `LegalSourceRecord`：法律标题、制定机关、文件类型、公布/施行日期、效力状态、管辖域、
  官方来源与内容哈希；
- `ProvisionRecord`：稳定四位 `provision_id`、不补零 `article_no`、完整正文、层级、版本、
  L0 成熟度、完整原文跨度 ID 与内容哈希。

两类合同固定 `contract_version = "1.1"`，继续继承 `ContractModel` 的
`extra="forbid"` 约束。对应示例与 JSON Schema 已随本分支提交。
`ProvisionRecord.source_span_ids` 至少包含一项；两类记录均拒绝早于 `valid_from` 的
`valid_to`。

## 兼容决定

- `provision_id` 遵循计划书，使用 `article_0001` 至 `article_1260`；
- `article_no` 与现有 `ProvisionRef` 对齐，使用不补零字符串，如 `"509"`；
- 条件 ID 延续已冻结示例和工作流的下划线形式，如
  `cond.performance_impossible`；
- 完整法条跨度延续 `span.civil-code.563` 形式，避免破坏现有演示引用；
- 不修改既有 `RuleRecord` v1.0，规则人工复核状态记录在 manifest 中。

## 审核门槛

- 两个示例必须通过 Pydantic 合同与导出的 JSON Schema；
- 未知字段、补零 `article_no`、非法 SHA-256 和错误版本必须拒绝；
- P1 批准后再合并到主分支；若 P1 调整字段，应同步重建 P2 数据并更新 manifest 哈希。
