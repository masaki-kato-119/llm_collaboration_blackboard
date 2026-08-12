"""Strict Markdown YAML Front Matter serialization."""

from __future__ import annotations

from typing import Any

import yaml

from .errors import ValidationError

_DELIMITER = "---"


def parse(text: str) -> tuple[dict[str, Any], str]:
    if not text.startswith(f"{_DELIMITER}\n"):
        raise ValidationError("YAML Front Matter is required")
    lines = text.splitlines(keepends=True)
    end_index = next(
        (index for index, line in enumerate(lines[1:], start=1) if line.strip() == _DELIMITER),
        None,
    )
    if end_index is None:
        raise ValidationError("YAML Front Matter closing delimiter is missing")
    try:
        metadata = yaml.safe_load("".join(lines[1:end_index])) or {}
    except yaml.YAMLError as exc:
        raise ValidationError(f"invalid YAML Front Matter: {exc}") from exc
    if not isinstance(metadata, dict):
        raise ValidationError("YAML Front Matter must be a mapping")
    return metadata, "".join(lines[end_index + 1 :]).lstrip("\n")


def dump(metadata: dict[str, Any], body: str) -> str:
    yaml_text = yaml.safe_dump(metadata, allow_unicode=True, sort_keys=False).rstrip()
    normalized_body = body.rstrip() + "\n" if body.strip() else ""
    return f"{_DELIMITER}\n{yaml_text}\n{_DELIMITER}\n\n{normalized_body}"
