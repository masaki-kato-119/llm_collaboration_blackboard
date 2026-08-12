"""Server-rendered visual views for the local Facilitator Dashboard."""
# ruff: noqa: E501

from __future__ import annotations

from collections import Counter
from html import escape
from typing import Any


def workspace_page(workspace: dict[str, Any]) -> str:
    """Render a visual workspace overview, retaining unhealthy roots as cards."""
    projects = workspace["projects"]
    total_active = sum(len(project.get("in_progress_tasks", [])) for project in projects)
    total_blocked = sum(project.get("status_counts", {}).get("blocked", 0) for project in projects)
    total_events = sum(project.get("pending_events", 0) for project in projects)
    cards = "".join(_project_card(project) for project in projects)
    body = (
        _hero(
            "Facilitator Dashboard",
            "複数の Blackboard project を横断して、進行状況と介入ポイントを把握します。",
            _metric_strip((("Projects", len(projects)), ("Active", total_active), ("Blocked", total_blocked), ("Pending events", total_events))),
        )
        + f"<section class='section-heading'><div><p class='eyebrow'>Workspace overview</p><h2>Project pulse</h2></div>"
        f"<span class='refresh-note'>Auto refresh: {workspace['refresh_seconds']}s</span></section>"
        + f"<div class='project-grid'>{cards}</div>"
    )
    return _layout("Facilitator Dashboard", body, workspace["refresh_seconds"])


def project_page(detail: dict[str, Any], refresh_seconds: int) -> str:
    """Render one project's Plans, shared State, Memory, and recent Event index."""
    project = detail["project"]
    plans = "".join(_plan_row(project["id"], plan) for plan in detail["plans"])
    states = "".join(_state_row(state) for state in detail["states"]) or _empty_state("No State documents")
    memory = "".join(_memory_row(project["id"], item) for item in detail["memory"]) or _empty_state("No Memory documents")
    body = (
        _breadcrumb(("Workspace", "/"), (project["name"], None))
        + _hero(
            project["name"],
            project["root"],
            _metric_strip(
                (
                    ("Plans", len(detail["plans"])),
                    ("Active", sum(len(plan["in_progress_tasks"]) for plan in detail["plans"])),
                    ("Events", sum(plan["pending_events"] for plan in detail["plans"])),
                )
            ),
            health=detail["health"],
        )
        + "<div class='dashboard-grid dashboard-grid--wide'>"
        + f"<section class='panel panel--wide'><div class='panel-title'><div><p class='eyebrow'>Execution</p><h2>Plans</h2></div>"
        "<label class='plan-filter'><input id='completed-plan-toggle' type='checkbox'><span>Show completed Plans</span></label></div>"
        f"<div class='plan-list'>{plans}</div></section>"
        + f"<section class='panel'><div class='panel-title'><div><p class='eyebrow'>Signal</p><h2>State</h2></div></div><div class='compact-list compact-list--scrollable'>{states}</div></section>"
        + f"<section class='panel'><div class='panel-title'><div><p class='eyebrow'>Context</p><h2>Memory index</h2></div></div><div class='compact-list compact-list--scrollable'>{memory}</div></section>"
        + f"<section class='panel panel--wide'><div class='panel-title'><div><p class='eyebrow'>Audit</p><h2>Recent events</h2></div></div>{_event_list(detail['events'], scrollable=True)}</section>"
        + "</div>"
    )
    return _layout(f"Project: {project['name']}", body, refresh_seconds, include_plan_filter_script=True)


