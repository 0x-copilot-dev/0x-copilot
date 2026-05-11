# Ambiguity, Errors, and Sharing — S2, S7, S8

**Coverage:**

- S2 — Ambiguous prompt triggers `ask_a_question` tool
- S7 — Typed error paths (malformed inputs, unknown IDs, unauthorized access)
- S8 — Share + fork lifecycle (share token, preview, fork, revoke)

**Roadmap PR dependencies:**

- Error mapping lives in [`runtime_api/http/errors.py`](../../../src/runtime_api/http/errors.py) — `RuntimeApiErrorMapper`. Any structural refactor of `RuntimeApiService` must preserve typed-error response shapes.
- Share / fork live in `agent_runtime/api/` — [P9 service consolidation](../00-roadmap.md#phase-2--decoupling-foundation--hygiene) must preserve all public methods including `share`, `fork`, `self_fork`.

---

## Scenario 8.1 — Ambiguous prompt → ask_a_question (S2)

### Goal

Verify the agent calls the `ask_a_question` built-in tool when the user's request is too vague to act on, rather than guessing or fabricating.

### Preconditions

- Stack healthy.

### Execution

```bash
TOKEN=$(cat /tmp/dev-bearer.txt)
CONV_ID=$(curl -s -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"title":"S2 ambiguous"}' http://127.0.0.1:8200/v1/agent/conversations \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['conversation_id'])")

# Very ambiguous prompt with no context
RUN_ID=$(curl -s -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d "{\"conversation_id\":\"$CONV_ID\",\"user_input\":\"Do that thing we talked about.\"}" \
  http://127.0.0.1:8200/v1/agent/runs | python3 -c "import sys,json; print(json.load(sys.stdin)['run_id'])")

curl -s -N --max-time 60 -H "Authorization: Bearer $TOKEN" \
  "http://127.0.0.1:8200/v1/agent/runs/$RUN_ID/stream?after_sequence=0&follow=true" > sse.txt
```

### Deterministic invariants

| #     | Assertion                                                                                                                                                                                                            |
| ----- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| INV-1 | Baseline.                                                                                                                                                                                                            |
| INV-2 | Final run status `completed`.                                                                                                                                                                                        |
| INV-3 | Either (a) `ask_a_question` was called — `tool_call_started.payload.tool_name == "ask_a_question"` — OR (b) the model responded directly with a clarifying question. Both are acceptable. Outright invention is NOT. |
| INV-4 | If (a), there is a `tool_result` for `ask_a_question` and the response is shaped as a clarifying question.                                                                                                           |
| INV-5 | No write-capable tools (e.g. Slack send, draft, send message) are called — verify zero `tool_call_started` events for any non-`ask_a_question` / non-`suggest_mcp_connector` tool.                                   |

### Content rubric (judge)

D1, D5, D6. Pass threshold **5 / 6**.

- **D1 Correctness:** Response is a clarifying question or a request for context. NOT an attempt to "do" something.
- **D5 Surface honesty:** Response acknowledges it has no record of "that thing" / asks what the user is referring to. Does NOT invent a referenced topic.
- **D6 Brevity:** A single clarifying question, not a list of 5 questions or a long monologue.

### Pass criteria

All invariants AND judge ≥ 5/6.

### Negative path — false positive guard

Run a prompt that is _not_ ambiguous and verify `ask_a_question` does NOT fire:

```bash
RUN_NEG=$(curl -s -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d "{\"conversation_id\":\"$CONV_ID\",\"user_input\":\"What is 7 times 8?\"}" \
  http://127.0.0.1:8200/v1/agent/runs | python3 -c "import sys,json; print(json.load(sys.stdin)['run_id'])")
```

- INV-NEG-1: Zero `tool_call_started` events with `payload.tool_name == "ask_a_question"`.
- INV-NEG-2: Final response contains `"56"`.

---

## Scenario 8.2 — Typed error: malformed CreateRunRequest

### Goal

Verify the facade returns a typed Pydantic validation error (HTTP 422) with a structured body — not a leaking internal exception.

### Execution

```bash
TOKEN=$(cat /tmp/dev-bearer.txt)

# Missing required fields
curl -s -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{}' http://127.0.0.1:8200/v1/agent/runs > err-empty.json

# user_input wrong type
curl -s -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"conversation_id":"some-id","user_input":123}' http://127.0.0.1:8200/v1/agent/runs > err-type.json

# conversation_id refers to a conversation in another org
curl -s -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"conversation_id":"conv_from_another_org","user_input":"hi"}' http://127.0.0.1:8200/v1/agent/runs > err-cross-org.json
```

### Deterministic invariants

| #     | Assertion                                                                                                                                                                                         |
| ----- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| INV-1 | All three responses return a 4xx HTTP status (422 for the first two, 404 or 403 for the third).                                                                                                   |
| INV-2 | Response bodies are valid JSON.                                                                                                                                                                   |
| INV-3 | Each body has either FastAPI's default `{"detail": [...]}` shape OR the typed `{"code", "safe_message", "retryable", "correlation_id"}` shape (depends on the route's mapper).                    |
| INV-4 | The `safe_message` (when present) is a non-empty user-safe string. NEVER contains: file paths, stack traces, environment variable names, raw SQL, Postgres error codes, or internal class names.  |
| INV-5 | The `correlation_id` (when present) is a hex string.                                                                                                                                              |
| INV-6 | For the cross-org test (third call), the response does NOT leak existence of conversation IDs in other orgs — `safe_message` says "not found" / "no access", never "exists but you can't see it". |
| INV-7 | No 5xx responses for any of these inputs. 5xx for user-input errors is a regression.                                                                                                              |

