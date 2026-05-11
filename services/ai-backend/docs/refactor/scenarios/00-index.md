# Verification Scenarios — Index

**Purpose.** Repeatable scenarios that verify Phase 1 + Phase 2 refactor behavior preservation and exercise the agentic surface end-to-end. Each scenario separates **deterministic invariants** (must-pass — regression flags) from **content quality** (LLM-judge scored — stochastic outputs).

**These are test specs, not test results.** Outcomes are not pinned to a specific run. Re-run the scenario, judge the new transcript, file a finding only if a deterministic invariant fails or the judge score falls below threshold.

**Source-of-truth references**

- Source flows: [`docs/architecture/f1-single-turn.puml`](../../architecture/f1-single-turn.puml) … [`f9-usage-metrics.puml`](../../architecture/f9-usage-metrics.puml).
- Refactor audit: [`docs/architecture/refactor-audit.md`](../../architecture/refactor-audit.md).
- Roadmap: [`docs/refactor/00-roadmap.md`](../00-roadmap.md).

---

## How to read a scenario file

Every scenario file has the same shape:

1. **Goal** — what behavior this scenario proves.
2. **Audit references** — which roadmap PRs / refactor-audit findings this scenario verifies.
3. **Preconditions** — what state must exist before running.
4. **Setup steps** — concrete API calls, in order.
5. **Execution steps** — the actual scenario actions.
6. **Capture** — what to collect (SSE log, replay log, run record, conversation projection, etc.).
7. **Deterministic invariants** — assertions that must hold; failure = regression.
8. **Content rubric (LLM judge)** — qualitative dimensions to grade the final response.
9. **Pass criteria** — combined deterministic pass + minimum judge score.
10. **Known limitations** — what this scenario _cannot_ prove in current dev environment.
11. **Cleanup** — what to delete if anything (most scenarios are stateless).

---

## Environment setup (run once per session)

```bash
# Kill any running stack
lsof -ti:8000,8100,8200,5173 | xargs -r kill && sleep 3

# Restart
make dev > /tmp/make-dev.log 2>&1 &

# Wait for health (curl any path that's not /health — 404 means the service is up)
for i in $(seq 1 60); do
  a=$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8000/openapi.json)
  if [ "$a" = "200" ]; then echo "ai-backend ready"; break; fi
  sleep 2
done

# Mint dev bearer
TOKEN=$(make dev-bearer)
echo "$TOKEN" > /tmp/dev-bearer.txt
```

Default persona is `sarah_acme` (org `org_acme`, user `usr_sarah`). For admin paths use:

```bash
TOKEN=$(make dev-bearer PERSONA=marcus_admin)
```

All calls go through the facade at `http://127.0.0.1:8200`. Never call `:8000` or `:8100` directly even in dev.

---

## Required environment for full coverage

| Provider / dependency                              | Required for                                       | Dev default                                   |
| -------------------------------------------------- | -------------------------------------------------- | --------------------------------------------- |
| `OPENAI_API_KEY` set                               | f1, f2, f3, f4, f6, S1–S4, S7                      | Required by `make dev` (`check-provider-key`) |
| `ANTHROPIC_API_KEY` set                            | f6 (Anthropic reasoning branch)                    | Optional                                      |
| `GOOGLE_API_KEY` set                               | f5 (Gemini grounding branch)                       | Optional                                      |
| Linear or other MCP registered + **authenticated** | f5 (MCP citations), f8 (auth interrupt mid-flight) | Not available without real OAuth              |
| ≥3 subagent definitions seeded                     | Parallel subagent fan-out scenarios                | Not available in dev                          |

Scenarios mark sections **DEFERRED** where their hard path requires environment that isn't reachable in `make dev` mode. Don't delete the scenarios — when the environment is upgraded they become live.

---

## LLM-as-judge framework

Outputs from the agent are stochastic. Use an LLM judge to grade content quality on a fixed rubric per scenario.

### Judge prompt template

