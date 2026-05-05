# Decomp — `agent_runtime/delegation/subagents/contracts.py`

Source: [services/ai-backend/src/agent_runtime/delegation/subagents/contracts.py](../../../services/ai-backend/src/agent_runtime/delegation/subagents/contracts.py) — **524 LOC, L.** Pydantic contracts for subagent definitions, handoffs, results, and async lifecycle state. Mostly type definitions with **strict shape invariants** (exactly-one-outcome, required-summaries-on-success, ordered-timestamps).

## A. Top-level structure

| Symbol                                       |   Lines | Purpose                                                                                                                                                                                        |
| -------------------------------------------- | ------: | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `OutputSchema`, `ResultMetadata` (TypeAlias) |   33–34 | Shared aliases.                                                                                                                                                                                |
| `SubagentTransport(StrEnum)`                 |   37–41 | `ASGI`, `HTTP`.                                                                                                                                                                                |
| `AsyncTaskStatus(StrEnum)`                   |   44–52 | 6 states: `QUEUED`, `RUNNING`, `SUCCEEDED`, `FAILED`, `CANCELLED`, `TIMED_OUT`.                                                                                                                |
| `SubagentErrorCode(StrEnum)`                 |   55–66 | 9 typed errors: `SUBAGENT_UNAVAILABLE`, `CONCURRENCY_LIMIT_EXCEEDED`, `TIMEOUT`, `CANCELLED`, `STALE_TASK_ID`, `MALFORMED_RESULT`, `OVERSIZED_RESULT`, `RUNNER_ERROR`, `VALIDATION_ERROR`.     |
| `RuntimeContextReference(RuntimeContract)`   |   69–98 | Compact `(user_id, org_id, trace_id, permission_scopes)` reference. `from_context` factory.                                                                                                    |
| `SubagentDefinition(RuntimeContract)`        | 101–155 | **Model-visible subagent metadata.** 9 fields: name, description, graph_id, transport, tools, skills, required_scopes, timeout, concurrency.                                                   |
| `SubagentOutputContract(RuntimeContract)`    | 158–192 | `format`, `required_fields={response, execution_summary, plan_summary}`, optional `json_schema`.                                                                                               |
| `SubagentTask(RuntimeContract)`              | 195–228 | **The compact handoff.** Excludes raw conversation history; carries objective + summary + constraints + capability narrowings + output_contract.                                               |
| `SubagentArtifact(RuntimeContract)`          | 231–248 | `name`, `artifact_type="text"`, `reference` (URI/blob ref).                                                                                                                                    |
| `SubagentError(RuntimeContract)`             | 251–277 | Safe error: code, safe_message, retryable, task_id?, correlation_id (auto-uuid).                                                                                                               |
| `SubagentResult(RuntimeContract)`            | 280–383 | **Validated subagent output**: response, execution_summary, plan_summary, artifacts, recent_messages, error. **Exactly-one-outcome** + required-summaries-on-success. `ok` / `fail` factories. |
| `AsyncSubagentLaunch(RuntimeContract)`       | 386–402 | `thread_id`, `run_id`, `status` ∈ {QUEUED, RUNNING}.                                                                                                                                           |
| `AsyncTaskState(RuntimeContract)`            | 405–433 | Persistent task state: task_id, subagent_name, thread_id, run_id, status, created_at, updated_at, deadline_at?. **Timestamp ordering invariants.**                                             |
| `AsyncTaskLifecycleResult(RuntimeContract)`  | 436–494 | Envelope for start/check/update/cancel/list. **Exactly-one-of {state, tasks, error}**, plus optional `result` only when state is set.                                                          |
| `SubagentValueNormalizer`                    | 497–524 | Re-export shim + length-bounded `normalize_id` (uses `Limits.ID_MAX_LENGTH` + `Patterns.ID`).                                                                                                  |

## B. Feature inventory

| Domain                                          | Symbols                                                             |  LOC |
| ----------------------------------------------- | ------------------------------------------------------------------- | ---: |
| **StrEnum vocabulary**                          | `SubagentTransport`, `AsyncTaskStatus`, `SubagentErrorCode`         |  ~30 |
| **Subagent definition (model-visible)**         | `SubagentDefinition`, `SubagentOutputContract`                      |  ~95 |
| **Handoff envelope (capability-narrowed task)** | `SubagentTask`, `RuntimeContextReference`                           |  ~65 |
| **Result + artifact + error**                   | `SubagentResult`, `SubagentArtifact`, `SubagentError`               | ~155 |
| **Async lifecycle state**                       | `AsyncSubagentLaunch`, `AsyncTaskState`, `AsyncTaskLifecycleResult` | ~110 |
| **Validation glue**                             | `SubagentValueNormalizer`                                           |  ~30 |

