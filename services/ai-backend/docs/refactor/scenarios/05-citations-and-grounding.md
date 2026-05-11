# Citations and Grounding — f5

**Coverage:**

- f5 — Citations across MCPs, a subagent, and provider grounding ([source flow](../../architecture/f5-citations.puml))

**Roadmap PR dependencies:**

- [P7 Batch citation ingestion](../00-roadmap.md#phase-2--decoupling-foundation--hygiene) — must preserve invariants below.
- [P14 Citation infrastructure consolidation (8 files → 3)](../00-roadmap.md#phase-4--targeted-decoupling) — same invariants, harder refactor.

**Status:** DEFERRED in `make dev` mode. Citations require either:

- A connected, **authenticated** MCP server returning tool results that include `doc_id`/`url`/`title`/snippet metadata, OR
- A provider with native grounding metadata (Gemini grounding, OpenAI web search) configured and enabled.

`make dev` defaults to OpenAI-only with no MCP authentication. The citation event types (`source_ingested`, `sources_ingested`, `citation_made`) are present in the schema but cannot be triggered today.

This file documents the _expected_ behavior so that when the path is unblocked, verification is mechanical.

---

## Scenario 5.1 — Multi-MCP turn with shared ordinals

### Goal

A single turn that queries two different MCP servers (e.g. Linear + Notion). Verify that:

1. Each tool result's cited sources funnel through `CitationLedger` once (idempotent).
2. `ConversationOrdinalAllocator` issues ordinals `[[1]]`, `[[2]]`, `[[3]]`, … across both servers — the ordinal namespace is conversation-scoped, not per-server.
3. `source_ingested` events fire per-source with `payload.connector` set correctly.
4. The model's final response uses `[[N]]` markers that match the ledger.
5. The final response payload's `citations` array is sealed (deterministic given the same `(run_id, connector, doc_id)` set) at the moment `final_response` is emitted.

### Preconditions

- Linear and Notion (or two MCPs) both installed AND authenticated (`auth_state == "valid"`).
- Both servers respond to a search/query tool with structured results that include source metadata (`doc_id`, `url`, `title`, optional snippet).

### Execution

```bash
TOKEN=$(cat /tmp/dev-bearer.txt)
CONV_ID=$(curl -s -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"title":"f5 multi-mcp citations"}' http://127.0.0.1:8200/v1/agent/conversations \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['conversation_id'])")

PROMPT="Find any Linear tickets and any Notion docs about the Q3 launch plan, then summarize the risks. Cite each claim."

RUN_ID=$(curl -s -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d "{\"conversation_id\":\"$CONV_ID\",\"user_input\":\"$PROMPT\"}" \
  http://127.0.0.1:8200/v1/agent/runs | python3 -c "import sys,json; print(json.load(sys.stdin)['run_id'])")

curl -s -N --max-time 180 -H "Authorization: Bearer $TOKEN" \
  "http://127.0.0.1:8200/v1/agent/runs/$RUN_ID/stream?after_sequence=0&follow=true" > sse.txt
curl -s -H "Authorization: Bearer $TOKEN" \
  "http://127.0.0.1:8200/v1/agent/runs/$RUN_ID/events?after_sequence=0" > replay.json
curl -s -H "Authorization: Bearer $TOKEN" \
  "http://127.0.0.1:8200/v1/agent/conversations/$CONV_ID/sources" > sources.json
```

### Capture

- `sse.txt`, `replay.json`
- `sources.json` — workspace Sources tab projection (`SourceAggregate`s grouped by connector + doc_id).

### Deterministic invariants

| #      | Assertion                                                                                                                  |
| ------ | -------------------------------------------------------------------------------------------------------------------------- |
| INV-1  | Baseline.                                                                                                                  |
| INV-2  | At least two `tool_call_started` events with distinct `payload.tool_name` values, one per MCP server.                      |
| INV-3  | At least two `source_ingested` events (or one `sources_ingested` if batched per P7) — one set per connector.               |
| INV-4  | `source_ingested.payload.connector` is one of `"linear"`, `"notion"` (matching the tool calls).                            |
| INV-5  | `final_response.payload.citations` is non-empty. Length matches the number of unique `(connector, doc_id)` pairs ingested. |
| INV-6  | Within `final_response.payload.citations`, ordinals are dense `[1, 2, 3, ...]` with no gaps.                               |
| INV-7  | Ordinal ↔ source mapping is consistent: each `[[N]]` in the final response text corresponds to `citations[N-1]`.           |
| INV-8  | Idempotency: the same `(run_id, connector, doc_id)` does not produce duplicate `source_ingested` events.                   |
| INV-9  | `sources.json` projection contains every unique `(connector, doc_id)` from the run, grouped correctly.                     |
| INV-10 | `citation_made` events (when emitted via `CitationResolver`) reference an ordinal that exists in the ledger.               |

### Content rubric

D1, D2, D4, D5. Pass threshold **6 / 8**.

- **D1 Correctness:** Each risk claim is supported by a Linear ticket or Notion doc actually returned by the tools.
- **D2 Tool choice:** Both Linear and Notion tools were called, not just one.
- **D4 Faithfulness:** Every `[[N]]` marker in the response refers to a real ingested source. No hallucinated citations.
- **D5 Surface honesty:** If a connector returned zero results, the response acknowledges this rather than fabricating.

### Pass criteria

All invariants hold AND judge ≥ 6/8.

### Failure modes / common drifts

- Model emits `[[N]]` markers that don't appear in the sealed `citations` array — D4 fails.
- Two `source_ingested` for the same `(connector, doc_id)` — INV-8 fails. Likely idempotency bug.
- Final response `citations` array order doesn't match insertion order — INV-7 fails. Likely a sealing/ordering bug.

---

## Scenario 5.2 — Subagent-shared citation namespace

### Goal

A research-style prompt that delegates to a subagent. Verify that subagents:

1. Inherit the same `conversation_id`.
2. Use the **same** `ConversationOrdinalAllocator` instance (or one keyed identically) — ordinals don't reset per subagent.
3. Their tool calls' citations land in the same conversation-wide ledger.

### Preconditions

- At least one subagent definition seeded (currently NOT available in dev).
- At least one MCP server authenticated.

### Status

DEFERRED (subagent seeding required).

### Execution

```bash
PROMPT="Use the research-analyzer subagent to investigate customer escalations from Q3 — pull Linear tickets and Notion docs. Give me an action plan."

RUN_ID=$(curl -s -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d "{\"conversation_id\":\"$CONV_ID\",\"user_input\":\"$PROMPT\"}" \
  http://127.0.0.1:8200/v1/agent/runs | python3 -c "import sys,json; print(json.load(sys.stdin)['run_id'])")
```

### Deterministic invariants

In addition to 5.1:

| #        | Assertion                                                                                                                                                                                                                                       |
| -------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| INV-SA-1 | `subagent_fleet_started` and `subagent_started` events fire.                                                                                                                                                                                    |
| INV-SA-2 | At least one `tool_call_started` event has `source == "subagent"` (or a `subagent_id` populated on the envelope) showing the subagent made tool calls.                                                                                          |
| INV-SA-3 | Citations ingested _during the subagent's execution_ use ordinals that continue the supervisor's namespace — not start from 1. Concretely: if the supervisor ingested 3 sources before delegating, the subagent's first ingestion is ordinal 4. |
| INV-SA-4 | `final_response.payload.citations` collected across the entire conversation (supervisor + subagent) is one sealed list.                                                                                                                         |
| INV-SA-5 | `subagent_completed` includes a `SubagentResult` with `execution_summary` containing tool-call counts; the count matches the actual `tool_call_started` events with that `subagent_id`.                                                         |

---

## Scenario 5.3 — Provider grounding citations (Gemini)

### Goal

For a Gemini-grounded turn (web search), verify the [`CitationStreamPipeline`](../../../src/agent_runtime/execution/providers/citation_pipeline.py) extracts URL/title/snippet metadata from the provider stream and feeds it through `CitationLedger` with `payload.connector == "web"`.

### Preconditions

- `GOOGLE_API_KEY` set.
- A Gemini grounding-capable model has `configured: true` in `GET /v1/agent/models`.

### Status

DEFERRED until Gemini key is in `make dev` defaults.

### Execution

```bash
RUN_ID=$(curl -s -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d "{\"conversation_id\":\"$CONV_ID\",
       \"user_input\":\"What were the top open-source LLM releases in the last 30 days? Cite each one.\",
       \"model\":{\"id\":\"gemini-2.5-pro\"}}" \
  http://127.0.0.1:8200/v1/agent/runs | python3 -c "import sys,json; print(json.load(sys.stdin)['run_id'])")
```

### Deterministic invariants

| #       | Assertion                                                                                           |
| ------- | --------------------------------------------------------------------------------------------------- |
| INV-1   | Baseline.                                                                                           |
| INV-G-1 | At least one `source_ingested` with `payload.connector == "web"`.                                   |
| INV-G-2 | `source_ingested.payload.url` is a real URL (`https://` prefix, not a placeholder).                 |
| INV-G-3 | Zero `reasoning_summary*` events (Gemini has no reasoning summary stream — different from 3.1/3.2). |
| INV-G-4 | Final response cites web sources with `[[N]]` markers.                                              |
| INV-G-5 | `final_response.payload.citations[i].connector == "web"` for grounded entries.                      |

---

## Scenario 5.4 — Crash recovery: registry rebuild from store

### Goal

If the worker crashes mid-run, the citation registry must rebuild from `CitationStorePort.list_for_run(run_id)` when the run resumes. This proves the in-memory ledger is a _cache_ and the store is authoritative.

### Preconditions

- Postgres backend (`RUNTIME_STORE_BACKEND=postgres`) so a real crash + restart is possible. In-memory backend can't simulate this cleanly.

### Status

DEFERRED to a future hardening pass. Probably tested as part of [P17 LangGraph Checkpointer adoption](../00-roadmap.md#phase-5--major-library-swaps--structural-shifts) since checkpoint state and citation state interact.

### Steps (sketch)

1. Submit a multi-tool MCP turn (as 5.1).
2. After the first `source_ingested` arrives, SIGKILL the worker.
3. Restart the worker. The run command claim should re-fire.
4. Verify: the resumed run does NOT re-emit `source_ingested` for the already-ingested source (idempotency on `(run_id, connector, doc_id)`).
5. Final `citations` array has correct ordinals across the crash boundary.

---

## Combined pass / fail rollup

All four sub-scenarios are deferred today. When the environment supports them:

- 5.1 is the primary gate for the citation contract.
- 5.2 gates the subagent-namespace invariant — _must_ pass before [P14 Citation infrastructure consolidation](../00-roadmap.md#phase-4--targeted-decoupling) ships.
- 5.3 / 5.4 are environment-dependent.

### What this scenario file pins for refactor work

Even without runs, [P14](../00-roadmap.md#phase-4--targeted-decoupling) (8 citation files → 3) must preserve **every** invariant above. The PRD for P14 should explicitly reference INV-3, INV-6, INV-7, INV-8, INV-SA-3, INV-SA-4 as required tests before merge.
