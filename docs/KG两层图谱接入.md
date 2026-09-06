# P1 两层知识图谱构建

规则层包含 LegalSource、Provision、LegalRule、RuleCondition、ConditionGroup、
RuleException、LegalConsequence。案例层包含 Case、Claim、CourtFinding、
ConditionFinding、ReasoningStep、DecisionItem。SourceSpan 是两层的证据附件。

P2/P3 canonical JSONL 是事实来源，Neo4j 是可重建的派生视图。导入器先运行双方的
发布验收，再检查共享 ID 冲突和悬空关系。不会执行 LLM 抽取或改变合同版本。

## 运行

从正式仓库根目录执行：

```powershell
uv run python -m casepath.ingestion.graph
uv run python -m casepath.ingestion.graph --output data/derived/graph/preview.json
uv run pytest tests/ingestion/graph
```

默认只做离线预览。实际导入前，在本机环境设置 CASEPATH_NEO4J_URI、
CASEPATH_NEO4J_DATABASE、CASEPATH_NEO4J_WRITE_USER、CASEPATH_NEO4J_WRITE_PASSWORD。
写账号需要创建约束及写数据权限，与在线只读账号分开。程序不自动读取 .env。

```powershell
uv run python -m casepath.ingestion.graph --import-neo4j
```

需安装项目 live 依赖。发布采用一个数据事务，UNWIND 分批只用于控制请求体大小，
没有将各批单独提交。任何数据或验收错误都会回滚整个数据事务。约束在事务前创建，
失败时可能保留空约束。旧图发布不删除，生产大规模更新需另行设计保留和清理策略。

输入哈希、图结构和投影版本共同决定 release_id。重复导入相同内容不会新增节点或边。
不同发布隔离存储；成功后更新 CasePathCatalog.active_release_id。额外的 Release 和
Catalog 节点是发布元数据，不是第三层法律知识。

## P4 查询约定

必须先读活动版本，并在本轮查询中固定该版本，不能查询所有版本混合的 Case 节点。

```cypher
MATCH (catalog:CasePathCatalog {name:'casepath'})
RETURN catalog.active_release_id AS release_id
```

规则条件到案例证据路径：

```cypher
MATCH (rule:CasePathEntity:LegalRule {release_id:$release_id, object_id:$rule_id})
      -[:HAS_GROUP]->(g:ConditionGroup)-[:HAS_MEMBER]->(condition:RuleCondition)
MATCH (case:Case)-[:HAS_ASSESSMENT]->(a:ConditionFinding)-[:ABOUT]->(condition)
OPTIONAL MATCH (a)-[:BASED_ON]->(f:CourtFinding)
OPTIONAL MATCH (a)-[:EVIDENCED_BY]->(s:SourceSpan)
RETURN rule.object_id, g.role, g.operator, condition.object_id,
       case.object_id, a.status, a.confidence, a.human_verified,
       collect(DISTINCT f.predicate) AS findings,
       collect(DISTINCT s.quote) AS quotes
```

所有业务 ID 保存在 object_id，原字段（如 case_id）也保留。Neo4j 内部 key 将类型和
业务 ID 组合；不要拿 Neo4j elementId 当合同 ID。完整属性另存 payload_json，复杂嵌套
属性用 *_json 保存。原文不截断；日期暂存 ISO 字符串。CORE/DIAGNOSTIC 在条件组上。

轻量与深度案例共用一个 Case 节点，分别记录 contract_version 与 deep_contract_version。
ConditionFinding 以 case_id 和 condition_id 派生稳定 ID；当前是案例级，未编造 claim_id。
存在原文但没有 finding_ids 的桥接可回溯 SourceSpan，不能伪造 BASED_ON 关系。
不生成 Decision→LegalConsequence、永久支持/限制标签或推断的案例适用关系。

## 验收与限制

单元测试覆盖冲突节点、悬空边、共享条件去重、两层证据路径、事务故障和切换顺序。
设置 CASEPATH_KG_TEST_DATABASE（独立测试库）后运行测试，可验证真实 Neo4j 重复导入。
未配置时跳过真实集成测试，不能宣称已验证数据库连接或实际导入。

本版没有创建全文/向量索引，也没有实现 P4 排序。SourceSpan 保留原字段和所属对象证据边，
偏移遵循 P2/P3 的原载体；没有将案例事实、说理偏移改成相对整篇文书的偏移。
已知仅一条深度案例，图可用于联调，不能据此开展多案例分化实验。
