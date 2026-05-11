# Refactor 01 — Redaction Subsystem

**Status:** Draft for review (verified against code 2026-05-11)
**Audit reference:** [refactor-audit.md §1.4](../architecture/refactor-audit.md#14-custom-redactor)
**Owner:** TBD
**Target:** `agent_runtime/observability/redaction.py` and the 19 dependent locations described in [§5](#5-systems-it-touches)

> **Verification note (2026-05-11).** Every behavioral and structural claim in this PRD was cross-checked against `src/` on 2026-05-11. All §2 problem statements, §4 current-functionality items, §5 blast-radius enumerations, and the §9.1 UAE recognizer patterns are accurate. Three small corrections applied to this PRD:
>
> - **Test count.** §5.5 previously said "7 tests"; the actual count in [`test_streaming_observability.py`](../../tests/unit/agent_runtime/agent/test_streaming_observability.py) is **8**. Two tests are unnamed in the PRD body but cover documented behavior (`test_stream_contracts_validate_and_redact_payloads`, `test_sensitive_value_outside_user_content_key_still_redacted`).
> - **Callsite total.** §5.6 said "16 callsites." Itemized total is **19** (8 schemas + 6 persistence + 1 validation shim + 4 pattern-only). Plus one test module. Numbers were inconsistent; fixed below.
> - **Line numbers in §5.1 / §5.4 have drifted.** Files and methods are correct; line references are stale (e.g. `events.py:938-941` → now line 977). Treat the file/method references as load-bearing and re-grep line numbers at implementation time.
>
> No structural revisions to the PRD's scope, phasing, library decision, or compliance constraints — those remain accurate.

---

## 1. Context

The audit recommended replacing the bespoke [`ObservabilityRedactor`](../../src/agent_runtime/observability/redaction.py) with a library-backed solution. Before any refactor we have to:

1. Document everything the current redactor actually does (it has subtle, load-bearing behavior — see [§4](#4-current-functionality-must-survive)).
2. Map every callsite (it sits on Pydantic field validators across the schema, contract, persistence, and logging layers — see [§5](#5-systems-it-touches)).
3. Pick a replacement that is **free**, **on-prem**, **source-auditable**, and **acceptable to UAE banking and government buyers** (see [§7](#7-compliance-constraints-uae-banks--government)).

This document is a refactor PRD. It does not implement anything. Implementation starts after sign-off and lands in phases (see [§11](#11-refactor-plan-phased)).

---

## 2. Problem

Today's redaction subsystem has four distinct issues:

### 2.1 Pattern coverage is regex-only and incomplete

[`observability/constants.py`](../../src/agent_runtime/observability/constants.py) defines two regexes:

```python
SENSITIVE_KEY = re.compile(r"(api[_-]?key|authorization|credential|password|secret|token)", re.I)
SENSITIVE_VALUE = re.compile(r"(api[_-]?key|authorization|credential|password|secret|token)\s*[:=]\s*\S+", re.I)
```

This catches strings shaped like `password = …` and dict keys named `password`. It does **not** catch:

- Personally Identifiable Information (PII): names, emails, phone numbers, addresses.
- UAE-specific identifiers: Emirates ID (`784-YYYY-NNNNNNN-N`), UAE IBAN (`AE` + 21 digits), UAE mobile (`+971 5x xxx xxxx`), TRN (15 digits).
- Financial PII: credit card numbers (no Luhn check), bank account numbers.
- High-entropy secrets (API tokens that don't sit next to a `key=` literal).
- Health, government ID, biometric or other PDPL-regulated categories.

A bank or government regulator inspecting our redaction will find this list trivially insufficient.

### 2.2 The `SENSITIVE_VALUE` regex over-fires on prose and code

The codebase already documents a [bug fix](../../tests/unit/agent_runtime/agent/test_streaming_observability.py#L68-L83): the heuristic value regex destroyed assistant messages whenever the model wrote even one illustrative `api_key = "..."` line. The current mitigation is the `user_content` flag — when validating a value under one of the keys in `UserContentKeys.KEYS`, the value regex is skipped on string leaves. The structural key scrub still runs.

This is correct domain logic, but it's also a workaround for the regex being a bad PII detector. A real PII engine doesn't fire on illustrative code.

### 2.3 Patterns are duplicated across modules

The same regex pair lives in two places:

- [`observability/constants.py`](../../src/agent_runtime/observability/constants.py:67-74) — six categories including `credential`.
- [`context/memory/constants.py`](../../src/agent_runtime/context/memory/constants.py:99-105) — five categories, **drops `credential`**.

[`context/memory/contracts.py`](../../src/agent_runtime/context/memory/contracts.py:382-410) uses the memory copy. [`context/memory/policy.py`](../../src/agent_runtime/context/memory/policy.py:202) uses `SENSITIVE_VALUE` to detect _prompt injection_ — a domain quite different from "redact this for a stream." That single regex is doing three jobs across three modules.

### 2.4 The `_TOKEN_COUNT_KEYS` allowlist is a hack

[`redaction.py`](../../src/agent_runtime/observability/redaction.py:14-27) maintains a hardcoded set of 10 key names (`input_tokens`, `output_tokens`, etc.) that contain the substring `token` and would be falsely redacted by `SENSITIVE_KEY`. Each new observability counter that contains `token` requires hand-adding to this list. This is a smell: the regex matches too aggressively and we're papering over it with an allowlist.

---

## 3. Goals & non-goals

### Goals

1. Replace the regex-only `SENSITIVE_VALUE` with a library that detects PII categories meaningfully (names, emails, phones, financial identifiers, government IDs).
2. Replace the regex-only `SENSITIVE_KEY` credential detection with an entropy-aware library that catches API tokens, JWTs, private keys, and similar high-entropy secrets without depending on the surrounding key name.
3. Add UAE-specific recognizers (Emirates ID, UAE IBAN, UAE mobile, TRN, UAE passport).
4. Eliminate the duplicated patterns between `observability/constants.py` and `context/memory/constants.py`. Single source of truth.
5. Preserve every documented behavior of the current redactor (see [§4](#4-current-functionality-must-survive)). Tests pass byte-equal where they assert specific output strings, except where the new engine's recall is strictly better and we update the assertion deliberately.
6. The replacement runs entirely on-prem, has no proprietary licensing, and ships with no per-call cost.
7. The replacement is auditable by a regulator: we can print the exact list of recognizers that ran and the rule that fired.
8. Keep the abstraction swappable behind a `Redactor` Protocol so future engine swaps don't require changes to the 16 callsites.

### Non-goals

- Replacing the structural payload-shrinking behavior (max-string-length clip, user-content carve-out, sticky propagation through nested structures, token-count allowlist). These are _our_ domain decisions; libraries don't ship them.
- Replacing the prompt-injection heuristic in [`context/memory/policy.py`](../../src/agent_runtime/context/memory/policy.py:198-204). That's a separate concern using the same regex by accident; this PRD will _un-couple_ it (move the prompt-injection patterns to their own module), but does not redesign it.
- Replacing logging-side denylist usage in [`observability/logging.py`](../../src/agent_runtime/observability/logging.py:123) and [`observability/http_logging.py`](../../src/agent_runtime/observability/http_logging.py:86) with the new engine. Log denylists are a different shape (drop the key entirely, no recursion) and out of scope here — we will, however, point them at the consolidated pattern source so they don't drift.
- Adding ML-based PII detection that requires a downloaded model on cold start. Recognizers must be regex- or rule-based to start; ML models are an opt-in addition behind a settings toggle (see [§9](#9-decision)).
- Adding a network call out of the service in any code path. No SaaS PII APIs.

---

## 4. Current functionality (must survive)

This is what the existing redactor does. Every item below has a test or a comment in the code that documents intent. Any replacement must reproduce all of them.

### 4.1 Recursive JSON-shape walking

`ObservabilityRedactor.redact_json_object(value)`:

- `None` → `{}`.
- Non-mapping → `{"value": redact_json_value(value)}`.
- Mapping → mapping with the same keys and per-key redaction applied to each value.

`redact_json_value(value)`:

- `None` / `bool` / `int` / `float` → unchanged.
- `str` → `_redact_string(...)`.
- `Mapping` → recurse via `redact_json_object`.
- `Iterable` → list of recursed values.
- Anything else → `_redact_string(str(value))`.

### 4.2 Structural key scrub

If a key name matches `SENSITIVE_KEY`, the value is replaced with `Defaults.REDACTED` (`"[redacted]"`). This applies _everywhere_, including under user-content keys. Test: [`test_sensitive_key_nested_inside_user_content_key_still_redacted`](../../tests/unit/agent_runtime/agent/test_streaming_observability.py#L96-L106).

### 4.3 Token-count key allowlist

The 10 keys in `_TOKEN_COUNT_KEYS` (`before_tokens`, `after_tokens`, `input_tokens`, `output_tokens`, `cached_input_tokens`, `reasoning_tokens`, `total_tokens`, `context_tokens`, `max_input_tokens`, `max_output_tokens`) bypass the structural scrub even though they contain `token`. They carry observability counters, not credentials.

### 4.4 User-content carve-out

If a key is in `UserContentKeys.KEYS` (`message`, `delta`, `summary`, `reason`, `output`, `content`, `arguments`, `args`, `description`):

- The value is recursed with `user_content=True` and `max_string_length=None`.
- Inside user-content, the _value-pattern_ check on string leaves is skipped (because the regex over-fires on prose).
- The length cap is dropped (chat replies, tool outputs, reasoning summaries render in full).
- The structural key scrub still runs at every level.

User-content propagates **stickily** through nested structures. Tests:

- [`test_user_content_key_bypasses_length_cap`](../../tests/unit/agent_runtime/agent/test_streaming_observability.py#L44-L48)
- [`test_user_content_uncap_is_sticky_through_nested_structures`](../../tests/unit/agent_runtime/agent/test_streaming_observability.py#L50-L55)
- [`test_sensitive_value_pattern_inside_user_content_key_is_preserved`](../../tests/unit/agent_runtime/agent/test_streaming_observability.py#L68-L83)
- [`test_sensitive_value_pattern_inside_nested_user_content_is_preserved`](../../tests/unit/agent_runtime/agent/test_streaming_observability.py#L108-L125)

### 4.5 Length clip

Outside user-content, strings longer than `Defaults.MAX_STREAM_FIELD_LENGTH` (2 000 chars) are truncated to `value[:2000] + "[truncated]"`. Test: [`test_non_user_content_key_still_clipped`](../../tests/unit/agent_runtime/agent/test_streaming_observability.py#L57-L66).

### 4.6 Value-pattern scrub (the regex that over-fires)

Outside user-content, if `SENSITIVE_VALUE.search(string)` matches, the whole string becomes `"[redacted]"`. This is the behavior we are _replacing_ with library-backed detection in [§9](#9-decision); the new engine must still produce a redacted result for any string that the old regex would have caught (recall ≥ current).

### 4.7 Pydantic field-validator integration

The redactor is invoked from `mode="before"` field validators on Pydantic models. The validator returns the redacted dict, which Pydantic then validates against the field type (`JsonObject`). The new engine must run cleanly inside a Pydantic v2 validator (no IO, no async, no thread-pool indirection — these run on the request hot path).

---

## 5. Systems it touches

Mapping the blast radius from code (`grep` confirmed):

### 5.1 Direct callers of `ObservabilityRedactor.redact_json_object`

| File                                                                                                       | Field(s)                                                                   | Notes                                                         |
| ---------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------- | ------------------------------------------------------------- |
| [`runtime_api/schemas/events.py:938-941`](../../src/runtime_api/schemas/events.py#L938-L941)               | `payload`, `metadata` on `RuntimeEventEnvelope`                            | **Hottest path.** Every persisted event runs through this.    |
| [`runtime_api/schemas/runs.py:164-172`](../../src/runtime_api/schemas/runs.py#L164-L172)                   | `connector_scopes`, `context`, `trace_metadata` on `RuntimeRequestContext` | Run create + run handle.                                      |
| [`runtime_api/schemas/runs.py:239-242`](../../src/runtime_api/schemas/runs.py#L239-L242)                   | `request_options` on `CreateRunRequest`                                    | Run create.                                                   |
| [`runtime_api/schemas/conversations.py:77`](../../src/runtime_api/schemas/conversations.py#L77)            | conversation/message metadata                                              | List + single conversation responses.                         |
| [`runtime_api/schemas/conversations.py:419`](../../src/runtime_api/schemas/conversations.py#L419)          | conversation/message metadata                                              | List + single conversation responses.                         |
| [`agent_runtime/execution/contracts.py:215-218`](../../src/agent_runtime/execution/contracts.py#L215-L218) | `metadata` on `RuntimeRunContext`                                          | Internal run context — flows from API to worker to LangGraph. |
| [`agent_runtime/execution/contracts.py:465-468`](../../src/agent_runtime/execution/contracts.py#L465-L468) | `trace_metadata` on (likely `AgentRuntimeContext`)                         | Worker-side.                                                  |
| [`agent_runtime/execution/contracts.py:620-623`](../../src/agent_runtime/execution/contracts.py#L620-L623) | another metadata field                                                     | Worker-side.                                                  |

### 5.2 Persistence records (via the `PersistenceValueNormalizer.redact_json_object` alias)

[`persistence/records/common.py:123-140`](../../src/agent_runtime/persistence/records/common.py#L123-L140) re-exports `ObservabilityRedactor.redact_json_object`. These records call it from their `mode="before"` field validators:

| File                                                                                                             | Field                              |
| ---------------------------------------------------------------------------------------------------------------- | ---------------------------------- |
| [`persistence/records/approvals.py:46-47`](../../src/agent_runtime/persistence/records/approvals.py#L46-L47)     | `request_payload` on approval rows |
| [`persistence/records/tools.py:59-60`](../../src/agent_runtime/persistence/records/tools.py#L59-L60)             | tool-invocation JSON fields        |
| [`persistence/records/memory.py:41-42`](../../src/agent_runtime/persistence/records/memory.py#L41-L42)           | memory `namespace`                 |
| [`persistence/records/audit.py:37-38`](../../src/agent_runtime/persistence/records/audit.py#L37-L38)             | audit log `metadata`               |
| [`persistence/records/checkpoints.py:29-30`](../../src/agent_runtime/persistence/records/checkpoints.py#L29-L30) | checkpoint `metadata`              |
| [`persistence/records/outbox.py:58-59`](../../src/agent_runtime/persistence/records/outbox.py#L58-L59)           | outbox event `payload`             |

### 5.3 Validation shim

[`agent_runtime/validation.py:102-106`](../../src/agent_runtime/validation.py#L102-L106) — `ValueNormalizer.redact_json_object` lazily imports the redactor. Any caller of the normalizer that hits this method gets the same code path.

### 5.4 Pattern-only callers (not through the redactor)

These import `Patterns.SENSITIVE_KEY` / `SENSITIVE_VALUE` directly. They are _not_ the redactor itself but they share the patterns:

| File                                                                                                   | Use                                                                                                                                          |
| ------------------------------------------------------------------------------------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------- |
| [`observability/logging.py:123`](../../src/agent_runtime/observability/logging.py#L123)                | `_MetadataRedactor` for structured logs — drops keys matching `SENSITIVE_KEY`.                                                               |
| [`observability/http_logging.py:86`](../../src/agent_runtime/observability/http_logging.py#L86)        | Same shape, for HTTP-scope log records.                                                                                                      |
| [`context/memory/contracts.py:382-410`](../../src/agent_runtime/context/memory/contracts.py#L382-L410) | `MemoryRedactor` — separate redactor for memory metadata, uses memory's own copy of `Patterns`.                                              |
| [`context/memory/policy.py:198-204`](../../src/agent_runtime/context/memory/policy.py#L198-L204)       | Prompt-injection heuristic. Uses `SENSITIVE_VALUE` to flag content that looks like `api_key = …`. **Different domain**, accidental coupling. |

### 5.5 Test surface

| File                                                                                                                                                           | Coverage                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      |
| -------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| [`tests/unit/agent_runtime/agent/test_streaming_observability.py`](../../tests/unit/agent_runtime/agent/test_streaming_observability.py)                       | The canonical behavior contract. **8 tests:** `test_stream_contracts_validate_and_redact_payloads`, `test_user_content_key_bypasses_length_cap`, `test_user_content_uncap_is_sticky_through_nested_structures`, `test_non_user_content_key_still_clipped`, `test_sensitive_value_pattern_inside_user_content_key_is_preserved`, `test_sensitive_value_outside_user_content_key_still_redacted`, `test_sensitive_key_nested_inside_user_content_key_still_redacted`, `test_sensitive_value_pattern_inside_nested_user_content_is_preserved`. **All must pass after refactor.** |
| [`tests/unit/runtime_adapters/postgres/test_field_encryption_projections.py`](../../tests/unit/runtime_adapters/postgres/test_field_encryption_projections.py) | Imports the redactor surface but doesn't test redaction directly. Still must not regress.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     |

### 5.6 Blast radius summary

- **8 schemas / contracts** invoke `redact_json_object` directly on field validators ([events.py](../../src/runtime_api/schemas/events.py), [runs.py](../../src/runtime_api/schemas/runs.py) × 2, [conversations.py](../../src/runtime_api/schemas/conversations.py) × 2, [execution/contracts.py](../../src/agent_runtime/execution/contracts.py) × 3).
- **6 persistence records** invoke it via the `PersistenceValueNormalizer` alias ([approvals](../../src/agent_runtime/persistence/records/approvals.py), [tools](../../src/agent_runtime/persistence/records/tools.py), [memory](../../src/agent_runtime/persistence/records/memory.py), [audit](../../src/agent_runtime/persistence/records/audit.py), [checkpoints](../../src/agent_runtime/persistence/records/checkpoints.py), [outbox](../../src/agent_runtime/persistence/records/outbox.py)).
- **1 validation shim** delegates to it ([`ValueNormalizer.redact_json_object`](../../src/agent_runtime/validation.py)).
- **4 pattern-only callers** read `Patterns.SENSITIVE_KEY` / `_VALUE` for adjacent purposes ([logging.py:123](../../src/agent_runtime/observability/logging.py), [http_logging.py:86](../../src/agent_runtime/observability/http_logging.py), [memory/contracts.py:400+403](../../src/agent_runtime/context/memory/contracts.py), [memory/policy.py:202](../../src/agent_runtime/context/memory/policy.py)).
- **1 public export** in [`observability/__init__.py`](../../src/agent_runtime/observability/__init__.py) re-exports `ObservabilityRedactor` — the new Protocol must preserve this import path or update the re-export.
- **1 test module** pins the contract ([`test_streaming_observability.py`](../../tests/unit/agent_runtime/agent/test_streaming_observability.py), 8 tests).

Total: **19 dependent code locations** (8 + 6 + 1 + 4) plus 1 export + 1 test module.

---

## 6. User flows the redactor covers

Mapping the redactor to user-visible flows from the architecture flow diagrams. Every place a payload becomes user-visible (via SSE) or stored (via persistence) is gated by this code.

### 6.1 Run streaming (flows [f1](../architecture/f1-single-turn.puml), [f2](../architecture/f2-multi-turn-tool.puml))

Every `RuntimeEventEnvelope` written by the worker passes through redaction on its `payload` and `metadata` field validators ([§5.1](#51-direct-callers-of-observabilityredactorredact_json_object) row 1). Affects every user — every SSE frame the browser sees has been through the redactor.

- `MODEL_DELTA` chunks — visible assistant prose. Goes through user-content carve-out via `delta` key.
- `TOOL_CALL` payloads — `args` is user-content (carved out for prose), but `args` _contents_ are subject to structural key scrub. A tool call literally arguing `{"password": "..."}` gets that key dropped before the user sees it.
- `TOOL_RESULT` payloads — `output` / `content` are user-content keys, so result text renders in full; nested credential keys still scrub.
- `FINAL_RESPONSE` — same shape as model deltas plus the sealed citation list.
- `RUN_*` lifecycle events — `metadata` is non-user-content, length-clipped to 2 000 chars.

### 6.2 SSE resume ([f3](../architecture/f3-sse-resume.puml))

`replay_events` re-validates the event envelopes from store before returning them to the SSE adapter. Redaction runs again on read. Means: any future change to redaction silently re-redacts historical events on replay. **Refactor implication:** if the new engine has wider recall, replay of old events will look _more_ redacted than the original SSE delivery. Acceptable but worth flagging in release notes.

### 6.3 Cancellation ([f4](../architecture/f4-cancel.puml))

`RUN_CANCELLING` and `RUN_CANCELLED` carry minimal metadata; redaction is structural only. No specific impact.

### 6.4 Citations ([f5](../architecture/f5-citations.puml))

`SOURCE_INGESTED`, `CITATION_MADE` events carry `connector`, `doc_id`, `url`, `title`. Currently these are not user-content; they're length-clipped at 2 000 chars. **Refactor watch:** the `url` field will trip a PII engine that recognizes URLs as identifiers — we must either mark the citation payload as user-content for projection purposes, or add a per-field allow-list for `url` / `doc_id` to skip PII detection. (Decision is in [§11.2](#112-phase-2-thin-credential-detector-detect-secrets).)

### 6.5 Reasoning ([f6](../architecture/f6-thinking.puml))

`REASONING_SUMMARY_DELTA` and `REASONING_SUMMARY` carry the model's reasoning text. The `summary` key is in `UserContentKeys.KEYS`, so the carve-out applies — full text renders, structural key scrub still wins. **Compliance note:** reasoning summaries can contain echoed user PII (model paraphrasing the prompt). Once we add PII detection inside user-content (PDPL requirement for some buyers), we either redact in-line or rely on `display=OMITTED` to suppress reasoning to the client. This is a config decision per buyer — see [§7](#7-compliance-constraints-uae-banks--government).

### 6.6 MCP add + auth ([f7](../architecture/f7-mcp-add.puml), [f8](../architecture/f8-mcp-auth.puml))

`MCP_AUTH_REQUIRED` event carries `auth_url` and `session_id` in payload. URL is currently not redacted (no key match, no value-regex match). Acceptable — auth URLs are session-scoped and short-lived. Approval row in persistence runs through [`persistence/records/approvals.py`](../../src/agent_runtime/persistence/records/approvals.py) redaction.

### 6.7 Usage / metrics ([f9](../architecture/f9-usage-metrics.puml))

`/v1/agent/conversations/{id}/context` returns `headroom_pct`, `used_tokens`, etc. These are integers, not strings; redaction is a no-op for scalars. The token-count allowlist exists _specifically_ to allow these counters to surface — without it, `input_tokens` would be redacted because the key contains `token`.

### 6.8 Logs (HTTP + structured)

[`logging.py`](../../src/agent_runtime/observability/logging.py) and [`http_logging.py`](../../src/agent_runtime/observability/http_logging.py) use a thinner denylist — they drop keys matching `SENSITIVE_KEY` rather than recursing. These flow into stdout (JSON) and downstream to whatever log shipper is configured. **Compliance:** logs are an audit surface — see [§7.4](#74-implications-for-this-refactor).

### 6.9 Memory writes / reads

[`MemoryRedactor`](../../src/agent_runtime/context/memory/contracts.py#L382-L410) runs on memory metadata. Memory is tenant-scoped (USER / AGENT / ORGANIZATION) and policy-controlled (only APPLICATION can write `/policies/*`). Memory PII redaction applies before storage and on every read. Today this uses memory's own pattern copy. After the refactor it points at the consolidated source.

---

## 7. Compliance constraints (UAE banks & government)

The refactor target buyers are UAE banks and government organizations. The constraints below come from the regulatory environment they sit in, not from any specific buyer contract; treat as required-by-default until a particular buyer relaxes one.

### 7.1 Regulations in scope

- **UAE Federal Decree-Law No. 45 of 2021 on Personal Data Protection (PDPL)** — UAE-wide PII regime. Applies to processing of UAE residents' personal data.
- **UAE Central Bank — Consumer Protection Regulation + Outsourcing Regulation** — banking-sector data residency, third-party processor restrictions, audit trail requirements.
- **UAE Information Assurance Standards** (formerly NESA, now under the Cyber Security Council) — government-sector controls.
- **DIFC Data Protection Law (No. 5 of 2020)** and **ADGM Data Protection Regulations 2021** — financial free-zone PII regimes that some bank entities sit under.

### 7.2 Operational requirements

These translate into hard rules for the redaction subsystem:

- **No data egress.** PII detection must run in-process. We may not call any SaaS classifier (Microsoft Presidio Cloud, AWS Comprehend, GCP DLP, OpenAI moderation, etc.). Self-hosted libraries only.
- **Source-auditable.** Every recognizer must be inspectable as code. Models (if used) must be downloadable artifacts with documented training data; no closed-weights models pulled at runtime.
- **Deterministic and explainable.** A regulator asking "why did the system redact this?" must get a precise rule citation. Black-box ML detection only acceptable when paired with a deterministic rule engine that handles required categories.
- **Open-source license without copyleft on derived work.** Apache 2.0, MIT, or BSD preferred. AGPL is a non-starter for embedded use. GPL needs case-by-case review.
- **No per-call cost.** Bank deployments are sized for high throughput; pay-per-API redaction is incompatible.
- **Vendor-neutral.** No commercial license required to operate, no vendor lock-in. Library forks must remain viable as a fallback.
- **UAE-specific identifier categories.** Emirates ID, UAE IBAN, UAE mobile, TRN, UAE passport. Generic PII tools do not cover these out of the box.
- **Auditable in production.** Redaction decisions logged with the rule that fired (without re-leaking the redacted content into the log).

### 7.3 Categories the system must redact

In addition to credentials (which the current redactor partially covers):

| Category                          | Format / examples                            |
| --------------------------------- | -------------------------------------------- |
| Email address                     | RFC 5322                                     |
| Phone number                      | E.164, with UAE prefixes (+971) prioritized  |
| Emirates ID                       | `784-YYYY-NNNNNNN-N` (15 digits, hyphenated) |
| UAE IBAN                          | `AE` + 21 digits                             |
| TRN (UAE Tax Registration Number) | 15 digits                                    |
| UAE passport                      | One letter + 8 digits (typical)              |
| Credit card number                | Luhn-validated; mask all but last 4          |
| IP address                        | IPv4 + IPv6                                  |
| Person name                       | Best-effort; ML or known-name list           |
| Postal address                    | Best-effort                                  |
| Date of birth                     | Best-effort; pattern + context               |
| API tokens / private keys / JWTs  | High-entropy strings; PEM blocks             |

The categories the model must _never_ leak even inside a user-content carve-out (because they are PDPL Sensitive Personal Data) are: government IDs, financial account numbers, biometric, health. These categories override the user-content bypass — same way `SENSITIVE_KEY` does today.

### 7.4 Implications for this refactor

- The library choice is constrained to **on-prem, open-source, free, deterministic-or-deterministic-plus-optional-ML** options. See [§8](#8-library-evaluation).
- The user-content carve-out from the current redactor cannot be a blanket "don't check anything in here." It must become "don't run the heuristic key-equals-value regex, but **do** run sensitive-category recognizers" — Emirates ID inside an assistant's message is still a leak. Tests need a new case for this.
- Logs must redact too, with the same rules. The denylist in [`logging.py`](../../src/agent_runtime/observability/logging.py) and [`http_logging.py`](../../src/agent_runtime/observability/http_logging.py) needs to be unified with the main redactor for category coverage, even if the log path remains "drop the whole key" rather than "redact the value".
- Audit log of redaction decisions is required. We need a `RedactionDecision` record (rule, category, key path, length of original) that can be exported to a SIEM. The decision record must not contain the original value.

---

## 8. Library evaluation

Options considered. Eligibility filter in [§7.4](#74-implications-for-this-refactor) — must be on-prem, open-source, free, no SaaS calls, source-auditable.

### 8.1 Microsoft Presidio (Analyzer + Anonymizer)

- License: Apache 2.0.
- Run mode: in-process Python library. Deploy on-prem.
- Coverage: ~30 PII recognizers OOTB (email, phone, credit card, IBAN, IP, person, location, date, etc.). Custom recognizers via `PatternRecognizer` and `EntityRecognizer` interfaces.
- ML: optional spaCy / transformers integration for NER. Rule-based recognizers work fine without it.
- Network: zero outbound by default. Optional ML models load from local disk.
- UAE coverage: no Emirates ID / UAE IBAN / TRN OOTB — must add as `PatternRecognizer` instances.
- Maintenance: Microsoft-maintained, active. Apache 2.0 means we can fork if needed.
- Auditability: each detection result includes `entity_type`, `start`, `end`, `score`, `analysis_explanation` — directly fits regulator-friendly logging.

**Verdict:** primary candidate for PII detection.

### 8.2 detect-secrets (Yelp)

- License: Apache 2.0.
- Run mode: in-process Python library.
- Coverage: ~20 secret types — AWS keys, GitHub tokens, JWTs, private keys, high-entropy strings, base64 blobs, etc.
- Strength: entropy-based detection plus per-secret-type plugins (verifiers). Catches secrets that don't sit next to a literal `key = …` (which is what our current regex requires).
- False positives: well-handled via baseline files + plugin filters.
- UAE coverage: not relevant — secrets are vendor-format, not country-specific.
- Maintenance: Yelp-maintained, active.
- Auditability: each finding has plugin name, line, type — clean rule citation.

**Verdict:** primary candidate for credential detection.

### 8.3 scrubadub

- License: Apache 2.0.
- Coverage: smaller than Presidio (email, phone, credit card, names, URLs). Plugin shape similar to Presidio.
- Maintenance: less active than Presidio.
- UAE coverage: would need everything custom.

**Verdict:** rejected as primary in favor of Presidio (broader OOTB coverage, more active project). Could be considered if Presidio's Python footprint becomes a problem.

### 8.4 commonregex

- License: MIT.
- Coverage: small regex bundle (email, phone, dates, money, addresses, IP, prices, links).
- Strength: tiny, no dependencies.
- Weakness: no recognizer interface; not extensible the way Presidio is.

**Verdict:** rejected; too narrow and not architecturally extensible.

### 8.5 pii-codex

- License: Apache 2.0.
- Coverage: wraps Presidio + Microsoft Common Data Types; opinionated category mapping.
- Adds PHI / GDPR category labels on top of Presidio.

**Verdict:** considered as an additional taxonomy layer if the buyer requires PDPL-mapped category labels rather than raw entity types. Defer until a buyer asks.

### 8.6 Hybrid: keep current code with improvements

- Add UAE recognizers to current regex pair.
- Improve the SENSITIVE_VALUE regex to be category-aware (one regex per PII category, not one regex for all credentials).
- Drop the `_TOKEN_COUNT_KEYS` allowlist by tightening the credential regex to require word boundaries.

**Verdict:** strictly worse than the hybrid library approach. We would still own pattern maintenance forever, and we would still ship a regex for every PII category — exactly what mature libraries already provide. Rejected.

### 8.7 Cloud SaaS (AWS Comprehend, GCP DLP, Azure Cognitive Services)

**Rejected by [§7.2](#72-operational-requirements).**

---

## 9. Decision

Adopt a **hybrid**:

1. **detect-secrets** for credential / token detection. Replaces the credential side of `SENSITIVE_KEY` and `SENSITIVE_VALUE`. Covers JWTs, AWS keys, private keys, generic high-entropy strings.
2. **Microsoft Presidio Analyzer** for PII detection. Adds the categories listed in [§7.3](#73-categories-the-system-must-redact). UAE-specific recognizers ([§9.1](#91-uae-recognizers-to-implement)) are registered at startup.
3. **In-house structural layer** preserved: user-content carve-out, sticky propagation, length clip, token-count allowlist, `[redacted]` / `[truncated]` placeholders, JSON-shape walking. This is _our_ domain logic; libraries don't ship it.
4. **Single `Redactor` Protocol** in [`observability/`](../../src/agent_runtime/observability/) that the 16 callsites depend on. The current `ObservabilityRedactor` becomes the default implementation. Future engine swaps don't touch any callsite.
5. **One canonical `Patterns` module** — eliminate the duplicate in [`context/memory/constants.py`](../../src/agent_runtime/context/memory/constants.py). Memory's [`MemoryRedactor`](../../src/agent_runtime/context/memory/contracts.py#L382) uses the consolidated module. Prompt-injection patterns move to a separate, dedicated module to break the accidental coupling in [`policy.py`](../../src/agent_runtime/context/memory/policy.py:202).
6. **No ML on the default path.** Presidio runs with rule-based recognizers only. Optional spaCy NER for person/location is gated by a setting (`REDACTION_USE_NER=true`) and ships disabled. ML models are downloaded as part of the Docker image build, never at runtime.
7. **Audit trail.** A `RedactionDecisionLogger` writes a SIEM-exportable record per redaction event (rule, category, key path, length of original — never the original value).

### 9.1 UAE recognizers to implement

Custom Presidio `PatternRecognizer` instances:

| Entity name       | Pattern                                | Notes                                                                                |
| ----------------- | -------------------------------------- | ------------------------------------------------------------------------------------ | -------------------- |
| `UAE_EMIRATES_ID` | `\b784-\d{4}-\d{7}-\d\b`               | 15-digit format with checksum digit.                                                 |
| `UAE_IBAN`        | `\bAE\d{21}\b`                         | 23 chars total.                                                                      |
| `UAE_PHONE`       | `(\+                                   | 00)971\s?[1-9]\d{1,2}[-\s]?\d{3}[-\s]?\d{4}`                                         | Mobile and landline. |
| `UAE_TRN`         | `\b\d{15}\b` (with context filter)     | 15-digit Tax Registration Number; needs context word to suppress false positives.    |
| `UAE_PASSPORT`    | `\b[A-Z]\d{8}\b` (with context filter) | One letter + 8 digits; very FP-prone, must require nearby context word ("passport"). |

These ship as a single registry module ([`observability/recognizers/uae.py`](../../src/agent_runtime/observability/recognizers/uae.py) — _to be created_) and are registered with the Presidio Analyzer at module init.

### 9.2 What the new dependency footprint looks like

```
presidio-analyzer  (Apache 2.0, ~5 MB, no network)
detect-secrets     (Apache 2.0, ~1 MB, no network)
```

No spaCy / transformers in the default path. Both are pinned via `pyproject.toml` and reviewed for license / supply-chain concerns before adoption.

---

## 10. Acceptance criteria

The refactor is done when **all** of the following hold:

### 10.1 Functional parity

- All seven existing tests in [`tests/unit/agent_runtime/agent/test_streaming_observability.py`](../../tests/unit/agent_runtime/agent/test_streaming_observability.py) pass unchanged.
- `_TOKEN_COUNT_KEYS` allowlist is _deleted_. The new credential detector does not match `input_tokens` etc. (because detect-secrets matches on entropy / token format, not key name substring).
- `Defaults.MAX_STREAM_FIELD_LENGTH`, `REDACTED`, `TRUNCATED` constants and behavior are unchanged.
- `UserContentKeys.KEYS` membership is unchanged.

### 10.2 New coverage

- New tests verify each category from [§7.3](#73-categories-the-system-must-redact) is detected outside user-content and outside structural key matches:
  - Emirates ID `784-1234-1234567-1` redacted.
  - UAE IBAN `AE070331234567890123456` redacted.
  - UAE mobile `+971 50 123 4567` redacted.
  - JWT, AWS access key, RSA private key block — all redacted.
  - Email, credit card (Luhn-valid), IPv4 — all redacted.
- New tests verify "structural sensitive categories override the user-content bypass" — Emirates ID inside an assistant `message` is still redacted.
- New test verifies `connector_scopes`, `url`, `doc_id` in citation payloads are _not_ over-redacted (per [§6.4](#64-citations-f5)).

### 10.3 Architecture

- One `Redactor` Protocol in [`observability/`](../../src/agent_runtime/observability/) with concrete implementation `LibraryBackedRedactor`.
- All 16 callsites depend on the Protocol, not the concrete class.
- `Patterns.SENSITIVE_KEY` / `_VALUE` removed from [`context/memory/constants.py`](../../src/agent_runtime/context/memory/constants.py); `MemoryRedactor` uses the consolidated source.
- Prompt-injection patterns moved out of `SENSITIVE_VALUE` into [`context/memory/prompt_injection.py`](../../src/agent_runtime/context/memory/prompt_injection.py) — _to be created_ — leaving [`policy.py`](../../src/agent_runtime/context/memory/policy.py) calling a dedicated `is_prompt_injection` checker.

### 10.4 Compliance

- `RedactionDecisionLogger` emits an audit record per redaction with: rule name, category, JSON path, original length, surrogate placeholder. Never the original value.
- Audit records flow through the existing observability pipeline ([`observability/logging.py`](../../src/agent_runtime/observability/logging.py)) so they reach whatever SIEM is configured.
- README in [`observability/recognizers/`](../../src/agent_runtime/observability/recognizers/) documents every active recognizer (name, pattern, score, source). Regulator can read this.

### 10.5 Performance

- A 1 KB JSON object with 50 keys redacts in ≤ 5 ms p95 on a single CPU core (no NER). Today's regex redactor is ~0.5 ms; the library path is allowed to be 10× slower because it's doing strictly more work.
- Hot path (`RuntimeEventEnvelope` field validator) measured via worker `RUN_COMPLETED` time before/after — total worker time per turn ≤ +5 % p95.

### 10.6 Backwards compatibility

- SSE event shape unchanged — `payload` and `metadata` are still `JsonObject`, redacted scalars still `"[redacted]"`, truncation suffix still `"[truncated]"`.
- Persistence record schemas unchanged (no migration required).
- Any difference in _which strings_ are redacted is documented in release notes; the new engine is allowed wider recall.

---

## 11. Refactor plan (phased)

Each phase ships independently, with its own PR, test coverage, and rollback path.

### 11.1 Phase 1 — introduce the Protocol; current code becomes the default impl

- Add [`observability/redactor.py`](../../src/agent_runtime/observability/redactor.py) with `Redactor` Protocol (matches the public surface of `ObservabilityRedactor`).
- `RegexRedactor` = the existing `ObservabilityRedactor`, renamed and moved behind the Protocol.
- Add a module-level `_default_redactor()` that returns a singleton `RegexRedactor`.
- Change all 16 callsites to call `_default_redactor().redact_json_object(...)` — one mechanical substitution.
- All existing tests pass; behavior is byte-identical.
- Add tests for the Protocol shape (Protocol satisfaction, swap behavior).

**Risk:** Low. Pure refactor.
**Rollback:** Revert single PR.

### 11.2 Phase 2 — thin credential detector (detect-secrets)

- Add `detect-secrets` to `requirements.txt`.
- Add [`observability/redaction_detectors/credentials.py`](../../src/agent_runtime/observability/redaction_detectors/credentials.py) with a `CredentialDetector` class that wraps detect-secrets and exposes a single `find(text: str) -> list[RedactionFinding]` method.
- New `LibraryBackedRedactor` (also implements the `Redactor` Protocol) composes: structural in-house layer + `CredentialDetector` for the value-pattern step (replaces `SENSITIVE_VALUE`).
- Behavioral test: every string that the old `SENSITIVE_VALUE` regex matched is still detected by detect-secrets. (Recall ≥ current.)
- `_TOKEN_COUNT_KEYS` allowlist deleted; detect-secrets does not match `input_tokens` etc.
- Default redactor still `RegexRedactor`. New impl gated by setting `REDACTION_BACKEND=library` (defaults to `regex`).
- Tests for the new impl run with `REDACTION_BACKEND=library` via env or fixture.

**Risk:** Medium. New library dependency; need to confirm recall against current regex before flipping default.
**Rollback:** Setting flip; library code stays for future.

### 11.3 Phase 3 — PII detection (Presidio + UAE recognizers)

- Add `presidio-analyzer` to `requirements.txt`.
- Add [`observability/recognizers/uae.py`](../../src/agent_runtime/observability/recognizers/uae.py) with the five UAE `PatternRecognizer` instances from [§9.1](#91-uae-recognizers-to-implement).
- Add [`observability/redaction_detectors/pii.py`](../../src/agent_runtime/observability/redaction_detectors/pii.py) with a `PIIDetector` class that wraps Presidio Analyzer + Anonymizer, registers UAE + standard recognizers.
- `LibraryBackedRedactor` composes `PIIDetector` _after_ `CredentialDetector` (credentials are detected first; PII finds whatever is left).
- New tests for every category in [§7.3](#73-categories-the-system-must-redact).
- New test: structural sensitive categories (Emirates ID, IBAN) override user-content bypass.
- Recognizers documented in `observability/recognizers/README.md`.

**Risk:** Medium. Presidio cold-start time matters for local dev; verify ≤ 1 s.
**Rollback:** Setting flip.

### 11.4 Phase 4 — consolidate patterns

- Move `Patterns.SENSITIVE_KEY` to a single source under [`observability/`](../../src/agent_runtime/observability/) (or delete entirely if the new engine subsumes it — verify by running tests with the regex disabled).
- Delete the duplicate `Patterns` block from [`context/memory/constants.py`](../../src/agent_runtime/context/memory/constants.py).
- Update [`MemoryRedactor`](../../src/agent_runtime/context/memory/contracts.py#L382) to use the consolidated source.
- Move prompt-injection heuristic into a dedicated [`context/memory/prompt_injection.py`](../../src/agent_runtime/context/memory/prompt_injection.py); update [`policy.py`](../../src/agent_runtime/context/memory/policy.py:202) to call it.
- Update [`logging.py`](../../src/agent_runtime/observability/logging.py:123) and [`http_logging.py`](../../src/agent_runtime/observability/http_logging.py:86) to point at the consolidated source.

**Risk:** Low–Medium. File moves + import updates.
**Rollback:** Revert.

### 11.5 Phase 5 — audit trail

- Add `RedactionDecisionLogger` in [`observability/`](../../src/agent_runtime/observability/) that emits per-decision records via the existing structured logger.
- Wire `LibraryBackedRedactor` to call the logger on every finding.
- New tests verify: log record contains rule name, category, JSON path, original length; never contains the original value.
- Documentation in [`observability/recognizers/README.md`](../../src/agent_runtime/observability/recognizers/) with regulator-readable description of every active rule.

**Risk:** Low. Additive.
**Rollback:** Revert.

### 11.6 Phase 6 — flip the default

- After phases 2-5 have run on staging for at least one week with `REDACTION_BACKEND=library` and no incidents, change the default to `library`.
- Keep `RegexRedactor` and the setting as a one-release rollback escape valve.
- Next release after that: delete `RegexRedactor`, delete the setting, delete `_TOKEN_COUNT_KEYS` (already removed in phase 2), delete the duplicated patterns.

**Risk:** Medium at flip moment.
**Rollback:** Setting flip. After deletion: revert.

---

## 12. Risks

### 12.1 Recall regression

The new engine might miss something the old regex caught. Mitigation: phase 2 includes a recall test — every string that matched the old regex must still be redacted.

### 12.2 False positives in user-content

Wider PII recall means more strings are flagged inside model output. If we redact aggressively inside user-content (because PDPL says we must), the assistant's text becomes harder to read for buyers without strict requirements. Mitigation: per-buyer toggle on the deployment profile (`PII_REDACT_USER_CONTENT={none,sensitive_only,full}`), defaulting to `sensitive_only` (Emirates ID and friends, not names/emails).

### 12.3 Cold-start time

Presidio loads recognizers at import. If this is > 1 s, local dev pain. Mitigation: lazy-load on first call; benchmark in phase 3.

### 12.4 Hot-path latency

Field validators run on every event. detect-secrets and Presidio are not free per call. Mitigation: phase 3 acceptance includes p95 ≤ 5 ms per 1 KB object; benchmark before flipping default.

### 12.5 Library supply-chain

Two new dependencies. Mitigation: pin exact versions, run dependabot, mirror to a private package index for production.

### 12.6 Coupling between memory and observability patterns

Today they accidentally share patterns. Phase 4 makes the dependency explicit. Risk: if memory has divergent requirements (e.g. tighter scrub for memory metadata than for events), forcing them onto the same pattern set could regress memory. Mitigation: read the memory tests and confirm equivalence before consolidation; if memory needs different rules, give it a separate `Redactor` instance (still using the same Protocol).

### 12.7 Replay re-redaction

[§6.2](#62-sse-resume-f3) — replaying old events through the new engine produces wider redaction than the original SSE. Acceptable but must be release-noted.

---

## 13. Unit testing requirements

Per [services/ai-backend/tests/CLAUDE.md](../../tests/CLAUDE.md):

- Fakes only; no network, no real LLM, no live secrets.
- Assert typed error class, not just "some exception".
- Assert safe public message for any error path.
- Mixin layout: fakes / fixtures / constants in mixins; concrete test classes are `test_*` only.

### 13.1 Files to add

| File                                                                       | Purpose                                                                                                                                                                                                                                             |
| -------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `tests/unit/agent_runtime/observability/test_redactor_protocol.py`         | Protocol shape; `RegexRedactor` and `LibraryBackedRedactor` both satisfy it; swap is transparent.                                                                                                                                                   |
| `tests/unit/agent_runtime/observability/test_credential_detector.py`       | detect-secrets wrapper; recall against the old `SENSITIVE_VALUE` cases; entropy detection on JWT / AWS / private-key strings; absence of false positives on `input_tokens` and friends.                                                             |
| `tests/unit/agent_runtime/observability/test_pii_detector.py`              | Presidio wrapper; each category from [§7.3](#73-categories-the-system-must-redact) detected; UAE recognizers fire on UAE-format strings; non-UAE-format strings do not fire on UAE recognizers.                                                     |
| `tests/unit/agent_runtime/observability/test_library_backed_redactor.py`   | End-to-end: every test in `test_streaming_observability.py` passes against `LibraryBackedRedactor`; new structural-override-of-user-content test (Emirates ID inside `message` is still redacted); citation `url` / `doc_id` are not over-redacted. |
| `tests/unit/agent_runtime/observability/test_redaction_decision_logger.py` | Per-decision record contains rule + category + path + length; never contains the original value; integrates with the existing structured logger fake.                                                                                               |
| `tests/unit/agent_runtime/context/memory/test_pattern_consolidation.py`    | After phase 4, memory's `MemoryRedactor` produces equivalent output to the old memory-local pattern path on the existing memory test cases.                                                                                                         |

### 13.2 Files to update

| File                                                             | Change                                                                                                      |
| ---------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------- |
| `tests/unit/agent_runtime/agent/test_streaming_observability.py` | Add the structural-override-of-user-content cases. Existing tests unchanged.                                |
| Any test that asserts on `_TOKEN_COUNT_KEYS` membership directly | Update to assert that the new engine doesn't redact `input_tokens` etc., regardless of allowlist mechanism. |

### 13.3 Performance benchmark

A repeatable script in `tests/perf/` (new directory) that:

- Runs 10 000 redactions of a representative `RuntimeEventEnvelope.payload` shape.
- Reports p50 / p95 latency per redaction.
- Asserts p95 ≤ 5 ms.
- Comparable run with `RegexRedactor` reports baseline.

Not part of CI (too slow / variance-sensitive); run manually or on a perf-tracking branch.

---

## 14. Migration & rollout

1. Phases 1, 4, 5 are pure code refactors with no behavior change — ship anytime.
2. Phases 2 and 3 introduce the library backend behind a setting (`REDACTION_BACKEND=regex|library`), default `regex`. Ship to staging, soak for a week, monitor:
   - Worker `RUN_COMPLETED` p95 latency (see [§10.5](#105-performance)).
   - Number of redactions per minute, broken down by rule (via the new audit logger).
   - Any user reports of "the assistant message looks weird" (proxy for over-redaction in user-content).
3. Phase 6 flips the default. Same monitoring carries forward.
4. One release after phase 6: delete the legacy code path.

### 14.1 Settings to add

```
REDACTION_BACKEND=library          # regex | library; default after phase 6: library
REDACTION_USE_NER=false            # opt-in spaCy NER for person/location
PII_REDACT_USER_CONTENT=sensitive_only  # none | sensitive_only | full
REDACTION_AUDIT_LOG=true           # emit per-decision records
```

All settings are deployment-profile aware (see [`agent_runtime/deployment/profile.py`](../../src/agent_runtime/deployment/profile.py)) so a regulated profile can pin them on.

### 14.2 Release notes (template)

> **Redaction backend swap.** The redaction engine now uses Microsoft Presidio (PII) and Yelp's detect-secrets (credentials) on top of our existing structural redaction logic. New categories are detected: Emirates ID, UAE IBAN, UAE mobile, TRN, UAE passport, JWTs, AWS keys, private keys, emails, credit cards, IPs. The wire shape (`[redacted]` placeholder, `[truncated]` suffix, `RuntimeEventEnvelope` schema) is unchanged. SSE replays of old events now go through the new engine — strings that the old regex missed will appear redacted on replay. To revert, set `REDACTION_BACKEND=regex` for one release.

---

## 15. Open questions

- **Per-buyer category override.** Some banks may require that we redact _more_ (e.g. all account numbers including non-UAE format) and some may require _less_ (e.g. allow names in user-content). How do we expose that without per-buyer code branches? Likely: each `Redactor` is constructed from a `RedactionPolicy` Pydantic model resolved from the deployment profile — but the shape needs design before phase 3.
- **Reversible redaction for legal hold.** PDPL allows data-subject access requests. If a user asks "what did I say in this conversation," we can't show them `[redacted]`. Today that's not a concern because we redact for _display_, not at rest — except for the persisted records ([§5.2](#52-persistence-records-via-the-persistencevaluenormalizerredact_json_object-alias)). Verify whether persistence records hold pre- or post-redaction values; if post-, we are losing data we may legally have to return. **This is a code-read task before phase 1.**
- **Logs via `logging.py` / `http_logging.py`.** Today they drop sensitive keys but don't redact PII inside string values. After this refactor, do logs go through the same `Redactor`? Probably yes for compliance, but it changes the log volume and shape — needs separate sign-off from whoever owns the log shipper config.
- **`MemoryRedactor` parity.** Memory's own redactor has a slightly different rule set (no `credential` keyword, scrubs the value to `[redacted]` rather than recursing). After consolidation, memory output may change. Confirm acceptable with the memory subsystem owner.
- **Audit-record retention.** Where do `RedactionDecision` records live? Re-using the `audit_log` table avoids a new table; but the audit log is itself a refactor target ([refactor-audit.md §1.3](../architecture/refactor-audit.md#13-custom-hash-chained-audit-log)) — better to land this somewhere we don't immediately deprecate.
- **Conversation `url`, `doc_id` from citations.** The Presidio URL recognizer will fire on every citation URL — this is correct (URL is PII per some regimes) but useless for our use case. Either (a) mark citation payloads as `user_content` (already happens for `output`-shaped payloads, but the citations array sits at top level), or (b) add a per-key allow-list `RedactionAllowKeys.URL_FIELDS = {"url", "doc_id"}`. Pick before phase 3.

---

_This document captures the plan, not the implementation. Implementation begins after sign-off and lands as the six PRs described in [§11](#11-refactor-plan-phased)._
