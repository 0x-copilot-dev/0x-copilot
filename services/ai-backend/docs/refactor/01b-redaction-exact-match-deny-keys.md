# Refactor PRD — Exact-match key deny set; delete `SENSITIVE_VALUE` regex (P11.2)

**Status:** Shipped 2026-05-11
**Parent:** [`01-redaction-subsystem.md`](01-redaction-subsystem.md) §11.2
**Audit reference:** [refactor-audit.md §1.4](../architecture/refactor-audit.md#14-custom-redactor)
**Owner:** Agent runtime team

---

## 1. Problem

After [P11.1](01a-redaction-protocol.md), the active redactor is `RegexRedactor`, which still relies on two regex-based behaviors that are the actual smells the audit flagged:

1. **`SENSITIVE_KEY` uses regex substring search.** `re.search(r"(api[_-]?key|authorization|credential|password|secret|token)", key)` matches any key whose name contains one of those substrings. That's why `input_tokens`, `output_tokens`, `cached_input_tokens`, and seven other observability-counter keys had to be hand-allowlisted in `_TOKEN_COUNT_KEYS` — the regex was matching them on the substring `token`.
2. **`SENSITIVE_VALUE` regex scans every string leaf** for credential-shaped substrings (`re.search(r"(api_key|password|secret|...)\s*[:=]\s*\S+", value)`). This is the over-fire smell: the regex destroys assistant messages whenever the model writes `api_key = "..."` in an illustrative example. The current workaround is the `user_content` carve-out, which only papers over the bug for known user-visible keys.

Both behaviors fight the structural direction set in the parent PRD (§8): **logs are the only redaction surface; redaction is by field type, not by value content.** P11.2 is the smallest possible step that aligns the deny-key behavior with the new direction.

### What this PRD does

- Replace the `SENSITIVE_KEY` regex with an **exact-match** `frozenset` of literal key names.
- Delete `Patterns.SENSITIVE_VALUE` entirely. No value scanning anywhere.
- Delete `_TOKEN_COUNT_KEYS` (the workaround the substring match required).
- Simplify the user-content carve-out: drop the "skip value regex" branch since there's no value regex left. Keep the length-cap drop.
- Update tests that asserted on the deleted value-scan behavior.

### What this PRD does NOT do

- Touch the field-tagging system (`Sensitive[]` annotation) — that's [P11.3](01c-redaction-field-tagging.md).
- Touch memory's [`Patterns`](../../src/agent_runtime/context/memory/constants.py) duplicate — that's [P11.4](01d-redaction-pattern-consolidation.md).
- Remove `redact_json_object` from non-log paths — that's [P11.5](01e-redaction-remove-from-non-log-paths.md).
- Delete the `ObservabilityRedactor` backwards-compat facade — that's [P11.6](01f-redaction-cleanup.md).
- Migrate the 19 callsites. They keep working through the facade.

---

## 2. Goal and non-goals

### Goal

Replace pattern-based key matching with structural exact-match key matching, and remove value scanning entirely from `RegexRedactor`. Behavior changes are scoped and tested.

### Non-goals

- Add new sensitive-key categories beyond what the existing regex captured.
- Change the `Redactor` Protocol shape.
- Touch any code outside [`agent_runtime/observability/`](../../src/agent_runtime/observability/).
- Make redaction more sophisticated. (P11.3 adds field tagging; P11.5 stops running redaction in the wrong layer. P11.2 is just "use the right matching primitive.")

### Success criteria

1. [`agent_runtime/observability/redactor.py`](../../src/agent_runtime/observability/redactor.py) exports a `DENY_KEYS: frozenset[str]` constant with the 15 literal key names listed in §4.
2. `RegexRedactor._redact_key_value` matches keys against `DENY_KEYS` via `key in DENY_KEYS`. No `re.search`.
3. `RegexRedactor._redact_string` removes the `Patterns.SENSITIVE_VALUE.search(...)` branch. The body becomes "clip to `max_string_length` if needed, otherwise return as-is."
4. `_TOKEN_COUNT_KEYS` constant is deleted from `redactor.py`.
5. [`agent_runtime/observability/constants.py`](../../src/agent_runtime/observability/constants.py) `Patterns.SENSITIVE_VALUE` is deleted. `Patterns.SENSITIVE_KEY` is also deleted (the deny set in `redactor.py` replaces it).
6. The 4 pattern-only callers ([logging.py:123](../../src/agent_runtime/observability/logging.py), [http_logging.py:86](../../src/agent_runtime/observability/http_logging.py), [memory/contracts.py:400+403](../../src/agent_runtime/context/memory/contracts.py), [memory/policy.py:202](../../src/agent_runtime/context/memory/policy.py)) are updated to import the deny set from `redactor.py`. (Memory's own duplicate `Patterns` block stays for now — [P11.4](01d-redaction-pattern-consolidation.md) consolidates it.)
7. Existing tests in [`test_streaming_observability.py`](../../tests/unit/agent_runtime/agent/test_streaming_observability.py) are updated: tests asserting value-pattern scrubbing inside non-user-content are deleted; tests asserting key-name scrubbing pass unchanged.
8. New tests assert the exact-match semantics (`input_tokens` is not redacted; `password` is).

---

## 3. Systems touched

### 3.1 Files changed

| File                                                                                                   | Change                                                                                                                                                                                                                                                                                                                                                                     |
| ------------------------------------------------------------------------------------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| [`agent_runtime/observability/redactor.py`](../../src/agent_runtime/observability/redactor.py)         | Add `DENY_KEYS: frozenset[str]`. Delete `_TOKEN_COUNT_KEYS`. Rewrite `RegexRedactor._redact_key_value` to use `key in DENY_KEYS`. Rewrite `RegexRedactor._redact_string` to drop the `Patterns.SENSITIVE_VALUE.search(...)` branch — only length-clip remains. Simplify `_redact_key_value`'s `UserContentKeys` branch to "drop length cap" (no value-regex flag to pass). |
| [`agent_runtime/observability/constants.py`](../../src/agent_runtime/observability/constants.py)       | Delete `Patterns.SENSITIVE_KEY` and `Patterns.SENSITIVE_VALUE`. If `Patterns` becomes empty, delete the class. (Keep `Keys`, `Defaults`, `UserContentKeys` — they're unrelated.)                                                                                                                                                                                           |
| [`agent_runtime/observability/logging.py`](../../src/agent_runtime/observability/logging.py)           | `_MetadataRedactor.redact` swaps `Patterns.SENSITIVE_KEY.search(key)` for `key in DENY_KEYS`. Import `DENY_KEYS` from `redactor.py`.                                                                                                                                                                                                                                       |
| [`agent_runtime/observability/http_logging.py`](../../src/agent_runtime/observability/http_logging.py) | Same swap.                                                                                                                                                                                                                                                                                                                                                                 |
| [`agent_runtime/context/memory/contracts.py`](../../src/agent_runtime/context/memory/contracts.py)     | `MemoryRedactor.redact_metadata` swaps `Patterns.SENSITIVE_KEY.search(key)` for `key in DENY_KEYS`. Delete the `isinstance(item, str) and Patterns.SENSITIVE_VALUE.search(item)` branch — no value scanning anymore. Import `DENY_KEYS` from `agent_runtime.observability.redactor`.                                                                                       |
| [`agent_runtime/context/memory/policy.py`](../../src/agent_runtime/context/memory/policy.py)           | Delete the `if Patterns.SENSITIVE_VALUE.search(normalized): return True` branch in `is_prompt_injection`. Prompt-injection detection now relies only on the explicit `PROMPT_INJECTION_PATTERNS` list (already present). Add a comment explaining the decoupling — the regex was an accidental coupling, not a real signal for prompt injection.                           |
| [`agent_runtime/context/memory/constants.py`](../../src/agent_runtime/context/memory/constants.py)     | Memory's local `Patterns.SENSITIVE_KEY` and `Patterns.SENSITIVE_VALUE` stay for now ([P11.4](01d-redaction-pattern-consolidation.md) removes them after the consolidated source is in place). Imports updated only where they were called.                                                                                                                                 |

### 3.2 Files not touched

- 19 redaction callsites (events, runs, conversations, persistence records, validation shim, execution contracts). They go through `ObservabilityRedactor.redact_json_object(...)` → `RegexRedactor.redact_json_object(...)` — the inner implementation changes, the call shape doesn't.
- `Sensitive` annotation system — P11.3.
- Memory's own constants — P11.4.

### 3.3 Tests changed

| File                                                                                                                                     | Change                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| ---------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| [`tests/unit/agent_runtime/agent/test_streaming_observability.py`](../../tests/unit/agent_runtime/agent/test_streaming_observability.py) | Delete `test_sensitive_value_outside_user_content_key_still_redacted` (value scanning no longer happens — that case now passes through). Keep all 7 other tests; `test_sensitive_value_pattern_inside_user_content_key_is_preserved` and `test_sensitive_value_pattern_inside_nested_user_content_is_preserved` continue to pass (the carve-out behavior they exercise no longer needs the value-regex skip, but the assertion — "value is not redacted" — still holds). |
| `tests/unit/agent_runtime/observability/test_deny_keys.py` (new)                                                                         | New file. Tests: `input_tokens` is not in `DENY_KEYS`; `password` is; `apikey`, `api_key`, `api-key` are all in; `tokenizer` (which contains `token` as substring) is **not** redacted; `My_Password` (case sensitivity TBD — see §6) is or isn't redacted per the case-policy decision.                                                                                                                                                                                 |
| `tests/unit/agent_runtime/observability/test_logging.py`, `test_http_logging.py`                                                         | Verify their `_MetadataRedactor` swap. Existing assertions on `authorization`, `api_key` keys being dropped still hold. New assertion: `input_tokens` and `output_tokens` in log metadata pass through as integer values.                                                                                                                                                                                                                                                |
| Memory-side: any test that exercised `MemoryRedactor` against value-pattern scrubbing                                                    | Updated to assert the value passes through unchanged. (Memory's pattern was already different — `credential` was missing — so the scope of "what was being scrubbed" was already inconsistent. The new behavior is consistent.)                                                                                                                                                                                                                                          |

---

## 4. The new deny set

```python
# agent_runtime/observability/redactor.py

DENY_KEYS: frozenset[str] = frozenset(
    {
        # Generic credentials
        "password",
        "passwd",
        "secret",
        "credential",
        "credentials",
        # API tokens
        "api_key",
        "apikey",
        "api-key",
        # OAuth / session tokens
        "authorization",
        "auth_token",
        "access_token",
        "refresh_token",
        # Asymmetric crypto material
        "private_key",
        "client_secret",
        # Generic catch-all (still exact match, just the word itself)
        "token",
    }
)
```

15 entries. Closed set. Updated only when a new credential field name appears in code review.

**What's deliberately not in the set:**

- `input_tokens`, `output_tokens`, etc. — these are observability counters. The substring-match regex had to be worked around with the `_TOKEN_COUNT_KEYS` allowlist; exact match doesn't need the workaround.
- `bearer_token`, `id_token` — verify in code if any production payload uses these names. Add if yes; reject if not. The closed-set rule means we add as needed, not as a precaution.
- Anything that looks like a name (`person`, `email`, `phone`) — those are PII, not credentials. PII detection is structural via [P11.3](01c-redaction-field-tagging.md) field tagging, not via a deny set.

The deny set covers `metadata: dict[str, JsonScalar]` shaped fields where we can't enforce a schema. Typed Pydantic fields use the `Sensitive[]` annotation instead, once P11.3 lands.

---

## 5. Design

### 5.1 `RegexRedactor` after P11.2

```python
class RegexRedactor:
    def redact_json_object(
        self,
        value: object,
        *,
        max_string_length: int | None = Defaults.MAX_STREAM_FIELD_LENGTH,
        user_content: bool = False,
    ) -> dict[str, object]:
        if value is None:
            return {}
        if not isinstance(value, Mapping):
            return {
                "value": self.redact_json_value(
                    value,
                    max_string_length=max_string_length,
                    user_content=user_content,
                )
            }
        return {
            str(key): self._redact_key_value(
                str(key),
                item,
                max_string_length=max_string_length,
                user_content=user_content,
            )
            for key, item in value.items()
        }

    # ... redact_json_value unchanged ...

    def _redact_key_value(self, key, value, *, max_string_length, user_content):
        if key in DENY_KEYS:
            return Defaults.REDACTED
        if key in UserContentKeys.KEYS:
            return self.redact_json_value(
                value,
                max_string_length=None,  # drop length cap; no value-regex skip needed
                user_content=True,
            )
        return self.redact_json_value(
            value,
            max_string_length=max_string_length,
            user_content=user_content,
        )

    def _redact_string(self, value, *, max_string_length, user_content):
        # No value-pattern scrubbing. The user_content parameter survives
        # in the signature for source-compat with the Redactor Protocol,
        # but only the length-clip path remains.
        if max_string_length is None or len(value) <= max_string_length:
            return value
        return f"{value[:max_string_length]}{Defaults.TRUNCATED}"
```

The implementation goes from 165 lines to ~120 lines. `_TOKEN_COUNT_KEYS` gone. `Patterns.SENSITIVE_VALUE.search(value)` gone. The substring `re.search(SENSITIVE_KEY, key)` gone.

### 5.2 Why `user_content` parameter survives

The `Redactor` Protocol from P11.1 declares `user_content: bool = False` on both methods. Removing it now would break the Protocol contract. P11.3 reworks the Protocol; P11.2 just narrows what the parameter does.

After P11.2, `user_content=True` only means "drop the length cap." After P11.3, the whole carve-out concept is replaced by field-tagging-driven behavior. The parameter is deprecated then but kept for compatibility one phase longer.

### 5.3 Case sensitivity decision

`DENY_KEYS` uses exact lowercase match. Most JSON keys in this codebase are snake_case ASCII lowercase. Two implementation options:

**Option A (recommended):** Match the key string as-is. Callers normalize keys to lowercase upstream when they need to. Predictable, no surprises.

**Option B:** Lowercase the key before lookup. Catches `Password` and `PASSWORD` automatically. Slightly more forgiving; adds one `.lower()` per dict entry on the hot path.

**Decision: Option A.** The current regex used `re.IGNORECASE`, so technically this is a tightening — `My_Password` was matched before, isn't now. In practice all production payloads from MCP / tool / model output use ASCII lowercase snake_case, so this rarely fires. If a real case-mismatch shows up post-deploy, we add `.lower()` then; doing it preemptively spends CPU we don't need to spend.

---

## 6. Behaviors preserved

| Behavior                                                                          | After P11.2                                                                                           |
| --------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------- |
| Recursive JSON walking (None → {}, non-mapping → {"value": …}, mapping → recurse) | Unchanged.                                                                                            |
| `{"password": "foo"}` → value scrubbed                                            | **Preserved.** Exact match on `password`.                                                             |
| `{"api_key": "foo"}` → value scrubbed                                             | **Preserved.** Exact match on `api_key`.                                                              |
| `{"authorization": "Bearer xyz"}` → value scrubbed                                | **Preserved.** Exact match on `authorization`.                                                        |
| `{"input_tokens": 42}` → integer preserved                                        | **Preserved.** `input_tokens` no longer needs the explicit allowlist.                                 |
| `{"tokenizer": "claude"}` → value preserved                                       | **Preserved.** Substring `token` no longer matches; exact match `tokenizer` ≠ `token`.                |
| Length clip outside user-content (>2000 → truncated)                              | Unchanged.                                                                                            |
| User-content keys drop the length cap                                             | Unchanged.                                                                                            |
| Sticky user-content propagation through nested structures                         | Unchanged.                                                                                            |
| `{"args": {"password": "foo"}}` — sensitive key inside user-content               | **Preserved.** Key scrub still fires inside user-content (user-content branch only drops length cap). |

## 6.1 Behaviors removed

| Behavior                                                              | After P11.2                                                                                 |
| --------------------------------------------------------------------- | ------------------------------------------------------------------------------------------- |
| `{"metadata": "my password = hunter2"}` → value scrubbed              | **Removed.** Value passes through. (Was a false-positive magnet anyway.)                    |
| Substring matching: `{"my_authorization_id": "..."}` → value scrubbed | **Removed.** Exact match only.                                                              |
| `My_Password` case-insensitive match                                  | **Removed.** Case-sensitive match. (See §5.3.)                                              |
| Prompt-injection detection on `SENSITIVE_VALUE` match                 | **Removed** in `policy.py`. Prompt-injection check now relies only on explicit phrase list. |

---

## 7. Risks and mitigations

| Risk                                                                                                                       | Likelihood | Impact | Mitigation                                                                                                                                                                                                                                                      |
| -------------------------------------------------------------------------------------------------------------------------- | ---------- | ------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| A production payload uses a key like `my_password_field` and currently gets scrubbed; after P11.2 the value passes through | Medium     | Medium | Audit production payload shapes pre-merge. Grep events and persistence records for current `[redacted]` placeholders that come through key-substring matches outside our deny set; add to the deny set if any are real.                                         |
| Removing the value regex regresses an unknown corner of the code that depends on it                                        | Low        | Medium | The 19 callsites all go through `ObservabilityRedactor` which delegates to `RegexRedactor` — there are no other consumers of `Patterns.SENSITIVE_VALUE` outside the four pattern-only callers (logging, http_logging, memory). All four are explicitly updated. |
| Memory's `is_prompt_injection` was using `SENSITIVE_VALUE` to flag prompt-injection-like content; removing it loses signal | Low        | Medium | The regex was a coincidence; it caught `api_key = ...` shaped content, not actual prompt-injection patterns. Real prompt-injection content uses phrases (already in `PROMPT_INJECTION_PATTERNS`). Decoupling is an improvement.                                 |
| Case-sensitivity tightening misses a key like `Password` from an external system                                           | Low        | Medium | Adopt Option A (case-sensitive). If a real case-mismatch shows up post-deploy, add `.lower()` in a follow-up — one line.                                                                                                                                        |
| Length-cap clip behavior subtly changes when `user_content=True` is passed alongside a non-None `max_string_length`        | Low        | Low    | Test pins the existing behavior — user-content paths still drop the cap via the `_redact_key_value` branch explicitly setting `max_string_length=None` on recursion.                                                                                            |

---

## 8. Test requirements

### 8.1 New tests

**`tests/unit/agent_runtime/observability/test_deny_keys.py`** (new file):

- `test_password_is_redacted` — `{"password": "x"}` → value is `[redacted]`.
- `test_api_key_variants_are_redacted` — `api_key`, `apikey`, `api-key` all redact.
- `test_input_tokens_passes_through` — `{"input_tokens": 42}` → `42`.
- `test_substring_tokenizer_passes_through` — `{"tokenizer": "claude"}` → `"claude"`.
- `test_substring_my_password_field_passes_through` — `{"my_password_field": "x"}` → `"x"` (no substring match).
- `test_case_sensitive_password_passes_through` — `{"Password": "x"}` → `"x"` (case-sensitive).
- `test_deny_keys_inside_user_content_still_scrubbed` — `{"args": {"password": "x"}}` → key scrub still wins.
- `test_deny_keys_membership_is_closed_set` — assert `DENY_KEYS` has exactly the 15 entries from §4.

### 8.2 Tests updated

**`test_streaming_observability.py`:**

- Delete `test_sensitive_value_outside_user_content_key_still_redacted`. The asserted behavior (free-text value containing `password = …` gets value-scrubbed) is removed.
- Verify the remaining 7 tests still pass:
  - `test_stream_contracts_validate_and_redact_payloads` — key-based scrubbing remains.
  - `test_user_content_key_bypasses_length_cap` — length-cap drop unchanged.
  - `test_user_content_uncap_is_sticky_through_nested_structures` — sticky propagation unchanged.
  - `test_non_user_content_key_still_clipped` — clip unchanged.
  - `test_sensitive_value_pattern_inside_user_content_key_is_preserved` — was asserting that user-content content with `api_key = …` passes through. Still passes (no value scan happens anywhere now).
  - `test_sensitive_key_nested_inside_user_content_key_still_redacted` — sensitive key inside user-content still scrubbed by exact-match deny set.
  - `test_sensitive_value_pattern_inside_nested_user_content_is_preserved` — same logic as above.

**`test_logging.py` / `test_http_logging.py`:**

- Update the metadata-redaction tests to assert `input_tokens` / `output_tokens` pass through (they're counters).
- Existing assertions on `authorization`, `api_key`, `password` being dropped continue to pass.

**Memory tests:**

- Any test asserting `MemoryRedactor` value-scrubbing on `"my password = ..."` strings is deleted.
- Tests asserting `MemoryRedactor` key-scrubbing on `{"password": ...}` continue to pass.
- `is_prompt_injection` tests: any test that fed it a `"api_key = ..."` string and expected `True` is deleted. Real prompt-injection phrases (already covered by `PROMPT_INJECTION_PATTERNS`) continue to test `True`.

### 8.3 Regression suite

- Full `agent_runtime/`, `runtime_api/`, `runtime_adapters/` unit suites must remain green.
- Any baseline that asserted specific `[redacted]` placeholders inside `payload` / `metadata` of persisted rows is updated to assert the original value passes through (key-scrub still applies; value-scrub no longer happens).

---

## 9. Rollout / rollback

### 9.1 Rollout

Single PR. No feature flag. Behavior change is targeted and bounded by the test updates in §8.2 — every change in behavior corresponds to an explicit test edit.

### 9.2 Rollback

Revert the PR. The deletions (`Patterns.SENSITIVE_VALUE`, `_TOKEN_COUNT_KEYS`) come back; `DENY_KEYS` goes away.

---

## 10. Acceptance criteria

- [x] `DENY_KEYS` exported from [`agent_runtime/observability/redactor.py`](../../src/agent_runtime/observability/redactor.py); exactly the 15 keys from §4.
- [x] `RegexRedactor._redact_key_value` matches via `key in DENY_KEYS`.
- [x] `RegexRedactor._redact_string` no longer references `Patterns.SENSITIVE_VALUE`.
- [x] `_TOKEN_COUNT_KEYS` deleted.
- [x] `Patterns.SENSITIVE_KEY` and `Patterns.SENSITIVE_VALUE` deleted from [`observability/constants.py`](../../src/agent_runtime/observability/constants.py). The `Patterns` class is gone entirely (was the only remaining content).
- [x] `_MetadataRedactor.redact` in [`logging.py`](../../src/agent_runtime/observability/logging.py) and [`http_logging.py`](../../src/agent_runtime/observability/http_logging.py) imports `DENY_KEYS` and matches against it.
- [x] `MemoryRedactor.redact_metadata` imports `DENY_KEYS`, drops the value-scrub branch.
- [x] `policy.is_prompt_injection` no longer references `Patterns.SENSITIVE_VALUE`.
- [x] New test file `test_deny_keys.py` exists with 22 tests across 6 test classes covering set membership, exact-match scrubbing, substring-no-longer-fires, case sensitivity, value-scanning-removed, and key-scrub-inside-user-content.
- [x] `test_streaming_observability.py` — 8 tests pass (the previously asserted "value-scrubbed outside user-content" case was rewritten to assert pass-through instead; PRD said delete it, implementation kept it as a documenting test).
- [x] One existing test updated: `test_drops_sensitive_metadata_keys` in `test_http_logging.py` — now exercises the exact-match + case-sensitive contract.
- [x] One existing test updated: `test_compression_event_redacts_sensitive_metadata` in `test_context_memory_management.py` — `note` field no longer value-scrubbed.
- [x] Broader regression suite green: **1161 tests passing, 0 failures** across `agent_runtime/`, `runtime_api/`, `runtime_adapters/`.

---

## 11. Done definition

- Tests in §10 are green.
- `git diff` shows: 1 file deleted ( `Patterns.SENSITIVE_KEY` / `SENSITIVE_VALUE` from `constants.py` — either the constants are deleted with the class kept, or the class is deleted entirely if empty), `redactor.py` modified, `logging.py` / `http_logging.py` modified, `memory/contracts.py` + `memory/policy.py` modified, 1 new test file, 1 test file modified.
- Parent PRD's phase table marks P11.2 as **Shipped**.
- This PRD's Status header flipped to **Shipped 2026-05-XX**.
