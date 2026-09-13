"""跨轮会话运行时元数据（非公共 API）。

与 WorkflowSnapshot 原子提交：工作流成功前不落库。
投影账本、无法回答记录、纠正链放这里，不能只塞进 contextvars。
请求期间通过 ContextVar 共享给 B/E；请求结束必须 finally 清理。
"""

from __future__ import annotations

from contextvars import ContextVar, Token
from typing import Literal

from pydantic import Field

from casepath.contracts import ContractModel
from casepath.contracts.base import Identifier

ProjectionOutcome = Literal["MAPPED", "NO_RELEVANT_EVIDENCE", "FAILED"]
MAPPER_VERSION = "projection.v2"


class ProjectionLedgerEntry(ContractModel):
    """一条事实—条件映射任务的处理记录。处理后仍未知也算完成（NO_RELEVANT_EVIDENCE）。"""

    fact_id: Identifier
    condition_id: Identifier
    condition_definition_version: str = Field(min_length=1)
    mapper_version: str = Field(default=MAPPER_VERSION, min_length=1)
    fact_text_version: str = Field(default="empty", min_length=1)
    outcome: ProjectionOutcome


class CorrectionLink(ContractModel):
    """明确纠正：旧证据保留审计，但停止参与有效聚合。"""

    target_fact_id: Identifier
    replacement_fact_id: Identifier | None = None
    condition_id: Identifier | None = None
    reason: str = Field(min_length=1, max_length=200)


class SessionRuntimeMeta(ContractModel):
    """随 SessionRecord 持久化的内部元数据，不进入 P5 公共响应合同。"""

    projection_ledger: list[ProjectionLedgerEntry] = Field(default_factory=list)
    # 用户已对某条件表示无法补充；E 不得换措辞再问同一条件事实。
    unable_condition_ids: list[Identifier] = Field(default_factory=list)
    declined_condition_ids: list[Identifier] = Field(default_factory=list)
    corrections: list[CorrectionLink] = Field(default_factory=list)
    # 最近一次追问停止原因，供解释层区分“信息充分 / 无法补充 / 生成失败”。
    last_stop_reason: str | None = None


_RUNTIME: ContextVar[SessionRuntimeMeta | None] = ContextVar("casepath_runtime_meta", default=None)


def bind_runtime_meta(meta: SessionRuntimeMeta) -> Token:
    return _RUNTIME.set(meta)


def reset_runtime_meta(token: Token | None) -> None:
    if token is not None:
        _RUNTIME.reset(token)


def current_runtime_meta() -> SessionRuntimeMeta | None:
    return _RUNTIME.get()


def require_runtime_meta() -> SessionRuntimeMeta:
    meta = _RUNTIME.get()
    if meta is None:
        # 单测直接调 adapter 时可显式 bind；禁止无声新建后写回会话。
        raise RuntimeError("SessionRuntimeMeta 未绑定：须由 SessionService 在请求级注入")
    return meta


def condition_definition_version(predicate: str, label: str) -> str:
    """条件定义版本：定义文本变化后旧映射应允许重算。"""
    import hashlib

    digest = hashlib.sha256(f"{label}\n{predicate}".encode()).hexdigest()[:16]
    return f"conddef.{digest}"


def fact_text_version(text: str) -> str:
    import hashlib

    return "fact." + hashlib.sha256(text.encode()).hexdigest()[:16]


def ledger_key(entry: ProjectionLedgerEntry) -> tuple[str, str, str, str, str]:
    return (
        entry.fact_id,
        entry.condition_id,
        entry.condition_definition_version,
        entry.mapper_version,
        entry.fact_text_version,
    )


def successful_ledger_keys(meta: SessionRuntimeMeta) -> set[tuple[str, str, str, str, str]]:
    return {
        ledger_key(item)
        for item in meta.projection_ledger
        if item.outcome != "FAILED"
    }
