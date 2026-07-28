"""Wire shape for ``GET /v1/agent/conversations/{conversation_id}/canvas``.

Canvas identity is conversation-scoped; operation state stays run-scoped
(PRD-02). These subjects say *what can be opened*, never *what may be decided* —
approve/reject authority remains bound to the run that staged the work.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from agent_runtime.execution.contracts import RuntimeContract


class ConversationCanvasSubject(RuntimeContract):
    """One openable canvas subject, with the run that produced it."""

    #: Stable identity the client keys tabs on. Byte-identical to the key
    #: ``projectCanvasLifecycle`` produces for the same subject, which is what
    #: lets live and archived subjects merge without a reconciliation table.
    subject_key: str = Field(min_length=1, max_length=512)
    kind: Literal["artifact", "surface"]
    subject_id: str = Field(min_length=1, max_length=255)
    #: Provenance only. The client renders it and gates decision affordances on
    #: it, but the server must never accept it back as a scope widener.
    run_id: str = Field(min_length=1, max_length=255)
    title: str = Field(max_length=512)
    revision: int | None = None
    renderer_hint: str = Field(min_length=1, max_length=128)
    created_at: datetime


class ConversationCanvasResponse(RuntimeContract):
    """The conversation's openable subjects, newest first."""

    conversation_id: str = Field(min_length=1, max_length=255)
    subjects: tuple[ConversationCanvasSubject, ...] = ()
    next_cursor: str | None = None


__all__ = (
    "ConversationCanvasResponse",
    "ConversationCanvasSubject",
)
