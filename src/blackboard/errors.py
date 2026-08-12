"""Domain errors returned by the Blackboard service."""

from __future__ import annotations


class BlackboardError(Exception):
    code = "blackboard_error"

    def as_dict(self) -> dict[str, str]:
        return {"error": self.code, "message": str(self)}


class ValidationError(BlackboardError):
    code = "validation_error"


class NotFoundError(BlackboardError):
    code = "not_found"


class ConflictError(BlackboardError):
    code = "conflict"


class NotExecutableError(BlackboardError):
    code = "not_executable"


class RoleMismatchError(BlackboardError):
    code = "role_mismatch"


class PermissionDeniedError(BlackboardError):
    code = "permission_denied"


class ClaimOwnerError(BlackboardError):
    code = "not_claim_owner"


class InvalidTransitionError(BlackboardError):
    code = "invalid_transition"
