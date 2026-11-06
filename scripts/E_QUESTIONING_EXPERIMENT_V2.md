# E0/E1 追问实验（严格版）

`run_e_questioning_experiment_v2.py` 只接受已经冻结的输入，不负责从在线 A-D 重新构造样本。
这样 E0 和 E1 使用同一份 `QueryState`、案例候选、比较结果和规则用途范围，避免每次重跑时
前面的 Live 检索漂移。

## 冻结文件

冻结文件是 UTF-8 JSONL，必须恰好 20 行，四类各 5 条：

* `critical_ask`：关键事实缺失，应询问；
* `refinement_ask`：已有部分事实但还需要一个必要细化；
* `sufficient_stop`：当前证据足以完成本轮解释，应停止；
* `unable_stop`：用户已表示不知道或拒答，剩余事项没有可回答路径，应停止。

每行至少包含：

```json
{
  "sample_id": "E001",
  "scenario_group_id": "training-01",
  "category": "critical_ask",
  "query_state": {},
  "retrieval_bundle": {},
  "comparison_bundle": {},
  "runtime_meta": {},
  "rule_scope": {
    "active_rule_ids": [],
    "alternative_rule_ids": [],
    "background_rule_ids": [],
    "active_condition_ids": []
  },
  "gold": {
    "expected_action": "ASK",
    "acceptable_missing_facts": ["替代机构的上课时间"],
    "forbidden_reasks": ["是否已经付款"],
    "unable_or_declined_topics": [],
    "rationale": "该事实会改变案例比较，但历史中尚未回答。",
    "reviewer_type": "AI",
    "review_status": "reviewed"
  },
  "input_sha256": "由脚本对输入对象计算"
}
```

事实、证据和条件状态必须互相一致；不能为了制造一个缺口而把没有原文依据的条件改成
`SATISFIED`。同一故事的连续轮次使用相同的 `scenario_group_id`，但每个样本仍保存完整状态。

`input_sha256` 是以下五个对象按排序键、无空格 JSON 序列化后的 SHA-256：
`query_state + retrieval_bundle + comparison_bundle + runtime_meta + rule_scope`。脚本会重新计算
并拒绝哈希不一致的文件。冻结后不要在运行器中重新调用 A-D。

## 运行

正式运行必须由 `create_configured_app()` 装配 Live LLM；连接失败、结构化输出失败或预算失败
都记录为 `ERROR`，不会回退到问题卡或确定性策略。先用 4 条开发样本检查模型输出，再跑 20 条
冻结样本：

```powershell
$env:PYTHONDONTWRITEBYTECODE = "1"
uv run python scripts/run_e_questioning_experiment_v2.py `
  --frozen experiments/e_questioning/frozen_samples.jsonl `
  --out artifacts/e_questioning/<run_id>
```

运行结束后只生成 `blind_review.jsonl`，不能直接计算准确率。完成逐条审核并另存为
`blind_review.reviewed.jsonl` 后，使用独立的 score-only 命令，避免重复调用 Live LLM：

```powershell
uv run python scripts/run_e_questioning_experiment_v2.py `
  --score-only `
  --reviewed artifacts/e_questioning/<run_id>/blind_review.reviewed.jsonl `
  --raw artifacts/e_questioning/<run_id>/raw_runs.jsonl `
  --out artifacts/e_questioning/<run_id>
```

运行器增量写入：

* `raw_runs.jsonl`：未附加审核字段的 E0/E1 原始输出；
* `blind_review.jsonl`：逐条审核表；
* `manifest.json`：输入哈希、Git HEAD、随机种子和运行数量；
* `STATUS.md`：审核完成前明确禁止计算主指标。

每个样本每个方案运行两次，E0/E1 顺序由 `--seed` 随机打乱。两种方案使用同一
`input_sha256`，分别记录逻辑模型调用次数和耗时。

## 审核规则

审核者逐句阅读实际问题并填写：

* `action_correct`：应问时问、应停时正常停；`ERROR` 一律不正确；
* `target_fact_correct`：命中允许的事实缺口，而不是只碰巧用了正确 condition；
* `repeats_known_fact`：是否重复已有事实；
* `reasks_unable_or_declined`：是否重问“不知道/拒答”主题；
* `bundles_extra_fact`：是否在一个问题中夹带未允许的额外问题；
* `review_comment`：判定依据。

只有所有行 `review_status=reviewed` 后，才允许用 `--reviewed` 计算 `summary.csv`。
`reviewer_type=AI` 只能表示 AI 辅助审核，不能写成独立人工盲评。

审核完成后生成四联图：

```powershell
python scripts/plot_e_questioning_results.py `
  --reviewed artifacts/e_questioning/<run_id>/blind_review.reviewed.jsonl `
  --out artifacts/e_questioning/<run_id>/e_quality_cost.svg
```

## 指标口径

```text
决策正确率 = action_correct 为真的样本数 / 该分组样本数
已知事实重复率 = repeats_known_fact 为真的 ASK 数 / 实际 ASK 数
无法/拒答重问率 = reasks_unable_or_declined 为真的 ASK 数 / 实际 ASK 数
夹带额外事实率 = bundles_extra_fact 为真的 ASK 数 / 实际 ASK 数
```

没有生成问题时分母为 0，结果为 `N/A`，不能写成 0%；`ERROR` 不得转成 `STOP`。
`logical_model_calls` 是算法层逻辑调用次数；供应商请求数无法可靠获取时保留 `null`。

旧版 `run_e_questioning_experiment.py` 与旧 artifacts 仅作审计材料，不得与本实验结果混用，
也不得覆盖旧目录。
