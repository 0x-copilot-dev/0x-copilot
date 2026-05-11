# Refactor PRD — Redaction Protocol introduction (P11.1)

**Status:** Shipped 2026-05-11
**Parent:** [`01-redaction-subsystem.md`](01-redaction-subsystem.md) §11.1
**Audit reference:** [refactor-audit.md §1.4](../architecture/refactor-audit.md#14-custom-redactor)
**Owner:** Agent runtime team

---

## 1. Problem

The current redactor surface is the classmethod-only `ObservabilityRedactor` in [`agent_runtime/observability/redaction.py`](../../src/agent_runtime/observability/redaction.py). 19 callsites depend on it directly. There is no Protocol — any future swap (detect-secrets, Presidio, regulator-customized engine) requires touching every callsite.

This sub-PRD introduces a `Redactor` Protocol and reshapes the existing code into a swappable default implementation. **No behavior change. No new dependencies. No callsite migration.** The Protocol exists so future sub-PRDs (P11.2–P11.6) can swap the implementation by mutating a single singleton, not by editing 19 files.

---

## 2. Goal and non-goals

### Goal

Introduce a `Redactor` Protocol that the existing `ObservabilityRedactor` satisfies. Provide a swap mechanism (a singleton + setter) so future engines can replace the default at startup or in tests. Preserve every existing call shape — callsites do not move.

### Non-goals

- Migrate the 19 callsites. They keep calling `ObservabilityRedactor.redact_json_object(...)`. The class becomes a thin facade over the new singleton.
- Change regex behavior, the `_TOKEN_COUNT_KEYS` allowlist, the user-content carve-out, the length clip, or any other functional behavior.
- Introduce new dependencies (`detect-secrets`, `presidio-analyzer`). Those land in P11.2 and P11.3.
- Touch [`context/memory/contracts.py:MemoryRedactor`](../../src/agent_runtime/context/memory/contracts.py) or the patterns duplication. That's P11.4.
- Add the audit-decision logger. That's P11.5.

### Success criteria

1. New file [`agent_runtime/observability/redactor.py`](../../src/agent_runtime/observability/redactor.py) exists with the `Redactor` Protocol, a `RegexRedactor` instance-based implementation, a module-level `default_redactor()` accessor, and a `set_default_redactor(redactor)` setter that returns the prior default.
2. [`agent_runtime/observability/redaction.py`](../../src/agent_runtime/observability/redaction.py) is rewritten as a backwards-compat facade. `ObservabilityRedactor` becomes a classmethod shim that delegates to `default_redactor()`.
3. All 8 existing tests in [`test_streaming_observability.py`](../../tests/unit/agent_runtime/agent/test_streaming_observability.py) pass byte-identical.
4. A new test file [`tests/unit/agent_runtime/observability/test_redactor_protocol.py`](../../tests/unit/agent_runtime/observability/test_redactor_protocol.py) covers Protocol satisfaction, singleton resolution, and swap mechanics.
5. No callsite changes outside `observability/`.
6. `make test` passes; existing `redact_json_object` consumers (events, runs, conversations, persistence records) continue to work.

---

## 3. Systems touched

### 3.1 Files added

| File                                                               | Purpose                                                                                                                                                                                                                                                                            |
| ------------------------------------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `agent_runtime/observability/redactor.py`                          | `Redactor` Protocol + `RegexRedactor` instance-based default impl + `default_redactor()` + `set_default_redactor(redactor)` helpers. The Protocol is `@runtime_checkable` so `isinstance(obj, Redactor)` works for tests. The setter returns the previous default for restoration. |
| `tests/unit/agent_runtime/observability/test_redactor_protocol.py` | Tests: Protocol satisfaction; default redactor is a `RegexRedactor` at import; `set_default_redactor` swaps; calling `ObservabilityRedactor.redact_json_object` after a swap reaches the new impl; swap is restorable.                                                             |

### 3.2 Files changed

| File                                                                                             | Change                                                                                                                                                                                                                                                                                                                                                                      |
| ------------------------------------------------------------------------------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| [`agent_runtime/observability/redaction.py`](../../src/agent_runtime/observability/redaction.py) | The 100-line classmethod implementation moves into `RegexRedactor` (instance methods) inside the new `redactor.py`. This file becomes a ~30-line backwards-compat surface: `ObservabilityRedactor` is a classmethod shim where each method delegates to `default_redactor().redact_*(...)`. `_TOKEN_COUNT_KEYS` moves into `redactor.py` next to the `RegexRedactor` class. |
| [`agent_runtime/observability/__init__.py`](../../src/agent_runtime/observability/__init__.py)   | Add `Redactor`, `RegexRedactor`, `default_redactor`, `set_default_redactor` to the public export. Keep `ObservabilityRedactor` exported as before.                                                                                                                                                                                                                          |

### 3.3 Files **not** touched

- The 19 callsites enumerated in the parent PRD §5.1, §5.2, §5.3. They keep working unchanged.
- [`context/memory/`](../../src/agent_runtime/context/memory/) anything. P11.4 owns memory consolidation.
- Pattern files [`observability/constants.py`](../../src/agent_runtime/observability/constants.py) and [`context/memory/constants.py`](../../src/agent_runtime/context/memory/constants.py). P11.4 owns the merge.
- [`observability/logging.py`](../../src/agent_runtime/observability/logging.py) and [`observability/http_logging.py`](../../src/agent_runtime/observability/http_logging.py). Their inline `_MetadataRedactor` helpers stay; consolidation is part of P11.4 + [`01-otel-adoption.md`](01-otel-adoption.md).

---

## 4. Design

### 4.1 Protocol shape

The Protocol mirrors the existing classmethod surface of `ObservabilityRedactor`. Both methods take the same kwargs and return the same shapes. `Protocol` defines instance methods; `RegexRedactor` provides them.

```python
@runtime_checkable
class Redactor(Protocol):
    def redact_json_object(
        self,
        value: object,
        *,
        max_string_length: int | None = ...,
        user_content: bool = ...,
    ) -> dict[str, object]: ...

    def redact_json_value(
        self,
        value: object,
        *,
        max_string_length: int | None = ...,
        user_content: bool = ...,
    ) -> object: ...
```

### 4.2 Default singleton + swap

```python
_DEFAULT: Redactor = RegexRedactor()

def default_redactor() -> Redactor:
    return _DEFAULT

def set_default_redactor(redactor: Redactor) -> Redactor:
    """Swap the process-wide default. Returns the prior default so callers can restore."""
    global _DEFAULT
    previous = _DEFAULT
    _DEFAULT = redactor
    return previous
```

The singleton lives in `redactor.py` module scope. No DI container; tests use `set_default_redactor(fake)` in a fixture and restore in teardown.

### 4.3 Backwards-compat shim

`ObservabilityRedactor` stays in `redaction.py` but its body becomes one-line delegations:

```python
class ObservabilityRedactor:
    """Backwards-compat surface. Delegates to ``default_redactor()``.

    New code should call ``default_redactor().redact_json_object(...)``
    directly. This class is preserved so existing imports keep working
    until P11.6 deletes it.
    """

    @classmethod
    def redact_json_object(
        cls,
        value: object,
        *,
        max_string_length: int | None = Defaults.MAX_STREAM_FIELD_LENGTH,
        user_content: bool = False,
    ) -> dict[str, object]:
        return default_redactor().redact_json_object(
            value,
            max_string_length=max_string_length,
            user_content=user_content,
        )

    @classmethod
    def redact_json_value(
        cls,
        value: object,
        *,
        max_string_length: int | None = Defaults.MAX_STREAM_FIELD_LENGTH,
        user_content: bool = False,
    ) -> object:
        return default_redactor().redact_json_value(
            value,
            max_string_length=max_string_length,
            user_content=user_content,
        )
```

This is byte-identical behavior for callsites because the underlying logic in `RegexRedactor` is the verbatim move of the current classmethods.

### 4.4 Why not migrate callsites in P11.1

Three reasons:

1. **Minimum-diff principle.** Substitution is achievable via the singleton without touching 19 files. The Protocol is the contract; the singleton is the swap point.
2. **Reversibility.** If P11.2 / P11.3 reveal that the library backends miss a case, we can flip the singleton back to `RegexRedactor` instantly. Touching 19 callsites would lock us in.
3. **Callsite migration belongs in P11.6.** Once the library backends are stable, the cleanup PR removes the `ObservabilityRedactor` facade and switches callsites to import `default_redactor` directly. Same change, better timing.

---

## 5. Behaviors preserved

Each is a pinned test before merge.

| Behavior                                                                                   | How preserved                                                                              |
| ------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------ |
| Recursive walking semantics (None → {}, non-mapping → {"value": …}, mapping → recurse)     | Logic moves verbatim from `ObservabilityRedactor` classmethods to `RegexRedactor` methods. |
| `SENSITIVE_KEY` structural scrub (replace value with `Defaults.REDACTED`)                  | Same method body.                                                                          |
| `_TOKEN_COUNT_KEYS` allowlist (10 keys bypass scrub)                                       | Constant moves alongside `RegexRedactor`.                                                  |
| `UserContentKeys` carve-out (drops length cap, skips value-pattern check on string leaves) | Same method body.                                                                          |
| Sticky propagation (user_content flag carries through nested structures)                   | Same method body.                                                                          |
| Length clip outside user-content (`value[:max] + TRUNCATED`)                               | Same method body.                                                                          |
| `SENSITIVE_VALUE` regex check outside user-content                                         | Same method body.                                                                          |
| Pydantic field-validator integration (no async, no IO, runs on hot path)                   | Singleton resolution is `O(1)` global-variable read; no overhead.                          |
| 8 existing tests in `test_streaming_observability.py` pass unchanged                       | They call `ObservabilityRedactor.redact_json_object(...)` which delegates to the default.  |
| All 19 callsites continue to work without modification                                     | They still import and call `ObservabilityRedactor`.                                        |

---

## 6. Risks and mitigations

| Risk                                                                                    | Likelihood | Impact | Mitigation                                                                                                                                                                                                 |
| --------------------------------------------------------------------------------------- | ---------- | ------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Singleton mutability creates flaky tests when one test swaps and forgets to restore     | Medium     | Medium | `set_default_redactor` returns the prior default. New test file provides a `swap_redactor` context manager / pytest fixture for safe scoped swaps.                                                         |
| Module import order — `redactor.py` and `redaction.py` import each other                | Low        | Medium | `redaction.py` imports `default_redactor` from `redactor.py` (one direction only). `redactor.py` does not import from `redaction.py`. Verified by import-cycle test (a Python-level `import` smoke check). |
| Hot-path overhead — extra function call per redaction                                   | Low        | Low    | One module-global lookup + one classmethod dispatch. Effectively free at Python call cost. Benchmark optional but not required to merge.                                                                   |
| A new contributor calls `default_redactor()` to get a _copy_ of the default             | Low        | Low    | Docstring explicitly states it returns the singleton; not a factory.                                                                                                                                       |
| Tests that monkeypatch `ObservabilityRedactor.redact_json_object` directly stop working | Low        | Medium | Grep for `monkeypatch.*ObservabilityRedactor` and `mock.patch.*ObservabilityRedactor` in tests; update to use `set_default_redactor` instead. If none found, no migration needed.                          |

---

## 7. Test requirements

### 7.1 New tests (`test_redactor_protocol.py`)

- `test_default_is_regex_redactor` — at import, `default_redactor()` returns a `RegexRedactor` instance.
- `test_regex_redactor_satisfies_protocol` — `isinstance(default_redactor(), Redactor)` is `True`.
- `test_set_default_redactor_returns_previous` — swap installs a fake and the return value is the original.
- `test_observability_redactor_delegates_to_default` — after `set_default_redactor(fake)`, calling `ObservabilityRedactor.redact_json_object(value)` reaches `fake`.
- `test_swap_is_restorable` — round-trip swap returns the system to the original default.
- `test_fake_redactor_satisfies_protocol` — a hand-rolled fake with the two methods passes `isinstance(..., Redactor)`.

### 7.2 Regression tests (must pass unchanged)

- All 8 tests in [`tests/unit/agent_runtime/agent/test_streaming_observability.py`](../../tests/unit/agent_runtime/agent/test_streaming_observability.py).
- Tests that import or use `ObservabilityRedactor` indirectly (events, runs, conversations, persistence records). `make test` should be green.

### 7.3 Out of scope (deferred to P11.2 / P11.3)

- Property-based tests asserting redactor behavior on a generated corpus.
- Performance benchmarks.
- Library-backed engine tests.

---

## 8. Rollout / rollback

### 8.1 Rollout

Single PR. No feature flag. Behavior is byte-identical so there's nothing to gate.

### 8.2 Rollback

Revert the PR. The Protocol file is new; `redaction.py` reverts to its current classmethod implementation.

---

## 9. Acceptance criteria

- [x] DRY investigation complete (see parent PRD's DRY note).
- [x] [`agent_runtime/observability/redactor.py`](../../src/agent_runtime/observability/redactor.py) exists with `Redactor` Protocol, `RegexRedactor` instance impl, `_TOKEN_COUNT_KEYS`, and `RedactorRegistry` (`default()`, `set_default()`, `reset_for_tests()`). Codebase style preference for class-scoped helpers (per [`services/ai-backend/CLAUDE.md`](../../CLAUDE.md) — "keep production helper behavior **inside** classes") drove the registry shape; the parent PRD's `default_redactor()` / `set_default_redactor()` free-function naming is satisfied via `RedactorRegistry.default()` / `RedactorRegistry.set_default()`.
- [x] [`agent_runtime/observability/redaction.py`](../../src/agent_runtime/observability/redaction.py) rewritten as a 60-line backwards-compat facade.
- [x] [`agent_runtime/observability/__init__.py`](../../src/agent_runtime/observability/__init__.py) re-exports `Redactor`, `RegexRedactor`, `RedactorRegistry` alongside the existing `ObservabilityRedactor`.
- [x] [`tests/unit/agent_runtime/observability/test_redactor_protocol.py`](../../tests/unit/agent_runtime/observability/test_redactor_protocol.py) exists with **7 tests** — the six from §7.1 plus `test_protocol_rejects_partial_implementation` to lock the contract from the negative side.
- [x] All 8 redactor regression tests in [`test_streaming_observability.py`](../../tests/unit/agent_runtime/agent/test_streaming_observability.py) pass byte-identical.
- [x] Broader smoke check (agent_runtime + runtime_api + runtime_adapters unit suites) passes — **1085 tests, 0 failures**.
- [x] No import cycle. `redaction.py` imports `RedactorRegistry` from `redactor.py`; `redactor.py` does not import from `redaction.py`.

---

## 10. Done definition

- Tests in §9 are green.
- `git diff` shows: 1 new file in `src/observability/`, 1 file rewritten in `src/observability/`, 1 new test file. No other production-code changes.
- Parent PRD updated to mark P11.1 row as `Shipped`.
