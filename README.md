# CasePath

CasePath 是“规范规则层 + 案例适用层”的交互式民事法律信息检索与条件化解释框架。
当前兼容十一种 v1.1 公共合同，增加 v1.2 会话合同、v1.3 P4工作流合同及多轮会话服务。
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
- `GET /v1/contracts/{contract_name}/schema`

## P2 法条与规范规则层

交付分支为 `P2`，由组长统一合并。本节集中记录 P2 的完成情况、复核证据与接入说明。

### 已完成

- 法条导入与数据生成：1 个法源、1,260 条连续且唯一的法条、5 条规则、1,268 个来源跨度。
  当前发布数据标注 4 条基础规则为 L3、1 条综合规则为 L2。
- 对齐公共 v1.1 合同：法源、法条和规则分别使用 `LegalSourceRecord`、`ProvisionRecord`
  和 `RuleRecord`；普通条件通过 `ConditionGroup` 表达 ALL/ANY，替代履行例外使用
  `RuleException`，法条内嵌全文 `SourceSpan`。
- 固定上游 revision、输入和输出 SHA-256；校验条号、非空正文、跨文件引用、原文字符
  区间和关键条文固定哈希。摄取阶段修复上游 109 条层级错位，并在 manifest 中记录。
- 构建先在 staging 目录生成并校验，发布失败可回滚 P2 拥有的五个生成文件；
  上游输入相对路径已修正为 `../legal-rag/data/laws/`。
- 提供摄取、规则、数据集和发布回滚测试，验证演示约定的 5 个事实标识均可解析。
  新增的 5 项独立验收测试使用导出 JSON Schema 校验全部 2,534 条发布记录；只复制
  `rules.jsonl` 到隔离目录，即可建立 5 条规则、9 个事实标识（含例外）和 8 个细粒度
  来源跨度的索引，验证引用闭包并构建可检索文本。对应 `jsonschema` 开发依赖已锁定。
- 完成 2026-09-05 法条与规则复核：对照两处官方全文核验全部 1,260 条正文，并检查
  5 条规则的条件、例外、法律后果与原文依据。复核日期、证据和结论绑定本次输入及
  输出哈希；修改生成日期不能更新复核日期或为变更后的规则沿用旧复核结果。
- 将法源地址更新为实际可访问的最高法全文页；综合规则的描述明确限定到第 563 条
  第一款第四项，避免被误读为覆盖全部解除路径。

代码入口：[摄取器](src/casepath/ingestion/laws/civil_code.py)、
[规则生成器](src/casepath/rule_layer/civil_code.py)、
[构建入口](src/casepath/rule_layer/build.py)、
[数据校验器](src/casepath/rule_layer/validation.py)、
[独立验收测试](tests/rule_layer/test_p2_acceptance.py)、
[官方正文比对工具](src/casepath/rule_layer/authority_compare.py)。

生成数据位于 `data/canonical/rules/`，请通过构建器更新，不手工修改 JSONL：

| 文件 | 记录数 | 用途 |
| --- | ---: | --- |
| [legal_sources.jsonl](data/canonical/rules/legal_sources.jsonl) | 1 | 法律版本与权威来源 |
| [provisions.jsonl](data/canonical/rules/provisions.jsonl) | 1,260 | 完整正文、生效日期与全文跨度 |
| [rules.jsonl](data/canonical/rules/rules.jsonl) | 5 | 4 条 L3 基础规则与 1 条 L2 综合规则 |
| [source_spans.jsonl](data/canonical/rules/source_spans.jsonl) | 1,268 | 法条全文及细粒度原文跨度 |

来源、版本、哈希、修复审计和复核状态统一记录在
[civil_code.manifest.json](data/manifests/civil_code.manifest.json)。

### 当前状态与复核结果

P2 约定范围内的代码、数据交付与法条规则复核已完成，目前无待完成的 P2 开发任务。
复核日期为 **2026-09-05**，记录编号为 `review.civil_code.2026-09-05`。

