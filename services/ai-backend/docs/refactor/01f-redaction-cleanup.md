# Refactor PRD — Cleanup (P11.6)

**Status:** Shipped 2026-05-11
**Parent:** [`01-redaction-subsystem.md`](01-redaction-subsystem.md) §11.6
**Audit reference:** [refactor-audit.md §1.4](../architecture/refactor-audit.md#14-custom-redactor)
**Owner:** Agent runtime team

---

## 1. Problem

After [P11.5](01e-redaction-remove-from-non-log-paths.md), the following abstractions exist but have **zero production callers**:

- `agent_runtime/observability/redaction.py` — the `ObservabilityRedactor` backwards-compat facade. Was the entry point for 14 callsites; all migrated to `JsonObjectCoercer.coerce` in P11.5.
- `Redactor` Protocol in `redactor.py` — declared so the redactor could be swapped at the registry. The registry's only consumer was `ObservabilityRedactor`. Both are unused.
- `RegexRedactor` class in `redactor.py` — the Protocol default impl. Holds the recursive walking + key-scrub + length-clip + user-content carve-out logic. Every production path that used to reach this class now reaches either `JsonObjectCoercer` (for shape coercion) or `_MetadataRedactor` in `logging.py` / `http_logging.py` (for deny-key filtering on log metadata).
- `RedactorRegistry` class — singleton holder + swap mechanism. Nothing left to swap.
- `ValueNormalizer.redact_json_object` and `PersistenceValueNormalizer.redact_json_object` — one-phase backwards-compat aliases the parent PRD said to keep through P11.6. P11.5 migrated their callers; the aliases are dead.

The active redaction surface after P11.6 is exactly:

- `DENY_KEYS` (the canonical credential-key set)
- `_MetadataRedactor` private classes inside `logging.py` and `http_logging.py` (consume `DENY_KEYS`)
- `SafeLogDumper.dump_safe()` (consumes `Sensitive[]` annotations on Pydantic fields, used by `RuntimeLogEvent.to_log_dict()` and `HttpLogEvent.to_log_dict()`)
- `JsonObjectCoercer.coerce()` (shape coercion only, used by all 14 non-log JsonObject field validators)
- `Sensitive` + `SensitiveCategory` (the annotation marker + enum used by `SafeLogDumper`)

That's the whole redaction story.

### What this PRD does

- Delete `agent_runtime/observability/redaction.py` entirely.
- Delete `Redactor` Protocol, `RegexRedactor`, `RedactorRegistry` from `redactor.py`.
- Delete `redact_json_object` from `ValueNormalizer` (validation shim) and `PersistenceValueNormalizer` (persistence facade).
- Update `agent_runtime/observability/__init__.py` exports.
- Delete `tests/unit/agent_runtime/observability/test_redactor_protocol.py` (exercises the gone abstractions).
- Delete `TestObservabilityRedactorUserContent` from `tests/unit/agent_runtime/agent/test_streaming_observability.py` (asserted on length-cap and user-content carve-out behaviors that no longer exist anywhere).
- Trim `tests/unit/agent_runtime/observability/test_deny_keys.py` to a `DENY_KEYS` membership pin only. The deny-key SEMANTIC continues to be tested via the log-event tests in `test_logging.py` / `test_http_logging.py`.

### What this PRD does NOT do

- Touch `DENY_KEYS`, `JsonObjectCoercer`, `SafeLogDumper`, `Sensitive`, `SensitiveCategory`.
- Touch `_MetadataRedactor` in `logging.py` / `http_logging.py`.
- Touch any of the 14 callsites migrated in P11.5.
- Change `Defaults.REDACTED` / `Defaults.TRUNCATED` constants in `observability/constants.py`. They're still used by `_MetadataRedactor`'s log filtering (the `[redacted]` value is what log records carry where a deny-keyed entry was). `UserContentKeys.KEYS` stays for now — see §9.

---

## 2. Goal and non-goals

### Goal

Retire the dead abstractions. The redaction surface shrinks to exactly what the structural model needs: a deny set, a coercer, a tagged-field log dumper.

### Non-goals

- Add new redaction features.
- Rewrite log-event tests.
- Touch the parent PRD's behavioral promises (logs filter deny-keys + elide tagged fields; everywhere else flows whole). All are unchanged.

### Success criteria

1. `agent_runtime/observability/redaction.py` deleted.
2. `redactor.py` no longer defines `Redactor`, `RegexRedactor`, `RedactorRegistry`.
3. `ValueNormalizer.redact_json_object` and `PersistenceValueNormalizer.redact_json_object` deleted.
4. `agent_runtime/observability/__init__.py` exports the live surface only: `DENY_KEYS`, `JsonObjectCoercer`, `SafeLogDumper`, `Sensitive`, `SensitiveCategory`, plus the unchanged `RuntimeLogEvent` / `RuntimeLogger` / tracing exports.
5. `test_redactor_protocol.py` deleted.
6. `TestObservabilityRedactorUserContent` class deleted from `test_streaming_observability.py`.
7. `test_deny_keys.py` trimmed to the `DENY_KEYS` membership pin.
8. Full regression suite green.

---

## 3. Systems touched

### 3.1 Files deleted

| File                                                                                                                                         | Reason                                                                                |
| -------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------- |
| [`agent_runtime/observability/redaction.py`](../../src/agent_runtime/observability/redaction.py)                                             | `ObservabilityRedactor` facade has zero callers after P11.5.                          |
| [`tests/unit/agent_runtime/observability/test_redactor_protocol.py`](../../tests/unit/agent_runtime/observability/test_redactor_protocol.py) | Tests the Protocol / Registry / RegexRedactor abstractions — all deleted by this PRD. |

### 3.2 Files changed

| File                                                                                                                                     | Change                                                                                                                                                                                                                                                           |
| ---------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| [`agent_runtime/observability/redactor.py`](../../src/agent_runtime/observability/redactor.py)                                           | Delete `Redactor` Protocol, `RegexRedactor` class, `RedactorRegistry` class. Keep `DENY_KEYS`, `JsonObjectCoercer`, `SafeLogDumper`, `Sensitive`, `SensitiveCategory`. Trim unused imports (e.g. `Iterable`, `Mapping` — verify still needed for the survivors). |
| [`agent_runtime/observability/__init__.py`](../../src/agent_runtime/observability/__init__.py)                                           | Remove exports of `ObservabilityRedactor`, `Redactor`, `RedactorRegistry`, `RegexRedactor`. Keep everything else.                                                                                                                                                |
| [`agent_runtime/validation.py`](../../src/agent_runtime/validation.py)                                                                   | Delete `ValueNormalizer.redact_json_object` classmethod.                                                                                                                                                                                                         |
| [`agent_runtime/persistence/records/common.py`](../../src/agent_runtime/persistence/records/common.py)                                   | Delete `redact_json_object = _Redactor.redact_json_object` and the `ObservabilityRedactor as _Redactor` import.                                                                                                                                                  |
| [`tests/unit/agent_runtime/agent/test_streaming_observability.py`](../../tests/unit/agent_runtime/agent/test_streaming_observability.py) | Delete `TestObservabilityRedactorUserContent` class (all 7 tests). Remove the `ObservabilityRedactor` import. Keep `TestStreamingAndObservability::test_stream_contracts_validate_payloads_without_redaction`.                                                   |
| [`tests/unit/agent_runtime/observability/test_deny_keys.py`](../../tests/unit/agent_runtime/observability/test_deny_keys.py)             | Trim to `TestDenyKeyMembership` only (which asserts on `DENY_KEYS` directly). Delete the test classes that exercised `ObservabilityRedactor.redact_json_object`. Deny-key SEMANTIC coverage continues via `test_logging.py` + `test_http_logging.py`.            |

### 3.3 Files **not** touched

- Log emitters in `logging.py` / `http_logging.py`. Their `_MetadataRedactor` still filters `DENY_KEYS`. Tests cover behavior.
- `JsonObjectCoercer`, `SafeLogDumper`, `Sensitive`, `SensitiveCategory`, `DENY_KEYS` in `redactor.py`.
- The 14 callsites migrated in P11.5.
- `MemoryRedactor` in `context/memory/contracts.py`. Still consumes `DENY_KEYS` for memory-metadata filtering.

---

## 4. Acceptance criteria

- [x] `redaction.py` deleted.
- [x] `Redactor`, `RegexRedactor`, `RedactorRegistry` removed from `redactor.py`. Trimmed unused imports (`Iterable`, `Protocol`, `runtime_checkable`, `Defaults`, `UserContentKeys`).
- [x] `ValueNormalizer.redact_json_object` deleted.
- [x] `PersistenceValueNormalizer.redact_json_object` deleted; `_Redactor` import removed; `del _V, _Redactor` fixed to `del _V, _Coercer`.
- [x] `observability/__init__.py` exports the live surface only: `DENY_KEYS`, `JsonObjectCoercer`, `SafeLogDumper`, `Sensitive`, `SensitiveCategory`, plus unchanged log / tracer exports. `ObservabilityRedactor`, `Redactor`, `RedactorRegistry`, `RegexRedactor` removed.
- [x] `test_redactor_protocol.py` deleted.
- [x] `TestObservabilityRedactorUserContent` class deleted from `test_streaming_observability.py`; comment block explains where the surviving coverage lives.
- [x] `test_deny_keys.py` trimmed to `TestDenyKeyMembership` (3 tests: exact set match, `frozenset` type, pinned size = 15).
- [x] `RuntimeLogger.exception_metadata` updated to drop the vestigial `ObservabilityRedactor.redact_json_value` call (was a length-clip with stale docstring claims after P11.2). Docstring rewritten to reflect that exception messages flow through to operator-side metadata; tool-emission hygiene is the upstream defense.
- [x] One test updated: `test_invoke_runtime_logs_safe_error_without_raw_exception` — now asserts that `safe_message` is the user-facing safe boundary (unchanged contract) AND that `metadata.exception_message` carries the raw exception (the new operator-side contract).
- [x] Full regression suite green — **1176 tests passing, 0 failures**.

---

## 5. Done definition

- Tests green.
- Parent PRD's phase table marks P11.6 as **Shipped** — completing the P11 sequence.
- This PRD's Status header flipped to **Shipped 2026-05-11**.

---

## 6. The redaction surface after P11.6

For reference — the entire redaction story in one table:

| Layer                                                                                                     | Mechanism                                                                                               |
| --------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------- |
| Log records (`RuntimeLogEvent.metadata`, `HttpLogEvent.metadata`)                                         | `_MetadataRedactor` (private in `logging.py` / `http_logging.py`) drops keys in `DENY_KEYS`.            |
| Log records (typed fields on the log event Pydantic model)                                                | `to_log_dict()` → `SafeLogDumper.dump_safe()` elides any field annotated `Sensitive(...)`.              |
| `ContextCompressionEvent.metadata`                                                                        | `MemoryRedactor.redact_metadata` drops keys in `DENY_KEYS`. (Memory's only metadata-redaction surface.) |
| `ManagedContextPayload.content` / `.preview` in log dumps                                                 | Annotated `Sensitive(SensitiveCategory.MODEL_OUTPUT)`; elided by `SafeLogDumper`.                       |
| `RuntimeEventEnvelope.payload`, `metadata`, runs / conversations metadata, persistence record JSON fields | `JsonObjectCoercer.coerce(value)` — shape only, no redaction. Values pass through whole.                |
| Free-text values anywhere                                                                                 | Not scanned. Sensitivity is a property of the field (tagging) or the key (deny set), never the value.   |

---

## 9. Note on follow-up cleanup (out of scope)

- `Defaults.REDACTED` and `Defaults.TRUNCATED` constants remain in `observability/constants.py`. The `REDACTED` value is what log records carry where a deny-keyed entry was dropped (the `_MetadataRedactor` doesn't currently use the constant — it just drops the entry). Worth auditing whether either constant has remaining consumers; if neither does, a tiny follow-up PR can delete them. Out of scope here.
- `UserContentKeys.KEYS` in `observability/constants.py` — pre-P11.2 this was the carve-out for "don't run the value regex on user content." After P11.2, no value regex exists. After P11.5, no length cap exists. The constant might still have consumers (let's audit at implementation time). If it doesn't, follow-up PR deletes it.
