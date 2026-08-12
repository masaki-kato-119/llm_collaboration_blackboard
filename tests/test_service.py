from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest

from blackboard.errors import ClaimOwnerError, ConflictError, InvalidTransitionError, PermissionDeniedError, ValidationError
from blackboard.service import BlackboardService


@pytest.fixture()
def service(tmp_path):
    instance = BlackboardService(tmp_path / "blackboard")
    instance.initialize_project(
        "alpha",
        "Project Alpha",
        [
            {"id": "research", "task": "Research", "role": "Researcher"},
            {"id": "implement", "task": "Implement", "role": "Implementer"},
            {"id": "security", "task": "Security review", "role": "Reviewer"},
            {"id": "performance", "task": "Performance review", "role": "Reviewer"},
            {"id": "final", "task": "Final review", "role": "Reviewer"},
        ],
        {
            "implement": ["research"],
            "security": ["implement"],
            "performance": ["implement"],
            "final": ["security", "performance"],
        },
    )
    return instance


def claim_and_complete(service, task_id, actor_id, role):
    plan = service.read_plan(role)
    claim = service.claim_task(task_id, actor_id, role, plan["revision"])
    return service.update_task(task_id, actor_id, role, "done", claim["plan_revision"])


def test_dependencies_and_parallel_tasks(service):
    assert [task["id"] for task in service.read_plan("Researcher")["executable_tasks"]] == ["research"]

    claim_and_complete(service, "research", "llm_a", "Researcher")
    assert [task["id"] for task in service.read_plan("Implementer")["executable_tasks"]] == ["implement"]

    claim_and_complete(service, "implement", "llm_b", "Implementer")
    executable = service.read_plan("Reviewer")["executable_tasks"]
    assert {task["id"] for task in executable} == {"security", "performance"}

    claim_and_complete(service, "security", "llm_c", "Reviewer")
    assert "final" not in {task["id"] for task in service.read_plan("Reviewer")["executable_tasks"]}
    claim_and_complete(service, "performance", "llm_d", "Reviewer")
    assert {task["id"] for task in service.read_plan("Reviewer")["executable_tasks"]} == {"final"}


def test_concurrent_claim_has_exactly_one_winner(service):
    revision = service.read_plan("Researcher")["revision"]

    def attempt(actor_id):
        try:
            return service.claim_task("research", actor_id, "Researcher", revision)["task"]["started_by"]
        except ConflictError:
            return None

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(attempt, ["llm_a", "llm_b"]))
    assert sorted(result for result in results if result is not None) in (["llm_a"], ["llm_b"])


def test_claim_owner_and_revision_are_enforced(service):
    revision = service.read_plan()["revision"]
    claim = service.claim_task("research", "llm_a", "Researcher", revision)
    with pytest.raises(ClaimOwnerError):
        service.update_task("research", "llm_b", "Researcher", "done", claim["plan_revision"])
    with pytest.raises(ConflictError):
        service.claim_task("research", "llm_b", "Researcher", revision)


def test_conflict_error_tells_the_caller_what_to_do(service):
    revision = service.read_plan()["revision"]
    service.claim_task("research", "llm_a", "Researcher", revision)
    with pytest.raises(ConflictError, match="[Rr]e-read"):
        service.claim_task("research", "llm_b", "Researcher", revision)


def test_outbox_recovers_after_event_write_failure(service, monkeypatch):
    original_emit = service.emit_event

    def fail_event(*args, **kwargs):
        raise OSError("simulated event disk error")

    monkeypatch.setattr(service, "emit_event", fail_event)
    claim = service.claim_task("research", "llm_a", "Researcher", service.read_plan()["revision"])
    assert claim["event_delivery"] == "pending"
    assert service.read_plan()["pending_events"] == 1

    monkeypatch.setattr(service, "emit_event", original_emit)
    recovered = service.flush_event_outbox()
    assert recovered["status"] == "delivered"
    assert service.read_plan()["pending_events"] == 0
    assert [event["frontmatter"]["event_type"] for event in service.read_events()] == ["task_started"]


