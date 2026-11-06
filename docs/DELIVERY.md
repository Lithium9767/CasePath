# 结项交付与证据说明

[返回首页](../README.md)

整理日期：2026-11-06。课程项目已结项；本文区分源码交付、历史验收材料和研究/生产化边界。

## 版本范围

本次定位到本地 master，HEAD 为 7b9ff53（完成合并）。
该基线后的图谱浏览、算法注释、条件证据与实验文件随结项归档一并提交。
本文描述归档内容，基线 SHA 不是本次新提交号；最终提交以 Git 记录为准。
本次新增 source_turn 的 Schema 与前端类型同步，历史实验仍保持原始结果、不改写成绩。

## 数据口径

本次读取正式 JSONL 的非空行，得到：

| 路径（相对仓库） | 记录数 |
| --- | ---: |
| data/canonical/rules/legal_sources.jsonl | 1 |
| data/canonical/rules/provisions.jsonl | 1260 |
| data/canonical/rules/rules.jsonl | 776 |
| data/canonical/rules/source_spans.jsonl | 3272 |
| data/canonical/cases/cases_l1.jsonl | 156 |
| data/canonical/cases/cases_deep.jsonl | 150 |
| data/canonical/cases/additional_cases.jsonl | 6 |

案例目录说明的来源构成为 45 条 legal-rag、105 条 LexRel、6 条最高法编辑摘要。
轻量和结构化记录按案例 ID 对应，不相加；additional_cases 的摘要副本也不算新案例。
本次做了文件记录数核对，未重新执行来源全文核验或数据语义审核。

成熟度按数据自身及 [规则说明](../data/canonical/rules/README.md)、
[案例说明](../data/canonical/cases/README.md) 阅读。结构化不等于高成熟度，法条条数不等于规则条数。
[规则 manifest](../data/manifests/civil_code.manifest.json)、
[案例 manifest](../data/manifests/cases.manifest.json) 记录输入输出来源与版本。
图节点/边总量和向量条数应随发布版本核对，本文不复用旧阶段的在线数量。

## 可核查的交付

- FastAPI 会话服务、公共合同与运行配置；Neo4j/结构化 LLM/embedding 客户端。
- P2/P3 正式数据、构建/校验/图投影与版本凭据。
- HybridCypher A–E、增量映射、缺失事实追问及证据解释。
- React 前端、真实会话接口、条件/案例/引用与图谱展示。
- tests 下的合同、工作流、数据、算法和接口检查；scripts 下的诊断/实验工具。

详见 [分工](TEAM.md) 和 [架构](ARCHITECTURE.md)。

## 历史实验材料如何引用

| 材料 | 能说明什么 | 不能扩展成什么 |
| --- | --- | --- |
| [模块贡献诊断](../artifacts/module_contributions/run_20260914T1413/README.md) | 13 个固定关系主题查询上的探索性检索对照；账本重放机制 | 独立 A 准确率、严格 E 追问效果或全领域性能证明 |
| [演示实测记录](../artifacts/demo_rehearsal/20260914T141126Z/README.md) | 一次合成健身房场景真实依赖调用及逐轮缺陷 | 每次输出一致、浏览器端验收或所有重复问题解决 |
| [E 实验协议](../scripts/E_QUESTIONING_EXPERIMENT_V2.md) | 冻结输入、公平基线、审核与评分要求 | 仅凭脚本/数据存在就声称实验完成 |
| [测试目录](../tests/) | 可复现的程序行为检查入口 | 人类专家法律审核与真实用户泛化效果 |

旧 README 的 491 passed 等数字是特定阶段记录，不是本次工作区测试成绩。
早期无 LLM/不公平样本产生的 75%→100% 不作为结项贡献证据；AI 审核不能写成人工独立标注。
当前副本未找到 artifacts/p4_acceptance 目录，不将对话中提及的历史八场景报告写为本地已核验附件。

## 保留的限制

- D 仍为描述性比较基线；互信息未实现，信息增益字段为熵×覆盖率启发式。
- E 已按缺失事实决策，但跨条件语义重复仍有波动；最新演示记录也保留了重复追问。
- 当前本地聚合策略按最新轮次方向证据优先，保留旧证据但不保证跨轮相反仍为 CONFLICTING；
  这是实际策略变化，历史冲突验收不能直接代表本次版本。
- 引用有来源不等于引用核验器已接入；置信度不是正确率或胜诉概率。
- L1 规则/案例仍需专业内容复核，结构化案例不一定有足够的条件认定与结果。
- 内存会话、未完整鉴权和数据隔离，不面向直接公网生产部署。
- 全图浏览代码与接口在本地副本中，历史接口检查不能替代当前浏览器性能验收。
- 数据上游授权、敏感信息和再分发范围需逐来源确认，不应批量公开实验原文。

## 本次文档整理做了什么

更新首页、分工、架构、运行、交付与文档索引；保存旧 README，并给早期计划添加历史标记。
文档整理本身未改业务逻辑、数据、图或向量。随后按用户要求一并归档现有代码与实验，
补齐 Schema/前端类型和说明，未调用付费模型、写线上图或重启服务。
发布前检查结果见 [归档说明](../artifacts/README.md)；未重新跑真实多轮或浏览器验收。
