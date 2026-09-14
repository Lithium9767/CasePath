# A / C 检索评测

本目录保留原始 A / B / C 离线实现评测、合同 C1 / C2 新协议，以及历史 P3 的纯 BM25 主题基线。
不要把它们当成相同实验，也不要把无网络的离线实现称为启用了全部服务的线上结果。

当前源码已完成第一轮 A/C 修复；原 `ac_retrieval.facts/canonical.json` 是修复前基线，
`fixed_v3` 为当前定稿，`fixed`、`fixed_v2` 为未采用的中间实验。
同一 47 题已用于发现问题和修改算法，所以修复后成绩只能称为开发回归，不是独立盲测。
前后增益、回退、未解决问题及上线限制见根 README 的“A/C 第一轮检索修复”。

## 当前展示范围：合同 C1 / C2

第三轮新增可选 `--case-feedback`（A/C、C1、C2 prepare），只给多句叙述使用低权本地词法反馈。
命名为 `c.bm25` 的对照仍使用纯 BM25，反馈只影响候选系统，不能两边一起换算法再称对照。
最终配对使用 `c2.prf_pool.jsonl` 的 178 对及四份判断文件；新旧算法使用同一个分母。
结果和完整复现命令见 [最新优化记录](../../合同检索优化_记录.md)。工厂开关默认仍关闭，
评测必须显式启用，旧报告不重写。这里不包含 LLM / embedding 服务实测。

第二轮开发实验支持 `--case-han-unigram-weight 0.5`（C1 / C2 prepare）；默认仍为 0.25。
算法变化必须生成新池，不用旧不完整标签直接评分新 Top10。例如：

```powershell
uv run python -m casepath.evaluation.condition_benchmark prepare --inputs data/evaluation/c2_queries.part1.jsonl data/evaluation/c2_queries.part2.jsonl --scope-config data/evaluation/c2_contract_scope.json --artifact-prefix c2.u05 --previous-pool data/evaluation/c2_pool.jsonl --case-han-unigram-weight 0.5
```

本轮新池 `c2.u05_pool.jsonl` 保留原 160 对，新增 12 对并补评，共 172 对。新旧算法必须使用相同
扩展池及判断重新计分：`score --artifact-prefix c2.u05` 加三份判断文件；比较旧算法时再加
`--baseline-run data/reports/c2_pool.runs.json`。后者使用原冻结排名，不重跑旧算法、不覆盖旧报告，
同时记录排名来源哈希与评测池来源哈希。新旧的查询、案例库及数据快照不相同时拒绝配对。
评分输出请指定新文件名，详见 [本轮结果与采用决定](../../合同检索优化_记录.md)。

合同选题按领域预先确定，不按结果过滤；原来的全领域报告与题目仍保留。
原 A 合同 4 题沿用 `ac_queries.jsonl` 中 `A.contract.*`；旧 C 合同 10 题是领域以
`合同关系` 结尾的 LexRel 主题。C1/C2 是不同任务，不能把二者合并平均或作为算法前后对照。

### C1：法律关系主题召回

入口 `casepath.evaluation.relation_coverage`。LexRel 的定位是法律关系抽取，不是案例相似性基准，
因此本协议只问“能否找到含该法律关系的案例”。

- 从两份独立输入 `inputs/lexrel_service_contract.json`、`inputs/lexrel_retrieval_cases.json`
  核对 105 条案例及关系标签；构建 BM25 / 图之前限定候选库，不做检索后过滤。
- `--contract-only` 选择 10 个合同主题，排除夫妻、机动车侵权、独立追偿三个主题；
  但其案例不被额外移出 105 条候选库，同一案例允许有多种关系。
- 只用保守事实前缀作为文本；标签不放进查询或文本评分字段，图证据未宣称全局无泄漏。
- 按来源标签衡量完整性，未标注源不当作负例；该完整性并非专家对所有法律关系的穷尽判断。
- 同时报 Recall、nDCG、Precision、TopK 召回上限、来源标签覆盖率。
  例如 36 个正例时 Recall@20 的上限为 20/36，不能期望达到 100%。

```powershell
uv run python -m casepath.evaluation.relation_coverage --data-root data --contract-only --k 5 10 20 --output data/reports/c1.contracts.facts.rerun.json
```

已冻结成绩在 `data/reports/c1.contracts.facts.json`。105 条候选的新协议不能和原来 156 条混合候选
的成绩直接宣称算法提升。该成绩也不能用来声称已完成具体法律条件相似性评测。

### C2：合同条件比较检索

入口 `casepath.evaluation.condition_benchmark`。13 个来源启发的合同场景、156 条完整案例库，
160 对已判断候选，75 对达到主要正例标准（2—3 分）。

