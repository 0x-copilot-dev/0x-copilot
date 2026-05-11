# MCP Lifecycle — f7 + f8

**Coverage:**

- f7 — Adding an MCP server, three install paths ([source flow](../../architecture/f7-mcp-add.puml))
- f8 — Calling an unauthenticated MCP tool, in-chat auth, run resume ([source flow](../../architecture/f8-mcp-auth.puml))

**Roadmap PR dependencies:**

- These scenarios pin the MCP registry boundary (`backend` owns mutation, ai-backend is read-only) — preserved across all current refactor phases.

**Status:**

- f7 (install via catalog, Path B) — testable in `make dev`.
- f7 Path A (JSON config) — out of scope here (operator-only).
- f7 Path C (custom UI add) — testable but needs real MCP URL + descriptor; deferred unless a stub MCP is available.
- f8 — DEFERRED. The auth-interrupt code path requires a server whose `auth_state` is visible to the model. `make dev` setups have all servers either fully authenticated (model calls succeed) or unauthenticated (policy hides from `list_available_servers`). True f8 requires a token-expired-mid-run scenario.

---

## Scenario 4.1 — Install MCP server via catalog (f7 Path B)

### Goal

Install an MCP server from the catalog. Verify:

1. Catalog returns a populated entry list.
2. POST `/v1/mcp/servers/install` creates a `ServerRecord` with `auth_state == "unauthenticated"`.
3. The installed server immediately appears in `GET /v1/mcp/servers` for this org.
4. ai-backend picks it up on the next `create_agent_runtime` (no cache).
5. Backend owns the mutation; ai-backend never imports backend's Python.

### Preconditions

- Stack healthy.
- No prior install of the target connector this session (or start from a fresh `make dev`).

### Execution

```bash
TOKEN=$(cat /tmp/dev-bearer.txt)

# Step 1: catalog should have ≥10 entries (seeded)
curl -s -H "Authorization: Bearer $TOKEN" \
  "http://127.0.0.1:8200/v1/mcp/catalog" > catalog.json

# Step 2: no servers installed initially
curl -s -H "Authorization: Bearer $TOKEN" \
  "http://127.0.0.1:8200/v1/mcp/servers" > servers-before.json

# Step 3: install Linear (slug from catalog)
curl -s -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"slug":"linear"}' \
  "http://127.0.0.1:8200/v1/mcp/servers/install" > install-response.json

# Step 4: server visible immediately
curl -s -H "Authorization: Bearer $TOKEN" \
  "http://127.0.0.1:8200/v1/mcp/servers" > servers-after.json

# Step 5: trigger a fresh agent runtime to verify no-cache contract.
# A trivial run that doesn't actually use Linear, but proves a new factory pass occurred.
CONV_ID=$(curl -s -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"title":"f7 install verify"}' http://127.0.0.1:8200/v1/agent/conversations \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['conversation_id'])")
RUN_ID=$(curl -s -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d "{\"conversation_id\":\"$CONV_ID\",\"user_input\":\"Hi\"}" \
  http://127.0.0.1:8200/v1/agent/runs | python3 -c "import sys,json; print(json.load(sys.stdin)['run_id'])")
# That run's system prompt assembly should have re-queried the registry, picking up Linear.
```

### Capture

- `catalog.json`, `servers-before.json`, `install-response.json`, `servers-after.json`.

### Deterministic invariants

| #     | Assertion                                                                                                                                                                                                                                                                                              |
| ----- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| INV-1 | `catalog.entries` is non-empty and contains at least `linear`, `notion`, `github`, `asana`.                                                                                                                                                                                                            |
| INV-2 | Each catalog entry has `slug`, `display_name`, `url`, `transport`, `auth_mode`, `default_scopes`, `requires_pre_registered_client`.                                                                                                                                                                    |
| INV-3 | `servers-before.servers` does not contain `seed:linear` (assumes fresh session).                                                                                                                                                                                                                       |
| INV-4 | `install-response.server_id == "seed:linear"`, `install-response.auth_state == "unauthenticated"`, `install-response.health == "healthy"`.                                                                                                                                                             |
| INV-5 | `install-response.oauth_client_configured == false` when `catalog.linear.requires_pre_registered_client == false` (DCR will run on first auth start).                                                                                                                                                  |
| INV-6 | `servers-after.servers` contains exactly one entry where `server_id == "seed:linear"`.                                                                                                                                                                                                                 |
| INV-7 | The follow-up run's `create_agent_runtime` completed (run reached `run_completed`). This proves no cache was holding stale state. (Cannot directly assert the system-prompt content without code instrumentation, but successful completion + the policy filter test in 4.4 below covers it together.) |
| INV-8 | Install API is on `backend-facade` at port 8200. Direct call to `:8000/internal/v1/mcp/...` should reject without `ENTERPRISE_SERVICE_TOKEN` header — verifies the boundary.                                                                                                                           |

### Content rubric