def test_memory_state_and_events_are_markdown_documents(service):
    memory = service.write_memory("decision_001", "# Decision\n\nUse Markdown.", "llm_a", "Researcher")
    assert memory["frontmatter"]["revision"] == 1
    updated_memory = service.write_memory(
        "decision_001",
        "# Decision\n\nKeep Markdown canonical.",
        "llm_a",
        "Researcher",
        memory["frontmatter"]["revision"],
    )
    assert updated_memory["frontmatter"]["revision"] == 2

    service.write_state(
        "alpha_state",
        "Implementation is active.",
        "llm_b",
        "Implementer",
        "in_progress",
        current_task="implement",
    )
    assert service.read_state("alpha_state")["frontmatter"]["current_task"] == "implement"
    assert "stale_warning" not in service.read_state("alpha_state")
    event = service.emit_event("human_intervention", "human_1", "Priority changed.", role="Implementer")
    assert service.read_events(event_type="human_intervention")[0]["id"] == event["id"]


def test_read_state_warns_when_stale(service, monkeypatch):
    from datetime import datetime, timedelta, timezone

    old_timestamp = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat(timespec="seconds").replace("+00:00", "Z")
    monkeypatch.setattr("blackboard.service._now", lambda: old_timestamp)
    service.write_state("old_state", "Written a while ago.", "llm_a", "Implementer", "in_progress")
    monkeypatch.undo()

    stale = service.read_state("old_state")
    assert stale["stale_warning"].startswith("Not updated in 10 day")

    fresh = service.write_state(
        "old_state", "Updated just now.", "llm_a", "Implementer", "in_progress",
        expected_revision=stale["frontmatter"]["revision"],
    )
    assert "stale_warning" not in service.read_state("old_state")
    assert fresh["frontmatter"]["revision"] == 2


def test_read_events_filters_by_since_and_until(service, monkeypatch):
    timestamps = iter(
        [
            "2026-08-10T10:00:00Z",
            "2026-08-10T11:00:00Z",
            "2026-08-10T12:00:00Z",
        ]
    )
    monkeypatch.setattr("blackboard.service._now", lambda: next(timestamps))

    early = service.emit_event("note", "actor_a", "early", role="Implementer")
    mid = service.emit_event("note", "actor_a", "mid", role="Implementer")
    late = service.emit_event("note", "actor_a", "late", role="Implementer")

    assert [item["id"] for item in service.read_events(event_type="note", since="2026-08-10T11:00:00Z")] == [
        late["id"],
        mid["id"],
    ]
    assert [item["id"] for item in service.read_events(event_type="note", until="2026-08-10T11:00:00Z")] == [
        mid["id"],
        early["id"],
    ]
    assert [item["id"] for item in service.read_events(
        event_type="note",
        since="2026-08-10T11:00:00Z",
        until="2026-08-10T11:00:00Z",
    )] == [mid["id"]]

    with pytest.raises(ValidationError, match="since must be less than or equal to until"):
        service.read_events(since="2026-08-10T12:00:00Z", until="2026-08-10T11:00:00Z")
    with pytest.raises(ValidationError, match="since must be an ISO 8601 timestamp"):
        service.read_events(since="not-a-timestamp")


def test_role_permissions_are_enforced(service):
    with pytest.raises(PermissionDeniedError, match="write_state"):
        service.write_state("denied", "nope", "llm_a", "Researcher", "pending")
    with pytest.raises(PermissionDeniedError, match="recover_task"):
        service.update_task("research", "llm_a", "Implementer", "pending", service.read_plan()["revision"])


def test_blocked_task_can_be_recovered_by_reviewer(service):
    claim = service.claim_task("research", "llm_a", "Researcher", service.read_plan()["revision"])
    blocked = service.update_task("research", "llm_a", "Researcher", "blocked", claim["plan_revision"])
    recovered = service.update_task("research", "reviewer_1", "Reviewer", "pending", blocked["plan_revision"])
    assert recovered["task"]["status"] == "pending"
    assert recovered["task"]["started_by"] == ""
    assert "task_recovered" in {event["frontmatter"]["event_type"] for event in service.read_events()}
    with pytest.raises(InvalidTransitionError):
        service.update_task("research", "reviewer_1", "Reviewer", "pending", recovered["plan_revision"])


