# Refactor PRD — Pattern consolidation (P11.4)

**Status:** Shipped 2026-05-11
**Parent:** [`01-redaction-subsystem.md`](01-redaction-subsystem.md) §11.4
**Audit reference:** [refactor-audit.md §1.4](../architecture/refactor-audit.md#14-custom-redactor)
**Owner:** Agent runtime team

---

## 1. Problem

The DRY investigation in the parent PRD found three suspected duplications and resolved them:

1. **Memory's `SENSITIVE_KEY` drops `credential`** — accidental drift; same domain as observability's redaction.
2. **`MemoryRedactor` has one consumer**, same job as `ObservabilityRedactor` but flat-dict scope.
3. **Two log schemas** — different contracts, redactors duplicated.

P11.2 already did most of the consolidation work as a side-effect of switching to the exact-match deny set:

- `MemoryRedactor` now imports `DENY_KEYS` from `agent_runtime.observability.redactor` (one source of truth for credential-shaped keys).
- `policy.MemoryWriteGuard.is_prompt_injection` no longer references `Patterns.SENSITIVE_VALUE` (the accidental coupling between prompt-injection detection and credential scrubbing is broken).

What's left for P11.4:

1. **Dead code in [`context/memory/constants.py`](../../src/agent_runtime/context/memory/constants.py).** `Patterns.SENSITIVE_KEY` and `Patterns.SENSITIVE_VALUE` are still declared but no longer referenced anywhere. They need to be deleted.
2. **Prompt-injection patterns are inline in [`policy.py`](../../src/agent_runtime/context/memory/policy.py).** `MemoryWriteGuard.PROMPT_INJECTION_PATTERNS` is a 5-tuple of literal phrase strings that lives mid-file alongside the policy authorizer. It belongs in its own module so the policy authorizer can stay focused on path-and-actor authorization, not phrase matching.

### What this PRD does

- Delete `Patterns.SENSITIVE_KEY` and `Patterns.SENSITIVE_VALUE` from memory's constants. Keep `Patterns.ID`, `MEMORY_PATH`, `NAMESPACE_SEGMENT`, `PATH_PREFIX` — those are memory-path structural validators, not redaction concerns.
- Extract `PROMPT_INJECTION_PATTERNS` and the `is_prompt_injection` classifier into a new `agent_runtime/context/memory/prompt_injection.py` module.
- Update the one caller (`MemoryPolicyAuthorizer.ensure_authorized`) to call the new detector directly. Delete `MemoryWriteGuard.is_prompt_injection` and `PROMPT_INJECTION_PATTERNS` from `policy.py`. `MemoryWriteGuard` itself can stay (or be removed if empty after the move).

### What this PRD does NOT do

- Touch `DENY_KEYS` (already canonical in `observability/redactor.py` since P11.2).
- Touch `MemoryRedactor` (already wired to the consolidated deny set since P11.2).
- Expand the prompt-injection phrase list. The current 5 phrases stay; adding/tuning is a separate compliance concern.
- Replace phrase-matching with anything more sophisticated. Phrase matching is deliberate (deterministic, auditable, no false positives on prose that mentions instructions in passing).
- Remove `redact_json_object` from non-log paths — that's [P11.5](01e-redaction-remove-from-non-log-paths.md).

---

## 2. Goal and non-goals

### Goal

Eliminate the last two stragglers from the cross-module pattern-and-phrase tangle. Memory's `constants.py` carries only memory-path validators; prompt-injection detection has its own home.

### Non-goals

- Touch the existing test for `test_prompt_injection_memory_write_is_rejected`. The behavior is preserved end-to-end — the test continues to pass through `MemoryPolicyAuthorizer.ensure_authorized`.
- Add new prompt-injection signals (regex, LLM-based classification, etc.). Phrase matching today; richer detection if a future PRD scopes it.
- Reorganize the broader `context/memory/` package. P11.4 is two file edits plus one new module.

### Success criteria

1. `Patterns.SENSITIVE_KEY` and `Patterns.SENSITIVE_VALUE` are removed from [`context/memory/constants.py`](../../src/agent_runtime/context/memory/constants.py). The remaining `Patterns` class keeps `ID`, `MEMORY_PATH`, `NAMESPACE_SEGMENT`, `PATH_PREFIX`.
2. New file `agent_runtime/context/memory/prompt_injection.py` exports `PromptInjectionDetector` with `is_prompt_injection(content: str | None) -> bool` and `PROMPT_INJECTION_PATTERNS` as a class-scoped tuple constant.
3. `MemoryWriteGuard.is_prompt_injection` and `MemoryWriteGuard.PROMPT_INJECTION_PATTERNS` are deleted from [`policy.py`](../../src/agent_runtime/context/memory/policy.py). If `MemoryWriteGuard` is then empty, the class is also deleted; otherwise the remainder stays.
4. `MemoryPolicyAuthorizer.ensure_authorized` calls `PromptInjectionDetector.is_prompt_injection(content)` directly. Import added accordingly.
5. New test file `tests/unit/agent_runtime/context/memory/test_prompt_injection.py` covers the detector: each phrase matches, non-injection content doesn't, `None` content returns `False`, case-insensitive (`.lower()` normalization).
6. Existing `test_prompt_injection_memory_write_is_rejected` continues to pass — end-to-end behavior preserved.
7. Full regression suite green.

---

## 3. Systems touched

### 3.1 Files added

| File                                                               | Purpose                                                                                                                                          |
| ------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------ |
| `agent_runtime/context/memory/prompt_injection.py`                 | `PromptInjectionDetector` class with `PROMPT_INJECTION_PATTERNS` constant and `is_prompt_injection(content)` classmethod. ~30 lines.             |
| `tests/unit/agent_runtime/context/memory/test_prompt_injection.py` | Detector behavior tests: each phrase matches, distractor strings don't, `None` returns `False`, case normalization works. (~6 tests, ~50 lines.) |

### 3.2 Files changed

| File                                                                                               | Change                                                                                                                                                                                                                                                                           |
| -------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| [`agent_runtime/context/memory/constants.py`](../../src/agent_runtime/context/memory/constants.py) | Delete `Patterns.SENSITIVE_KEY` and `Patterns.SENSITIVE_VALUE` (and the leading `import re` if no other regex remains — `ID`, `MEMORY_PATH`, etc. still use `re`, so the import stays).                                                                                          |
| [`agent_runtime/context/memory/policy.py`](../../src/agent_runtime/context/memory/policy.py)       | Delete `MemoryWriteGuard.is_prompt_injection` and `PROMPT_INJECTION_PATTERNS`. If `MemoryWriteGuard` is now empty, delete the class. Update the one caller (`MemoryPolicyAuthorizer.ensure_authorized`) to call `PromptInjectionDetector.is_prompt_injection(content)` directly. |

### 3.3 Files **not** touched

- [`context/memory/contracts.py`](../../src/agent_runtime/context/memory/contracts.py) — `MemoryRedactor` already uses `DENY_KEYS` from observability since P11.2; no change here.
- `MemoryPathAuthorizer` and other content of `policy.py` — not touched.
- The single existing test `test_prompt_injection_memory_write_is_rejected` — its end-to-end path (`MemoryPolicyAuthorizer.ensure_authorized` raising on injection content) is preserved.

---

## 4. Design

### 4.1 `PromptInjectionDetector`

```python
# agent_runtime/context/memory/prompt_injection.py

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
"""

from __future__ import annotations


class PromptInjectionDetector:
    """Memory-write content classifier."""

    PROMPT_INJECTION_PATTERNS: tuple[str, ...] = (
        "ignore previous instructions",
        "ignore all previous instructions",
        "reveal the system prompt",
        "developer message",
        "system message",
    )

    @classmethod
    def is_prompt_injection(cls, content: str | None) -> bool:
        if content is None:
            return False
        normalized = content.lower()
        return any(
            pattern in normalized for pattern in cls.PROMPT_INJECTION_PATTERNS
        )
```

Verbatim move of the existing logic out of `policy.py`. No behavior change. No tests change semantics — the existing end-to-end test still hits the same code path.

### 4.2 Caller update in `policy.py`

Before:

```python
class MemoryPolicyAuthorizer:
    @classmethod
    def ensure_authorized(cls, ...):
        # ...
        if (
            operation is MemoryAccessOperation.WRITE
            and content is not None
            and MemoryWriteGuard.is_prompt_injection(content)
        ):
            raise AgentRuntimeError(...)
```

After:

```python
from agent_runtime.context.memory.prompt_injection import PromptInjectionDetector

class MemoryPolicyAuthorizer:
    @classmethod
    def ensure_authorized(cls, ...):
        # ...
        if (
            operation is MemoryAccessOperation.WRITE
            and content is not None
            and PromptInjectionDetector.is_prompt_injection(content)
        ):
            raise AgentRuntimeError(...)
```

`MemoryWriteGuard` then has nothing left (the class was only `PROMPT_INJECTION_PATTERNS` + `is_prompt_injection`) — delete it.

### 4.3 What stays in `context/memory/constants.py`

`Patterns` keeps these four regexes:

```python
class Patterns:
    ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")
    MEMORY_PATH = re.compile(r"^/[A-Za-z0-9._:/-]+$")
    NAMESPACE_SEGMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")
    PATH_PREFIX = re.compile(r"^/[A-Za-z0-9._-]+/$")
```

These are memory-path structural validators — they validate IDs/paths conform to a format. They're orthogonal to redaction and stay where they are.

---

## 5. Behaviors preserved

| Behavior                                                                                                     | After P11.4                                                                              |
| ------------------------------------------------------------------------------------------------------------ | ---------------------------------------------------------------------------------------- |
| `MemoryPolicyAuthorizer.ensure_authorized` rejects memory writes containing `"ignore previous instructions"` | Yes — call chains through `PromptInjectionDetector.is_prompt_injection` now.             |
| Each of the 5 phrases matches (case-insensitive)                                                             | Yes — verbatim move.                                                                     |
| Non-injection content (`"Set my preferences to dark mode"`) passes                                           | Yes — same matching logic.                                                               |
| `None` content returns `False`                                                                               | Yes — `if content is None: return False` preserved.                                      |
| `Patterns.ID`, `Patterns.MEMORY_PATH`, `Patterns.NAMESPACE_SEGMENT`, `Patterns.PATH_PREFIX`                  | Unchanged. Memory-path validation continues to use them.                                 |
| `MemoryRedactor.redact_metadata` deny-key behavior                                                           | Unchanged. Still imports `DENY_KEYS` from `observability/redactor.py` (set up in P11.2). |

---

## 6. Risks and mitigations

| Risk                                                                                             | Likelihood | Impact | Mitigation                                                                                                                                                                                                                                           |
| ------------------------------------------------------------------------------------------------ | ---------- | ------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| External consumer imports `MemoryWriteGuard` directly                                            | Low        | Low    | Grep before deletion. The class is internal to memory policy enforcement; no public API contract documents it. If any external import is found (none expected), leave `MemoryWriteGuard` as a thin shim that delegates to `PromptInjectionDetector`. |
| Test for prompt-injection rejection (`test_prompt_injection_memory_write_is_rejected`) regresses | Low        | Medium | End-to-end path is unchanged. The test calls `MemoryPolicyAuthorizer.ensure_authorized` which still rejects; the only internal change is which class implements `is_prompt_injection`. CI smoke proves this.                                         |
| `Patterns.SENSITIVE_KEY` / `_VALUE` deletion breaks code I missed                                | Low        | Medium | Grep confirmed: zero references in `src/` after P11.2. CI confirms: 1181 tests green at end of P11.3 without these patterns being used.                                                                                                              |
| `MemoryWriteGuard` deletion accidentally removes a method I missed                               | Low        | Low    | Re-read `policy.py` end-to-end before deletion to confirm `MemoryWriteGuard` truly only carries the two attributes.                                                                                                                                  |

---

## 7. Test requirements

### 7.1 New tests (`test_prompt_injection.py`)

- `test_each_documented_phrase_matches` — each of the 5 phrases in `PROMPT_INJECTION_PATTERNS` matches.
- `test_case_insensitive_match` — `"IGNORE PREVIOUS INSTRUCTIONS"` matches.
- `test_non_injection_content_passes` — typical user content (e.g. "Set my preferences", "What did Sarah say in the meeting?") does not match.
- `test_none_content_returns_false` — `is_prompt_injection(None) is False`.
- `test_empty_string_returns_false` — `is_prompt_injection("") is False`.
- `test_pattern_constant_is_tuple` — `PROMPT_INJECTION_PATTERNS` is a tuple, not a list (immutability).

### 7.2 Tests preserved

- `test_prompt_injection_memory_write_is_rejected` (existing) — must continue to pass without modification.
- All other tests in `tests/unit/agent_runtime/memory/` — must continue to pass.

### 7.3 Regression suite

- Full `agent_runtime/`, `runtime_api/`, `runtime_adapters/` unit suites green.

---

## 8. Rollout / rollback

### 8.1 Rollout

Single PR. No feature flag. Pure refactor.

### 8.2 Rollback

Revert. The new module disappears; the old inline class returns.

---

## 9. Acceptance criteria

- [x] `Patterns.SENSITIVE_KEY` and `Patterns.SENSITIVE_VALUE` removed from [`context/memory/constants.py`](../../src/agent_runtime/context/memory/constants.py). The `Patterns` class keeps `ID`, `MEMORY_PATH`, `NAMESPACE_SEGMENT`, `PATH_PREFIX` for memory-path validation.
- [x] New file [`agent_runtime/context/memory/prompt_injection.py`](../../src/agent_runtime/context/memory/prompt_injection.py) exports `PromptInjectionDetector`.
- [x] `MemoryWriteGuard` deleted — only its two members existed and both moved.
- [x] `MemoryPolicyAuthorizer.authorize` calls `PromptInjectionDetector.is_prompt_injection(...)` directly.
- [x] New test file [`test_prompt_injection.py`](../../tests/unit/agent_runtime/memory/test_prompt_injection.py) exists with **8 tests** (the 6 listed in §7.1 plus a substring-match-in-longer-text check and a phrase-count pin).
- [x] Existing `test_prompt_injection_memory_write_is_rejected` (end-to-end through `MemoryPolicyAuthorizer.ensure_authorized`) passes unchanged.
- [x] Full regression suite green — **1189 tests passing, 0 failures**.

---

## 10. Done definition

- Tests in §9 green.
- Parent PRD's phase table marks P11.4 as **Shipped**.
- This PRD's Status header flipped to **Shipped 2026-05-11**.
