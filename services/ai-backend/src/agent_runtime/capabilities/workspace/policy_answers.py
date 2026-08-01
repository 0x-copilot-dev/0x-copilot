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

# The desktop host backend owns the wording of its own answers. Referencing the
# constants keeps ONE copy of each user-facing string; transcribing them here
# would let the two drift until the classifier silently stopped matching — which
# is precisely the failure a live journey caught.
from agent_runtime.capabilities.desktop.workspace_backend import (
    _SafeMessage as _DesktopSafeMessage,
)


class WorkspacePolicyAnswerCode(StrEnum):
    """Typed codes for workspace answers that are decisions, not failures."""

    #: The enforce-mode tombstone: no workspace capability is mounted at all.
    UNAVAILABLE = "workspace_unavailable"
    #: Host access is on, but the user has shared no folder yet. This is the
    #: one the desktop app actually emits for `ls /workspace/` on a fresh chat.
    NO_GRANTS = "workspace_no_grants"
    #: The user's grants do not cover the requested path. Denied by policy —
    #: the remedy is granting access, never repeating the call.
    PERMISSION_DENIED = "workspace_permission_denied"


class WorkspacePolicyAnswers:
    """Declared policy answers and the typed code each one carries."""

    #: Emitted by every tombstone-backend operation and by upload rejection.
    #: Kept byte-identical to what the model reads, so the agent still receives
    #: a clean, actionable sentence rather than a serialised envelope.
    UNAVAILABLE = (
        "Local workspace access is unavailable. Create an artifact or download "
        "instead; no local file was changed."
    )

    #: The desktop host backend's own copy, referenced rather than duplicated —
    #: a second transcription of user-facing text is exactly how these two
    #: drift apart and the classifier silently stops matching. Imported at
    #: module scope below; `capabilities/desktop` imports nothing from here, so
    #: there is no cycle.
    NO_GRANTS = _DesktopSafeMessage.NO_GRANTS
    PERMISSION_DENIED = _DesktopSafeMessage.PERMISSION_DENIED

    _BY_TEXT: Mapping[str, WorkspacePolicyAnswerCode] = MappingProxyType(
        {
            UNAVAILABLE: WorkspacePolicyAnswerCode.UNAVAILABLE,
            NO_GRANTS: WorkspacePolicyAnswerCode.NO_GRANTS,
            PERMISSION_DENIED: WorkspacePolicyAnswerCode.PERMISSION_DENIED,
        }
    )

    #: The Deep Agents filesystem middleware renders a backend error two ways
    #: depending on the tool: ``content=f"Error: {result.error}"`` for the read
    #: family (ls / read_file / glob / grep) and bare ``content=res.error`` for
    #: the write family. The declared message is the same either way, so the
    #: prefix is stripped before matching rather than duplicated in the table.
    #: Verified against the installed middleware by
    #: ``test_matches_the_real_middleware_rendering``.
    _TRANSPORT_PREFIX = "error:"

    @classmethod
    def code_for(cls, text: object) -> WorkspacePolicyAnswerCode | None:
        """Return the policy code for ``text``, or ``None`` when it is a fault.

        Matching is exact on the declared message, after removing whitespace and
        the middleware's ``Error: `` prefix. Anything else — including the
        gateway's caught-exception messages, which ARE real failures — returns
        ``None`` and keeps its failure classification.
        """
        if not isinstance(text, str):
            return None
        candidate = text.strip()
        if candidate.lower().startswith(cls._TRANSPORT_PREFIX):
            candidate = candidate[len(cls._TRANSPORT_PREFIX) :].strip()
        return cls._BY_TEXT.get(candidate)

    @classmethod
    def is_policy_answer(cls, text: object) -> bool:
        """Whether ``text`` is a declared policy answer rather than a fault."""
        return cls.code_for(text) is not None
