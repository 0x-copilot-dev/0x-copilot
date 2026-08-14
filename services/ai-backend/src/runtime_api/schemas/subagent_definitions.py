"""Wire shape for the declared-agent routes.

One envelope and nothing else. The element type is the domain contract
:class:`~agent_runtime.delegation.subagents.contracts.SubagentDefinition`
itself, because a declared agent's ``tools`` / ``skills`` / ``allowed_scopes``
are a capability grant that ``SubagentAuthorityPolicy.narrow`` reads directly —
a parallel DTO would be a second place for one of those ceilings to be dropped
or widened in translation, and the bug would look like a mapping-layer typo
rather than a permission hole.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from agent_runtime.delegation.subagents.contracts import SubagentDefinition


class DeclaredSubagentListResponse(BaseModel):
    """Every agent this installation has declared, sorted by name."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    subagents: tuple[SubagentDefinition, ...] = ()


__all__ = ("DeclaredSubagentListResponse",)
