"""P1 离线图发布器；写账号只用于此入口，在线仍用只读 gateway。

整批节点、关系和活动版本指针在同一事务提交。旧发布保留，不清空数据库。
"""

import json
from collections import defaultdict

from casepath.ingestion.graph.projection import LABELS, RELATIONS, digest, report


def native_properties(properties):
    # Neo4j 属性不接受嵌套 map；复杂值显式编码，原始结构另存 JSON 便于还原。
    result = {}
    for key, value in properties.items():
        if value is None:
            continue
        if isinstance(value, dict) or (
            isinstance(value, list) and any(isinstance(v, (dict, list)) for v in value)
        ):
            result[key + "_json"] = json.dumps(value, ensure_ascii=False, sort_keys=True)
        else:
            result[key] = value
    result["payload_json"] = json.dumps(properties, ensure_ascii=False, sort_keys=True)
    return result


def publish(driver, database, bundle, *, batch_size=500, timeout=120):
    # 【b 落库】消费投影包，用 Cypher 写属性图；和在线只读 gateway、embedding 建库独立。
    if batch_size <= 0 or timeout <= 0:
        raise ValueError("batch size and timeout must be positive")
    payload = {k: v for k, v in bundle.items() if k != "release_id"}
    if digest(payload) != bundle["release_id"]:
        raise ValueError("graph bundle hash mismatch")
    nodes = defaultdict(list)
    edges = defaultdict(list)
    keys = {n["key"] for n in bundle["nodes"]}
    if len(keys) != len(bundle["nodes"]):
        raise ValueError("duplicate graph node")
    for node in bundle["nodes"]:
        if node["label"] not in LABELS:
            raise ValueError("unapproved graph label")
        properties = native_properties(node["properties"])
        properties.update(
            object_id=node["object_id"], key=node["key"], release_id=bundle["release_id"]
        )
        nodes[node["label"]].append({"key": node["key"], "props": properties})
    for edge in bundle["edges"]:
        if edge["type"] not in RELATIONS or not {edge["source"], edge["target"]} <= keys:
            raise ValueError("invalid graph relationship")
        edges[edge["type"]].append(edge)

    # 标签及关系类型仅允许代码内白名单；所有数据值使用参数。
    with driver.session(database=database) as session:
        # 此处只建唯一性约束及其支撑索引，不创建 Neo4j 全文/向量索引。
        for query in [
            (
                "CREATE CONSTRAINT casepath_entity IF NOT EXISTS FOR (n:CasePathEntity) "
                "REQUIRE (n.release_id, n.key) IS UNIQUE"
            ),
            (
                "CREATE CONSTRAINT casepath_release IF NOT EXISTS FOR (n:CasePathRelease) "
                "REQUIRE n.release_id IS UNIQUE"
            ),
            (
                "CREATE CONSTRAINT casepath_catalog IF NOT EXISTS FOR (n:CasePathCatalog) "
                "REQUIRE n.name IS UNIQUE"
            ),
        ]:
            session.run(query).consume()
        # schema DDL 独立提交；数据事务失败至多留下空约束，不改变旧活动图。
        with session.begin_transaction(timeout=timeout) as tx:
            release = bundle["release_id"]
            # 对唯一 catalog 加写锁，将多个发布操作串行化。
            tx.run(
                "MERGE (c:CasePathCatalog {name:'casepath'}) "
                "SET c.lock_version=coalesce(c.lock_version,0)+1"
            ).consume()
            for label, rows in sorted(nodes.items()):
                # UNWIND 展开参数批次；MERGE 按发布版本+key 创建或匹配节点。
                for start in range(0, len(rows), batch_size):
                    tx.run(
                        f"UNWIND $rows AS row MERGE (n:CasePathEntity:{label} "
                        "{release_id:$release, key:row.key}) SET n = row.props",
                        rows=rows[start : start + batch_size],
                        release=release,
                    ).consume()
            for relation, rows in sorted(edges.items()):
                # 根据 source/target key 找到两端，再建立明确关系，不需要计算向量。
                for start in range(0, len(rows), batch_size):
                    tx.run(
                        "UNWIND $rows AS row "
                        "MATCH (a:CasePathEntity {release_id:$release,key:row.source}) "
                        "MATCH (b:CasePathEntity {release_id:$release,key:row.target}) "
                        f"MERGE (a)-[r:{relation} {{key:row.key}}]->(b) "
                        "SET r.release_id=$release",
                        rows=rows[start : start + batch_size],
                        release=release,
                    ).consume()
            count = tx.run(
                "MATCH (n:CasePathEntity {release_id:$release}) RETURN count(n) AS count",
                release=release,
            ).single()["count"]
            edge_count = tx.run(
                "MATCH (a:CasePathEntity {release_id:$release})-[r]->"
                "(b:CasePathEntity {release_id:$release}) RETURN count(r) AS count",
                release=release,
            ).single()["count"]
            if count != len(bundle["nodes"]) or edge_count != len(bundle["edges"]):
                raise ValueError("published graph count mismatch; transaction rolled back")
            tx.run(
                "MERGE (r:CasePathRelease {release_id:$release}) "
                "SET r.projection_version=$version,r.input_hashes_json=$hashes,"
                "r.node_count=$nodes,r.edge_count=$edges "
                "WITH r MATCH (c:CasePathCatalog {name:'casepath'}) "
                "SET c.active_release_id=r.release_id",
                release=release,
                version=bundle["projection_version"],
                hashes=json.dumps(bundle["input_hashes"], sort_keys=True),
                nodes=count,
                edges=edge_count,
            ).consume()
            tx.commit()
    return report(bundle)
