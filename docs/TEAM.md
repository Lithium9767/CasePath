# 团队分工与交付边界

[返回首页](../README.md) · [代码阅读](ARCHITECTURE.md)

项目采用 P1–P5 五人协作。下表整理职责和最终代码位置，不以目录独占或 Git 作者数量推断个人贡献；
成员姓名未在本次材料中确认，因此保留角色编号，不填写猜测的姓名。

## 职责与对应代码

| 角色 | 主要职责 | 代码/数据入口 | 对其他成员的交付 |
| --- | --- | --- | --- |
| P1 系统集成 | 定义公共合同、统一运行配置、连接外部服务、管理会话与异常、集成发布 | [contracts](../src/casepath/contracts/)、[ports](../src/casepath/ports/)、[application](../src/casepath/application/)、[workflow](../src/casepath/workflow/)、[api](../src/casepath/api/)、[bootstrap.py](../src/casepath/bootstrap.py)、[infrastructure](../src/casepath/adapters/infrastructure/) | 可替换接口、Live 装配、会话快照和错误响应 |
| P2 规则数据 | 法源核验、法条导入、条件/条件组/后果构造、规则发布与质量校验 | [ingestion/laws](../src/casepath/ingestion/laws/)、[rule_layer](../src/casepath/rule_layer/)、[规则数据](../data/canonical/rules/) | 稳定规则/条件 ID、原文 SourceSpan、发布 manifest |
| P3 案例数据 | 清洗与分段、请求/查明/理由/结果组织、规则条件桥接、补充案例与评测语料 | [ingestion/cases](../src/casepath/ingestion/cases/)、[案例合同](../src/casepath/contracts/case.py)、[案例数据](../data/canonical/cases/) | 轻量索引、结构化记录、桥接证据与输入输出凭据 |
| P4 算法 | 查询扩展、混合召回、用途分层、增量映射、案例比较、缺失事实追问、解释 | [p4_factory.py](../src/casepath/adapters/p4_factory.py)、[retrieval](../src/casepath/adapters/retrieval/)、[conditions](../src/casepath/adapters/conditions/)、[questioning](../src/casepath/adapters/questioning/)、[explanation](../src/casepath/adapters/explanation/) | 实现 P1 端口的组件、结构化检索/比较/追问输出、诊断与实验 |
| P5 前端 | 用户对话、条件与案例面板、证据和图谱呈现、状态反馈、演示联调 | [frontend/src](../frontend/src/)、[client.ts](../frontend/src/api/client.ts)、[RightPanel](../frontend/src/components/RightPanel/) | 消费 WorkflowSnapshot 的界面、交互与演示材料 |

## 跨模块协作

1. **P2 → P3/P4**：共享 condition_id 及条件定义；修改 ID 必须同步检查案例桥接和索引。
2. **P3 → P4**：提供可追溯的案例认定；“引用法条”“请求援引规则”“裁判理由适用规则”不能混用。
3. **P2/P3 → P1**：正式数据通过校验后由 [图投影](../src/casepath/ingestion/graph/projection.py) 转为节点和边，再导入 Neo4j；embedding 是另一路检索索引，不负责生成图关系。
4. **P1 ↔ P4**：P1 管客户端、配置和会话事务；P4 管 Cypher 路径、Prompt、检索与决策逻辑。
5. **P1/P4 → P5**：接口返回结构化快照，而非只有回答字符串；未知、未定角色、降级和失败需要保留到界面。

## 结项后的阅读与维护约定

- 合同变动同时检查 Schema、示例、前端类型和兼容性；合同并非所有类型统一一个版本。
- 不为对称另建空的 case_layer：案例生产逻辑目前集中在 ingestion/cases。
- 数据、图和向量索引按发布版本配套；不要用早期五规则构建流程覆盖扩展数据。
- 模型措辞、算法分数、来源审核等级分别维护，不把“程序可运行”等同于“法律内容已审核”。
- 文档中的职责是协作边界，第二阶段的数据精修和图谱前后端接入存在共同工作，不重复归功。
- 如需姓名版分工表，在本表角色后补真实姓名即可，无须重命名代码目录。