def test_event_list_uses_in_memory_cache(service):
    first = service.emit_event("cache_probe", "actor_a", "one", role="Implementer")
    listed = service.store.list_events()
    assert service.store._event_cache is not None
    cached_ids = [item.metadata["id"] for item in listed]
    assert first["id"] in cached_ids
    # A second list should reuse the populated cache.
    assert [item.metadata["id"] for item in service.store.list_events()] == cached_ids
    second = service.emit_event("cache_probe", "actor_a", "two", role="Implementer")
    assert service.store._event_cache is None
    refreshed = [item.metadata["id"] for item in service.store.list_events()]
    assert second["id"] in refreshed
    assert first["id"] in refreshed


def test_multiple_named_plans_share_one_blackboard_root(service, tmp_path):
    second = service.initialize_project(
        "beta",
        "Project Beta",
        [{"id": "explore", "task": "Explore", "role": "Researcher"}],
        plan_id="beta",
    )
    assert second["plan_id"] == "beta"
    assert {item["plan_id"] for item in service.list_plans()} == {"project", "beta"}

    alpha = service.claim_task("research", "llm_a", "Researcher", service.read_plan()["revision"])
    beta = service.claim_task("explore", "llm_b", "Researcher", service.read_plan(plan_id="beta")["revision"], plan_id="beta")
    assert alpha["task"]["status"] == "in_progress"
    assert beta["task"]["status"] == "in_progress"
    assert service.read_plan()["tasks"][0]["status"] == "in_progress"
    assert service.read_plan(plan_id="beta")["tasks"][0]["status"] == "in_progress"

    with pytest.raises(ConflictError):
        service.initialize_project("alpha-again", "Dup", [{"id": "x", "task": "X", "role": "Researcher"}], plan_id="project")


def test_list_plans_shows_in_progress_work_across_plans(service):
    service.initialize_project(
        "beta", "Project Beta", [{"id": "explore", "task": "Explore", "role": "Researcher"}], plan_id="beta",
    )
    service.claim_task("research", "llm_a", "Researcher", service.read_plan()["revision"])
    service.claim_task("explore", "llm_b", "Researcher", service.read_plan(plan_id="beta")["revision"], plan_id="beta")

    by_plan = {entry["plan_id"]: entry["in_progress_tasks"] for entry in service.list_plans()}
    assert [task["id"] for task in by_plan["project"]] == ["research"]
    assert by_plan["project"][0]["started_by"] == "llm_a"
    assert [task["id"] for task in by_plan["beta"]] == ["explore"]
    assert by_plan["beta"][0]["started_by"] == "llm_b"

    # A plan with nothing claimed reports an empty list, not a missing key.
    service.initialize_project("gamma", "Project Gamma", [{"id": "idle", "task": "Idle", "role": "Researcher"}], plan_id="gamma")
    by_plan = {entry["plan_id"]: entry["in_progress_tasks"] for entry in service.list_plans()}
    assert by_plan["gamma"] == []


def test_list_actor_roles_observes_role_history_across_plans(service):
    assert service.list_actor_roles() == {}

    claim_and_complete(service, "research", "llm_a", "Researcher")
    service.initialize_project("beta", "Beta", [{"id": "explore", "task": "Explore", "role": "Implementer"}], plan_id="beta")
    service.claim_task("explore", "llm_a", "Implementer", service.read_plan(plan_id="beta")["revision"], plan_id="beta")

    roles = service.list_actor_roles()
    assert roles["llm_a"] == ["Implementer", "Researcher"]
    assert "llm_b" not in roles


def test_add_task_appends_to_an_existing_plan(service):
    revision = service.read_plan()["revision"]
    added = service.add_task(
        {"id": "followup", "task": "Follow-up work", "role": "Implementer"},
        "facilitator_1",
        "Facilitator",
        revision,
        task_dependencies=["research"],
        reason="Add the approved follow-up work.",
        priority="P1",
    )
    assert added["task"]["id"] == "followup"
    assert added["event_delivery"] == "delivered"

    plan = service.read_plan()
    assert "followup" in {task["id"] for task in plan["tasks"]}
    assert plan["dependencies"]["followup"] == ["research"]
    assert plan["priorities"]["followup"] == "P1"
    # research is still pending, so followup is not yet executable.
    assert "followup" not in {task["id"] for task in plan["executable_tasks"]}
    events = service.read_events(task_id="followup")
    assert [event["frontmatter"]["event_type"] for event in events] == ["task_added"]
    assert events[0]["content"] == "reason: Add the approved follow-up work.\n"

    claim_and_complete(service, "research", "llm_a", "Researcher")
    assert "followup" in {task["id"] for task in service.read_plan()["executable_tasks"]}


