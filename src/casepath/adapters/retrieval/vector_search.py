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
    # L2 归一化 v/||v||；查询与文档都归一化后，点积等于余弦相似度。
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
        # 【c 离线入口】scripts/build_vector_index.py 调用；一条规则/案例保存一条向量。
        # 同时记录对象 ID、文本哈希、发布版本、模型配置、维度，不是生成图关系。
        # 【离线建库】批量调用 embedding，会产生模型调用；普通 Live 提问不走这里。
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
        # 当前保存本地 JSON 文件，rule/case 用 target 区分，不是 Neo4j 原生向量索引。
        write_json(path, self.payload)

    @classmethod
    def load(cls, path: Path):
        # 【Live 启动】只读取已发布文件，不为全库重新生成向量。
        return cls(json.loads(path.read_text("utf-8")))

    def validate(self, catalog, model):
        # 【Live 装配会执行】检查数据、模型、维度及文本哈希，防止旧向量配新数据。
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

    def __init__(
        self, catalog, model: EmbeddingModel, index: VectorIndex, *, case_feedback: bool = False,
    ):
        self.catalog, self.model = catalog, model
        self.spec = model.spec
        # 已经建立好的index向量索引
        self.vectors = index.validate(catalog, model)
        # 在建立一个BM25的检索器
        self.lexical = MemoryHybridSearch(catalog, case_feedback=case_feedback)
        # 因此 MemoryHybridSearch 不是“只给 Demo 用”；混合检索仍依赖其关键词通道。

    def search(self, text, *, target, context, top_k):
        # 【在线查询】仅为本次查询生成向量，与已加载的规则/案例向量比较。
        if context != self.catalog.context() or self.model.spec != self.spec:
            raise RetrieverUnavailable("向量查询上下文或模型版本漂移")
        # BM25检索
        lexical = self.lexical.search(text, target=target, context=context, top_k=top_k)
        try:
            # 把问题文本转换成向量
            response = self.model.embed([text])
            if len(response) != 1:
                raise ValueError("查询向量数量错误")
            # 查询向量归一化
            query = unit_vector(response[0], self.spec.dimensions)
            # 查询向量 和 每条规则/案例向量 的点积。
            # 因为两边都做了单位归一化，所以这个点积就是余弦相似度。
            # 相似度从高到低排序
            ranked = sorted(
                # 【c 在线查询】遍历当前 target 全部向量、计算点积、排序取 top_k。
                # 这是精确线性扫描，不是 FAISS/HNSW 等近似最近邻检索。
                (
                    (sum(a * b for a, b in zip(query, vector, strict=True)), key)
                    for key, vector in self.vectors[target].items()
                ),
                key=lambda item: (-item[0], item[1]),
            )
        except Exception as exc:
            raise RetrieverUnavailable("向量查询失败，不伪装为空召回") from exc

        # 最后把BM25和余弦相似度一起返回
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
