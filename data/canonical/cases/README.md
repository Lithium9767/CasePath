# 案例数据

- `cases_l1.jsonl`：156 条轻量索引记录（45 条 legal-rag、105 条 LexRel、6 条最高法公开案例编辑摘要）。
- `cases_deep.jsonl`：上述 150 条的结构化记录（legal-rag + LexRel）；6 条 SPC 摘要仅保留 L1。
- `additional_cases.jsonl`：最高法公开典型案例补充的 6 件民事案例摘要副本（与 SPC 输入同源，不进入 deep）。
- 六条 SPC 摘要没有深度记录，不伪造诉请、判项或权威原文跨度，未确认终审日期保持为空。

输入、输出哈希及版本见[案例 manifest](../../manifests/cases.manifest.json)。
当前数量与交付口径见[结项交付说明](../../../docs/DELIVERY.md)，运行方式见[运行指南](../../../docs/RUNNING.md)。
数据发布来源与输入输出哈希见上述案例 manifest；当前合并数量以本目录为准。

补充案例仅保存不泄漏检索结果的 L1 摘要。案号、法院或裁判日期在最高人民法院汇编页未明确披露时保留为 `null`，不得推测填写。
