# CasePath

CasePath 是“规范规则层 + 案例适用层”的交互式民事法律解释框架。当前仓库已经完成公共合同v1.1，不包含未经验证的法律结论。

## 当前完成

- 十一个v1.1公共合同：法源、法条、规则、案例、用户状态、检索、解释、回答、错误、能力状态和工作流快照；
- `RuleCondition + ConditionGroup`规则逻辑结构，以及规则、案例、查询和解释的跨ID一致性校验；
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

## 团队规则

1. `contracts/` 由集成负责人维护，修改必须同步版本；
2. 图数据库和向量库只能实现 `ports/`，不能被业务状态机直接调用；
3. LLM 输出必须先转换为合同对象，禁止在模块间传递自由文本状态；
4. 用户未提供的信息必须是 `UNKNOWN`，不得默认为不满足；
5. 所有展示用法律主张必须携带 `citation_id` 或 `SourceSpan`；
6. 第一周禁止把多智能体框架加入关键链路。
