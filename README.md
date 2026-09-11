# CasePath

CasePath 是“规范规则层 + 案例适用层”的交互式民事法律信息检索与条件化解释框架。
当前兼容历史 v1.1 公共合同，增加 v1.2 会话合同、v1.3 P4工作流/规则合同及多轮会话服务。
默认仍使用演示检索和保守回答记录器，不提供自动法律判断，引用尚未核验。

## 当前完成

- 十一个v1.1公共合同：法源、法条、规则、案例、用户状态、检索、解释、回答、错误、能力状态和工作流快照；
- `RuleCondition + ConditionGroup`规则逻辑结构，以及规则、案例、查询和解释的跨ID一致性校验；
- 检索、条件投影、案例分化、追问策略、解释规划的可替换端口；
- 规则召回→条件投影→案例检索→案例分化→追问→解释的确定性主工作流；
- 不依赖 Neo4j、向量库或外部 LLM 的内存演示链路；
- JSON Schema 导出脚本和契约测试；
- FastAPI 可选入口。
- 单进程内存会话服务：创建、读取、回答、原文保存、并发版本检查和成功请求重放。
- P2规则数据：1部民法典、1,260条法条、1,268个原文片段和5条核验规则。

## 目录职责

```text
src/casepath/contracts/   五人共同遵守的数据合同
src/casepath/application/ 会话记录、跨轮次服务和应用错误
src/casepath/ports/       外部数据库、检索器、LLM的抽象端口
src/casepath/workflow/    确定性状态机与模块编排
src/casepath/adapters/    按职责分组的实现（infrastructure/retrieval/conditions/questioning/explanation/demo）
src/casepath/api/         HTTP接口，不放业务规则
src/casepath/ingestion/   P2离线法条读取、层级修复和来源校验
src/casepath/rule_layer/  P2规则构造、原子发布和数据验收
contracts/examples/       跨模块示例JSON
data/canonical/rules/     P2生成并发布的规范规则层数据
scripts/                  Schema导出等开发工具
tests/                    契约和纵向链路测试
```

## 本地运行（Demo，无需密钥）

```powershell
uv sync --extra api --extra live --group dev
uv run casepath-demo
uv run pytest
uv run uvicorn casepath.api.main:app --reload
```

`casepath.api.main:app` **始终是 Demo**：固定检索基线、不读 Neo4j/LLM。
正式算法必须走下文「环境配置」中的 `configured` 入口。

API 启动后：

- `GET /health`
- `POST /v1/sessions`
- `GET /v1/sessions/{session_id}`
- `POST /v1/sessions/{session_id}/answers`
- `GET /v1/capabilities`
- `GET /v1/contracts/{contract_name}/schema`

## 环境配置（Live）

程序**不会自动加载** `.env`。必须把变量写进进程环境，或启动时加
`uv run --env-file .env ...`。不要把真实密码或 API Key 提交到 Git。

### 1. 创建本地 `.env`

在仓库根目录 `CasePath/`（含 `pyproject.toml` 与 `data/` 的那一层）：

```powershell
copy .env.example .env
```

用编辑器打开 `.env`，按需填写。完整变量说明见 `.env.example` 内注释。

### 2. 变量一览

| 类别 | 变量 | 作用 |
| --- | --- | --- |
| 模式 | `CASEPATH_MODE=live` | 只有显式 `live` 才进正式装配；否则 Demo |
| 数据 | `CASEPATH_DATA_ROOT=data` | canonical / derived 数据根目录 |
| 追问 | `CASEPATH_MAX_QUESTION_TURNS` | 最多追问轮次，默认 5 |
| LLM | `CASEPATH_LLM_API_KEY` | 也可改用 `DASHSCOPE_API_KEY` |
| LLM | `CASEPATH_LLM_BASE_URL` / `CASEPATH_LLM_MODEL` | OpenAI 兼容地址与模型名 |
| LLM | `CASEPATH_LLM_REQUIRED` | `true`：缺 Key 拒绝启动；`false`：允许词法降级 |
| LLM | `CASEPATH_LLM_STRUCTURED_OUTPUT` | 默认 `json_object`；确认支持后再用 `json_schema` |
| 图（读） | `CASEPATH_NEO4J_URI` / `USER` / `PASSWORD` / `DATABASE` | 在线只读查询；**不填密码则用内存图** |
| 图（写） | `CASEPATH_NEO4J_WRITE_USER` / `WRITE_PASSWORD` | **仅** `--import-neo4j` 使用，不回退读密码 |
| 向量 | `CASEPATH_EMBEDDING_*` + `CASEPATH_VECTOR_INDEX` | 启用向量召回；缺省则 `vector_search` 不可用 |

