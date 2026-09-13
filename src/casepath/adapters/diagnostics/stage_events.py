"""阶段耗时与计数日志。失败也要记录；日志失败不得拖垮业务请求。"""

from __future__ import annotations

import logging
from typing import Any

_LOG = logging.getLogger("casepath.stage")


def emit_stage_event(
    *,
    stage: str,
    duration_ms: float,
    counts: dict[str, int] | None = None,
    reason_codes: list[str] | None = None,
    identifiers: dict[str, list[str]] | None = None,
    succeeded: bool = True,
) -> None:
    """写出结构化阶段事件；默认只含 ID/计数/原因，不含用户原话或完整提示词。"""
    try:
        payload: dict[str, Any] = {
            "stage": stage,
            "duration_ms": round(duration_ms, 2),
            "succeeded": succeeded,
            "counts": counts or {},
            "reason_codes": reason_codes or [],
            "identifiers": {
                key: list(values)[:50] for key, values in (identifiers or {}).items()
            },
        }
        _LOG.info("stage_event %s", payload)
    except Exception:  # noqa: BLE001 — 观测失败不得影响主路径
        _LOG.exception("emit_stage_event failed for stage=%s", stage)
