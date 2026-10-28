"""Shared exception types and small value objects for NEXUS Command Center."""

from __future__ import annotations


class NexusError(Exception):
    """Base error with an HTTP status and a stable machine code."""

    status = 500
    code = "nexus.error"

    def __init__(self, message: str, *, status: int | None = None,
                 code: str | None = None):
        super().__init__(message)
        self.message = message
        if status is not None:
            self.status = status
        if code is not None:
            self.code = code


class ValidationError(NexusError):
    status = 400
    code = "nexus.validation"


class UnauthorizedError(NexusError):
    status = 401
    code = "nexus.unauthorized"


class ForbiddenError(NexusError):
    status = 403
    code = "nexus.forbidden"


class NotFoundError(NexusError):
    status = 404
    code = "nexus.not_found"


class ConflictError(NexusError):
    status = 409
    code = "nexus.conflict"


class PreconditionError(NexusError):
    status = 412
    code = "nexus.precondition"


class StoreUnavailableError(NexusError):
    status = 503
    code = "nexus.store_unavailable"


# ---------------------------------------------------------------------------
# Durable task state vocabulary. One source of truth for the engine + UI.
# ---------------------------------------------------------------------------

# Open (non-terminal) states, in rough lifecycle order.
CREATED = "created"
QUEUED = "queued"
WAITING_APPROVAL = "waiting_approval"
RUNNING = "running"

# Terminal states.
DONE = "done"
FAILED = "failed"
CANCELLED = "cancelled"

# States a task may be claimed from (i.e. a worker may pick it up).
CLAIMABLE = (QUEUED,)
ELIGIBLE = tuple(CLAIMABLE)

# Non-terminal states (may still become terminal through allowed transitions).
OPEN_STATES = (CREATED, QUEUED, WAITING_APPROVAL, RUNNING)
TERMINAL_STATES = (DONE, FAILED, CANCELLED)
ALL_STATES = OPEN_STATES + TERMINAL_STATES
