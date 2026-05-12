# Testing Guide

How to test `ai-backend` code. Covers structure, required test types, fixtures,
the edge-case matrix, and the PR checklist.

See also:

- [architecture/02-contracts.md](../architecture/02-contracts.md) — Pydantic contracts to validate
- [architecture/00-system-map.md](../architecture/00-system-map.md) — module boundaries to mirror in tests

---

## Core principle

Prove contracts, policies, and state transitions without live LLMs, live MCP servers,
real credentials, or network. Fake at the **adapter** level — not at the code under test.

---

## Test directory layout

Mirror the subpackage paths under `tests/unit/`:

```
tests/unit/
  agent_runtime/
    capabilities/
      tools/            ← unit tests for tool registry, loader, permissions
      mcp/              ← unit tests for MCP registry, middleware
      skills/           ← unit tests for skill loader, manifest, policy
    execution/          ← unit tests for factory, provider adapters
    context/memory/     ← unit tests for memory backends, policy
    delegation/subagents/ ← unit tests for runner, handoff, definitions
    api/                ← unit tests for coordinators, event producer
    persistence/        ← unit tests for records, schema
    observability/      ← unit tests for redactor, usage recorder
    budgets/            ← unit tests for enforcer, estimator, charger
  runtime_worker/
    handlers/           ← unit tests for run, cancel, approval handlers
    jobs/               ← unit tests for sweeper jobs
  runtime_api/
    schemas/            ← unit tests for event presentation projection
    sse/                ← unit tests for SSE adapter
```

Run with the service's own venv:

```bash
cd services/ai-backend && .venv/bin/python -m pytest tests/unit/
```

---

## Six-layer test model

Every feature area needs tests across the layers that apply to it:

| Layer                       | What to test                                                                                                                                  |
| --------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------- |
| **1 — Contract validation** | `MyModel.model_validate(valid_input)` passes; `model_validate(invalid_input)` raises `ValidationError` with the right field                   |
| **2 — Policy**              | `is_authorized()`, `is_card_authorized()`, `is_server_card_visible()` returns correct decision for each permission / scope / role combination |
| **3 — Registry**            | List returns expected items; lookup finds by name; duplicate registration raises; disabled items filtered; collision policy is deterministic  |
| **4 — Middleware**          | Pass-through with correct args; transformation on valid input; denial on gate condition; does not call `next` when denied                     |
| **5 — State transitions**   | Run status moves from QUEUED → RUNNING → COMPLETED; approval from PENDING → APPROVED; budget reservation → charge → release                   |
| **6 — Backend-backed**      | Adapter method called with correct args; result mapped to record; external failure raises typed domain error with safe message                |

---

## Required tests per component

| Component                     | Must test                                                                                                                                    |
| ----------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------- |
| Pydantic model                | Valid parse, invalid parse (each required field), constrained field violations                                                               |
| Registry                      | List, lookup by name, duplicate registration, disabled filtering, collision policy                                                           |
| Loader                        | Success path, unknown name → typed error, unauthorized → typed error, malformed schema → typed error, external service failure → typed error |
| Middleware                    | Pass-through, transformation, gate denial (does not call next)                                                                               |
| State machine                 | Every allowed transition; forbidden transitions raise typed errors                                                                           |
| Handler (run/cancel/approval) | Happy path, ownership mismatch (no-op), wrong state transition (no-op or typed error)                                                        |
| Event producer                | Event type correct, payload shape correct, visibility correct, presentation fields projected                                                 |
| Adapter                       | Port method signature, idempotency (same call twice = same result), concurrent calls do not corrupt state                                    |

---

## Assertions

Assert **typed results**, not just truthiness:

```python
# Good
result = ToolPermissionChecker().is_card_authorized(card, context)
assert result is False

error = MyToolInput.model_validate_raises(bad_input)
assert error.errors()[0]["loc"] == ("field_name",)
assert "safe message" in str(exception)
assert isinstance(error, AgentRuntimeError)
assert error.code == RuntimeErrorCode.UNAUTHORIZED
```

**Never** assert `"secret"` is not in exception messages — assert the exact safe message.
**Never** assert a subagent received full conversation history — assert it received only the task.
**Never** assert unauthorized capability is present — assert it is **absent** from the model's tool list.

---

## Test fixtures — canonical families

Keep fixtures in `tests/unit/conftest.py` or per-module `conftest.py`.

### Runtime contexts

```python
def fake_context(org_id="acme", user_id="u1", scopes=("tools:use",)) -> AgentRuntimeContext
def admin_context() -> AgentRuntimeContext   # admin scope, full permissions
def no_connectors_context() -> AgentRuntimeContext  # no MCP servers visible
def missing_identity_context() -> AgentRuntimeContext  # no user_id / org_id
```

### Tool fixtures

```python
def safe_read_only_tool_card() -> ToolCard    # no approval required
def side_effecting_tool_card() -> ToolCard    # requires_approval=True
def malformed_tool_card() -> ToolCard         # invalid JSON schema
```

