# Decomp — `agent_runtime/execution/contracts.py`

Source: [services/ai-backend/src/agent_runtime/execution/contracts.py](../../../services/ai-backend/src/agent_runtime/execution/contracts.py) — **534 LOC, L.** The single source-of-truth for the runtime foundation's typed surface. Defines every Pydantic model that flows through the runtime: contexts, model config, error envelopes, stream events, dependency-injection ports envelope, and the StrEnum vocabulary.

## A. Top-level structure

| Symbol                                                       |   Lines | Purpose                                                                                                                                                                |
| ------------------------------------------------------------ | ------: | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")` |      33 | Identifier regex shared across all id fields.                                                                                                                          |
| Type aliases `JsonScalar`, `JsonValue`, `JsonObject`         |   35–37 | Recursive JSON typing.                                                                                                                                                 |
| `RuntimeContract(BaseModel)`                                 |   40–43 | **Base model with `extra="forbid", frozen=True, validate_assignment=True`.** Every model in this file inherits.                                                        |
| `FeatureFlag(StrEnum)`                                       |   46–54 | 6 gates: `DYNAMIC_TOOL_LOADING`, `SKILLS_MIDDLEWARE`, `DYNAMIC_MCP_LOADING`, `CONTEXT_MEMORY`, `SUBAGENTS`, `STREAMING_OBSERVABILITY`.                                 |
| `RuntimeErrorCode(StrEnum)`                                  |   57–69 | 10 typed error classes safe for public surfaces.                                                                                                                       |
| `RuntimeRunStatus(StrEnum)`                                  |   72–78 | Product-visible run status: `accepted`, `running`, `succeeded`, `failed`.                                                                                              |
| `StreamEventSource(StrEnum)`                                 |   81–91 | 8 sources: `MAIN_AGENT`, `SUBAGENT`, `TOOL`, `MCP`, `SUMMARIZATION`, `SYSTEM`, `RUNTIME`, `MODEL`.                                                                     |
| `StreamEventType(StrEnum)`                                   |  94–106 | 10 event types: `progress`, `tool_call`, `tool_result`, `custom`, `lifecycle`, `subagent_update`, `observation`, `error`, `final`, `final_response`.                   |
| `StreamSource = StreamEventSource` (alias)                   |     109 | Backwards-compat alias.                                                                                                                                                |
| `ModelReasoningEffort(StrEnum)`                              | 112–120 | `none`/`minimal`/`low`/`medium`/`high`/`xhigh`.                                                                                                                        |
| `ModelReasoningSummary(StrEnum)`                             | 123–128 | OpenAI: `auto`/`concise`/`detailed`.                                                                                                                                   |
| `ModelReasoningDisplay(StrEnum)`                             | 131–135 | Anthropic: `omitted`/`summarized`.                                                                                                                                     |
| `ModelThinkingMode(StrEnum)`                                 | 138–142 | Anthropic: `enabled`/`adaptive`.                                                                                                                                       |
| `ModelReasoningConfig(RuntimeContract)`                      | 145–164 | Reasoning + thinking controls. **Validates `budget_tokens` cannot be set with `thinking_mode=adaptive`** (157–164).                                                    |
| `ModelConfig(RuntimeContract)`                               | 167–190 | Model + provider + token caps + temperature + reasoning. `max_input_tokens ≤ 2_000_000`, `timeout_seconds ∈ (0, 600]`, `temperature ∈ [0, 2]`.                         |
| `RuntimeRunContext(RuntimeContract)`                         | 193–218 | Product-owned IDs propagated through LangGraph: `request_id`, `run_id`, `trace_id`, `parent_trace_id`, `started_at`, `metadata`.                                       |
| `RuntimeRunHandle(RuntimeContract)`                          | 221–248 | Small response from run create. `from_context` factory.                                                                                                                |
| `AgentRuntimeContext(RuntimeContract)`                       | 251–334 | **The big one.** Request-level identity, authorization, model, trace context. 13 fields.                                                                               |
| `AgentRuntimeContext.run_context` (property)                 | 323–334 | Synthesize a `RuntimeRunContext` from this context.                                                                                                                    |
| `RuntimeDependencies(RuntimeContract)`                       | 337–383 | DI ports envelope: registries, factories, optional skill registry / prior tool result loader / subagent artifacts backend.                                             |
| `RuntimeErrorEnvelope(RuntimeContract)`                      | 386–428 | User-safe serialized error: `code`, `safe_message` (≤500), `retryable`, `correlation_id`. `from_exception` factory dispatches `AgentRuntimeError` to its own envelope. |
| `StreamEvent(RuntimeContract)`                               | 431–464 | Normalized + redacted event: id, source, type, trace, payload, metadata, timestamp.                                                                                    |
| `StreamValueNormalizer`                                      | 467–479 | Re-export shim over `agent_runtime.validation.ValueNormalizer`.                                                                                                        |
| Module-level `_normalize_*` functions                        | 482–534 | Lazy-import wrappers around `ValueNormalizer` methods.                                                                                                                 |

