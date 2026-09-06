import json
import sys

from casepath.bootstrap import build_demo_workflow
from casepath.contracts import QueryState


# 小demo演示
def main() -> None:
    # 判断sys.stdout是否支持reconfigure
    if hasattr(sys.stdout, "reconfigure"):
        # 如果支持，则重新配置编码为utf-8，正确输出中文
        sys.stdout.reconfigure(encoding="utf-8")
    # 构建演示工作流
    workflow = build_demo_workflow()
    # 运行演示工作流
    snapshot = workflow.run(
        QueryState(
            session_id="demo-session-001",
            initial_query="我在健身房充了5000元，店关门了，还有3000元没消费。",
        )
    )
    # 打印结果
    print(json.dumps(snapshot.model_dump(mode="json"), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