def test_add_task_rejects_duplicate_id_and_stale_revision(service):
    revision = service.read_plan()["revision"]
    with pytest.raises(ValidationError):
        service.add_task({"id": "research", "task": "Dup", "role": "Researcher"}, "llm_a", "Implementer", revision)
    with pytest.raises(ConflictError):
        service.add_task({"id": "followup", "task": "X", "role": "Implementer"}, "llm_a", "Implementer", revision + 1)


def test_add_task_rejects_unknown_or_cyclic_dependencies(service):
    revision = service.read_plan()["revision"]
    with pytest.raises(ValidationError):
        service.add_task(
            {"id": "followup", "task": "X", "role": "Implementer"},
            "llm_a",
            "Implementer",
            revision,
            task_dependencies=["does_not_exist"],
        )
    with pytest.raises(ValidationError):
        service.add_task(
            {"id": "self_cycle", "task": "Depends on itself", "role": "Implementer"},
            "llm_a",
            "Implementer",
            revision,
            task_dependencies=["self_cycle"],
        )


def test_claim_task_records_work_scope_on_the_started_event(service):
    revision = service.read_plan()["revision"]
    service.claim_task(
        "research", "llm_a", "Researcher", revision,
        work_scope=["src/blackboard/service.py", "tests/test_service.py", "  ", ""],
    )
    events = service.read_events(event_type="task_started", task_id="research")
    assert events[0]["content"] == "- src/blackboard/service.py\n- tests/test_service.py\n"


def test_claim_task_without_work_scope_leaves_event_content_empty(service):
    revision = service.read_plan()["revision"]
    service.claim_task("research", "llm_a", "Researcher", revision)
    events = service.read_events(event_type="task_started", task_id="research")
    assert events[0]["content"] == ""


def test_edit_task_updates_only_a_pending_task_with_audited_cas(service):
    revision = service.read_plan()["revision"]
    edited = service.edit_task(
        "implement",
        "llm_a",
        "Implementer",
        revision,
        task="Implement the approved contract",
        task_role="Reviewer",
        task_dependencies=[],
    )
    assert edited["task"] == {
        "id": "implement",
        "task": "Implement the approved contract",
        "role": "Reviewer",
        "status": "pending",
        "started_by": "",
        "started": "",
        "completed_by": "",
        "completed": "",
    }
    plan = service.read_plan()
    assert plan["dependencies"]["implement"] == []
    assert [event["frontmatter"]["event_type"] for event in service.read_events(task_id="implement")] == [
        "task_updated"
    ]
    with pytest.raises(ConflictError):
        service.edit_task("implement", "llm_a", "Implementer", revision, task="Stale")

    claim = service.claim_task("implement", "reviewer_1", "Reviewer", plan["revision"])
    with pytest.raises(InvalidTransitionError, match="only pending"):
        service.edit_task("implement", "llm_a", "Implementer", claim["plan_revision"], task="Too late")


def test_cancel_task_is_logical_delete_and_protects_active_dependents(service):
    with pytest.raises(InvalidTransitionError, match="non-terminal dependents"):
        service.cancel_task("research", "llm_a", "Researcher", service.read_plan()["revision"])

    service.set_task_priority(
        "final", "P1", "facilitator_1", "Facilitator", "Prepare the final review.", service.read_plan()["revision"]
    )
    cancelled = service.cancel_task(
        "final", "facilitator_1", "Facilitator", service.read_plan()["revision"], reason="No final review is needed."
    )
    assert cancelled["task"]["status"] == "cancelled"
    assert "final" not in service.read_plan()["priorities"]
    events = service.read_events(task_id="final")
    assert [event["frontmatter"]["event_type"] for event in events] == ["task_cancelled", "task_prioritized"]
    assert events[0]["content"] == "reason: No final review is needed.\n"
    with pytest.raises(InvalidTransitionError, match="only pending or in_progress"):
        service.cancel_task("final", "reviewer_1", "Reviewer", cancelled["plan_revision"])


