# P2 阶段交接说明

本说明对应 P2 实现提交 `b196bfc`，用于本轮模块交接。交付分支为 `P2`，由组长统一合并。
本文只描述 P2 的实现与接入要求，不代表整项目联调已经通过。

## 已完成

- 法条导入与数据生成：1 个法源、1,260 条连续且唯一的法条、5 条规则、1,268 个来源跨度。
  当前发布数据标注 4 条基础规则为 L3、1 条综合规则为 L2。
- 对齐公共 v1.1 合同：普通条件通过 `ConditionGroup` 组合，替代履行例外使用
  `RuleException`；法条内嵌全文跨度，旧的逐条/逐跨度 `content_hash` 和原子条件
  `operator`、`required` 已从 P2 输出移除。
- 校验非空正文、稳定 ID、跨文件引用、原文字符区间、关键条文固定哈希、输入版本及
  输出文件哈希；构建先暂存校验，发布失败可回滚 P2 生成文件。
- 修正输入相对路径为 `../legal-rag/data/laws/`，保留固定上游 revision，确保可重建。
- 提供摄取、规则、数据集和发布回滚测试；验证演示约定的 5 个事实标识均能在 P2
  聚合规则的普通条件或例外中解析。

代码入口：[摄取器](../src/casepath/ingestion/laws/civil_code.py)、
[规则生成器](../src/casepath/rule_layer/civil_code.py)、
[构建入口](../src/casepath/rule_layer/build.py)、
[数据校验器](../src/casepath/rule_layer/validation.py)。
交付文件及数量见 [数据目录说明](../data/canonical/rules/README.md)。

## 尚未完成与待确认

- 真实数据端到端联调尚未作为 P2 验收证据完成。目前能证明数据可解析、引用一致及
  演示 ID 兼容；后续需在组长的集成环境中确认真实规则检索、条件投影和引用回查。
- 综合服务退款规则仍为 L2：尚未结构化接入预付式消费专项司法解释，也未实现专项
  解除事由或退款金额计算；这些限制已保存在规则正文与 manifest 中。
- 人工复核证据仍需确认：manifest 已有来源、日期、条文哈希和 `human_verified`
  标记，但当前仓库未记录复核人及独立复核记录。该标记由构建器生成，自动测试只能
  证明数据与固定定义一致，不能单凭标记证明已完成人工复核；交付前需补齐可追溯记录。
- Windows 符号链接测试因当前环境无创建权限而跳过；该项尚未在本次完整测试中执行。

## P2 合同冗余检查及处理

| 内容 | 处理与原因 |
| --- | --- |
| `LegalSourceRecord`、`ProvisionRecord`、`RuleRecord` | 保留，分别表达整部法律、单条原文、结构化规则，职责不同。 |
| 内嵌 `SourceSpan` 与独立 `source_spans.jsonl` | 保留。内嵌片段支持规则独立读取，独立文件用于按 ID 回查；由同一构建流程生成并逐项校验，不维护两套人工数据。 |
| `ProvisionRef` 中的条号、标题和日期 | 属于可派生的重复信息，但当前公共合同和规则独立读取依赖它们；由法条统一生成并校验，暂不删字段。 |
| 普通条件、条件组和例外 | 保留，分别表达事实、ALL/ANY 组合和阻却语义，不能互相替代。 |
| 旧哈希字段、原子条件 `operator`/`required` | 前次适配已移除，使用 manifest 哈希、条件组与例外表达。 |
| 数据目录说明中的“法条包含修正后的层级” | 本次修正；当前法条合同不含层级字段，修复信息仅在摄取过程和 manifest 审计记录中保留。 |

本次未发现可以在保持当前公共合同兼容的同时直接删除的其他字段。
未新增平行合同，也未修改公共合同、生成数据或其他模块实现。

## 接入 P2 时需要注意

- 优先读取 `data/canonical/rules/rules.jsonl` 建立规则索引；来源回查使用
  `provisions.jsonl`、`legal_sources.jsonl` 与 `source_spans.jsonl`。
- 条号为 `"563"`，法条 ID 为 `law.prc.civil_code.2021.article_0563`，全文跨度 ID 为
  `span.civil-code.563`；应直接复用发布数据里的 ID。
- 事实标识要同时覆盖 `conditions` 和 `exceptions`。`cond.alternative_performance`
  保留原字符串，但现在位于 `exceptions[].exception_id`，不能作为普通 ALL 条件处理。
- 片段偏移基于规范化法条正文，满足 `text[start_offset:end_offset] == quote`，
  不表示 PDF 页码或字节位置。

## 验证方式与已有结果

在 `CasePath/` 根目录、已安装项目依赖的 Python 环境中运行：

```powershell
python -m casepath.rule_layer.build --data-root data --validate-only
python -m pytest -o "addopts=" -q -rs
```

只校验已提交数据无需克隆上游仓库；从原始数据重新构建才需要同级 `legal-rag/`
及固定 revision `ce7872c7ae343e5ff860d627195ec4e72c7ef7ce`。

实现提交 `b196bfc` 的已有验证结果：数据校验 `passed`；全仓测试
`133 passed, 1 skipped`（Windows 创建符号链接时报 `WinError 1314`）；
全仓 Ruff 静态检查通过，P2 自有 Python 文件格式检查通过。
这些结果不包含其他分支合并后的联调验证，也不替代人工法律复核。
