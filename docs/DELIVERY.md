# 结项交付说明

[返回首页](../README.md)

CasePath 已完成知识工程课程实践的设计、实现与交付。本文汇总交付内容、数据与代码检查结果。

## 交付内容

| 部分 | 交付 |
| --- | --- |
| 系统工程 | FastAPI 会话服务、公共合同、工作流、配置及外部服务客户端 |
| 规则与案例数据 | 法条、结构化规则、案例记录、来源跨度、构建校验与版本凭据 |
| 知识图谱 | 两层图谱投影、跨层关系、Neo4j 导入与只读查询 |
| 在线算法 | HybridCypher A–E、增量条件映射、缺失事实追问与证据解释 |
| 前端 | 对话、条件矩阵、案例对照、引用及全图与检索路径展示 |
| 开发工具 | 自动化测试、Schema 导出、索引构建与实验脚本 |

代码位置与成员职责见 [团队分工](TEAM.md)，模块调用关系见 [架构说明](ARCHITECTURE.md)。

## 数据口径

2026-11-06 核对本地正式 JSONL 记录数量：

| 文件 | 记录数 |
| --- | ---: |
| data/canonical/rules/legal_sources.jsonl | 1 |
| data/canonical/rules/provisions.jsonl | 1,260 |
| data/canonical/rules/rules.jsonl | 776 |
| data/canonical/rules/source_spans.jsonl | 3,272 |
| data/canonical/cases/cases_l1.jsonl | 156 |
| data/canonical/cases/cases_deep.jsonl | 150 |

156 件案例包含 45 条 legal-rag、105 条 LexRel 和 6 条最高法编辑摘要。
150 条结构化记录属于这 156 件案例，不重复相加；additional_cases.jsonl 是 6 条摘要副本。
数据结构与来源见 [规则说明](../data/canonical/rules/README.md)、[案例说明](../data/canonical/cases/README.md)、
[规则 manifest](../data/manifests/civil_code.manifest.json) 和 [案例 manifest](../data/manifests/cases.manifest.json)。

## 提交前代码检查

2026-11-06 运行结果：

- 后端自动化测试：562 passed、2 skipped。
- 跳过项：专用 Neo4j 测试库未配置、Windows 符号链接权限不可用。
- Ruff 检查通过。
- 前端生产构建、TypeScript 类型检查通过。
- 4 项前端检索路径测试通过。

以上为代码级检查。运行命令见 [运行指南](RUNNING.md)；
实验脚本保留在 scripts，实验诊断产物在本地保存，不作为本页交付成绩。

## 文档交付

[项目首页](../README.md) · [团队分工](TEAM.md) · [架构](ARCHITECTURE.md) ·
[运行指南](RUNNING.md) · [文档导航](README.md)
