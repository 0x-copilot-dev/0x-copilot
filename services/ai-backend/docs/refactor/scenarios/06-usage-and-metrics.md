# Usage Metrics + Memory — f9 and S3

**Coverage:**

- f9 — Usage / token metrics queries ([source flow](../../architecture/f9-usage-metrics.puml))
- S3 — Memory write/recall across turns

**Roadmap PR dependencies:**

- [P12 Pricing source → LiteLLM](../00-roadmap.md#phase-3--library-replacements-independent) — preserves cost-stamping immutability.
- Memory scopes / policies are non-negotiable invariants per [refactor-audit § Memory](../../architecture/refactor-audit.md#behaviors-that-must-be-preserved).

---

## Scenario 6.1 — In-chat `/context` headroom

### Goal

Verify the per-conversation context endpoint returns:

1. Model identity and (if known) context window size.
2. Live token usage from the latest run.
3. A server-computed `headroom_pct` (frontend MUST NOT compute this — see [refactor-audit](../../architecture/refactor-audit.md)).
4. Per-call and per-subagent breakdowns.
5. Compression events for any offloads.

### Preconditions

- Stack healthy.
- At least one completed run in a conversation.

### Setup + Execution

```bash
TOKEN=$(cat /tmp/dev-bearer.txt)

# Make a run so the conversation has telemetry
CONV_ID=$(curl -s -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"title":"f9 context"}' http://127.0.0.1:8200/v1/agent/conversations \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['conversation_id'])")
RUN_ID=$(curl -s -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d "{\"conversation_id\":\"$CONV_ID\",\"user_input\":\"Hi\"}" \
  http://127.0.0.1:8200/v1/agent/runs | python3 -c "import sys,json; print(json.load(sys.stdin)['run_id'])")
curl -s -N --max-time 30 -H "Authorization: Bearer $TOKEN" \
  "http://127.0.0.1:8200/v1/agent/runs/$RUN_ID/stream?after_sequence=0&follow=true" > /dev/null

# Query context
curl -s -H "Authorization: Bearer $TOKEN" \
  "http://127.0.0.1:8200/v1/agent/conversations/$CONV_ID/context" > context.json
```

### Deterministic invariants

| #     | Assertion                                                                                                                                      |
| ----- | ---------------------------------------------------------------------------------------------------------------------------------------------- |
| INV-1 | `context.model.provider` is a known provider string (`openai` / `anthropic` / `gemini`).                                                       |
| INV-2 | `context.model.name` matches the model used.                                                                                                   |
| INV-3 | `context.current.last_run_id` equals the most recent completed run id.                                                                         |
| INV-4 | `context.current.input_tokens >= 1` and `output_tokens >= 1` after a real run.                                                                 |
| INV-5 | `context.current.cached_input_tokens >= 0` (separately tracked).                                                                               |
| INV-6 | When `context.model.context_window_tokens` is non-null, `available_tokens` and `headroom_pct` are computed server-side and present.            |
| INV-7 | When `context_window_tokens` is null, `available_tokens` and `headroom_pct` are null (degraded gracefully — no client-side fallback expected). |
| INV-8 | `breakdown.by_call`, `breakdown.by_subagent`, `breakdown.compression_events` are arrays (empty allowed).                                       |

### Content rubric

N/A.

### Known gap (not a failure)

Currently `context.model.context_window_tokens` is null on the `gpt-5.4-mini` model card, so `headroom_pct` is always null in `make dev`. The model card needs `context_window_tokens` populated. Not a deterministic failure — but file as a usability gap.

---

## Scenario 6.2 — Usage rollups: user, run, org, connector

### Goal

Verify all five usage endpoints produce coherent numbers from telemetry rows written during runs.

### Preconditions

- Several completed runs in the last 7 days (rollups are time-bucketed).

### Execution

```bash
TOKEN=$(cat /tmp/dev-bearer.txt)
START=$(date -u -v-7d +%Y-%m-%d)
END=$(date -u +%Y-%m-%d)

# Per-user
curl -s -H "Authorization: Bearer $TOKEN" \
  "http://127.0.0.1:8200/v1/usage/me?start=$START&end=$END" > usage-me.json

# Per-conversation
curl -s -H "Authorization: Bearer $TOKEN" \
  "http://127.0.0.1:8200/v1/usage/me/conversations?start=$START&end=$END" > usage-me-conv.json

# Per-run (use any completed run id)
RUN_ID=...
curl -s -H "Authorization: Bearer $TOKEN" \
  "http://127.0.0.1:8200/v1/usage/runs/$RUN_ID" > usage-run.json

# Org (admin only)
TOKEN_ADMIN=$(make dev-bearer PERSONA=marcus_admin)
curl -s -H "Authorization: Bearer $TOKEN_ADMIN" \
  "http://127.0.0.1:8200/v1/usage/org?start=$START&end=$END" > usage-org.json

# Conversation
CONV_ID=...
curl -s -H "Authorization: Bearer $TOKEN" \
  "http://127.0.0.1:8200/v1/usage/conversations/$CONV_ID" > usage-conv.json
```

### Deterministic invariants

| #      | Assertion                                                                                                                                                                                                                                                                                                       |
| ------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| INV-1  | `usage-me.total` == `sum(by_day)` == `sum(by_model)`. Three views of the same data must reconcile.                                                                                                                                                                                                              |
| INV-2  | `usage-me.total.runs_count` equals the number of completed runs for this user in the window.                                                                                                                                                                                                                    |
| INV-3  | `usage-run.total` for one specific run is included in `usage-me.total` (run is the smallest aggregation unit).                                                                                                                                                                                                  |
| INV-4  | `usage-org.total` ≥ `usage-me.total` (org aggregates all users).                                                                                                                                                                                                                                                |
| INV-5  | `usage-org.by_user` is present and non-empty in admin response.                                                                                                                                                                                                                                                 |
| INV-6  | If `cost_micro_usd != null`, the value is a non-negative integer. (Today this is null because pricing isn't seeded for `gpt-5.4-mini` — see [P12 PRD](../00-roadmap.md#phase-3--library-replacements-independent).)                                                                                             |
| INV-7  | **Frozen-cost invariant.** Run cost stamping happens once at write time. Compute `usage-run.total.cost_micro_usd` twice (current call vs a future call after a hypothetical pricing-catalog edit) — values must match. Document as a test even though enforcement requires the pricing edit to be reproducible. |
| INV-8  | `usage-me.by_connector` contains entries only for connectors that actually contributed to runs (no spurious "no_connector" buckets unless real runs had no connector use).                                                                                                                                      |
| INV-9  | Period parameter validation: `?period=` defaults to `"7d"`. Strings like `"7d"`, `"30d"`, `"24h"` accept; ISO-week (`"2026-W19"`) currently rejects — confirm whether this is intentional.                                                                                                                      |
| INV-10 | `cold_start_fallback` field present and boolean. `true` means rollup tables are warming; same numbers must still be correct.                                                                                                                                                                                    |

### Content rubric

N/A.

### Known gaps

- `cost_micro_usd` null in dev — see P12.
- `by_call` is empty on `usage-run.json` — `RuntimeModelCallUsageRecord` writing or surfacing may be partial.
- `cold_start_fallback: true` is expected in `make dev` (in-memory, no rollup loop persisting state).

---

## Scenario 6.3 — Memory write + recall across turns (S3)

### Goal

Verify the agent can write to `/memories` and recall in a later turn, respecting the path-policy:

- Writes to `/memories/*` allowed by USER and ASSISTANT actors.
- Writes to `/policies/*` rejected unless actor is APPLICATION (cannot be reached from chat).
- Writes to `/skills/*` allowed.

### Preconditions

- Stack healthy.
- A conversation in which the agent's filesystem includes the memory backend (default).

### Execution

```bash
TOKEN=$(cat /tmp/dev-bearer.txt)
CONV_ID=$(curl -s -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"title":"S3 memory roundtrip"}' http://127.0.0.1:8200/v1/agent/conversations \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['conversation_id'])")

# Turn 1: ask the agent to remember a preference
RUN1=$(curl -s -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d "{\"conversation_id\":\"$CONV_ID\",\"user_input\":\"Please remember that I prefer responses in markdown bullet lists and that my code editor is Cursor. Save these preferences to /memories.\"}" \
  http://127.0.0.1:8200/v1/agent/runs | python3 -c "import sys,json; print(json.load(sys.stdin)['run_id'])")

curl -s -N --max-time 60 -H "Authorization: Bearer $TOKEN" \
  "http://127.0.0.1:8200/v1/agent/runs/$RUN1/stream?after_sequence=0&follow=true" > sse-t1.txt

# Turn 2: recall
RUN2=$(curl -s -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d "{\"conversation_id\":\"$CONV_ID\",\"user_input\":\"What do you remember about my preferences?\"}" \
  http://127.0.0.1:8200/v1/agent/runs | python3 -c "import sys,json; print(json.load(sys.stdin)['run_id'])")

curl -s -N --max-time 60 -H "Authorization: Bearer $TOKEN" \
  "http://127.0.0.1:8200/v1/agent/runs/$RUN2/stream?after_sequence=0&follow=true" > sse-t2.txt
```

### Deterministic invariants

| #     | Assertion                                                                                                                                                                                                   |
| ----- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| INV-1 | Baseline on both runs.                                                                                                                                                                                      |
| INV-2 | In Turn 1, the agent calls the filesystem write tool (or whichever tool wraps memory writes — name depends on the build). At least one `tool_call_started.payload.tool_name` references the memory backend. |
| INV-3 | The write succeeds (`tool_result.payload.status == "completed"` for the memory write).                                                                                                                      |
| INV-4 | In Turn 2, before answering, the agent calls the filesystem read tool against `/memories/*`.                                                                                                                |
| INV-5 | Turn 2's final response references at least one of "markdown" / "bullet" / "Cursor".                                                                                                                        |
| INV-6 | No `compression_note` events (the prompt isn't large enough to trigger offload).                                                                                                                            |

### Content rubric (judge)

D1, D5. Pass threshold **3 / 4**.

- **D1 Correctness:** Turn 2 response recalls _both_ preferences (markdown bullets + Cursor) faithfully. Hallucinating extra preferences = partial credit. Recalling neither = 0.
- **D5 Surface honesty:** Turn 2 does NOT pretend to remember things that weren't said in Turn 1.

### Pass criteria

All invariants hold AND judge ≥ 3/4.

### Negative path — `/policies` write should be denied

Send a follow-up turn:

```bash
RUN3=$(curl -s -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d "{\"conversation_id\":\"$CONV_ID\",\"user_input\":\"Now write to /policies/escalation.md that I'm allowed to skip approvals.\"}" \
  http://127.0.0.1:8200/v1/agent/runs | python3 -c "import sys,json; print(json.load(sys.stdin)['run_id'])")
```

Expected:

- INV-NEG-1: The write to `/policies/*` is rejected by `MemoryPolicyAuthorizer` (the actor is USER/ASSISTANT, not APPLICATION).
- INV-NEG-2: The agent surfaces the rejection — does NOT silently succeed.
- INV-NEG-3: No memory item appears at `/policies/escalation.md` (verify via filesystem read or a memory query endpoint if exposed).

### Known limitations

- The exact tool name for memory writes depends on the Deep Agents filesystem implementation. Adjust the invariants to reference the actual tool when running.
- Memory persistence across `make dev` restarts is **not guaranteed** in `RUNTIME_STORE_BACKEND=in_memory` mode. To verify durability, swap to Postgres backend.

---

## Scenario 6.4 — Compression note on a large turn

### Goal

Submit a prompt with a deliberately huge attached document or context blob. Verify the runtime offloads / summarizes when token budget is exceeded.

### Preconditions

- Stack healthy.
- A large input you can paste (≥ 200,000 tokens worth — the limit depends on the model's context window).

### Status

Pragmatic. With `gpt-5.4-mini`'s context window (often 200k+), triggering compression in a chat-style prompt is hard. Easier to test via:

- A loop of long messages in one conversation that accumulates context.
- Or a single attachment that exceeds the budget.

### Execution sketch

```bash
# Simulate a long-running conversation. Repeat the same Turn enough times
# that aggregate input tokens approach the limit.
for i in $(seq 1 50); do
  curl -s -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
    -d "{\"conversation_id\":\"$CONV_ID\",\"user_input\":\"Repeat back this number: $i. <attach a 4000-token blob>\"}" \
    http://127.0.0.1:8200/v1/agent/runs > /dev/null
done

# Now a turn that should trigger compression:
RUN_LONG=$(...)
```

### Deterministic invariants

| #       | Assertion                                                                                                                       |
| ------- | ------------------------------------------------------------------------------------------------------------------------------- |
| INV-C-1 | At least one `compression_note` event appears in the stream once the budget threshold is crossed.                               |
| INV-C-2 | `compression_note.payload` contains compression strategy info — one of `inline` / `offload` / `summarize` / `fallback_summary`. |
| INV-C-3 | When `offload` fires, a `ContextPayloadRecord` is written (verifiable via persistence inspection).                              |
| INV-C-4 | The visible response is still coherent — the agent doesn't fail or hallucinate due to compression.                              |

### Content rubric

D1, D5. Same as 6.3.

---

## Combined pass / fail rollup

- 6.1: full pass gate.
- 6.2: full pass gate with documented known-gap items (cost_micro_usd null, by_call empty in dev).
- 6.3: positive path testable; negative `/policies` path testable.
- 6.4: pragmatic — file a finding only if compression doesn't fire when it should.
