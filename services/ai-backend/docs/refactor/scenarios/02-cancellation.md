# Cancellation — f4

**Coverage:**

- f4 — Run cancellation, mid-flight interrupt ([source flow](../../architecture/f4-cancel.puml))

**Roadmap PR dependencies:**

- No specific roadmap PR is targeted; this scenario tests a pre-existing invariant that is currently **flagged as broken** during the May 2026 verification pass. Either a regression from P1/P2 or a pre-existing bug — code investigation pending.

**Status:** Known issue. The deterministic invariant about ≤1 extra MODEL_DELTA after cancel is **failing** in `make dev` mode. This scenario exists to (a) reproduce the failure, (b) lock in the contract for the eventual fix.

---

## Scenario 2.1 — Cancel after the run is streaming

### Goal

Cancel a long-running turn mid-flight. Verify:

1. Cancel API returns 202 with `status: cancelling` and current `latest_sequence_no`.
2. `run_cancelling` event appears in the stream (advisory).
3. Run halts: at most one extra `model_delta` event after `run_cancelling`.
4. Terminal event is `run_cancelled`. Final run status is `cancelled`.
5. The active run handler's claim is released; the cancel handler's claim is released.

### Preconditions

- Stack healthy.
- `OPENAI_API_KEY` configured.

### Setup

```bash
TOKEN=$(cat /tmp/dev-bearer.txt)
CONV_ID=$(curl -s -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"title":"f4 cancel mid-flight"}' http://127.0.0.1:8200/v1/agent/conversations \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['conversation_id'])")
```

### Execution

```bash
RUN_ID=$(curl -s -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d "{\"conversation_id\":\"$CONV_ID\",\"user_input\":\"Write me a 2000-word essay on the history of distributed computing. Be very thorough and detailed.\"}" \
  http://127.0.0.1:8200/v1/agent/runs | python3 -c "import sys,json; print(json.load(sys.stdin)['run_id'])")

# Stream in background
( curl -s -N --max-time 60 -H "Authorization: Bearer $TOKEN" \
    "http://127.0.0.1:8200/v1/agent/runs/$RUN_ID/stream?after_sequence=0&follow=true" > sse.txt ) &
STREAM_PID=$!

# Cancel after we've seen a few model_deltas (4 seconds is usually enough)
sleep 4
CANCEL_AT=$(date +%s%N)

curl -s -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"requested_by_user_id":"usr_sarah","reason":"verification test"}' \
  "http://127.0.0.1:8200/v1/agent/runs/$RUN_ID/cancel" > cancel-response.json

wait $STREAM_PID

# Authoritative replay
curl -s -H "Authorization: Bearer $TOKEN" \
  "http://127.0.0.1:8200/v1/agent/runs/$RUN_ID" > run.json
curl -s -H "Authorization: Bearer $TOKEN" \
  "http://127.0.0.1:8200/v1/agent/runs/$RUN_ID/events?after_sequence=0" > replay.json
```

### Capture

- `cancel-response.json` — should contain `status: cancelling`, `cancel_requested_at`, `latest_sequence_no`.
- `sse.txt` — full event stream until terminal.
- `replay.json` — authoritative event list.
- `run.json` — final run record.

### Deterministic invariants

| #     | Assertion                                                                                                                                                                   | Currently                                                                                                                                              |
| ----- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------ |
| INV-1 | All baseline invariants (sequence_no contiguous, no PRESENTATION_UPDATED, etc.).                                                                                            | OK                                                                                                                                                     |
| INV-2 | Cancel API responds within 1s with HTTP 200 / 202 and `status == "cancelling"`.                                                                                             | OK                                                                                                                                                     |
| INV-3 | `run_cancelling` event present in `replay.events`.                                                                                                                          | OK                                                                                                                                                     |
| INV-4 | **[FLAG]** Count of `model_delta` events with `sequence_no > cancelling_seq` is ≤ 1.                                                                                        | **FAILING** — observed 3000+ extra MODEL_DELTAs in dev. The active executor does not observe the cancel signal between deltas.                         |
| INV-5 | Terminal event is `run_cancelled`.                                                                                                                                          | OK (after the run completes naturally and the cancel command is finally claimed)                                                                       |
| INV-6 | `run.json.status == "cancelled"`.                                                                                                                                           | OK                                                                                                                                                     |
| INV-7 | `run_cancelled.sequence_no > run_cancelling.sequence_no` AND ideally `< run_completed.sequence_no` (the run should not have a `run_completed` event when it was cancelled). | **FAILING** — `run_completed` is currently fired at `seq=N-1`, then `run_cancelled` at `seq=N`, indicating the run finished before cancel was honored. |

