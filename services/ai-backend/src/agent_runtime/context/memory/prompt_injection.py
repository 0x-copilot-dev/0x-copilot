"""Heuristic prompt-injection detection for memory writes.

The detector is a closed phrase list. Memory writes that contain any phrase
(case-insensitive) are rejected by ``MemoryPolicyAuthorizer``. The list is
intentionally short and exact — false-positive cost on legitimate memory writes
is high, and the attacker surface here is narrow (the model must be convinced
to write adversarial instructions to its own memory). This detector is one
cheap layer in defense-in-depth; broader mitigation lives in the system-prompt
and tool-permission layers.
"""

from __future__ import annotations


class PromptInjectionDetector:
    """Stateless classifier for memory-write content.

    The pattern tuple is class-scoped so consumers can introspect
    ``PromptInjectionDetector.PROMPT_INJECTION_PATTERNS`` without a
    separate module-level export.
    """

    PROMPT_INJECTION_PATTERNS: tuple[str, ...] = (
        "ignore previous instructions",
        "ignore all previous instructions",
        "reveal the system prompt",
        "developer message",
        "system message",
    )

    @classmethod
    def is_prompt_injection(cls, content: str | None) -> bool:
        """Return ``True`` when ``content`` matches any injection phrase (case-insensitive).

        ``None`` returns ``False`` so callers can forward optional content
        fields directly without a separate guard.
        """

        if content is None:
            return False
        normalized = content.lower()
        return any(pattern in normalized for pattern in cls.PROMPT_INJECTION_PATTERNS)
