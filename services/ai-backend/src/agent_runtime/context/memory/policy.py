"""Read/write policy checks for scoped memory paths."""

from __future__ import annotations

from pydantic import field_validator

from agent_runtime.execution.contracts import RuntimeContract, RuntimeErrorCode
from agent_runtime.execution.errors import AgentRuntimeError
from agent_runtime.context.memory.constants import Keys, Messages, Values
from agent_runtime.context.memory.prompt_injection import PromptInjectionDetector
from agent_runtime.context.memory.contracts import (
    MemoryAccessOperation,
    MemoryActorRole,
    MemoryPathPolicy,
    MemoryValueNormalizer,
)


class MemoryAccessRequest(RuntimeContract):
    """Access request bound to a concrete path and actor."""

    path: str
    actor_role: MemoryActorRole
    operation: MemoryAccessOperation
    content: str | None = None

    @field_validator(Keys.Field.PATH)
    @classmethod
    def _normalize_path(cls, value: object) -> str:
        return MemoryValueNormalizer.normalize_memory_path(value, Keys.Field.PATH)

    @field_validator(Keys.Field.CONTENT)
    @classmethod
    def _normalize_optional_content(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return MemoryValueNormalizer.normalize_nonempty_string(
            value, Keys.Field.CONTENT
        )


class MemoryPolicyDecision:
    """Public policy result for read/write authorization."""

    def __init__(self, *, allowed: bool, safe_message: str | None = None) -> None:
        self.allowed = allowed
        self.safe_message = safe_message

    @classmethod
    def allow(cls) -> "MemoryPolicyDecision":
        return cls(allowed=True)

    @classmethod
    def deny(cls, safe_message: str) -> "MemoryPolicyDecision":
        return cls(allowed=False, safe_message=safe_message)


class MemoryPolicyAuthorizer:
    """Authorize memory access before any content is read or written."""

    @classmethod
    def default_policies(cls) -> tuple[MemoryPathPolicy, ...]:
        """Return the default enterprise memory path policies."""

        return (
            MemoryPathPolicy(
                path_prefix=Values.Path.MEMORIES,
                read_roles={MemoryActorRole.USER, MemoryActorRole.ASSISTANT},
                write_roles={MemoryActorRole.USER, MemoryActorRole.ASSISTANT},
                shared=False,
                approval_required=False,
            ),
            MemoryPathPolicy(
                path_prefix=Values.Path.POLICIES,
                read_roles={
                    MemoryActorRole.USER,
                    MemoryActorRole.ASSISTANT,
                    MemoryActorRole.APPLICATION,
                },
                write_roles={MemoryActorRole.APPLICATION},
                shared=True,
                approval_required=True,
            ),
            MemoryPathPolicy(
                path_prefix=Values.Path.SKILLS,
                read_roles={MemoryActorRole.ASSISTANT, MemoryActorRole.APPLICATION},
                write_roles={MemoryActorRole.APPLICATION},
                shared=True,
                approval_required=True,
            ),
        )

    @classmethod
    def policy_for_path(
        cls,
        path: str,
        policies: tuple[MemoryPathPolicy, ...] | None = None,
    ) -> MemoryPathPolicy | None:
        """Return the most specific policy matching the supplied memory path."""

        normalized_path = MemoryValueNormalizer.normalize_memory_path(
            path, Keys.Field.PATH
        )
        candidates = tuple(
            policy
            for policy in (policies or cls.default_policies())
            if normalized_path.startswith(policy.path_prefix)
        )
        if not candidates:
            return None
        return max(candidates, key=lambda policy: len(policy.path_prefix))

    @classmethod
    def authorize(
        cls,
        *,
        path: str,
        actor_role: MemoryActorRole,
        operation: MemoryAccessOperation,
        content: str | None = None,
        policies: tuple[MemoryPathPolicy, ...] | None = None,
        memory_writes_allowed: bool | None = None,
    ) -> MemoryPolicyDecision:
        """Return whether a memory operation is allowed by path policy.

        ``memory_writes_allowed=False`` (PR 8.0.5) short-circuits every
        write with the dedicated ``MEMORY_DISABLED_BY_USER`` message —
        keeps the user-toggled refusal cleanly separable from the
        policy-denied path in observability + audit. ``None`` (the
        default) leaves existing call sites that don't yet thread the
        snapshot working unchanged.
        """

        if memory_writes_allowed is False and operation is MemoryAccessOperation.WRITE:
            return MemoryPolicyDecision.deny(Messages.Errors.MEMORY_DISABLED_BY_USER)

        policy = cls.policy_for_path(path, policies)
        if policy is None:
            return MemoryPolicyDecision.deny(Messages.Errors.MEMORY_POLICY_DENIED)

        allowed_roles = (
            policy.read_roles
            if operation is MemoryAccessOperation.READ
            else policy.write_roles
        )
        if actor_role not in allowed_roles:
            return MemoryPolicyDecision.deny(Messages.Errors.MEMORY_POLICY_DENIED)

        if (
            operation is MemoryAccessOperation.WRITE
            and PromptInjectionDetector.is_prompt_injection(content)
        ):
            return MemoryPolicyDecision.deny(Messages.Errors.PROMPT_INJECTION_REJECTED)

        return MemoryPolicyDecision.allow()

    @classmethod
    def ensure_authorized(
        cls,
        *,
        path: str,
        actor_role: MemoryActorRole,
        operation: MemoryAccessOperation,
        content: str | None = None,
        policies: tuple[MemoryPathPolicy, ...] | None = None,
        correlation_id: str | None = None,
    ) -> None:
        """Raise a safe typed error when a memory operation is not allowed."""

        decision = cls.authorize(
            path=path,
            actor_role=actor_role,
            operation=operation,
            content=content,
            policies=policies,
        )
        if decision.allowed:
            return
        raise AgentRuntimeError(
            RuntimeErrorCode.PERMISSION_DENIED,
            decision.safe_message or Messages.Errors.MEMORY_POLICY_DENIED,
            retryable=False,
            correlation_id=correlation_id,
        )


# P11.4: ``MemoryWriteGuard`` was removed. Its only purpose was the
# prompt-injection phrase list + ``is_prompt_injection`` classifier,
# which now lives in :mod:`agent_runtime.context.memory.prompt_injection`.
# ``MemoryPolicyAuthorizer`` above calls
# :class:`PromptInjectionDetector` directly.
