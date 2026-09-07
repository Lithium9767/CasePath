# P2 法源合同变更请求（已按P1合同接收）

## 最终决定

P2最初在分支中提出了一套独立的法源字段。合并到P1时没有保留第二套同义合同，
而是统一映射到P1公共对象：

| P2分支字段 | P1正式字段或存放位置 |
|---|---|
| `document_type` | `LegalSourceRecord.source_type` |
| `valid_from / valid_to` | `effective_from / effective_to` |
| `official_source_url` | `official_url` |
| `source_span_ids` | `ProvisionRecord.source_spans`中的稳定ID |
| 单记录`content_hash` | 输入固定哈希、正文集合固定哈希与manifest输出哈希 |
| `book/sub_book/chapter/section` | `ProvisionRecord`的可选层级字段 |
| `maturity` | `ProvisionRecord.maturity` |

`ProvisionRecord`和`RuleRecord`继续读取v1.1历史JSON，新生成数据默认输出v1.3。
`LegalSourceRecord`沿用P1的v1.1字段集合。所有对象继续继承
`ContractModel(extra="forbid")`，未知字段会被拒绝。

## 保留的P2约束

- `provision_id`使用`article_0001`至`article_1260`；
- `article_no`使用不补零字符串，例如`"509"`；
- 条件ID使用已冻结的下划线形式，例如`cond.performance_impossible`；
- 完整法条跨度使用`span.civil-code.563`形式；
- 上游仓库提交、输入哈希、109条层级修复和权威核验信息写入manifest；
- 数据必须由原子构建器生成并通过跨文件引用、原文偏移及确定性规则校验。

规则逻辑不采用P2旧分支的单条件`UNLESS/required`字段。P1以
`ConditionGroup(role=CORE|DIAGNOSTIC)`表达法定核心条件和案例比较条件，避免P4把
“替代履行”等诊断事实误当作法条明文要件。
