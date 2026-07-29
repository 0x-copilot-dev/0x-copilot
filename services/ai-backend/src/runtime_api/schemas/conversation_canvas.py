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
from agent_runtime.surfaces_v2.ledger_models import SurfaceAccent


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
    #: The identity hue chosen at publication, if one was. Travels with `title`
    #: because it is the same kind of fact — display identity the artifact owns,
    #: which the tab strip and the surface card must agree on. ``None`` means no
    #: preference was expressed, and the client derives a hue from the renderer
    #: hint; it does NOT mean "no colour", which is the explicit ``none`` value.
    accent: SurfaceAccent | None = None
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
