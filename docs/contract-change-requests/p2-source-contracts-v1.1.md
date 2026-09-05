# P2 法源合同 v1.1 适配记录

## 状态

P1 已在提交 `f674c49` 中接受“区分整部法律与单条法条”的需求，并以调整后的字段
冻结 `LegalSourceRecord`、`ProvisionRecord` 和 `RuleRecord` v1.1。本文件记录原 P2
提案如何迁移到 P1 的正式合同；正式定义以 `src/casepath/contracts/`、导出的 JSON
Schema 和 `contracts/CHANGELOG.md` 为准。

## P1 最终合同与原提案的差异

- `LegalSourceRecord` 使用 `source_type`、`effective_from`、`effective_to` 和
  `official_url`，内容哈希与效力核验记录放入数据 manifest；
- `ProvisionRecord` 直接内嵌至少一个 `SourceSpan`，不再保存 `source_span_ids`、
  单条内容哈希、层级和成熟度字段；
- `SourceSpan` 不保存内容哈希，引用正确性由字符区间回放和发布文件哈希共同校验；
- `RuleRecord` 升级为 v1.1，原子条件不再携带 `operator`/`required`，全部普通条件必须
  进入 `ConditionGroup`；`UNLESS` 语义改由 `RuleException` 表达。

## P2 迁移决定

- `provision_id` 继续使用 `article_0001` 至 `article_1260`；
- `ProvisionRecord.article_no` 与 `ProvisionRef.article_no` 均使用不补零数字字符串，
  如 `"509"`，避免同一法条出现两个机器节点；
- 条件 ID 继续使用 P1/P3/P4 实际采用的下划线形式，如
  `cond.performance_impossible`；
- 完整法条跨度继续使用 `span.civil-code.563`，兼容既有演示引用；
- `cond.alternative_performance` 按 v1.1 规则合同迁入 `RuleException.exception_id`，
  暂时保留原字符串以避免破坏跨团队 ID。P4 接入真实规则数据时需要同时投影规则条件
  和可回答的规则例外；
- 上游层级错位仍在 P2 摄取阶段修复并在 manifest 中审计，但 P1 冻结的
  `ProvisionRecord` 不公开层级字段。

## 验收门槛

- 1 个法源、1,260 条法条和 5 条规则必须通过 P1 运行时合同与导出 Schema；
- 每条法条的内嵌全文跨度必须与独立 `source_spans.jsonl` 中的规范跨度一致；
- 所有普通条件必须进入条件组，所有条件、例外与后果必须引用可回放的原文跨度；
- 输入 revision、输入哈希、权威核验记录、输出数量与固定发布哈希必须一致；
- P1、P2 与完整仓库测试必须同时通过。
