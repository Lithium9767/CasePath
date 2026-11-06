"""将候选状态与逐条 AI 审核合并为冻结实验集。

该脚本要求每个样本都有显式审核行，禁止把 pending 候选直接改名为 frozen。
审核身份固定记录为 AI；这不是独立人工盲评。
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def canonical_input(row: dict[str, Any]) -> str:
    payload = {
        key: row[key]
        for key in (
            "query_state",
            "retrieval_bundle",
            "comparison_bundle",
            "runtime_meta",
            "rule_scope",
            "catalog_context",
        )
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--decisions", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    candidates = [
        json.loads(line)
        for line in args.candidate.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    decisions = {
        row["sample_id"]: row
        for row in (
            json.loads(line)
            for line in args.decisions.read_text(encoding="utf-8").splitlines()
            if line.strip()
        )
    }
    if len(candidates) != 20 or set(decisions) != {row["sample_id"] for row in candidates}:
        raise ValueError("候选集和审核集必须一一覆盖 20 条样本")

    output: list[dict[str, Any]] = []
    for row in candidates:
        decision = decisions[row["sample_id"]]
        if decision.get("review_status") != "reviewed" or decision.get("decision") != "accept":
            raise ValueError(f"样本 {row['sample_id']} 未通过逐条审核")
        if hashlib.sha256(canonical_input(row).encode()).hexdigest() != row.get("input_sha256"):
            raise ValueError(f"样本 {row['sample_id']} 输入哈希不一致")
        frozen = dict(row)
        gold = dict(frozen["gold"])
        gold.update(
            {
                "reviewer_type": decision["reviewer_type"],
                "review_status": decision["review_status"],
                "review_comment": decision["review_comment"],
            }
        )
        frozen["gold"] = gold
        frozen["dataset_version"] = "e-questioning.v1-frozen"
        output.append(frozen)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in output) + "\n",
        encoding="utf-8",
    )
    manifest = args.out.with_name("frozen_manifest.json")
    manifest.write_text(
        json.dumps(
            {
                "dataset_version": "e-questioning.v1-frozen",
                "sample_count": len(output),
                "review_status": "reviewed",
                "reviewer_type": "AI",
                "review_method": "逐条阅读初始事实、条件状态、允许缺口和禁止重复事项后形成的 AI 辅助审核；不是独立人工盲评。",
                "candidate_sha256": hashlib.sha256(args.candidate.read_bytes()).hexdigest(),
                "decisions_sha256": hashlib.sha256(args.decisions.read_bytes()).hexdigest(),
                "created_at": datetime.now(UTC).isoformat(),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