### Diagnostic checks (to triage the failure)

| #      | Check                                                                                                                                                      | What it tells you                                                                                                                                             |
| ------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| DIAG-1 | Inspect `events[run_cancelling.sequence_no .. run_cancelled.sequence_no]`. Are they all `model_delta`? Does `run_completed` appear before `run_cancelled`? | If yes, the run handler ignored the cancel signal entirely.                                                                                                   |
| DIAG-2 | Look at the time delta between `cancel_requested_at` (from cancel response) and `run_cancelling.created_at`.                                               | If > 100ms, the service is slow to _append_ the cancelling event. If < 100ms but the model continues, the event was appended but not observed by the handler. |
| DIAG-3 | Check whether `RuntimeWorker` semaphore is bounded such that the cancel command claim is queued behind the active run.                                     | Worker handler serialization would explain this — verify in `runtime_worker/loop.py`.                                                                         |
| DIAG-4 | Check whether the run handler's loop calls `persistence.get_run(run_id)` (or similar) on each tick to observe cancellation.                                | If it only checks at higher-level node boundaries, the cancel can't interrupt within a single `astream(messages)` call.                                       |

### Content rubric (judge)

N/A — this scenario verifies cancellation semantics, not content. If the run completes despite cancel, the response will be a full essay and not useful as a content judge artifact.

### Pass criteria

- INV-1, INV-2, INV-3, INV-5, INV-6 hold.
- INV-4 and INV-7 are **currently expected to fail**. When the underlying fix lands, this scenario becomes a strict pass gate.

---

## Scenario 2.2 — Cancel before the model streams (early cancel race)

### Goal

Cancel within < 1s of run creation, before `model_call_started` fires. Verify the run is short-circuited cleanly (ideally 0 model_deltas, no `final_response`, no `run_completed` event).

### Preconditions

Same as 2.1.

### Setup

```bash
TOKEN=$(cat /tmp/dev-bearer.txt)
CONV_ID=$(curl -s -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"title":"f4 early cancel"}' http://127.0.0.1:8200/v1/agent/conversations \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['conversation_id'])")
```

### Execution

```bash
RUN_ID=$(curl -s -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d "{\"conversation_id\":\"$CONV_ID\",\"user_input\":\"Write a 3000-word essay on operating systems history. Take your time.\"}" \
  http://127.0.0.1:8200/v1/agent/runs | python3 -c "import sys,json; print(json.load(sys.stdin)['run_id'])")

sleep 0.5

curl -s -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"requested_by_user_id":"usr_sarah"}' \
  "http://127.0.0.1:8200/v1/agent/runs/$RUN_ID/cancel" > cancel-response.json

# Stream from start
curl -s -N --max-time 60 -H "Authorization: Bearer $TOKEN" \
  "http://127.0.0.1:8200/v1/agent/runs/$RUN_ID/stream?after_sequence=0&follow=true" > sse.txt

curl -s -H "Authorization: Bearer $TOKEN" \
  "http://127.0.0.1:8200/v1/agent/runs/$RUN_ID/events?after_sequence=0" > replay.json
```

### Capture

Same as 2.1.

### Deterministic invariants

