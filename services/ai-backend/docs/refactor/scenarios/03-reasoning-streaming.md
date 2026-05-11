# Reasoning Streaming — f6

**Coverage:**

- f6 — Reasoning / "thinking" model turn ([source flow](../../architecture/f6-thinking.puml))

**Roadmap PR dependencies:**

- Touches the provider stream adapter contract — relevant to [P20 LiteLLM provider streaming](../00-roadmap.md#phase-5--major-library-swaps--structural-shifts) verification.

**Status:** Known flag. OpenAI Responses adapter currently emits no `reasoning_summary*` events and no `model_call_completed` envelope despite `gpt-5.4-mini` advertising `supports_reasoning: true` with `reasoning.summary: "auto"`. Anthropic and Gemini branches are deferred (provider keys not configured by default in `make dev`).

---

## Scenario 3.1 — OpenAI reasoning summary streaming (gpt-5.4-mini)

### Goal

Submit a problem that benefits from reasoning. Verify:

1. `model_call_started` and `model_call_completed` bookend the model call.
2. `reasoning_summary_delta` events stream during the reasoning phase, followed by a terminal `reasoning_summary` carrying the full text.
3. `usage_metadata` in the final response includes `reasoning_tokens` when the provider bills separately.
4. `display: "OMITTED"` (a future Anthropic case) skips emitting reasoning events but still bills tokens (covered in 3.2).

### Preconditions

- Stack healthy.
- `OPENAI_API_KEY` configured.
- Default model is `gpt-5.4-mini`. Verify via `GET /v1/agent/models`:
  - `models[0].id == "gpt-5.4-mini"`
  - `models[0].supports_reasoning == true`
  - `models[0].reasoning.summary == "auto"`

### Setup

```bash
TOKEN=$(cat /tmp/dev-bearer.txt)
CONV_ID=$(curl -s -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"title":"f6 reasoning"}' http://127.0.0.1:8200/v1/agent/conversations \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['conversation_id'])")
```

### Execution

A reasoning-heavy prompt — avoid anything the model can answer with a single template.

```bash
PROMPT="Three people split a restaurant bill: Person A had \$24.50, Person B had \$31.90, Person C had \$31.00 in pre-tax items. Each pays their pre-tax amount plus a fair share of 8.5% tax and 22% tip. Show exactly what each person pays, rounded to the nearest cent. Verify the totals reconcile to the grand total."

RUN_ID=$(curl -s -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d "{\"conversation_id\":\"$CONV_ID\",\"user_input\":\"$PROMPT\"}" \
  http://127.0.0.1:8200/v1/agent/runs | python3 -c "import sys,json; print(json.load(sys.stdin)['run_id'])")

curl -s -N --max-time 120 -H "Authorization: Bearer $TOKEN" \
  "http://127.0.0.1:8200/v1/agent/runs/$RUN_ID/stream?after_sequence=0&follow=true" > sse.txt
curl -s -H "Authorization: Bearer $TOKEN" \
  "http://127.0.0.1:8200/v1/agent/runs/$RUN_ID/events?after_sequence=0" > replay.json
```

### Capture

- `sse.txt`, `replay.json`.
- `usage`: extract from `final_response.payload.performance_metrics.usage` (and `model_call_completed.payload.usage` once it's emitted).

### Deterministic invariants

| #     | Assertion                                                                                                                                                 | Currently                                                       |
| ----- | --------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------- |
| INV-1 | All baseline invariants (sequence_no contiguous, no PRESENTATION_UPDATED, etc.).                                                                          | OK                                                              |
| INV-2 | Exactly one `model_call_started` event.                                                                                                                   | OK                                                              |
| INV-3 | Exactly one `model_call_completed` event with `payload.usage` populated.                                                                                  | **FAILING** — no `model_call_completed` event observed in dev.  |
| INV-4 | At least one `reasoning_summary_delta` event with `activity_kind == "reasoning"`, `visibility == "user"`.                                                 | **FAILING** — zero `reasoning_summary*` events observed in dev. |
| INV-5 | Exactly one `reasoning_summary` event that closes the reasoning channel, emitted before `final_response`.                                                 | **FAILING.**                                                    |
| INV-6 | Cumulative `reasoning_summary_delta.payload.delta` text concatenated equals `reasoning_summary.payload.summary` (or the canonical full reasoning string). | Pending dependency on INV-4/INV-5.                              |
| INV-7 | `final_response.payload.performance_metrics.usage` includes a `reasoning_tokens` field when reasoning was performed.                                      | **FAILING** — `reasoning_tokens` not in payload.                |
| INV-8 | Terminal is `run_completed`.                                                                                                                              | OK                                                              |

### Content rubric (judge)

Dimensions D1, D6. Pass threshold **3 / 4**.

- **D1 Correctness:** The answer is numerically correct.
  - Pre-tax total: 87.40
  - Each person's amount: pre-tax × (1 + 0.085 + 0.22) = pre-tax × 1.305
  - A: 24.50 × 1.305 = 31.9725 → $31.97
  - B: 31.90 × 1.305 = 41.6295 → $41.63
  - C: 31.00 × 1.305 = 40.455 → $40.46 (banker's rounding → $40.46) — accept either $40.45 or $40.46 depending on rounding convention
  - Grand total: ~114.06 (= 87.40 × 1.305). Accept rounding within $0.01.
- **D6 Brevity:** No padding. Shows each person's calculation and a reconciliation line. Not a 1000-word essay.

### Pass criteria

- When the reasoning streaming fix lands: INV-1 through INV-8 hold AND judge ≥ 3/4.
- Today: INV-1, INV-2, INV-8 hold; INV-3 through INV-7 are documented as **known failing** on the OpenAI adapter for `gpt-5.4-mini`.

### Diagnostic checks

| #      | Check                                                                                                                                                                                                                                                                                     | What it tells you                                                                                                                |
| ------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------- |
| DIAG-1 | Check the OpenAI Responses adapter ([`execution/providers/openai_responses_stream_adapter.py`](../../../src/agent_runtime/execution/providers/openai_responses_stream_adapter.py)) for whether it surfaces `summary_text.delta` / `summary_text.done` event types from the Responses API. | If unhandled, all reasoning events are dropped on the floor.                                                                     |
| DIAG-2 | Inspect the raw OpenAI stream to confirm reasoning summary parts are emitted in the first place.                                                                                                                                                                                          | Some `gpt-5.4-mini` configurations don't emit summaries at all; the configured reasoning effort + summary mode must enable them. |
| DIAG-3 | Check that `usage_metadata` from the Responses API is propagated to `RuntimeRunUsageRecord.reasoning_tokens`.                                                                                                                                                                             | If the column exists but the writer is dropping the field, this is a serialization bug.                                          |
| DIAG-4 | Check whether the `model_call_completed` envelope is being emitted but filtered (e.g. by visibility, by activity_kind, by the `from_stream` adapter).                                                                                                                                     | If the persistence layer drops it, it would never reach replay.                                                                  |

---

## Scenario 3.2 — Anthropic `thinking_mode` streaming (DEFERRED)

### Goal

Anthropic Claude branch: verify `thinking_mode ∈ {ENABLED, ADAPTIVE}` produces a thinking-delta stream with `display ∈ {OMITTED, SUMMARIZED}` controlling client visibility.

### Preconditions

- `ANTHROPIC_API_KEY` set.
- A Claude reasoning model (`claude-opus-4-7`) has `configured: true` in `GET /v1/agent/models`.
- Model selection request points to the Claude profile.

### Status

DEFERRED — `make dev` ships only an OpenAI key by default. Add the Anthropic key to `services/ai-backend/.env` and select the Claude model on the run to exercise this branch.

### Execution

```bash
# Pin to Claude
RUN_ID=$(curl -s -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d "{\"conversation_id\":\"$CONV_ID\",
       \"user_input\":\"<reasoning prompt as 3.1>\",
       \"model\":{\"id\":\"claude-opus-4-7\"}}" \
  http://127.0.0.1:8200/v1/agent/runs | python3 -c "import sys,json; print(json.load(sys.stdin)['run_id'])")
```

### Deterministic invariants

Add to baseline:

- INV-A1: When `thinking_mode == ENABLED` and `display == SUMMARIZED`, `reasoning_summary_delta` events fire (same as 3.1 INV-4).
- INV-A2: When `display == OMITTED`, **zero** `reasoning_summary*` events fire, but `model_call_completed.payload.usage.reasoning_tokens` is still > 0.
- INV-A3: When `thinking_mode == ADAPTIVE`, events fire only on the turns the provider decided to reason. Trying to assert always-on or always-off is wrong.

### Content rubric

Same as 3.1.

---

## Scenario 3.3 — Gemini grounding (no reasoning summary) (DEFERRED)

### Goal

Verify the Gemini branch correctly _does not_ emit `reasoning_summary*` events (Gemini has grounding metadata, not a reasoning summary), but does extract grounding citations through [`CitationStreamPipeline`](../../../src/agent_runtime/execution/providers/citation_pipeline.py).

### Preconditions

- `GOOGLE_API_KEY` set.
- `gemini-2.5-pro` has `configured: true`.

### Status

DEFERRED — Gemini key not in `make dev` defaults.

### Deterministic invariants

- INV-G1: Zero `reasoning_summary*` events (correct Gemini behavior — they have no reasoning summary stream).
- INV-G2: At least one `source_ingested` event with `payload.connector == "web"` when the prompt is groundable.
- INV-G3: Final response references the grounded sources via `[[N]]` markers and the final `payload.citations` array is non-empty.

---

## Combined pass / fail rollup

- Today: 3.1 passes only the baseline invariants. 3.2 / 3.3 are deferred. File a finding for the four `INV-3..INV-7` deltas and link to the diagnostic checks above.
- After OpenAI Responses adapter fix: 3.1 becomes a strict pass gate.
- After provider keys are added: 3.2 / 3.3 become live gates.