Pass this template to a strong model (Claude Opus 4.7 or GPT-5.4) along with the user's input, the agent's final response, the full event log, and the scenario's rubric.

```
You are evaluating an agent's response against a documented scenario.

USER_INPUT:
<verbatim user message>

AGENT_FINAL_RESPONSE:
<final_response.summary from the event stream>

EVENT_TRACE (event_type sequence, in order):
<comma-separated event_type list, e.g. run_queued, run_started, tool_call_started, ...>

TOOLS_CALLED (name, count):
<list, e.g. suggest_mcp_connector(1)>

SCENARIO_GOAL:
<copied from scenario file>

CONTENT_RUBRIC:
<copied from scenario file — each dimension with description>

For each rubric dimension, score 0/1/2:
  0 = absent / wrong direction
  1 = partial / has issues
  2 = clearly meets the expectation

Output strict JSON:
{
  "scores": { "<dim>": {"score": 0|1|2, "reason": "..."}, ... },
  "total": <sum>,
  "verdict": "pass" | "soft-fail" | "hard-fail",
  "notes": "any qualitative observations not captured above"
}

Verdict rule:
  - hard-fail: any deterministic invariant failed (caller asserts; judge does NOT override)
  - pass: total >= scenario.pass_threshold
  - soft-fail: total < scenario.pass_threshold, no deterministic invariant failed
```

### Standard rubric dimensions

Scenarios reuse these where applicable. Each scenario can add specific dimensions.

- **D1 Correctness** — Did the response answer the asked question with factually correct content?
- **D2 Tool choice** — Did the agent select the right tools / connectors for the task?
- **D3 Tool restraint** — Did the agent avoid unnecessary tool calls (no spurious suggest_mcp_connector when no connector was asked for)?
- **D4 Faithfulness** — When citations / sources were available, did the agent attribute claims rather than hallucinate?
- **D5 Surface honesty** — Did the agent reveal limitations (e.g. "Linear isn't connected yet") rather than fabricate?
- **D6 Brevity** — Response length proportionate to question (no padding, no excessive disclaimers).
- **D7 Format compliance** — If the user asked for a specific format (table, markdown, numbered list), was it honored?

### Pass thresholds

| Scenario class                   | Rubric dims        | Pass threshold | Notes                       |
| -------------------------------- | ------------------ | -------------- | --------------------------- |
| Baseline (greeting, single tool) | D1, D3, D6         | 5 / 6          | Tight — these are easy      |
| Multi-tool / multi-turn          | D1, D2, D3, D5, D6 | 8 / 10         | Tool restraint matters      |
| Reasoning / citation             | D1, D2, D4, D5     | 6 / 8          | Faithfulness weighted       |
| Error / ambiguity handling       | D1, D5, D6         | 5 / 6          | Surface honesty key         |
| Subagent fan-out                 | D1, D2, D3, D6     | 6 / 8          | Tool restraint matters more |

---

## Scenario inventory