正文分别对照[最高人民法院发布的民法典全文](https://www.court.gov.cn/zixun/xiangqing/233181.html)
和[国家统计局发布的民法典全文](https://www.stats.gov.cn/gk/tjfg/xgfxfg/202503/t20250312_1958939.html)。
比对按条号进行，仅忽略 Unicode 排版空白；两个来源均为 1,260 条匹配、0 条差异，
无缺失或重复条号。原始网页 SHA-256、规范化正文摘要与逐规则结论保存在
[复核记录](data/manifests/civil_code.manifest.json)。

| 规则 | 对照条文与检查结果 |
| --- | --- |
| 全面履行与诚信履行 | 第 509 条前两款；条件与履行后果一致，通过。 |
| 催告后仍不履行的解除 | 第 563 条第一款第三项、第 565 条；保留主要债务迟延、催告、合理期限及生效程序，通过。 |
| 合同目的不能实现的解除 | 第 563 条第一款第四项、第 565 条；区分解除权与解除生效，通过。 |
| 解除后的终止履行与补救 | 第 566 条第一款；以依法解除为前提，不推导固定全额退款，通过。 |
| 服务合同费用补救综合框架 | 已明确限于第 563 条第一款第四项路径；在声明范围内通过，保留 L2。 |

替代履行是“合同目的仍可实现”的反向事实，不是适用于所有解除请求的独立法定例外。
当前解除规则不判断第 564 条行使期限；综合规则不处理预付式消费专项解除事由或计算
退款金额。这些是演示范围边界，4 条 L3 加 1 条 L2 符合计划数量要求。整项目联调由
组长统一组织，不包含在上述 P2 验证结论中。

### 合同冗余检查及处理

| 内容 | 处理与原因 |
| --- | --- |
| 法源、法条、规则三类合同 | 保留，分别表达整部法律、单条原文和结构化规则，职责不同。 |
| 内嵌 `SourceSpan` 与独立 `source_spans.jsonl` | 保留。前者支持规则独立读取，后者用于按 ID 回查；由同一构建流程生成并逐项校验。 |
| `ProvisionRef` 中的条号、标题和日期 | 属于可派生的重复信息，但当前公共合同和规则独立读取依赖它们；由法条统一生成并校验，暂不删除。 |
| 普通条件、条件组和例外 | 保留，分别表达事实、ALL/ANY 组合和阻却语义，不能互相替代。 |
| 旧 `content_hash`、原子条件 `operator`/`required` | 适配时已移除，改用 manifest 哈希、条件组和例外。 |

当前 `LegalSourceRecord` 使用 `source_type`、`effective_from`、`effective_to` 和
`official_url`；`ProvisionRecord` 内嵌来源跨度，不再输出层级、成熟度、单条哈希或
`source_span_ids`。层级修复只在摄取过程与 manifest 审计记录中保留。
正式合同以 `src/casepath/contracts/`、导出 Schema 和 `contracts/CHANGELOG.md` 为准。

### 数据接入要点

- 优先读取 `data/canonical/rules/rules.jsonl` 建规则索引；来源回查使用同目录的
  `provisions.jsonl`、`legal_sources.jsonl` 与 `source_spans.jsonl`。
- 直接复用发布数据里的 ID：条号为 `"563"`，法条 ID 为
  `law.prc.civil_code.2021.article_0563`，全文跨度 ID 为 `span.civil-code.563`。
- 事实标识需同时覆盖 `conditions` 和 `exceptions`。`cond.alternative_performance`
  保留原字符串，但现在位于 `exceptions[].exception_id`，不能作为普通 ALL 条件处理。
  例外成立时阻却对应路径；状态为 `UNKNOWN` 时不得输出确定结论。
- 片段偏移采用规范化法条正文的零基、右开字符区间，满足
  `text[start_offset:end_offset] == quote`，不表示 PDF 页码或字节位置。
- manifest 的文件 SHA-256 在计算前将 CRLF/CR 统一为 LF，以便跨平台复现；输入路径
  以 `CasePath/` 根目录为基准，输出路径以 `data/` 的父目录为基准。

规则文本区分取得解除权、依法解除生效和解除后的费用补救，不将其视为自动解除或
固定全额退款公式；适用限制以 `rules.jsonl` 与 manifest 为准。

### 验证方式与结果

在 `CasePath/` 根目录、已安装 `uv` 的环境中运行：

```powershell
uv sync --extra api --group dev --locked
uv run python -m casepath.rule_layer.build --data-root data --validate-only
uv run pytest -o "addopts=" -q -rs
```

2026-09-05 本次发布使用锁定依赖的独立 `.venv` 验证结果：数据校验 `passed`；
全仓测试 `157 passed, 1 skipped, 2 warnings`。跳过项是 Windows 创建符号链接时报
`WinError 1314`；两条警告来自 API 测试依赖的弃用提示。全仓 Ruff 静态检查与 P2 自有
Python 文件格式检查通过。新增的 5 项独立验收测试全部通过，可单独运行：

```powershell
uv run pytest tests/rule_layer/test_p2_acceptance.py
```

本次另外新增 19 项复核相关测试，覆盖官方页面正文提取、条号缺失/重复、正文差异及
复核日期、证据、结论与绑定哈希被修改时的拒绝行为，全部通过。

只校验已提交数据无需克隆上游仓库。从原始数据重建时，需要同级克隆的
[`litunan/legal-rag`](https://github.com/litunan/legal-rag)，且 revision 为
`ce7872c7ae343e5ff860d627195ec4e72c7ef7ce`。复现当前发布数据：

```powershell
uv run python -m casepath.rule_layer.build --generated-on 2026-09-05
```

需要联网重新比对官方正文时运行：

```powershell
uv run python -m casepath.rule_layer.authority_compare --data-root data
```

该命令只报告比对结果，不自动改写固定复核记录。私有 manifest 已升级为 v1.1，采用
`verified` 与 `reviewed_with_limitations` 状态，公共数据合同仍为 v1.1。
`--generated-on` 控制生成日期；旧 `--verified-on` 仅作兼容别名，不改变已记录的复核日期。
原文或规则发生变化时，需要重新对照并更新对应证据与哈希。

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
- 当前最多追问 3 轮；P4 策略负责过滤已问条件。若策略仍选中已回答问题，
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