## C. Functional spec per domain

### `SubagentDefinition` — supervisor's view of available subagents

Supervisor sees:

- `name`, `description` (1–DESCRIPTION_MAX_LENGTH chars)
- `tools`, `skills` (slug-sets) — the subagent's full capability surface.
- `required_scopes` — permissions the supervisor must have to invoke this subagent.
- `timeout_seconds` — bounded by `TIMEOUT_MAX_SECONDS`.
- `concurrency_limit` — bounded by `CONCURRENCY_LIMIT_MAX`.

The actual capability **narrowing** at handoff time is `SubagentTask.allowed_tools` ∩ `SubagentDefinition.tools` (computed by [`handoff.py`](subagents-bundle.md)).

### `SubagentTask` — the compact handoff

Crucial design decision (197): "intentionally excludes raw conversation history." The supervisor extracts the relevant summary AND constraints AND outputs the model-visible task. The subagent doesn't see prior turns directly.

Fields:

- `objective`, `relevant_summary` — both required, 1–`TASK_TEXT_MAX_LENGTH` chars.
- `constraints` — tuple of strings.
- `runtime_context_ref` — only `(user_id, org_id, trace_id, permission_scopes)` — NOT the full context.
- `allowed_tools`, `allowed_skills` — narrowed slug-sets.
- `output_contract` — `SubagentOutputContract` with default required fields.

### `SubagentResult` — exactly-one-outcome + required summaries

`_validate_result_shape` (329–341):

1. `len(artifacts) <= ARTIFACTS_MAX_COUNT` — bounds artifact count.
2. **Exactly-one-of `response` / `error`** — XOR check on None-ness.
3. **If response is set, `execution_summary` AND `plan_summary` must also be set** — partial successes don't pass validation.

`recent_messages` validation (309–327):

- Each message normalized non-empty.
- Count ≤ `RECENT_MESSAGES_MAX_COUNT`.
- Each message length ≤ `RECENT_MESSAGE_MAX_LENGTH`.
- Caps prevent oversized handoff results from inflating the supervisor's prompt.

### `AsyncTaskState` — timestamp ordering

`_validate_timestamps` (427–433):

- `updated_at >= created_at` — clocks always move forward.
- `deadline_at > created_at` (if set) — deadline must be in the future relative to creation.

### `AsyncTaskLifecycleResult` — exactly-one-of {state, tasks, error}

`_require_one_lifecycle_outcome` (444–453):

- `sum((has_state, has_tasks, has_error)) == 1` — strict mutual exclusion.
- `result` is only valid when `state` is set (i.e. task-finished envelopes).

Used as the return shape for every lifecycle method on `SubagentRunner` (start / check / update / cancel / list).

### `AsyncSubagentLaunch` status restriction

`_validate_launch_status` (398–402): launch status must be `QUEUED` or `RUNNING`. A launch that's already `SUCCEEDED`/`FAILED`/etc would be a bug — the runner would never observe a launched task in a terminal state.

### `SubagentValueNormalizer.normalize_id` length bound

Unlike the shared `ValueNormalizer.normalize_id` exposed elsewhere, this overload (515–524) **adds an `ID_MAX_LENGTH` cap** and matches against `Patterns.ID`. Subagent ids are constrained more tightly than runtime ids.

## D. Bugs / edge cases / invariants