def test_update_task_cancellation_also_protects_active_dependents(service):
    service.set_task_priority(
        "research", "P0", "facilitator_1", "Facilitator", "Priority before claim.", service.read_plan()["revision"]
    )
    claim = service.claim_task("research", "llm_a", "Researcher", service.read_plan()["revision"])
    with pytest.raises(InvalidTransitionError, match="non-terminal dependents"):
        service.update_task("research", "llm_a", "Researcher", "cancelled", claim["plan_revision"])

    standalone = service.add_task(
        {"id": "cancel_me", "task": "Cancel me", "role": "Researcher"},
        "facilitator_1",
        "Facilitator",
        service.read_plan()["revision"],
        priority="P1",
    )
    active = service.claim_task("cancel_me", "llm_a", "Researcher", standalone["plan_revision"])
    cancelled = service.update_task("cancel_me", "llm_a", "Researcher", "cancelled", active["plan_revision"])
    assert cancelled["task"]["status"] == "cancelled"
    assert "cancel_me" not in service.read_plan()["priorities"]


def test_cancel_task_requires_the_in_progress_claim_owner(service):
    service.add_task(
        {"id": "standalone", "task": "Standalone", "role": "Researcher"},
        "llm_a",
        "Researcher",
        service.read_plan()["revision"],
    )
    claim = service.claim_task("standalone", "llm_a", "Researcher", service.read_plan()["revision"])
    with pytest.raises(ClaimOwnerError):
        service.cancel_task("standalone", "llm_b", "Researcher", claim["plan_revision"])
    cancelled = service.cancel_task("standalone", "llm_a", "Researcher", claim["plan_revision"])
    assert cancelled["task"]["status"] == "cancelled"


def test_recover_task_uses_dedicated_permission_and_audited_transition(service):
    claim = service.claim_task("research", "llm_a", "Researcher", service.read_plan()["revision"])
    blocked = service.update_task("research", "llm_a", "Researcher", "blocked", claim["plan_revision"])
    with pytest.raises(PermissionDeniedError, match="recover_task"):
        service.recover_task("research", "llm_a", "Implementer", blocked["plan_revision"])
    recovered = service.recover_task("research", "reviewer_1", "Reviewer", blocked["plan_revision"])
    assert recovered["task"]["status"] == "pending"
    assert recovered["task"]["started_by"] == ""
    assert "task_recovered" in {
        event["frontmatter"]["event_type"] for event in service.read_events(task_id="research")
    }


def test_validate_plan_reports_direct_edit_diagnostics_without_mutating(service):
    healthy = service.validate_plan()
    assert healthy["valid"] is True
    assert healthy["issues"] == []
    assert healthy["task_count"] == 5

    plan_path = service.store.plan_path()
    plan_path.write_text(
        plan_path.read_text(encoding="utf-8").replace("updated:", "updated: not-a-timestamp #"),
        encoding="utf-8",
    )
    diagnostic = service.validate_plan()
    assert diagnostic["valid"] is False
    assert any("updated must be an ISO 8601 timestamp" in issue for issue in diagnostic["issues"])
    assert plan_path.read_text(encoding="utf-8").startswith("---\n")


def test_validate_plan_reports_invalid_task_table(service):
    plan_path = service.store.plan_path()
    plan_path.write_text("# missing front matter\n", encoding="utf-8")
    diagnostic = service.validate_plan()
    assert diagnostic == {
        "plan_id": "project",
        "valid": False,
        "issues": ["YAML Front Matter is required"],
    }