### Content rubric

N/A.

### Negative path — unknown run ID

```bash
curl -s -H "Authorization: Bearer $TOKEN" \
  "http://127.0.0.1:8200/v1/agent/runs/run_does_not_exist" > err-run-404.json
curl -s -H "Authorization: Bearer $TOKEN" \
  "http://127.0.0.1:8200/v1/agent/runs/run_does_not_exist/events?after_sequence=0" > err-run-events-404.json
```

- INV-NEG-1: Both responses are HTTP 404 with a typed body.
- INV-NEG-2: The `safe_message` does not differ in content based on whether the run truly doesn't exist vs exists-in-another-org. (Both should say "not found".)

### Negative path — no auth

```bash
curl -s "http://127.0.0.1:8200/v1/agent/conversations" > err-no-auth.json
```

- INV-NA-1: HTTP 401 (or 403).
- INV-NA-2: Body has typed shape.
- INV-NA-3: No information about valid endpoints or auth methods is leaked beyond "authentication required".

---

## Scenario 8.3 — Typed error: tool budget exhausted

### Goal

Force the per-task tool budget to fire. Verify a `budget_warning` event with a typed safe message, not a 500.

### Preconditions

- Stack healthy.
- The default tool budget per task is 5 (per [f2 source flow](../../architecture/f2-multi-turn-tool.puml)).

### Execution

A prompt designed to provoke many tool calls in one turn:

```bash
TOKEN=$(cat /tmp/dev-bearer.txt)
CONV_ID=$(curl -s -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"title":"S? tool budget"}' http://127.0.0.1:8200/v1/agent/conversations \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['conversation_id'])")

RUN_ID=$(curl -s -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d "{\"conversation_id\":\"$CONV_ID\",\"user_input\":\"Suggest these connectors one at a time, one tool call each: Linear, Notion, GitHub, Asana, Slack, Intercom, Sentry, Cloudflare. Use suggest_mcp_connector once per name.\"}" \
  http://127.0.0.1:8200/v1/agent/runs | python3 -c "import sys,json; print(json.load(sys.stdin)['run_id'])")

curl -s -N --max-time 120 -H "Authorization: Bearer $TOKEN" \
  "http://127.0.0.1:8200/v1/agent/runs/$RUN_ID/stream?after_sequence=0&follow=true" > sse.txt
```

### Deterministic invariants

| #     | Assertion                                                                                                             |
| ----- | --------------------------------------------------------------------------------------------------------------------- |
| INV-1 | Baseline.                                                                                                             |
| INV-2 | At least one `budget_warning` event appears once the per-task cap is reached.                                         |
| INV-3 | `budget_warning.payload` has a typed `safe_message` and a `retryable` flag.                                           |
| INV-4 | After `budget_warning`, no further `tool_call_started` for the same task.                                             |
| INV-5 | Final run status is `completed` (graceful degradation, not failure). The agent finishes with whatever results it has. |

### Known stochasticity

- The model may not actually attempt 8 tool calls — it may batch suggestions verbally. This isn't a budget test failure; it's a "couldn't reach the gate" situation. Re-run with a more forcing prompt if needed.

