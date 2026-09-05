"""单进程内存会话仓库；重启即丢失，不支持多个 worker 共享。"""

from threading import Lock

from casepath.application.errors import SessionConflict, SessionNotFound
from casepath.application.models import SessionRecord


class InMemorySessionRepository:
    def __init__(self) -> None:
        self._records: dict[str, SessionRecord] = {}
        self._lock = Lock()

    @staticmethod
    def _validated_copy(record: SessionRecord) -> SessionRecord:
        # 不信任调用方通过 model_copy(update=...) 跳过验证的对象。
        return SessionRecord.model_validate(record.model_dump())

    def create(self, record: SessionRecord) -> None:
        candidate = self._validated_copy(record)
        if candidate.revision != 0:
            raise SessionConflict("新会话版本必须为 0")
        with self._lock:
            if candidate.session_id in self._records:
                raise SessionConflict("会话编号已存在")
            self._records[candidate.session_id] = candidate

    def get(self, session_id: str) -> SessionRecord | None:
        with self._lock:
            record = self._records.get(session_id)
            # 调用方对嵌套列表的修改不能直接污染仓库。
            return record.model_copy(deep=True) if record is not None else None

    def save(self, record: SessionRecord, expected_revision: int) -> None:
        candidate = self._validated_copy(record)
        with self._lock:
            current = self._records.get(candidate.session_id)
            if current is None:
                raise SessionNotFound(candidate.session_id)
            if current.revision != expected_revision or candidate.revision != expected_revision + 1:
                raise SessionConflict("会话已更新，请读取最新结果")
            # 快照和成功回执一起替换，不存在半提交状态。
            self._records[candidate.session_id] = candidate