def test_list_memory_and_list_state_return_stable_metadata(service):
    service.write_memory("zeta", "later alphabetically", "llm_a", "Researcher", importance="low", tags=["z"])
    service.write_memory("alpha", "first alphabetically", "llm_b", "Researcher", importance="high", tags=["a"])
    service.write_state("z_state", "Later state", "llm_a", "Implementer", "in_progress", current_task="implement")
    service.write_state("a_state", "First state", "llm_b", "Implementer", "blocked")

    assert service.list_memory() == [
        {
            "id": "alpha",
            "updated": service.read_memory("alpha")["frontmatter"]["updated"],
            "importance": "high",
            "tags": ["a"],
            "source": "llm_b",
        },
        {
            "id": "zeta",
            "updated": service.read_memory("zeta")["frontmatter"]["updated"],
            "importance": "low",
            "tags": ["z"],
            "source": "llm_a",
        },
    ]
    states = service.list_state()
    assert [item["id"] for item in states] == ["a_state", "z_state"]
    assert states[0]["status"] == "blocked"
    assert states[1]["current_task"] == "implement"


def test_events_record_actor_role_for_direct_and_plan_outbox_events(service):
    direct = service.emit_event("note", "llm_a", "An observation", role="Researcher")
    assert direct["frontmatter"]["source"] == "llm_a"
    assert direct["frontmatter"]["role"] == "Researcher"

    claim = service.claim_task("research", "llm_a", "Researcher", service.read_plan()["revision"])
    event = service.read_events(task_id="research")[0]
    assert event["id"] == "task_research_task_started_r2"
    assert event["frontmatter"]["source"] == "llm_a"
    assert event["frontmatter"]["role"] == "Researcher"
    assert claim["event_delivery"] == "delivered"


def test_facilitator_sets_pending_task_priority_with_cas_and_reason(service):
    revision = service.read_plan()["revision"]

    result = service.set_task_priority(
        "research",
        "P0",
        "facilitator_1",
        "Facilitator",
        "Investigate the blocking architecture decision first.",
        revision,
    )

    assert result["task"]["priority"] == "P0"
    plan = service.read_plan()
    assert plan["priorities"] == {"research": "P0"}
    assert next(task for task in plan["tasks"] if task["id"] == "research")["priority"] == "P0"
    event = service.read_events(event_type="task_prioritized", task_id="research")[0]
    assert event["frontmatter"]["role"] == "Facilitator"
    assert event["content"] == (
        "priority_before: P2\npriority_after: P0\nreason: Investigate the blocking architecture decision first.\n"
    )

    with pytest.raises(ConflictError):
        service.set_task_priority("research", "P1", "facilitator_1", "Facilitator", "Stale update.", revision)
    with pytest.raises(PermissionDeniedError, match="set_task_priority"):
        service.set_task_priority("implement", "P1", "llm_a", "Implementer", "Not a Facilitator.", plan["revision"])
    with pytest.raises(ValidationError, match="P0, P1, P2, or P3"):
        service.set_task_priority("implement", "urgent", "facilitator_1", "Facilitator", "Invalid priority.", plan["revision"])


def test_priority_rejects_non_pending_task_and_invalid_plan_metadata(service):
    claim = service.claim_task("research", "llm_a", "Researcher", service.read_plan()["revision"])
    with pytest.raises(InvalidTransitionError, match="only pending"):
        service.set_task_priority(
            "research", "P0", "facilitator_1", "Facilitator", "Do not touch active work.", claim["plan_revision"]
        )

    plan_path = service.store.plan_path()
    plan_path.write_text(
        plan_path.read_text(encoding="utf-8").replace("priorities: {}", "priorities:\n  missing_task: P0"),
        encoding="utf-8",
    )
    diagnostic = service.validate_plan()
    assert diagnostic["valid"] is False
    assert diagnostic["issues"] == ["priority task 'missing_task' is not a task"]


def test_state_does_not_implicitly_change_plan_task_executability(service):
    service.write_state(
        "project_state",
        "A facilitator is waiting for a decision.",
        "facilitator_1",
        "Implementer",
        "blocked",
        current_task="research",
    )
    executable = service.read_plan("Researcher")["executable_tasks"]
    assert [task["id"] for task in executable] == ["research"]
    claim = service.claim_task("research", "llm_a", "Researcher", service.read_plan()["revision"])
    assert claim["task"]["status"] == "in_progress"
