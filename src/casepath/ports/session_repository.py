"""会话存储端口；内存和未来的数据库实现遵守同一并发约定。"""

from typing import Protocol

from casepath.application.models import SessionRecord


class SessionRepository(Protocol):
    def create(self, record: SessionRecord) -> None:
        """原子创建，重复编号必须抛出 SessionConflict。"""
        ...

    def get(self, session_id: str) -> SessionRecord | None:
        """返回隔离副本；不存在时返回 None。"""
        ...

    def save(self, record: SessionRecord, expected_revision: int) -> None:
        """原子检查旧版本并保存下一版本，同时提交回答回执。"""
        ...
