from __future__ import annotations

import asyncio
import multiprocessing
import os
import sys
from pathlib import Path

import pytest

from blackboard.errors import ConflictError, ValidationError
from blackboard.service import BlackboardService


def _create_project(root: str | Path) -> BlackboardService:
    service = BlackboardService(root)
    service.initialize_project(
        "alpha",
        "Project Alpha",
        [
            {"id": "research", "task": "Research", "role": "Researcher"},
            {"id": "implement", "task": "Implement", "role": "Implementer"},
            {"id": "security", "task": "Security review", "role": "Reviewer"},
            {"id": "performance", "task": "Performance review", "role": "Reviewer"},
        ],
        {"implement": ["research"], "security": ["implement"], "performance": ["implement"]},
    )
    return service


def _claim_in_process(root: str, task_id: str, actor_id: str, role: str, revision: int, result_queue) -> None:
    try:
        result = BlackboardService(root).claim_task(task_id, actor_id, role, revision)
        result_queue.put(("claimed", result["task"]["id"]))
    except ConflictError:
        result_queue.put(("conflict", task_id))


def test_cross_process_same_task_claim_has_one_winner(tmp_path):
    root = tmp_path / "blackboard"
    service = _create_project(root)
    revision = service.read_plan()["revision"]
    context = multiprocessing.get_context("spawn")
    results = context.Queue()
    processes = [
        context.Process(target=_claim_in_process, args=(str(root), "research", actor, "Researcher", revision, results))
        for actor in ("llm_a", "llm_b")
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=15)
        assert process.exitcode == 0
    outcomes = [results.get(timeout=2)[0] for _ in processes]
    assert sorted(outcomes) == ["claimed", "conflict"]


def test_cross_process_parallel_tasks_can_both_be_claimed(tmp_path):
    root = tmp_path / "blackboard"
    service = _create_project(root)

    research = service.claim_task("research", "llm_a", "Researcher", service.read_plan()["revision"])
    service.update_task("research", "llm_a", "Researcher", "done", research["plan_revision"])
    implementation = service.claim_task("implement", "llm_b", "Implementer", service.read_plan()["revision"])
    service.update_task("implement", "llm_b", "Implementer", "done", implementation["plan_revision"])

    revision = service.read_plan()["revision"]
    context = multiprocessing.get_context("spawn")
    results = context.Queue()
    first = context.Process(target=_claim_in_process, args=(str(root), "security", "llm_c", "Reviewer", revision, results))
    second = context.Process(target=_claim_in_process, args=(str(root), "performance", "llm_d", "Reviewer", revision, results))
    first.start()
    second.start()
    first.join(timeout=15)
    second.join(timeout=15)
    assert first.exitcode == 0
    assert second.exitcode == 0

    # One client may observe a stale revision. It rereads and claims its still-pending independent task.
    outcomes = [results.get(timeout=2) for _ in range(2)]
    if any(outcome[0] == "conflict" for outcome in outcomes):
        plan = service.read_plan("Reviewer")
        remaining = plan["executable_tasks"]
        assert len(remaining) == 1
        service.claim_task(remaining[0]["id"], "llm_retry", "Reviewer", plan["revision"])
    statuses = {task["id"]: task["status"] for task in service.read_plan()["tasks"]}
    assert statuses["security"] == "in_progress"
    assert statuses["performance"] == "in_progress"


def test_human_markdown_edit_is_visible_after_restart(tmp_path):
    root = tmp_path / "blackboard"
    service = _create_project(root)
    service.write_state("project_state", "Initial status", "human_1", "Implementer", "in_progress")
    state_path = root / "state" / "project_state.md"
    state_path.write_text(state_path.read_text(encoding="utf-8").replace("Initial status", "Edited by a human"), encoding="utf-8")

    restarted_service = BlackboardService(root)
    assert restarted_service.read_state("project_state")["content"] == "Edited by a human\n"


def test_invalid_human_plan_edit_is_rejected(tmp_path):
    root = tmp_path / "blackboard"
    _create_project(root)
    (root / "plan" / "project.md").write_text("# missing front matter\n", encoding="utf-8")
    with pytest.raises(ValidationError):
        BlackboardService(root).read_plan()