def memory_page(detail: dict[str, Any], refresh_seconds: int) -> str:
    """Render the raw Markdown of one Memory document in its own page."""
    project, memory = detail["project"], detail["memory"]
    frontmatter = memory["frontmatter"]
    document_id = str(frontmatter["id"])
    tags = ", ".join(str(tag) for tag in frontmatter.get("tags", [])) or "No tags"
    source = str(frontmatter.get("source", "unknown"))
    body = (
        _breadcrumb((project["name"], f"/projects/{project['id']}"), ("Memory index", f"/projects/{project['id']}"), (document_id, None))
        + _hero(
            f"Memory: {document_id}",
            f"Source: {source}",
            _metric_strip((("Revision", int(frontmatter["revision"])), ("Tags", len(frontmatter.get("tags", [])))),),
        )
        + "<section class='panel markdown-panel'><div class='panel-title'><div><p class='eyebrow'>Memory source</p><h2>Markdown</h2></div>"
        f"<span class='panel-hint'>{escape(tags)}</span></div><pre class='markdown-source'>{escape(memory['content'])}</pre></section>"
    )
    return _layout(f"Memory: {document_id}", body, refresh_seconds)


def plan_page(detail: dict[str, Any], refresh_seconds: int) -> str:
    """Render a Plan board and pending-only Facilitator intervention forms."""
    project, plan = detail["project"], detail["plan"]
    plan_id, project_id, revision = plan["plan_id"], project["id"], plan["revision"]
    counts = Counter(task["status"] for task in plan["tasks"])
    board = "".join(
        _board_column(column, tasks, project_id, plan_id, revision, plan["dependencies"]) for column, tasks in detail["board"].items()
    )
    validation = "Healthy Plan" if detail["validation"]["valid"] else "Plan validation requires attention"
    body = (
        _breadcrumb((project["name"], f"/projects/{project_id}"), (plan["title"], None))
        + _hero(
            plan["title"],
            f"Plan revision {revision} - {validation}",
            _metric_strip((("Pending", counts["pending"]), ("In progress", counts["in_progress"]), ("Blocked", counts["blocked"]), ("Done", counts["done"]))),
            health="ok" if detail["validation"]["valid"] else "invalid_plan",
        )
        + "<section class='section-heading'><div><p class='eyebrow'>Flow</p><h2>Execution board</h2></div>"
        "<span class='refresh-note'>Only pending cards expose Facilitator controls</span></section>"
        + f"<div class='board board--scrollable'>{board}</div>"
        + f"<section class='panel add-task-panel'><div class='panel-title'><div><p class='eyebrow'>Backlog</p><h2>Add pending task</h2></div>"
        "<span class='panel-hint'>The reason is recorded in the audit Event</span></div>"
        f"{_add_form(project_id, plan_id, revision)}</section>"
        + f"<section class='panel'><div class='panel-title'><div><p class='eyebrow'>Audit</p><h2>Recent events</h2></div></div>{_event_list(detail['events'], scrollable=True)}</section>"
    )
    return _layout(f"Plan: {plan['title']}", body, refresh_seconds, include_intervention_script=True)


def invalid_plan_page(project_id: str, plan_id: str, detail: Any, refresh_seconds: int) -> str:
    """Render a non-editable page when Plan validation prevents safe loading."""
    body = (
        _breadcrumb(("Project", f"/projects/{project_id}"), ("Plan unavailable", None))
        + "<section class='empty-hero error-hero'><p class='eyebrow'>Plan health</p><h1>Plan unavailable: "
        + escape(plan_id)
        + "</h1><p>Plan validation failed. Facilitator edits are disabled until the Plan is repaired.</p>"
        + f"<pre>{escape(str(detail))}</pre></section>"
    )
    return _layout(f"Invalid Plan: {plan_id}", body, refresh_seconds)


def _project_card(project: dict[str, Any]) -> str:
    project_id = escape(project["id"])
    counts = project.get("status_counts", {})
    error = f"<p class='inline-error'>{escape(project['error'])}</p>" if project.get("error") else ""
    return (
        "<article class='project-card'>"
        f"<div class='card-topline'><span class='status-chip status-chip--{escape(project['health'])}'>{escape(project['health'])}</span>"
        f"<span class='card-id'>{project_id}</span></div>"
        f"<h3><a href='/projects/{project_id}'>{escape(project['name'])}</a></h3>"
        f"<p class='path'>{escape(project['root'])}</p>"
        f"<div class='card-metrics'>{_mini_metric('Plans', project.get('plan_count', 0))}"
        f"{_mini_metric('Active', len(project.get('in_progress_tasks', [])))}{_mini_metric('Blocked', counts.get('blocked', 0))}</div>"
        f"<div class='card-footer'><span>{project.get('pending_events', 0)} pending events</span>"
        f"<span>{project.get('stale_state_count', 0)} stale State</span></div>{error}</article>"
    )


