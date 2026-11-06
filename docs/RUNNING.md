# 安装与运行

[返回首页](../README.md)

以下命令在含 pyproject.toml 的仓库根目录执行。Python 范围按 pyproject.toml 为 >=3.11,<3.15；
前端需要可运行当前 Vite 的 Node.js/npm。uv 管理 Python 依赖、锁文件和虚拟环境，不是业务算法。

## 1. 安装与 Demo

```shell
uv sync --locked --extra api --extra live --group dev
uv run casepath-demo
uv run uvicorn casepath.api.main:app --host 127.0.0.1 --port 8000
```

main:app 是 Demo，不读取正式 LLM/Neo4j 业务路径。无需外部服务即可检查接口与基本流程。

## 2. Live 配置

首次创建配置文件（已有文件不要覆盖）：

```powershell
Copy-Item .env.example .env
```

Linux/macOS 对应：`cp -n .env.example .env`。编辑
[.env.example](../.env.example) 对应配置，不把真实密钥提交到 Git。

| 配置 | 用途 |
| --- | --- |
| CASEPATH_MODE=live | 明确启用正式装配 |
| CASEPATH_DATA_ROOT | 数据根目录，通常 data |
| CASEPATH_LLM_API_KEY / BASE_URL / MODEL | 结构化 LLM 的凭据、兼容接口和模型 |
| CASEPATH_LLM_REQUIRED | 是否要求模型配置；具体默认值见模板与配置类 |
| CASEPATH_NEO4J_URI / USER / PASSWORD / DATABASE | 在线图读取 |
| CASEPATH_NEO4J_WRITE_USER / WRITE_PASSWORD | 显式图导入使用的写入凭据 |
| CASEPATH_EMBEDDING_* / CASEPATH_VECTOR_INDEX | embedding 服务与匹配的已建向量索引 |

变量名的公共前缀按第一项展开，例如 CASEPATH_LLM_BASE_URL、CASEPATH_NEO4J_PASSWORD。
模型名称以你的服务账号实际可用配置为准，不照搬历史文档示例。

程序不会自行加载 .env，启动时必须使用 --env-file 或预先设置进程环境变量。
未配图网关时可使用内存图邻接；未启用向量时是降级召回，不能声称完整 Live 依赖已就绪。

## 3. 首次准备图与向量（仅需要时执行）

通常已有发布数据不必重新做 P2/P3 抽取。可先只校验：

```shell
uv run python -m casepath.rule_layer --validate-only
uv run python -m casepath.ingestion.cases --validate-only
```

离线图预览：

```shell
uv run python -m casepath.ingestion.graph --data-root data --output data/derived/graph/preview.json
```

以下是有副作用操作，不属于启动必跑项。确认数据库、账号、数据版本和费用后再执行：

```shell
# 写入 Neo4j，使用明确配置的写账号
uv run --env-file .env python -m casepath.ingestion.graph --import-neo4j
# 调用 embedding 服务并写向量索引，可能产生费用
uv run --env-file .env python scripts/build_vector_index.py
```

数据发布、图与向量模型/维度必须一致。不要只改模型名而复用旧模型生成的向量。
重新构建案例可能依赖同级 legal-rag 固定版本输入；来源和哈希见 data/manifests，
历史重建配方不等于每次启动步骤。

## 4. 启动正式后端

```shell
uv run --env-file .env uvicorn casepath.api.configured:create_configured_app --factory --host 127.0.0.1 --port 8000
```

不要与 Demo 同时占用 8000。默认单进程内存会话，重启会丢失会话，不添加多 worker 参数。
接口文档：http://127.0.0.1:8000/docs 。

## 5. 前端

另开终端：

```shell
cd frontend
npm ci
```

首次复制 frontend/.env.example 为 frontend/.env，设置：

```env
VITE_USE_MOCK=false
VITE_API_BASE=
```

```shell
npm run dev
```

访问 http://localhost:5173 。API_BASE 留空通过 Vite 代理到 8000；
VITE_USE_MOCK=true 不调用真实会话服务。页面全图浏览要求在线 Neo4j 及新增 graph 接口，
内存图降级不等于全图 API 可用。

## 6. 检查与常见问题

- /health 返回成功只证明 HTTP 存活；/v1/capabilities 检查模式、可用和降级状态。
- 503 需看具体组件错误与阶段日志；不要从页面 LLM LIVE 推断每次调用一定成功。
- 查询路径为空不等于不存在相关规则；未定角色不等于没有召回案例。
- 搬动仓库后旧 .venv、绝对数据路径可能失效，应检查配置并用 uv 重新同步环境。
- 不向日志输出 .env 全文；外部服务凭据失效需在配置端更新。

## 7. 本地验证命令

```shell
uv run pytest -ra
uv run ruff check src tests scripts
uv run python scripts/export_schemas.py
cd frontend
npm run typecheck
npm run build
node --test tests/*.test.mjs
```

export_schemas 会更新生成文件；测试可能含需要专用测试库的集成项。
不要把线上 Neo4j 用作可清理的测试库。上述是复现命令，不表示本次文档整理重新跑过它们。
