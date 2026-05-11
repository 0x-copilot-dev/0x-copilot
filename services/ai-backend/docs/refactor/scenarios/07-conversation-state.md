# Conversation State — S1 / S5 / S6 + projection check

**Coverage:**

- S1 — Multi-turn within the same conversation (history reload)
- S5 — Subagents endpoint on a real conversation
- S6 — Drafts endpoint on a real conversation
- `latest_run_id` / `latest_run_status` projection on conversation detail and list

**Roadmap PR dependencies:**

- Conversation projection logic lives in [`agent_runtime/api/`](../../../src/agent_runtime/api/) (target of [P22 RuntimeApiService split](../00-roadmap.md#phase-6--coordinator-split-do-last)).
- The `latest_run_*` fields are part of the conversation public contract and must survive every refactor.

---

## Scenario 7.1 — Multi-turn within the same conversation (S1)

### Goal

Verify that:

1. Sending a second user message to the same conversation creates a _new_ run with its own `sequence_no` namespace.
2. The new run's executor receives prior conversation history (the model's response references context from turn 1).
3. Each run has its own SSE stream, replay log, and final response.
4. Cross-run state (e.g. tool budget per turn) resets correctly.

### Preconditions

- Stack healthy.
- A fresh conversation.

### Execution

```bash
TOKEN=$(cat /tmp/dev-bearer.txt)
CONV_ID=$(curl -s -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"title":"S1 multi-turn"}' http://127.0.0.1:8200/v1/agent/conversations \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['conversation_id'])")

# Turn 1: introduce a fact
RUN1=$(curl -s -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d "{\"conversation_id\":\"$CONV_ID\",\"user_input\":\"My favorite color is octarine. Please remember that for the rest of this chat.\"}" \
  http://127.0.0.1:8200/v1/agent/runs | python3 -c "import sys,json; print(json.load(sys.stdin)['run_id'])")
curl -s -N --max-time 60 -H "Authorization: Bearer $TOKEN" \
  "http://127.0.0.1:8200/v1/agent/runs/$RUN1/stream?after_sequence=0&follow=true" > sse-t1.txt

# Turn 2: probe history
RUN2=$(curl -s -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d "{\"conversation_id\":\"$CONV_ID\",\"user_input\":\"What is my favorite color?\"}" \
  http://127.0.0.1:8200/v1/agent/runs | python3 -c "import sys,json; print(json.load(sys.stdin)['run_id'])")
curl -s -N --max-time 60 -H "Authorization: Bearer $TOKEN" \
  "http://127.0.0.1:8200/v1/agent/runs/$RUN2/stream?after_sequence=0&follow=true" > sse-t2.txt

# Turn 3: probe modifier
RUN3=$(curl -s -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d "{\"conversation_id\":\"$CONV_ID\",\"user_input\":\"Actually scratch that. My favorite is now ultraviolet. Confirm.\"}" \
  http://127.0.0.1:8200/v1/agent/runs | python3 -c "import sys,json; print(json.load(sys.stdin)['run_id'])")
curl -s -N --max-time 60 -H "Authorization: Bearer $TOKEN" \
  "http://127.0.0.1:8200/v1/agent/runs/$RUN3/stream?after_sequence=0&follow=true" > sse-t3.txt

# Turn 4: probe latest
RUN4=$(curl -s -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d "{\"conversation_id\":\"$CONV_ID\",\"user_input\":\"What's my favorite color again?\"}" \
  http://127.0.0.1:8200/v1/agent/runs | python3 -c "import sys,json; print(json.load(sys.stdin)['run_id'])")
curl -s -N --max-time 60 -H "Authorization: Bearer $TOKEN" \
  "http://127.0.0.1:8200/v1/agent/runs/$RUN4/stream?after_sequence=0&follow=true" > sse-t4.txt

# Conversation messages
curl -s -H "Authorization: Bearer $TOKEN" \
  "http://127.0.0.1:8200/v1/agent/conversations/$CONV_ID/messages" > messages.json
curl -s -H "Authorization: Bearer $TOKEN" \
  "http://127.0.0.1:8200/v1/agent/conversations/$CONV_ID" > conv.json
```

### Deterministic invariants

| #      | Assertion                                                                                                                                                                                                                                                         |
| ------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| INV-1  | Baseline invariants hold for each of the 4 runs independently.                                                                                                                                                                                                    |
| INV-2  | Each run's `sequence_no` namespace is independent (Turn 2's first event is `sequence_no=1`, not `sequence_no=N+1` where N is Turn 1's last).                                                                                                                      |
| INV-3  | `messages.json` has 8 entries (4 user + 4 assistant) in order, alternating roles.                                                                                                                                                                                 |
| INV-4  | Each assistant message has `parent_message_id` referencing the prior user message.                                                                                                                                                                                |
| INV-5  | Each message has `content_text` set to the actual message body (NOT empty — verify both user and assistant).                                                                                                                                                      |
| INV-6  | Each assistant message has `run_id` set to the run that produced it.                                                                                                                                                                                              |
| INV-7  | `conv.latest_run_id` equals `RUN4` (the most recent completed run). **[Currently failing]** in `make dev` — the conversation detail returns `latest_run_id: null` and `latest_run_status: null`. File as a finding; this is the `latest_run_id` projection check. |
| INV-8  | `conv.latest_run_status` equals `"completed"`. **[Currently failing]** — same projection bug.                                                                                                                                                                     |
| INV-9  | The 4 runs have monotonically increasing `created_at` timestamps.                                                                                                                                                                                                 |
| INV-10 | The tool budget resets per run (no budget_warning carryover between turns). Verify if Scenario 9.2 has triggered budget elsewhere; not expected here since none of these turns call tools.                                                                        |

### Content rubric (judge)

D1, D5. Pass threshold **6 / 8** across the four turns combined.

- **D1 Correctness (per turn):**
  - Turn 2: response says "octarine" (or paraphrases). If "I don't remember" → 0. If the wrong color → 0.
  - Turn 4: response says "ultraviolet". Should NOT say "octarine" (history was overridden in Turn 3).
- **D5 Surface honesty:** If Turn 3's "scratch that" override is missed in Turn 4, score the run honestly — model failed to follow update, not the system.

### Pass criteria

- All deterministic invariants except INV-7 / INV-8 (filed as known projection bug). Judge total ≥ 6/8.
- The `latest_run_id` finding is filed separately; this scenario does not block on it.

### Known gaps

- The `latest_run_id` / `latest_run_status` projection is currently broken in `make dev` even on completed runs. Investigate whether this is a backend-facade response shaping issue or a deeper projection bug in `RuntimeApiService`.

---

## Scenario 7.2 — Subagents endpoint (S5)

### Goal

Verify the conversation-scoped subagents projection endpoint:

1. Returns an array (possibly empty).
2. Reflects events emitted during runs in that conversation (`SUBAGENT_STARTED`, `_PROGRESS`, `_COMPLETED`).
3. Each subagent entry has `task_id`, `parent_task_id`, `status`, lifecycle timestamps.
4. Status enum matches `SubagentLifecycleStatus`: `queued | running | completed | cancelled | failed | timed_out`.

### Preconditions

- A conversation that has run.

### Execution

```bash
TOKEN=$(cat /tmp/dev-bearer.txt)
# Use the conversation from Scenario 7.1 (or 9.x for one that *did* delegate)
curl -s -H "Authorization: Bearer $TOKEN" \
  "http://127.0.0.1:8200/v1/agent/conversations/$CONV_ID/subagents" > subagents.json
```

### Deterministic invariants

| #     | Assertion                                                                                                                                                                                                                                   |
| ----- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| INV-1 | HTTP 200, JSON body present.                                                                                                                                                                                                                |
| INV-2 | Response body is an array (or object with an array field — handle both). Empty array is valid when no subagents fired.                                                                                                                      |
| INV-3 | When non-empty: each entry has `task_id`, `status` ∈ allowed enum, `started_at` (ISO 8601 UTC).                                                                                                                                             |
| INV-4 | Subagents that completed have `completed_at` populated; those still running have `completed_at == null`.                                                                                                                                    |
| INV-5 | `parent_task_id` is set (linking to the supervisor's tool call) when this is a child of a delegation, otherwise null.                                                                                                                       |
| INV-6 | When events of type `SUBAGENT_FLEET_STARTED` were emitted in the run, the same fleet identifier groups child subagents in this projection (verify by cross-referencing event `payload.fleet_id` with subagent entry `fleet_id` or similar). |

### Content rubric

N/A — this is a projection contract test.

### Known limitations

- No subagent definitions are seeded in `make dev`, so the projection is _always_ empty in this environment. The endpoint should still return a valid empty array — that's the testable contract in dev.
- To test the non-empty path, seed at least one `SubagentDefinition` and run Scenario 9.3 first.

---

## Scenario 7.3 — Drafts endpoint (S6)

### Goal

Verify the drafts projection endpoint:

1. Returns an array, possibly empty.
2. Each draft has `draft_id`, `connector` (e.g. `slack`), `status` (`pending | sent | discarded`), `version`, content payload.
3. Drafts appear when the agent has used a write-capable tool that requires confirmation (e.g. "draft a Slack message").
4. After `POST /v1/agent/drafts/{draft_id}/send` or `discard`, status flips.

### Preconditions

- A conversation that has run.
- For non-empty path: a write-capable tool wired up (e.g. Slack send). Not available in `make dev` without an authenticated Slack MCP.

### Execution

```bash
TOKEN=$(cat /tmp/dev-bearer.txt)
curl -s -H "Authorization: Bearer $TOKEN" \
  "http://127.0.0.1:8200/v1/agent/conversations/$CONV_ID/drafts" > drafts.json
```

### Deterministic invariants

| #     | Assertion                                                                                                                                                 |
| ----- | --------------------------------------------------------------------------------------------------------------------------------------------------------- |
| INV-1 | HTTP 200.                                                                                                                                                 |
| INV-2 | Body is a list (or has a list field). Empty allowed.                                                                                                      |
| INV-3 | When non-empty: each entry has `draft_id`, `connector`, `status`, `version`, `created_at`, `updated_at`.                                                  |
| INV-4 | Versions are monotonic per `draft_id` (append-only via `DraftStorePort.insert_version`).                                                                  |
| INV-5 | A draft's `status` transitions are constrained: `pending → sent`, `pending → discarded`. `sent` and `discarded` are terminal — no further status changes. |

### Negative path

Try to update a non-existent draft:

```bash
curl -s -X PATCH -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"content":"updated"}' \
  "http://127.0.0.1:8200/v1/agent/drafts/draft_does_not_exist" > patch-error.json
```

- INV-NEG-1: Response is HTTP 404 with a typed `safe_message` ("draft not found" or similar).
- INV-NEG-2: No mutation occurred (verify by re-reading the conversation drafts list).

### Known limitations

- Drafts in `make dev` are empty because no write-capable tool is configured. The contract test (INV-1, INV-2) passes trivially. The full path requires a real authenticated MCP server with a write tool.

---

## Scenario 7.4 — Conversation list pagination and ordering

### Goal

Verify `GET /v1/agent/conversations` returns sensible defaults:

1. Items ordered by recency (most-recent updated first).
2. Pagination cursor present when count exceeds the default page size.
3. Each item has the projection fields (`title`, `created_at`, `updated_at`, `latest_run_id`, `latest_run_status`).

### Execution

```bash
TOKEN=$(cat /tmp/dev-bearer.txt)
curl -s -H "Authorization: Bearer $TOKEN" \
  "http://127.0.0.1:8200/v1/agent/conversations" > conversations.json
```

### Deterministic invariants

| #     | Assertion                                                                                                                      |
| ----- | ------------------------------------------------------------------------------------------------------------------------------ |
| INV-1 | HTTP 200. Body has `items` or similar list.                                                                                    |
| INV-2 | Items are sorted descending by `updated_at`. The first item is the most recently active conversation.                          |
| INV-3 | Each item carries the public projection fields.                                                                                |
| INV-4 | `latest_run_id` and `latest_run_status` are present (currently null in dev — same projection bug as 7.1 INV-7).                |
| INV-5 | Pagination — if there are more conversations than the page size, the response includes a `next_cursor` or pagination metadata. |
| INV-6 | Items belong to the calling user's org (cross-org isolation is enforced — `org_id` on every item equals the bearer's org).     |

### Content rubric

N/A.

---

## Scenario 7.5 — Conversation restore (after archive / soft-delete)

### Goal

Verify the archive → restore lifecycle:

- POST `/v1/agent/conversations/{id}/restore` un-archives a conversation.
- An archived conversation does NOT appear in the default list but IS retrievable via direct ID.

### Execution

```bash
TOKEN=$(cat /tmp/dev-bearer.txt)
CONV_ID=$(curl -s -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"title":"S? archive+restore"}' http://127.0.0.1:8200/v1/agent/conversations \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['conversation_id'])")

# Soft delete / archive (verify the exact endpoint shape in openapi)
curl -s -X DELETE -H "Authorization: Bearer $TOKEN" \
  "http://127.0.0.1:8200/v1/agent/conversations/$CONV_ID" > archive.json

# List — should not include it
curl -s -H "Authorization: Bearer $TOKEN" \
  "http://127.0.0.1:8200/v1/agent/conversations" > list-after-archive.json

# Restore
curl -s -X POST -H "Authorization: Bearer $TOKEN" \
  "http://127.0.0.1:8200/v1/agent/conversations/$CONV_ID/restore" > restore.json

# List — should include it again
curl -s -H "Authorization: Bearer $TOKEN" \
  "http://127.0.0.1:8200/v1/agent/conversations" > list-after-restore.json
```

### Deterministic invariants

| #     | Assertion                                                                                                              |
| ----- | ---------------------------------------------------------------------------------------------------------------------- |
| INV-1 | After archive, the conversation's `deleted_at` or `archived_at` field is set.                                          |
| INV-2 | After archive, the conversation does NOT appear in the default `GET /conversations` list.                              |
| INV-3 | After archive, direct `GET /conversations/{id}` either 404s or returns the archived record (verify intended behavior). |
| INV-4 | After restore, `archived_at` is null and the conversation reappears in the default list.                               |
| INV-5 | All messages and run state are preserved across the archive / restore cycle (no data loss).                            |

### Content rubric

N/A.

---

## Combined pass / fail rollup

- 7.1: positive path passes; INV-7/INV-8 (`latest_run_*` projection) are known broken — separate finding.
- 7.2 / 7.3: contract-only in dev; non-empty path requires environment.
- 7.4: pass gate.
- 7.5: pass gate.