def _plan_row(project_id: str, plan: dict[str, Any]) -> str:
    validation = plan["validation"]
    complete = "true" if plan["all_tasks_complete"] else "false"
    return (
        f"<a class='plan-row' data-plan-completed='{complete}' href='/projects/{escape(project_id)}/plans/{escape(plan['plan_id'])}'>"
        f"<div><strong>{escape(plan['title'])}</strong><span>Revision {plan['revision']}</span></div>"
        f"<div class='row-metrics'>{_mini_metric('Active', len(plan['in_progress_tasks']))}"
        f"{_mini_metric('Blocked', plan['status_counts'].get('blocked', 0))}"
        f"<span class='status-chip status-chip--{'ok' if validation['valid'] else 'invalid_plan'}'>{'healthy' if validation['valid'] else 'needs repair'}</span>"
        "</div></a>"
    )


def _state_row(state: dict[str, Any]) -> str:
    warning = "<span class='dot dot--warning'></span>" if "stale_warning" in state else "<span class='dot'></span>"
    return f"<div class='compact-row'>{warning}<strong>{escape(state['id'])}</strong><span>{escape(str(state.get('status', 'unknown')))}</span></div>"


def _memory_row(project_id: str, item: dict[str, Any]) -> str:
    document_id = escape(item["id"])
    return (
        f"<a class='compact-row memory-row' href='/projects/{escape(project_id)}/memory/{document_id}'>"
        f"<span class='dot dot--memory'></span><strong>{document_id}</strong><span>{escape(str(item['importance']))}</span></a>"
    )


def _board_column(
    column: str,
    tasks: list[dict[str, Any]],
    project_id: str,
    plan_id: str,
    revision: int,
    dependencies: dict[str, list[str]],
) -> str:
    label = column.replace("_", " ")
    cards = "".join(_task_card(task, project_id, plan_id, revision, dependencies) for task in tasks) or _empty_state("No tasks")
    return (
        f"<section class='board-column board-column--{escape(column)}'><div class='column-heading'><h3>{escape(label)}</h3>"
        f"<span>{len(tasks)}</span></div><div class='task-stack'>{cards}</div></section>"
    )


def _task_card(task: dict[str, Any], project_id: str, plan_id: str, revision: int, dependencies: dict[str, list[str]]) -> str:
    task_id = escape(task["id"])
    priority = escape(task.get("priority", "P2"))
    dependency_ids = dependencies.get(task["id"], [])
    deps = ",".join(dependency_ids)
    summary = (
        f"<summary class='task-summary'><div class='task-meta'><span>{task_id}</span>"
        f"<span class='priority priority--{priority.lower()}'>{priority}</span></div>"
        f"<h4>{escape(task['task'])}</h4><p>{escape(task['role'])}</p></summary>"
    )
    metadata = _task_metadata(task, dependency_ids)
    if task["status"] != "pending":
        return f"<details class='task-card task-card--{escape(task['status'])}'>{summary}<div class='task-details'>{metadata}</div></details>"
    endpoint = f"/api/projects/{escape(project_id)}/plans/{escape(plan_id)}/tasks/{task_id}"
    return (
        f"<details class='task-card task-card--{escape(task['status'])}'>{summary}<div class='task-details'>{metadata}"
        + "<details class='task-intervention'><summary>Facilitator intervention</summary><div class='intervention-panel'>"
        + _edit_form(endpoint, task, deps, revision)
        + _priority_form(endpoint, revision)
        + _cancel_form(endpoint, revision)
        + "</div></details></div></details>"
    )


