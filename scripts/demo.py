"""Human-inspectable walkthrough of the spec 20.2 MVP scenario.

Creates a real Blackboard on disk (default: ./demo_blackboard) and drives it
through the Researcher -> Implementer -> Reviewer flow using only the same
BlackboardService operations an MCP client would call. After running, open
the generated directory to inspect the Markdown Plan, Memory, State, and
Event files by hand.

Usage:
    python scripts/demo.py [path]
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from blackboard.service import BlackboardService  # noqa: E402


def _print_plan(service: BlackboardService, label: str) -> None:
    plan = service.read_plan()
    print(f"\n--- {label} ---")
    for task in plan["tasks"]:
        print(f"  {task['id']:<10} {task['status']:<12} role={task['role']:<12} by={task['started_by'] or '-'}")


def main() -> None:
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent / "demo_blackboard"
    if root.exists():
        shutil.rmtree(root)

    service = BlackboardService(root)
    service.initialize_project(
        "demo",
        "Project Alpha",
        [
            {"id": "research", "task": "Research the problem space", "role": "Researcher"},
            {"id": "implement", "task": "Implement the solution", "role": "Implementer"},
            {"id": "review", "task": "Review the implementation", "role": "Reviewer"},
        ],
        {"implement": ["research"], "review": ["implement"]},
    )
    print(f"Blackboard created at: {root}")
    _print_plan(service, "initial plan")

    # LLM A = Researcher
    plan = service.read_plan("Researcher")
    claim = service.claim_task("research", "llm_a", "Researcher", plan["revision"])
    service.write_memory("research_findings", "# Findings\n\nThe problem is well scoped.", "llm_a", "Researcher")
    service.update_task("research", "llm_a", "Researcher", "done", claim["plan_revision"])
    _print_plan(service, "after Researcher finishes")

    # LLM B = Implementer
    plan = service.read_plan("Implementer")
    claim = service.claim_task("implement", "llm_b", "Implementer", plan["revision"])
    service.write_state("demo_state", "Implementation under way.", "llm_b", "Implementer", "in_progress", current_task="implement")
    service.update_task("implement", "llm_b", "Implementer", "done", claim["plan_revision"])
    _print_plan(service, "after Implementer finishes")

    # LLM C = Reviewer
    plan = service.read_plan("Reviewer")
    claim = service.claim_task("review", "llm_c", "Reviewer", plan["revision"])
    service.update_task("review", "llm_c", "Reviewer", "done", claim["plan_revision"])
    _print_plan(service, "after Reviewer finishes")

    print("\n--- Memory written by llm_a, read back ---")
    print(service.read_memory("research_findings")["content"])

    print("--- Events (most recent first) ---")
    for event in service.read_events():
        print(f"  {event['frontmatter']['event_type']:<18} task={event['frontmatter'].get('task_id')}")

    print(f"\nInspect the Markdown files under: {root}")


if __name__ == "__main__":
    main()