N/A — this scenario tests registry / API contract.

### Pass criteria

All invariants hold.

### Cleanup

```bash
# Optionally delete the installed server for a clean slate next run
curl -s -X DELETE -H "Authorization: Bearer $TOKEN" \
  "http://127.0.0.1:8200/v1/mcp/servers/seed:linear"
```

---

## Scenario 4.2 — Inline suggest in chat (f7 Path B mid-chat)

### Goal

When the user mentions a _not-yet-installed_ connector in chat, the agent calls `suggest_mcp_connector` (a builtin) which emits an inline `mcp_auth_required` card (approval_kind `mcp_auth`). The run does NOT pause; the user clicks Connect on the card to install.

### Preconditions

- Stack healthy.
- Linear _not_ yet installed (or if installed-unauthenticated, the same behavior applies — covered in 4.3).

### Execution

```bash
TOKEN=$(cat /tmp/dev-bearer.txt)
CONV_ID=$(curl -s -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"title":"f7 inline suggest"}' http://127.0.0.1:8200/v1/agent/conversations \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['conversation_id'])")
RUN_ID=$(curl -s -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d "{\"conversation_id\":\"$CONV_ID\",\"user_input\":\"Can you help me connect to Linear so I can search tickets?\"}" \
  http://127.0.0.1:8200/v1/agent/runs | python3 -c "import sys,json; print(json.load(sys.stdin)['run_id'])")

curl -s -N --max-time 60 -H "Authorization: Bearer $TOKEN" \
  "http://127.0.0.1:8200/v1/agent/runs/$RUN_ID/stream?after_sequence=0&follow=true" > sse.txt
```

### Deterministic invariants

(Cross-references Scenario 1.2 — this is essentially the same flow, called out here for the MCP-specific assertions.)

| #     | Assertion                                                                                                                                              |
| ----- | ------------------------------------------------------------------------------------------------------------------------------------------------------ |
| INV-1 | Baseline invariants hold.                                                                                                                              |
| INV-2 | A `tool_call_started` event has `payload.tool_name == "suggest_mcp_connector"`.                                                                        |
| INV-3 | Exactly one `mcp_auth_required` event with `payload.approval_kind == "mcp_auth"`, `payload.server_id == "seed:linear"`, `activity_kind == "mcp_auth"`. |
| INV-4 | `mcp_auth_required.metadata.presentation.kind == "auth"`.                                                                                              |
| INV-5 | Run reaches `run_completed` — not `waiting_for_approval`. (The inline suggestion is non-blocking; the actual MCP-tool auth interrupt is f8.)           |
| INV-6 | `tool_result.payload.output.content` (parsed JSON) has `status == "emitted"` and `server_id == "linear"`.                                              |

### Content rubric

D1, D5. Pass threshold **3 / 4**.

- **D1 Correctness:** Response acknowledges Linear isn't connected and points to the Connect action.
- **D5 Surface honesty:** Does NOT pretend tickets were searched. Does NOT invent ticket titles.

### Pass criteria

All invariants hold AND judge ≥ 3/4.

---

## Scenario 4.3 — Catalog suggestion when connector is installed-unauthenticated

### Goal

Same as 4.2 but with the connector already _installed_ (auth_state=unauthenticated). The agent should NOT call `suggest_mcp_connector` (the connector already exists); the agent should NOT invent Linear data; the agent should surface that auth is needed.

### Preconditions

- Linear installed but `auth_state == "unauthenticated"` (run Scenario 4.1 first, do not authenticate).

### Execution

Same as 4.2.

### Deterministic invariants

| #     | Assertion                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      |
| ----- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| INV-1 | Baseline.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      |
| INV-2 | The model behavior depends on whether `McpPermissionPolicy` exposes the unauthenticated Linear card to the model. Document the observed behavior:<br>(a) If the model still calls `suggest_mcp_connector` — note this is a quirk but not a regression.<br>(b) If the model attempts a Linear MCP tool call — verify it routes to `AuthMcpTool` and emits `mcp_auth_required` (this is the true f8 path; if this happens here, move to Scenario 4.4 below).<br>(c) If the model fabricates Linear data without calling any tool — **HARD FAIL** (D5 violation). |

### Content rubric

Same as 4.2. D5 (Surface honesty) is weighted at 2× — fabrication is the most serious failure mode here.

---

## Scenario 4.4 — MCP auth required mid-flight (f8 — DEFERRED)

### Goal

The headline f8 flow: an MCP tool call fails the permission check, the executor short-circuits on `action_interrupt_events`, the run pauses in `waiting_for_approval`, the user authenticates, an `APPROVAL_RESOLVED` command resumes the run, and the tool call succeeds.

### Status

**DEFERRED.** No reliable path in `make dev` mode:

- Unauthenticated server: model never selects its tools (`McpPermissionPolicy.is_server_card_visible` hides them).
- Authenticated server: tool call succeeds; no auth interrupt.
- Expired-token mid-run: requires a real OAuth-backed connector and the token to expire during the run window — not reproducible on-demand.