- **Compact handoff excludes conversation history** (197): defends against prompt budget exhaustion at the subagent boundary.
- **Capability narrowing is opt-in via `allowed_tools/skills`** (202–203): empty allowlist means "no tools/skills available". Defends against accidentally exposing the full registry to a narrowed subagent.
- **Required summaries on success** (337–340): supervisor can't get an answer without an execution summary AND plan summary. Drives subagent design to always produce both.
- **Exactly-one-outcome** (329–341, 444–453, 398–402): three independent classes enforce mutual-exclusion patterns. Repeated boilerplate but explicit.
- **Timestamp ordering** (427–433): defends against clock-skew bugs.
- **`deadline_at <= created_at` rejection** (431): a deadline-before-creation is meaningless.
- **`recent_messages` count + length caps** (322–326): prevents a malicious / buggy subagent from returning a 10000-message dump.
- **`response` length cap** (`RESULT_RESPONSE_MAX_LENGTH`, 284): bounds the assistant-visible answer text.
- **`json_schema` is loosely-typed** (185–192): only checks `isinstance(value, Mapping)`. Doesn't validate against JSON Schema spec — full validation lives in the renderer.
- **`SubagentArtifact.reference` is a string** (236): it's a URI / pointer / blob path. The artifact is NOT embedded — defends against oversized message blobs.
- **`SubagentDefinition.tools` and `skills` default to empty frozensets** (111–112): a definition that doesn't list any capabilities means the subagent can't use any.
- **`required_scopes` defaults empty** (113): no required scopes by default — definitions must opt in to scope gates.
- **`enabled = True` default** (122): definitions are visible to the supervisor by default; opt out via `enabled=False`.
- **`SubagentValueNormalizer.normalize_id` length cap** (518): the only normalizer that ADDS a cap on top of the shared base.

## E. Hardcoded vs configurable

### Hardcoded

- All enum vocabulary.
- `SubagentTask.objective` / `relevant_summary` non-empty.
- Default required fields `{response, execution_summary, plan_summary}` (162–169).
- "launch status must be queued or running" message.

### Configurable (via `Limits` constants in `subagents/constants.py`)

- `DESCRIPTION_MIN_LENGTH`, `DESCRIPTION_MAX_LENGTH`
- `TIMEOUT_MAX_SECONDS`
- `CONCURRENCY_LIMIT_MAX`
- `TASK_TEXT_MAX_LENGTH`
- `RESULT_RESPONSE_MAX_LENGTH`
- `SUMMARY_MAX_LENGTH`
- `SAFE_MESSAGE_MAX_LENGTH`
- `RECENT_MESSAGES_MAX_COUNT`, `RECENT_MESSAGE_MAX_LENGTH`
- `ARTIFACTS_MAX_COUNT`
- `ID_MAX_LENGTH`

### Configurable (via `Defaults`)

- `Defaults.SUBAGENT_TIMEOUT_SECONDS`
- `Defaults.SUBAGENT_CONCURRENCY_LIMIT`
- `Defaults.OUTPUT_FORMAT`

## F. External dependencies and coupling

### Internal

- `agent_runtime.execution.contracts.AgentRuntimeContext`, `JsonScalar`, `RuntimeContract`.
- `agent_runtime.delegation.subagents.constants` — `Defaults`, `Limits`, `Messages`, `Patterns`, `Values`, `_Fields`.
- Lazy: `agent_runtime.validation.ValueNormalizer` (in `SubagentValueNormalizer` shim).

### Stdlib / third-party

- `pydantic.Field`, `PositiveInt`, `ValidationInfo`, `field_validator`, `model_validator`.
- `enum.StrEnum`, `uuid.uuid4`, `datetime`.

## G. Suggested decomposition seams

1. **`subagent_enums.py`** — three StrEnums. ~30 LOC.
2. **`subagent_definition.py`** — `SubagentDefinition`, `SubagentOutputContract`. ~95 LOC.
3. **`subagent_task.py`** — `SubagentTask`, `RuntimeContextReference`. ~65 LOC.
4. **`subagent_result.py`** — `SubagentResult`, `SubagentArtifact`, `SubagentError`. ~155 LOC. The exactly-one-outcome invariant lives here.
5. **`async_lifecycle.py`** — `AsyncSubagentLaunch`, `AsyncTaskState`, `AsyncTaskLifecycleResult`. ~110 LOC.
6. **`subagent_validation.py`** — `SubagentValueNormalizer` (the only normalizer with an ID length cap). ~30 LOC.

The **exactly-one-outcome invariant** appears in 3 places (`SubagentResult` lines 329–341, `AsyncTaskLifecycleResult` lines 444–453, `McpToolCallResult` and `McpLoadResult` in [mcp-cards.md](../capabilities/mcp-cards.md)). A shared mixin or generic `OneOf[A, B, C]` type would deduplicate.

The **required summaries on success** rule (337–340) is the strongest design decision in this file — codifying that "no answer is valid without a plan and execution trace." Worth preserving prominently in any refactor.
