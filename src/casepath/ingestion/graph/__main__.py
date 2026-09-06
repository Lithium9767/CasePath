"""默认仅预览；--import-neo4j 显式发布。凭据来自环境，不回显。"""

import argparse
import json
import os
from pathlib import Path

from casepath.ingestion.graph.projection import load_projection, report
from casepath.ingestion.laws.jsonl import write_json


def main():
    parser = argparse.ArgumentParser(description="CasePath 规则/案例两层图谱构建")
    parser.add_argument("--data-root", type=Path, default=Path("data"))
    parser.add_argument("--output", type=Path, help="可选派生图预览 JSON，禁止覆盖 canonical")
    parser.add_argument("--import-neo4j", action="store_true")
    args = parser.parse_args()
    bundle = load_projection(args.data_root)
    if args.output:
        target = args.output.resolve()
        root = args.data_root.resolve()
        if target.is_relative_to(root / "canonical") or target.is_relative_to(root / "manifests"):
            parser.error("graph output must not overwrite canonical data or manifests")
        write_json(target, bundle)
    if args.import_neo4j:
        from neo4j import GraphDatabase

        from casepath.adapters.neo4j_writer import publish

        # 写账号独立配置，绝不从在线只读账号自动取密码。
        password = os.getenv("CASEPATH_NEO4J_WRITE_PASSWORD")
        user = os.getenv("CASEPATH_NEO4J_WRITE_USER")
        if not password or not user:
            parser.error("set CASEPATH_NEO4J_WRITE_USER and CASEPATH_NEO4J_WRITE_PASSWORD")
        uri = os.getenv("CASEPATH_NEO4J_URI", "bolt://localhost:7688")
        database = os.getenv("CASEPATH_NEO4J_DATABASE", "neo4j")
        with GraphDatabase.driver(uri, auth=(user, password), connection_timeout=5) as driver:
            driver.verify_connectivity()
            publish(driver, database, bundle)
    print(
        json.dumps(dict(report(bundle), published=args.import_neo4j), ensure_ascii=False, indent=2)
    )


if __name__ == "__main__":
    main()
