"""Authoritative narrowing rules for delegated subagent work.

Subagent definitions describe the *maximum* capability envelope a task may
receive.  A parent grant is an independent ceiling.  The child always gets the
intersection of the two plus any explicit request; it can never recover a
tool, scope, skill, capability, or approval posture that one of those parents
withheld.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import ClassVar

from pydantic import Field, field_validator

from agent_runtime.capabilities.tools.permissions import (
    ToolUsePolicyKind,
    ToolUsePolicyMode,
)
from agent_runtime.execution.contracts import RuntimeContract
from agent_runtime.validation import ValueNormalizer


class SubagentAuthorityError(ValueError):
    """The supplied child handoff attempted to escape verified authority."""


class SubagentPolicyGrant(RuntimeContract):
    """The three approval-policy ceilings that a delegated task must obey."""

    read: ToolUsePolicyMode = ToolUsePolicyMode.AUTO
    write: ToolUsePolicyMode = ToolUsePolicyMode.ASK
    destructive: ToolUsePolicyMode = ToolUsePolicyMode.REQUIRE

    _RANK: ClassVar[dict[ToolUsePolicyMode, int]] = {
        ToolUsePolicyMode.AUTO: 0,
        ToolUsePolicyMode.ASK: 1,
        ToolUsePolicyMode.REQUIRE: 2,
        ToolUsePolicyMode.BLOCK: 3,
    }

    @classmethod
    def narrow(
        cls,
        parent: "SubagentPolicyGrant",
        definition: "SubagentPolicyGrant",
    ) -> "SubagentPolicyGrant":
        """Keep the more restrictive value for every approval-policy axis."""

        def stricter(
            left: ToolUsePolicyMode, right: ToolUsePolicyMode
        ) -> ToolUsePolicyMode:
            return left if cls._RANK[left] >= cls._RANK[right] else right

        return cls(
            read=stricter(parent.read, definition.read),
            write=stricter(parent.write, definition.write),
            destructive=stricter(parent.destructive, definition.destructive),
        )

    def mode_for(self, kind: ToolUsePolicyKind) -> ToolUsePolicyMode:
        return {
            ToolUsePolicyKind.READ: self.read,
            ToolUsePolicyKind.WRITE: self.write,
            ToolUsePolicyKind.DESTRUCTIVE: self.destructive,
        }[kind]


class SubagentCapabilityGrant(RuntimeContract):
    """Verified parent or effective child capability ceiling.

    An empty set is a real deny-all ceiling, not a wildcard.  Callers that
    intentionally inherit a definition must construct a grant from that
    definition through :class:`SubagentAuthorityPolicy`.
    """

    capabilities: frozenset[str] = Field(default_factory=frozenset)
    tools: frozenset[str] = Field(default_factory=frozenset)
    skills: frozenset[str] = Field(default_factory=frozenset)
    permission_scopes: frozenset[str] = Field(default_factory=frozenset)
    policy: SubagentPolicyGrant = Field(default_factory=SubagentPolicyGrant)

    @field_validator("capabilities", "tools", "skills", mode="before")
    @classmethod
    def _normalize_slug_set(cls, value: object) -> frozenset[str]:
        return frozenset(
            ValueNormalizer.normalize_slug(item, "subagent_capability_grant")
            for item in _iterable(value)
        )

    @field_validator("permission_scopes", mode="before")
    @classmethod
    def _normalize_scope_set(cls, value: object) -> frozenset[str]:
        return frozenset(
            ValueNormalizer.normalize_scope(item, "subagent_permission_scopes")
            for item in _iterable(value)
        )


class SubagentAuthorityPolicy:
    """Pure intersection and identity checks shared by handoff and lifecycle."""

    DISPATCH_CAPABILITY = "subagent"

    @classmethod
    def inherited_parent_grant(
        cls,
        *,
        context_scopes: frozenset[str],
        definition_tools: frozenset[str],
        definition_skills: frozenset[str],
    ) -> SubagentCapabilityGrant:
        """Build the compatibility parent ceiling when no explicit grant exists.

        This is deliberately the definition's own ceiling, not an unbounded
        wildcard.  Existing callers retain their behavior while new assembly
        points can pass a narrower verified parent grant.
        """

        return SubagentCapabilityGrant(
            capabilities=frozenset({cls.DISPATCH_CAPABILITY}),
            tools=definition_tools,
            skills=definition_skills,
            permission_scopes=context_scopes,
        )

    @classmethod
    def narrow(
        cls,
        *,
        parent: SubagentCapabilityGrant,
        definition_tools: frozenset[str],
        definition_skills: frozenset[str],
        definition_allowed_scopes: frozenset[str],
        definition_policy: SubagentPolicyGrant,
        requested_tools: Iterable[str],
        requested_skills: Iterable[str],
        context_scopes: frozenset[str],
    ) -> SubagentCapabilityGrant:
        """Compute a fail-closed child grant with no widening path."""

        requested_tool_set = _requested_or_configured(requested_tools, definition_tools)
        requested_skill_set = _requested_or_configured(
            requested_skills, definition_skills
        )
        scope_ceiling = (
            definition_allowed_scopes
            if definition_allowed_scopes
            else parent.permission_scopes
        )
        return SubagentCapabilityGrant(
            capabilities=parent.capabilities.intersection({cls.DISPATCH_CAPABILITY}),
            tools=parent.tools.intersection(definition_tools, requested_tool_set),
            skills=parent.skills.intersection(definition_skills, requested_skill_set),
            permission_scopes=parent.permission_scopes.intersection(
                context_scopes, scope_ceiling
            ),
            policy=SubagentPolicyGrant.narrow(parent.policy, definition_policy),
        )

    @staticmethod
    def require_same_tenant(
        *,
        expected_user_id: str,
        expected_org_id: str,
        expected_trace_id: str,
        received_user_id: str,
        received_org_id: str,
        received_trace_id: str,
    ) -> None:
        """Reject a handoff whose context was not minted from this parent run."""

        if (
            expected_user_id != received_user_id
            or expected_org_id != received_org_id
            or expected_trace_id != received_trace_id
        ):
            raise SubagentAuthorityError("subagent context does not match parent run")


def _iterable(value: object) -> tuple[object, ...]:
    if value is None:
        return ()
    if isinstance(value, (str, bytes)):
        raise ValueError("subagent capability grants must be iterables, not strings")
    try:
        return tuple(value)  # type: ignore[arg-type]
    except TypeError as exc:
        raise ValueError("subagent capability grants must be iterables") from exc


def _requested_or_configured(
    requested: Iterable[str], configured: frozenset[str]
) -> frozenset[str]:
    normalized = frozenset(
        ValueNormalizer.normalize_slug(item, "requested_subagent_capability")
        for item in requested
    )
    return normalized if normalized else configured


__all__ = (
    "SubagentAuthorityError",
    "SubagentAuthorityPolicy",
    "SubagentCapabilityGrant",
    "SubagentPolicyGrant",
)