运行配置由 `RuntimeSettings.from_env()` 与 `EmbeddingSettings.from_env()` 分别加载。

### 3. LLM 示例

百炼（`.env.example` 默认）：

```env
CASEPATH_LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
CASEPATH_LLM_MODEL=qwen-plus
CASEPATH_LLM_STRUCTURED_OUTPUT=json_object
```

DeepSeek：

```env
CASEPATH_LLM_BASE_URL=https://api.deepseek.com
CASEPATH_LLM_MODEL=deepseek-v4-flash
CASEPATH_LLM_STRUCTURED_OUTPUT=json_object
```

`BASE_URL` 需带到服务商要求的兼容根路径（通常含或不含 `/v1`，以该厂商文档为准）。

### 4. Neo4j：先导入，再填只读密码

Aura 使用控制台给出的 `neo4j+s://...`，不要写 `localhost`。导入用写账号：

```powershell
uv sync --extra live
uv run --extra live --env-file .env python -m casepath.ingestion.graph --import-neo4j
```

成功输出含 `"published": true` 与 `release_id`。之后在 `.env` 填入**只读**
`CASEPATH_NEO4J_USER` / `CASEPATH_NEO4J_PASSWORD`，供 Live API 查询。

### 5. Embedding / 向量索引（可选）

要启用向量通道时，在 `.env` 配置：

```env
CASEPATH_EMBEDDING_API_KEY=...
CASEPATH_EMBEDDING_BASE_URL=...
CASEPATH_EMBEDDING_MODEL=...
CASEPATH_EMBEDDING_REVISION=...
CASEPATH_EMBEDDING_DIMENSIONS=...
CASEPATH_VECTOR_INDEX=data/derived/vectors/index.json
```

然后显式重建索引（会调用 embedding 服务并产生费用）：

```powershell
uv run --extra live --env-file .env python scripts/build_vector_index.py
```

数据或图谱变更后，应重新导入 Neo4j，并按需重建向量索引，再重启 API。

### 6. 启动 Live API

```powershell
uv sync --extra api --extra live --group dev
uv run --extra api --extra live --env-file .env uvicorn casepath.api.configured:create_configured_app --factory --host 127.0.0.1 --port 8000
```