def _task_metadata(task: dict[str, Any], dependency_ids: list[str]) -> str:
    """Render the task fields revealed when a board card is opened."""
    fields = [
        ("Status", task["status"].replace("_", " ")),
        ("Role", task["role"]),
        ("Dependencies", ", ".join(dependency_ids) or "None"),
    ]
    if task["started_by"]:
        fields.extend((("Started by", task["started_by"]), ("Started", task["started"])))
    if task["completed_by"]:
        fields.extend((("Completed by", task["completed_by"]), ("Completed", task["completed"])))
    rows = "".join(f"<div><dt>{escape(label)}</dt><dd>{escape(value)}</dd></div>" for label, value in fields)
    return f"<dl class='task-info'>{rows}</dl>"


def _edit_form(endpoint: str, task: dict[str, Any], dependencies: str, revision: int) -> str:
    task_control = f"<input name='task' value='{escape(task['task'])}' required>"
    role_control = f"<input name='task_role' value='{escape(task['role'])}' required>"
    dependency_control = f"<input name='dependencies' value='{escape(dependencies)}'>"
    return (
        f"<form data-endpoint='{endpoint}' data-method='PATCH' class='form form--compact'><h5>Edit pending task</h5>"
        f"{_field('Task', task_control)}{_field('Role', role_control)}{_field('Dependencies', dependency_control)}"
        f"{_field('Priority', _priority_select(task.get('priority', 'P2')))}{_common_fields(revision)}"
        "<button class='button button--primary'>Save edit</button></form>"
    )


def _priority_form(endpoint: str, revision: int) -> str:
    return (
        f"<form data-endpoint='{endpoint}/priority' data-method='POST' class='form form--compact'><h5>Set priority</h5>"
        f"{_field('Priority', _priority_select('P2'))}{_common_fields(revision)}"
        "<button class='button button--secondary'>Set priority</button></form>"
    )


def _cancel_form(endpoint: str, revision: int) -> str:
    return (
        f"<form data-endpoint='{endpoint}/cancel' data-method='POST' class='form form--compact form--danger'><h5>Cancel pending task</h5>"
        f"{_common_fields(revision)}<button class='button button--danger'>Cancel task</button></form>"
    )


def _add_form(project_id: str, plan_id: str, revision: int) -> str:
    endpoint = f"/api/projects/{escape(project_id)}/plans/{escape(plan_id)}/tasks"
    id_control = "<input name='id' required pattern='[A-Za-z0-9_-]+'>"
    task_control = "<input name='task' required>"
    role_control = "<input name='task_role' required>"
    dependency_control = "<input name='dependencies'>"
    return (
        f"<form data-endpoint='{endpoint}' data-method='POST' class='form form--add'>"
        f"{_field('ID', id_control)}{_field('Task', task_control)}{_field('Role', role_control)}"
        f"{_field('Dependencies', dependency_control)}{_field('Priority', _priority_select('P2'))}"
        f"{_common_fields(revision)}<button class='button button--primary'>Add task</button></form>"
    )


def _field(label: str, control: str) -> str:
    return f"<label><span>{label}</span>{control}</label>"


def _common_fields(revision: int) -> str:
    return (
        _field("Actor ID", "<input name='actor_id' required>")
        + _field("Reason", "<input name='reason' required>")
        + f"<input type='hidden' name='expected_revision' value='{revision}'>"
    )


def _priority_select(selected: str) -> str:
    return "<select name='priority'>" + "".join(
        f"<option {'selected' if value == selected else ''}>{value}</option>" for value in ("P0", "P1", "P2", "P3")
    ) + "</select>"


def _event_list(events: list[dict[str, Any]], *, scrollable: bool = False) -> str:
    rows = "".join(
        "<li class='event-row'>"
        f"<span class='event-time'>{escape(str(event['frontmatter'].get('created', '')))}</span>"
        f"<strong>{escape(str(event['frontmatter'].get('event_type', '')))}</strong>"
        f"<span>{escape(str(event['frontmatter'].get('source', '')))} / {escape(str(event['frontmatter'].get('role', '')))}</span></li>"
        for event in events
    ) or _empty_state("No events")
    scroll_class = " event-list--scrollable" if scrollable else ""
    return f"<ul class='event-list{scroll_class}'>{rows}</ul>"


