# CasePath

CasePath 是“规范规则层 + 案例适用层”的交互式民事法律信息检索与条件化解释框架。
当前保留十一种 v1.1 公共合同，增加两种 v1.2 会话合同及可运行的多轮会话服务。
默认仍使用演示检索和保守回答记录器，不提供自动法律判断，引用尚未核验。

## 当前完成

- 十一个v1.1公共合同：法源、法条、规则、案例、用户状态、检索、解释、回答、错误、能力状态和工作流快照；
- `RuleCondition + ConditionGroup`规则逻辑结构，以及规则、案例、查询和解释的跨ID一致性校验；
- 检索、条件投影、追问策略、解释规划的可替换端口；
- 不依赖 Neo4j、向量库或外部 LLM 的内存演示链路；
- JSON Schema 导出脚本和契约测试；
- FastAPI 可选入口。
- 单进程内存会话服务：创建、读取、回答、原文保存、并发版本检查和成功请求重放。

## 目录职责

```text
src/casepath/contracts/   五人共同遵守的数据合同
src/casepath/application/ 会话记录、跨轮次服务和应用错误
src/casepath/ports/       外部数据库、检索器、LLM的抽象端口
src/casepath/workflow/    确定性状态机与模块编排
src/casepath/adapters/    内存、Neo4j、向量库、LLM等实现
src/casepath/api/         HTTP接口，不放业务规则
contracts/examples/       跨模块示例JSON
scripts/                  Schema导出等开发工具
tests/                    契约和纵向链路测试
```

## 本地运行

```powershell
uv sync --extra api --group dev
uv run casepath-demo
uv run pytest
uv run uvicorn casepath.api.main:app --reload
```

API 启动后：

- `GET /health`
- `POST /v1/sessions`
- `GET /v1/sessions/{session_id}`
- `POST /v1/sessions/{session_id}/answers`
- `GET /v1/capabilities`
- `POST /v1/demo/analyze`（遗留无状态接口，已在 OpenAPI 标记弃用）
- `GET /v1/contracts/{contract_name}/schema`

## P1 会话模块怎么阅读

按 `api/sessions.py → application/session_service.py → ports/interfaces.py →
workflow/engine.py → adapters/memory_session_repository.py` 阅读。

| 文件 | 负责的事情 |
| --- | --- |
| `contracts/session.py` | 新增创建请求和 P4 回答解释结果；不改已有 v1.1 对象 |
| `application/models.py` | 保存最新快照、状态版本和回答回执 |
| `ports/session_repository.py` | 规定创建、读取、原子版本更新接口 |
| `adapters/memory_session_repository.py` | 带锁和深拷贝的内存仓库 |
| `ports/interfaces.py` | 增加 `AnswerInterpreter`，由 P4 实现语义映射 |
| `application/session_service.py` | 校验待回答问题、调用 P4、合并证据、重跑、保存 |
| `api/sessions.py` / `api/errors.py` | 三个会话入口及统一错误响应 |
| `bootstrap.py` / `api/main.py` | 每个应用实例只装配一套仓库和服务 |

完整调用链：前端 → 会话 API → SessionService → 回答解释器 / Workflow → Repository。
`Workflow` 处理单轮分析，`SessionService` 处理跨轮次保存和一致性。

## P5 联调示例

在另一个 PowerShell 终端执行（服务启动命令见上文）：

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

## P4 接入约定

实现 `AnswerInterpreter.interpret(state, pending_question, answer_request)`，
返回 `AnswerInterpretation`，再注入 `SessionService`。P1 不解析法律关键词。

- 新事实 `text` 必须逐字摘自回答原文，不能把模型推论写成用户原话。
- 新事实 `source_turn`、条件 `last_updated_turn` 为历史最大轮次加一。
- 更新只能引用当前条件矩阵中已登记的 ID；新增候选条件应先由投影流程登记。
- 非 UNKNOWN 更新必须引用事实；合并后统一验证所有事实引用和来源轮次。
- 更新按 condition_id 替换整个条件记录。P4 负责历史证据聚合和冲突判断，P1 不擅自合并法律语义。
- 服务将完整回答保存在 `DialogueTurn.answer`，并保存含选项的回答回执；
  `UserFact` 只保存 P4 识别出的事实片段，避免把同一原文保存成两条事实。
- 真实组件可通过 `create_app(service=..., capabilities=...)` 注入。
  `answer_interpreter=None` 时回答接口返回 503，不静默切换 Demo。

## 一致性与运行限制

- `(session_id, question_id)` 标识一次回答。同内容重试返回原成功快照，异内容返回 409。
  同内容按模型规范化后的所有字段比较，包含 `selected_option`；不支持用重试接口修改答案。
- 快照和回执一起提交；解析、检索或校验失败不修改仓库，并发只允许一个版本胜出。
- 初始问题保存在 `QueryState.initial_query`，完整回答保存在 `DialogueTurn.answer`；
  P4 提取的事实片段使用 `UserFact.text + source_turn`。本次不扩展用户 SourceSpan 合同，
  也不提供文档原文的字符偏移核验。
- 当前最多追问 3 轮；P4 策略负责过滤已问条件。若策略仍选中已回答问题，
  Workflow 将其作为组件输出错误拒绝，不会把策略错误伪装成正常停止。
- 仍保留旧的“案例检索后条件投影”端口顺序。回答状态先在服务层更新，
  因而后续案例检索能看到新条件；P4-v1 初始查询的投影前置属于后续端口改造。
- 仅单进程、单 worker 联调；重启或热重载会清空会话。未提供磁盘持久化、
  会话过期清理、登录鉴权和用户隔离，请勿直接部署到公网或存放真实敏感案件材料。
- `/health` 只表示进程可响应；`/v1/capabilities` 才说明 Demo、内存和未接入能力。
  未配置引用核验器，不能把引用 ID 或日志当作已核验的证据。
- 会话状态只有 `SessionService` 可以修改。`Workflow` 只对传入状态执行一轮分析；
  旧的 `Workflow.apply_answer()` 已删除。新前端不得使用已弃用的无状态 Demo HTTP 接口。
- 十一种既有模型保持 v1.1；只有新增模型采用 v1.2。详细兼容说明见 `contracts/CHANGELOG.md`。

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
