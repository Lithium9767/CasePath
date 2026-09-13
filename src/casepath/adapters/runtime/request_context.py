"""统一请求作用域：RuleScope / RoundEvidence / SessionRuntimeMeta。

SessionService 只进入本包装层，不直接 import reset_round 等检索实现细节。
绑定返回 token，finally 中按相反顺序恢复，支持嵌套作用域。
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from casepath.adapters.retrieval.retrieval_catalog import (
    bind_round,
    bind_scope,
    reset_round,
    reset_scope,
)
from casepath.application.runtime_meta import (
    SessionRuntimeMeta,
    bind_runtime_meta,
    reset_runtime_meta,
)


@contextmanager
def p4_request_context(runtime_meta: SessionRuntimeMeta) -> Iterator[SessionRuntimeMeta]:
    """建立本轮运行作用域；正常结束与异常退出均恢复外层 ContextVar。"""
    meta = runtime_meta
    meta_token = bind_runtime_meta(meta)
    # 显式绑定空槽位，使本轮 set_scope/set_round 仍落在可恢复的 token 链上。
    scope_token = bind_scope(None)
    round_token = bind_round(None)
    try:
        yield meta
    finally:
        reset_round(round_token)
        reset_scope(scope_token)
        reset_runtime_meta(meta_token)
