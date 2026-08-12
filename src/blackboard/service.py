"""Blackboard domain operations and task coordination rules."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .errors import (
    BlackboardError,
    ClaimOwnerError,
    ConflictError,
    InvalidTransitionError,
    NotExecutableError,
    NotFoundError,
    RoleMismatchError,
    ValidationError,
)
from .models import (
    DEFAULT_TASK_PRIORITY,
    TASK_COLUMNS,
    TASK_STATUSES,
    dependencies,
    parse_plan_body,
    priorities,
    render_plan_body,
    require_actor,
    require_id,
    require_priority,
    require_role,
)
from .permissions import require_permission
from .store import DEFAULT_PLAN_ID, Document, MarkdownStore

STALE_STATE_DAYS = 7
"""Age (days since `updated`) at which read_state adds a staleness warning.

Spec chapter 7 keeps Plan and State intentionally separate, so State is
never auto-derived from Plan. This is a read-time nudge only: it does not
change the document or write anything.
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _parse_timestamp(value: str, field_name: str) -> datetime:
    """Parse an ISO 8601 timestamp into a timezone-aware UTC datetime."""
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValidationError(f"{field_name} must be an ISO 8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise ValidationError(f"{field_name} must include a timezone")
    return parsed.astimezone(timezone.utc)


def _reason_event_content(reason: str | None) -> str:
    """Render an optional intervention reason into the mutation's Event body."""
    return f"reason: {reason.strip()}\n" if reason is not None else ""


class BlackboardService:
    """Application service for one Blackboard root.

    A root may contain multiple named Plans. Memory, State, and Event remain
    shared at the Blackboard root. The default Plan id is ``project``.
    """

    def __init__(self, root: str | Path) -> None:
        self.store = MarkdownStore(root)

    def initialize_project(
        self,
        project_id: str,
        title: str,
        tasks: list[dict[str, str]],
        task_dependencies: dict[str, list[str]] | None = None,
        plan_id: str = DEFAULT_PLAN_ID,
    ) -> dict[str, Any]:
        """Create the directory layout and one named Plan document."""
        require_id(project_id, "project_id")
        plan_id = require_id(plan_id, "plan_id")
        self.store.initialize()
        path = self.store.plan_path(plan_id)
        if path.exists():
            raise ConflictError(f"Plan '{plan_id}' already exists")
        normalized_tasks = [self._new_task(task) for task in tasks]
        body = render_plan_body(f"# Plan: {title}", normalized_tasks)
        timestamp = _now()
        metadata: dict[str, Any] = {
            "id": project_id,
            "type": "plan",
            "created": timestamp,
            "updated": timestamp,
            "revision": 1,
            "dependencies": task_dependencies or {},
            "priorities": {},
            "outbox": [],
        }
        # Validate the dependencies before persisting the initial Plan.
        dependencies(metadata, {task["ID"] for task in normalized_tasks})
        self.store.write(path, metadata, body)
        return self.read_plan(plan_id=plan_id)

    def list_plans(self) -> list[dict[str, Any]]:
        """List named Plans, each with a summary of who is currently working on what.

        ``in_progress_tasks`` lets a new actor see, across every Plan in this
        root, what is already claimed before picking work — without having
        to call read_plan once per plan_id first.
        """
        plans: list[dict[str, Any]] = []
        for plan_id in self.store.list_plan_ids():
            document = self.store.read(self.store.plan_path(plan_id), "plan")
            title, tasks = parse_plan_body(document.body)
            in_progress = [
                {
                    "id": task["ID"],
                    "role": task["Role"],
                    "started_by": task["Started By"],
                    "started": task["Started"],
                }
                for task in tasks
                if task["Status"] == "in_progress"
            ]
            plans.append(
                {
                    "plan_id": plan_id,
                    "id": document.metadata["id"],
                    "title": title.removeprefix("# "),
                    "revision": document.metadata["revision"],
                    "in_progress_tasks": in_progress,
                }
            )
        return plans

    def list_actor_roles(self) -> dict[str, list[str]]:
        """Observed role history per actor_id, across every Plan in this root.

        This is not authentication and not enforced: it only reports which
        roles an actor_id has claimed tasks under in the past, derived from
        Plan task rows (``Started By`` / ``Role``). Actors still self-declare
        their role on every call; this is purely an observation aid for a
        human or another actor reviewing who has been active.
        """
        roles_by_actor: dict[str, set[str]] = {}
        for plan_id in self.store.list_plan_ids():
            document = self.store.read(self.store.plan_path(plan_id), "plan")
            _, tasks = parse_plan_body(document.body)
            for task in tasks:
                actor = task["Started By"]
                if actor:
                    roles_by_actor.setdefault(actor, set()).add(task["Role"])
        return {actor: sorted(roles) for actor, roles in sorted(roles_by_actor.items())}

    def read_plan(self, role: str | None = None, plan_id: str = DEFAULT_PLAN_ID) -> dict[str, Any]:
        plan_id = require_id(plan_id, "plan_id")
        plan = self.store.read(self.store.plan_path(plan_id), "plan")
        title, tasks = parse_plan_body(plan.body)
        task_ids = {task["ID"] for task in tasks}
        graph = dependencies(plan.metadata, task_ids)
        priority_map = priorities(plan.metadata, task_ids)
        if role is not None:
            role = require_role(role)
        executable = [
            self._task_public(task, priority_map.get(task["ID"], DEFAULT_TASK_PRIORITY))
            for task in tasks
            if (role is None or task["Role"] == role) and self._is_executable(task, tasks, graph)
        ]
        return {
            "plan_id": plan_id,
            "id": plan.metadata["id"],
            "title": title.removeprefix("# "),
            "revision": plan.metadata["revision"],
            "tasks": [self._task_public(task, priority_map.get(task["ID"], DEFAULT_TASK_PRIORITY)) for task in tasks],
            "dependencies": graph,
            "priorities": priority_map,
            "executable_tasks": executable,
            "pending_events": sum(1 for event in plan.metadata.get("outbox", []) if event.get("status") == "pending"),
        }

    def validate_plan(self, plan_id: str = DEFAULT_PLAN_ID) -> dict[str, Any]:
        """Diagnose a Plan without changing it.

        This validates data that is meaningful for direct human Markdown edits
        in addition to the normal document/table/dependency parsing. It cannot
        prove that a human incremented revision relative to an unseen prior
        file version; callers should use it before resuming MCP mutations.
        """
        plan_id = require_id(plan_id, "plan_id")
        try:
            document = self.store.read(self.store.plan_path(plan_id), "plan")
            _, tasks = parse_plan_body(document.body)
            task_ids = {task["ID"] for task in tasks}
            graph = dependencies(document.metadata, task_ids)
            priorities(document.metadata, task_ids)
        except BlackboardError as exc:
            return {"plan_id": plan_id, "valid": False, "issues": [str(exc)]}

        issues: list[str] = []
        for field_name in ("created", "updated"):
            try:
                _parse_timestamp(str(document.metadata[field_name]), field_name)
            except ValidationError as exc:
                issues.append(str(exc))
        issues.extend(self._task_audit_issues(tasks))
        issues.extend(self._outbox_issues(document.metadata.get("outbox"), {task["ID"] for task in tasks}))
        return {
            "plan_id": plan_id,
            "revision": document.metadata["revision"],
            "valid": not issues,
            "issues": issues,
            "task_count": len(tasks),
            "dependency_count": sum(len(item) for item in graph.values()),
        }

    def set_task_priority(
        self,
        task_id: str,
        priority: str,
        actor_id: str,
        role: str,
        reason: str,
        expected_revision: int,
        plan_id: str = DEFAULT_PLAN_ID,
    ) -> dict[str, Any]:
        """Set a pending task's advisory priority with one CAS/Outbox mutation."""
        task_id, actor_id = require_id(task_id, "task_id"), require_actor(actor_id)
        priority = require_priority(priority)
        role = require_permission(role, "set_task_priority")
        plan_id = require_id(plan_id, "plan_id")
        if not isinstance(reason, str) or not reason.strip():
            raise ValidationError("reason is required")
        reason = reason.strip()
        with self.store.plan_lock(plan_id):
            document, title, tasks, _ = self._load_plan_locked(plan_id)
            self._check_revision(document, expected_revision)
            existing = self._find_task(tasks, task_id)
            if existing["Status"] != "pending":
                raise InvalidTransitionError("only pending tasks can have their priority changed")
            priority_map = priorities(document.metadata, {task["ID"] for task in tasks})
            before = priority_map.get(task_id, DEFAULT_TASK_PRIORITY)
            priority_map[task_id] = priority
            event_content = f"priority_before: {before}\npriority_after: {priority}\nreason: {reason}\n"
            self._commit_plan(
                plan_id,
                document,
                title,
                tasks,
                event_type="task_prioritized",
                actor_id=actor_id,
                task_id=task_id,
                role=role,
                metadata_overrides={"priorities": priority_map},
                event_content=event_content,
            )
        delivery = self.flush_event_outbox(plan_id)
        return {
            "task": self._task_public(existing, priority),
            "plan_id": plan_id,
            "plan_revision": delivery["revision"],
            "event_delivery": delivery["status"],
        }

    def add_task(
        self,
        task: dict[str, str],
        actor_id: str,
        role: str,
        expected_revision: int,
        plan_id: str = DEFAULT_PLAN_ID,
        task_dependencies: list[str] | None = None,
        reason: str | None = None,
        priority: str | None = None,
    ) -> dict[str, Any]:
        """Append one new task to an already-initialized Plan."""
        actor_id = require_actor(actor_id)
        role = require_permission(role, "add_task")
        plan_id = require_id(plan_id, "plan_id")
        new_task = self._new_task(task)
        if reason is not None and (not isinstance(reason, str) or not reason.strip()):
            raise ValidationError("reason must be non-empty when provided")
        if priority is not None:
            priority = require_priority(priority)
        with self.store.plan_lock(plan_id):
            document, title, tasks, _ = self._load_plan_locked(plan_id)
            self._check_revision(document, expected_revision)
            if any(existing["ID"] == new_task["ID"] for existing in tasks):
                raise ValidationError(f"task '{new_task['ID']}' already exists")
            updated_tasks = [*tasks, new_task]
            all_ids = {item["ID"] for item in updated_tasks}
            updated_graph = dict(document.metadata.get("dependencies") or {})
            if task_dependencies:
                updated_graph[new_task["ID"]] = [require_id(dep_id, "dependency ID") for dep_id in task_dependencies]
            priority_map = priorities(document.metadata, {item["ID"] for item in tasks})
            if priority is not None:
                priority_map[new_task["ID"]] = priority
            # Re-validate the whole graph: unknown targets and cycles must still be rejected.
            validated_graph = dependencies({"dependencies": updated_graph}, all_ids)
            self._commit_plan(
                plan_id,
                document,
                title,
                updated_tasks,
                event_type="task_added",
                actor_id=actor_id,
                task_id=new_task["ID"],
                role=role,
                metadata_overrides={"dependencies": validated_graph, "priorities": priority_map},
                event_content=_reason_event_content(reason),
            )
        delivery = self.flush_event_outbox(plan_id)
        return self._task_result(new_task, plan_id, delivery)

    def edit_task(
        self,
        task_id: str,
        actor_id: str,
        role: str,
        expected_revision: int,
        task: str | None = None,
        task_role: str | None = None,
        task_dependencies: list[str] | None = None,
        plan_id: str = DEFAULT_PLAN_ID,
        reason: str | None = None,
        priority: str | None = None,
    ) -> dict[str, Any]:
        """Edit a pending task's description, required Role, or dependencies.

        Task IDs are immutable, and claimed or terminal tasks cannot be edited.
        Passing an empty dependency list explicitly removes all dependencies.
        """
        task_id, actor_id = require_id(task_id, "task_id"), require_actor(actor_id)
        role = require_permission(role, "edit_task")
        plan_id = require_id(plan_id, "plan_id")
        if task is None and task_role is None and task_dependencies is None and priority is None:
            raise ValidationError("provide task, task_role, task_dependencies, or priority to edit a task")
        if reason is not None and (not isinstance(reason, str) or not reason.strip()):
            raise ValidationError("reason must be non-empty when provided")
        if priority is not None:
            priority = require_priority(priority)
        with self.store.plan_lock(plan_id):
            document, title, tasks, graph = self._load_plan_locked(plan_id)
            self._check_revision(document, expected_revision)
            existing = self._find_task(tasks, task_id)
            if existing["Status"] != "pending":
                raise InvalidTransitionError("only pending tasks can be edited")
            if task is not None:
                existing["Task"] = self._validate_task_name(task)
            if task_role is not None:
                existing["Role"] = require_role(task_role)
            updated_graph = dict(graph)
            if task_dependencies is not None:
                updated_graph[task_id] = [require_id(item, "dependency ID") for item in task_dependencies]
            validated_graph = dependencies({"dependencies": updated_graph}, {item["ID"] for item in tasks})
            priority_map = priorities(document.metadata, {item["ID"] for item in tasks})
            if priority is not None:
                priority_map[task_id] = priority
            self._commit_plan(
                plan_id,
                document,
                title,
                tasks,
                event_type="task_updated",
                actor_id=actor_id,
                task_id=task_id,
                role=role,
                metadata_overrides={"dependencies": validated_graph, "priorities": priority_map},
                event_content=_reason_event_content(reason),
            )
        delivery = self.flush_event_outbox(plan_id)
        return self._task_result(existing, plan_id, delivery)

    def cancel_task(
        self,
        task_id: str,
        actor_id: str,
        role: str,
        expected_revision: int,
        plan_id: str = DEFAULT_PLAN_ID,
        reason: str | None = None,
    ) -> dict[str, Any]:
        """Logically delete a pending or owned in-progress task.

        A task with non-terminal dependents cannot be cancelled because that
        would leave executable work permanently blocked by a cancelled input.
        """
        task_id, actor_id = require_id(task_id, "task_id"), require_actor(actor_id)
        role = require_permission(role, "cancel_task")
        plan_id = require_id(plan_id, "plan_id")
        if reason is not None and (not isinstance(reason, str) or not reason.strip()):
            raise ValidationError("reason must be non-empty when provided")
        with self.store.plan_lock(plan_id):
            document, title, tasks, graph = self._load_plan_locked(plan_id)
            self._check_revision(document, expected_revision)
            existing = self._find_task(tasks, task_id)
            if existing["Status"] not in {"pending", "in_progress"}:
                raise InvalidTransitionError("only pending or in_progress tasks can be cancelled")
            if existing["Status"] == "in_progress":
                if existing["Role"] != role:
                    raise RoleMismatchError(f"task '{task_id}' requires role '{existing['Role']}'")
                if existing["Started By"] != actor_id:
                    raise ClaimOwnerError(f"task '{task_id}' is claimed by '{existing['Started By']}'")
            active_dependents = self._non_terminal_dependents(task_id, tasks, graph)
            if active_dependents:
                raise InvalidTransitionError(
                    "cannot cancel a task with non-terminal dependents: " + ", ".join(active_dependents)
                )
            existing["Status"] = "cancelled"
            priority_map = priorities(document.metadata, {item["ID"] for item in tasks})
            priority_map.pop(task_id, None)
            self._commit_plan(
                plan_id, document, title, tasks,
                event_type="task_cancelled", actor_id=actor_id, task_id=task_id, role=role,
                metadata_overrides={"priorities": priority_map}, event_content=_reason_event_content(reason),
            )
        delivery = self.flush_event_outbox(plan_id)
        return self._task_result(existing, plan_id, delivery)

    def recover_task(
        self,
        task_id: str,
        actor_id: str,
        role: str,
        expected_revision: int,
        plan_id: str = DEFAULT_PLAN_ID,
    ) -> dict[str, Any]:
        """Recover a blocked task to pending using the dedicated permission."""
        task_id, actor_id = require_id(task_id, "task_id"), require_actor(actor_id)
        role = require_permission(role, "recover_task")
        plan_id = require_id(plan_id, "plan_id")
        with self.store.plan_lock(plan_id):
            document, title, tasks, _ = self._load_plan_locked(plan_id)
            self._check_revision(document, expected_revision)
            existing = self._find_task(tasks, task_id)
            if existing["Status"] != "blocked":
                raise InvalidTransitionError("only blocked tasks can be recovered to pending")
            existing["Status"] = "pending"
            existing["Started By"] = ""
            existing["Started"] = ""
            existing["Completed By"] = ""
            existing["Completed"] = ""
            self._commit_plan(
                plan_id, document, title, tasks,
                event_type="task_recovered", actor_id=actor_id, task_id=task_id, role=role,
            )
        delivery = self.flush_event_outbox(plan_id)
        return self._task_result(existing, plan_id, delivery)

    def claim_task(
        self,
        task_id: str,
        actor_id: str,
        role: str,
        expected_revision: int,
        plan_id: str = DEFAULT_PLAN_ID,
        work_scope: list[str] | None = None,
    ) -> dict[str, Any]:
        """Claim a task. ``work_scope`` is an optional, non-binding declaration of

        the files/paths this actor expects to touch, recorded on the
        ``task_started`` Event for other actors to see (e.g. via read_event).
        It is not enforced — Blackboard does not lock source files, only its
        own documents — so it is only as reliable as the declaring actor.
        """
        task_id, actor_id = require_id(task_id, "task_id"), require_actor(actor_id)
        role = require_permission(role, "claim_task")
        plan_id = require_id(plan_id, "plan_id")
        scope_content = "\n".join(f"- {path}" for path in work_scope if path.strip()) if work_scope else ""
        with self.store.plan_lock(plan_id):
            document, title, tasks, graph = self._load_plan_locked(plan_id)
            self._check_revision(document, expected_revision)
            task = self._find_task(tasks, task_id)
            if task["Role"] != role:
                raise RoleMismatchError(f"task '{task_id}' requires role '{task['Role']}'")
            if not self._is_executable(task, tasks, graph):
                raise NotExecutableError(f"task '{task_id}' is not executable")
            task["Status"] = "in_progress"
            task["Started By"] = actor_id
            task["Started"] = _now()
            self._commit_plan(
                plan_id, document, title, tasks,
                event_type="task_started", actor_id=actor_id, task_id=task_id, role=role,
                event_content=scope_content,
            )
        delivery = self.flush_event_outbox(plan_id)
        return self._task_result(task, plan_id, delivery)

    def update_task(
        self,
        task_id: str,
        actor_id: str,
        role: str,
        status: str,
        expected_revision: int,
        plan_id: str = DEFAULT_PLAN_ID,
    ) -> dict[str, Any]:
        task_id, actor_id = require_id(task_id, "task_id"), require_actor(actor_id)
        plan_id = require_id(plan_id, "plan_id")
        if status == "pending":
            role = require_permission(role, "recover_task")
            event_type = "task_recovered"
        elif status in {"done", "blocked", "cancelled"}:
            role = require_permission(role, "update_task")
            event_type = {"done": "task_completed", "blocked": "task_blocked", "cancelled": "task_cancelled"}[status]
        else:
            raise InvalidTransitionError("task status must be done, blocked, cancelled, or pending")
        with self.store.plan_lock(plan_id):
            document, title, tasks, graph = self._load_plan_locked(plan_id)
            self._check_revision(document, expected_revision)
            task = self._find_task(tasks, task_id)
            metadata_overrides: dict[str, Any] | None = None
            if status == "pending":
                if task["Status"] != "blocked":
                    raise InvalidTransitionError("only blocked tasks can be recovered to pending")
                task["Status"] = "pending"
                task["Started By"] = ""
                task["Started"] = ""
                task["Completed By"] = ""
                task["Completed"] = ""
            else:
                if task["Role"] != role:
                    raise RoleMismatchError(f"task '{task_id}' requires role '{task['Role']}'")
                if task["Status"] != "in_progress":
                    raise InvalidTransitionError("only in_progress tasks can be completed, blocked, or cancelled")
                if task["Started By"] != actor_id:
                    raise ClaimOwnerError(f"task '{task_id}' is claimed by '{task['Started By']}'")
                active_dependents = self._non_terminal_dependents(task_id, tasks, graph)
                if status == "cancelled" and active_dependents:
                    raise InvalidTransitionError(
                        "cannot cancel a task with non-terminal dependents: " + ", ".join(active_dependents)
                    )
                task["Status"] = status
                if status == "cancelled":
                    priority_map = priorities(document.metadata, {item["ID"] for item in tasks})
                    priority_map.pop(task_id, None)
                    metadata_overrides = {"priorities": priority_map}
                if status == "done":
                    task["Completed By"] = actor_id
                    task["Completed"] = _now()
            self._commit_plan(
                plan_id, document, title, tasks,
                event_type=event_type, actor_id=actor_id, task_id=task_id, role=role,
                metadata_overrides=metadata_overrides,
            )
        delivery = self.flush_event_outbox(plan_id)
        return self._task_result(task, plan_id, delivery)

    def write_memory(
        self,
        document_id: str,
        content: str,
        actor_id: str,
        role: str,
        expected_revision: int | None = None,
        importance: str = "normal",
        tags: list[str] | None = None,
    ) -> dict[str, Any]:
        require_id(document_id)
        require_actor(actor_id)
        require_permission(role, "write_memory")
        path = self.store.memory_path(document_id)
        extras = {"importance": importance, "tags": tags or [], "source": actor_id}
        document = self._write_generic(path, "memory", document_id, content, expected_revision, extras)
        return self._document_public(document)

    def read_memory(self, document_id: str) -> dict[str, Any]:
        require_id(document_id)
        return self._document_public(self.store.read(self.store.memory_path(document_id), "memory"))

    def list_memory(self) -> list[dict[str, Any]]:
        """List Memory metadata in deterministic ID order without full content."""
        memories: list[dict[str, Any]] = []
        for document_id in self.store.list_memory_ids():
            document = self.store.read(self.store.memory_path(document_id), "memory")
            memories.append(
                {
                    "id": document.metadata["id"],
                    "updated": document.metadata["updated"],
                    "importance": document.metadata.get("importance", "normal"),
                    "tags": document.metadata.get("tags", []),
                    "source": document.metadata.get("source"),
                }
            )
        return memories

    def write_state(
        self,
        document_id: str,
        content: str,
        actor_id: str,
        role: str,
        status: str,
        expected_revision: int | None = None,
        current_task: str | None = None,
    ) -> dict[str, Any]:
        require_id(document_id)
        require_actor(actor_id)
        require_permission(role, "write_state")
        if status not in TASK_STATUSES:
            raise ValidationError("state status must be a valid task status")
        extras: dict[str, Any] = {"status": status, "source": actor_id}
        if current_task is not None:
            extras["current_task"] = require_id(current_task, "current_task")
        document = self._write_generic(self.store.state_path(document_id), "state", document_id, content, expected_revision, extras)
        return self._document_public(document)

    def read_state(self, document_id: str) -> dict[str, Any]:
        require_id(document_id)
        document = self.store.read(self.store.state_path(document_id), "state")
        public = self._document_public(document)
        age_days = (datetime.now(timezone.utc) - _parse_timestamp(str(document.metadata["updated"]), "updated")).days
        if age_days >= STALE_STATE_DAYS:
            public["stale_warning"] = (
                f"Not updated in {age_days} day(s) (since {document.metadata['updated']}). "
                "This may no longer reflect the current situation; consider write_state if you can confirm it."
            )
        return public

    def list_state(self) -> list[dict[str, Any]]:
        """List State metadata in deterministic ID order without full content."""
        states: list[dict[str, Any]] = []
        for document_id in self.store.list_state_ids():
            document = self.store.read(self.store.state_path(document_id), "state")
            state: dict[str, Any] = {
                "id": document.metadata["id"],
                "updated": document.metadata["updated"],
                "status": document.metadata.get("status"),
                "source": document.metadata.get("source"),
            }
            if "current_task" in document.metadata:
                state["current_task"] = document.metadata["current_task"]
            age_days = (datetime.now(timezone.utc) - _parse_timestamp(str(document.metadata["updated"]), "updated")).days
            if age_days >= STALE_STATE_DAYS:
                state["stale_warning"] = f"Not updated in {age_days} day(s) (since {document.metadata['updated']})."
            states.append(state)
        return states

    def emit_event(
        self,
        event_type: str,
        actor_id: str,
        content: str = "",
        task_id: str | None = None,
        event_id: str | None = None,
        role: str | None = None,
        *,
        enforce_permissions: bool = True,
    ) -> dict[str, Any]:
        require_actor(actor_id)
        if enforce_permissions:
            if role is None:
                raise ValidationError("role is required")
            role = require_permission(role, "emit_event")
        if not event_type or not event_type.strip():
            raise ValidationError("event_type is required")
        if task_id is not None:
            require_id(task_id, "task_id")
        event_id = event_id or f"event_{uuid.uuid4().hex}"
        require_id(event_id, "event_id")
        path = self.store.event_path(event_id)
        if path.exists():
            return self._document_public(self.store.read(path, "event"))
        timestamp = _now()
        metadata: dict[str, Any] = {
            "id": event_id,
            "type": "event",
            "created": timestamp,
            "updated": timestamp,
            "revision": 1,
            "event_type": event_type,
            "source": actor_id,
        }
        if task_id is not None:
            metadata["task_id"] = task_id
        if role is not None:
            metadata["role"] = require_role(role)
        return self._document_public(self.store.write(path, metadata, content))

    def read_events(
        self,
        event_type: str | None = None,
        task_id: str | None = None,
        limit: int = 50,
        since: str | None = None,
        until: str | None = None,
    ) -> list[dict[str, Any]]:
        if task_id is not None:
            require_id(task_id, "task_id")
        if limit < 1 or limit > 500:
            raise ValidationError("limit must be between 1 and 500")
        since_dt = _parse_timestamp(since, "since") if since is not None else None
        until_dt = _parse_timestamp(until, "until") if until is not None else None
        if since_dt is not None and until_dt is not None and since_dt > until_dt:
            raise ValidationError("since must be less than or equal to until")
        events = self.store.list_events()
        return [
            self._document_public(event)
            for event in events
            if (event_type is None or event.metadata.get("event_type") == event_type)
            and (task_id is None or event.metadata.get("task_id") == task_id)
            and self._event_in_time_range(event, since_dt, until_dt)
        ][:limit]

    @staticmethod
    def _event_in_time_range(
        event: Document,
        since_dt: datetime | None,
        until_dt: datetime | None,
    ) -> bool:
        if since_dt is None and until_dt is None:
            return True
        created = _parse_timestamp(str(event.metadata["created"]), "created")
        if since_dt is not None and created < since_dt:
            return False
        if until_dt is not None and created > until_dt:
            return False
        return True

    def flush_event_outbox(self, plan_id: str = DEFAULT_PLAN_ID) -> dict[str, Any]:
        """Deliver pending Plan events. Safe to call repeatedly after a crash."""
        plan_id = require_id(plan_id, "plan_id")
        delivered = 0
        while True:
            with self.store.plan_lock(plan_id):
                document, title, tasks, _ = self._load_plan_locked(plan_id)
                pending = next((entry for entry in document.metadata.get("outbox", []) if entry.get("status") == "pending"), None)
                if pending is None:
                    return {"status": "delivered", "delivered": delivered, "revision": document.metadata["revision"], "plan_id": plan_id}
                event = dict(pending)
            try:
                self.emit_event(
                    event_type=event["event_type"],
                    actor_id=event["source"],
                    content=event.get("content", ""),
                    task_id=event.get("task_id"),
                    event_id=event["id"],
                    role=event.get("role"),
                    enforce_permissions=False,
                )
            except OSError:
                plan = self.store.read(self.store.plan_path(plan_id), "plan")
                return {"status": "pending", "delivered": delivered, "revision": plan.metadata["revision"], "plan_id": plan_id}
            with self.store.plan_lock(plan_id):
                document, title, tasks, _ = self._load_plan_locked(plan_id)
                for entry in document.metadata.get("outbox", []):
                    if entry.get("id") == event["id"]:
                        entry["status"] = "delivered"
                        entry["delivered_at"] = _now()
                        self._commit_plan(plan_id, document, title, tasks)
                        delivered += 1
                        break

    def _write_generic(
        self,
        path: Path,
        document_type: str,
        document_id: str,
        content: str,
        expected_revision: int | None,
        extras: dict[str, Any],
    ) -> Document:
        if path.exists():
            existing = self.store.read(path, document_type)
            self._check_revision(existing, expected_revision)
            metadata = dict(existing.metadata)
            metadata.update(extras)
            metadata["updated"] = _now()
            metadata["revision"] += 1
        else:
            if expected_revision is not None:
                raise ConflictError(f"document '{document_id}' does not exist")
            timestamp = _now()
            metadata = {"id": document_id, "type": document_type, "created": timestamp, "updated": timestamp, "revision": 1, **extras}
        return self.store.write(path, metadata, content)

    def _load_plan_locked(self, plan_id: str = DEFAULT_PLAN_ID) -> tuple[Document, str, list[dict[str, str]], dict[str, list[str]]]:
        document = self.store.read(self.store.plan_path(plan_id), "plan")
        title, tasks = parse_plan_body(document.body)
        return document, title, tasks, dependencies(document.metadata, {task["ID"] for task in tasks})

    def _commit_plan(
        self,
        plan_id: str,
        document: Document,
        title: str,
        tasks: list[dict[str, str]],
        event_type: str | None = None,
        actor_id: str | None = None,
        task_id: str | None = None,
        role: str | None = None,
        metadata_overrides: dict[str, Any] | None = None,
        event_content: str = "",
    ) -> Document:
        metadata = dict(document.metadata)
        if metadata_overrides:
            metadata.update(metadata_overrides)
        metadata["updated"] = _now()
        metadata["revision"] += 1
        if event_type is not None:
            event_id = f"task_{task_id}_{event_type}_r{metadata['revision']}"
            outbox_entry = {
                "id": event_id,
                "event_type": event_type,
                "source": actor_id,
                "task_id": task_id,
                "role": role,
                "status": "pending",
            }
            if event_content:
                outbox_entry["content"] = event_content
            metadata.setdefault("outbox", []).append(outbox_entry)
        return self.store.write(self.store.plan_path(plan_id), metadata, render_plan_body(title, tasks))

    @staticmethod
    def _check_revision(document: Document, expected_revision: int | None) -> None:
        if expected_revision is None or document.metadata["revision"] != expected_revision:
            raise ConflictError(
                f"expected revision {expected_revision}, actual revision {document.metadata['revision']}. "
                "Someone else updated this document first. Re-read it (its response includes the current "
                "revision) and retry your call with that revision."
            )

    @staticmethod
    def _find_task(tasks: list[dict[str, str]], task_id: str) -> dict[str, str]:
        try:
            return next(task for task in tasks if task["ID"] == task_id)
        except StopIteration as exc:
            raise NotFoundError(f"task '{task_id}' does not exist") from exc

    @staticmethod
    def _is_executable(task: dict[str, str], tasks: list[dict[str, str]], graph: dict[str, list[str]]) -> bool:
        if task["Status"] != "pending":
            return False
        statuses = {item["ID"]: item["Status"] for item in tasks}
        return all(statuses[dependency_id] == "done" for dependency_id in graph.get(task["ID"], []))

    @staticmethod
    def _non_terminal_dependents(
        task_id: str,
        tasks: list[dict[str, str]],
        graph: dict[str, list[str]],
    ) -> list[str]:
        return [
            item["ID"]
            for item in tasks
            if task_id in graph.get(item["ID"], []) and item["Status"] not in {"done", "cancelled"}
        ]

    @staticmethod
    def _task_audit_issues(tasks: list[dict[str, str]]) -> list[str]:
        issues: list[str] = []
        for task in tasks:
            task_id = task["ID"]
            status = task["Status"]
            started = task["Started"]
            completed = task["Completed"]
            if status == "pending" and any(task[field] for field in ("Started By", "Started", "Completed By", "Completed")):
                issues.append(f"pending task '{task_id}' must not contain claim or completion fields")
            if status == "in_progress":
                if not task["Started By"] or not started:
                    issues.append(f"in_progress task '{task_id}' must contain Started By and Started")
                if task["Completed By"] or completed:
                    issues.append(f"in_progress task '{task_id}' must not contain completion fields")
            if status == "done":
                if not task["Started By"] or not started or not task["Completed By"] or not completed:
                    issues.append(f"done task '{task_id}' must contain claim and completion fields")
            for field_name, value in (("Started", started), ("Completed", completed)):
                if value:
                    try:
                        _parse_timestamp(value, f"task '{task_id}' {field_name}")
                    except ValidationError as exc:
                        issues.append(str(exc))
        return issues

    @staticmethod
    def _outbox_issues(raw_outbox: object, task_ids: set[str]) -> list[str]:
        if not isinstance(raw_outbox, list):
            return ["outbox must be a list"]
        issues: list[str] = []
        seen_ids: set[str] = set()
        for index, entry in enumerate(raw_outbox):
            label = f"outbox entry {index}"
            if not isinstance(entry, dict):
                issues.append(f"{label} must be a mapping")
                continue
            try:
                event_id = require_id(str(entry.get("id", "")), f"{label} id")
                require_actor(str(entry.get("source", "")))
            except ValidationError as exc:
                issues.append(str(exc))
                continue
            if event_id in seen_ids:
                issues.append(f"{label} id '{event_id}' is duplicated")
            seen_ids.add(event_id)
            if not isinstance(entry.get("event_type"), str) or not entry["event_type"].strip():
                issues.append(f"{label} event_type must be a non-empty string")
            task_id = entry.get("task_id")
            if task_id not in task_ids:
                issues.append(f"{label} task_id must reference a Plan task")
            status = entry.get("status")
            if status not in {"pending", "delivered"}:
                issues.append(f"{label} status must be pending or delivered")
            if status == "delivered":
                delivered_at = entry.get("delivered_at")
                if not delivered_at:
                    issues.append(f"{label} delivered_at is required when delivered")
                else:
                    try:
                        _parse_timestamp(str(delivered_at), f"{label} delivered_at")
                    except ValidationError as exc:
                        issues.append(str(exc))
        return issues

    @staticmethod
    def _new_task(task: dict[str, str]) -> dict[str, str]:
        task_id = require_id(task.get("id", ""), "task ID")
        role = require_role(task.get("role", ""))
        name = BlackboardService._validate_task_name(task.get("task", ""))
        return dict(zip(TASK_COLUMNS, (task_id, name, role, "pending", "", "", "", ""), strict=True))

    @staticmethod
    def _validate_task_name(value: object) -> str:
        name = str(value).strip()
        if not name or "|" in name:
            raise ValidationError("task must be non-empty and must not contain '|'")
        return name

    @staticmethod
    def _task_result(task: dict[str, str], plan_id: str, delivery: dict[str, Any]) -> dict[str, Any]:
        return {
            "task": BlackboardService._task_public(task),
            "plan_id": plan_id,
            "plan_revision": delivery["revision"],
            "event_delivery": delivery["status"],
        }

    @staticmethod
    def _task_public(task: dict[str, str], priority: str | None = None) -> dict[str, str]:
        public = {
            "id": task["ID"],
            "task": task["Task"],
            "role": task["Role"],
            "status": task["Status"],
            "started_by": task["Started By"],
            "started": task["Started"],
            "completed_by": task["Completed By"],
            "completed": task["Completed"],
        }
        if priority is not None:
            public["priority"] = priority
        return public

    @staticmethod
    def _document_public(document: Document) -> dict[str, Any]:
        return {"id": document.metadata["id"], "frontmatter": document.metadata, "content": document.body}
