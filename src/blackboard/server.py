"""stdio MCP server for the Blackboard MVP."""

from __future__ import annotations

import os
import subprocess
import threading
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from .service import BlackboardService
from .store import DEFAULT_PLAN_ID

_SERVICES: dict[str, BlackboardService] = {}
_SERVICES_LOCK = threading.Lock()
_STARTED_AT = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _server_version() -> str:
    try:
        return version("llm-collaboration-blackboard")
    except PackageNotFoundError:
        return "unknown"


def _git_sha() -> str | None:
    """Best-effort git HEAD SHA of the checkout this server was started from.

    Diagnostic only: helps tell a stale MCP connection (old tool schema)
    apart from an actual bug, since a long-lived stdio session keeps the
    tool list it had at startup even after the source changes on disk.
    """
    repo_root = Path(__file__).resolve().parents[2]
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=2,
            # This server's own stdin is the live MCP stdio transport pipe.
            # Without this, git would inherit that handle by default, which
            # hangs/fails on Windows when the parent's stdio reader is
            # concurrently active. git never reads stdin for this command,
            # so it is safe to detach.
            stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def _service() -> BlackboardService:
    root = os.environ.get("BLACKBOARD_ROOT")
    if not root:
        raise ValueError("BLACKBOARD_ROOT must point to a Blackboard directory")
    key = str(Path(root).resolve())
    with _SERVICES_LOCK:
        service = _SERVICES.get(key)
        if service is None:
            service = BlackboardService(root)
            _SERVICES[key] = service
        return service


mcp = FastMCP(
    "llm-collaboration-blackboard",
    instructions=(
        "Markdown Blackboard access layer. This server never chooses an LLM or decomposes work. "
        "Use role-specific executable tasks from read_plan, then claim_task with the revision you read. "
        "All actor_id and role values must be supplied explicitly to mutating task tools. "
        "A Blackboard root may contain multiple named Plans; omit plan_id to use the default 'project' Plan. "
        "Memory, State, and Event documents are shared across Plans in the same root. "
        "add_task appends one task to an existing Plan; task identity and dependency targets are validated "
        "and the whole dependency graph is re-checked for cycles. "
        "Facilitators may set an advisory P0-P3 priority only for pending tasks; priority never changes "
        "claim eligibility or automatically schedules work. "
        "Call get_server_info if an expected tool seems to be missing from this connection's tool list."
    ),
)


@mcp.tool()
def get_server_info() -> dict[str, Any]:
    """Report this MCP server process's version, git SHA, start time, and Blackboard root.

    A long-lived stdio connection keeps the tool schema it had at startup,
    so a tool added to the source after connecting (e.g. a newly released
    one) will not appear until a fresh connection is made. If an expected
    tool seems to be missing, call this first: an old started_at/git_sha
    relative to the source you expect means the connection is stale, not
    that the feature is missing.
    """
    return {
        "server_version": _server_version(),
        "git_sha": _git_sha(),
        "started_at": _STARTED_AT,
        "blackboard_root": os.environ.get("BLACKBOARD_ROOT"),
    }


@mcp.tool()
def read_memory(id: str) -> dict[str, Any]:
    """Read a persistent Memory document by id."""
    return _service().read_memory(id)


@mcp.tool()
def list_memory() -> list[dict[str, Any]]:
    """List Memory metadata in deterministic ID order without full content."""
    return _service().list_memory()


@mcp.tool()
def write_memory(
    id: str,
    content: str,
    actor_id: str,
    role: str,
    expected_revision: int | None = None,
    importance: str = "normal",
    tags: list[str] | None = None,
) -> dict[str, Any]:
    """Create or update Memory. Updates require the revision returned by read_memory."""
    return _service().write_memory(id, content, actor_id, role, expected_revision, importance, tags)


@mcp.tool()
def list_plans() -> list[dict[str, Any]]:
    """List named Plans in this Blackboard root, each with its in_progress tasks.

    Check this before claiming work when multiple actors may be active: it
    shows what is already claimed, by whom, across every Plan in this root.
    """
    return _service().list_plans()


@mcp.tool()
def list_actor_roles() -> dict[str, list[str]]:
    """Report which roles each actor_id has claimed tasks under, across every Plan.

    This is an observation aid, not authentication: actors still self-declare
    their role on every call, and nothing here is enforced. Use it to sanity
    check who has been active and under which roles before trusting a
    Memory/Event entry attributed to an unfamiliar actor_id.
    """
    return _service().list_actor_roles()


@mcp.tool()
def read_plan(role: str | None = None, plan_id: str = DEFAULT_PLAN_ID) -> dict[str, Any]:
    """Read one Plan and its executable tasks, optionally for one Role."""
    return _service().read_plan(role, plan_id)


@mcp.tool()
def validate_plan(plan_id: str = DEFAULT_PLAN_ID) -> dict[str, Any]:
    """Diagnose Plan metadata, task audit fields, dependencies, and Outbox without changing it."""
    return _service().validate_plan(plan_id)


@mcp.tool()
def add_task(
    task: dict[str, str],
    actor_id: str,
    role: str,
    expected_revision: int,
    plan_id: str = DEFAULT_PLAN_ID,
    dependencies: list[str] | None = None,
    reason: str | None = None,
    priority: str | None = None,
) -> dict[str, Any]:
    """Append one new task to an already-initialized Plan. A stale revision is rejected as a conflict.

    ``task`` uses the same shape as one entry of initialize_project's ``tasks``
    (id/task/role). ``dependencies`` lists the IDs of tasks the new task
    requires; targets must already exist in the Plan.
    """
    return _service().add_task(task, actor_id, role, expected_revision, plan_id, dependencies, reason, priority)


