"""Role to operation permission mapping (spec chapter 18).

Authentication is out of scope. Actors still declare their role; the service
enforces which operations that declared role may perform.
"""

from __future__ import annotations

from .errors import PermissionDeniedError
from .models import require_role

BASE_OPERATIONS = frozenset(
    {
        "claim_task",
        "update_task",
        "write_memory",
        "write_state",
        "emit_event",
        "add_task",
        "edit_task",
        "cancel_task",
    }
)

ROLE_PERMISSIONS: dict[str, frozenset[str]] = {
    # Researchers capture findings; they do not own current-state updates.
    "Researcher": frozenset({
        "claim_task", "update_task", "write_memory", "emit_event", "add_task", "edit_task", "cancel_task",
    }),
    "Implementer": BASE_OPERATIONS,
    # Reviewers may recover blocked tasks back to pending.
    "Reviewer": BASE_OPERATIONS | {"recover_task"},
    # Facilitators can reshape only the unclaimed backlog. Dashboard endpoint
    # guards enforce pending-only semantics; this mapping remains declarative
    # because authentication is still out of scope.
    "Facilitator": frozenset({
        "write_memory", "write_state", "emit_event", "add_task", "edit_task", "cancel_task", "set_task_priority",
    }),
}


def permissions_for(role: str) -> frozenset[str]:
    """Return the operations allowed for a declared role.

    Unknown roles receive the base mutating set without recovery, so free-form
    Plan roles keep working while recovery stays explicitly granted.
    """
    normalized = require_role(role)
    return ROLE_PERMISSIONS.get(normalized, BASE_OPERATIONS)


def require_permission(role: str, operation: str) -> str:
    normalized = require_role(role)
    if operation not in permissions_for(normalized):
        raise PermissionDeniedError(f"role '{normalized}' is not permitted to '{operation}'")
    return normalized
