import json
import sys

from casepath.bootstrap import build_demo_workflow
from casepath.contracts import QueryState


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    workflow = build_demo_workflow()
    snapshot = workflow.run(
        QueryState(
            session_id="demo-session-001",
            initial_query="我在健身房充了5000元，店关门了，还有3000元没消费。",
        )
    )
    print(json.dumps(snapshot.model_dump(mode="json"), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
