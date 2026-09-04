from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel

ModelT = TypeVar("ModelT", bound=BaseModel)


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def sha256_text(content: str) -> str:
    return sha256_bytes(content.encode("utf-8"))


def normalise_lf_bytes(content: bytes) -> bytes:
    """Return text bytes with platform newlines canonicalised to LF.

    Git may materialise the same text blob with CRLF on Windows and LF on
    Unix.  Dataset manifests therefore hash canonical text bytes rather than
    checkout-specific bytes.
    """

    return content.replace(b"\r\n", b"\n").replace(b"\r", b"\n")


def sha256_file(path: Path) -> str:
    """Hash a UTF-8 dataset file after canonicalising line endings to LF."""

    return sha256_bytes(normalise_lf_bytes(path.read_bytes()))


def _as_json_value(record: BaseModel | dict[str, Any]) -> dict[str, Any]:
    if isinstance(record, BaseModel):
        return record.model_dump(mode="json")
    return record


def render_jsonl(records: Iterable[BaseModel | dict[str, Any]]) -> str:
    lines = [
        json.dumps(_as_json_value(record), ensure_ascii=False, separators=(",", ":"))
        for record in records
    ]
    return "\n".join(lines) + ("\n" if lines else "")


def write_text_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_name(f".{path.name}.tmp")
    temporary_path.write_text(content, encoding="utf-8", newline="\n")
    temporary_path.replace(path)


def write_jsonl(path: Path, records: Iterable[BaseModel | dict[str, Any]]) -> None:
    write_text_atomic(path, render_jsonl(records))


def write_json(path: Path, payload: BaseModel | dict[str, Any]) -> None:
    value = payload.model_dump(mode="json") if isinstance(payload, BaseModel) else payload
    content = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    write_text_atomic(path, content)


def read_jsonl(path: Path, model: type[ModelT]) -> list[ModelT]:
    records: list[ModelT] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            records.append(model.model_validate_json(line))
        except ValueError as error:
            raise ValueError(f"{path}:{line_number}: {error}") from error
    return records