def _hero(title: str, subtitle: str, metrics: str, *, health: str | None = None) -> str:
    badge = f"<span class='status-chip status-chip--{escape(health)}'>{escape(health)}</span>" if health else ""
    return (
        "<section class='hero'><div class='hero-copy'><div class='hero-kicker'><span class='brand-mark'>LB</span>"
        f"<span>LLM Collaboration Blackboard</span>{badge}</div><h1>{escape(title)}</h1><p>{escape(subtitle)}</p></div>{metrics}</section>"
    )


def _metric_strip(metrics: tuple[tuple[str, int], ...]) -> str:
    return "<div class='metric-strip'>" + "".join(_mini_metric(label, value, large=True) for label, value in metrics) + "</div>"


def _mini_metric(label: str, value: int, *, large: bool = False) -> str:
    style = " metric--large" if large else ""
    return f"<span class='metric{style}'><strong>{value}</strong><small>{escape(label)}</small></span>"


def _breadcrumb(*items: tuple[str, str | None]) -> str:
    rendered = []
    for label, url in items:
        rendered.append(f"<a href='{escape(url)}'>{escape(label)}</a>" if url else f"<span>{escape(label)}</span>")
    return "<nav class='breadcrumb'>" + "<span>/</span>".join(rendered) + "</nav>"


def _empty_state(message: str) -> str:
    return f"<p class='empty-state'>{escape(message)}</p>"


def _layout(
    title: str,
    body: str,
    refresh_seconds: int,
    *,
    include_intervention_script: bool = False,
    include_plan_filter_script: bool = False,
) -> str:
    script = (_PLAN_FILTER_SCRIPT if include_plan_filter_script else "") + (_INTERVENTION_SCRIPT if include_intervention_script else "")
    return f"""<!doctype html><html lang='ja'><head><meta charset='utf-8'><title>{escape(title)}</title>
<meta name='viewport' content='width=device-width, initial-scale=1'><meta http-equiv='refresh' content='{refresh_seconds}'>
<style>{_STYLE}</style></head><body><div class='console-shell'><header class='topbar'><a class='brand' href='/'><span>LB</span>
<strong>Facilitator Console</strong></a><span>Local only</span></header><main>{body}</main></div>{script}</body></html>"""