验收：

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
Invoke-RestMethod http://127.0.0.1:8000/v1/capabilities
```

关注 `legal_graph`、`llm`、`vector_search`、`condition_projector` 的 `available` / `mode`。
`/health` 只表示进程存活；能力清单才说明是否真正接到图与模型。

说明：

- 启动与 `/v1/capabilities` **不会**调用 LLM（不耗额度）；真正发模型是在
  `POST /v1/sessions` 等业务请求时。
- 客户端初始化不验证 Key 远端是否有效；鉴权/超时失败在请求时返回稳定错误，
  且失败不会提交会话更新。
- 接入 LLM 不等于「完整 RAG 已验收」；追问与部分解释路径仍可能是启发式基线。
- 会话默认内存存储，重启或热重载会丢会话；单进程联调，勿直接公网部署敏感材料。

### 7. 前端

```powershell
cd frontend
copy .env.example .env
npm install
npm run dev
```

`frontend/.env`：

```env
VITE_USE_MOCK=false
VITE_API_BASE=
```

`VITE_API_BASE` 留空时经 Vite 代理访问本机 `8000`；独立后端时再填写完整 API 根地址。
`VITE_USE_MOCK=true` 仅用于本地对照 Mock，不走真实会话接口。

### 8. 配置分层（按目标选）

| 目标 | 需要 |
| --- | --- |
| 离线 Demo / 单测 | 可不建 `.env`；用 `main:app` 或 `casepath-demo` |
| 真 LLM | `CASEPATH_MODE=live` + LLM 变量；`configured` 入口 |
| 真图查询 | 完成 `--import-neo4j`，并配置只读 Neo4j 密码 |
| 向量召回 | Embedding 变量 + `build_vector_index.py` |

## P1 会话模块怎么阅读

按 `api/sessions.py → application/session_service.py → ports/interfaces.py →
workflow/engine.py → adapters/infrastructure/memory_session_repository.py` 阅读。

| 文件 | 负责的事情 |
| --- | --- |
| `contracts/session.py` | 新增创建请求和 P4 回答解释结果；不改已有 v1.1 对象 |
| `application/models.py` | 保存最新快照、状态版本和回答回执 |
| `ports/session_repository.py` | 规定创建、读取、原子版本更新接口 |
| `adapters/infrastructure/memory_session_repository.py` | 带锁和深拷贝的内存仓库 |
| `ports/interfaces.py` | 增加 `AnswerInterpreter`，由 P4 实现语义映射 |
| `application/session_service.py` | 校验待回答问题、调用 P4、合并证据、重跑、保存 |
| `api/sessions.py` / `api/errors.py` | 三个会话入口及统一错误响应 |
| `bootstrap.py` / `api/main.py` | 每个应用实例只装配一套仓库和服务 |

完整调用链：前端 → 会话 API → SessionService → 回答解释器 / Workflow → Repository。
`Workflow` 处理单轮分析，`SessionService` 处理跨轮次保存和一致性。

## P5 联调示例

在另一个 PowerShell 终端执行（Live 或 Demo 服务已启动）：

```powershell
$baseUrl = 'http://127.0.0.1:8000'
$createBody = @{query='健身房关门了，还有未消费余额'} | ConvertTo-Json
$snapshot = Invoke-RestMethod -Method Post -Uri "$baseUrl/v1/sessions" -ContentType 'application/json; charset=utf-8' -Body ([Text.Encoding]::UTF8.GetBytes($createBody))
$sessionId = $snapshot.query_state.session_id
$question = $snapshot.next_question
$answerBody = @{question_id=$question.question_id; condition_id=$question.condition_id; answer='不清楚'; selected_option='不清楚'} | ConvertTo-Json
$updated = Invoke-RestMethod -Method Post -Uri "$baseUrl/v1/sessions/$sessionId/answers" -ContentType 'application/json; charset=utf-8' -Body ([Text.Encoding]::UTF8.GetBytes($answerBody))
Invoke-RestMethod -Uri "$baseUrl/v1/sessions/$sessionId"
```

这条固定演示问题会产生追问；一般调用应先检查 `next_question` 是否为空。
Demo 会把“不清楚”的完整原文保存在 `DialogueTurn.answer`、重新运行工作流并停止重复询问；
相关条件仍为 UNKNOWN，解释继续展示不确定性。测试中的固定 P4 替身则另外验证了
状态确实变化时，新状态会传给案例检索器，更新后再生成解释。

## 正式组件边界

- `Neo4jGraphGateway`：只读路由、明确数据库与事务超时，执行 P4 参数化 Cypher。
- `OpenAICompatibleStructuredLanguageModel`：OpenAI 兼容接口 + Pydantic 合同校验。
- P4 实现 `P4ComponentFactory`，接收 P1 管理的图与 LLM 端口，返回工作流组件和能力状态。
- `create_live_app()` 只负责装配，Live 模式不会静默改用 Demo；关闭时释放连接。

## P4 接入约定

现有 `casepath.api.main:app` 始终是 Demo。正式启动步骤见上文「环境配置」。

单轮P4端口顺序固定为：

```text
RuleRetriever
→ ConditionProjector
→ CaseRetriever
→ CaseComparator
→ QuestionPolicy
→ ExplanationPlanner
```

`CaseComparator`只计算一次条件熵、结果互信息、预期信息增益、排序变化、分支缩减和
案例分歧等指标，并通过`ComparisonBundle`同时交给追问与解释模块。

实现 `AnswerInterpreter.interpret(state, pending_question, answer_request)`，
返回 `AnswerInterpretation`，再注入 `SessionService`。P1 不解析法律关键词。

- 新事实 `text` 必须逐字摘自回答原文，不能把模型推论写成用户原话。
- 新事实 `source_turn`、条件 `last_updated_turn` 为历史最大轮次加一。
- 更新只能引用当前条件矩阵中已登记的 ID；新增候选条件应先由投影流程登记。
- 非 UNKNOWN 更新必须引用事实；合并后统一验证所有事实引用和来源轮次。
- 更新按 condition_id 替换整个条件记录。P4 负责历史证据聚合和冲突判断，P1 不擅自合并法律语义。
- 条件映射可使用`confidence`、`evidence`、`mapping_reasons`和`score_components`保留
  多事实支持、反对或限定关系；置信度不是胜诉概率。
- v1.3以`evidence`为权威细粒度证据；`supporting_fact_ids`只是兼容摘要，必须等于
  `evidence.fact_id`的去重集合。P4不要维护两套互相矛盾的列表。
- `ScoredReference.reasons`说明对象为何入选；`score_components`只分解总分来源。
- 原子`RuleCondition`不使用`PARTIAL`：事实只能满足、不满足、未知、冲突或不适用；
  “部分成立”应在`ConditionGroup`或条件化解释分支中表达，用户争议对应`CONFLICTING`。
- 检索对象使用`score_components`、`retrieval_channels`、`source_span_ids`和
  `graph_paths`解释总分来源，支持、限制和边界角色仍由三个候选列表明确区分。
- 服务将完整回答保存在 `DialogueTurn.answer`，并保存含选项的回答回执；
  `UserFact` 只保存 P4 识别出的事实片段，避免把同一原文保存成两条事实。
- 真实组件可通过 `create_app(service=..., capabilities=...)` 注入。
  `answer_interpreter=None` 时回答接口返回 503，不静默切换 Demo。
- 正式装配直接构造`CasePathWorkflow`并通过`build_session_service()`注入；P1通过
  `LegalGraphGateway`与`StructuredLanguageModel`管理连接和结构化调用边界，
  P4负责Cypher路径、Prompt、排序及条件判断算法。

## 一致性与运行限制

- `(session_id, question_id)` 标识一次回答。同内容重试返回原成功快照，异内容返回 409。
  同内容按模型规范化后的所有字段比较，包含 `selected_option`；不支持用重试接口修改答案。
- 快照和回执一起提交；解析、检索或校验失败不修改仓库，并发只允许一个版本胜出。
- 初始问题保存在 `QueryState.initial_query`，完整回答保存在 `DialogueTurn.answer`；
  P4 提取的事实片段使用 `UserFact.text + source_turn`。本次不扩展用户 SourceSpan 合同，
  也不提供文档原文的字符偏移核验。
- 当前最多追问 5 轮；P4 策略负责过滤已问条件。若策略仍选中已回答问题，
  Workflow 将其作为组件输出错误拒绝，不会把策略错误伪装成正常停止。
- 条件投影在案例检索前执行；案例检索器必须使用同一轮投影后的状态。
- 仅单进程、单 worker 联调；重启或热重载会清空会话。未提供磁盘持久化、
  会话过期清理、登录鉴权和用户隔离，请勿直接部署到公网或存放真实敏感案件材料。
- `/health` 只表示进程可响应；`/v1/capabilities` 才说明 Demo、内存和未接入能力。
  未配置引用核验器，不能把引用 ID 或日志当作已核验的证据。
- 检索和分化是否降级分别记录在`RetrievalBundle.degraded`与
  `ComparisonBundle.degraded`；它们与“是否继续追问”的会话状态是两个维度。
- 会话状态只有 `SessionService` 可以修改。`Workflow` 只对传入状态执行一轮分析；
  旧的 `Workflow.apply_answer()` 和无状态 Demo HTTP 接口均已删除。
- v1.1历史JSON仍可读取；新建查询、检索、解释和快照默认输出v1.3。
  P5接收新增字段前应按`contracts/CHANGELOG.md`更新Schema。

## P2 规范规则数据

P2以同级`../legal-rag`仓库固定提交中的《民法典》JSON为输入，修复已知的109条
层级错位，再生成P1公共合同格式的数据。`condition_id`是P2、P3和P4共享的稳定连接键。

```powershell
uv run python -m casepath.rule_layer --verified-on 2026-09-04 `
  --upstream-revision ce7872c7ae343e5ff860d627195ec4e72c7ef7ce
uv run python -m casepath.rule_layer --validate-only
uv run pytest tests/ingestion/laws tests/rule_layer
```