### Steps once a path exists

1. Install Linear and authenticate (real OAuth dance against a Linear test workspace).
2. Confirm `GET /v1/mcp/servers/seed:linear` returns `auth_state == "valid"`.
3. Invalidate the token in the vault (or force-expire via an admin endpoint if one exists).
4. Submit: `"Search my latest 5 Linear issues."`
5. Capture the SSE stream.

### Deterministic invariants

| #     | Assertion                                                                                                                                                                          |
| ----- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| INV-1 | `mcp_auth_required` event with `source == "tool"`, `payload.approval_id` populated, `payload.server_id == "seed:linear"`.                                                          |
| INV-2 | An approval row is created — verify via `GET /v1/agent/approvals?conversation_id=<id>`.                                                                                            |
| INV-3 | Run status transitions to `waiting_for_approval` (verify via `GET /v1/agent/runs/{run_id}`).                                                                                       |
| INV-4 | The active executor stops streaming after the `mcp_auth_required` envelope. No further `model_delta` events.                                                                       |
| INV-5 | The cancel handler queue claim for the run is NOT marked complete yet (resume must reclaim and finish).                                                                            |
| INV-6 | After `POST /v1/agent/approvals/{approval_id}/decision {decision: "approve"}`, an `APPROVAL_RESOLVED` event is appended and the worker enqueues a new `APPROVAL_RESOLVED` command. |
| INV-7 | Resumed run continues from the previous LangGraph state. New `tool_call_started` for the same Linear tool fires; `tool_result` succeeds (auth now valid).                          |
| INV-8 | Final response cites Linear sources via citations (overlaps with Scenario 5.1).                                                                                                    |
| INV-9 | Run terminal is `run_completed`.                                                                                                                                                   |

### Content rubric

D1, D2, D4. Pass threshold **5 / 6**.

- **D1 Correctness:** Response lists actual Linear issues from the workspace, not invented ones.
- **D2 Tool choice:** Linear search/list tool was called (not `suggest_mcp_connector`).
- **D4 Faithfulness:** Issue titles / IDs in the response match what the tool returned (judge can cross-check the `tool_result.payload.output` against the final response).

### Pass criteria

All invariants hold AND judge ≥ 5/6 (once the flow is reachable).

### Token rotation variant (multi-fire)

When a long-running turn outlives the token TTL:

- INV-MR-1: A second `mcp_auth_required` fires on the same run, with a _different_ `approval_id`.
- INV-MR-2: The approval lifecycle repeats — resume re-runs the executor.
- INV-MR-3: Sequence integrity preserved across multiple pause/resume cycles.

---

## Scenario 4.5 — Custom MCP add (f7 Path C — partially testable)

### Goal

Verify the POST `/v1/mcp/servers` endpoint accepts a custom descriptor (URL + auth_type + optional pre-registered client fields) and produces a `ServerRecord` with `health` reflecting an initial discovery probe.

### Preconditions

- Stack healthy.
- A test MCP server URL is reachable (or accept that `health` will be `unreachable`).

### Execution

```bash
TOKEN=$(cat /tmp/dev-bearer.txt)
curl -s -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{
    "name": "test-mcp-custom",
    "display_name": "Test Custom MCP",
    "url": "https://example.invalid/mcp",
    "transport": "http",
    "auth_mode": "none"
  }' \
  "http://127.0.0.1:8200/v1/mcp/servers" > custom-install.json
```

### Deterministic invariants

| #     | Assertion                                                                                 |
| ----- | ----------------------------------------------------------------------------------------- | ------------- | ------------------------------------------------------------------------------------------------------------ |
| INV-1 | Response is HTTP 200/201 with a `server_id` (auto-generated for non-seed servers).        |
| INV-2 | `auth_state == "valid"` if `auth_mode == "none"`, else `auth_state == "unauthenticated"`. |
| INV-3 | The server appears in `GET /v1/mcp/servers`.                                              |
| INV-4 | `health` is one of `healthy`                                                              | `unreachable` | `error` depending on whether the URL was probed successfully. A `null` or missing health field is a failure. |

### Cleanup

```bash
SERVER_ID=$(python3 -c "import json; print(json.load(open('custom-install.json'))['server_id'])")
curl -s -X DELETE -H "Authorization: Bearer $TOKEN" \
  "http://127.0.0.1:8200/v1/mcp/servers/$SERVER_ID"
```

---

## Combined pass / fail rollup

- 4.1: full pass gate in `make dev`.
- 4.2: full pass gate in `make dev`.
- 4.3: behavioral observation — outcome (a)/(b)/(c) characterizes the policy, not a strict pass/fail unless (c) fires (HARD FAIL).
- 4.4: deferred until real OAuth path is reachable.
- 4.5: testable but cosmetic without a real probe target.
