import json
from pathlib import Path

# 在 pyproject.toml中定义 packages = ["src/casepath"]
# 通过 pip install -e . pip读取pyproject把它注册成import casepath可导入的包
# import 名字由[project] name = "casepath" 决定
from casepath.contracts import (
    AnswerRequest,
    CapabilityStatus,
    CaseRecord,
    ErrorResponse,
    ExplanationPlan,
    LegalSourceRecord,
    ProvisionRecord,
    QueryState,
    RetrievalBundle,
    RuleRecord,
    WorkflowSnapshot,
)

MODELS = {
    "legal-source-record": LegalSourceRecord,
    "provision-record": ProvisionRecord,
    "rule-record": RuleRecord,
    "case-record": CaseRecord,
    "query-state": QueryState,
    "retrieval-bundle": RetrievalBundle,
    "explanation-plan": ExplanationPlan,
    "answer-request": AnswerRequest,
    "error-response": ErrorResponse,
    "capability-status": CapabilityStatus,
    "workflow-snapshot": WorkflowSnapshot,
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
