import json
from pathlib import Path

from casepath.contracts import (
    CaseRecord,
    ExplanationPlan,
    QueryState,
    RetrievalBundle,
    RuleRecord,
)

MODELS = {
    "rule-record": RuleRecord,
    "case-record": CaseRecord,
    "query-state": QueryState,
    "retrieval-bundle": RetrievalBundle,
    "explanation-plan": ExplanationPlan,
}


def main() -> None:
    output_dir = Path("contracts/schemas")
    output_dir.mkdir(parents=True, exist_ok=True)
    for name, model in MODELS.items():
        path = output_dir / f"{name}.schema.json"
        path.write_text(
            json.dumps(model.model_json_schema(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(path)


if __name__ == "__main__":
    main()
