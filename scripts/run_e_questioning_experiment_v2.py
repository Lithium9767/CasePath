"""严格版 E0/E1 配对实验运行器。

与旧版实验脚本的关键区别：

1. 只接受已经冻结的 JSONL 状态，不从一个 Live 快照复制出“假多场景”；
2. E0 和 E1 读取完全相同的 QueryState、RetrievalBundle、ComparisonBundle、
   SessionRuntimeMeta、RuleScope；
3. Live LLM 不可用时立即失败，不回退到问题卡或确定性分支；
4. 原始运行记录与审核记录分离；没有审核文件就不计算正确率；
5. ERROR 不会被解释成 STOP，重复率按实际生成问题数计算。

冻结文件的行格式见 scripts/E_QUESTIONING_EXPERIMENT_V2.md。
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import random
import subprocess
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from pydantic import Field

from casepath.adapters.questioning.key_condition_policy import KeyConditionQuestionPolicy
from casepath.adapters.retrieval.retrieval_catalog import RuleScope, set_scope
from casepath.adapters.runtime.request_context import p4_request_context
from casepath.api.configured import create_configured_app
from casepath.application.runtime_meta import SessionRuntimeMeta
from casepath.contracts import ComparisonBundle, ContractModel, QueryState, RetrievalBundle

ROOT = Path(__file__).resolve().parents[1]


class BaselineQuestion(ContractModel):
    """E0 单次直接判断的输出合同。"""

    action: str = Field(pattern="^(ASK|STOP)$")
    condition_id: str | None = None
    question: str = Field(default="", max_length=80)
    reason: str = Field(min_length=1, max_length=200)


@dataclass(frozen=True)
class FrozenSample:
    sample_id: str
    scenario_group_id: str
    category: str
    state: QueryState
    bundle: RetrievalBundle
    comparison: ComparisonBundle
    runtime_meta: SessionRuntimeMeta
    scope: RuleScope
    catalog_context: dict[str, Any]
    gold: dict[str, Any]
    input_sha256: str


class CountingModel:
    """统计算法层实际调用次数；不把它写成供应商请求次数。"""

    def __init__(self, delegate: Any):
        self.delegate = delegate
        self.logical_calls = 0
        self.seconds = 0.0

    def generate_structured(self, **kwargs):
        started = time.perf_counter()
        self.logical_calls += 1
        try:
            return self.delegate.generate_structured(**kwargs)
        finally:
            self.seconds += time.perf_counter() - started


def encode(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if hasattr(value, "__dataclass_fields__"):
        return {
            name: encode(getattr(value, name))
            for name in value.__dataclass_fields__
        }
    if isinstance(value, (list, tuple)):
        return [encode(item) for item in value]
    if isinstance(value, dict):
        return {key: encode(item) for key, item in value.items()}
    return value


def canonical_input(sample: FrozenSample) -> str:
    payload = {
        "query_state": encode(sample.state),
        "retrieval_bundle": encode(sample.bundle),
        "comparison_bundle": encode(sample.comparison),
        "runtime_meta": encode(sample.runtime_meta),
        "rule_scope": encode(sample.scope),
        "catalog_context": encode(sample.catalog_context),
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def load_samples(path: Path) -> list[FrozenSample]:
    rows: list[FrozenSample] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        raw = json.loads(line)
        try:
            state = QueryState.model_validate(raw["query_state"])
            bundle = RetrievalBundle.model_validate(raw["retrieval_bundle"])
            comparison = ComparisonBundle.model_validate(raw["comparison_bundle"])
            meta = SessionRuntimeMeta.model_validate(raw.get("runtime_meta", {}))
            # RuleScope 是运行时 dataclass，不是 Pydantic 合同；冻结文件仍要
            # 显式重建，避免把任意 JSON 对象直接传入 E。
            scope = RuleScope(**raw["rule_scope"])
        except Exception as exc:
            raise ValueError(f"冻结样本第 {line_number} 行合同无效") from exc
        if not isinstance(raw.get("gold"), dict):
            raise TypeError(f"冻结样本 {raw.get('sample_id', line_number)} 缺少 gold")
        sample = FrozenSample(
            sample_id=raw["sample_id"],
            scenario_group_id=raw["scenario_group_id"],
            category=raw["category"],
            state=state,
            bundle=bundle,
            comparison=comparison,
            runtime_meta=meta,
            scope=scope,
            catalog_context=raw.get("catalog_context", {}),
            gold=raw["gold"],
            input_sha256=raw.get("input_sha256", ""),
        )
        actual_hash = hashlib.sha256(canonical_input(sample).encode()).hexdigest()
        if sample.input_sha256 != actual_hash:
            raise ValueError(f"冻结样本 {sample.sample_id} 输入哈希不匹配")
        if sample.gold.get("review_status") != "reviewed":
            raise ValueError(f"冻结样本 {sample.sample_id} 尚未完成金标准审核")
        required_gold = {
            "expected_action",
            "acceptable_missing_facts",
            "forbidden_reasks",
            "unable_or_declined_topics",
        }
        missing_gold = required_gold - sample.gold.keys()
        if missing_gold:
            raise ValueError(f"冻结样本 {sample.sample_id} 缺少金标准字段: {sorted(missing_gold)}")
        if sample.gold["expected_action"] not in {"ASK", "STOP"}:
            raise ValueError(f"冻结样本 {sample.sample_id} expected_action 非 ASK/STOP")
        rows.append(sample)
    if len(rows) != 20:
        raise ValueError(f"测试集必须恰好 20 条冻结状态，当前为 {len(rows)} 条")
    counts = defaultdict(int)
    for item in rows:
        counts[item.category] += 1
    expected = {"critical_ask": 5, "refinement_ask": 5, "sufficient_stop": 5, "unable_stop": 5}
    if dict(counts) != expected:
        raise ValueError(f"四类样本必须各 5 条，当前为 {dict(counts)}")
    return rows


def baseline_prompt(sample: FrozenSample) -> tuple[str, str]:
    """E0 与 E1 获得同样信息；差异只在于是否经过 E1 多阶段流程。"""
    system = (
        "你是 CasePath 的 E0 直接追问基线。根据完整输入判断是否仍有一个重要且可回答的事实缺口。"
        "如果有，返回 ASK、一个 condition_id 和一个只询问事实的普通语言问题；如果没有，返回 STOP。"
        "必须检查当前事实、完整问答历史、无法回答/拒答记录、候选条件和案例比较摘要。"
        "不得重复已经明确回答的事实，不得重问用户明确无法回答或拒绝回答的事项，"
        "不得要求用户判断法律结论。只输出符合 schema 的 JSON。"
    )
    user = json.dumps(
        {
            "query_state": encode(sample.state),
            "retrieval_bundle": encode(sample.bundle),
            "comparison_bundle": encode(sample.comparison),
            "runtime_meta": encode(sample.runtime_meta),
            "rule_scope": encode(sample.scope),
            "catalog_context": sample.catalog_context,
        },
        ensure_ascii=False,
    )
    return system, user


def run_e0(model: Any, sample: FrozenSample, repeat: int) -> dict[str, Any]:
    counter = CountingModel(model)
    started = time.perf_counter()
    try:
        system, user = baseline_prompt(sample)
        call_meta = sample.runtime_meta.model_copy(deep=True)
        with p4_request_context(call_meta):
            set_scope(sample.scope)
            output = counter.generate_structured(
                system_prompt=system,
                user_prompt=user,
                response_model=BaselineQuestion,
                stage="question",
            )
        action = output.action
        if action == "ASK" and (not output.condition_id or not output.question.strip()):
            raise ValueError("E0 ASK 缺少 condition_id 或 question")
        question = encode(output)
        error = None
        stop_reason = output.reason if action == "STOP" else None
    except Exception as exc:  # noqa: BLE001
        action, question, error, stop_reason = "ERROR", None, type(exc).__name__, None
    return {
        "sample_id": sample.sample_id,
        "scenario_group_id": sample.scenario_group_id,
        "variant": "E0",
        "repeat": repeat,
        "input_sha256": sample.input_sha256,
        "actual_action": action,
        "question": question,
        "logical_model_calls": counter.logical_calls,
        "actual_model_requests": None,
        "elapsed_seconds": time.perf_counter() - started,
        "error_type": error,
        "stop_reason": stop_reason,
    }


def run_e1(policy: KeyConditionQuestionPolicy, sample: FrozenSample, repeat: int) -> dict[str, Any]:
    counter = CountingModel(policy.language_model)
    original = policy.language_model
    policy.language_model = counter
    started = time.perf_counter()
    try:
        call_meta = sample.runtime_meta.model_copy(deep=True)
        with p4_request_context(call_meta):
            set_scope(sample.scope)
            output = policy.select(
                sample.state.model_copy(deep=True),
                sample.bundle.model_copy(deep=True),
                sample.comparison.model_copy(deep=True),
            )
        # 生成阶段耗尽预算不能伪装成“信息充分”。生产策略会把原因写入
        # request context；实验中将这类结果单独记为 ERROR，避免抬高 STOP 正确率。
        if output is None and call_meta.last_stop_reason in {
            "QUESTION_BUDGET_REACHED",
            "GENERATION_FAILED",
            "MISSING_FACT_GENERATION_FAILED",
        }:
            raise RuntimeError(f"E1 stopped because of {call_meta.last_stop_reason}")
        action = "ASK" if output is not None else "STOP"
        question = encode(output) if output is not None else None
        error = None
        stop_reason = call_meta.last_stop_reason
    except Exception as exc:  # noqa: BLE001
        action, question, error, stop_reason = "ERROR", None, type(exc).__name__, None
    finally:
        policy.language_model = original
    return {
        "sample_id": sample.sample_id,
        "scenario_group_id": sample.scenario_group_id,
        "variant": "E1",
        "repeat": repeat,
        "input_sha256": sample.input_sha256,
        "actual_action": action,
        "question": question,
        "logical_model_calls": counter.logical_calls,
        "actual_model_requests": None,
        "elapsed_seconds": time.perf_counter() - started,
        "error_type": error,
        "stop_reason": stop_reason,
    }


def save_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
        encoding="utf-8",
    )


def build_review_template(samples: list[FrozenSample], rows: list[dict[str, Any]], path: Path) -> None:
    by_id = {item.sample_id: item for item in samples}
    output = []
    for row in rows:
        sample = by_id[row["sample_id"]]
        output.append(
            {
                "review_id": f"{row['sample_id']}.{row['variant']}.r{row['repeat']}",
                "sample_id": row["sample_id"],
                "scenario_group_id": row["scenario_group_id"],
                "initial_query": sample.state.initial_query,
                "facts": [encode(item) for item in sample.state.user_facts],
                "dialogue_history": [encode(item) for item in sample.state.dialogue_history],
                "expected_action": sample.gold["expected_action"],
                "acceptable_missing_facts": sample.gold["acceptable_missing_facts"],
                "forbidden_reasks": sample.gold["forbidden_reasks"],
                "unable_or_declined_topics": sample.gold["unable_or_declined_topics"],
                "actual_action": row["actual_action"],
                "variant": row["variant"],
                "repeat": row["repeat"],
                "actual_question": row["question"],
                "input_sha256": row["input_sha256"],
                "error_type": row["error_type"],
                "logical_model_calls": row["logical_model_calls"],
                "elapsed_seconds": row["elapsed_seconds"],
                "action_correct": None,
                "target_fact_correct": None,
                "repeats_known_fact": None,
                "reasks_unable_or_declined": None,
                "bundles_extra_fact": None,
                "review_comment": "",
                "reviewer_type": "AI",
                "review_status": "pending",
            }
        )
    save_jsonl(path, output)


def score_review(path: Path, out_dir: Path, raw_path: Path | None = None) -> None:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not rows or any(item.get("review_status") != "reviewed" for item in rows):
        raise ValueError("审核文件仍有 pending，不能计算主结果")
    if any(item.get("actual_action") not in {"ASK", "STOP", "ERROR"} for item in rows):
        raise ValueError("审核文件包含非法 actual_action")
    required_review_fields = {
        "review_id",
        "sample_id",
        "variant",
        "repeat",
        "input_sha256",
        "action_correct",
        "target_fact_correct",
        "repeats_known_fact",
        "reasks_unable_or_declined",
        "bundles_extra_fact",
    }
    for item in rows:
        missing = required_review_fields - item.keys()
        if missing:
            raise ValueError(f"审核行 {item.get('review_id')} 缺少字段: {sorted(missing)}")
    if raw_path is not None:
        raw_rows = [json.loads(line) for line in raw_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        raw_ids = {
            f"{item['sample_id']}.{item['variant']}.r{item['repeat']}"
            for item in raw_rows
        }
        review_ids = {item["review_id"] for item in rows}
        if raw_ids != review_ids:
            raise ValueError("审核文件与 raw_runs.jsonl 的样本/方案/重复轮次不一致")
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for item in rows:
        groups[(item["variant"], item["expected_action"])].append(item)
    summary = []
    for (variant, expected), group in sorted(groups.items()):
        questions = [item for item in group if item["actual_action"] == "ASK"]
        errors = [item for item in group if item["actual_action"] == "ERROR"]
        def ask_quality_correct(item: dict[str, Any]) -> bool:
            if item["actual_action"] != "ASK":
                return False
            return all(
                bool(item.get(field))
                for field in ("action_correct", "target_fact_correct")
            ) and not any(
                bool(item.get(field))
                for field in (
                    "repeats_known_fact",
                    "reasks_unable_or_declined",
                    "bundles_extra_fact",
                )
            )

        summary.append(
            {
                "variant": variant,
                "expected_action": expected,
                "n": len(group),
                "n_ask": len(questions),
                "n_error": len(errors),
                "decision_accuracy": sum(bool(item["action_correct"]) for item in group) / len(group),
                "ask_quality_accuracy": (
                    sum(ask_quality_correct(item) for item in questions) / len(questions)
                    if questions else None
                ),
                "repeat_known_rate": (
                    sum(bool(item["repeats_known_fact"]) for item in questions) / len(questions)
                    if questions else None
                ),
                "reask_unable_or_declined_rate": (
                    sum(bool(item["reasks_unable_or_declined"]) for item in questions) / len(questions)
                    if questions else None
                ),
                "bundles_extra_fact_rate": (
                    sum(bool(item["bundles_extra_fact"]) for item in questions) / len(questions)
                    if questions else None
                ),
            }
        )
    with (out_dir / "summary.csv").open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=list(summary[0]))
        writer.writeheader()
        writer.writerows(summary)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--frozen", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=20260914)
    parser.add_argument("--reviewed", type=Path)
    parser.add_argument("--raw", type=Path, help="score-only 时用于校验审核行来源的 raw_runs.jsonl")
    parser.add_argument(
        "--score-only",
        action="store_true",
        help="只对已经完成的审核文件评分，不启动 Live LLM，也不重跑 E0/E1",
    )
    args = parser.parse_args()
    out_dir = args.out.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    if args.score_only:
        if args.reviewed is None:
            parser.error("--score-only 必须同时提供 --reviewed")
        if args.raw is None:
            parser.error("--score-only 必须同时提供 --raw")
        score_review(args.reviewed.resolve(), out_dir, args.raw.resolve())
        return
    if args.reviewed is not None:
        parser.error("正式运行阶段不接受 --reviewed；审核完成后请单独使用 --score-only")
    random.seed(args.seed)
    if args.frozen is None:
        parser.error("正式运行必须提供 --frozen")
    samples = load_samples(args.frozen.resolve())
    if (out_dir / "raw_runs.jsonl").exists():
        raise FileExistsError(f"输出目录已有 raw_runs.jsonl，请换新的 run_id: {out_dir}")
    app = create_configured_app()
    with TestClient(app):
        runtime = getattr(app.state, "live_runtime", None)
        if runtime is None:
            raise RuntimeError("正式实验禁止使用 Demo 应用；请将 CASEPATH_MODE 设置为 LIVE")
        policy = runtime.service.workflow.dependencies.question_policy
        if not isinstance(policy, KeyConditionQuestionPolicy) or runtime.language_model is None:
            raise RuntimeError("正式实验要求真实 Live LLM；不可回退到问题卡或无 LLM 分支")
        rows: list[dict[str, Any]] = []
        order = [(sample, variant) for sample in samples for variant in ("E0", "E1")]
        for repeat in (1, 2):
            random.shuffle(order)
            for sample, variant in order:
                result = run_e0(runtime.language_model, sample, repeat) if variant == "E0" else run_e1(policy, sample, repeat)
                rows.append(result)
                save_jsonl(out_dir / "raw_runs.jsonl", rows)
    build_review_template(samples, rows, out_dir / "blind_review.jsonl")
    meta = {
        "git_head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "seed": args.seed,
        "sample_count": len(samples),
        "run_count": len(rows),
        "source_sha256": hashlib.sha256(args.frozen.read_bytes()).hexdigest(),
        "created_at": datetime.now(UTC).isoformat(),
    }
    (out_dir / "manifest.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "STATUS.md").write_text(
        "# 等待审核\n\n`blind_review.jsonl` 尚未完成逐条语义审核，当前不生成正确率。\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
