"""Read models and configuration for the local Facilitator Dashboard.

The dashboard deliberately reads and changes Blackboard data only through
``BlackboardService``.  It never parses or writes Plan Markdown itself, so the
same revision-CAS, locking, and Outbox rules apply to browser-driven work.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .errors import NotFoundError, ValidationError
from .models import require_id
from .service import BlackboardService

TASK_COLUMNS = ("pending", "dependency_waiting", "in_progress", "blocked", "done", "cancelled")


@dataclass(frozen=True)
class DashboardProject:
    """One locally configured Blackboard root."""

    id: str
    name: str
    root: Path


@dataclass(frozen=True)
class DashboardConfig:
    """Dashboard configuration loaded from a local YAML file."""

    projects: tuple[DashboardProject, ...]
    refresh_seconds: int = 5


def load_dashboard_config(path: str | Path) -> DashboardConfig:
    """Load and validate a dashboard YAML configuration.

    Relative project roots are resolved from the directory containing the
    configuration, rather than from the process working directory.
    """
    config_path = Path(path).resolve()
    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValidationError(f"cannot read dashboard configuration: {exc}") from exc
    except yaml.YAMLError as exc:
        raise ValidationError(f"dashboard configuration is not valid YAML: {exc}") from exc
    if not isinstance(raw, dict):
        raise ValidationError("dashboard configuration must be a mapping")

    raw_projects = raw.get("projects")
    if not isinstance(raw_projects, list) or not raw_projects:
        raise ValidationError("dashboard configuration requires a non-empty projects list")

    projects: list[DashboardProject] = []
    for entry in raw_projects:
        if not isinstance(entry, dict):
            raise ValidationError("each dashboard project must be a mapping")
        project_id = require_id(str(entry.get("id", "")), "dashboard project id")
        name = entry.get("name")
        root = entry.get("root")
        if not isinstance(name, str) or not name.strip():
            raise ValidationError(f"dashboard project '{project_id}' requires a non-empty name")
        if not isinstance(root, str) or not root.strip():
            raise ValidationError(f"dashboard project '{project_id}' requires a root path")
        root_path = Path(root)
        if not root_path.is_absolute():
            root_path = config_path.parent / root_path
        projects.append(DashboardProject(project_id, name.strip(), root_path.resolve()))

    project_ids = [project.id for project in projects]
    if len(set(project_ids)) != len(project_ids):
        raise ValidationError("dashboard project IDs must be unique")
    refresh_seconds = raw.get("refresh_seconds", 5)
    if not isinstance(refresh_seconds, int) or isinstance(refresh_seconds, bool) or refresh_seconds < 1:
        raise ValidationError("refresh_seconds must be a positive integer")
    return DashboardConfig(tuple(projects), refresh_seconds)


class DashboardReadModel:
    """Aggregate view of configured Blackboard roots for a Facilitator."""

    def __init__(self, config: DashboardConfig) -> None:
        self.config = config
        self._projects = {project.id: project for project in config.projects}

    def list_projects(self) -> dict[str, Any]:
        """Return workspace overview data, retaining unhealthy roots as cards."""
        return {
            "refresh_seconds": self.config.refresh_seconds,
            "projects": [self._project_summary(project) for project in self.config.projects],
        }

    def project_detail(self, project_id: str) -> dict[str, Any]:
        """Return Plans, shared indices, and activity for one project root."""
        project = self._project(project_id)
        service = BlackboardService(project.root)
        plans = [self._plan_summary_or_error(service, item) for item in service.list_plans()]
        return {
            "project": self._project_identity(project),
            "health": "ok" if all(plan["validation"]["valid"] for plan in plans) else "invalid_plan",
            "plans": plans,
            "actor_roles": service.list_actor_roles(),
            "states": service.list_state(),
            "memory": service.list_memory(),
            "events": service.read_events(limit=100),
        }

    def plan_detail(self, project_id: str, plan_id: str) -> dict[str, Any]:
        """Return the board-ready detail for one named Plan."""
        project = self._project(project_id)
        service = BlackboardService(project.root)
        plan = service.read_plan(plan_id=plan_id)
        validation = service.validate_plan(plan_id)
        tasks = plan["tasks"]
        statuses = Counter(task["status"] for task in tasks)
        blocked_by_dependency = {
            task["id"]
            for task in tasks
            if task["status"] == "pending" and task["id"] not in {item["id"] for item in plan["executable_tasks"]}
        }
        board = {column: [] for column in TASK_COLUMNS}
        for task in tasks:
            column = "dependency_waiting" if task["id"] in blocked_by_dependency else task["status"]
            board[column].append(task)
        return {
            "project": self._project_identity(project),
            "plan": plan,
            "validation": validation,
            "status_counts": dict(sorted(statuses.items())),
            "board": board,
            "events": service.read_events(limit=100),
        }

    def memory_detail(self, project_id: str, document_id: str) -> dict[str, Any]:
        """Return one shared Memory document for its read-only dashboard page."""
        project = self._project(project_id)
        return {
            "project": self._project_identity(project),
            "memory": BlackboardService(project.root).read_memory(document_id),
        }

    def service_for(self, project_id: str) -> BlackboardService:
        """Return the service for a configured root, without exposing its path lookup."""
        return BlackboardService(self._project(project_id).root)

    def _project_summary(self, project: DashboardProject) -> dict[str, Any]:
        try:
            service = BlackboardService(project.root)
            plans = [self._plan_summary_or_error(service, item) for item in service.list_plans()]
            status_counts: Counter[str] = Counter()
            active_work: list[dict[str, Any]] = []
            pending_events = 0
            for plan in plans:
                status_counts.update(plan["status_counts"])
                active_work.extend(plan["in_progress_tasks"])
                pending_events += plan["pending_events"]
            states = service.list_state()
            return {
                **self._project_identity(project),
                "health": "ok" if all(plan["validation"]["valid"] for plan in plans) else "invalid_plan",
                "plan_count": len(plans),
                "status_counts": dict(sorted(status_counts.items())),
                "in_progress_tasks": active_work,
                "pending_events": pending_events,
                "stale_state_count": sum("stale_warning" in state for state in states),
                "validation_error_count": sum(len(plan["validation"]["issues"]) for plan in plans),
            }
        except (OSError, ValidationError) as exc:
            return {**self._project_identity(project), "health": "error", "error": str(exc)}

    def _plan_summary(self, service: BlackboardService, plan_id: str) -> dict[str, Any]:
        plan = service.read_plan(plan_id=plan_id)
        status_counts = dict(sorted(Counter(task["status"] for task in plan["tasks"]).items()))
        return {
            "plan_id": plan_id,
            "id": plan["id"],
            "title": plan["title"],
            "revision": plan["revision"],
            "status_counts": status_counts,
            "all_tasks_complete": bool(status_counts) and set(status_counts) <= {"done", "cancelled"},
            "in_progress_tasks": [task for task in plan["tasks"] if task["status"] == "in_progress"],
            "pending_events": plan["pending_events"],
            "validation": service.validate_plan(plan_id),
        }

    def _plan_summary_or_error(self, service: BlackboardService, listed_plan: dict[str, Any]) -> dict[str, Any]:
        """Keep a directly edited invalid Plan visible in project-level views."""
        try:
            return self._plan_summary(service, listed_plan["plan_id"])
        except ValidationError as exc:
            return {
                "plan_id": listed_plan["plan_id"],
                "id": listed_plan["id"],
                "title": listed_plan["title"],
                "revision": listed_plan["revision"],
                "status_counts": {},
                "all_tasks_complete": False,
                "in_progress_tasks": listed_plan["in_progress_tasks"],
                "pending_events": 0,
                "validation": {"valid": False, "issues": [str(exc)]},
            }

    def _project(self, project_id: str) -> DashboardProject:
        project_id = require_id(project_id, "dashboard project id")
        try:
            return self._projects[project_id]
        except KeyError as exc:
            raise NotFoundError(f"dashboard project '{project_id}' is not configured") from exc

    @staticmethod
    def _project_identity(project: DashboardProject) -> dict[str, str]:
        return {"id": project.id, "name": project.name, "root": str(project.root)}
