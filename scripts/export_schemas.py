import json
from pathlib import Path

# 在 pyproject.toml中定义 packages = ["src/casepath"]
# 通过 pip install -e . pip读取pyproject把它注册成import casepath可导入的包
# import 名字由[project] name = "casepath" 决定
from casepath.contracts.registry import CONTRACTS

# 兼容原工具名称，实际来源统一到注册表。
MODELS = CONTRACTS


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