### Content rubric

D5. Pass threshold **1 / 2**.

- **D5 Surface honesty:** Response acknowledges hitting the limit / didn't pretend to suggest more connectors than it actually called.

---

## Scenario 8.4 — Share lifecycle (S8 part 1)

### Goal

Verify the share endpoint:

1. POST creates a share token with a server-generated `share_id` and a public-shareable `share_token`.
2. GET (unauthenticated, via the share_token) returns a read-only conversation preview.
3. PATCH updates share metadata (e.g. revoke, regenerate, set expiry).
4. DELETE revokes the share permanently — the public URL returns 404 afterward.

### Preconditions

- A conversation with at least 1 completed run.

### Execution

```bash
TOKEN=$(cat /tmp/dev-bearer.txt)
CONV_ID=$(curl -s -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"title":"S8 share+fork"}' http://127.0.0.1:8200/v1/agent/conversations \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['conversation_id'])")

# At least one run
RUN_ID=$(curl -s -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d "{\"conversation_id\":\"$CONV_ID\",\"user_input\":\"Tell me a haiku about coffee.\"}" \
  http://127.0.0.1:8200/v1/agent/runs | python3 -c "import sys,json; print(json.load(sys.stdin)['run_id'])")
curl -s -N --max-time 30 -H "Authorization: Bearer $TOKEN" \
  "http://127.0.0.1:8200/v1/agent/runs/$RUN_ID/stream?after_sequence=0&follow=true" > /dev/null

# Create share
curl -s -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{}' "http://127.0.0.1:8200/v1/agent/conversations/$CONV_ID/share" > share-create.json
SHARE_TOKEN=$(python3 -c "import json; print(json.load(open('share-create.json'))['share_token'])")
SHARE_ID=$(python3 -c "import json; print(json.load(open('share-create.json'))['share_id'])")

# Preview (no auth required)
curl -s "http://127.0.0.1:8200/v1/agent/shares/$SHARE_TOKEN/preview" > share-preview.json

# Full content (no auth — public)
curl -s "http://127.0.0.1:8200/v1/agent/shares/$SHARE_TOKEN" > share-content.json

# List shares for this conversation
curl -s -H "Authorization: Bearer $TOKEN" \
  "http://127.0.0.1:8200/v1/agent/conversations/$CONV_ID/shares" > share-list.json

# Revoke
curl -s -X DELETE -H "Authorization: Bearer $TOKEN" \
  "http://127.0.0.1:8200/v1/agent/shares/$SHARE_ID" > share-revoke.json

# Confirm revoked
curl -s -o /dev/null -w "%{http_code}" "http://127.0.0.1:8200/v1/agent/shares/$SHARE_TOKEN" > share-after-revoke.txt
```

### Deterministic invariants

| #     | Assertion                                                                                                                      |
| ----- | ------------------------------------------------------------------------------------------------------------------------------ |
| INV-1 | `share-create.json` contains `share_id`, `share_token`, `created_at`, `created_by_user_id`.                                    |
| INV-2 | `share_token` is a high-entropy random string (not predictable from `share_id`).                                               |
| INV-3 | `share-preview.json` returns HTTP 200, contains the conversation title and message excerpts but NOT internal user identifiers. |
| INV-4 | `share-content.json` returns HTTP 200 with the full conversation content.                                                      |
| INV-5 | The same `share_token` is accessible without auth (verify by curl without the bearer).                                         |
| INV-6 | `share-list.json` includes the share with the right metadata (creator, created_at, active flag).                               |
| INV-7 | `share-revoke.json` returns HTTP 200/204.                                                                                      |
| INV-8 | After revoke, `share-after-revoke.txt` is `404` (or 410 Gone). Public access stops immediately.                                |
| INV-9 | Share-related audit entries are appended for: create, preview-by-unauthenticated, revoke.                                      |

### Content rubric

N/A — contract test.

### Known security checks

- INV-SEC-1: The share token must NOT include the org_id, user_id, conversation_id verbatim — it must be opaque.
- INV-SEC-2: A share URL from one org must not leak in the list / search endpoints of another org.
- INV-SEC-3: Revoke is hard-delete or tombstoned — replaying the old share_token after revoke does not surface conversation content even via a race condition.

---

## Scenario 8.5 — Fork lifecycle (S8 part 2)

### Goal

A share's recipient can fork into a new conversation in their own org. Verify:

