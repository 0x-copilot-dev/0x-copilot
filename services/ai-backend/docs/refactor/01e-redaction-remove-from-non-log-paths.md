# Refactor PRD — Remove redaction from non-log paths (P11.5)

**Status:** Shipped 2026-05-11
**Parent:** [`01-redaction-subsystem.md`](01-redaction-subsystem.md) §11.5
**Audit reference:** [refactor-audit.md §1.4](../architecture/refactor-audit.md#14-custom-redactor)
**Owner:** Agent runtime team

---

## 1. Problem

Today `redact_json_object` runs as a Pydantic `mode="before"` field validator on **19 callsites** across SSE schemas, runtime contracts, runs / conversations payloads, and 6 persistence records. The redactor does two things at every one of those callsites:

1. **Structural coercion.** Coerces `None → {}`, non-mapping → `{"value": value}`, mapping → dict.
2. **Deny-key scrubbing + length clipping.** Scrubs values under keys in the deny set and clips strings > 2 000 chars (outside user-content keys).

The parent PRD's structural direction (§8) says **logs are the only redaction surface**. Sensitive data flows through SSE / persistence / runtime context whole; only log records elide it. So step 2 above is wrong at these 19 callsites — it scrubs data that shouldn't be scrubbed.

After [P11.1](01a-redaction-protocol.md), [P11.2](01b-redaction-exact-match-deny-keys.md), [P11.3](01c-redaction-field-tagging.md), and [P11.4](01d-redaction-pattern-consolidation.md), the supporting infrastructure is in place:

- `DENY_KEYS` lives in `observability/redactor.py` and is consumed by log emitters only (`_MetadataRedactor` in `logging.py` / `http_logging.py`).
- `Sensitive[]` field tagging exists; `SafeLogDumper` elides tagged fields at log emission.
- `ManagedContextPayload.content` is the first real-world tag, proving the round trip works.

P11.5 strips the redactor call from the 19 non-log callsites. The structural coercion (None / non-mapping / dict shape) stays — Pydantic constructors and existing test fixtures depend on it. The redaction part disappears.

### What this PRD does

- Introduce a small `JsonObjectCoercer` helper class in `observability/redactor.py` that does coercion only: `None → {}`, non-mapping → `{"value": value}`, mapping → `dict(value)`. No redaction, no scanning, no clipping.
- Add `coerce_json_object` classmethods to `ValueNormalizer` (validation shim) and `PersistenceValueNormalizer` (persistence facade) that route through `JsonObjectCoercer`.
- Switch all 19 callsites from `redact_json_object` to `coerce_json_object`. Mechanical find-replace per file.
- Update tests that asserted on `[redacted]` placeholders in SSE / persistence paths to assert on the raw value passing through.

### What this PRD does NOT do

- Touch log redaction. `_MetadataRedactor` in `logging.py` and `http_logging.py` still drops deny-keyed entries from `RuntimeLogEvent.metadata` and `HttpLogEvent.metadata`. `SafeLogDumper` still elides tagged Pydantic fields.
- Touch field tagging. The `Sensitive[]` annotations from P11.3 keep working; `SafeLogDumper` is unchanged.
- Delete `ObservabilityRedactor` or `RegexRedactor`. Those still serve the log emitters via the registry. Cleanup is [P11.6](01f-redaction-cleanup.md).
- Keep length clipping (the >2 000 char truncation). Removing it is a deliberate design call — see §4.4.
- Migrate to a `PayloadSizeLimiter`. If post-deploy data shows oversized rows are a real problem, a focused PRD adds size limits where needed.

---

## 2. Goal and non-goals

### Goal

Make sensitive data (LLM replies, user input, tool output, approval payloads) flow whole through every layer except logs. The 19 callsites that today scrub via `redact_json_object` switch to structural coercion only.

### Non-goals

- Add new redaction. Logs already have it; nothing else needs it.
- Add a configurable redaction policy. Categories on `Sensitive[]` exist for future per-buyer policy; no policy machinery in P11.5.
- Reduce the scope of what `_MetadataRedactor` filters in logs. Logs continue to drop deny-keyed entries.
- Touch the `Redactor` Protocol or the `RedactorRegistry`.
- Migrate the 19 callsites to import a different module. The names `ObservabilityRedactor` and `PersistenceValueNormalizer` keep their classes; only the method called on them changes.

### Success criteria

1. `JsonObjectCoercer` exists in [`observability/redactor.py`](../../src/agent_runtime/observability/redactor.py) with one classmethod `coerce(value: object) -> dict[str, Any]`.
2. [`ValueNormalizer`](../../src/agent_runtime/validation.py) has a new `coerce_json_object` classmethod that delegates to `JsonObjectCoercer.coerce`. The existing `redact_json_object` method stays for the log path (or is delegated to alongside, depending on whether log emitters use the normalizer — they don't today).
3. [`PersistenceValueNormalizer`](../../src/agent_runtime/persistence/records/common.py) has a new `coerce_json_object` classmethod.
4. All **19 non-log callsites** call `coerce_json_object` instead of `redact_json_object`:
   - [`runtime_api/schemas/events.py:977`](../../src/runtime_api/schemas/events.py) (`payload` + `metadata` on `RuntimeEventEnvelope`)
   - [`runtime_api/schemas/runs.py:172`](../../src/runtime_api/schemas/runs.py) (one validator covering ≥1 field)
   - [`runtime_api/schemas/runs.py:242`](../../src/runtime_api/schemas/runs.py) (`request_options`)
   - [`runtime_api/schemas/conversations.py:77`](../../src/runtime_api/schemas/conversations.py)
   - [`runtime_api/schemas/conversations.py:419`](../../src/runtime_api/schemas/conversations.py)
   - [`agent_runtime/execution/contracts.py:218`](../../src/agent_runtime/execution/contracts.py)
   - [`agent_runtime/execution/contracts.py:468`](../../src/agent_runtime/execution/contracts.py)
   - [`agent_runtime/execution/contracts.py:623`](../../src/agent_runtime/execution/contracts.py)
   - [`agent_runtime/persistence/records/approvals.py`](../../src/agent_runtime/persistence/records/approvals.py)
   - [`agent_runtime/persistence/records/tools.py`](../../src/agent_runtime/persistence/records/tools.py)
   - [`agent_runtime/persistence/records/memory.py`](../../src/agent_runtime/persistence/records/memory.py)
   - [`agent_runtime/persistence/records/audit.py`](../../src/agent_runtime/persistence/records/audit.py)
   - [`agent_runtime/persistence/records/checkpoints.py`](../../src/agent_runtime/persistence/records/checkpoints.py)
   - [`agent_runtime/persistence/records/outbox.py`](../../src/agent_runtime/persistence/records/outbox.py)
5. `_MetadataRedactor` in [`logging.py`](../../src/agent_runtime/observability/logging.py) and [`http_logging.py`](../../src/agent_runtime/observability/http_logging.py) continue to filter `DENY_KEYS` — unchanged.
6. Tests that asserted on `[redacted]` placeholders in SSE / persistence paths are updated to assert on raw values passing through. Tests asserting on log redaction continue to pass.
7. Full regression suite green.

---

## 3. Systems touched

### 3.1 Files added

| File                                                                 | Purpose                                                                                                                       |
| -------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------- |
| `tests/unit/agent_runtime/observability/test_json_object_coercer.py` | Pins coercion behavior: `None → {}`, non-mapping → `{"value": value}`, mapping → `dict(value)`. No redaction. No length clip. |

### 3.2 Files changed

| File                                                                                                           | Change                                                                                                                                                                                                                                                                           |
| -------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| [`agent_runtime/observability/redactor.py`](../../src/agent_runtime/observability/redactor.py)                 | Add `JsonObjectCoercer` class with `coerce(value)` classmethod.                                                                                                                                                                                                                  |
| [`agent_runtime/observability/__init__.py`](../../src/agent_runtime/observability/__init__.py)                 | Re-export `JsonObjectCoercer`.                                                                                                                                                                                                                                                   |
| [`agent_runtime/validation.py`](../../src/agent_runtime/validation.py)                                         | Add `ValueNormalizer.coerce_json_object` classmethod. Existing `redact_json_object` stays (currently delegates to `ObservabilityRedactor.redact_json_object` — used by no remaining production callers after P11.5, kept for one phase per the parent PRD's P11.6 cleanup plan). |
| [`agent_runtime/persistence/records/common.py`](../../src/agent_runtime/persistence/records/common.py)         | Add `PersistenceValueNormalizer.coerce_json_object = JsonObjectCoercer.coerce`. Keep `redact_json_object` alias for backwards compat through P11.6.                                                                                                                              |
| 8 schema / contract files (events, runs × 2 validators, conversations × 2 validators, execution/contracts × 3) | Validator bodies switch from `ObservabilityRedactor.redact_json_object(value)` to `JsonObjectCoercer.coerce(value)`. Imports updated.                                                                                                                                            |
| 6 persistence records (approvals, tools, memory, audit, checkpoints, outbox)                                   | Validator bodies switch from `PersistenceValueNormalizer.redact_json_object(value)` to `PersistenceValueNormalizer.coerce_json_object(value)`.                                                                                                                                   |

### 3.3 Files **not** touched

- [`agent_runtime/observability/logging.py`](../../src/agent_runtime/observability/logging.py) — `_MetadataRedactor` still filters via `DENY_KEYS`. Untouched.
- [`agent_runtime/observability/http_logging.py`](../../src/agent_runtime/observability/http_logging.py) — same.
- `ObservabilityRedactor`, `RegexRedactor`, `RedactorRegistry`, `SafeLogDumper`, `Sensitive`, `SensitiveCategory` — all stay as they are. P11.6 will retire the `ObservabilityRedactor` backwards-compat facade.

---

## 4. Design

### 4.1 `JsonObjectCoercer`

```python
class JsonObjectCoercer:
    """Pydantic field-validator helper that coerces values into the
    ``dict`` shape Pydantic ``JsonObject`` fields expect, without
    performing redaction.

    The pre-P11.5 ``ObservabilityRedactor.redact_json_object`` validator
    did two unrelated jobs: structural coercion (None / non-mapping →
    dict-like) and credential redaction (deny-key scrubbing, length
    clipping). P11.5 separated them. Logs keep the deny-key filter at
    their own validation boundary; everywhere else, only coercion runs.

    Behavior:

        None         → {}
        non-mapping  → {"value": value}
        mapping      → dict(value)

    No recursion. No value scanning. No deny-key scrubbing. No length
    clipping. The value flows through whole and is the caller's
    responsibility to handle from here.
    """

    @classmethod
    def coerce(cls, value: object) -> dict[str, Any]:
        if value is None:
            return {}
        if not isinstance(value, Mapping):
            return {"value": value}
        return dict(value)
```

### 4.2 Coercion vs redaction semantics — explicit

Before P11.5, given `{"password": "hunter2", "ok": "fine"}`:

```python
ObservabilityRedactor.redact_json_object(value)
# → {"password": "[redacted]", "ok": "fine"}
```

After P11.5, the same input flowing into SSE / persistence / runtime context:

```python
JsonObjectCoercer.coerce(value)
# → {"password": "hunter2", "ok": "fine"}
```

Logs are unaffected — they continue to drop `password`:

```python
event = RuntimeLogEvent(..., metadata={"password": "hunter2", "ok": "fine"})
event.metadata
# → {"ok": "fine"}    # _MetadataRedactor filtered "password"
```

### 4.3 Why coerce instead of deleting the validator entirely

Pydantic validators that previously coerced None → {} and wrapped non-mapping values were doing real structural work that many callsites depend on. Two examples found in code review:

- `RuntimeRequestContext` fixtures construct with `connector_scopes=None` expecting `{}`.
- Stream event emission paths sometimes pass `payload=None` for events that have no body.

If the validator is removed, those paths would either fail Pydantic validation (non-Optional field with None) or carry a None where downstream code expects a dict. The coercer keeps the structural behavior; the redaction part — the actually-wrong-here behavior — disappears.

### 4.4 Length clipping decision: remove

Pre-P11.5, the redactor clipped strings > 2 000 chars to `value[:2000] + "[truncated]"` outside user-content keys. P11.5 removes this entirely.

Why:

- The clip is **data loss**. A tool that returns a 100 KB legitimate result has 98% of its output destroyed before the user / model sees it.
- The user-content carve-out already exempts what users see (assistant text). The clip only affected metadata blobs and operator-facing diagnostics — exactly the contexts where the operator probably wants full data when debugging.
- The system has other size guards (tool budget enforcement, run timeouts, Pydantic field-level `max_length` where it matters). The 2 000-char clip is redundant.
- Postgres TOAST handles large column values transparently. SSE frames render slowly when huge but don't fail.

If real production payloads grow to multi-MB sizes and storage / bandwidth becomes a problem, a focused `PayloadSizeLimiter` PRD adds bounded clipping where needed — with explicit decisions about which fields and what thresholds.

### 4.5 Operational consequences

After P11.5:

- **SSE to browser:** full payload values land in `RuntimeEventEnvelope.payload`. A tool emitting `{"password": "..."}` (a tool bug) ships the password to the browser. This is the user's own session — the user already gave the credential to the tool.
- **Persistence:** durable rows carry full data. The `runtime_events` / `agent_messages` / etc. tables will accumulate any data tools emit. Audit / retention sweep handles deletion lifecycles — no extra work needed here, but operators should be aware that tool-emission hygiene is the upstream defense, not data-shape redaction at the persistence boundary.
- **LLM provider context:** unaffected by this PRD. The path from tool output → model context goes through `ManagedContextPayload` / runtime memory, not through `RuntimeEventEnvelope`. Sensitive content reaching the model provider is a separate concern owned by the tool / connector layer.
- **Logs:** unchanged. Deny-key filter + field tagging continue to elide credentials and tagged Pydantic fields.

The architecturally correct line is now drawn: logs are the boundary that protects against operator observability; everything else trusts that upstream (tools, connectors, memory writers) emits only what should be there.

---

## 5. Behaviors preserved

| Behavior                                                                            | After P11.5                                                                                                                             |
| ----------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------- |
| Pydantic `payload: JsonObject` and `metadata: JsonObject` field validation          | Still validates; coerces None / non-mapping per `JsonObjectCoercer`.                                                                    |
| Log-record `metadata` deny-key filter                                               | Unchanged.                                                                                                                              |
| `SafeLogDumper` field-tagging elision on `RuntimeLogEvent` / `HttpLogEvent`         | Unchanged.                                                                                                                              |
| `ManagedContextPayload.content` accessible via attribute access and `model_dump()`  | Unchanged.                                                                                                                              |
| `Sensitive[]` annotations on any Pydantic model                                     | Unchanged.                                                                                                                              |
| `RuntimeEventEnvelope.payload` validates as a dict                                  | Yes — coercer enforces the shape.                                                                                                       |
| Persisted record schemas (DDL, column types)                                        | Unchanged. Same Pydantic models; same migrations.                                                                                       |
| `f1`–`f4` flow integration tests (single turn, multi-turn tool, SSE resume, cancel) | Pass — they exercise lifecycle, not redacted-content shape. Tests asserting `[redacted]` placeholders are updated to assert raw values. |
| Approval workflow                                                                   | Unchanged. Approval payloads pass through whole; `MCP_AUTH_REQUIRED` events carry auth URLs whole.                                      |

### 5.1 Behaviors removed

| Behavior                                                                        | After P11.5                                                                                                                                                                               |
| ------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `{"password": "x"}` value scrubbed in event payload                             | No. Value passes through. Tool emitters must not include credentials in payloads they emit.                                                                                               |
| 2 000-char length clip on metadata strings                                      | No. Strings pass through whole. See §4.4 for rationale.                                                                                                                                   |
| User-content carve-out (skipping value-regex inside `message` / `delta` / etc.) | Already dead in P11.2 (value regex was deleted there). The carve-out's length-cap branch was the only remaining behavior; that's now also gone everywhere except the registry's log path. |
| `[redacted]` and `[truncated]` placeholders in SSE / persistence payloads       | No longer produced from these layers. Logs still produce them via deny-key filter.                                                                                                        |

---

## 6. Risks and mitigations

| Risk                                                                                            | Likelihood | Impact    | Mitigation                                                                                                                                                                                                                                      |
| ----------------------------------------------------------------------------------------------- | ---------- | --------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| A misbehaving tool emits `{"password": "..."}` and the credential is durably stored in DB       | Medium     | High      | Document explicitly that tool emission hygiene is the upstream defense. The platform does not silently scrub credentials at the persistence boundary. If a real tool ships with this bug, fix the tool. The legacy redactor was hiding the bug. |
| A tool returns a 1 MB output and the SSE frame is large / slow                                  | Medium     | Medium    | SSE rendering is the frontend's concern; collapse / paginate in the UI. If a specific high-volume tool ships in production with consistent multi-KB outputs, a focused per-tool size cap is the right intervention.                             |
| A persisted row grows huge and degrades query performance                                       | Low        | Medium    | Postgres TOAST handles oversized columns transparently. Monitor `pg_stat_user_tables` after rollout; if a specific table grows fast, add a targeted size limit there.                                                                           |
| A test asserts `[redacted]` in an SSE payload and now fails                                     | High       | Low       | Expected. Update the test to assert the raw value passes through. Run regression suite — every such test will fail explicitly with a clear mismatch.                                                                                            |
| A test asserts on the user-content carve-out length behavior                                    | Medium     | Low       | The carve-out's length-drop is gone outside logs. Tests need updating to assert no truncation.                                                                                                                                                  |
| Caller passes None where a `JsonObject` field is required and the coercer fixes it up           | High       | (Desired) | Behavior preserved — coercer handles None → {}.                                                                                                                                                                                                 |
| `PersistenceValueNormalizer.redact_json_object` still exists for backwards compat through P11.6 | Low        | Low       | Document that the method exists as a one-phase compat hook. P11.6 deletes it.                                                                                                                                                                   |

---

## 7. Test requirements

### 7.1 New tests (`test_json_object_coercer.py`)

- `test_none_coerces_to_empty_dict` — `JsonObjectCoercer.coerce(None) == {}`.
- `test_dict_passes_through_as_dict` — input dict produces an equal output dict (new instance).
- `test_non_mapping_is_wrapped` — `JsonObjectCoercer.coerce("string")` returns `{"value": "string"}`.
- `test_credential_keys_are_not_scrubbed` — `JsonObjectCoercer.coerce({"password": "x"}) == {"password": "x"}`. Important: this pins the new behavior.
- `test_long_strings_are_not_clipped` — a 10 000-char string under a non-user-content key passes through unchanged.
- `test_nested_credential_keys_are_not_scrubbed` — coercion is shallow; `{"args": {"password": "x"}}` passes through with nested `password` intact.

### 7.2 Tests updated

These tests asserted on the old redaction behavior in SSE / persistence / contracts paths. Update to assert pass-through:

- `tests/unit/agent_runtime/agent/test_streaming_observability.py::test_stream_contracts_validate_and_redact_payloads` — was checking `event.payload["api_key"] == Defaults.REDACTED`. Now asserts the value passes through. Rename to drop "redact" from the name if appropriate.
- Any test on persistence-record fixtures that asserted on `[redacted]` placeholders.
- Tests in `runtime_api/schemas/` that exercised the redactor.

Tests on log emitters (`test_logging.py`, `test_http_logging.py`) continue to pass — those still run the deny-key filter via `_MetadataRedactor`. No update.

Tests on `ObservabilityRedactor` directly (still in use by log paths via `RegexRedactor`) continue to pass — that code path is unchanged.

### 7.3 Regression suite

- Full `agent_runtime/` + `runtime_api/` + `runtime_adapters/` unit suites green.
- Where a test breaks because it asserted on pre-P11.5 redaction in a non-log path, update the assertion. Don't suppress.

---

## 8. Rollout / rollback

### 8.1 Rollout

Single PR. No feature flag. The behavior change is bounded by the 19 callsite updates and the test updates.

Production data impact: from this PR forward, new `runtime_events` / persistence rows carry full data values. Existing rows are unchanged.

### 8.2 Rollback

Revert the PR. The 19 callsites flip back to `redact_json_object`; the coercer code stays but goes unused; tests revert.

Existing post-deploy data that contains un-scrubbed values stays in the DB. Operators concerned about this need a separate cleanup script — the rollback does not retroactively scrub historical rows.

---

## 9. Acceptance criteria

- [x] `JsonObjectCoercer` exists in [`observability/redactor.py`](../../src/agent_runtime/observability/redactor.py) with one classmethod `coerce(value)`.
- [x] `ValueNormalizer.coerce_json_object` and `PersistenceValueNormalizer.coerce_json_object` added.
- [x] `JsonObjectCoercer` re-exported from [`agent_runtime/observability/__init__.py`](../../src/agent_runtime/observability/__init__.py).
- [x] **14 non-log callsites** switched from `redact_json_object` to `coerce_json_object` (the parent PRD's "19" was approximate; actual count is 14 across 8 schema/contract files and 6 persistence records, plus the validation shim).
- [x] `_MetadataRedactor` in `logging.py` and `http_logging.py` unchanged.
- [x] New test file [`test_json_object_coercer.py`](../../tests/unit/agent_runtime/observability/test_json_object_coercer.py) exists with **13 tests** across 4 test classes covering None / non-mapping / mapping coercion + the explicit no-redaction contract.
- [x] Tests previously asserting on `[redacted]` in non-log paths updated to assert pass-through (5 test methods touched across 3 files: `test_streaming_observability.py`, `test_persistence_contracts.py`, `test_fastapi_runtime_api.py`, `test_runtime_event_timeline.py`).
- [x] Full regression suite green — **1202 tests passing, 0 failures**.

---

## 10. Done definition

- Tests in §9 green.
- Parent PRD's phase table marks P11.5 as **Shipped**.
- This PRD's Status header flipped to **Shipped 2026-05-11**.
