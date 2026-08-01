"""Workspace answers that are policy decisions rather than faults.

The Deep Agents ``BackendProtocol`` result types (``LsResult``, ``ReadResult``,
``WriteResult``, …) carry a single ``error: str | None`` channel, and the
filesystem middleware renders a populated ``error`` as ``(text, "error")`` — the
same shape a crashed tool produces. Two very different things therefore leave the
backend indistinguishable:

* *"I broke."* — an exception was caught and the operation genuinely failed.
* *"Not available here, do X instead."* — a deliberate, correct answer from
  :class:`~agent_runtime.capabilities.workspace.deep_backend.WorkspaceTombstoneBackend`,
  the always-mounted fallback for "no folder is attached to this chat".

Collapsing the second into the first is what made a working policy decision
render as a run-level alarm. This registry is the ONE place that re-establishes
the distinction: it declares the exact text of each policy answer alongside the
typed code it should carry, so ``tool_result`` classification can keep a policy
answer out of the failure taxonomy without pattern-matching prose at a distance.

Adding a policy answer means adding it here — never a bare string at a call site.
"""

from __future__ import annotations

from enum import StrEnum
from types import MappingProxyType
from typing import Mapping


class WorkspacePolicyAnswerCode(StrEnum):
    """Typed codes for workspace answers that are decisions, not failures."""

    #: No folder is attached to this chat, so local files cannot be reached.
    UNAVAILABLE = "workspace_unavailable"


class WorkspacePolicyAnswers:
    """Declared policy answers and the typed code each one carries."""

    #: Emitted by every tombstone-backend operation and by upload rejection.
    #: Kept byte-identical to what the model reads, so the agent still receives
    #: a clean, actionable sentence rather than a serialised envelope.
    UNAVAILABLE = (
        "Local workspace access is unavailable. Create an artifact or download "
        "instead; no local file was changed."
    )

    _BY_TEXT: Mapping[str, WorkspacePolicyAnswerCode] = MappingProxyType(
        {UNAVAILABLE: WorkspacePolicyAnswerCode.UNAVAILABLE}
    )

    @classmethod
    def code_for(cls, text: object) -> WorkspacePolicyAnswerCode | None:
        """Return the policy code for ``text``, or ``None`` when it is a fault.

        Matching is exact on the declared message (after trimming surrounding
        whitespace the tool transport may add). Anything else — including the
        gateway's caught-exception messages, which ARE real failures — returns
        ``None`` and keeps its failure classification.
        """
        if not isinstance(text, str):
            return None
        return cls._BY_TEXT.get(text.strip())

    @classmethod
    def is_policy_answer(cls, text: object) -> bool:
        """Whether ``text`` is a declared policy answer rather than a fault."""
        return cls.code_for(text) is not None