构建器先在临时目录生成并完成跨文件校验，最后才替换五个发布文件；发布中途失败会恢复旧数据。
规则条件通过`ConditionGroup.role`区分`CORE`法定核心条件和`DIAGNOSTIC`案例比较条件，
P4不得把诊断条件直接当作规则成立要件。

## 验证

```powershell
uv sync --extra api --group dev --locked
uv run pytest
uv run ruff check src tests scripts
uv run python scripts/export_schemas.py
```

HTTP 测试需要 `api` 可选依赖和开发依赖 `httpx`。测试覆盖内存隔离、并发冲突、
幂等重放、三轮交互、UNKNOWN 防重复、失败回滚、证据引用、HTTP 错误及 Schema 一致性。
当前锁定的 Starlette/httpx 组合会产生上游弃用警告，不影响测试结果；未通过过滤器隐藏警告。

## 团队规则

1. `contracts/` 由集成负责人维护，修改必须同步版本；
2. 图数据库和向量库只能实现 `ports/`，不能被业务状态机直接调用；
3. LLM 输出必须先转换为合同对象，禁止在模块间传递自由文本状态；
4. 用户未提供的信息必须是 `UNKNOWN`，不得默认为不满足；
5. 所有展示用法律主张必须携带 `citation_id` 或 `SourceSpan`；
6. 第一周禁止把多智能体框架加入关键链路。
