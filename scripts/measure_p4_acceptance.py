"""第一阶段真实多轮验收采样。

复用原 measure_p4 思路：包装 A–E/解释端口与 LLM，不改图谱/向量库。
按真实 next_question 继续；不固定轮次；失败即记录，不跳过报成功。
人工场景可保留原文；不向日志写入密钥。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from casepath.adapters.explanation.grounded_explanation import evidence_package
from casepath.adapters.infrastructure.structured_llm import OpenAICompatibleStructuredLanguageModel
from casepath.api.configured import create_configured_app
from casepath.application.runtime_meta import current_runtime_meta

sys.stdout.reconfigure(encoding="utf-8")
logging.basicConfig(level=logging.WARNING)

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "artifacts" / "p4_acceptance"
PROMPT_HASH = hashlib.sha256(
    (ROOT / "src/casepath/adapters/explanation/grounded_explanation.py")
    .read_text(encoding="utf-8")
    .encode()
).hexdigest()[:12]

events: list[dict[str, Any]] = []
tag: dict[str, Any] = {}


def encode(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, (list, tuple)):
        return [encode(item) for item in value]
    if isinstance(value, dict):
        return {key: encode(item) for key, item in value.items()}
    return value


def summarize_state(state: dict[str, Any] | None) -> dict[str, Any]:
    if not state:
        return {}
    return {
        "status": state.get("status"),
        "fact_ids": [item.get("fact_id") for item in state.get("user_facts", [])],
        "fact_texts": [
            {"fact_id": item.get("fact_id"), "text": item.get("text"), "source_turn": item.get("source_turn")}
            for item in state.get("user_facts", [])
        ],
        "conditions": [
            {
                "condition_id": item.get("condition_id"),
                "status": item.get("status"),
                "supporting_fact_ids": item.get("supporting_fact_ids"),
                "evidence": item.get("evidence"),
                "mapping_reasons": item.get("mapping_reasons"),
            }
            for item in state.get("condition_states", [])
        ],
        "dialogue_turns": len(state.get("dialogue_history") or []),
    }


def summarize_bundle(bundle: dict[str, Any] | None) -> dict[str, Any]:
    if not bundle:
        return {}
    return {
        "rule_ids": [item.get("object_id") for item in bundle.get("rule_refs", [])],
        "support": [item.get("object_id") for item in bundle.get("support_case_refs", [])],
        "limiting": [item.get("object_id") for item in bundle.get("limiting_case_refs", [])],
        "boundary": [item.get("object_id") for item in bundle.get("boundary_case_refs", [])],
        "fused_case_count": bundle.get("fused_case_count"),
        "undetermined_case_count": bundle.get("undetermined_case_count"),
        "undetermined_reason_summary": bundle.get("undetermined_reason_summary"),
        "degraded": bundle.get("degraded"),
        "degradation_reason": bundle.get("degradation_reason"),
    }


def summarize_question(question: Any) -> dict[str, Any] | None:
    if question is None:
        return None
    data = encode(question)
    return {
        "question_id": data.get("question_id"),
        "condition_id": data.get("condition_id"),
        "question": data.get("question"),
        "why_asked": data.get("why_asked"),
        "score_components": data.get("score_components"),
    }


def save(event: dict[str, Any]) -> None:
    events.append({**tag, **event, "recorded_at": datetime.now(UTC).isoformat()})


def flush(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(item, ensure_ascii=False) for item in events) + "\n",
        encoding="utf-8",
    )


def git_meta() -> dict[str, Any]:
    def run(args: list[str]) -> str:
        try:
            return subprocess.check_output(args, cwd=ROOT, text=True).strip()
        except (OSError, subprocess.SubprocessError):
            return "unknown"

    return {
        "git_head": run(["git", "rev-parse", "HEAD"]),
        "git_short": run(["git", "rev-parse", "--short", "HEAD"]),
        "git_status_short": run(["git", "status", "--short"]),
        "git_dirty": bool(run(["git", "status", "--porcelain"])),
    }


class Probe:
    def __init__(self, obj: Any, stage: str, method: str, input_fn: Callable | None = None):
        self.obj = obj
        self.stage = stage
        self.method = method
        self.input_fn = input_fn

    def __getattr__(self, name: str):
        target = getattr(self.obj, name)
        if name != self.method:
            return target

        def invoke(*args, **kwargs):
            started = time.perf_counter()
            payload = {
                "kind": "port",
                "stage": self.stage,
                "input_summary": self.input_fn(*args, **kwargs) if self.input_fn else None,
            }
            try:
                result = target(*args, **kwargs)
                payload.update(
                    {
                        "seconds": time.perf_counter() - started,
                        "succeeded": True,
                        "output_summary": _port_output(self.stage, result),
                    }
                )
                if self.stage == "F":
                    payload["evidence_package"] = _capture_evidence_package(*args)
                save(payload)
                return result
            except Exception as exc:
                payload.update(
                    {
                        "seconds": time.perf_counter() - started,
                        "succeeded": False,
                        "error": type(exc).__name__,
                        "error_message": str(exc)[:300],
                    }
                )
                save(payload)
                raise

        return invoke


def _port_output(stage: str, result: Any) -> Any:
    data = encode(result)
    if stage == "A":
        return {
            "rule_ids": [item.get("object_id") for item in (data or [])],
            "count": len(data or []),
        }
    if stage == "B":
        return summarize_state(data)
    if stage == "C":
        return summarize_bundle(data)
    if stage == "D":
        return {
            "ranked_condition_ids": data.get("ranked_condition_ids"),
            "degraded": data.get("degraded"),
            "degradation_reason": data.get("degradation_reason"),
            "condition_count": len(data.get("condition_comparisons") or []),
        }
    if stage == "E":
        return summarize_question(data)
    if stage == "F":
        return {
            "main_explanation": (data.get("main_explanation") or "")[:500],
            "applicable_rule_ids": data.get("applicable_rule_ids"),
            "support_case_ids": data.get("support_case_ids"),
            "limiting_case_ids": data.get("limiting_case_ids"),
            "boundary_case_ids": data.get("boundary_case_ids"),
            "unresolved_condition_ids": data.get("unresolved_condition_ids"),
            "fused_case_count": data.get("fused_case_count"),
            "undetermined_case_count": data.get("undetermined_case_count"),
            "question_stop_reason": data.get("question_stop_reason"),
        }
    return data


def _capture_evidence_package(*args) -> dict[str, Any] | None:
    try:
        # build(state, bundle, comparison)
        if len(args) < 3:
            return None
        state, bundle, comparison = args[0], args[1], args[2]
        # Probe wraps planner; catalog is on underlying object via deps — rebuild via grounded helper
        catalog = getattr(getattr(args[0], "__class__", None), "catalog", None)
        # Prefer service catalog from free variable set later
        catalog = _CATALOG
        package = evidence_package(catalog, state, bundle, comparison)
        return {
            "facts": package.get("facts"),
            "corrections": package.get("corrections"),
            "conditions": [
                {
                    "condition_id": item.get("condition_id"),
                    "status": item.get("status"),
                    "evidence": item.get("evidence"),
                }
                for item in package.get("conditions", [])
            ],
            "limitations": package.get("limitations"),
            "rule_ids": [item.get("rule_id") for item in package.get("rules", [])],
            "case_ids": [
                {"case_id": item.get("case_id"), "role": item.get("role")}
                for item in package.get("cases", [])
            ],
        }
    except Exception as exc:  # noqa: BLE001 — 采样失败不得拖垮业务
        return {"capture_error": type(exc).__name__, "message": str(exc)[:200]}


_CATALOG = None


AnswerPolicy = Callable[[dict[str, Any], dict[str, Any]], str | None]


def policy_by_condition(mapping: dict[str, str], *, default: str | None = None) -> AnswerPolicy:
    def choose(question: dict[str, Any], _body: dict[str, Any]) -> str | None:
        return mapping.get(question["condition_id"], default)

    choose.reset = lambda: None  # type: ignore[attr-defined]
    return choose


def policy_sequence(answers: list[str]) -> AnswerPolicy:
    box = {"i": 0}

    def choose(_question: dict[str, Any], _body: dict[str, Any]) -> str | None:
        if box["i"] >= len(answers):
            return None
        text = answers[box["i"]]
        box["i"] += 1
        return text

    def reset() -> None:
        box["i"] = 0

    choose.reset = reset  # type: ignore[attr-defined]
    return choose


def policy_first_then_by_condition(
    first: str,
    mapping: dict[str, str],
    *,
    default: str | None = None,
) -> AnswerPolicy:
    """首轮固定回答（如显式纠正/冲突句），之后按条件 ID 回答，避免追问顺序波动耗尽序列。"""
    box = {"used_first": False}

    def choose(question: dict[str, Any], _body: dict[str, Any]) -> str | None:
        if not box["used_first"]:
            box["used_first"] = True
            return first
        return mapping.get(question["condition_id"], default)

    def reset() -> None:
        box["used_first"] = False

    choose.reset = reset  # type: ignore[attr-defined]
    return choose


SCENARIOS: list[dict[str, Any]] = [
    {
        "id": "gym_normal",
        "family": "健身房退款",
        "variant": "normal",
        "query": "我在健身房充了5000元，店关门了，还有3000元没消费，老板不退款。",
        "answer": policy_by_condition(
            {
                "cond.contract_exists": "我办了卡，有会员协议。",
                "cond.payment_made": "我已经付款5000元，有付款记录。",
                "cond.unperformed_balance": "已上10节，剩余15节价值约3000元还没消费。",
                "cond.performance_impossible": (
                    "8月20日门锁着，25日器材搬走并贴了招租。老板微信明确说以后不开了，"
                    "没有恢复时间和其他门店。"
                ),
                "cond.alternative_performance": "没有其他门店，也没有可接受的替代课程。",
                "cond.contract_terminated": "我微信通知不再继续并要求退3000元，老板说看到了但不退。",
            },
            default=None,
        ),
        "checks": ["不反复确认已知付款", "补充后能收敛或给出停止原因"],
    },
    {
        "id": "gym_counter_reask_payment",
        "family": "健身房退款",
        "variant": "counter",
        "query": "我在健身房充了5000元，店关门了，还有3000元没消费，老板不退款。",
        "answer": policy_by_condition(
            {
                "cond.contract_exists": "我办了卡。",
                "cond.payment_made": "我之前说过了，已经交了5000元。",
                "cond.unperformed_balance": "还有没消费完的余额。",
                "cond.performance_impossible": "店已经永久关门，没有恢复安排。",
                "cond.alternative_performance": "没有替代门店。",
                "cond.contract_terminated": "我要求退款，对方不退。",
            },
            default=None,
        ),
        "checks": ["已知付款后不应循环追问付款"],
    },
    {
        "id": "training_normal",
        "family": "培训转课",
        "variant": "normal",
        "query": "我报了1万元培训班，上了一半机构停课，安排我到另一家机构继续上，但我想退款。",
        "answer": policy_by_condition(
            {
                "cond.contract_exists": "我签了20节课1万元的培训协议。",
                "cond.payment_made": "我签了20节课1万元的协议并已付款。",
                "cond.unperformed_balance": "已经上10节，剩余约5000元对应课程没上。",
                "cond.alternative_performance": (
                    "新机构在20公里外，上课从周末改成工作日白天，课程内容也不同。"
                    "我没有同意转课，也没有去新机构上课。"
                ),
                "cond.performance_impossible": "原机构已经停课，说安排转课，没有说何时恢复原班。",
                "cond.contract_terminated": "我微信提出退款，对方只同意转课，未退任何钱。",
            },
            default=None,
        ),
        "checks": ["替代服务差异被利用", "不偏离退款主线"],
    },
    {
        "id": "training_course_already_stated",
        "family": "培训转课",
        "variant": "coverage",
        # 首轮已说明课程不同：用于验收程序闸门，不应再问「课程是否一样」。
        "query": (
            "我报了1万元培训班，上了一半机构停课，安排我到另一家机构继续上，"
            "但新机构课程内容也不同，我想退款。"
        ),
        "answer": policy_by_condition(
            {
                "cond.contract_exists": "我签了20节课1万元的培训协议。",
                "cond.payment_made": "我已付款1万元。",
                "cond.unperformed_balance": "已经上10节，剩余约5000元对应课程没上。",
                "cond.alternative_performance": "新机构太远，我没有同意转课，也没去上课。",
                "cond.performance_impossible": "原机构已经停课，没有恢复时间。",
                "cond.contract_terminated": "我微信提出退款，对方只同意转课。",
            },
            default=None,
        ),
        "checks": ["已说明课程不同后不再问课程是否一样"],
    },
    {
        "id": "training_counter_unclear",
        "family": "培训转课",
        "variant": "counter",
        "query": "我报了1万元培训班，上了一半机构停课，安排我到另一家机构继续上，但我想退款。",
        "answer": policy_by_condition(
            {
                "cond.alternative_performance": "不清楚",
                "cond.performance_impossible": "不清楚",
            },
            default="不清楚",
        ),
        "checks": ["不清楚不改状态", "不换措辞重问同一条件"],
    },
    {
        "id": "inheritance_normal",
        "family": "继承咨询",
        "variant": "normal",
        "query": (
            "父亲去世了，留下房子，我和姐姐想知道怎么处理。"
            "母亲还在，爷爷奶奶都已去世，父亲只有我和姐姐两个子女。"
            "他生前好像写过遗嘱。"
        ),
        "answer": policy_by_condition(
            {
                "cond.will_exists": "我找到一张父亲亲笔写的纸，写着房子留给姐姐，有签名和日期，没有公证。",
                "cond.estate_mixed_with_spouse": "房子是父亲婚后用工资买的，产权登记在父亲名下。",
                "cond.heirs_identified": "母亲还在，爷爷奶奶都已去世，父亲只有我和姐姐两个子女。",
                "cond.forced_share": "母亲和姐姐都健在，没有人表示放弃继承。",
            },
            default="不清楚",
        ),
        "checks": ["已说明家庭成员后不再重复问子女"],
    },
    {
        "id": "inheritance_counter_repeat_family",
        "family": "继承咨询",
        "variant": "counter",
        "query": "父亲去世了，留下房子，我和姐姐想知道怎么处理，他生前好像写过遗嘱。",
        "answer": policy_sequence(
            [
                "母亲还在，爷爷奶奶都已去世，父亲只有我和姐姐两个子女。",
                "只有我和姐姐两个子女。",
                "不清楚",
            ]
        ),
        "checks": ["重复家庭信息后应停止再问同类事实"],
    },
    {
        "id": "correction_amount_normal",
        "family": "明确纠正金额",
        "variant": "normal",
        # 首轮回答显式纠正金额（不依赖系统是否先问付款条件）。
        "query": "我交了5000元培训费，已经上了10节课，店关门了，想退款。",
        "answer": policy_first_then_by_condition(
            "金额说错了，是6000元。店已经关门，没有恢复时间，也没有其他门店可继续上课。",
            {
                "cond.contract_exists": "双方签了培训协议，有书面约定。",
                "cond.payment_made": "金额说错了，是6000元，已付款。",
                "cond.unperformed_balance": "已经上了10节课，还有没上的课时。",
                "cond.performance_impossible": "店已经关门，没有恢复时间。",
                "cond.alternative_performance": "没有替代安排。",
                "cond.contract_terminated": "我想退款，还没有办完解除手续。",
            },
        ),
        "checks": ["旧金额退出映射与解释有效依据", "同段其他事实不误伤"],
    },
    {
        "id": "correction_counter_no_marker",
        "family": "明确纠正金额",
        "variant": "counter",
        "query": "我交了5000元培训费，想退款。",
        "answer": policy_first_then_by_condition(
            "我交的是6000元。双方有培训约定。",
            {
                "cond.contract_exists": "双方有培训约定。",
                "cond.payment_made": "我交的是6000元。",
                "cond.contract_terminated": "我想退款，还没有解除合同。",
                "cond.performance_impossible": "不清楚",
                "cond.alternative_performance": "不清楚",
                "cond.unperformed_balance": "不清楚",
            },
            default="不清楚",
        ),
        "checks": ["无纠正标记时不自动更正"],
    },
    {
        "id": "conflict_normal",
        "family": "新旧事实冲突",
        "variant": "normal",
        "query": "我交了5000元办了健身卡。",
        "answer": policy_first_then_by_condition(
            "其实我还没付款。我办了卡，合同成立了。",
            {
                "cond.contract_exists": "我办了卡，合同成立了。",
                "cond.payment_made": "其实我还没付款。",
                "cond.contract_terminated": "我想退卡，但还没办完手续。",
                "cond.performance_impossible": "不清楚",
                "cond.alternative_performance": "不清楚",
                "cond.unperformed_balance": "不清楚",
            },
            default="不清楚",
        ),
        "checks": ["保留冲突，不自动当作更正"],
    },
    {
        "id": "unable_normal",
        "family": "不清楚／拒绝回答",
        "variant": "normal",
        "query": "培训班关门了能不能退费，我交过钱也上过课。",
        "answer": policy_by_condition({}, default="不清楚"),
        "checks": ["不清楚不改状态", "不换措辞重问"],
    },
    {
        "id": "declined_normal",
        "family": "不清楚／拒绝回答",
        "variant": "counter",
        "query": "培训班关门了能不能退费，我交过钱也上过课。",
        "answer": policy_by_condition({}, default="暂不回答"),
        "checks": ["拒绝回答不改状态", "不再追问该条件"],
    },
]


def install_probes(service) -> None:
    global _CATALOG
    deps = service.workflow.dependencies
    _CATALOG = deps.case_comparator.catalog
    if hasattr(deps.rule_retriever, "retriever"):
        deps.rule_retriever.retriever = Probe(
            deps.rule_retriever.retriever,
            "A_pool",
            "retrieve",
            input_fn=lambda state: {"session_id": state.session_id, "query": state.initial_query[:200]},
        )
    service.workflow.dependencies = replace(
        deps,
        **{
            field: Probe(
                getattr(deps, field),
                stage,
                method,
                input_fn=input_fn,
            )
            for field, stage, method, input_fn in [
                (
                    "rule_retriever",
                    "A",
                    "retrieve",
                    lambda state: {
                        "session_id": state.session_id,
                        "query": state.initial_query[:200],
                        "fact_count": len(state.user_facts),
                    },
                ),
                (
                    "condition_projector",
                    "B",
                    "project",
                    lambda state, rule_refs: {
                        "rule_ids": [item.object_id for item in rule_refs],
                        "fact_ids": [item.fact_id for item in state.user_facts],
                        "prior_conditions": [
                            {"id": item.condition_id, "status": item.status.value}
                            for item in state.condition_states
                        ],
                    },
                ),
                (
                    "case_retriever",
                    "C",
                    "retrieve",
                    lambda state, rule_refs: {
                        "rule_ids": [item.object_id for item in rule_refs],
                        "condition_statuses": {
                            item.condition_id: item.status.value for item in state.condition_states
                        },
                    },
                ),
                (
                    "case_comparator",
                    "D",
                    "compare",
                    lambda state, bundle: {
                        "rule_ids": [item.object_id for item in bundle.rule_refs],
                        "role_counts": {
                            "support": len(bundle.support_case_refs),
                            "limiting": len(bundle.limiting_case_refs),
                            "boundary": len(bundle.boundary_case_refs),
                        },
                    },
                ),
                (
                    "question_policy",
                    "E",
                    "select",
                    lambda state, bundle, comparison: {
                        "rule_ids": [item.object_id for item in bundle.rule_refs],
                        "asked": [turn.condition_id for turn in state.dialogue_history],
                        "unknown": [
                            item.condition_id
                            for item in state.condition_states
                            if item.status.value in {"UNKNOWN", "CONFLICTING"}
                        ],
                    },
                ),
                (
                    "explanation_planner",
                    "F",
                    "build",
                    lambda state, bundle, comparison: {
                        "status": state.status.value,
                        "rule_ids": [item.object_id for item in bundle.rule_refs],
                    },
                ),
            ]
        },
    )


def install_llm_probe() -> None:
    original = OpenAICompatibleStructuredLanguageModel.generate_structured

    def measured(self, **kwargs):
        started = time.perf_counter()
        stage = kwargs.get("stage")
        user_prompt = kwargs.get("user_prompt") or ""
        event = {
            "kind": "llm",
            "stage": stage,
            "input_chars": len(user_prompt),
            "system_chars": len(kwargs.get("system_prompt") or ""),
        }
        if stage == "explanation":
            try:
                package = json.loads(user_prompt)
                event["evidence_package"] = {
                    "facts": package.get("facts"),
                    "corrections": package.get("corrections"),
                    "limitations": package.get("limitations"),
                    "conditions": [
                        {
                            "condition_id": item.get("condition_id"),
                            "status": item.get("status"),
                            "evidence_fact_ids": [
                                e.get("fact_id") for e in item.get("evidence") or []
                            ],
                        }
                        for item in package.get("conditions", [])
                    ],
                }
            except Exception as exc:  # noqa: BLE001
                event["evidence_package_error"] = type(exc).__name__
        try:
            result = original(self, **kwargs)
            event.update(
                {
                    "seconds": time.perf_counter() - started,
                    "succeeded": True,
                    "output": encode(result),
                }
            )
            save(event)
            return result
        except Exception as exc:
            event.update(
                {
                    "seconds": time.perf_counter() - started,
                    "succeeded": False,
                    "error": type(exc).__name__,
                    "error_message": str(exc)[:300],
                }
            )
            save(event)
            raise

    OpenAICompatibleStructuredLanguageModel.generate_structured = measured


def run_scenario(client: TestClient, scenario: dict[str, Any], *, repeat: int, out: Path) -> None:
    tag.update(scenario=scenario["id"], family=scenario["family"], variant=scenario["variant"], repeat=repeat)
    reset = getattr(scenario["answer"], "reset", None)
    if callable(reset):
        reset()
    save({"kind": "scenario_start", "checks": scenario["checks"], "query": scenario["query"]})
    payload = {"query": scenario["query"]}
    url = "/v1/sessions"
    turn = 0
    max_turns = 8
    while turn <= max_turns:
        tag["turn"] = turn
        started = time.perf_counter()
        response = client.post(url, json=payload)
        body = response.json() if response.content else {}
        http_event = {
            "kind": "http",
            "status": response.status_code,
            "seconds": time.perf_counter() - started,
            "request_id": response.headers.get("x-request-id"),
            "input": payload if turn == 0 else {
                "question_id": payload.get("question_id"),
                "condition_id": payload.get("condition_id"),
                "answer": payload.get("answer"),
            },
            "output_summary": {
                "session_id": (body.get("query_state") or {}).get("session_id"),
                "status": (body.get("query_state") or {}).get("status"),
                "next_question": summarize_question(body.get("next_question")),
                "state": summarize_state(body.get("query_state")),
                "retrieval": summarize_bundle(body.get("retrieval_bundle")),
                "explanation_stop": (body.get("explanation_plan") or {}).get("question_stop_reason"),
                "explanation_preview": ((body.get("explanation_plan") or {}).get("main_explanation") or "")[
                    :400
                ],
                "fused_case_count": (body.get("explanation_plan") or {}).get("fused_case_count"),
                "undetermined_case_count": (body.get("explanation_plan") or {}).get(
                    "undetermined_case_count"
                ),
            },
        }
        if response.status_code >= 400:
            http_event["error_body"] = body
            save(http_event)
            flush(out)
            print(scenario["id"], f"r{repeat}", f"t{turn}", "FAIL", response.status_code, flush=True)
            save({"kind": "scenario_end", "result": "http_error", "failed_turn": turn})
            return
        save(http_event)
        flush(out)
        question = body.get("next_question")
        print(
            scenario["id"],
            f"r{repeat}",
            f"t{turn}",
            response.status_code,
            round(http_event["seconds"], 2),
            (question or {}).get("condition_id"),
            (question or {}).get("question"),
            flush=True,
        )
        if not question:
            meta = current_runtime_meta()
            save(
                {
                    "kind": "scenario_end",
                    "result": "completed_no_question",
                    "stop_reason": (body.get("explanation_plan") or {}).get("question_stop_reason"),
                    "runtime_meta": encode(meta) if meta is not None else None,
                }
            )
            flush(out)
            return
        answer = scenario["answer"](question, body)
        if answer is None:
            save(
                {
                    "kind": "scenario_end",
                    "result": "no_scripted_answer",
                    "pending_question": summarize_question(question),
                    "note": "场景脚本未覆盖该问题；不自动编造事实",
                }
            )
            flush(out)
            print(scenario["id"], f"r{repeat}", "STOP no_scripted_answer", question["condition_id"], flush=True)
            return
        url = f"/v1/sessions/{body['query_state']['session_id']}/answers"
        payload = {
            "question_id": question["question_id"],
            "condition_id": question["condition_id"],
            "answer": answer,
        }
        turn += 1
    save({"kind": "scenario_end", "result": "max_turns_reached", "max_turns": max_turns})
    flush(out)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repeats", type=int, default=3, help="每个脚本重复次数（观察波动）")
    parser.add_argument("--only", nargs="*", help="只跑指定 scenario id")
    parser.add_argument("--families-once", action="store_true", help="每类只跑首个脚本三次")
    args = parser.parse_args()

    out = OUT_DIR / f"run_{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}.jsonl"
    install_llm_probe()
    app = create_configured_app()
    service = app.state.session_service
    install_probes(service)
    catalog = service.workflow.dependencies.case_comparator.catalog

    selected = SCENARIOS
    if args.only:
        selected = [item for item in SCENARIOS if item["id"] in set(args.only)]
    elif args.families_once:
        seen = set()
        selected = []
        for item in SCENARIOS:
            if item["family"] in seen:
                continue
            seen.add(item["family"])
            selected.append(item)

    with TestClient(app) as client:
        caps = client.get("/v1/capabilities").json()
        save(
            {
                "kind": "metadata",
                **git_meta(),
                "prompt_file_hash": PROMPT_HASH,
                "catalog_release_id": catalog.release_id,
                "catalog_index_version": catalog.index_version,
                "embedding_model": catalog.embedding_model,
                "rule_count": len(catalog.rules),
                "case_count": len(catalog.cases),
                "capabilities": caps,
                "synthetic": True,
                "note": "人工验收场景；默认不记录 API 密钥",
            }
        )
        llm = next(item for item in caps if item["capability"] == "llm")
        if not llm.get("available") or llm.get("mode") != "LIVE":
            save({"kind": "fatal", "reason": "llm_not_live", "llm": llm})
            flush(out)
            raise SystemExit(f"llm is not LIVE: {llm}")
        flush(out)
        for scenario in selected:
            for repeat in range(1, args.repeats + 1):
                run_scenario(client, scenario, repeat=repeat, out=out)
    print("SAVED", out, flush=True)


if __name__ == "__main__":
    main()
