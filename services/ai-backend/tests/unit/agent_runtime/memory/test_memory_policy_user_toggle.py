"""PR 8.0.5 — memory toggle short-circuits writes when user opts out."""

from __future__ import annotations

from agent_runtime.context.memory.constants import Messages, Values
from agent_runtime.context.memory.contracts import (
    MemoryAccessOperation,
    MemoryActorRole,
)
from agent_runtime.context.memory.policy import MemoryPolicyAuthorizer


class TestMemoryWriteToggle:
    def test_writes_disabled_returns_user_message(self) -> None:
        decision = MemoryPolicyAuthorizer.authorize(
            path=f"{Values.Path.MEMORIES}/foo",
            actor_role=MemoryActorRole.USER,
            operation=MemoryAccessOperation.WRITE,
            content="hi",
            memory_writes_allowed=False,
        )
        assert decision.allowed is False
        # The dedicated message MUST land — distinct from the
        # generic policy-denied path so observability can split them.
        assert decision.safe_message == Messages.Errors.MEMORY_DISABLED_BY_USER

    def test_writes_disabled_does_not_affect_reads(self) -> None:
        # Reads still resolve through path policy. A user disabling
        # *writes* does not blind the assistant to existing memories.
        decision = MemoryPolicyAuthorizer.authorize(
            path=f"{Values.Path.MEMORIES}/foo",
            actor_role=MemoryActorRole.USER,
            operation=MemoryAccessOperation.READ,
            memory_writes_allowed=False,
        )
        assert decision.allowed is True

    def test_default_call_unchanged(self) -> None:
        # Existing call sites that don't thread the toggle keep working
        # exactly as before — backwards compatible.
        decision = MemoryPolicyAuthorizer.authorize(
            path=f"{Values.Path.MEMORIES}/foo",
            actor_role=MemoryActorRole.USER,
            operation=MemoryAccessOperation.WRITE,
            content="hi",
        )
        assert decision.allowed is True

    def test_explicit_true_is_passthrough(self) -> None:
        decision = MemoryPolicyAuthorizer.authorize(
            path=f"{Values.Path.MEMORIES}/foo",
            actor_role=MemoryActorRole.USER,
            operation=MemoryAccessOperation.WRITE,
            content="hi",
            memory_writes_allowed=True,
        )
        assert decision.allowed is True
