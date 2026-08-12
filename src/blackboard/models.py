"""Validation and Markdown rendering for Blackboard documents."""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

from .errors import ValidationError

ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")
TASK_STATUSES = {"pending", "in_progress", "done", "blocked", "cancelled"}
TASK_PRIORITIES = {"P0", "P1", "P2", "P3"}
DEFAULT_TASK_PRIORITY = "P2"
TASK_COLUMNS = ("ID", "Task", "Role", "Status", "Started By", "Started", "Completed By", "Completed")
REQUIRED_BASE_FIELDS = {"id", "type", "created", "updated", "revision"}


def require_id(value: str, label: str = "id") -> str:
    if not isinstance(value, str) or not ID_PATTERN.fullmatch(value):
        raise ValidationError(f"{label} must match {ID_PATTERN.pattern}")
    return value


def require_actor(value: str) -> str:
    return require_id(value, "actor_id")


def require_role(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError("role must be a non-empty string")
    return value.strip()


def validate_document(metadata: dict[str, Any], expected_type: str) -> None:
    missing = REQUIRED_BASE_FIELDS - set(metadata)
    if missing:
        raise ValidationError(f"missing Front Matter fields: {', '.join(sorted(missing))}")
    require_id(str(metadata["id"]))
    if metadata["type"] != expected_type:
        raise ValidationError(f"document type must be '{expected_type}'")
    if not isinstance(metadata["revision"], int) or metadata["revision"] < 1:
        raise ValidationError("revision must be a positive integer")


def parse_plan_body(body: str) -> tuple[str, list[dict[str, str]]]:
    lines = body.splitlines()
    try:
        table_start = next(index for index, line in enumerate(lines) if line.strip().startswith("|"))
    except StopIteration as exc:
        raise ValidationError("Plan body must contain a task table") from exc
    if table_start + 1 >= len(lines):
        raise ValidationError("Plan task table separator is missing")
    header = _split_row(lines[table_start])
    if tuple(header) != TASK_COLUMNS:
        raise ValidationError("Plan task table has unexpected columns")
    separator = _split_row(lines[table_start + 1])
    if len(separator) != len(TASK_COLUMNS) or not all(set(cell) <= {"-", ":"} for cell in separator):
        raise ValidationError("Plan task table separator is invalid")
    tasks: list[dict[str, str]] = []
    for line in lines[table_start + 2 :]:
        if not line.strip():
            continue
        if not line.lstrip().startswith("|"):
            break
        row = _split_row(line)
        if len(row) != len(TASK_COLUMNS):
            raise ValidationError("Plan task table row has unexpected columns")
        task = dict(zip(TASK_COLUMNS, row, strict=True))
        task["ID"] = require_id(task["ID"], "task ID")
        if task["Status"] not in TASK_STATUSES:
            raise ValidationError(f"invalid task status '{task['Status']}'")
        require_role(task["Role"])
        tasks.append(task)
    if not tasks:
        raise ValidationError("Plan must contain at least one task")
    if len({task["ID"] for task in tasks}) != len(tasks):
        raise ValidationError("task IDs must be unique")
    return "\n".join(lines[:table_start]).strip() or "# Plan", tasks


def render_plan_body(title: str, tasks: Iterable[dict[str, str]]) -> str:
    lines = [title.strip() or "# Plan", "", "| " + " | ".join(TASK_COLUMNS) + " |", "|---|---|---|---|---|---|---|---|"]
    for task in tasks:
        lines.append("| " + " | ".join(task.get(column, "") for column in TASK_COLUMNS) + " |")
    return "\n".join(lines)


def dependencies(metadata: dict[str, Any], task_ids: set[str]) -> dict[str, list[str]]:
    raw = metadata.get("dependencies", {})
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValidationError("dependencies must be a mapping")
    parsed: dict[str, list[str]] = {}
    for task_id, prerequisite_ids in raw.items():
        task_key = require_id(str(task_id), "dependency task ID")
        if task_key not in task_ids:
            raise ValidationError(f"dependency target '{task_key}' is not a task")
        if not isinstance(prerequisite_ids, list):
            raise ValidationError("dependency values must be lists")
        parsed[task_key] = [require_id(str(item), "dependency ID") for item in prerequisite_ids]
        unknown = set(parsed[task_key]) - task_ids
        if unknown:
            raise ValidationError(f"unknown dependency IDs: {', '.join(sorted(unknown))}")
    _validate_acyclic(parsed)
    return parsed


def priorities(metadata: dict[str, Any], task_ids: set[str]) -> dict[str, str]:
    """Validate the optional Plan priority mapping and return it normalized."""
    raw = metadata.get("priorities", {})
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValidationError("priorities must be a mapping")
    parsed: dict[str, str] = {}
    for task_id, priority in raw.items():
        task_key = require_id(str(task_id), "priority task ID")
        if task_key not in task_ids:
            raise ValidationError(f"priority task '{task_key}' is not a task")
        parsed[task_key] = require_priority(priority)
    return parsed


def require_priority(value: object) -> str:
    """Validate a Facilitator backlog priority."""
    if not isinstance(value, str) or value not in TASK_PRIORITIES:
        raise ValidationError("priority must be one of P0, P1, P2, or P3")
    return value


def _validate_acyclic(graph: dict[str, list[str]]) -> None:
    visited: set[str] = set()
    active: set[str] = set()

    def visit(node: str) -> None:
        if node in active:
            raise ValidationError("Plan dependencies must not contain a cycle")
        if node in visited:
            return
        active.add(node)
        for child in graph.get(node, []):
            visit(child)
        active.remove(node)
        visited.add(node)

    for node in graph:
        visit(node)


def _split_row(line: str) -> list[str]:
    stripped = line.strip()
    if not stripped.startswith("|") or not stripped.endswith("|"):
        raise ValidationError("Plan task table rows must start and end with '|'")
    return [cell.strip() for cell in stripped[1:-1].split("|")]
