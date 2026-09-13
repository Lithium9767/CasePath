# 案例召回测试集

`case_retrieval_qrels.jsonl` 提供 13 个民事法律关系查询及其相关案例 ID。
相关性来自 LexRel 专家标注，适合计算 Recall@K；它衡量关系类型召回，不代表裁判结果预测准确率。

重新构建案例数据后运行：

```bash
python scripts/build_case_retrieval_qrels.py
```

每条记录包含 `query_id`、`query_text`、`relation_type` 和 `relevant_case_ids`。

运行当前 BM25 基线：

```bash
python scripts/evaluate_case_recall.py --k 5 10 20
```

该脚本只跑无需外部服务的 BM25 基线。`data/derived/vectors.json` 属于旧语料发布；进行向量召回测试前，需使用当前 embedding 配置重新运行 `scripts/build_vector_index.py`。
