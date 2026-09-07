from types import SimpleNamespace

import pytest

from casepath.adapters.infrastructure.embedding_client import EmbeddingClient
from casepath.application.errors import RetrieverUnavailable
from casepath.ports.embedding import EmbeddingSpec


def test_embedding_batches_reorders_and_applies_timeout():
    calls = []

    def create(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(
            data=[
                SimpleNamespace(index=i, embedding=[1.0, 0.0])
                for i in reversed(range(len(kwargs["input"])))
            ]
        )

    sdk = SimpleNamespace(embeddings=SimpleNamespace(create=create), close=lambda: None)
    model = EmbeddingClient(sdk, EmbeddingSpec("test", "r1", 2), batch_size=2, timeout_seconds=7)
    assert len(model.embed(["甲", "乙", "丙"])) == 3
    assert [len(call["input"]) for call in calls] == [2, 1]
    assert all(call["timeout"] == 7 for call in calls)


def test_embedding_provider_errors_are_sanitized():
    def fail(**kwargs):
        raise TimeoutError("secret")

    sdk = SimpleNamespace(embeddings=SimpleNamespace(create=fail))
    model = EmbeddingClient(sdk, EmbeddingSpec("test", "r1", 2))
    with pytest.raises(RetrieverUnavailable) as exc:
        model.embed(["甲"])
    assert "secret" not in str(exc.value)


def test_configured_vector_index_is_loaded_without_embedding_on_startup(tmp_path, monkeypatch):
    from pathlib import Path

    from fastapi.testclient import TestClient

    from casepath.adapters.retrieval.retrieval_catalog import load_canonical_catalog
    from casepath.adapters.retrieval.vector_search import VectorIndex
    from casepath.api.configured import create_configured_app
    from casepath.config import RuntimeSettings
    from casepath.config.embedding import EmbeddingSettings

    root = Path(__file__).resolve().parents[2] / "data"
    catalog = load_canonical_catalog(root)
    calls, closed = [], []

    class Model:
        spec = EmbeddingSpec("fake", "v1", 2)

        def embed(self, texts):
            calls.append(len(texts))
            return [[1, 0] for _ in texts]

        def close(self):
            closed.append(True)

    model = Model()
    path = tmp_path / "vectors.json"
    VectorIndex.build(catalog, model).save(path)
    calls.clear()
    monkeypatch.setenv("CASEPATH_VECTOR_INDEX", str(path))
    monkeypatch.setattr(
        RuntimeSettings, "from_env", lambda: RuntimeSettings(mode="live", data_root=root)
    )
    monkeypatch.setattr(
        EmbeddingSettings,
        "from_env",
        lambda: SimpleNamespace(index_path=path, connect=lambda: model),
    )
    with TestClient(create_configured_app()) as client:
        caps = {x["capability"]: x for x in client.get("/v1/capabilities").json()}
        assert caps["vector_search"]["available"]
        assert calls == []
    assert closed == [True]
