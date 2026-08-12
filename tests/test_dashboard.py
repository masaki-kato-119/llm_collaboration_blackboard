from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from blackboard.dashboard import DashboardReadModel, load_dashboard_config
from blackboard.dashboard_server import create_app
from blackboard.errors import ValidationError
from blackboard.service import BlackboardService


def _project(root, project_id: str, task_id: str) -> BlackboardService:
    service = BlackboardService(root)
    service.initialize_project(project_id, project_id.title(), [{"id": task_id, "task": task_id.title(), "role": "Implementer"}])
    return service


def _config(tmp_path, roots: dict[str, object]):
    entries = "\n".join(
        f"  - id: {project_id}\n    name: {project_id.title()}\n    root: {root}" for project_id, root in roots.items()
    )
    path = tmp_path / "dashboard.yaml"
    path.write_text(f"projects:\n{entries}\nrefresh_seconds: 5\n", encoding="utf-8")
    return path


def test_dashboard_read_model_aggregates_multiple_projects(tmp_path):
    alpha = _project(tmp_path / "alpha", "alpha", "implement")
    _project(tmp_path / "beta", "beta", "review")
    claim = alpha.claim_task("implement", "llm_a", "Implementer", alpha.read_plan()["revision"])
    assert claim["event_delivery"] == "delivered"
    config = load_dashboard_config(_config(tmp_path, {"alpha": tmp_path / "alpha", "beta": tmp_path / "beta"}))

    overview = DashboardReadModel(config).list_projects()

    assert overview["refresh_seconds"] == 5
    summaries = {project["id"]: project for project in overview["projects"]}
    assert summaries["alpha"]["status_counts"] == {"in_progress": 1}
    assert summaries["alpha"]["in_progress_tasks"][0]["started_by"] == "llm_a"
    assert summaries["beta"]["status_counts"] == {"pending": 1}


def test_plan_detail_splits_dependency_waiting_tasks(tmp_path):
    root = tmp_path / "alpha"
    service = BlackboardService(root)
    service.initialize_project(
        "alpha",
        "Alpha",
        [
            {"id": "first", "task": "First", "role": "Implementer"},
            {"id": "second", "task": "Second", "role": "Implementer"},
        ],
        {"second": ["first"]},
    )
    model = DashboardReadModel(load_dashboard_config(_config(tmp_path, {"alpha": root})))

    detail = model.plan_detail("alpha", "project")

    assert [task["id"] for task in detail["board"]["pending"]] == ["first"]
    assert [task["id"] for task in detail["board"]["dependency_waiting"]] == ["second"]
    assert detail["validation"]["valid"] is True


def test_dashboard_http_read_routes_and_missing_project(tmp_path):
    root = tmp_path / "alpha"
    service = _project(root, "alpha", "implement")
    finished = service.initialize_project(
        "alpha", "Finished", [{"id": "archive", "task": "Archive", "role": "Implementer"}], plan_id="finished"
    )
    claimed = service.claim_task("archive", "llm_a", "Implementer", finished["revision"], plan_id="finished")
    service.update_task("archive", "llm_a", "Implementer", "done", claimed["plan_revision"], plan_id="finished")
    cancelled = service.initialize_project(
        "alpha", "Cancelled", [{"id": "obsolete", "task": "Obsolete", "role": "Implementer"}], plan_id="cancelled"
    )
    claimed = service.claim_task("obsolete", "llm_a", "Implementer", cancelled["revision"], plan_id="cancelled")
    service.update_task("obsolete", "llm_a", "Implementer", "cancelled", claimed["plan_revision"], plan_id="cancelled")
    service.write_memory("decision", "# Decision\n\nKeep the Markdown source visible.", "llm_a", "Implementer")
    client = TestClient(create_app(_config(tmp_path, {"alpha": root})))

    assert client.get("/api/projects").json()["projects"][0]["id"] == "alpha"
    assert client.get("/api/projects/alpha").status_code == 200
    plan = client.get("/api/projects/alpha/plans/project")
    assert plan.status_code == 200
    assert plan.json()["plan"]["plan_id"] == "project"
    assert client.get("/api/projects/missing").status_code == 404
    workspace = client.get("/")
    assert workspace.status_code == 200
    assert "Facilitator Dashboard" in workspace.text
    assert "/projects/alpha" in workspace.text
    assert "console-shell" in workspace.text
    assert "status-chip" in workspace.text
    project_page = client.get("/projects/alpha")
    assert project_page.status_code == 200
    plan_page = client.get("/projects/alpha/plans/project")
    assert plan_page.status_code == 200
    assert "Add pending task" in plan_page.text
    assert "Facilitator intervention" in plan_page.text
    assert "board-column" in plan_page.text
    assert "board--scrollable" in plan_page.text
    assert "task-card" in plan_page.text
    assert "task-details" in plan_page.text
    assert "View details" in plan_page.text
    assert "Dependencies" in plan_page.text
    assert "event-list--scrollable" in plan_page.text
    assert "compact-list--scrollable" in project_page.text
    assert "completed-plan-toggle" in project_page.text
    assert "completed-plan-toggle' type='checkbox' checked" not in project_page.text
    assert project_page.text.count("data-plan-completed='true'") == 2
    assert ".plan-row[hidden] { display: none; }" in project_page.text
    assert "/projects/alpha/memory/decision" in project_page.text
    memory_page = client.get("/projects/alpha/memory/decision")
    assert memory_page.status_code == 200
    assert "Memory: decision" in memory_page.text
    assert "Keep the Markdown source visible." in memory_page.text
    assert "markdown-source" in memory_page.text
    assert client.get("/projects/alpha/memory/missing").status_code == 404


