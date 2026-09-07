"""显式离线向量构建，会向配置的 embedding 服务发送规则/案例检索文本并产生费用。"""

from casepath.adapters.retrieval_catalog import load_canonical_catalog
from casepath.adapters.vector_search import VectorIndex
from casepath.config import RuntimeSettings
from casepath.config.embedding import EmbeddingSettings


def main():
    settings = EmbeddingSettings.from_env()
    catalog = load_canonical_catalog(RuntimeSettings.from_env().data_root)
    model = settings.connect()
    try:
        index = VectorIndex.build(catalog, model, batch_size=settings.batch_size)
        index.validate(catalog, model)
        index.save(settings.index_path)
        print(
            f"vector index saved: objects={len(index.payload['records'])}, release={catalog.release_id}"
        )
    finally:
        model.close()


if __name__ == "__main__":
    main()
