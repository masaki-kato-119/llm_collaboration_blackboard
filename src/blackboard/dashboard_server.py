"""Local HTTP entry point for the Facilitator Dashboard read API."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from .dashboard import DashboardReadModel, load_dashboard_config
from .dashboard_ui import invalid_plan_page, memory_page, plan_page, project_page, workspace_page
from .errors import BlackboardError, ConflictError, InvalidTransitionError, NotFoundError, PermissionDeniedError


class CreateTaskRequest(BaseModel):
    id: str
    task: str = Field(min_length=1)
    task_role: str = Field(min_length=1)
    dependencies: list[str] = Field(default_factory=list)
    priority: str = "P2"
    actor_id: str
    reason: str = Field(min_length=1)
    expected_revision: int


class EditTaskRequest(BaseModel):
    task: str | None = Field(default=None, min_length=1)
    task_role: str | None = Field(default=None, min_length=1)
    dependencies: list[str] | None = None
    priority: str | None = None
    actor_id: str
    reason: str = Field(min_length=1)
    expected_revision: int


class TaskActionRequest(BaseModel):
    actor_id: str
    reason: str = Field(min_length=1)
    expected_revision: int


class PriorityRequest(TaskActionRequest):
    priority: str


def create_app(config_path: str | Path) -> FastAPI:
    """Build the local dashboard application for one explicit configuration."""
    read_model = DashboardReadModel(load_dashboard_config(config_path))
    app = FastAPI(title="LLM Collaboration Blackboard Facilitator Dashboard", version="0.1.0")

    @app.get("/", response_class=HTMLResponse)
    def workspace() -> str:
        return workspace_page(read_model.list_projects())

    @app.get("/projects/{project_id}", response_class=HTMLResponse)
    def project_page_route(project_id: str) -> str:
        return project_page(_read_or_http_error(read_model.project_detail, project_id), read_model.config.refresh_seconds)

    @app.get("/projects/{project_id}/plans/{plan_id}", response_class=HTMLResponse)
    def plan_page_route(project_id: str, plan_id: str) -> str:
        try:
            detail = _read_or_http_error(read_model.plan_detail, project_id, plan_id)
        except HTTPException as exc:
            if exc.status_code != 422:
                raise
            return invalid_plan_page(project_id, plan_id, exc.detail, read_model.config.refresh_seconds)
        return plan_page(detail, read_model.config.refresh_seconds)

    @app.get("/projects/{project_id}/memory/{document_id}", response_class=HTMLResponse)
    def memory_page_route(project_id: str, document_id: str) -> str:
        detail = _read_or_http_error(read_model.memory_detail, project_id, document_id)
        return memory_page(detail, read_model.config.refresh_seconds)

    @app.get("/api/projects")
    def list_projects() -> dict[str, Any]:
        return read_model.list_projects()

    @app.get("/api/projects/{project_id}")
    def project_detail(project_id: str) -> dict[str, Any]:
        return _read_or_http_error(read_model.project_detail, project_id)

    @app.get("/api/projects/{project_id}/plans/{plan_id}")
    def plan_detail(project_id: str, plan_id: str) -> dict[str, Any]:
        return _read_or_http_error(read_model.plan_detail, project_id, plan_id)

    @app.post("/api/projects/{project_id}/plans/{plan_id}/tasks")
    def add_task(project_id: str, plan_id: str, request: CreateTaskRequest) -> dict[str, Any]:
        service = _editable_service(read_model, project_id, plan_id)
        return _mutation_or_http_error(
            service.add_task,
            {"id": request.id, "task": request.task, "role": request.task_role},
            request.actor_id,
            "Facilitator",
            request.expected_revision,
            plan_id,
            request.dependencies,
            request.reason,
            request.priority,
        )

    @app.patch("/api/projects/{project_id}/plans/{plan_id}/tasks/{task_id}")
    def edit_task(project_id: str, plan_id: str, task_id: str, request: EditTaskRequest) -> dict[str, Any]:
        service = _editable_service(read_model, project_id, plan_id)
        _require_pending_task(service, task_id, plan_id)
        return _mutation_or_http_error(
            service.edit_task,
            task_id,
            request.actor_id,
            "Facilitator",
            request.expected_revision,
            request.task,
            request.task_role,
            request.dependencies,
            plan_id,
            request.reason,
            request.priority,
        )

    @app.post("/api/projects/{project_id}/plans/{plan_id}/tasks/{task_id}/cancel")
    def cancel_task(project_id: str, plan_id: str, task_id: str, request: TaskActionRequest) -> dict[str, Any]:
        service = _editable_service(read_model, project_id, plan_id)
        _require_pending_task(service, task_id, plan_id)
        return _mutation_or_http_error(
            service.cancel_task,
            task_id,
            request.actor_id,
            "Facilitator",
            request.expected_revision,
            plan_id,
            request.reason,
        )

    @app.post("/api/projects/{project_id}/plans/{plan_id}/tasks/{task_id}/priority")
    def set_task_priority(project_id: str, plan_id: str, task_id: str, request: PriorityRequest) -> dict[str, Any]:
        service = _editable_service(read_model, project_id, plan_id)
        _require_pending_task(service, task_id, plan_id)
        return _mutation_or_http_error(
            service.set_task_priority,
            task_id,
            request.priority,
            request.actor_id,
            "Facilitator",
            request.reason,
            request.expected_revision,
            plan_id,
        )

    @app.post("/api/projects/{project_id}/plans/{plan_id}/flush-outbox")
    def flush_outbox(project_id: str, plan_id: str) -> dict[str, Any]:
        service = _editable_service(read_model, project_id, plan_id)
        return _mutation_or_http_error(service.flush_event_outbox, plan_id)

    return app


def _read_or_http_error(operation: Any, *args: str) -> dict[str, Any]:
    try:
        return operation(*args)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except BlackboardError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


def _editable_service(read_model: DashboardReadModel, project_id: str, plan_id: str) -> Any:
    """Resolve a healthy Plan before a browser mutation is allowed."""
    service = _read_or_http_error(read_model.service_for, project_id)
    validation = _read_or_http_error(service.validate_plan, plan_id)
    if not validation["valid"]:
        raise HTTPException(status_code=422, detail={"message": "Plan is invalid; edits are disabled.", "issues": validation["issues"]})
    return service


def _require_pending_task(service: Any, task_id: str, plan_id: str) -> None:
    plan = _read_or_http_error(service.read_plan, None, plan_id)
    task = next((item for item in plan["tasks"] if item["id"] == task_id), None)
    if task is None:
        raise HTTPException(status_code=404, detail=f"task '{task_id}' does not exist")
    if task["status"] != "pending":
        raise HTTPException(status_code=409, detail="Facilitator interventions are allowed only for pending tasks")


def _mutation_or_http_error(operation: Any, *args: Any) -> dict[str, Any]:
    try:
        return operation(*args)
    except ConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (InvalidTransitionError, PermissionDeniedError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (BlackboardError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


def main() -> None:
    """Run the dashboard on loopback only by default."""
    parser = argparse.ArgumentParser(description="Run the local Facilitator Dashboard")
    parser.add_argument("--config", required=True, help="Path to the dashboard YAML configuration")
    parser.add_argument("--host", default="127.0.0.1", help="Bind host (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8765, help="Bind port (default: 8765)")
    args = parser.parse_args()
    uvicorn.run(create_app(args.config), host=args.host, port=args.port)