def test_mcp_stdio_contract_exposes_and_calls_core_tools(tmp_path):
    import json

    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    root = tmp_path / "blackboard"
    _create_project(root)

    async def exercise_server() -> None:
        parameters = StdioServerParameters(
            command=sys.executable,
            args=["-m", "blackboard.server"],
            env={**os.environ, "BLACKBOARD_ROOT": str(root)},
        )
        async with stdio_client(parameters) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                tool_names = {tool.name for tool in (await session.list_tools()).tools}
                assert {
                    "read_memory", "list_memory", "write_memory", "read_plan", "validate_plan",
                    "add_task", "edit_task", "cancel_task", "set_task_priority", "claim_task", "update_task", "recover_task",
                    "read_state", "list_state", "write_state", "read_event", "emit_event", "list_plans",
                } <= tool_names

                async def call(name, arguments):
                    result = await session.call_tool(name, arguments)
                    assert not result.isError, result.content
                    values = [json.loads(item.text) for item in result.content]
                    return values if name in {"list_memory", "list_state", "read_event"} else values[0]

                await call(
                    "write_memory",
                    {"id": "shared_fact", "content": "A shared fact.", "actor_id": "llm_a", "role": "Researcher"},
                )
                assert [item["id"] for item in await call("list_memory", {})] == ["shared_fact"]
                plan = await call("validate_plan", {})
                assert plan["valid"] is True

                edited = await call(
                    "edit_task",
                    {
                        "task_id": "implement",
                        "actor_id": "llm_b",
                        "role": "Implementer",
                        "expected_revision": plan["revision"],
                        "task": "Implement through MCP",
                    },
                )
                assert edited["task"]["task"] == "Implement through MCP"
                prioritized = await call(
                    "set_task_priority",
                    {
                        "task_id": "research",
                        "priority": "P0",
                        "actor_id": "facilitator_1",
                        "role": "Facilitator",
                        "reason": "Prioritize discovery before implementation.",
                        "expected_revision": edited["plan_revision"],
                    },
                )
                assert prioritized["task"]["priority"] == "P0"
                claim = await call(
                    "claim_task",
                    {
                        "task_id": "research",
                        "actor_id": "llm_a",
                        "role": "Researcher",
                        "expected_revision": prioritized["plan_revision"],
                    },
                )
                blocked = await call(
                    "update_task",
                    {
                        "task_id": "research",
                        "actor_id": "llm_a",
                        "role": "Researcher",
                        "status": "blocked",
                        "expected_revision": claim["plan_revision"],
                    },
                )
                recovered = await call(
                    "recover_task",
                    {
                        "task_id": "research",
                        "actor_id": "reviewer_1",
                        "role": "Reviewer",
                        "expected_revision": blocked["plan_revision"],
                    },
                )
                assert recovered["task"]["status"] == "pending"
                cancelled = await call(
                    "cancel_task",
                    {
                        "task_id": "performance",
                        "actor_id": "reviewer_1",
                        "role": "Reviewer",
                        "expected_revision": recovered["plan_revision"],
                    },
                )
                assert cancelled["task"]["status"] == "cancelled"
                await call(
                    "write_state",
                    {
                        "id": "current",
                        "content": "Working.",
                        "actor_id": "llm_b",
                        "role": "Implementer",
                        "status": "in_progress",
                        "current_task": "implement",
                    },
                )
                assert [item["id"] for item in await call("list_state", {})] == ["current"]
                event = await call(
                    "emit_event",
                    {"event_type": "note", "actor_id": "llm_a", "role": "Researcher", "content": "Observed."},
                )
                assert event["frontmatter"]["role"] == "Researcher"

    asyncio.run(exercise_server())
    assert BlackboardService(root).read_memory("shared_fact")["content"] == "A shared fact.\n"


def test_get_server_info_reports_version_and_root(tmp_path):
    import json

    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    root = tmp_path / "blackboard"
    _create_project(root)

    async def exercise_server() -> dict:
        parameters = StdioServerParameters(
            command=sys.executable,
            args=["-m", "blackboard.server"],
            env={**os.environ, "BLACKBOARD_ROOT": str(root)},
        )
        async with stdio_client(parameters) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                result = await session.call_tool("get_server_info", {})
                assert not result.isError
                return json.loads(result.content[0].text)

    info = asyncio.run(exercise_server())
    assert info["blackboard_root"] == str(root)
    assert info["server_version"] != "unknown"
    assert info["started_at"].endswith("Z")
    # This test runs inside the project's own git checkout, so git_sha must
    # resolve. A regression here previously slipped through unnoticed because
    # git rev-parse inherited this server's own MCP-stdio stdin handle and
    # failed silently on Windows; _git_sha now detaches stdin explicitly.
    assert info["git_sha"] is not None
    assert len(info["git_sha"]) == 40