@mcp.tool()
def edit_task(
    task_id: str,
    actor_id: str,
    role: str,
    expected_revision: int,
    task: str | None = None,
    task_role: str | None = None,
    dependencies: list[str] | None = None,
    plan_id: str = DEFAULT_PLAN_ID,
    reason: str | None = None,
    priority: str | None = None,
) -> dict[str, Any]:
    """Edit a pending task's text, required Role, or dependencies with revision CAS.

    Task IDs are immutable. Pass ``dependencies=[]`` to remove all dependencies.
    """
    return _service().edit_task(task_id, actor_id, role, expected_revision, task, task_role, dependencies, plan_id, reason, priority)


@mcp.tool()
def cancel_task(
    task_id: str,
    actor_id: str,
    role: str,
    expected_revision: int,
    plan_id: str = DEFAULT_PLAN_ID,
    reason: str | None = None,
) -> dict[str, Any]:
    """Logically cancel a pending or owned in-progress task with an audit Event."""
    return _service().cancel_task(task_id, actor_id, role, expected_revision, plan_id, reason)


@mcp.tool()
def set_task_priority(
    task_id: str,
    priority: str,
    actor_id: str,
    role: str,
    reason: str,
    expected_revision: int,
    plan_id: str = DEFAULT_PLAN_ID,
) -> dict[str, Any]:
    """Set a pending task's advisory P0-P3 priority with CAS and an audit Event.

    Priority is human-facing backlog guidance. It does not choose, claim, or
    block work for any LLM.
    """
    return _service().set_task_priority(task_id, priority, actor_id, role, reason, expected_revision, plan_id)


@mcp.tool()
def recover_task(
    task_id: str,
    actor_id: str,
    role: str,
    expected_revision: int,
    plan_id: str = DEFAULT_PLAN_ID,
) -> dict[str, Any]:
    """Recover a blocked task to pending; the declared Role needs recover_task permission."""
    return _service().recover_task(task_id, actor_id, role, expected_revision, plan_id)


@mcp.tool()
def claim_task(
    task_id: str,
    actor_id: str,
    role: str,
    expected_revision: int,
    plan_id: str = DEFAULT_PLAN_ID,
    work_scope: list[str] | None = None,
) -> dict[str, Any]:
    """Atomically claim one executable task. A stale revision is rejected as a conflict.

    ``work_scope`` optionally declares the files/paths you expect to touch
    (e.g. ["src/blackboard/service.py", "tests/test_service.py"]). It is
    recorded on the task_started Event for other actors to see before they
    start their own work, but it is not enforced.
    """
    return _service().claim_task(task_id, actor_id, role, expected_revision, plan_id, work_scope)


@mcp.tool()
def update_task(
    task_id: str,
    actor_id: str,
    role: str,
    status: str,
    expected_revision: int,
    plan_id: str = DEFAULT_PLAN_ID,
) -> dict[str, Any]:
    """Finish, block, cancel, or recover a task.

    Claim owners may set done/blocked/cancelled on in_progress tasks.
    Roles with recover_task permission may set pending on blocked tasks.
    The Plan state is authoritative. Its corresponding audit Event is placed in
    the Plan Outbox and is retried idempotently if Event persistence fails.
    """
    return _service().update_task(task_id, actor_id, role, status, expected_revision, plan_id)


@mcp.tool()
def read_state(id: str) -> dict[str, Any]:
    """Read a State document by id.

    The response includes a ``stale_warning`` field if the document hasn't
    been updated in a while. This is a read-time nudge only — State is never
    auto-rewritten from Plan (see CONTRIBUTING.md).
    """
    return _service().read_state(id)


@mcp.tool()
def list_state() -> list[dict[str, Any]]:
    """List State metadata in deterministic ID order without full content."""
    return _service().list_state()


@mcp.tool()
def write_state(
    id: str,
    content: str,
    actor_id: str,
    role: str,
    status: str,
    expected_revision: int | None = None,
    current_task: str | None = None,
) -> dict[str, Any]:
    """Create or update current State. Updates require the revision returned by read_state."""
    return _service().write_state(id, content, actor_id, role, status, expected_revision, current_task)


@mcp.tool()
def read_event(
    event_type: str | None = None,
    task_id: str | None = None,
    limit: int = 50,
    since: str | None = None,
    until: str | None = None,
) -> list[dict[str, Any]]:
    """List audit Events, optionally filtered by type, task, or created time range."""
    return _service().read_events(event_type, task_id, limit, since, until)


@mcp.tool()
def emit_event(
    event_type: str,
    actor_id: str,
    role: str,
    content: str = "",
    task_id: str | None = None,
) -> dict[str, Any]:
    """Append an independent audit Event. This tool does not change Plan state."""
    return _service().emit_event(event_type, actor_id, content, task_id, role=role)


@mcp.tool()
def initialize_project(
    project_id: str,
    title: str,
    tasks: list[dict[str, str]],
    dependencies: dict[str, list[str]] | None = None,
    plan_id: str = DEFAULT_PLAN_ID,
) -> dict[str, Any]:
    """Create the Blackboard directories and one named Plan file.

    This bootstrap utility is used by a human when creating a project. LLM
    collaboration thereafter uses the core Blackboard tools above. Additional
    Plans may be created in the same root by calling this again with a new plan_id.
    """
    return _service().initialize_project(project_id, title, tasks, dependencies, plan_id)


@mcp.tool()
def flush_event_outbox(plan_id: str = DEFAULT_PLAN_ID) -> dict[str, Any]:
    """Retry pending Plan-generated Events after a previous Event write failure."""
    return _service().flush_event_outbox(plan_id)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
