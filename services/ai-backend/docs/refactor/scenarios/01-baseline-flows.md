# Baseline Flows — f1, f2, f3

**Coverage:**

- f1 — single-turn message, no tools ([source flow](../../architecture/f1-single-turn.puml))
- f2 — multi-turn with a built-in tool ([source flow](../../architecture/f2-multi-turn-tool.puml))
- f3 — SSE resume after disconnect ([source flow](../../architecture/f3-sse-resume.puml))

**Roadmap PR dependencies:**

- [P1](../00-roadmap.md#phase-1--performance-wins-no-structural-change) PresentationGenerator polish removal — must hold.
- [P2](../00-roadmap.md#phase-1--performance-wins-no-structural-change) SSE bus / delivery — must hold.
- [P3](../00-roadmap.md#phase-1--performance-wins-no-structural-change) Parallel `create_agent_runtime` bootstrap — should improve `prompt_build_ms`.

---

## Scenario 1.1 — Single-turn greeting (f1)

### Goal

Verify a "hello"-shaped turn produces exactly the expected event sequence with monotonic `sequence_no`, no polish, and closes cleanly on `run_completed`.

### Preconditions

- Stack healthy per [`00-index.md`](00-index.md#environment-setup-run-once-per-session).
- `OPENAI_API_KEY` configured. Default model `gpt-5.4-mini`.

### Setup

```bash
TOKEN=$(cat /tmp/dev-bearer.txt)
CONV_ID=$(curl -s -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"title":"f1 baseline"}' http://127.0.0.1:8200/v1/agent/conversations \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['conversation_id'])")
```

### Execution

```bash
RUN_ID=$(curl -s -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d "{\"conversation_id\":\"$CONV_ID\",\"user_input\":\"Hi! Just say hello back in one short sentence.\"}" \
  http://127.0.0.1:8200/v1/agent/runs | python3 -c "import sys,json; print(json.load(sys.stdin)['run_id'])")

curl -s -N --max-time 60 -H "Authorization: Bearer $TOKEN" \
  "http://127.0.0.1:8200/v1/agent/runs/$RUN_ID/stream?after_sequence=0&follow=true" > sse.txt
curl -s -H "Authorization: Bearer $TOKEN" \
  "http://127.0.0.1:8200/v1/agent/runs/$RUN_ID/events?after_sequence=0" > replay.json
```

### Capture

- `sse.txt` (full SSE stream until terminal)
- `replay.json` (authoritative event list from replay endpoint)
- Final response: extract `events[-2].payload.message` (or the `final_response.summary`)

### Deterministic invariants

| #     | Assertion                                                                                                                                                                         |
| ----- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| INV-1 | Event count ≥ 6.                                                                                                                                                                  |
| INV-2 | Event-type sequence (positional) starts with `[run_queued, run_started, model_call_started]` and ends with `[..., final_response, run_completed]`.                                |
| INV-3 | Between `model_call_started` and `final_response`, every event is either `model_delta` or (optionally) `model_call_completed` or `reasoning_summary*`. No `presentation_updated`. |
| INV-4 | `sequence_no` is contiguous monotonic starting at 1.                                                                                                                              |
| INV-5 | **Phase 1 invariant.** Across the run, `sum(e.event_type == 'presentation_updated') == 0` AND every `e.presentation is None`.                                                     |
| INV-6 | `replay.run_status == "completed"`.                                                                                                                                               |
| INV-7 | SSE stream closed naturally (curl exited zero or hit max-time after terminal).                                                                                                    |
| INV-8 | Each SSE frame's `id:` line value matches its `data.sequence_no`.                                                                                                                 |

### Content rubric (judge)

Dimensions D1 (Correctness), D3 (Tool restraint), D6 (Brevity). Pass threshold **5 / 6**.

- **D1 Correctness:** The reply is a greeting in one short sentence. Treat any of "Hello", "Hi", "Hey there", etc. as correct; "Goodbye" / "I can't help" as wrong.
- **D3 Tool restraint:** No tool calls fired. Score 0 if any `tool_call_started` events appear.
- **D6 Brevity:** Response is one sentence (≤ 25 words).

### Pass criteria

- All deterministic invariants hold AND judge total ≥ 5.

### Known limitations

- None — this scenario is fully exercised by `make dev`.

---

## Scenario 1.2 — Built-in tool call: connector suggestion (f2)

### Goal

Verify the model calls `suggest_mcp_connector` when the user mentions a third-party service that isn't already authenticated, that the tool lifecycle emits clean `tool_call_started → tool_call_delta* → tool_call_completed → tool_result`, and that no PRESENTATION_UPDATED follows the tool result.

### Preconditions

- Stack healthy.
- No MCP server currently _authenticated_ (Linear may be installed-unauthenticated; that's fine).

### Setup

```bash
TOKEN=$(cat /tmp/dev-bearer.txt)
CONV_ID=$(curl -s -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"title":"f2 builtin tool"}' http://127.0.0.1:8200/v1/agent/conversations \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['conversation_id'])")
```

### Execution

```bash
RUN_ID=$(curl -s -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d "{\"conversation_id\":\"$CONV_ID\",\"user_input\":\"Can you help me connect to Linear so I can search tickets?\"}" \
  http://127.0.0.1:8200/v1/agent/runs | python3 -c "import sys,json; print(json.load(sys.stdin)['run_id'])")

curl -s -N --max-time 90 -H "Authorization: Bearer $TOKEN" \
  "http://127.0.0.1:8200/v1/agent/runs/$RUN_ID/stream?after_sequence=0&follow=true" > sse.txt
```

### Capture

- `sse.txt`, `replay.json` as above.
- Tool names called: `tools = [e for e in events if e.event_type == 'tool_call_started']`.

### Deterministic invariants

| #     | Assertion                                                                                                                                                                                                                     |
| ----- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| INV-1 | All baseline invariants from Scenario 1.1 (sequence_no contiguous, terminal closes, no PRESENTATION_UPDATED, `presentation is None` everywhere).                                                                              |
| INV-2 | At least one `tool_call_started` with `payload.tool_name == "suggest_mcp_connector"`.                                                                                                                                         |
| INV-3 | For each `tool_call_started`, there is exactly one later `tool_call_completed` with the same `payload.call_id`, and at least one `tool_result` with that `call_id`.                                                           |
| INV-4 | `tool_call_delta` events for a given `call_id` are contiguous and precede `tool_call_completed`.                                                                                                                              |
| INV-5 | `mcp_auth_required` event with `payload.approval_kind == "mcp_auth"` and `payload.server_id == "seed:linear"` is emitted from source `tool`. This is an inline suggestion (non-blocking) and must NOT cause the run to pause. |
| INV-6 | Final run status is `completed`. The run does NOT transition to `waiting_for_approval`.                                                                                                                                       |
| INV-7 | `activity_kind` on `tool_call_*` events is `"tool"`. `activity_kind` on `mcp_auth_required` is `"mcp_auth"`.                                                                                                                  |

### Content rubric (judge)

Dimensions D1, D2, D3, D5, D6. Pass threshold **8 / 10**.

- **D1 Correctness:** Response explains Linear isn't yet connected and points to a Connect action.
- **D2 Tool choice:** `suggest_mcp_connector` was used (not e.g. `ask_a_question`).
- **D3 Tool restraint:** Tool called once for Linear, not multiple times.
- **D5 Surface honesty:** Response does NOT pretend tickets were searched, does NOT invent ticket titles.
- **D6 Brevity:** Response is short — one-two sentences pointing to the inline Connect card.

### Pass criteria

- All deterministic invariants hold AND judge total ≥ 8.

### Known stochasticity / failure modes

- Model occasionally double-calls `suggest_mcp_connector` (once for Linear, once if it speculates Jira). Treat as soft-fail unless `tool_call_started` count > 3.
- Model may emit reasoning before the tool call, inflating delta count. Not a failure unless `tool_call_delta` count exceeds 200.

---

## Scenario 1.3 — SSE resume after disconnect (f3)

### Goal

Verify the resume contract: reconnecting with `?after_sequence=N` yields exactly the events with `sequence_no > N`, with zero overlap to the prior connection and zero gap, ending with a clean terminal close.

### Preconditions

- Stack healthy.

### Setup

```bash
TOKEN=$(cat /tmp/dev-bearer.txt)
CONV_ID=$(curl -s -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"title":"f3 resume"}' http://127.0.0.1:8200/v1/agent/conversations \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['conversation_id'])")
```

### Execution

```bash
RUN_ID=$(curl -s -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d "{\"conversation_id\":\"$CONV_ID\",\"user_input\":\"Count from one to ten in words, one per line. Then say goodbye.\"}" \
  http://127.0.0.1:8200/v1/agent/runs | python3 -c "import sys,json; print(json.load(sys.stdin)['run_id'])")

# Phase 1: connect, disconnect after 2s
curl -s -N --max-time 2 -H "Authorization: Bearer $TOKEN" \
  "http://127.0.0.1:8200/v1/agent/runs/$RUN_ID/stream?after_sequence=0&follow=true" > phase1.txt || true

P1_LAST=$(grep '^id:' phase1.txt | tail -1 | awk '{print $2}')

# Phase 2: reconnect from P1_LAST until terminal
curl -s -N --max-time 60 -H "Authorization: Bearer $TOKEN" \
  "http://127.0.0.1:8200/v1/agent/runs/$RUN_ID/stream?after_sequence=$P1_LAST&follow=true" > phase2.txt

# Authoritative replay
curl -s -H "Authorization: Bearer $TOKEN" \
  "http://127.0.0.1:8200/v1/agent/runs/$RUN_ID/events?after_sequence=0" > replay.json
```

### Capture

- `phase1.txt`, `phase2.txt`, `replay.json`
- Extract `phase1_seqs`, `phase2_seqs`, `server_seqs` (list of sequence_no values from each).

### Deterministic invariants

| #     | Assertion                                                                                                                                   |
| ----- | ------------------------------------------------------------------------------------------------------------------------------------------- |
| INV-1 | All baseline invariants (no polish, etc.).                                                                                                  |
| INV-2 | `phase1_seqs` is a contiguous prefix starting at 1.                                                                                         |
| INV-3 | `phase2_seqs[0] > phase1_seqs[-1]` — Phase 2 starts strictly after Phase 1 ended.                                                           |
| INV-4 | `set(phase1_seqs) ∩ set(phase2_seqs) == ∅` — no duplicate event delivery.                                                                   |
| INV-5 | `sorted(set(phase1_seqs) ∪ set(phase2_seqs)) == server_seqs` — union matches the authoritative log; no gap, no duplicate, no missing event. |
| INV-6 | Phase 2 final event is one of the terminal types and SSE stream closed naturally.                                                           |

### Content rubric (judge)

Skip — this scenario tests delivery semantics, not content. (If you want a content gate: D1 = "agent counted 1–10 in words then said goodbye", D6 = "no padding.")

### Pass criteria

- All deterministic invariants hold.

### Known limitations

- The `--max-time 2` cutoff is a proxy for a network disconnect. In a real client the disconnect would be triggered by closing the TCP socket — same observable effect.
- If Phase 1 captures 0 events (model started slower than 2s), bump max-time to 4s and re-run. Don't lower it — < 1s often races with the first `run_queued`.

### Notes for the `follow=false` variant

Outside this scenario, `?follow=false` is contracted to return a single synthetic `heartbeat` envelope (`metadata.transient=true`) and close. A separate scenario (or extension here) should verify:

- Exactly one `heartbeat` envelope in the response.
- `metadata.transient == true`.
- Stream closes immediately after that one frame regardless of run status.

---

## Combined pass / fail rollup

A baseline regression report should include all three scenarios. The set passes when:

- Every deterministic invariant across 1.1 / 1.2 / 1.3 holds.
- Judge scores: 1.1 ≥ 5/6, 1.2 ≥ 8/10. (1.3 has no content score.)
