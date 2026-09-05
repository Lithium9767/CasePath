"""存储隔离与原子版本检查，防止并发覆盖或隐式修改会话。"""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from casepath.adapters.memory_session_repository import InMemorySessionRepository
from casepath.application.errors import SessionConflict, SessionNotFound
from casepath.application.models import SessionRecord


def test_create_get_are_deep_copies(initial):
    repo = InMemorySessionRepository()
    record = SessionRecord(session_id=initial.query_state.session_id, latest_snapshot=initial)
    repo.create(record)
    record.latest_snapshot.trace.append("污染输入")
    copy = repo.get(record.session_id)
    assert "污染输入" not in copy.latest_snapshot.trace
    copy.latest_snapshot.query_state.user_facts.clear()
    copy.latest_snapshot.trace.append("污染输出")
    assert "污染输出" not in repo.get(record.session_id).latest_snapshot.trace
    assert repo.get("missing") is None
    with pytest.raises(SessionConflict):
        repo.create(record)


def test_save_requires_existing_and_next_revision(initial):
    repo = InMemorySessionRepository()
    record = SessionRecord(session_id=initial.query_state.session_id, latest_snapshot=initial)
    with pytest.raises(SessionNotFound):
        repo.save(record, 0)
    repo.create(record)
    with pytest.raises(SessionConflict):
        repo.save(record, 0)
    record.revision = 1
    repo.save(record, 0)
    with pytest.raises(SessionConflict):
        repo.save(record, 0)
    assert repo.get(record.session_id).revision == 1


def test_concurrent_saves_have_exactly_one_winner(initial):
    repo = InMemorySessionRepository()
    record = SessionRecord(session_id=initial.query_state.session_id, latest_snapshot=initial)
    repo.create(record)
    barrier = Barrier(2)

    def save():
        candidate = repo.get(record.session_id)
        candidate.revision = 1
        barrier.wait(timeout=5)
        try:
            repo.save(candidate, 0)
            return "saved"
        except SessionConflict:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(lambda _: save(), range(2))) == ["conflict", "saved"]
    assert repo.get(record.session_id).revision == 1