| 文件 | 用途 |
| --- | --- |
| `c2_queries.part1/part2.jsonl` | 原始 20 个编写场景，保留非合同题作来源记录，不纳入本轮展示 |
| `c2_contract_scope.json` | 首次检索前固定的 13 个纳入 ID / 7 个排除 ID 和范围理由 |
| `c2_queries.jsonl` | 冻结的 13 个合同问题、比较争点和场景来源 |
| `c2_pool.jsonl` | 每题去重候选，隐藏系统/分数/名次，以稳定哈希排序 |
| `c2_judgments.part1/part2.jsonl` | 共 160 对判断；等级、理由、原文所在字段及精确摘录 |
| `../reports/c2_pool.runs.json` | 建池时冻结的排名、候选/查询/数据/实现哈希及配置 |
| `../reports/c2.contracts.facts.json` | 逐题及汇总成绩、标签哈希、评分实现哈希和局限 |

流程：先冻结问题与范围，再取 facts 下 BM25 / 融合 / 角色候选池各 Top10、
canonical BM25 Top10 和场景来源案例的并集，最后在不看检索排名的情况下逐对核对正文。
没有向量服务，不声称池中包含 Vector Top10。来源案例也必须评分，不能自动判相关。
比较争点、来源案例 ID 和标签均不传给运行时 QueryState。

等级：3 = 核心事实/争点高度一致；2 = 有共同具体争点且重要差异可供比较；
1 = 仅背景；0 = 不具备可说明的比较价值；null = 尚无法判断。
“同属合同”“标题相似”或“同一裁判结果”均不能单独决定高分。

主要指标 `comparison_recall` 的正例是 **grade >= 2**；普通 `recall` 的 **grade >= 1** 包含背景，
仅作补充，两者不可混名。nDCG 采用 `2^grade - 1`。
两种 Recall 分母都是已评候选池里的正例，不是 156 条案例库中所有相关案例。
已评覆盖率分母是实际返回的 TopK；空排名仍计零召回，覆盖率不伪造为 100%。

评分拒绝重复/缺失/池外判断、无法在原文中找到的引文、指纹变化、池外 TopK、K 超过 10。
若某题有 null，即使不在 TopK，也不发布该题的 Recall/nDCG 点估计，并显式报告未完成题数。
本轮 160 对无 null，13 题全部计分。它是来源核对的开发级判断，**不是人工专家金标准**；
没有声称判断已无任何语义错误，也不是独立真实用户测试集。

现有冻结排名评分，不重新检索、不调用服务：

```powershell
uv run python -m casepath.evaluation.condition_benchmark score --data-root data --inputs data/evaluation/c2_judgments.part1.jsonl data/evaluation/c2_judgments.part2.jsonl --k 5 10 --output data/reports/c2.contracts.facts.rerun.json
uv run pytest tests/evaluation/test_relation_coverage.py tests/evaluation/test_condition_benchmark.py -ra
```

建池命令（仅供还原流程）：

```powershell
uv run python -m casepath.evaluation.condition_benchmark prepare --data-root data --inputs data/evaluation/c2_queries.part1.jsonl data/evaluation/c2_queries.part2.jsonl --scope-config data/evaluation/c2_contract_scope.json --depth 10
```

`prepare` 保护现有文件，不覆盖不同的池或实现指纹；算法改变后应在独立数据副本建新版本、
复核新增候选，不能替换旧池或为了得分删改旧判断。评分实现指纹与建池时的检索实现分别保存，
不把更新评分校验误称为重新跑过检索。C2 结果一旦用于调优，后续仍是开发实验。

## 本轮入口

在仓库根目录安装依赖后执行；不读取 `.env`、不启动服务、不调用付费模型：

```powershell
# 隔离标题/案由/关键词标签，只给案例文本索引输入保守截取的事实
uv run python -m casepath.evaluation.ac_retrieval --case-text-view facts --k 5 10 20 --output data/reports/ac_retrieval.fixed_v3.facts.json

# 对照实验：保留当前生产目录使用的案例检索文本
uv run python -m casepath.evaluation.ac_retrieval --case-text-view canonical --k 5 10 20 --output data/reports/ac_retrieval.fixed_v3.canonical.json

uv run pytest tests/evaluation tests/adapters/test_evaluation_trace.py -ra
```

已有不同内容的报告不能被覆盖，重新运行时应换一个输出文件名。
完整性校验失败或检索异常不能被当成成功空结果。

## 测试对象与排名口径

执行真实 `HybridCypherRuleRetriever` → `SemanticConditionProjector` →
`HybridCypherCaseRetriever`，使用 BM25 和内存图；另用同一文本视图的 BM25 做基线。
不注入正确规则 ID、案例 ID、相关性标注或预设条件答案到运行时查询。