def test_dashboard_pending_backlog_interventions_are_audited_and_guarded(tmp_path):
    root = tmp_path / "alpha"
    service = _project(root, "alpha", "implement")
    client = TestClient(create_app(_config(tmp_path, {"alpha": root})))
    revision = service.read_plan()["revision"]

    created = client.post(
        "/api/projects/alpha/plans/project/tasks",
        json={
            "id": "followup",
            "task": "Facilitate follow-up",
            "task_role": "Researcher",
            "dependencies": [],
            "priority": "P0",
            "actor_id": "human_1",
            "reason": "Address the most urgent stakeholder question.",
            "expected_revision": revision,
        },
    )
    assert created.status_code == 200
    created_revision = created.json()["plan_revision"]
    priority = client.post(
        "/api/projects/alpha/plans/project/tasks/followup/priority",
        json={
            "priority": "P1",
            "actor_id": "human_1",
            "reason": "The initial inquiry was resolved.",
            "expected_revision": created_revision,
        },
    )
    assert priority.status_code == 200
    plan = service.read_plan()
    assert plan["priorities"]["followup"] == "P1"
    assert service.read_events(task_id="followup")[0]["frontmatter"]["role"] == "Facilitator"
    stale = client.post(
        "/api/projects/alpha/plans/project/tasks/followup/priority",
        json={
            "priority": "P2",
            "actor_id": "human_1",
            "reason": "This stale update must not be applied.",
            "expected_revision": created_revision,
        },
    )
    assert stale.status_code == 409
    assert client.post("/api/projects/alpha/plans/project/flush-outbox").json()["status"] == "delivered"

    active = service.claim_task("implement", "llm_a", "Implementer", plan["revision"])
    rejected = client.patch(
        "/api/projects/alpha/plans/project/tasks/implement",
        json={
            "task": "Do not edit active work",
            "actor_id": "human_1",
            "reason": "This should be rejected.",
            "expected_revision": active["plan_revision"],
        },
    )
    assert rejected.status_code == 409


def test_dashboard_disables_mutations_for_an_invalid_plan(tmp_path):
    root = tmp_path / "alpha"
    service = _project(root, "alpha", "implement")
    plan_path = service.store.plan_path()
    plan_path.write_text(
        plan_path.read_text(encoding="utf-8").replace("priorities: {}", "priorities: invalid"), encoding="utf-8"
    )
    client = TestClient(create_app(_config(tmp_path, {"alpha": root})))

    response = client.post(
        "/api/projects/alpha/plans/project/tasks",
        json={
            "id": "followup",
            "task": "Will not be added",
            "task_role": "Implementer",
            "actor_id": "human_1",
            "reason": "Must be rejected for invalid Plan.",
            "expected_revision": 1,
        },
    )

    assert response.status_code == 422
    assert service.validate_plan()["valid"] is False
    page = client.get("/projects/alpha/plans/project")
    assert page.status_code == 200
    assert "Facilitator edits are disabled" in page.text


def test_dashboard_config_rejects_duplicate_ids_and_invalid_refresh(tmp_path):
    path = tmp_path / "invalid.yaml"
    path.write_text(
        "projects:\n  - id: same\n    name: One\n    root: first\n  - id: same\n    name: Two\n    root: second\nrefresh_seconds: 0\n",
        encoding="utf-8",
    )

    with pytest.raises(ValidationError, match="IDs must be unique"):
        load_dashboard_config(path)
