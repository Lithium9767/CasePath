"""Summarize one acceptance JSONL run into a problem-oriented report."""

from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path


def main(path: Path) -> None:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    meta = next(r for r in rows if r.get("kind") == "metadata")
    print("HEAD", meta.get("git_short"), "dirty", meta.get("git_dirty"))
    print(
        "release",
        meta.get("catalog_release_id"),
        "index",
        meta.get("catalog_index_version"),
        "embed",
        meta.get("embedding_model"),
    )
    caps = {c["capability"]: c["mode"] for c in meta.get("capabilities", [])}
    print(
        "modes",
        {
            k: caps[k]
            for k in ["llm", "rule_retriever", "condition_projector", "case_retriever"]
            if k in caps
        },
    )

    by: dict[str, list] = defaultdict(list)
    for row in rows:
        if "scenario" in row:
            by[row["scenario"]].append(row)

    for sid, items in by.items():
        print("\n===", sid, "===")
        ends = [r for r in items if r.get("kind") == "scenario_end"]
        https = [r for r in items if r.get("kind") == "http"]
        ports = [r for r in items if r.get("kind") == "port"]
        llms = [r for r in items if r.get("kind") == "llm"]
        end = ends[-1] if ends else {}
        print("result", end.get("result"), "stop", end.get("stop_reason") or end.get("pending_question"))
        asked = []
        for http in https:
            nq = (http.get("output_summary") or {}).get("next_question")
            if nq:
                asked.append(nq.get("condition_id"))
            ans = (http.get("input") or {}).get("answer")
            if ans:
                print("  answer:", ans[:100].replace("\n", " "))
            st = (http.get("output_summary") or {}).get("state") or {}
            conds = {c["condition_id"]: c["status"] for c in st.get("conditions") or []}
            interesting = {k: v for k, v in conds.items() if v != "UNKNOWN"}
            if interesting:
                print("  known:", interesting)
            preview = (http.get("output_summary") or {}).get("explanation_preview")
            if preview:
                print("  explain:", preview[:140].replace("\n", " "))
        print("  asked:", asked)
        print("  ask_counts:", dict(Counter(asked)))
        for event in llms:
            if event.get("stage") == "explanation" and event.get("evidence_package"):
                facts = event["evidence_package"].get("facts") or []
                print(
                    "  evidence_roles:",
                    {f.get("fact_id"): f.get("evidence_role") for f in facts},
                )
                print("  corrections_in_pkg:", event["evidence_package"].get("corrections"))
                print(
                    "  fact_texts:",
                    [
                        (f.get("fact_id"), (f.get("text") or "")[:50], f.get("evidence_role"))
                        for f in facts
                    ],
                )
        for port in ports:
            if port.get("stage") == "E" and port.get("output_summary"):
                print("  E:", port.get("output_summary"))
            if port.get("stage") == "B" and port.get("output_summary"):
                known = {
                    c["condition_id"]: c["status"]
                    for c in (port["output_summary"].get("conditions") or [])
                    if c.get("status") != "UNKNOWN"
                }
                if known:
                    print("  B known:", known)


if __name__ == "__main__":
    main(Path(sys.argv[1]))