| #     | Assertion                                                                                                           | Currently                                                                 |
| ----- | ------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------- |
| INV-1 | All baseline invariants.                                                                                            | OK                                                                        |
| INV-2 | `run_cancelling.sequence_no` ≤ 3 (should fire before or around `run_started`).                                      | OK                                                                        |
| INV-3 | No `final_response` event in the replay.                                                                            | **FAILING** currently — run completes naturally before cancel is honored. |
| INV-4 | Count of `model_delta` events == 0 (ideally) or ≤ 1 (acceptable race).                                              | **FAILING** currently.                                                    |
| INV-5 | Terminal is `run_cancelled`. `run.status == "cancelled"`.                                                           | OK                                                                        |
| INV-6 | Total event count ≤ 5 in the happy case (run_queued, run_cancelling, run_cancelled — possibly a stray run_started). | **FAILING** currently — observed thousands of events.                     |

### Pass criteria

- When the underlying cancel fix lands: INV-1 through INV-6 all hold.
- Before the fix: INV-1, INV-2, INV-5 hold; INV-3 / INV-4 / INV-6 documented as known failures.

---

## Scenario 2.3 — Cancel after terminal (idempotency)

### Goal

Posting cancel after a run has already completed should be a no-op (or return a typed `safe_message` like "run already in terminal state"). It must not corrupt the run record or emit additional events.

### Preconditions

- Stack healthy.

### Execution

```bash
TOKEN=$(cat /tmp/dev-bearer.txt)
CONV_ID=$(curl -s -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"title":"f4 cancel idempotent"}' http://127.0.0.1:8200/v1/agent/conversations \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['conversation_id'])")
RUN_ID=$(curl -s -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d "{\"conversation_id\":\"$CONV_ID\",\"user_input\":\"Say hi.\"}" \
  http://127.0.0.1:8200/v1/agent/runs | python3 -c "import sys,json; print(json.load(sys.stdin)['run_id'])")

# Wait for completion
curl -s -N --max-time 30 -H "Authorization: Bearer $TOKEN" \
  "http://127.0.0.1:8200/v1/agent/runs/$RUN_ID/stream?after_sequence=0&follow=true" > /dev/null

# Now cancel
curl -s -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"requested_by_user_id":"usr_sarah"}' \
  "http://127.0.0.1:8200/v1/agent/runs/$RUN_ID/cancel" > cancel-response.json

# Replay before and after — should be identical
curl -s -H "Authorization: Bearer $TOKEN" \
  "http://127.0.0.1:8200/v1/agent/runs/$RUN_ID/events?after_sequence=0" > replay-after-cancel.json
curl -s -H "Authorization: Bearer $TOKEN" \
  "http://127.0.0.1:8200/v1/agent/runs/$RUN_ID" > run.json
```

### Deterministic invariants

| #     | Assertion                                                                                                                                                                  |
| ----- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| INV-1 | Cancel response is HTTP 200/202 OR a 4xx with a typed `safe_message` indicating "already terminal". HTTP 5xx is a failure.                                                 |
| INV-2 | `run.status == "completed"` (cancel does not flip terminal state retroactively).                                                                                           |
| INV-3 | Final `replay.events` event_type is still `run_completed`. No new `run_cancelled` event appended.                                                                          |
| INV-4 | `latest_sequence_no` did not change.                                                                                                                                       |
| INV-5 | No new audit-log entry beyond what would be expected for a no-op (if audit records cancel attempts, it should record a single "rejected" entry — not duplicate run state). |

### Pass criteria

- All invariants hold.

### Known stochasticity

- None — this is a deterministic call.

---

## Re-verifying after the fix

When the cancel cooperation fix lands, run all three scenarios. The combined report should show:

- 2.1: INV-4 and INV-7 pass (at most 1 extra MODEL_DELTA).
- 2.2: INV-3, INV-4, INV-6 pass.
- 2.3: unchanged.

Until that fix lands, **mark 2.1 INV-4 and 2.2 INV-3/4/6 as "known broken"** rather than retrying. Adding noise on a known-broken assertion delays detection of _new_ regressions.
