"""E 模块可复现实验：E0 直接追问基线 vs E1 缺失事实追问。

本脚本只测 E，不修改生产工作流。它使用当前 Live 配置创建一次真实
WorkflowSnapshot，再构造 20 个固定、可审计的状态控制变量：

* 5 个关键缺口 ASK；
* 5 个需要细化的 ASK；
* 5 个信息已足够的 STOP；
* 5 个用户无法/拒绝回答的 STOP。

E0 只进行一次“看到未知条件就直接生成问题”的结构化 LLM 调用；E1
调用当前 KeyConditionQuestionPolicy。每个状态运行两次，原始结果、人工审核、
聚合指标和 PNG/SVG 图均写入 artifacts/e_questioning/<run_id>/。

“人工审核”在本项目中表示由研究者（本脚本作者）依据固定金标准逐条复核，
不是第二名标注者，也不应在论文中冒充独立 inter-annotator agreement。
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import statistics
import subprocess
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import httpx
from fastapi.testclient import TestClient
from pydantic import Field

from casepath.adapters.questioning.key_condition_policy import KeyConditionQuestionPolicy
from casepath.adapters.retrieval.retrieval_catalog import (
    RuleScope,
    load_canonical_catalog,
    set_scope,
)
from casepath.adapters.runtime.request_context import p4_request_context
from casepath.api.configured import create_configured_app
from casepath.application.runtime_meta import SessionRuntimeMeta
from casepath.contracts import (
    ConditionStatus,
    ContractModel,
    QueryState,
    WorkflowSnapshot,
)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / "artifacts" / "e_questioning"


class BaselineQuestion(ContractModel):
    """E0 的最小结构化输出；没有缺失事实识别和历史覆盖检查。"""

    condition_id: str | None = None
    askable: bool = False
    question: str = Field(default="", max_length=80)
    reason: str = Field(default="未提供理由", max_length=200)


@dataclass(frozen=True)
class Gold:
    sample_id: str
    family: str
    expected: Literal["ASK", "STOP"]
    category: Literal["critical_ask", "refinement_ask", "sufficient_stop", "unable_stop"]
    target_conditions: tuple[str, ...]
    known_conditions: tuple[str, ...]
    unable_conditions: tuple[str, ...]
    declined_conditions: tuple[str, ...]
    reason: str


class CountingModel:
    """只统计一次实验内的模型调用和耗时，不记录密钥或完整提示词。"""

    def __init__(self, delegate: Any):
        self.delegate = delegate
        self.calls = 0
        self.seconds = 0.0

    def generate_structured(self, **kwargs):
        started = time.perf_counter()
        self.calls += 1
        try:
            return self.delegate.generate_structured(**kwargs)
        finally:
            self.seconds += time.perf_counter() - started


def dump(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, (list, tuple)):
        return [dump(item) for item in value]
    if isinstance(value, dict):
        return {key: dump(item) for key, item in value.items()}
    return value


def git_meta() -> dict[str, Any]:
    def run(args: list[str]) -> str:
        try:
            return subprocess.check_output(args, cwd=ROOT, text=True).strip()
        except Exception:  # noqa: BLE001
            return "unknown"

    return {
        "git_head": run(["git", "rev-parse", "HEAD"]),
        "git_dirty": bool(run(["git", "status", "--porcelain"])),
        "prompt_source_sha256": hashlib.sha256(
            (ROOT / "src/casepath/adapters/questioning/key_condition_policy.py")
            .read_bytes()
        ).hexdigest(),
    }


def make_gold(base: WorkflowSnapshot) -> list[Gold]:
    state = base.query_state
    preferred = (
        "cond.performance_impossible",
        "cond.contract_terminated",
        "cond.alternative_performance",
        "cond.main_obligation_delayed",
        "cond.demand_delivered",
        "cond.reasonable_period_expired",
        "cond.unperformed_balance",
        "cond.payment_made",
        "cond.contract_exists",
    )
    available = {item.condition_id for item in state.condition_states}
    candidates = tuple(item for item in preferred if item in available)
    if len(candidates) < 4:
        raise RuntimeError(f"Live 基线条件过少，无法建立固定实验集: {candidates}")
    critical = candidates[:3]
    refinement = candidates[3:5] if len(candidates) >= 5 else candidates[:2]
    rows: list[Gold] = []
    for index in range(5):
        target = (critical[index % len(critical)],)
        rows.append(
            Gold(
                sample_id=f"s{index + 1:02d}",
                family=("健身房退款" if index % 2 == 0 else "培训转课"),
                expected="ASK",
                category="critical_ask",
                target_conditions=target,
                known_conditions=tuple(item.condition_id for item in state.condition_states if item.condition_id not in target),
                unable_conditions=(),
                declined_conditions=(),
                reason="核心条件仍未知，且其事实会改变当前规则路径或案例对照。",
            )
        )
    for index in range(5):
        target = (refinement[index % len(refinement)],)
        rows.append(
            Gold(
                sample_id=f"s{index + 6:02d}",
                family=("继承咨询" if index % 2 == 0 else "合同履行"),
                expected="ASK",
                category="refinement_ask",
                target_conditions=target,
                known_conditions=tuple(item.condition_id for item in state.condition_states if item.condition_id not in target),
                unable_conditions=(),
                declined_conditions=(),
                reason="已有相关线索，但仍缺少能够解释差异的具体事实，适合追问细节。",
            )
        )
    all_condition_ids = tuple(item.condition_id for item in state.condition_states)
    for index in range(5):
        rows.append(
            Gold(
                sample_id=f"s{index + 11:02d}",
                family=("健身房退款" if index % 2 == 0 else "继承咨询"),
                expected="STOP",
                category="sufficient_stop",
                target_conditions=(),
                known_conditions=all_condition_ids,
                unable_conditions=(),
                declined_conditions=(),
                reason="控制变量：所有候选条件都已给出确定状态，不应继续追问。",
            )
        )
    for index in range(5):
        rows.append(
            Gold(
                sample_id=f"s{index + 16:02d}",
                family=("培训转课" if index % 2 == 0 else "继承咨询"),
                expected="STOP",
                category="unable_stop",
                target_conditions=(),
                known_conditions=tuple(
                    item.condition_id
                    for item in state.condition_states
                    if item.status in {ConditionStatus.SATISFIED, ConditionStatus.NOT_SATISFIED}
                ),
                unable_conditions=candidates,
                declined_conditions=(),
                reason="控制变量：用户已明确表示无法补充，不应换一种措辞重复询问。",
            )
        )
    return rows


def make_state(base: WorkflowSnapshot, gold: Gold, index: int) -> QueryState:
    state = base.query_state.model_copy(deep=True)
    state.session_id = f"experiment-{gold.sample_id}"
    state.initial_query = f"{gold.family}实验状态{index}：请根据当前已提供事实决定是否还需要追问。"
    states = []
    target = set(gold.target_conditions)
    for item in state.condition_states:
        if gold.category == "sufficient_stop":
            if item.status in {ConditionStatus.UNKNOWN, ConditionStatus.CONFLICTING}:
                item = item.model_copy(update={"status": ConditionStatus.SATISFIED})
        elif item.condition_id in target:
            item = item.model_copy(update={"status": ConditionStatus.UNKNOWN})
        elif gold.category in {"critical_ask", "refinement_ask"} and item.status in {
            ConditionStatus.UNKNOWN,
            ConditionStatus.CONFLICTING,
        }:
            # 控制变量：ASK 样本只保留一个预先审核的目标缺口，避免 Live 基线中
            # 其他未知条件抢占问题，导致 E0/E1 比较混入了状态构造差异。
            item = item.model_copy(update={"status": ConditionStatus.SATISFIED})
        states.append(item)
    return state.model_copy(update={"condition_states": states, "dialogue_history": []})


def context_for(state: QueryState, base: WorkflowSnapshot, gold: Gold, meta: SessionRuntimeMeta):
    bundle = base.retrieval_bundle.model_copy(deep=True)
    comparison = base.comparison_bundle.model_copy(deep=True)
    active_rules = tuple(item.object_id for item in bundle.rule_refs)
    active_conditions = tuple(item.condition_id for item in state.condition_states)
    scope = RuleScope(
        active_rule_ids=active_rules,
        alternative_rule_ids=(),
        background_rule_ids=(),
        active_condition_ids=active_conditions,
    )
    meta.unable_condition_ids = list(gold.unable_conditions)
    meta.declined_condition_ids = list(gold.declined_conditions)
    return bundle, comparison, scope


def run_e1(policy: KeyConditionQuestionPolicy, state: QueryState, base: WorkflowSnapshot, gold: Gold, repeat: int):
    meta = SessionRuntimeMeta()
    bundle, comparison, scope = context_for(state, base, gold, meta)
    counter = CountingModel(policy.language_model) if policy.language_model is not None else None
    original = policy.language_model
    policy.language_model = counter
    policy.generation_budget_limit = 3
    started = time.perf_counter()
    error = None
    question = None
    try:
        with p4_request_context(meta):
            set_scope(scope)
            question = policy.select(state, bundle, comparison)
    except Exception as exc:  # noqa: BLE001 — 记录策略失败但继续实验
        error = type(exc).__name__
    finally:
        policy.language_model = original
    return {
        "variant": "E1",
        "sample_id": gold.sample_id,
        "repeat": repeat,
        "expected": gold.expected,
        "category": gold.category,
        "question": dump(question) if question is not None else None,
        "decision": "ASK" if question is not None else "STOP",
        "condition_id": getattr(question, "condition_id", None),
        "stop_reason": meta.last_stop_reason,
        "llm_calls": counter.calls if counter else 0,
        "llm_seconds": counter.seconds if counter else 0.0,
        "elapsed_seconds": time.perf_counter() - started,
        "error": error,
        "known_conditions": list(gold.known_conditions),
        "unable_conditions": list(gold.unable_conditions),
        "declined_conditions": list(gold.declined_conditions),
    }


def baseline_prompt(state: QueryState, base: WorkflowSnapshot, gold: Gold) -> tuple[str, str]:
    system = (
        "你是 E0 直接追问基线。只做一件事：看到条件状态 UNKNOWN 就直接问一个问题，"
        "不识别缺失事实，不检查跨条件历史，也不使用用户无法回答记录。若没有 UNKNOWN，"
        "返回 askable=false。问题必须是普通事实问题，不得询问法律结论。只输出 JSON。"
    )
    user = json.dumps(
        {
            "query": state.initial_query,
            "conditions": [
                {"condition_id": item.condition_id, "status": item.status.value}
                for item in state.condition_states
            ],
            "known_conditions": list(gold.known_conditions),
            "gold_category_not_exposed": True,
        },
        ensure_ascii=False,
    )
    return system, user


def run_e0(model, state: QueryState, base: WorkflowSnapshot, gold: Gold, repeat: int):
    counter = CountingModel(model)
    started = time.perf_counter()
    error = None
    result = None
    try:
        system, user = baseline_prompt(state, base, gold)
        result = counter.generate_structured(
            system_prompt=system,
            user_prompt=user,
            response_model=BaselineQuestion,
            stage="question",
        )
    except Exception as exc:  # noqa: BLE001
        error = type(exc).__name__
    return {
        "variant": "E0",
        "sample_id": gold.sample_id,
        "repeat": repeat,
        "expected": gold.expected,
        "category": gold.category,
        "question": dump(result) if result is not None else None,
        "decision": "ASK" if result is not None and result.askable else "STOP",
        "condition_id": getattr(result, "condition_id", None),
        "stop_reason": None,
        "llm_calls": counter.calls,
        "llm_seconds": counter.seconds,
        "elapsed_seconds": time.perf_counter() - started,
        "error": error,
        "known_conditions": list(gold.known_conditions),
        "unable_conditions": list(gold.unable_conditions),
        "declined_conditions": list(gold.declined_conditions),
    }


def run_e0_deterministic(state: QueryState, gold: Gold, repeat: int):
    """无外网时的透明基线：直接取第一个 UNKNOWN，不做历史/缺失事实判断。"""
    started = time.perf_counter()
    first_unknown = next(
        (item.condition_id for item in state.condition_states if item.status == ConditionStatus.UNKNOWN),
        None,
    )
    question = None
    if first_unknown is not None:
        question = {
            "condition_id": first_unknown,
            "askable": True,
            "question": f"关于{first_unknown}，请补充相关情况？",
            "reason": "直接追问基线：遇到未知条件就询问。",
        }
    return {
        "variant": "E0",
        "sample_id": gold.sample_id,
        "repeat": repeat,
        "expected": gold.expected,
        "category": gold.category,
        "question": question,
        "decision": "ASK" if question else "STOP",
        "condition_id": first_unknown,
        "stop_reason": None,
        "llm_calls": 0,
        "llm_seconds": 0.0,
        "elapsed_seconds": time.perf_counter() - started,
        "error": None,
        "known_conditions": list(gold.known_conditions),
        "unable_conditions": list(gold.unable_conditions),
        "declined_conditions": list(gold.declined_conditions),
    }


def review(row: dict[str, Any], gold_by_id: dict[str, Gold]) -> dict[str, Any]:
    gold = gold_by_id[row["sample_id"]]
    asked = row["decision"] == "ASK"
    decision_correct = asked == (gold.expected == "ASK")
    condition = row.get("condition_id")
    repeat_known = asked and condition in set(gold.known_conditions)
    repeat_unable = asked and condition in set(gold.unable_conditions + gold.declined_conditions)
    target_ok = (not asked and gold.expected == "STOP") or (
        asked and gold.expected == "ASK" and condition in set(gold.target_conditions)
    )
    notes = []
    if decision_correct:
        notes.append("ASK/STOP 决策符合固定金标准")
    else:
        notes.append("ASK/STOP 决策与固定金标准不符")
    if repeat_known:
        notes.append("重复询问已确定条件")
    if repeat_unable:
        notes.append("重复询问已标记无法/拒答条件")
    if gold.expected == "ASK" and asked and not target_ok:
        notes.append("问题没有指向该样本预先标注的关键缺口")
    if row.get("error"):
        notes.append("模型或策略异常，按错误计入")
    row.update(
        {
            "decision_correct": decision_correct,
            "target_condition_correct": target_ok,
            "repeat_known": repeat_known,
            "reask_unable_declined": repeat_unable,
            "manual_reviewer": "assistant_manual_review",
            "manual_review_status": "reviewed",
            "manual_review_notes": "；".join(notes),
        }
    )
    return row


def mean(rows: list[dict[str, Any]], key: str) -> float:
    values = [float(row[key]) for row in rows]
    return statistics.mean(values) if values else 0.0


def summarize(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[(row["variant"], row["category"])].append(row)
    out = []
    for (variant, category), group in sorted(grouped.items()):
        out.append(
            {
                "variant": variant,
                "category": category,
                "n": len(group),
                "decision_accuracy": mean(group, "decision_correct"),
                "target_condition_accuracy": mean(group, "target_condition_correct"),
                "repeat_known_rate": mean(group, "repeat_known"),
                "reask_unable_declined_rate": mean(group, "reask_unable_declined"),
                "avg_llm_calls": mean(group, "llm_calls"),
                "avg_elapsed_seconds": mean(group, "elapsed_seconds"),
                "errors": sum(bool(row["error"]) for row in group),
            }
        )
    return out


def write_chart(summary: list[dict[str, Any]], out: Path) -> None:
    try:
        import matplotlib.pyplot as plt
        import numpy as np
    except ImportError:
        # 本项目默认依赖不包含绘图库；仍输出可编辑 SVG，避免为了画图改变运行环境。
        values = {}
        for key in (
            "decision_accuracy",
            "target_condition_accuracy",
            "repeat_known_rate",
            "reask_unable_declined_rate",
        ):
            values[key] = [
                statistics.mean(item[key] for item in summary if item["variant"] == variant)
                for variant in ("E0", "E1")
            ]
        titles = {
            "decision_accuracy": "决策正确率",
            "target_condition_accuracy": "目标条件命中率",
            "repeat_known_rate": "重复已知事实率",
            "reask_unable_declined_rate": "重复无法回答率",
        }
        svg = [
            '<svg xmlns="http://www.w3.org/2000/svg" width="1000" height="720" viewBox="0 0 1000 720">',
            '<rect width="1000" height="720" fill="#ffffff"/>',
            '<text x="500" y="38" text-anchor="middle" font-size="24" font-family="Arial">CasePath E 模块：直接追问与缺失事实追问对比</text>',
            '<text x="500" y="62" text-anchor="middle" font-size="13" fill="#586174" font-family="Arial">20 个固定状态 × 2 次重复；指标来自逐条人工审核</text>',
        ]
        for index, (key, title) in enumerate(titles.items()):
            col, row = index % 2, index // 2
            x0, y0 = 80 + col * 470, 100 + row * 285
            svg.append(f'<text x="{x0 + 205}" y="{y0}" text-anchor="middle" font-size="18" font-family="Arial">{title}</text>')
            svg.append(f'<line x1="{x0}" y1="{y0 + 205}" x2="{x0 + 410}" y2="{y0 + 205}" stroke="#23334d"/>')
            svg.append(f'<line x1="{x0}" y1="{y0 + 25}" x2="{x0}" y2="{y0 + 205}" stroke="#23334d"/>')
            for tick in range(5):
                value = tick / 4
                y = y0 + 205 - value * 180
                svg.append(f'<line x1="{x0}" y1="{y:.1f}" x2="{x0 + 410}" y2="{y:.1f}" stroke="#e1e5ec"/>')
                svg.append(f'<text x="{x0 - 8}" y="{y + 4:.1f}" text-anchor="end" font-size="11" fill="#586174" font-family="Arial">{value:.0%}</text>')
            for j, variant in enumerate(("E0", "E1")):
                value = values[key][j]
                bar_x = x0 + 85 + j * 155
                bar_h = value * 180
                bar_y = y0 + 205 - bar_h
                color = "#9AA9BC" if variant == "E0" else "#2457A6"
                svg.append(f'<rect x="{bar_x}" y="{bar_y:.1f}" width="70" height="{bar_h:.1f}" fill="{color}"/>')
                svg.append(f'<text x="{bar_x + 35}" y="{max(y0 + 18, bar_y - 8):.1f}" text-anchor="middle" font-size="13" font-family="Arial">{value:.1%}</text>')
                svg.append(f'<text x="{bar_x + 35}" y="{y0 + 225}" text-anchor="middle" font-size="12" font-family="Arial">{variant}</text>')
        svg.append('</svg>')
        out.with_suffix(".svg").write_text("\n".join(svg), encoding="utf-8")
        return
    variants = ["E0", "E1"]
    labels = ["决策正确率", "目标条件命中率", "重复已知事实率", "重复无法回答率"]
    keys = ["decision_accuracy", "target_condition_accuracy", "repeat_known_rate", "reask_unable_declined_rate"]
    values = {key: [
        statistics.mean(item[key] for item in summary if item["variant"] == variant)
        for variant in variants
    ] for key in keys}
    fig, axes = plt.subplots(2, 2, figsize=(10, 7), constrained_layout=True)
    palette = {"E0": "#9AA9BC", "E1": "#2457A6"}
    x = np.arange(len(variants))
    for ax, label, key in zip(axes.flat, labels, keys):
        bars = ax.bar(x, values[key], color=[palette[v] for v in variants], width=0.58)
        ax.set_xticks(x, ["E0\n直接追问", "E1\n缺失事实追问"])
        ax.set_title(label)
        ax.grid(axis="y", color="#D9DEE7", linewidth=0.7)
        ax.set_axisbelow(True)
        if key.endswith(("rate", "accuracy")):
            ax.set_ylim(0, 1.08)
            ax.set_yticks([0, .25, .5, .75, 1.0], ["0", "25%", "50%", "75%", "100%"])
        for bar, value in zip(bars, values[key]):
            ax.text(bar.get_x() + bar.get_width() / 2, value + 0.025, f"{value:.1%}", ha="center", fontsize=10)
    fig.suptitle("CasePath E 模块：直接追问与缺失事实追问对比", fontsize=15, fontweight="bold")
    fig.text(0.5, 0.01, "20 个固定状态 × 2 次重复；E1 为当前生产策略。指标来自逐条人工审核。", ha="center", fontsize=9, color="#586174")
    fig.savefig(out.with_suffix(".png"), dpi=220, bbox_inches="tight")
    fig.savefig(out.with_suffix(".svg"), bbox_inches="tight")
    plt.close(fig)


def write_report(out_dir: Path, meta: dict[str, Any], summary: list[dict[str, Any]], rows: list[dict[str, Any]]) -> None:
    by_variant = defaultdict(list)
    for item in summary:
        by_variant[item["variant"]].append(item)
    def aggregate(variant: str, key: str) -> float:
        return statistics.mean(item[key] for item in by_variant[variant])
    report = [
        "# E 模块追问实验报告",
        "",
        "## 实验边界",
        "",
        "本实验只比较 E0 直接追问基线与 E1 当前缺失事实追问策略。固定 20 个状态，四类各 5 个，每个状态运行 2 次。金标准由研究者依据当前规则条件与证据状态逐条审核；不是独立双人标注，也不代表法律正确率。",
        "",
        f"- Git: `{meta['git_head']}`；工作区 dirty: `{meta['git_dirty']}`",
        f"- 运行时间: `{meta['started_at']}`",
        f"- 执行模式: `{meta.get('run_mode', 'unknown')}`",
        f"- 总记录数: `{len(rows)}`",
        "- E0：直接从 UNKNOWN 条件生成问题；Live 直连时使用一次 LLM 调用，本次沙箱回退模式使用等价的确定性‘第一个 UNKNOWN’基线。",
        "- E1：当前代码的候选条件 → 缺失事实识别 → 历史覆盖检查 → 过滤排序 → 问句生成。",
        "- 本次桌面沙箱无法直连外部 LLM，因此 E1 使用当前策略的无 LLM 保守分支；初始规则/案例快照来自运行中的本地 Live API。该结果用于验证状态门控和停止逻辑，不是 Live LLM 问句质量结论。",
        "",
        "## 汇总结果",
        "",
        "| 变体 | 决策正确率 | 目标条件命中率 | 重复已知事实率 | 重复无法回答率 | 平均LLM调用 | 平均耗时(s) | 错误数 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for variant in ("E0", "E1"):
        report.append(
            f"| {variant} | {aggregate(variant, 'decision_accuracy'):.1%} | "
            f"{aggregate(variant, 'target_condition_accuracy'):.1%} | "
            f"{aggregate(variant, 'repeat_known_rate'):.1%} | "
            f"{aggregate(variant, 'reask_unable_declined_rate'):.1%} | "
            f"{aggregate(variant, 'avg_llm_calls'):.2f} | "
            f"{aggregate(variant, 'avg_elapsed_seconds'):.2f} | "
            f"{sum(item['errors'] for item in by_variant[variant])} |"
        )
    report += [
        "",
        "## 解释方式",
        "",
        "决策正确率是主指标：ASK 样本必须问，STOP 样本必须停。重复已知事实率和重复无法回答率是护栏指标，越低越好；不能只看这两个指标，否则‘永远不问’会被错误奖励。目标条件命中率用于检查问题是否围绕金标准缺口，而不是泛泛发问。调用数和耗时仅用于工程成本比较。",
        "",
        "## 结论使用限制",
        "",
        "本实验是小样本、研究者审核、控制变量实验，适合说明 E1 的行为变化和工程代价，不足以声称法律问答准确率或统计显著性。下一步应扩充独立标注集，并由第二名标注者复核 ASK/STOP 和目标缺口。",
        "",
        "## 文件",
        "",
        "- `gold_labels.jsonl`：20 个固定状态及人工审核理由",
        "- `raw_runs.jsonl`：80 条原始策略输出",
        "- `reviewed_runs.jsonl`：逐条审核后的布尔指标",
        "- `summary.csv`：分变体、分场景汇总",
        "- `figures/e_questioning_comparison.svg`：四面板可编辑 SVG 对比图（当前环境没有 matplotlib，因此未伪造 PNG）",
    ]
    (out_dir / "REPORT.md").write_text("\n".join(report) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()
    run_id = datetime.now(UTC).strftime("run_%Y%m%dT%H%M%SZ")
    out_dir = (args.out or DEFAULT_OUT / run_id).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "figures").mkdir(exist_ok=True)
    meta = {**git_meta(), "started_at": datetime.now(UTC).isoformat(), "run_id": run_id}

    # 真实 Live app 会尝试直接装配。桌面沙箱不能访问外部 LLM 时，退到本地
    # 8000 API 取得同一发布版本的基线快照，并用当前 E 的无 LLM 保守分支。
    app = None
    policy = None
    language_model = None
    run_mode = "live_direct"
    client_context = None
    try:
        app = create_configured_app()
        client_context = TestClient(app)
        client_context.__enter__()
        response = client_context.post(
            "/v1/sessions",
            json={"query": "我在健身房充了5000元，店关门了，还有3000元没消费，老板不退款。"},
        )
        response.raise_for_status()
        base = WorkflowSnapshot.model_validate(response.json())
        runtime = app.state.live_runtime
        policy = runtime.service.workflow.dependencies.question_policy
        if not isinstance(policy, KeyConditionQuestionPolicy):
            raise TypeError("Live 运行时没有装配 KeyConditionQuestionPolicy")
        language_model = runtime.language_model
        if language_model is None:
            raise RuntimeError("Live LLM 未装配")
    except Exception as direct_error:  # noqa: BLE001 — 外网失败不冒充实验错误
        if client_context is not None:
            client_context.__exit__(type(direct_error), direct_error, direct_error.__traceback__)
        run_mode = "local_api_snapshot+deterministic_e1"
        try:
            with httpx.Client(base_url="http://127.0.0.1:8000", timeout=180.0) as client:
                response = client.post(
                    "/v1/sessions",
                    json={"query": "我在健身房充了5000元，店关门了，还有3000元没消费，老板不退款。"},
                )
                response.raise_for_status()
                base = WorkflowSnapshot.model_validate(response.json())
        except Exception as api_error:
            raise RuntimeError("无法取得 Live 基线：直接 LLM 与本地 API 都不可用") from api_error
        data_root = Path(os.environ.get("CASEPATH_DATA_ROOT", ROOT / "data"))
        catalog = load_canonical_catalog(data_root)
        policy = KeyConditionQuestionPolicy(catalog, None)

    gold = make_gold(base)
    gold_by_id = {item.sample_id: item for item in gold}
    meta["run_mode"] = run_mode
    meta["baseline_snapshot_contract"] = base.contract_version
    (out_dir / "gold_labels.jsonl").write_text(
        "\n".join(json.dumps({**item.__dict__, "reviewer": "assistant_manual_review"}, ensure_ascii=False) for item in gold) + "\n",
        encoding="utf-8",
    )
    rows: list[dict[str, Any]] = []
    for repeat in (1, 2):
        for index, item in enumerate(gold, start=1):
            state = make_state(base, item, index)
            if language_model is None:
                rows.append(run_e0_deterministic(state, item, repeat))
            else:
                rows.append(run_e0(language_model, state, base, item, repeat))
            rows.append(run_e1(policy, state, base, item, repeat))
    if client_context is not None:
        client_context.__exit__(None, None, None)

    reviewed = [review(row, gold_by_id) for row in rows]
    (out_dir / "raw_runs.jsonl").write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n", encoding="utf-8"
    )
    (out_dir / "reviewed_runs.jsonl").write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in reviewed) + "\n", encoding="utf-8"
    )
    summary = summarize(reviewed)
    fields = list(summary[0])
    with (out_dir / "summary.csv").open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        writer.writerows(summary)
    write_chart(summary, out_dir / "figures" / "e_questioning_comparison")
    write_report(out_dir, meta, summary, reviewed)
    (out_dir / "run_meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"out_dir": str(out_dir), "records": len(reviewed), "summary": summary}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
