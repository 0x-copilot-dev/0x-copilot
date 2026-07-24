"""Pure, server-owned presentation policy and lifecycle projections.

Presentation is intentionally separate from execution, artifact persistence, and
effect application.  Modules in this package consume structural ledger events and
safe metadata only; they never inspect model prose or artifact bytes.
"""

from agent_runtime.presentation.policy import (
    PresentationPolicy,
    PresentationPolicyDecision,
    PresentationPolicyInput,
)
from agent_runtime.presentation.lifecycle import (
    CanvasLifecycleProjection,
    CanvasLifecycleState,
    CanvasProjection,
    CanvasSubject,
    CanvasSubjectKind,
)

__all__ = (
    "PresentationPolicy",
    "PresentationPolicyDecision",
    "PresentationPolicyInput",
    "CanvasLifecycleProjection",
    "CanvasLifecycleState",
    "CanvasProjection",
    "CanvasSubject",
    "CanvasSubjectKind",
)
