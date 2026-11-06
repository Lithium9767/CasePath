# P2 规范规则层数据

本目录由 `python -m casepath.rule_layer.stage2_refinement` 确定性生成，请勿手工修改 JSONL。
结项口径见[交付说明](../../../docs/DELIVERY.md)，核验命令见[运行指南](../../../docs/RUNNING.md)。
第二阶段规则修订与来源核对信息见下文 manifest。

| 文件 | 记录数 | 用途 |
|---|---:|---|
| `legal_sources.jsonl` | 1 | 《中华人民共和国民法典》的版本与权威来源 |
| `provisions.jsonl` | 1,260 | 第 1—1260 条完整正文与修正后的层级 |
| `rules.jsonl` | 776 | 771 条 L1 扩展规则，保留 4 条 L3 基础规则与 1 条 L2 综合规则；1,928 个唯一条件 |
| `source_spans.jsonl` | 3,272 | 每条法条全文跨度及规则使用的细粒度跨度 |

`SourceSpan.start_offset` 为零基索引，`end_offset` 为右开区间；必须满足：

```python
span.quote == provision.text[span.start_offset : span.end_offset]
```

manifest 中的文本文件 SHA-256 先把 CRLF/CR 规范化为 LF，因此在 Windows 与 Linux 检出时一致。
其中输入路径始终以 `CasePath/` 仓库根目录为基准；输出路径以 `data/` 的父目录为基准。

P3、P4 应直接复用 `rules.jsonl` 中的 `condition_id`。为兼容已冻结的演示链路，本数据使用
`cond.performance_impossible` 等下划线 ID，并保留完整法条跨度 ID
`span.civil-code.563`。法条 `provision_id` 使用四位编号，例如
`law.prc.civil_code.2021.article_0563`。

第 563 条规定的是解除权而非自动解除，第 565 条规定主张方式和解除生效时点；第 566 条的
返还或补救以合同已有效解除为前提，且不直接产生固定的全额退款公式。综合服务退款规则未
结构化接入 2025 年预付式消费司法解释，故标为 L2，并在规则正文中保留限制说明。详细来源、
哈希、复核日期和适用限制见 `../../manifests/civil_code.manifest.json`。

P1合同不在原子条件上使用`UNLESS`。法定核心条件放入`CORE`组；“替代履行”等
案例比较事实放入`DIAGNOSTIC`组，只影响检索、分化和追问，不能被P4直接当作法定要件。
