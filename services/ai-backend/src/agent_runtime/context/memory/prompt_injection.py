"""Heuristic prompt-injection detection for memory writes.

The detector is a closed phrase list. Memory writes that contain any
of these strings (case-insensitive) are flagged as injection attempts
and rejected by ``MemoryPolicyAuthorizer.ensure_authorized``. The list
is deliberately short and exact-phrase — false-positive cost on memory
writes is high (legitimate user content gets rejected) and the
attacker surface here is narrow (the model would have to be convinced
to instruct itself via memory). For broader injection mitigation see
the system-prompt + tool-permission layers; this detector is one cheap
hop in defense-in-depth.

Extracted from ``policy.MemoryWriteGuard`` in P11.4 so the policy
authorizer can stay focused on path-and-actor authorization. The
behavior is byte-identical to the prior inline implementation.
"""

from __future__ import annotations


class PromptInjectionDetector:
    """Memory-write content classifier.

    Single classmethod entry point so memory's policy enforcement can
    call it without instantiating state. The pattern tuple is class-
    scoped so consumers can introspect (`for p in PromptInjectionDetector.PROMPT_INJECTION_PATTERNS`)
    without exporting a separate module-level constant.
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
        """Return ``True`` when ``content`` contains any documented
        prompt-injection phrase (case-insensitive). ``None`` returns
        ``False`` so callers can pass through optional content fields
        without guarding."""

        if content is None:
            return False
        normalized = content.lower()
        return any(pattern in normalized for pattern in cls.PROMPT_INJECTION_PATTERNS)
