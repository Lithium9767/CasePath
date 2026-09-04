# CasePath

CasePath 是“规范规则层 + 案例适用层”的交互式民事法律解释框架。当前仓库是第一天的契约优先骨架，不包含未经验证的法律结论。

## 当前完成

- 五个冻结接口：`RuleRecord`、`CaseRecord`、`QueryState`、`RetrievalBundle`、`ExplanationPlan`；
- 检索、条件投影、追问策略、解释规划的可替换端口；
- 不依赖 Neo4j、向量库或外部 LLM 的内存演示链路；
- JSON Schema 导出脚本和契约测试；
- FastAPI 可选入口。

## 目录职责

```text
src/casepath/contracts/   五人共同遵守的数据合同
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
- `POST /v1/demo/analyze`
- `GET /v1/contracts/{contract_name}/schema`

## P2 法条与规则数据

P2 数据源来自同级克隆的 [`litunan/legal-rag`](https://github.com/litunan/legal-rag)。从
`CasePath/` 根目录执行：

```powershell
uv run python -m casepath.rule_layer.build --verified-on 2026-09-04
uv run python -m casepath.rule_layer.build --validate-only
```

构建命令会完成以下工作：

- 以 LF 规范化文本字节校验上游法条文件 SHA-256、1,260 个连续条号和非空正文；
- 修复上游 DOCX 转换器造成的 109 条层级错位；
- 生成 `legal_sources.jsonl`、`provisions.jsonl`、`rules.jsonl` 和
  `source_spans.jsonl`；
- 校验规则引用、原文右开区间偏移和文件清单哈希；
- 对第 509、563、565、566 条保留权威来源核验记录。

生成文件位于 `data/canonical/rules/`，来源、版本、哈希、规则复核状态及适用限制位于
`data/manifests/civil_code.manifest.json`。其中 4 条基础规则为 L3；综合服务退款规则因尚未结构化
接入 2025 年预付式消费司法解释，保守标为 L2，不能单独用于判断迁店等专项解除事由或退款金额。

## 团队规则

1. `contracts/` 由集成负责人维护，修改必须同步版本；
2. 图数据库和向量库只能实现 `ports/`，不能被业务状态机直接调用；
3. LLM 输出必须先转换为合同对象，禁止在模块间传递自由文本状态；
4. 用户未提供的信息必须是 `UNKNOWN`，不得默认为不满足；
5. 所有展示用法律主张必须携带 `citation_id` 或 `SourceSpan`；
6. 第一周禁止把多智能体框架加入关键链路。