## B. Feature inventory

| Domain                                                                       | Symbols                                                                                                                                                                                      |  LOC |
| ---------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---: |
| **StrEnum vocabulary (10 enums)**                                            | `FeatureFlag`, `RuntimeErrorCode`, `RuntimeRunStatus`, `StreamEventSource`, `StreamEventType`, `ModelReasoningEffort`, `ModelReasoningSummary`, `ModelReasoningDisplay`, `ModelThinkingMode` |  ~80 |
| **Model config (provider-neutral reasoning + selection)**                    | `ModelReasoningConfig`, `ModelConfig`                                                                                                                                                        |  ~50 |
| **Runtime context + identity (request/run/trace IDs + permissions + roles)** | `AgentRuntimeContext`, `RuntimeRunContext`, `RuntimeRunHandle`                                                                                                                               | ~150 |
| **Dependency injection envelope**                                            | `RuntimeDependencies` + `_validate_protocol`                                                                                                                                                 |  ~50 |
| **Error envelope + factory**                                                 | `RuntimeErrorEnvelope`                                                                                                                                                                       |  ~45 |
| **Stream event envelope**                                                    | `StreamEvent`                                                                                                                                                                                |  ~35 |
| **Validation glue**                                                          | `RuntimeContract` base, `_ID_PATTERN`, `_normalize_*` helpers, `StreamValueNormalizer` shim                                                                                                  |  ~75 |

## C. Functional spec per domain

### `RuntimeContract` base config

`extra="forbid"`: unknown fields are validation errors (defends against client-side typos masquerading as silently-ignored fields).
`frozen=True`: instances are immutable post-construction.
`validate_assignment=True`: even with frozen, internal helpers that try to assign are validation-checked.

### `_ID_PATTERN` (33)

`^[A-Za-z0-9][A-Za-z0-9._:-]*$` — must start with alphanumeric; subsequent characters from `[A-Za-z0-9._:-]`. Defends against URL-injection / SQL-injection / log-injection in identifiers.

Used in:

- `AgentRuntimeContext.user_id`/`org_id` validation (273).
- `RuntimeErrorEnvelope.correlation_id` validation (403).
- All `_normalize_runtime_id` calls (492–494).

### `ModelReasoningConfig` invariants

`thinking_mode == ADAPTIVE` AND `budget_tokens != None` → `ValueError("budget_tokens cannot be set when thinking_mode is adaptive")` (157–164). The two are mutually exclusive — adaptive thinking decides budget at runtime.

### `AgentRuntimeContext` validation rules

- `user_id`/`org_id`: non-empty AND match `_ID_PATTERN` (269–276).
- `roles`: slug-set, **must be non-empty** (`require_non_empty=True`, 281).
- `permission_scopes`: scope-set (allows colon-separated scopes like `mcp:write`).
- `connector_scopes`: `dict[connector_slug, frozenset[scope]]`. Keys go through slug normalization.
- `request_id`/`run_id`/`trace_id`: uuid4 default; if provided, must pass `_ID_PATTERN`.
- `parent_trace_id`: optional but validated when present.
- `max_parallel_tasks`: `PositiveInt`, ≤ 100.
- `trace_metadata`: `before` validator runs `ObservabilityRedactor.redact_json_object` — **PII / secret redaction at the boundary** (318–321).
- `feature_flags`: frozenset of `FeatureFlag` enum values.

### `RuntimeDependencies` protocol-conformance check (365–383)

For 4 specific fields (`tool_registry`, `mcp_registry`, `memory_backend_factory`, `subagent_catalog`), validate that the value has a callable method:

- `tool_registry.list_available_tools`
- `mcp_registry.list_available_servers`
- `memory_backend_factory.create`
- `subagent_catalog.list_available_subagents`

This is **structural typing enforcement** at the Pydantic boundary — caught at runtime, not just by mypy.

`arbitrary_types_allowed=True` (345) is required because the registries are Protocol classes.

### `RuntimeErrorEnvelope.from_exception` (408–428)

If exc is `AgentRuntimeError` → delegate to `exc.to_envelope(correlation_id)` so typed errors preserve their code + retryability + safe message.

Else → wrap as a generic `RUNTIME_FACTORY_ERROR` with safe message "The runtime could not complete the request safely." Auto-generates correlation_id.

`AgentRuntimeError` is **lazy-imported inside the method** (418) to avoid a circular import.

### `StreamEvent` redaction at validation (457–464)

Both `payload` and `metadata` go through `ObservabilityRedactor.redact_json_object` in `before` mode — every event is auto-redacted on construction, no caller can construct a leak.

## D. Bugs / edge cases / invariants

