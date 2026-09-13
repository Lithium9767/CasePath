"""Create the pinned, balanced LexRel subset used for case-retrieval evaluation."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any

SOURCE_SHA256 = "4dbc837e1f287fe2bc322523494061a70da2b49c344250e2bbe40065aea334a0"
SAMPLES_PER_RELATION = 8
RELATION_TYPES = (
    "借款合同关系",
    "保证合同关系",
    "夫妻关系",
    "机动车交通事故侵权责任法律关系",
    "财产保险合同关系",
    "除房屋外的买卖合同关系",
    "抵押合同关系",
    "房屋买卖合同关系",
    "追偿权关系",
    "劳务合同关系",
    "租赁合同关系",
    "物业服务合同关系",
)


def _normalized_text(text: str) -> str:
    return re.sub(r"\s+", "", text).strip()


def select_records(source: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Select a deterministic stratified subset without reusing facts or UUIDs."""

    selected: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    seen_facts: set[str] = set()
    ordered = sorted(source, key=lambda sample: str(sample.get("uniqid") or ""))
    for relation_type in RELATION_TYPES:
        relation_records: list[dict[str, Any]] = []
        for sample in ordered:
            relation = sample.get("relation") or {}
            uniqid = str(sample.get("uniqid") or "").strip()
            facts = _normalized_text(str(sample.get("input") or ""))
            if (
                relation_type not in relation
                or not uniqid
                or not facts
                or uniqid in seen_ids
                or facts in seen_facts
            ):
                continue
            relation_records.append(
                {
                    "primary_relation_type": relation_type,
                    "sample": sample,
                }
            )
            seen_ids.add(uniqid)
            seen_facts.add(facts)
            if len(relation_records) == SAMPLES_PER_RELATION:
                break
        if len(relation_records) != SAMPLES_PER_RELATION:
            raise ValueError(
                f"not enough unique records for {relation_type}: "
                f"expected {SAMPLES_PER_RELATION}, received {len(relation_records)}"
            )
        selected.extend(relation_records)
    return selected


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="LexRel/LexRel/test.json at the pinned revision")
    parser.add_argument(
        "output",
        type=Path,
        nargs="?",
        default=Path("data/inputs/lexrel_retrieval_cases.json"),
    )
    args = parser.parse_args()
    raw = args.source.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != SOURCE_SHA256:
        raise ValueError(f"unexpected LexRel test.json hash: {digest}")
    source = json.loads(raw)
    if not isinstance(source, list):
        raise TypeError("LexRel test.json must be a JSON array")
    selected = select_records(source)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(selected, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"wrote {len(selected)} records to {args.output}")


if __name__ == "__main__":
    main()