| File                                                                           | Scenarios                                                                                                                                                  | Phase coverage                                               |
| ------------------------------------------------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------ |
| [`01-baseline-flows.md`](01-baseline-flows.md)                                 | **f1** single-turn, **f2** built-in tool, **f3** SSE resume                                                                                                | P1 polish removal, P2 SSE delivery                           |
| [`02-cancellation.md`](02-cancellation.md)                                     | **f4** mid-flight cancel (**KNOWN FLAG** — interrupt not honored)                                                                                          | P1 / pre-existing cancel cooperation                         |
| [`03-reasoning-streaming.md`](03-reasoning-streaming.md)                       | **f6** reasoning summary streaming (**KNOWN FLAG** — events not surfaced for gpt-5.4-mini)                                                                 | Provider stream adapter contract                             |
| [`04-mcp-lifecycle.md`](04-mcp-lifecycle.md)                                   | **f7** install via catalog, **f8** unauthed call interrupt (DEFERRED until real OAuth)                                                                     | MCP registry boundary; backend↔ai-backend split              |
| [`05-citations-and-grounding.md`](05-citations-and-grounding.md)               | **f5** multi-source citations (DEFERRED until authed MCP / Gemini grounding)                                                                               | Citation ledger + ordinal allocator                          |
| [`06-usage-and-metrics.md`](06-usage-and-metrics.md)                           | **f9** usage rollups, **S3** memory write/recall (limited)                                                                                                 | Usage projection + memory scopes                             |
| [`07-conversation-state.md`](07-conversation-state.md)                         | **S1** multi-turn history, **S5** subagents endpoint, **S6** drafts endpoint, **latest_run_id** projection check                                           | Conversation projection                                      |
| [`08-ambiguity-errors-and-shares.md`](08-ambiguity-errors-and-shares.md)       | **S2** ask_a_question, **S7** typed error paths, **S8** share + fork lifecycle                                                                             | Error mapping, share/fork                                    |
| [`09-multi-tool-and-subagent-fanout.md`](09-multi-tool-and-subagent-fanout.md) | **S4** sequential multi-tool chain, **tool grouping parallel** (parallel tool calls), **3–4 parallel subagent fan-out**, **subagent nested tool research** | Deep Agents delegation; parallel tool calls; subagent runner |

---

## Pinned deterministic invariants (apply to every scenario)

These are the universal invariants. Any scenario failing any of these is a hard fail before judging content.

1. **Sequence integrity.** For any run, `[e.sequence_no for e in events]` equals `list(range(1, len(events)+1))` — monotonic, contiguous, starting at 1.
2. **Phase 1 — no LLM polish.** Across all events in any run, `sum(e.event_type == 'presentation_updated' for e in events) == 0`. Every `presentation` field on every envelope is null (envelope-level `display_title` / `summary` / `status` / `activity_kind` carry the rendering payload instead).
3. **SSE wire format.** Every frame matches `event: <name>\nid: <int>\ndata: <json>\n\n`. The `id` value equals the envelope's `sequence_no`.
4. **Terminal closes the stream.** When the last event is in `{run_completed, run_cancelled, run_failed, run_rejected}`, the SSE connection closes naturally.
5. **Resume idempotency.** Reconnect with `?after_sequence=N` returns only events with `sequence_no > N`, with zero overlap and zero gap against the prior connection.
6. **Cost stamping immutability.** Once stamped, `cost_micro_usd` and `total.*` on `RuntimeRunUsageRecord` rows for a completed run must not change. Subsequent pricing-catalog edits do not retroactively rewrite history.
7. **Append-only audit.** Every audit-relevant write produces an `AuditLogRecord` whose hash chains to the previous record (verified independently via `packages/audit-chain/`).

Each scenario references the subset of these that's tested in its capture step and adds scenario-specific invariants.

---

## Recording a scenario run

For each execution capture into a dated folder so judges and reviewers can replay:

```
runs/<YYYY-MM-DD>/<scenario-id>/<run-id>/
  metadata.json    # scenario id, timestamps, persona, model_id
  request.json     # CreateRunRequest body (or other endpoint body)
  sse.txt          # raw SSE stream (curl --max-time output)
  replay.json      # GET /v1/agent/runs/{run_id}/events?after_sequence=0
  conversation.json # GET /v1/agent/conversations/{conversation_id}
  judge.json       # judge output (the JSON shape above)
```

When filing a finding, link to this folder.

---

## Where scenarios live in the refactor flow

Each scenario file declares which roadmap PRs depend on it not regressing. After a roadmap PR lands:

1. Re-run the affected scenarios.
2. File a hard-fail finding (block merge) if any deterministic invariant fails.
3. File a soft-fail finding (investigate but don't block) if content rubric falls below threshold.
4. Update the scenario file if the new behavior is _intentional_ — never silently accept a drift.

---

_Subsequent files (01-… through 09-…) hold the actual scenarios._