### MCP fixtures

```python
def healthy_mcp_server_card() -> McpServerCard
def timeout_mcp_server_card() -> McpServerCard
def auth_failure_mcp_server_card() -> McpServerCard  # auth_state=NONE
def malformed_schema_mcp_server_card() -> McpServerCard
```

### Memory fixtures

```python
def user_scope_memory_backend() -> MemoryBackend
def org_read_only_memory_backend() -> MemoryBackend
def near_limit_memory_backend() -> MemoryBackend    # headroom < 10%
def injected_content_memory_backend() -> MemoryBackend  # prompt injection in stored values
```

### Subagent fixtures

```python
def researcher_subagent_definition() -> SubagentDefinition
def compact_handoff_task() -> SubagentTask
def valid_subagent_result() -> SubagentResult
def oversized_subagent_result() -> SubagentResult   # triggers truncation
def malformed_subagent_result() -> SubagentResult   # missing required fields
```

### Fixture anti-patterns

- No network calls in fixtures — all external IO faked
- No real API keys / credentials
- No hidden validation inside fixture factories (fakes should be dumb containers)
- Prefer narrow fixtures (one fact per fixture) over multi-purpose god fixtures

---

## Edge-case matrix

Minimum set of cases required per feature area. Add more as you discover them.

### Runtime foundation

- Missing `user_id`, `org_id`, or `model_config` → validation rejection
- Unknown feature flag → run continues with safe default, does not crash
- Missing protocol method on injected port → `NotImplementedError` (not `AttributeError`)
- Error serialised to HTTP response contains no internal detail

### Dynamic tool loading

- Duplicate tool names across connectors → deterministic collision policy (first wins / error)
- Permission revoked between list and load → typed `UNAUTHORIZED` error at load time
- Malformed tool JSON schema → rejected at boundary, safe fallback
- Connector unavailable at load time → typed error, tool absent from model's list

### Skills middleware

- Empty `SKILL.md` → empty manifest (not error)
- Missing required frontmatter field → configuration error (not silent skip)
- Duplicate skill names across sources → deterministic precedence (numeric source order)
- Virtual skill path must not be opened as a filesystem path

### Dynamic MCP loading

- MCP server timeout → typed error, server absent from list for this run
- Auth token expired during load → typed error + auth flow triggered
- Malformed tool descriptor → rejected, other tools on same server still loaded
- Tool count exceeds load budget → typed `BUDGET_EXCEEDED` error

### Context and memory

- Context overflow → summarisation fires before context window exceeded
- Empty summarisation result → safe fallback (do not append empty message)
- Org memory write attempt from agent scope → `UNAUTHORIZED` (not silent skip)
- Concurrent writes to same memory path → last-write-wins or explicit conflict error
- Prompt injection stored in user memory → `PromptInjectionDetector` rejects at read time

### Subagents

- Subagent pool exhausted → task queued, not dropped
- Task id truncated by model output → detection and typed error
- Oversized subagent result → truncation applied, size annotation added
- User updates task while prior run active → deterministic handling (cancel prior or error)
- Subagent must not receive full conversation history

### Streaming and observability

- Missing stream namespace → gracefully skipped
- Unknown v2 stream event type → ignored, warning logged (not crash)
- Tool result exceeds stream size limit → truncation applied
- Summarisation event does not leak into user-visible SSE stream
- Raw chain-of-thought never exposed in `model_delta` events

### Event sequence

- `sequence_no` starts at 1 for each new run
- `sequence_no` is monotonically increasing with no gaps
- `list_events_after(N)` returns only envelopes with `sequence_no > N`
- Cancel mid-stream: resulting envelopes have consecutive sequence numbers in some order

---

## Required regression tests

Any bug fix in these areas **must** add a regression test:

- Dynamic tool loading (permission edge cases, connector unavailability)
- MCP permission gate (scope checks, auth_state transitions)
- Context compression (overflow detection, summarisation fallback)
- Memory routing (scope isolation, injection detection)
- Subagent lifecycle (task idempotency, result truncation)
- SSE streaming (resume with gap fill, heartbeat on empty poll)
- Budget charging (CAS idempotency, concurrent run race)

---

## PR checklist

A change is not ready to merge until:

- [ ] Implementation matches the relevant spec/feature doc
- [ ] All Pydantic contracts touched are covered by validation tests (valid + invalid)
- [ ] Permission denial path tested (unauthorized input → access denied, not just exception raised)
- [ ] Malformed input path tested → typed domain error with safe message
- [ ] External failure path tested → typed domain error, no credential leak
- [ ] No real credentials in tests
- [ ] No connector SDK imported in test code (faked at adapter boundary)
- [ ] No full conversation history passed to subagents in tests
- [ ] Any discovered edge case added to the matrix above
- [ ] Relevant KB doc updated if a contract, invariant, or module boundary changed
