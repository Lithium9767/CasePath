"""从已完成审核的 E0/E1 实验结果生成四联 SVG 图。

不依赖 matplotlib，避免为了展示结果改变项目运行环境。脚本拒绝 pending 审核，
并把 ASK 指标的实际问题数作为分母。
"""

from __future__ import annotations

import argparse
import json
import statistics
from collections import defaultdict
from pathlib import Path
from xml.sax.saxutils import escape


def load_rows(path: Path) -> list[dict]:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not rows or any(row.get("review_status") != "reviewed" for row in rows):
        raise ValueError("审核文件为空或仍包含 pending，不能画正式结果图")
    return rows


def ratio(rows: list[dict], field: str) -> float | None:
    asks = [row for row in rows if row.get("actual_action") == "ASK"]
    if not asks:
        return None
    return sum(bool(row.get(field)) for row in asks) / len(asks)


def mean_number(rows: list[dict], field: str) -> float | None:
    values = [float(row[field]) for row in rows if row.get(field) is not None]
    return statistics.mean(values) if values else None


def metrics(rows: list[dict]) -> dict[str, dict[str, float | None]]:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        grouped[row["variant"]].append(row)
    result: dict[str, dict[str, float | None]] = {}
    for variant, items in grouped.items():
        result[variant] = {
            "decision_accuracy": sum(bool(row.get("action_correct")) for row in items) / len(items),
            "repeat_known_rate": ratio(items, "repeats_known_fact"),
            "reask_unable_rate": ratio(items, "reasks_unable_or_declined"),
            "extra_fact_rate": ratio(items, "bundles_extra_fact"),
            "logical_calls": mean_number(items, "logical_model_calls"),
            "elapsed_seconds": mean_number(items, "elapsed_seconds"),
        }
    return result


def svg_bar_panel(
    title: str,
    values: dict[str, float | None],
    *,
    x: int,
    y: int,
    width: int = 570,
    height: int = 280,
    scale: float = 1.0,
    suffix: str = "",
) -> str:
    max_value = scale or 1.0
    parts = [f'<g transform="translate({x},{y})">', f'<text class="title" x="0" y="22">{escape(title)}</text>']
    parts.append(f'<line class="axis" x1="48" y1="{height - 45}" x2="{width - 20}" y2="{height - 45}"/>')
    bar_width = 110
    for index, variant in enumerate(("E0", "E1")):
        value = values.get(variant)
        if value is None:
            label, bar_height = "N/A", 0
        else:
            label = f"{value:.2f}{suffix}"
            bar_height = max(0, min(height - 80, value / max_value * (height - 80)))
        bx = 100 + index * 220
        by = height - 45 - bar_height
        parts.append(f'<rect class="bar {variant.lower()}" x="{bx}" y="{by:.1f}" width="{bar_width}" height="{bar_height:.1f}" rx="8"/>')
        parts.append(f'<text class="value" x="{bx + bar_width / 2:.1f}" y="{max(42, by - 8):.1f}" text-anchor="middle">{escape(label)}</text>')
        parts.append(f'<text class="label" x="{bx + bar_width / 2:.1f}" y="{height - 20}" text-anchor="middle">{variant}</text>')
    parts.append("</g>")
    return "".join(parts)


def write_svg(values: dict[str, dict[str, float | None]], path: Path) -> None:
    panels = [
        svg_bar_panel("(a) 决策正确率", {k: v["decision_accuracy"] for k, v in values.items()}, x=30, y=25),
        svg_bar_panel("(b) 不当追问率（已知/无法/夹带）", {k: v["repeat_known_rate"] for k, v in values.items()}, x=620, y=25),
        svg_bar_panel("(c) 平均逻辑 LLM 调用数", {k: v["logical_calls"] for k, v in values.items()}, x=30, y=335, scale=max(1.0, max((v["logical_calls"] or 0) for v in values.values())), suffix=""),
        svg_bar_panel("(d) 平均 E 阶段耗时（秒）", {k: v["elapsed_seconds"] for k, v in values.items()}, x=620, y=335, scale=max(1.0, max((v["elapsed_seconds"] or 0) for v in values.values())), suffix="s"),
    ]
    svg = """<svg xmlns="http://www.w3.org/2000/svg" width="1220" height="650" viewBox="0 0 1220 650">
<style>
.title{font:600 18px Arial,'Microsoft YaHei',sans-serif;fill:#16233b}.axis{stroke:#8492a6;stroke-width:1}.bar{fill:#8fb3d9}.bar.e1{fill:#315f9f}.value,.label{font:14px Arial,'Microsoft YaHei',sans-serif;fill:#24324a}.label{font-weight:600}
</style>
<rect width="100%" height="100%" fill="#fbfcfe"/>
""" + "\n".join(panels) + "</svg>"
    path.write_text(svg, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reviewed", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    values = metrics(load_rows(args.reviewed.resolve()))
    if set(values) != {"E0", "E1"}:
        raise ValueError(f"需要同时包含 E0/E1，当前为 {sorted(values)}")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    write_svg(values, args.out.resolve())


if __name__ == "__main__":
    main()