_STYLE = """
:root { --ink: #e6edf8; --muted: #95a6c4; --bg: #070b16; --surface: #111a2d; --surface-2: #16223a; --line: #263757;
  --blue: #66a8ff; --cyan: #54dddb; --amber: #ffba5b; --red: #ff7d92; --green: #55d690; --shadow: 0 18px 48px rgba(0, 0, 0, .28); }
* { box-sizing: border-box; } body { margin: 0; color: var(--ink); background: radial-gradient(circle at 20% -10%, #223b75 0, transparent 30%), var(--bg);
  font: 14px/1.55 Inter, "Segoe UI", "Noto Sans JP", sans-serif; } a { color: inherit; text-decoration: none; } main { max-width: 1520px; margin: auto; padding: 36px 28px 56px; }
.console-shell { min-height: 100vh; } .topbar { height: 62px; padding: 0 28px; display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid var(--line); background: rgba(7, 11, 22, .84); backdrop-filter: blur(16px); color: var(--muted); }
.brand { display: flex; align-items: center; gap: 10px; color: var(--ink); letter-spacing: .02em; } .brand > span, .brand-mark { width: 28px; height: 28px; display: inline-grid; place-items: center; border-radius: 8px; color: #07101d; background: linear-gradient(135deg, var(--cyan), var(--blue)); font-size: 10px; font-weight: 800; }
.hero { display: flex; justify-content: space-between; gap: 28px; padding: 30px; border: 1px solid var(--line); border-radius: 18px; background: linear-gradient(130deg, rgba(32, 59, 116, .6), rgba(17, 26, 45, .85) 52%, rgba(14, 64, 80, .35)); box-shadow: var(--shadow); }
.hero-copy { max-width: 720px; } .hero-kicker, .card-topline, .panel-title, .section-heading, .column-heading, .task-meta { display: flex; align-items: center; justify-content: space-between; gap: 10px; } .hero-kicker { justify-content: flex-start; color: var(--muted); font-size: 12px; letter-spacing: .06em; text-transform: uppercase; }
h1, h2, h3, h4, h5, p { margin: 0; } h1 { margin-top: 14px; font-size: clamp(28px, 4vw, 44px); letter-spacing: -.035em; } h2 { font-size: 20px; letter-spacing: -.02em; } h3 { font-size: 17px; } h4 { font-size: 14px; line-height: 1.4; } h5 { font-size: 12px; color: var(--muted); text-transform: uppercase; letter-spacing: .05em; }
.hero p { margin-top: 8px; color: var(--muted); overflow-wrap: anywhere; }.eyebrow { color: var(--cyan); font-size: 11px; font-weight: 700; letter-spacing: .11em; text-transform: uppercase; }.metric-strip { display: grid; grid-template-columns: repeat(2, minmax(96px, 1fr)); gap: 9px; align-content: center; min-width: 260px; }
.metric { display: inline-flex; flex-direction: column; min-width: 54px; } .metric strong { font-size: 17px; } .metric small { color: var(--muted); font-size: 10px; text-transform: uppercase; letter-spacing: .06em; } .metric--large { padding: 12px; border: 1px solid rgba(116, 148, 205, .25); border-radius: 10px; background: rgba(6, 13, 28, .24); }.metric--large strong { font-size: 23px; }
.section-heading { margin: 34px 0 14px; }.refresh-note, .panel-hint, .card-id { color: var(--muted); font-size: 12px; }.project-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(285px, 1fr)); gap: 16px; }.project-card, .panel, .task-card { border: 1px solid var(--line); border-radius: 14px; background: linear-gradient(155deg, rgba(24, 36, 60, .95), rgba(14, 21, 38, .95)); box-shadow: 0 10px 25px rgba(0,0,0,.16); }.project-card { padding: 18px; transition: transform .18s ease, border-color .18s ease; }.project-card:hover { transform: translateY(-3px); border-color: #537ab7; }.project-card h3 { margin: 16px 0 4px; }.project-card h3 a:hover { color: var(--blue); }.path { min-height: 43px; color: var(--muted); font-size: 12px; overflow-wrap: anywhere; }
.status-chip { display: inline-flex; width: fit-content; padding: 3px 8px; border: 1px solid; border-radius: 999px; font-size: 10px; font-weight: 800; letter-spacing: .06em; text-transform: uppercase; }.status-chip--ok { color: var(--green); border-color: rgba(85,214,144,.45); background: rgba(85,214,144,.1); }.status-chip--invalid_plan, .status-chip--error { color: var(--red); border-color: rgba(255,125,146,.45); background: rgba(255,125,146,.1); }
.card-metrics, .card-footer, .row-metrics { display: flex; gap: 18px; align-items: center; }.card-metrics { margin-top: 16px; }.card-footer { padding-top: 14px; margin-top: 14px; border-top: 1px solid var(--line); color: var(--muted); font-size: 11px; }.inline-error { margin-top: 12px; color: var(--red); font-size: 12px; }.dashboard-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px; }.panel { padding: 20px; }.panel--wide { grid-column: span 2; }.plan-list, .compact-list { display: grid; gap: 9px; margin-top: 16px; }.compact-list--scrollable { max-height: 276px; overflow-y: auto; padding-right: 6px; }.plan-filter { display: inline-flex; align-items: center; gap: 7px; color: var(--muted); font-size: 12px; cursor: pointer; }.plan-filter input { accent-color: var(--cyan); }.plan-row { display: flex; justify-content: space-between; gap: 12px; padding: 14px; border-radius: 10px; background: rgba(4, 9, 20, .28); border: 1px solid transparent; }.plan-row[hidden] { display: none; }.plan-row:hover { border-color: #5278b4; }.plan-row strong, .compact-row strong { display: block; }.plan-row span { color: var(--muted); font-size: 12px; }.compact-row { display: grid; grid-template-columns: 10px 1fr auto; gap: 10px; align-items: center; padding: 9px 0; border-bottom: 1px solid rgba(38,55,87,.7); }.memory-row:hover { background: rgba(84,221,219,.06); color: var(--cyan); }.dot { width: 7px; height: 7px; border-radius: 50%; background: var(--green); }.dot--warning { background: var(--amber); }.dot--memory { background: var(--blue); }
.breadcrumb { display: flex; gap: 9px; margin-bottom: 16px; color: var(--muted); font-size: 12px; }.breadcrumb a:hover { color: var(--cyan); }.board { display: grid; grid-template-columns: repeat(6, minmax(220px, 1fr)); gap: 13px; overflow-x: auto; padding-bottom: 8px; }.board--scrollable { max-height: min(65vh, 640px); overflow: auto; padding: 0 6px 8px 0; }.board-column { min-height: 220px; padding: 12px; border: 1px solid var(--line); border-radius: 14px; background: rgba(4, 10, 22, .34); }.column-heading h3 { text-transform: capitalize; font-size: 13px; }.column-heading > span { display: inline-grid; place-items: center; min-width: 23px; height: 23px; border-radius: 8px; color: var(--muted); background: var(--surface-2); font-size: 11px; }.task-stack { display: grid; gap: 10px; margin-top: 13px; }.task-card { padding: 0; overflow: hidden; border-radius: 10px; border: 1px solid var(--line); border-left: 3px solid var(--blue); background: var(--surface); }.task-card--in_progress { border-left-color: var(--cyan); }.task-card--blocked { border-left-color: var(--amber); }.task-card--done { border-left-color: var(--green); opacity: .8; }.task-card--cancelled { border-left-color: var(--red); opacity: .65; }.task-summary { display: block; padding: 13px; cursor: pointer; list-style: none; }.task-summary::-webkit-details-marker { display: none; }.task-summary::after { content: 'View details'; display: block; margin-top: 9px; color: var(--cyan); font-size: 10px; font-weight: 700; letter-spacing: .06em; text-transform: uppercase; }.task-card[open] .task-summary::after { content: 'Hide details'; }.task-meta { color: var(--muted); font-size: 10px; letter-spacing: .05em; }.task-card h4 { margin: 10px 0 4px; }.task-card p { color: var(--muted); font-size: 12px; }.task-details { padding: 0 13px 13px; border-top: 1px solid var(--line); }.task-info { display: grid; gap: 7px; margin: 12px 0 0; }.task-info > div { display: grid; grid-template-columns: 92px minmax(0, 1fr); gap: 8px; }.task-info dt { color: var(--muted); font-size: 11px; }.task-info dd { margin: 0; overflow-wrap: anywhere; font-size: 12px; }.priority { padding: 2px 5px; border-radius: 5px; font-weight: 800; }.priority--p0 { color: #ff93a3; background: rgba(255,125,146,.14); }.priority--p1 { color: #ffc66e; background: rgba(255,186,91,.13); }.priority--p2 { color: #9cbef6; background: rgba(102,168,255,.13); }.priority--p3 { color: #9ba8bd; background: rgba(149,166,196,.12); }
.task-intervention { margin-top: 13px; padding-top: 10px; border-top: 1px solid var(--line); }.task-intervention > summary { color: var(--cyan); cursor: pointer; font-size: 12px; }.intervention-panel { display: grid; gap: 14px; margin-top: 12px; }.form { display: grid; gap: 10px; }.form--compact { padding: 11px; border-radius: 9px; background: rgba(4, 9, 20, .35); }.form--danger { border: 1px solid rgba(255,125,146,.24); }.form--add { grid-template-columns: repeat(3, minmax(0, 1fr)); align-items: end; }.form label { display: grid; gap: 4px; color: var(--muted); font-size: 11px; } input, select, button { min-width: 0; border-radius: 7px; font: inherit; } input, select { border: 1px solid #34496f; padding: 8px; color: var(--ink); background: #091224; } input:focus, select:focus { outline: 2px solid rgba(84,221,219,.55); border-color: var(--cyan); }.button { border: 0; padding: 9px 12px; color: #07101d; font-weight: 750; cursor: pointer; }.button--primary { background: linear-gradient(135deg, var(--cyan), #7eb2ff); }.button--secondary { color: #d8ebff; background: #263b60; }.button--danger { color: #ffdce2; background: #783449; }.event-list { display: grid; gap: 3px; margin: 16px 0 0; padding: 0; list-style: none; }.event-list--scrollable { max-height: 300px; overflow-y: auto; padding-right: 6px; }.event-row { display: grid; grid-template-columns: minmax(165px, .9fr) minmax(150px, 1fr) 1fr; gap: 12px; padding: 10px 0; border-bottom: 1px solid rgba(38,55,87,.7); color: var(--muted); }.event-row strong { color: var(--ink); }.event-time { font-variant-numeric: tabular-nums; font-size: 12px; }.markdown-panel { margin-top: 16px; }.markdown-source { max-height: min(65vh, 720px); margin: 16px 0 0; padding: 16px; overflow: auto; border: 1px solid var(--line); border-radius: 10px; color: #dbe9ff; background: #091224; font: 13px/1.65 "Cascadia Code", Consolas, monospace; white-space: pre-wrap; overflow-wrap: anywhere; }.empty-state { color: var(--muted); font-size: 12px; }.empty-hero { margin-top: 20px; padding: 36px; border: 1px solid var(--line); border-radius: 18px; background: var(--surface); }.error-hero { border-color: rgba(255,125,146,.55); }.error-hero p { margin-top: 10px; color: var(--muted); }.error-hero pre { margin: 20px 0 0; padding: 12px; overflow: auto; color: #ffc9d3; background: #130b14; border-radius: 8px; white-space: pre-wrap; }
@media (max-width: 860px) { main { padding: 24px 16px 42px; }.topbar { padding: 0 16px; }.hero { flex-direction: column; }.metric-strip { min-width: 0; grid-template-columns: repeat(4, minmax(0, 1fr)); }.dashboard-grid { grid-template-columns: 1fr; }.panel--wide { grid-column: span 1; }.form--add { grid-template-columns: 1fr 1fr; }.event-row { grid-template-columns: 1fr; gap: 2px; }.plan-row { flex-direction: column; }.row-metrics { flex-wrap: wrap; } }
@media (max-width: 520px) { .metric-strip { grid-template-columns: repeat(2, 1fr); }.form--add { grid-template-columns: 1fr; }.card-footer { flex-direction: column; align-items: flex-start; gap: 4px; } }
"""

_INTERVENTION_SCRIPT = """
<script>
for (const form of document.querySelectorAll('form[data-endpoint]')) {
  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    const payload = Object.fromEntries(new FormData(form));
    payload.expected_revision = Number(payload.expected_revision);
    if ('dependencies' in payload) payload.dependencies = payload.dependencies.split(',').map((item) => item.trim()).filter(Boolean);
    const response = await fetch(form.dataset.endpoint, { method: form.dataset.method, headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload) });
    if (response.ok) { window.location.reload(); return; }
    const error = await response.json();
    alert(`Intervention was not applied (${response.status}): ${JSON.stringify(error.detail)}`);
    if (response.status === 409) window.location.reload();
  });
}
</script>
"""

_PLAN_FILTER_SCRIPT = """
<script>
const completedPlanToggle = document.getElementById('completed-plan-toggle');
if (completedPlanToggle) {
  const applyCompletedPlanFilter = () => {
    document.querySelectorAll('[data-plan-completed="true"]').forEach((plan) => { plan.hidden = !completedPlanToggle.checked; });
  };
  applyCompletedPlanFilter();
  completedPlanToggle.addEventListener('change', () => {
    applyCompletedPlanFilter();
  });
}
</script>
"""