| 输出 | 含义 |
| --- | --- |
| `a.final` | A 实际返回的规则排名，评测配置将规则上限设为最大 K |
| `a.bm25` | 同一规则目录下的 BM25 基线 |
| `c.fused` | C 的融合相关性排名，在角色筛选、交错和候选池截断之前 |
| `c.pool` | C 实际交错保留的候选池顺序，与全局相关性排名分开报告 |
| `c.bm25` | 同一案例文本视图下的 BM25 基线 |

当前 A 的每路候选预算至少为 `max(rule_top_k, 50)`（不超过语料量），最终仍只返回 `rule_top_k`。
当前 C 的全局排名是查询通道 RRF 加低位图补召回；图的遍历顺序不再被当成相关性排名。
有查询命中时仅图候选排在后面，角色内仍按证据覆盖重排；这不是已经完成语义图重排的声明。

支持、限制、边界列表以及未定角色案例另行记录；不能拼接三类列表冒充总体 Top-K。
本轮不评价三种角色本身的法律正确性，也不把“未定角色”自动当成不相关。
观测器只复制已经计算好的 ID 和分数，不改变算法排序、过滤、权重或公共 contracts。

## 测试集与标注边界

- `ac_queries.jsonl` 保存查询、模块、领域、相关 ID 与 0—3 级相关程度，另有来源依据。
- `ac_queries.manifest.json` 固定查询文件、上游数据和标注依据；标注在查看本轮检索排名前确定。
- A 使用覆盖民法典七编的编写场景，对照仓库规则及其法条依据形成策划标注（silver），不是专家穷尽金标准。
- C 的 13 个 LexRel 主题查询使用独立输入中的关系标签构造二值相关性；它们测试关系主题，不是相似裁判或胜诉预测。
- C 的最高法场景测试已知案例定位；与 LexRel 主题任务分组统计，不当作新的盲测相似案例金标准。
- “未列入正例”不等于真实不相关。未标注结果按 0 增益计分，同时报告已标注/未标注返回数量。
- 测试集用于本轮固定评测，不得根据成绩删掉难题或调整标签；若用于调参，之后不能再把同一集合称为无偏测试集。

## 指标定义

`Recall@K = 前 K 条中已标注正例数 / 全部已标注正例数`，正例指相关程度大于 0。

`DCG@K = Σ (2^相关程度 - 1) / log2(排名 + 1)`，排名从 1 开始。
`nDCG@K = DCG@K / IDCG@K`；IDCG 将同一查询的标注按相关程度降序排列后计算。
二值标签也可以计算 nDCG，不要求每个查询都人为增加分级标签。

排除自身必须在取前 K 条之前进行，同时从正例及理想排序中排除；重复、未知 ID 直接拒绝。
空排名按 0 计分；没有正例的查询显式标为不可评分，不伪造 0 或 1，也不静默消失。
汇总使用查询级宏平均，并保留各领域、各标注类型的分组结果。

## 文本和服务限制

`facts` 只在评测副本中替换案例索引文本，不修改 canonical 或图证据。
其保守截取会去掉显式裁判理由、结果后缀；历史判决之后的事实也可能被截掉，
因此不宣称这是完美的事实/结论分类器。单条文本和完整索引视图均绑定哈希。

`canonical` 沿用生产索引中的标题、案由、关键词、保守事实正文前缀及提取事实。LexRel 标签也存在于这些字段，
用同一标签作评分答案有泄漏风险，成绩仅作现状对照，不代表无泄漏语义检索质量。
两种文本视图的图证据保持不变；这不是对全部图特征进行答案泄漏排除的证明。

运行的是同一批真实模块的离线配置，而不是 Demo 替身；但不含 LLM 查询扩写、LLM 规则筛选、
付费 embedding / 向量召回或远端 Neo4j。完整服务模式须另行配置并单独报告。

## 历史 P3 主题基线

`case_retrieval_qrels.jsonl` 提供 13 个民事法律关系查询及其相关案例 ID。
相关案例集合由 LexRel 的关系类型标注推导，衡量关系类型召回；不是逐对案例相关性专家标注，也不代表裁判结果预测准确率。

重新构建案例数据后运行：

```bash
python scripts/build_case_retrieval_qrels.py
```

每条记录包含 `query_id`、`query_text`、`relation_type` 和 `relevant_case_ids`。

运行当前 BM25 基线：

```bash
python scripts/evaluate_case_recall.py --k 5 10 20
```

该脚本只跑无需外部服务的 BM25 基线，不执行完整 A / C，也不计算 nDCG。
它从关键词取相关性标签，生产检索文本又包含这些标签，结果存在标签泄漏风险。
进行向量召回测试前，需核对索引的数据发布、文本哈希与 embedding 配置；不能把本轮离线成绩当成向量成绩。