- **`extra="forbid"` everywhere** (43): typo'd field names raise validation errors instead of silently being ignored. Defends against versioning-skew bugs.
- **`frozen=True` everywhere** (43): post-construction mutation impossible. Models are pure values.
- **PII redaction at boundary** (320–321, 458–464): `trace_metadata` and `payload`/`metadata` on stream events are auto-redacted. Caller can't construct an unredacted event.
- **`_ID_PATTERN` first-char restriction**: must start with `[A-Za-z0-9]` (not `.`, `:`, `-`). Stricter than allowed-chars set.
- **`budget_tokens` cap of 2,000,000** (152): hard upper bound; provider could go higher but we cap.
- **`max_input_tokens` cap of 2,000,000** (172): same cap.
- **`timeout_seconds` cap of 600** (173): 10-minute per-call max. Defends against worker-stuck-on-LLM bugs.
- **`temperature` clamp [0, 2]** (174): provider-agnostic.
- **`max_parallel_tasks ≤ 100`** (265): prevents resource exhaustion from runaway concurrent subagents.
- **Roles required non-empty** (281): a session with zero roles is invalid. Defends against authorization-by-default-empty.
- **`_normalize_runtime_id` accepts `None` → uuid4** (489–490): missing IDs auto-generate. `RuntimeRunContext`/`AgentRuntimeContext` use `default_factory=lambda: uuid4().hex` so this branch handles **explicit None** passed by callers.
- **Protocol-conformance via callable check** (365–383): structural typing enforcement at runtime. If a fake registry forgets `list_available_tools`, validation fails.
- **`from_exception` lazy-imports `AgentRuntimeError`** (418): defends against circular import.
- **`StreamValueNormalizer` shim** (467–479): explicit `del _V` to prevent class-attribute leakage of the lazy-imported helper.
- **Module-level `_normalize_*` wrappers** (482–534): all lazy-import `ValueNormalizer` inside the function body. The lazy imports defer the dependency on `agent_runtime.validation` until first use, breaking import cycles.
- **`StreamEvent.parent_task_id` independently validated** (448–455): nullable + `_ID_PATTERN`. Subagent events can have None `parent_task_id` (top-level main-agent events).

## E. Hardcoded vs configurable

### Hardcoded

- `_ID_PATTERN` regex.
- All enum vocabulary.
- Field length caps: `model_name ≤ 200`, `safe_message ≤ 500`.
- Token caps: `budget_tokens ≤ 2M`, `max_input_tokens ≤ 2M`.
- `timeout_seconds ≤ 600`, `max_parallel_tasks ≤ 100`.
- `temperature ∈ [0, 2]`.

### Configurable

- All field values are caller-supplied.
- Enum extension requires code change (StrEnum).

## F. External dependencies and coupling

### Internal

- `agent_runtime.execution.ports` — `McpRegistry`, `MemoryBackendFactory`, `SubagentCatalog`, `ToolRegistry` (Protocol classes).
- `agent_runtime.observability.constants.Keys`.
- `agent_runtime.observability.redaction.ObservabilityRedactor`.
- `agent_runtime.observability.tracing.TraceContext`.
- `agent_runtime.capabilities.skills.sources.SkillSourceConfig`.
- Lazy-imported: `agent_runtime.validation.ValueNormalizer`, `agent_runtime.execution.errors.AgentRuntimeError`.

### Stdlib / third-party

- `pydantic.BaseModel`, `ConfigDict`, `Field`, `PositiveInt`, `ValidationInfo`, `field_validator`, `model_validator`.
- `enum.StrEnum`, `re`, `uuid.uuid4`, `datetime`.

## G. Suggested decomposition seams

This file is the foundation; cuts should be conservative. Possible:

1. **`enums.py`** — all 10 StrEnums + `_ID_PATTERN`. ~85 LOC.
2. **`json_typing.py`** — `JsonScalar`, `JsonValue`, `JsonObject`. ~5 LOC. Could move to a shared utility.
3. **`runtime_contract.py`** — `RuntimeContract` base + the lazy-import `_normalize_*` helpers. ~60 LOC.
4. **`model_config.py`** — `ModelReasoningConfig`, `ModelConfig`. ~50 LOC.
5. **`run_context.py`** — `RuntimeRunContext`, `RuntimeRunHandle`, `AgentRuntimeContext`. ~150 LOC.
6. **`runtime_dependencies.py`** — `RuntimeDependencies` + protocol validators. ~50 LOC.
7. **`error_envelope.py`** — `RuntimeErrorEnvelope`. ~45 LOC.
8. **`stream_event.py`** — `StreamEvent` + `StreamValueNormalizer`. ~50 LOC.

The **cluster-of-uses** pattern: every other module imports specific types from this file. Splitting along the cuts above would preserve import paths via `from agent_runtime.execution.contracts import X` re-exports if needed.

The `_normalize_*` module-level wrappers (482–534) all lazy-import `ValueNormalizer` — that lazy-import scaffolding is the smell signaling the import-cycle workaround. A "load-bearing module" refactor that breaks the cycle (e.g. moving `ValueNormalizer` to `service-contracts`) would let these wrappers go.
