"""P4：可离线发布的双索引、版本守卫、余弦检索。构造搜索器不调用模型。"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict
from pathlib import Path

from casepath.adapters.retrieval.retrieval_catalog import MemoryHybridSearch, scored_reference
from casepath.application.errors import RetrieverUnavailable
from casepath.contracts import ScoreComponent
from casepath.ingestion.laws.jsonl import write_json
from casepath.ports.embedding import EmbeddingModel


def text_hash(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def unit_vector(values, dimensions):
    if len(values) != dimensions or any(not math.isfinite(x) for x in values):
        raise ValueError("向量维度或数值不合法")
    norm = math.sqrt(sum(x * x for x in values))
    if not math.isfinite(norm) or norm == 0:
        raise ValueError("向量范数必须为有限正数")
    return tuple(x / norm for x in values)


def documents(catalog):
    return {
        "rule": {key: item.search_text for key, item in catalog.rules.items()},
        "case": {key: item.search_text for key, item in catalog.cases.items()},
    }


class VectorIndex:
    def __init__(self, payload):
        # 防止调用者构造后改变共享索引。
        self.payload = json.loads(json.dumps(payload, allow_nan=False))

    @classmethod
    def build(cls, catalog, model: EmbeddingModel, *, batch_size=32):
        if batch_size <= 0:
            raise ValueError("batch_size 必须为正数")
        records = []
        for target, texts in documents(catalog).items():
            ids = sorted(texts)
            for start in range(0, len(ids), batch_size):
                chunk = ids[start : start + batch_size]
                vectors = model.embed([texts[key] for key in chunk])
                if len(vectors) != len(chunk):
                    raise ValueError("embedding 返回条数不一致")
                for key, vector in zip(chunk, vectors, strict=True):
                    records.append(
                        {
                            "target": target,
                            "object_id": key,
                            "text_sha256": text_hash(texts[key]),
                            "vector": unit_vector(vector, model.spec.dimensions),
                        }
                    )
        return cls(
            {
                "format_version": "1.0",
                "release_id": catalog.release_id,
                "index_version": catalog.index_version,
                "embedding": asdict(model.spec),
                "records": records,
            }
        )

    def save(self, path: Path):
        write_json(path, self.payload)

    @classmethod
    def load(cls, path: Path):
        return cls(json.loads(path.read_text("utf-8")))

    def validate(self, catalog, model):
        p = self.payload
        if (
            p["format_version"] != "1.0"
            or p["release_id"] != catalog.release_id
            or p["index_version"] != catalog.index_version
            or p["embedding"] != asdict(model.spec)
        ):
            raise ValueError("向量索引的数据、模型或版本不一致，必须重建")
        expected = {
            (target, key): text_hash(text)
            for target, texts in documents(catalog).items()
            for key, text in texts.items()
        }
        actual = {}
        vectors = {"rule": {}, "case": {}}
        for item in p["records"]:
            key = (item["target"], item["object_id"])
            if key in actual or key not in expected:
                raise ValueError("向量对象重复或不在目录中")
            actual[key] = item["text_sha256"]
            vectors[key[0]][key[1]] = unit_vector(item["vector"], model.spec.dimensions)
        if actual != expected:
            raise ValueError("向量文本哈希或覆盖范围不一致")
        return vectors


class VectorHybridSearch:
    vector_enabled = True

    def __init__(self, catalog, model: EmbeddingModel, index: VectorIndex):
        self.catalog, self.model = catalog, model
        self.spec = model.spec
        self.vectors = index.validate(catalog, model)
        self.lexical = MemoryHybridSearch(catalog)

    def search(self, text, *, target, context, top_k):
        if context != self.catalog.context() or self.model.spec != self.spec:
            raise RetrieverUnavailable("向量查询上下文或模型版本漂移")
        lexical = self.lexical.search(text, target=target, context=context, top_k=top_k)
        try:
            response = self.model.embed([text])
            if len(response) != 1:
                raise ValueError("查询向量数量错误")
            query = unit_vector(response[0], self.spec.dimensions)
            ranked = sorted(
                (
                    (sum(a * b for a, b in zip(query, vector, strict=True)), key)
                    for key, vector in self.vectors[target].items()
                ),
                key=lambda item: (-item[0], item[1]),
            )
        except Exception as exc:
            raise RetrieverUnavailable("向量查询失败，不伪装为空召回") from exc
        return lexical + [
            scored_reference(
                key,
                score,
                channels=["vector"],
                components=[ScoreComponent(name="vector", value=score, explanation="余弦相似度")],
                reasons=["语义向量通道召回"],
            )
            for score, key in ranked[:top_k]
            if score > 0
        ]
