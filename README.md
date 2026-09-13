# CasePath

CasePath 是“规范规则层 + 案例适用层”的交互式民事法律信息检索与条件化解释框架。
当前兼容历史 v1.1 公共合同，增加 v1.2 会话合同、v1.3 P4工作流/规则合同及多轮会话服务。
默认仍使用演示检索和保守回答记录器，不提供自动法律判断，引用尚未核验。

第二阶段数据交接见下文 [P2 规范规则数据](#p2-规范规则数据)：本次只调整数据与离线构建校验，未改算法、前端或公共 contracts。

## 当前完成

- 十一个v1.1公共合同：法源、法条、规则、案例、用户状态、检索、解释、回答、错误、能力状态和工作流快照；
- `RuleCondition + ConditionGroup`规则逻辑结构，以及规则、案例、查询和解释的跨ID一致性校验；
- 检索、条件投影、案例分化、追问策略、解释规划的可替换端口；
- 规则召回→条件投影→案例检索→案例分化→追问→解释的确定性主工作流；
- 不依赖 Neo4j、向量库或外部 LLM 的内存演示链路；
- JSON Schema 导出脚本和契约测试；
- FastAPI 可选入口。
- 单进程内存会话服务：创建、读取、回答、原文保存、并发版本检查和成功请求重放。
- P2规则数据：1部民法典、1,260条法条、3,272个法条原文片段和776条规则（771 L1、1 L2、4 L3）。

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

### 第二阶段数据交接（2026-09-13，结合 P3 增量）

本轮以团队 `master@8345205` 中已整合的 P2 数据为基线，提交到 `P2` 分支，由组长统一合并。
在上次 P2 提交 `030476f` 上，进一步对照了 `P3@6be749d`，逐项吸收数据工作，不整分支覆盖。
没有引入本地私有实验版，没有修改 P4 算法、追问、前端、API 或公共 contracts。

已完成的数据工作：

- 核查全部 1,260 条法条的编号连续性、唯一性、非空正文、Schema、原文跨度与引用完整性；保留原法源、法条正文和原有 5 条演示规则。
- 合并第 583、584 条中定义一致的违约条件，独立条件由 1,929 个减为 1,928 个，不删除其他损失、因果关系或限制条件。新增 3 个细粒度法条跨度，修正损失原因、提存前提的原文锚点。
- 补齐共享条件原文对应的父法条引用，合计修订 262 条规则记录；规则总数仍为 776，**不因修正引用而提升 L1/L2/L3 标签**。本轮涉及的第 570、583、584 条对照了[最高人民法院公布的民法典全文](https://www.court.gov.cn/zixun/xiangqing/233181.html)，文本哈希与变更清单保存在 manifest 的 `data_refinement` 中。
- 精炼原有 54 条案例：保留上一版金额、最终裁判与原文跨度修复；进一步修正 9 条 LexRel 的主体角色和诉请——不按关系主体顺序推断原被告，不把付款余额或抽象权利义务当作已提出的请求。未明确的主体、金额保持为空，未标注的诉请明确说明。
- 接入 P3 尚未被构建器读取的 6 条[最高法民法典典型案例](https://www.court.gov.cn/zixun/xiangqing/347181.html)，核对并补回关键经过、删去未被本案明确判令的概括性责任，区分一审日期与终审日期。它们是**编辑摘要、仅 L1**，不造深度记录和权威原文跨度。
- 完成空正文、重复 ID、去空白重复事实、重复关键词、跨数据集同 ID 原文冲突的校验，并绑定三个输入的固定哈希。正式案例为 **60 条轻量记录、54 条深度记录**；六条新增摘要可进入检索目录。
- 保留 1,140 条事实-only LexRel 本地评测语料。P3 新增的 96 条已全部包含其中，不能重复计数；相对当前 P2 正式库净增 1,131 条，可用案例联合去重为 **1,191 条**，不是线上案例数。新增独立评测入口，标签与检索文本隔离，可跑离线 BM25 或读取算法组预测排名评分。

| 正式文件 | 记录数 | 说明 |
| --- | ---: | --- |
| `data/canonical/rules/legal_sources.jsonl` | 1 | 法源和版本 |
| `data/canonical/rules/provisions.jsonl` | 1,260 | 完整法条，四位编号 |
| `data/canonical/rules/rules.jsonl` | 776 | 771 L1、1 L2、4 L3；1,928 个唯一条件 |
| `data/canonical/rules/source_spans.jsonl` | 3,272 | 可按字符偏移核对的法条片段 |
| `data/canonical/cases/cases_l1.jsonl` | 60 | 45 条 legal-rag、9 条 LexRel、6 条最高法摘要 |
| `data/canonical/cases/cases_deep.jsonl` | 54 | 原有 54 条的结构化层；六条摘要不伪装成深度案例 |

与 P3 的数量对账：P3 正式库 150 条中含 105 条 LexRel，其全部在 P2 的 1,140 条评测语料内；
所以评测语料相对 **P3** 净增是 1,035，相对 **P2** 净增是 1,131，不能混用这两个分母。
没有把 P3 的 96 条新增原文再复制上网：其中有完整长数字串/账户语境，且上游再分发许可未明确。
六条最高法摘要经修订后只保存一份输入、由构建器正式加载，不保留第二份闲置的 `additional_cases.jsonl`。

条件 ID 的唯一迁移为：

```text
cond.contract.not_or_badly_performed_583 ─┐
                                       ├→ cond.contract.nonperformance_or_nonconformity
cond.contract.not_or_badly_performed_584 ─┘
```

现有正式案例不引用这两个旧 ID；若下游另存了条件缓存，应同步刷新。
修订配方位于 `data/inputs/rules/2adc0d2c4c66953e973c4a0c57f576b5134d1a46c7d9f778813164e37dbee750.json`，
引用固定哈希的原始批次，不额外复制整份规则输入。

### 核验、重建和交接证据

在仓库根目录运行（无需 LLM、Neo4j 或付费 API）：

```powershell
uv run python -m casepath.rule_layer --validate-only
uv run python -m casepath.ingestion.cases --validate-only
uv run python -m casepath.ingestion.data_quality --data-root data --output data/reports/data_quality.stage2.json
uv run pytest -ra
uv run ruff check src tests scripts
```

从已签入的规则输入重建本轮数据：

```powershell
uv run python -m casepath.rule_layer.stage2_refinement --data-root data --check-only
uv run python -m casepath.rule_layer.stage2_refinement --data-root data
```

不要用历史五规则构建命令覆盖当前扩展版。案例需从同级 `legal-rag` 的固定原始输入重建：

```powershell
uv run python -m casepath.ingestion.cases --input ../legal-rag/data/processed/processed_cases.json --data-root data
```

固定输入提交为 `ce7872c7ae343e5ff860d627195ec4e72c7ef7ce`；输入哈希不匹配会拒绝发布。
构建先生成并校验临时产物，再发布正式文件。主要证据：

- [数据质量报告](data/reports/data_quality.stage2.json)：绑定 6 个正式 JSONL 和 2 个 manifest 的 SHA-256，列出连续编号、Schema、跨度、引用及成熟度检查；当前结构错误和父法条漏引均为 0。
- [法条与规则 manifest](data/manifests/civil_code.manifest.json)：来源、版本、固定输入、输出哈希、原核验记录与本轮修订明细。
- [案例 manifest](data/manifests/cases.manifest.json)：v7 抽取器、三份输入及输出哈希、数量与限制。
- [LexRel 构建凭据](data/reports/lexrel_retrieval.manifest.json)：上游版本、原始文件 SHA-256、实际构建数量、评测协议、输出哈希；不包含案例正文。
- [P2/P3 去重对账](data/reports/p3_data_handoff.stage2.json)：固定 P3 提交、样本 ID 与原文一致性、不同基线的净增数量及来源哈希。
- [最高法摘要来源核对](data/reports/spc_civil_code_first_batch.provenance.json)：逐案官方来源章节、编辑摘要属性及修订依据。
- [13 查询离线评测结果](data/reports/lexrel_recall.relation_type.stage2.json)：1,140 候选、仅事实文本、全部正例分母和汇总指标，不含原始案情。

本轮实测：**491 passed、2 skipped，Ruff 检查通过**；本次修改前为 395 项通过，新增 96 项回归检查。
两项跳过分别是未配置专用 Neo4j 测试库、当前 Windows 进程无符号链接权限；不是法条或案例校验跳过。
另有 2 条既有 Starlette/httpx 弃用警告。规则、案例独立验收、报告文件哈希、图预览重放均通过；
本地 LexRel 完整语料已构建并与签入的统计凭据核对一致。

代码入口：`rule_layer/stage2_refinement.py` 负责确定性规则修订，`rule_layer/expansion.py` 负责发布与验收，
`ingestion/cases/{sections,extract,cleaner}.py` 负责案例抽取，`ingestion/cases/lexrel_retrieval.py` 负责补充语料，
`ingestion/data_quality.py` 负责独立质量报告。对应回归测试在 `tests/rule_layer/` 与 `tests/ingestion/`。
本轮新增 `spc_supplement.py`（来源核对摘要加载）、`branch_handoff.py`（跨分支去重对账）、
`retrieval_evaluation.py`（隔离标签的离线评测），均在 `ingestion/cases/` 下。

### LexRel 补充数据与评测使用方式

来源：[THUNLP/LexRel](https://github.com/thunlp/LexRel)，固定提交
`5d04570483da5726d5db99d72633c3ab919b4e4b` 的 `LexRel/test.json`。
本轮未发现明确的数据再分发许可，且原文可能含个人或敏感信息，因此只提交构建代码和统计凭据，
不批量上传新增原文；`data/derived/lexrel_retrieval/` 已加入 `.gitignore`。

组员可显式从上游下载并在本地复现（不调用模型）：

```powershell
uv run python -m casepath.ingestion.cases.lexrel_retrieval --download
```

默认不会联网；也可用已下载、哈希一致的原始文件离线检查：

```powershell
uv run python -m casepath.ingestion.cases.lexrel_retrieval --input "D:\datasets\LexRel\test.json" --check-only
```

生成目录包括 `cases_l1.jsonl`、`labels.jsonl`、`qrels.jsonl`、`manifest.json`。
1,140 条记录无精确/空白归一化重复，含 103 类非空关系标签，涉及借款、担保、买卖、租赁、
婚姻、继承、侵权等领域；这是来源中的事实描述，不冒充完整裁判文书，不虚构法院、案号或裁判结果。

给算法组的协议：

1. 仅将 `cases_l1.jsonl` 的 `facts_text` 建索引；`labels.jsonl` 与 `qrels.jsonl` 只供评分，不能参与检索或查询构造。
2. 每条事实作一次查询，先剔除 `exclude_case_ids` 中的自身，再计算 Recall@K；该查询分母为全部 `relevant_case_ids`。
3. 只有 1,091 个 `evaluable=true` 查询计入平均值；49 个无正样本查询排除，其中 33 个无有效标签、16 个找不到同标签同伴。无标签样本不是已确认的不相关样本。
4. “共享至少一个关系标签”只是弱监督相关性，不是专家标注的相似判例或裁判结果金标准；标签分布不均衡，文字变体仅去首尾空白，不擅自语义合并。
5. 保留上游 test 划分；不能用其标签训练或调参后再报告为无偏测试。合并已有案例库时按 `case_id` 去除 9 个重合样本。

构建命令只生成语料；新增评测命令直接读取同一份语料，不需要加载线上目录或调用 API：

```powershell
uv run python -m casepath.ingestion.cases.retrieval_evaluation --protocol relation_type --k 5 10 20
```

`relation_type` 沿用 P3 的 13 个固定自然语言主题查询，但从独立标签文件取正例，且候选池为完整 1,140 条。
本次事实-only BM25 实测宏 Recall@5 / @10 / @20 为 **0.063266 / 0.132968 / 0.234000**。
这些是宽泛关系标签下的离线基线，不是胜诉预测准确率，也不与 P3 较小候选池、含标签检索文本的成绩直接比较。
例如“借款”查询有 536 个正例，即使前 20 条全相关，Recall@20 上限也只有 20/536。

`case_overlap` 使用每条事实作为查询，剔除自身，统计 1,091 个有正例查询；49 个无正例查询跳过。
该协议的校验与评分已有回归测试，但本机 Python 3.14.3 下，内置 BM25 全量长批次尚未跑通：
曾在第 492 个查询出现 `TypeError: 'list_iterator' object is not subscriptable`，同一查询单独运行正常，
具体原因尚未确定。因此本次没有全量 `case_overlap` 实测报告，也不把部分运行结果当作完整成绩。
可先使用上面已实测的 13 查询基线；算法组也可导出 JSONL 预测，
每行 `{"query_id":"…","ranked_case_ids":["lexrel.…"]}`，使用已通过测试的外部排名评分入口：

```powershell
uv run python -m casepath.ingestion.cases.retrieval_evaluation --protocol case_overlap --predictions predictions.jsonl --k 5 10 20
```

预测必须覆盖所有可评测查询，不能漏掉难例；空排名计零分，重复/未知 ID 拒绝，先去自身再截取前 K。
外部预测是否使用了标签，单靠评分器无法证明，结果会明确标记其检索来源未经审计。
可加 `--output data/reports/my_recall.json` 保存不含案情的结果；已有不同内容的报告拒绝覆盖，需换新文件名。

需要重跑 P3 对账时，在独立目录检出 `6be749d` 后运行（只读 P3）：

```powershell
uv run python -m casepath.ingestion.cases.branch_handoff --data-root data --p3-data-root ../CasePath-P3-review/data --output data/reports/p3_data_handoff.stage2.json
```

原始 1,140 条构建凭据固定记录的是初版 54 案例基线；本次跨分支报告按实际 P2/P3 文件计算重合和净增，
不擅自改写旧构建凭据。算法组后续仍需对正式 P4/向量检索运行自己的评测。

### 边界与后续工作

本轮“已有数据核对精炼”和“补充案例以支持召回率测试”两项数据交付已完成；不等于民法典全领域规则语义全部完善。
771 条 L1 尚未逐条提升为可靠的 L2/L3；报告保留 6 组同谓词候选和 89 条诊断条件语境提示，
它们是待核查线索，不等于 89 个已确认错误，也没有据此自动合并或改动法定要件。
多请求、多审级、周期性金额等复杂案例仍需结合全文判断，不能将结构校验通过等同于法律结论正确。

规则条件通过`ConditionGroup.role`区分`CORE`法定核心条件和`DIAGNOSTIC`案例比较条件，
P4不得把诊断条件直接当作规则成立要件。

集成后需按新数据重建图谱并按需重建向量索引。离线图预览可用：

```powershell
uv run python -m casepath.ingestion.graph --data-root data --output data/derived/graph/preview.json
```

签入的离线预览已同步：10,110 个节点、21,864 条边，
`release_id=929b1c0c2a032ff5fef01f16c5bba01e22faa9d8422aaa1ba986b014603454cc`。

本轮不连接远端 Neo4j，不调用付费 embedding，不更新部署或用户会话；旧向量文件不能代表新数据。
若启用这些服务，由集成负责人按上文 Live 配置重新导入/建索引并重启服务。
多次追问、召回算法、历史对话与前端图谱交互属于其他组员本阶段工作，不在本次修改范围内。

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