1. The fork creates a new conversation in the recipient's org with `forked_from_share_id` populated.
2. Messages from the source share are copied into the fork (up to whatever cut-off `source_message_id` specifies).
3. The fork's run sequence starts fresh (sequence_no=1 for the first new run).
4. Citations from the source are preserved.

### Preconditions

- A share created (re-use 8.4 setup, do not revoke).
- A second persona to fork _into_ (e.g. `marcus_admin` if same org, or a separate org for cross-tenant fork).

### Execution

```bash
TOKEN_SOURCE=$(make dev-bearer PERSONA=sarah_acme)
TOKEN_DEST=$(make dev-bearer PERSONA=marcus_admin)

# Source creates share (same as 8.4)
# ... omitted, see 8.4 for setup ...

# Recipient forks
curl -s -X POST -H "Authorization: Bearer $TOKEN_DEST" -H "Content-Type: application/json" \
  -d '{}' "http://127.0.0.1:8200/v1/agent/shares/$SHARE_TOKEN/fork" > fork-create.json

FORK_CONV=$(python3 -c "import json; print(json.load(open('fork-create.json'))['conversation_id'])")

# Verify the fork conversation belongs to the recipient
curl -s -H "Authorization: Bearer $TOKEN_DEST" \
  "http://127.0.0.1:8200/v1/agent/conversations/$FORK_CONV" > fork-conv.json

# Run a new turn in the fork
RUN_F=$(curl -s -X POST -H "Authorization: Bearer $TOKEN_DEST" -H "Content-Type: application/json" \
  -d "{\"conversation_id\":\"$FORK_CONV\",\"user_input\":\"Add a second haiku about tea.\"}" \
  http://127.0.0.1:8200/v1/agent/runs | python3 -c "import sys,json; print(json.load(sys.stdin)['run_id'])")
curl -s -N --max-time 30 -H "Authorization: Bearer $TOKEN_DEST" \
  "http://127.0.0.1:8200/v1/agent/runs/$RUN_F/stream?after_sequence=0&follow=true" > sse-fork.txt
```

### Deterministic invariants

| #     | Assertion                                                                                                                                |
| ----- | ---------------------------------------------------------------------------------------------------------------------------------------- |
| INV-1 | `fork-create.json` returns HTTP 200 with a new `conversation_id`.                                                                        |
| INV-2 | `fork-conv.json` has `user_id` = recipient's user_id (NOT the source's).                                                                 |
| INV-3 | `fork-conv.json` has `forked_from_share_id` matching `SHARE_ID`.                                                                         |
| INV-4 | The fork conversation contains the source's messages (or a subset if `source_message_id` was specified).                                 |
| INV-5 | The fork's new run (`RUN_F`) has `sequence_no` starting at 1 — independent event namespace.                                              |
| INV-6 | The fork's audit log shows the fork creation event with both source share_id and dest user_id.                                           |
| INV-7 | Source conversation is unchanged after fork — no mutations leak back.                                                                    |
| INV-8 | If the source share is later revoked, the fork remains accessible (forks are copies, not references).                                    |
| INV-9 | Cross-org isolation: if `TOKEN_DEST` belongs to a different org than `TOKEN_SOURCE`, the fork is correctly placed in `TOKEN_DEST`'s org. |

### Content rubric (judge)

D1. Pass threshold **1 / 2**.

- **D1 Correctness:** The fork's new turn answers with a haiku about tea (and may reference the prior haiku about coffee since it's in the conversation history).

### Self-fork variant

`POST /v1/agent/conversations/{id}/fork` is the in-org variant (self-fork) — no share token needed.

```bash
curl -s -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{}' "http://127.0.0.1:8200/v1/agent/conversations/$CONV_ID/fork" > self-fork.json
```

Apply 8.5 INV-1, INV-3 (replace `forked_from_share_id` with `forked_from_message_id` or similar), INV-5, INV-7.

---

## Combined pass / fail rollup

- 8.1 (ask_a_question): full pass gate in dev. Stochastic but D5/D6 rubric tightly graded.
- 8.2 (typed errors): full pass gate. INV-7 (no 5xx for user input) is the load-bearing assertion.
- 8.3 (budget warning): pass if budget fires when forced; soft-fail if the model refuses to call tools that many times.
- 8.4 (share): full pass gate.
- 8.5 (fork): full pass gate when two personas are available.
