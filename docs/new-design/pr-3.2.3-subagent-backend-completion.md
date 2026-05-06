# PR 3.2.3 — Subagent surface: backend result-summary projection, token usage footer, per-subagent cancel, audit log

> **Status:** Draft (PRD + Spec) · v1
> **Plan reference:** Wave 3 follow‑up to [`pr-3.2.2-subagent-card-shared-primitive.md`](./pr-3.2.2-subagent-card-shared-primitive.md). Closes the "remaining stuff" callout from PR 3.2.2 §6 (backend better projection) and PR 3.2.1 §6 (token footer + cancel from card). Stops papering over server‑side problems on the frontend.
> **Owner:** ai‑backend (worker‑side heuristic projection + 1 cancel endpoint + 3 audit actions + 1 column write, no migration) · backend‑facade (1 proxy route) · packages/api-types (1 added field) · frontend (drop FE truncation hack from adapter, add token footer + cancel button to `<SubagentCard>`).
> **Size:** **M.** No migration. No new event type. No new worker‑side LLM call (heuristics only — see §3 for the explicit decision). One new endpoint, one new payload field, ~120 LoC of worker projection, ~60 LoC of frontend.
> **Depends on:** ✅ PR 3.2.1 (`useSubagentActivities`, `<details>` pattern), ✅ PR 3.2.2 (`<SubagentCard>` + `subagentCardViewModel`), ✅ PR 1.5 (`SubagentEntry.token_usage`, `runtime_async_tasks`/`runtime_subagent_results`).
> **Reads alongside:**
>
> - [`pr-3.2.1-agents-tab-expandable-timeline.md`](./pr-3.2.1-agents-tab-expandable-timeline.md), [`pr-3.2.2-subagent-card-shared-primitive.md`](./pr-3.2.2-subagent-card-shared-primitive.md), [`pr-1.5-subagent-discovery-workspace-feeds.md`](./pr-1.5-subagent-discovery-workspace-feeds.md).
> - [`services/ai-backend/CLAUDE.md`](../../services/ai-backend/CLAUDE.md) — encryption/RLS/projection rules.
> - [`apps/frontend/CLAUDE.md`](../../apps/frontend/CLAUDE.md) — facade‑only network rule, projection field rule.
>
> **Sibling PRDs:** none. This PR is the close‑out of the subagent‑card surface line.

---

## 0 · TL;DR

PR 3.2.2 fixed the visuals (truncation, line‑clamp, shared card). It did **not** fix the cause: `objective_summary` and `result_summary` are raw passthroughs (the entire user prompt; the entire assistant response, code blocks and all). The frontend papers over this with `flattenForSummary` (regex strip code fences, collapse whitespace, char truncate) — fine as belt‑and‑braces, **bad** as a primary contract. Once the backend ships a real summary, the FE clamps almost never fire and the data quality propagates everywhere (recipient view, audit log export, future Tasks surface, future API consumers).

This PR ships the four "ready and small" items still owed on this surface line:

| #   | Item                                                                                                                                                                                                                                      | Where                          | Why now                                                                                                                                                 |
| --- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------- |
| A   | **Heuristic result‑summary projection at completion** — populate `execution_summary` in `runtime_subagent_results`; stamp it into the live event payload as `summary`.                                                                    | ai‑backend (worker)            | Closes the highest‑leverage data‑quality gap. The result column is already declared and wired through; today it is **always NULL**. (Verified.)         |
| B   | **Persist `objective_summary` to `runtime_async_tasks` at dispatch.** Today it lives only in `runtime_events` payloads — archive reads pull it from event scan.                                                                           | ai‑backend                     | Cheap, makes the column finally do its job, and the existing heuristic (`short_task_summary`) already produces a clean output for it.                   |
| C   | **Token usage footer** in the `<SubagentCard>` disclosure. PR 1.5 already returns `token_usage` per subagent; the FE just doesn't show it.                                                                                                | frontend                       | One small UI element, leverages existing data, completes the verification surface for power users / billing‑aware admins.                               |
| D   | **Per‑subagent cancel from the card.** New `POST /v1/agent/subagents/{task_id}/cancel` resolves to the subagent's run‑id and calls the existing run‑cancel primitive. Card grows a small Cancel button when status is `queued`/`running`. | ai‑backend + facade + frontend | Useful for parallel fleets where one subagent stalls. Cheap to add — the cancellation primitive (`POST /v1/agent/runs/{run_id}/cancel`) already exists. |
| E   | **Audit additions** — `subagent_dispatch`, `subagent_completed`, `subagent_cancelled` rows in `runtime_audit_log` so compliance / debugging has a trail.                                                                                  | ai‑backend                     | Today the audit log only has run‑level events; subagent lifecycle is invisible to compliance reviewers. Hygiene + bank‑review readiness.                |

Explicitly **not** in this PR: LLM‑based summary projection (heuristic v1 ships first; LLM v2 only if heuristics prove insufficient on real traffic — see §3 library evaluation), per‑step deep links, disclosure open‑state persistence, cross‑conversation Tasks surface. All listed in §6.

LoC estimate: ai‑backend ≈ 350 (worker projection + cancel endpoint + audit + column writes) · facade ≈ 30 · api‑types ≈ 20 · frontend ≈ 110 (footer + cancel button + adapter cleanup) — **net new ≈ 510 LoC** plus tests.

---

## 1 · PRD

### 1.1 Problem

Three concrete failures observed after PR 3.2.2:

1. **The frontend is doing the backend's job.** `subagentCardViewModel.flattenForSummary` strips markdown code fences and collapses whitespace before clamp. This works for the card today — but every consumer of `objective_summary` / `result_summary` (recipient view in PR 6.1, future Tasks tab, exports, API clients) has to reimplement the same hack. The data quality should be set once at the boundary where the data is produced.
2. **Wall‑of‑text in `runtime_events` payloads.** `subagent_completed` events carry the full `response_text` in `payload.summary`. Every replay of a long conversation re‑sends megabytes. Bandwidth, cache, and event-size budgets are all hit by this.
3. **Token usage is invisible to the user.** PR 1.5 already returns `token_usage` per subagent (`input_tokens`, `output_tokens`, `cached_input_tokens`, `total_tokens`) from `runtime_model_call_usage`. The card never shows it. Power users (billing‑aware admins, anyone debugging cost) currently have no in‑product way to see "this subagent consumed 8.4k tokens."
4. **Long‑running subagents are uncancellable from the card.** A parallel fleet with one stalled subagent today forces the user to cancel the whole run (kills work in flight on the other two healthy subagents). The `agent_runs` row for each subagent has its own cancellable run‑id; we just don't expose the path.
5. **No audit trail for subagent dispatch / cancel.** `runtime_audit_log` records run starts/completions/failures, approvals, and tool outcomes — but no subagent lifecycle. Bank/gov compliance reviews ask "who dispatched this; when; with what task; who cancelled it" — today the answer is "trace events manually."

### 1.2 Goals

1. **Result summary lands at the source.** When the worker observes `subagent_completed`, it produces a 1–2 sentence heuristic summary of `response_text`, persists it to `runtime_subagent_results.execution_summary` (encrypted), and stamps it into the event payload as the canonical `summary`. The `response_text` keeps the full text; consumers who want the full result fetch it explicitly.
2. **Objective summary lands in `runtime_async_tasks` at dispatch time.** Already heuristically derived in `stream_subagents.task_tool_call_payload()` (`short_task_summary` over `args.description`); we just need to write it through to the row alongside the existing event emit.
3. **Heuristics first; no worker LLM call in v1.** §3 documents the explicit decision and the upgrade ramp.
4. **Frontend simplifies, doesn't disable.** `subagentCardViewModel.flattenForSummary` stays as belt‑and‑braces (in case a future producer slips through with raw text). Char truncation limits stay. CSS clamps stay. Once backend summaries are deployed, the FE clamps almost never fire — but the visual contract is unchanged.
5. **Token footer surfaces the existing data.** The `<SubagentCard>` disclosure summary row gains a small token meta when `view.tokenUsage` is non‑null. Format: `1.2k in · 850 out · 8.4k total` (compact; line‑safe). Nothing ships if the data isn't there.
6. **Subagent cancel is one click.** Cancel button shown only when `status ∈ {queued, running}`. Confirms via a tiny inline confirmation or fires immediately (decision in §2.5). Cancellation propagates as `subagent_cancelled` event, the card transitions to `cancelled` status with the partial timeline preserved.
7. **Audit is end‑to‑end.** Three new audit actions cover dispatch, completion, and cancellation — same append‑only chain as existing actions; same redaction posture; same SIEM export pipeline.

### 1.3 Non‑goals

- ❌ **Worker‑side LLM call** for higher‑quality summaries. Heuristics first; v2 if observed bad.
- ❌ **Migration.** All target columns exist (`runtime_async_tasks.objective_summary` and `runtime_subagent_results.execution_summary`).
- ❌ **Per‑step deep links / URL grammar.** Owns its own PR (PR 3.2.4 if it ships).
- ❌ **Persist disclosure open state across navigation.** Quality‑of‑life paper cut, listed in PR 3.2.1 §6 / PR 3.2.2 §6 follow‑ups.
- ❌ **Cross‑conversation Tasks surface.** Different surface, different aggregation, separate PR.
- ❌ **Cost calculation in the token footer.** `SubagentTokenUsage` doesn't carry cents today; the existing usage rollup PR (`pr-7.2-per-connector-token-attribution.md`) owns cost. v1 shows raw tokens.
- ❌ **Promote `<SubagentCard>` to design‑system.** Premature; per PR 3.2.2 §6.
- ❌ **`subagent_progress` text projection.** v1 only summarizes terminal results; running progress events keep their existing payload shape. (Progress text is already short by design.)
- ❌ **Trim `response_text` size in `runtime_subagent_results`.** Keeping the full result is essential for replay parity, recipient view (PR 6.1), and audit. We add a summary; we don't replace.

### 1.4 Acceptance criteria

| #     | Criterion                                                                                                                                                                                                                                                                                                                                                                                                                                   | Verified by                                      |
| ----- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------ |
| AC‑A1 | When the worker emits `subagent_completed`, `runtime_subagent_results.execution_summary` is set to a heuristic 1–2 sentence summary of `response_text`, encrypted via the existing `FieldCodec.encrypt_v1`, and the event payload's `summary` field carries the same value (≤ 200 chars, no fenced code, no raw newlines).                                                                                                                  | Worker integration test + Postgres adapter test. |
| AC‑A2 | The summary heuristic strips Markdown code fences (` ```lang `…` ``` `), inline backticks, leading whitespace, collapses repeated newlines, and prefers the first sentence + last sentence when both fit in ≤ 200 chars; otherwise falls back to first‑sentence‑only; otherwise the first 200 chars with an ellipsis.                                                                                                                       | Pure unit test of `short_result_summary`.        |
| AC‑A3 | `runtime_events.payload_json_redacted.summary` is bounded ≤ 220 chars after the projector runs. Today the same row carries multi‑KB raw text. (Bandwidth + replay regression.)                                                                                                                                                                                                                                                              | Worker integration test asserts size bound.      |
| AC‑B1 | `runtime_async_tasks.objective_summary` is non‑NULL for every subagent dispatch, equal to the same `short_task_summary` value already stamped into the `subagent_started` event payload. Encrypted via `FieldCodec.encrypt_v1`.                                                                                                                                                                                                             | Postgres adapter test + worker dispatch test.    |
| AC‑B2 | Pre‑existing in‑flight subagents at deploy time still work: read paths fall back to the event payload when the column is NULL (existing behavior is preserved).                                                                                                                                                                                                                                                                             | Backwards‑compat test against a NULL column.     |
| AC‑C1 | When a `SubagentEntry.token_usage` is non‑null, the `<SubagentCard>` disclosure summary row renders one extra meta token: `1.2k in · 850 out · 8.4k total`. Cached input shows as `(150 cached)` only when > 0. When `token_usage` is null, the row is unchanged.                                                                                                                                                                           | RTL test.                                        |
| AC‑D1 | `POST /v1/agent/subagents/{task_id}/cancel` accepts `{reason?: string, requested_by_user_id: string}`, resolves `task_id` to the underlying `agent_runs.id` (via `runtime_async_tasks.run_id`), and calls the existing run‑cancel primitive. Returns `{task_id, status: 'cancelling', cancel_requested_at}`. Cross‑org `task_id` returns `404`. Already‑terminal subagent returns `409` with `safe_error_code='subagent_already_terminal'`. | API contract test + integration test.            |
| AC‑D2 | The `<SubagentCard>` shows a Cancel button **only** when `view.status ∈ {queued, running}`. Click fires the cancel, optimistically transitions the card to `cancelled` (the badge tone + meta line update); on success the live event reducer writes the authoritative state.                                                                                                                                                               | RTL test + integration test.                     |
| AC‑E1 | `runtime_audit_log` has three new actions: `subagent_dispatch`, `subagent_completed`, `subagent_cancelled`. Each row carries `task_id`, `subagent_name`, `parent_run_id`, `actor_user_id`, and the same encryption posture as existing actions. SIEM export (PR 7.1 surface) lists them.                                                                                                                                                    | Audit unit test + SIEM export contract test.     |
| AC‑F1 | The frontend's `subagentCardViewModel.flattenForSummary` stays intact (belt‑and‑braces). When a backend summary lands < 200 chars, the FE clamp doesn't fire; when a producer regresses and emits raw text, the FE still protects the visual.                                                                                                                                                                                               | RTL test (snapshot for both producer paths).     |
| AC‑F2 | RLS / tenant isolation invariants hold: cross‑org `subagent_id` reads return 404 from the new cancel endpoint; the new audit rows scope by `org_id` (RLS enforced); the new column writes go through the existing GUC propagation.                                                                                                                                                                                                          | Postgres adapter test (cross‑org refusal).       |

### 1.5 Risks

| Risk                                                                                                                            | Mitigation                                                                                                                                                                                                                                                                                                                                                                                                      |
| ------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Heuristic result summary is sometimes wrong (over‑truncates, picks a misleading sentence).                                      | We are replacing **a worse status quo** (full code blob), not a perfect summary. Heuristic outputs are bounded ≤ 200 chars, the disclosure preserves the full result via `fullResult`, and the thread always carries the raw assistant response. Real failures here are visible and isolated; LLM upgrade in v2 is a one‑file change in the worker.                                                             |
| Worker LLM call would have produced a better summary; we left quality on the table.                                             | Explicit. Documented in §3. The cost of an LLM call (latency at completion + dollars) and the dependency surface (model client wiring in the worker, retry/timeout policy) is real. Heuristic v1 ships in days; LLM v2 ships when heuristic complaints are concrete.                                                                                                                                            |
| `runtime_events` payload size bound (AC‑A3) breaks an existing replay consumer that reads the full text from the event payload. | Trace shows the event payload `summary` was always advisory; the authoritative full text lives in `runtime_subagent_results.response_text`. The FE reducer (`upsertSubagentActivity`) reads `summary` for the activity row, not the full text. The pane card reads from the entry, not the event. Risk is limited to ad‑hoc consumers; we add a release note + a sample query for "how to fetch the full text." |
| Per‑subagent cancel races the parent run cancel.                                                                                | Cancel is idempotent on the run‑side. If the parent already cancelled, the subagent endpoint sees a terminal status and returns 409 (AC‑D1). If the subagent cancel fires first, the parent continues uninterrupted (other subagents in the fleet keep working — the parent is a separate `agent_runs` row in the supervisor pattern). The existing run‑cancel handler already serializes via the worker queue. |
| Token footer adds visual noise.                                                                                                 | Renders only when `token_usage` is non‑null. Compact format. Single line, never wraps. Behind the disclosure summary row (already metadata‑weighted typography). Tested at narrow viewports.                                                                                                                                                                                                                    |
| Audit volume balloons (3× new action types per subagent).                                                                       | Each subagent emits 2 audit rows (dispatch + terminal). At p95 conversation depth (≤ 3 subagents), that's ≤ 6 extra rows per conversation. Audit table is partitioned + retention‑bounded; this is below the existing tool‑call audit volume. SIEM export is unchanged.                                                                                                                                         |
| Encrypting `objective_summary` / `execution_summary` doubles write cost per subagent.                                           | Already encrypted in the event payload via the same codec. Adding two column writes per subagent (one at dispatch, one at completion) is in the noise versus the rest of the event tail. Postgres write amplification negligible.                                                                                                                                                                               |
| Heuristic result summary leaks user content into a wider distribution (e.g., recipient‑view PR 6.1).                            | Same encryption + same RLS posture as the existing fields. Recipient‑view and SIEM export already handle the FE‑visible field set; no new field surface. Sources‑restricted recipient view (PR 6.1) doesn't expose subagent summaries unless the recipient has the matching connector — same rule applies.                                                                                                      |
| Reducer parses old events (pre‑deploy) where `summary` is huge.                                                                 | The FE reducer is unchanged; it consumes whatever `summary` is. Old events stay in the database with their old size; new events are bounded. No retroactive fix needed (events are immutable). The FE clamp covers the rare archive read.                                                                                                                                                                       |

### 1.6 Unit testing requirements

**ai‑backend:**

- `tests/unit/runtime_worker/test_short_result_summary.py` — pure unit on the new heuristic: code‑fence stripping, sentence picking, length bounds, edge cases (empty, all‑code, very long single sentence, multilingual whitespace).
- `tests/unit/runtime_worker/test_stream_subagents_summary_persist.py` — integration: drive a `subagent_completed` event through the worker pipeline; assert `runtime_subagent_results.execution_summary` is set, encrypted, and equal to the payload `summary`; assert `runtime_events.payload_json_redacted.summary.length ≤ 220`.
- `tests/unit/runtime_worker/test_stream_subagents_objective_persist.py` — assert `runtime_async_tasks.objective_summary` is populated at dispatch.
- `tests/unit/runtime_api/http/test_cancel_subagent_route.py` — accept, 404 on cross‑org, 409 on terminal, 200 on running. Resolution of `task_id → run_id` via `runtime_async_tasks`.
- `tests/unit/runtime_worker/test_audit_subagent_actions.py` — three new audit actions emit rows with the right columns; SIEM export contract unchanged.

**facade:**

- `tests/unit/backend_facade/test_proxy_cancel_subagent.py` — proxy passes through identity headers; rejects without `org_id`/`user_id`; returns 502 on upstream timeout.

**frontend:**

- `apps/frontend/src/features/chat/components/subagents/subagentCardViewModel.test.ts` (extend) — `tokenUsage` passes through unchanged from `SubagentEntry.token_usage` to the view model.
- `apps/frontend/src/features/chat/components/subagents/SubagentCard.test.tsx` (extend) — token footer renders when present, hidden when null; cancel button visible only for running/queued; click invokes `onCancel`; optimistic `cancelling` state.
- `apps/frontend/src/features/chat/components/workspace/AgentsTab.test.tsx` (extend) — `onCancelSubagent` prop wired through, calls the new API client.
- `apps/frontend/src/api/agentApi.test.ts` (extend) — `cancelSubagent(taskId, identity)` returns the typed response.

---

## 2 · Spec

### 2.1 Architecture

```
┌──────────────────────────────────────────────────────────────────────────────┐
│  EXISTING (PR 1.5 + 3.2.1 + 3.2.2)                                           │
│   subagent harness  ──►  stream_subagents.append_subagent_lifecycle_events   │
│         emits                  emits                                         │
│      SUBAGENT_STARTED   ──►   SUBAGENT_PROGRESS  ──►   SUBAGENT_COMPLETED    │
│         ▼                                                  ▼                 │
│   runtime_async_tasks                              runtime_subagent_results  │
│   (objective_summary NULL today)                   (execution_summary NULL)  │
│   runtime_events  ←─ payload.summary = full response_text (KBs)              │
│         ▼ SSE                                                                │
│   FE eventReducer  →  args.activities  →  <SubagentCard> view model          │
│        flattens code fences in JS (PR 3.2.2 hack)                            │
└────────────────────────────────────┬─────────────────────────────────────────┘
                                     │
                                     │ this PR
                                     ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│  NEW                                                                         │
│                                                                              │
│  Worker dispatch path (existing hook):                                       │
│    task_tool_call_payload(args)                                              │
│      ├─ short_task_summary(args.description) → store in event payload        │
│      └─ NEW: persist same value → runtime_async_tasks.objective_summary      │
│           (encrypted via FieldCodec.encrypt_v1, RLS via org_id GUC)          │
│                                                                              │
│  Worker completion path (existing hook):                                     │
│    task_tool_result_payload(payload, ...)                                    │
│      ├─ extract response_text                                                │
│      ├─ NEW: short_result_summary(response_text) → bounded ≤ 200 chars       │
│      ├─ NEW: persist → runtime_subagent_results.execution_summary (encrypted)│
│      ├─ stamp event payload.summary with the bounded summary                 │
│      └─ stamp event payload.full_text_ref = response_text_id (so consumers   │
│         that need the raw text fetch it explicitly)                          │
│                                                                              │
│  Audit additions:                                                            │
│    runtime_audit_log:                                                         │
│      action=subagent_dispatch    on every task_tool_call_payload write       │
│      action=subagent_completed   on every terminal subagent_lifecycle_event  │
│      action=subagent_cancelled   on cancel-subagent endpoint resolve         │
│                                                                              │
│  New endpoint (runtime_api):                                                 │
│    POST /v1/agent/subagents/{task_id}/cancel                                 │
│      → resolve task_id → runtime_async_tasks.run_id                          │
│      → reuse existing run-cancel primitive                                   │
│      → emit SUBAGENT_CANCELLED event                                         │
│      → audit subagent_cancelled                                              │
│                                                                              │
│  Facade proxy:                                                               │
│    POST /v1/agent/subagents/{task_id}/cancel                                 │
│      identity headers preserved; never exposes /internal/v1/*                │
│                                                                              │
│  Frontend:                                                                   │
│    api/agentApi.ts +cancelSubagent(taskId, identity)                         │
│    SubagentCard:                                                             │
│      + token footer (compact: "1.2k in · 850 out · 8.4k total")              │
│      + cancel button (queued/running only) → onCancel?(taskId)                │
│    subagentCardViewModel.fromEntry / fromArgs:                                │
│      + tokenUsage passthrough                                                │
└──────────────────────────────────────────────────────────────────────────────┘
```

### 2.2 Module boundaries

| Layer                                                                           | Module                                                                                                                                                                                                                                                   | Owns                                                                                                                   |
| ------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------- |
| `services/ai-backend/src/runtime_worker/stream_subagents.py`                    | **EXTEND** — `task_tool_call_payload` writes `objective_summary` to `runtime_async_tasks`; `task_tool_result_payload` calls a new `short_result_summary()` and writes `execution_summary` to `runtime_subagent_results`; stamps event payload `summary`. | Heuristic projection at the production boundary. Existing function signatures. ~80 LoC.                                |
| `services/ai-backend/src/runtime_worker/result_summary.py`                      | **NEW** — `short_result_summary(response_text: str) → str` heuristic; pure function.                                                                                                                                                                     | Code‑fence strip, whitespace collapse, sentence picking, char bound. Mirrors the existing `short_task_summary` design. |
| `services/ai-backend/src/agent_runtime/persistence/records/subagents.py`        | **EXTEND** — `SubagentResultRecord.execution_summary` already declared; add a writer in the persistence path.                                                                                                                                            | Type‑safe record contract; encrypted column write.                                                                     |
| `services/ai-backend/src/runtime_adapters/postgres/subagent_store.py`           | **EXTEND** — write paths for `objective_summary` (on dispatch) and `execution_summary` (on completion). Read path already returns these fields per PR 1.5 §AC‑2.                                                                                         | Postgres‑adapter writes through `FieldCodec`; RLS via existing GUC.                                                    |
| `services/ai-backend/src/runtime_worker/audit.py`                               | **EXTEND** — three new actions: `SUBAGENT_DISPATCH`, `SUBAGENT_COMPLETED`, `SUBAGENT_CANCELLED`. Same chain semantics as existing actions.                                                                                                               | Append‑only audit chain; SIEM‑exportable.                                                                              |
| `services/ai-backend/src/runtime_api/http/subagents.py`                         | **NEW** — `POST /v1/agent/subagents/{task_id}/cancel` route. Mirrors the existing `cancel_run` shape. Resolves `task_id → run_id` then calls `RuntimeApiService.cancel_run` internally.                                                                  | Thin shim. Identity from `RuntimeServiceAuthenticator`. Scope‑gated `RUNTIME_USE`.                                     |
| `services/ai-backend/src/runtime_api/schemas/subagents.py`                      | **NEW** — `CancelSubagentRequest`, `CancelSubagentResponse`. Pydantic IO contracts.                                                                                                                                                                      | Single source of truth.                                                                                                |
| `services/ai-backend/src/runtime_api/http/routes.py`                            | **EXTEND** — mount the new router under `/v1/agent`.                                                                                                                                                                                                     |                                                                                                                        |
| `services/backend-facade/src/backend_facade/routes/agent_proxy.py`              | **EXTEND** — proxy entry for `/v1/agent/subagents/{task_id}/cancel`.                                                                                                                                                                                     | Identity headers passthrough; never exposes `/internal/v1/*`.                                                          |
| `packages/api-types/src/index.ts`                                               | **EXTEND** — `CancelSubagentRequest`, `CancelSubagentResponse`. `SubagentTokenUsage` already present.                                                                                                                                                    |                                                                                                                        |
| `apps/frontend/src/api/agentApi.ts`                                             | **EXTEND** — `cancelSubagent(taskId, identity)` HTTP client via facade.                                                                                                                                                                                  |                                                                                                                        |
| `apps/frontend/src/features/chat/components/subagents/subagentCardViewModel.ts` | **EXTEND** — `tokenUsage: SubagentTokenUsage \| null` passthrough on the VM. `fromEntry` populates from `entry.token_usage`; `fromArgs` keeps null (in‑thread doesn't have it).                                                                          | Adapter unchanged in spirit; one new field.                                                                            |
| `apps/frontend/src/features/chat/components/subagents/SubagentCard.tsx`         | **EXTEND** — token meta line in the disclosure summary row; cancel button when status is queued/running.                                                                                                                                                 | Two small additions; no structural change.                                                                             |
| `apps/frontend/src/features/chat/components/workspace/AgentsTab.tsx`            | **EXTEND** — pass `onCancelSubagent` through; mount it on each card.                                                                                                                                                                                     |                                                                                                                        |
| `apps/frontend/src/features/chat/components/workspace/WorkspacePane.tsx`        | **EXTEND** — accept `onCancelSubagent` prop, forward to `AgentsTab`.                                                                                                                                                                                     |                                                                                                                        |
| `apps/frontend/src/features/chat/ChatScreen.tsx`                                | **EXTEND** — wire `cancelSubagent` API call into a new handler; pass to `WorkspacePane`. Mirror the existing `cancelRun` handler shape.                                                                                                                  |                                                                                                                        |

**Not touched:** any migration file (no schema change), any reducer logic, any event variant, any new frontend component beyond the additions to `<SubagentCard>`.

### 2.3 What we do NOT add

- ❌ A worker‑side LLM client. No `Anthropic()` import, no `init_chat_model`, no model auth surface in the worker. Heuristic only.
- ❌ A new event variant. `subagent_cancelled` is already part of `SUBAGENT_COMPLETED` lifecycle (status=`cancelled`); the cancel endpoint emits the existing `SUBAGENT_COMPLETED` with `status='cancelled'` per the existing reducer behavior.
- ❌ A migration. `runtime_subagent_results.execution_summary` and `runtime_async_tasks.objective_summary` exist; we just write them.
- ❌ A new auth scope. The cancel endpoint reuses `RUNTIME_USE`.
- ❌ A new dep. Heuristics use `re` + the existing `truncate_task_summary` pattern.
- ❌ A cost field. The token footer uses raw counts; cost belongs to the existing usage rollup PR.

### 2.4 Heuristic specification — `short_result_summary`

````python
# services/ai-backend/src/runtime_worker/result_summary.py

import re

_CODE_FENCE = re.compile(r"```[\s\S]*?```")
_INLINE_CODE = re.compile(r"`[^`]*`")
_WHITESPACE = re.compile(r"\s+")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")

_MAX_CHARS = 200

def short_result_summary(response_text: str) -> str:
    """Heuristic summary of a subagent's terminal result.

    Strips fenced code, collapses whitespace, picks first + last
    sentence when both fit, otherwise first sentence, otherwise the
    first 200 chars with an ellipsis. Returns "" if input is empty.
    """
    text = response_text or ""
    text = _CODE_FENCE.sub(" ", text)
    text = _INLINE_CODE.sub(" ", text)
    text = _WHITESPACE.sub(" ", text).strip()
    if not text:
        return ""
    if len(text) <= _MAX_CHARS:
        return text
    sentences = [s for s in _SENTENCE_END.split(text) if s]
    if not sentences:
        return _truncate(text, _MAX_CHARS)
    first = sentences[0]
    if len(sentences) > 1:
        last = sentences[-1]
        candidate = f"{first} … {last}"
        if len(candidate) <= _MAX_CHARS:
            return candidate
    return _truncate(first, _MAX_CHARS)


def _truncate(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    cut = value[: limit - 1].rstrip()
    return f"{cut}…"
````

Mirrors the existing `short_task_summary` shape (~30 LoC) — same authorship, same testing posture.

### 2.5 Cancel endpoint

```python
# services/ai-backend/src/runtime_api/http/subagents.py

class SubagentCancelRoutes:
    @classmethod
    async def cancel_subagent(
        cls,
        request: Request,
        task_id: str,
        payload: CancelSubagentRequest,
        org_id: str | None = Query(None),
        user_id: str | None = Query(None),
    ) -> CancelSubagentResponse:
        identity = RuntimeServiceAuthenticator.identity(request, org_id, user_id)
        return await cls.service(request).cancel_subagent(
            identity=identity, task_id=task_id, request=payload
        )
```

`RuntimeApiService.cancel_subagent` resolves `task_id` → `run_id` via the read port (`SubagentStorePort`), then defers to the existing `cancel_run` flow. On success, audits a `subagent_cancelled` row with `task_id`, `subagent_name`, `actor_user_id`, `reason`. The existing run‑cancel handler emits the `SUBAGENT_COMPLETED` event with `status=cancelled` (no new event type).

UX decision (from §1.2 goal #6): **fire immediately, no inline confirmation**. Cancel is reversible in spirit — the timeline is preserved, the subagent finishes gracefully, the user can restart with the same prompt. Adding a confirmation dialog adds friction for a low‑risk action. (Same posture as the run‑level Stop button.)

### 2.6 Token usage footer

```tsx
// SubagentCard.tsx — disclosure summary row addition

{
  view.tokenUsage ? (
    <span className="subagent-card__tokens">
      {compactTokens(view.tokenUsage.input_tokens)} in
      {" · "}
      {compactTokens(view.tokenUsage.output_tokens)} out
      {view.tokenUsage.cached_input_tokens > 0
        ? ` (${compactTokens(view.tokenUsage.cached_input_tokens)} cached)`
        : ""}
      {" · "}
      {compactTokens(view.tokenUsage.total_tokens)} total
    </span>
  ) : null;
}
```

`compactTokens(n)` → `"850"` / `"1.2k"` / `"34.5k"` / `"1.2M"`. ~10 LoC helper.

CSS: a fourth slot on the existing flex row, smaller font (10.5px), monospace, dim color. Doesn't wrap; truncates with ellipsis at narrow widths.

### 2.7 Audit additions

```python
# services/ai-backend/src/runtime_worker/audit.py — extend AuditAction

class AuditAction(StrEnum):
    ...
    SUBAGENT_DISPATCH = "subagent_dispatch"
    SUBAGENT_COMPLETED = "subagent_completed"
    SUBAGENT_CANCELLED = "subagent_cancelled"
```

Each row carries the same `org_id`, `actor_user_id`, `correlation_id` as the existing actions. Extra context lives in the `payload_json_redacted`:

- `subagent_dispatch`: `task_id`, `subagent_name`, `parent_run_id`, `objective_summary` (truncated, encrypted)
- `subagent_completed`: `task_id`, `subagent_name`, `parent_run_id`, `terminal_status`, `duration_ms`, `result_summary` (truncated, encrypted)
- `subagent_cancelled`: `task_id`, `subagent_name`, `parent_run_id`, `reason`

SIEM export contract (PR 7.1 surface) is unchanged — these actions appear in the same paginated query.

### 2.8 Streaming + persistence walk‑through

The data path remains the same as PR 3.2.1 / 3.2.2 — same SSE, same reducer, same `parent_task_id` linkage. The change is:

1. **Dispatch.** Worker emits `subagent_started` event (existing). NEW: at the same boundary, persist `objective_summary` to `runtime_async_tasks` (encrypted). NEW: audit `subagent_dispatch`.
2. **Completion.** Worker emits `subagent_completed` event (existing). NEW: compute `short_result_summary(response_text)`. NEW: persist `execution_summary` to `runtime_subagent_results` (encrypted). NEW: stamp event payload `summary` with the short summary (≤ 200 chars) instead of the raw text. NEW: audit `subagent_completed`.
3. **Cancel.** New endpoint. Resolves task → run, calls existing run‑cancel, emits `SUBAGENT_COMPLETED` with `status=cancelled`. NEW: audit `subagent_cancelled`.
4. **Frontend.** Reducer behavior unchanged. The view model gains `tokenUsage`. The card gains a token footer + a cancel button. The FE truncation hack stays as belt‑and‑braces.

Idempotence + replay parity are preserved: events are immutable; the persisted summary equals the event summary for any subagent dispatched after this PR ships. Pre‑existing rows have NULL summary columns, and read paths fall back to the event payload (AC‑B2).

### 2.9 Failure modes

| Failure                                                                      | Behavior                                                                                                                                                                                                  |
| ---------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `short_result_summary` produces empty string (subagent emitted no text).     | `execution_summary` is left NULL. Event payload `summary` is the existing empty‑state fallback. Card disclosure shows the calm "Single‑shot response — no inner tool calls." (PR 3.2.2 AC‑5) — unchanged. |
| Heuristic strips an inline code identifier the user wanted to see.           | Disclosure body still shows the truncated full result (`view.fullResult`, PR 3.2.2 AC‑4). Thread always carries the assistant message verbatim. No data is destroyed.                                     |
| Cancel fires while the subagent has already terminated.                      | Endpoint returns 409 with `safe_error_code='subagent_already_terminal'`. Card optimistic state reverts (the live event reducer wins; no transition).                                                      |
| Audit row write fails after the event emit succeeds.                         | Existing audit chain semantics: emit‑then‑audit, no two‑phase. We log the error and continue. SIEM ingestion has reconciliation already; same posture as existing actions.                                |
| Encrypted column write fails (codec error).                                  | Worker logs the error and continues. The event payload still carries the summary. The next replay attempt rewrites. We do not fail the run for an audit/persist hiccup.                                   |
| FE clamp fires because backend produced > 200 chars due to a heuristic miss. | Belt‑and‑braces does its job; the user sees a truncated summary. We log the case (instrumentation) and tune the heuristic in‑place.                                                                       |

---

## 3 · Library evaluation

The headline question for this PR is: **should the worker call an LLM to produce the result summary instead of using a heuristic?**

### 3.1 Heuristic vs LLM

| Approach                 | Pro                                                                                                                             | Con                                                                                                                                                                                                                                         |
| ------------------------ | ------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Heuristic (this PR)**  | Zero new deps, zero latency, zero cost, deterministic, easy to test, mirrors existing `short_task_summary`.                     | Sometimes picks a misleading first sentence. Code‑heavy results often produce thin summaries.                                                                                                                                               |
| **LLM (deferred to v2)** | Higher quality summaries, contextually aware, can rewrite "wrote prime checker" instead of "I have written the function below". | New surface in the worker (model client, auth, retry, timeout, cost, rate limit). Cost ≈ $0.0005 / subagent × the parallel‑subagent fan‑out × every conversation. Latency at completion (~ 200ms). One more thing to monitor and roll back. |

**Decision: heuristic v1 ships first.** The worker has no model client today — adding one is a meaningful surface change. The heuristic gets us 80% of the visible value (escape from "wall of code") with 0% of the surface change. If real‑traffic complaints are concrete and reproducible after PR 3.2.3 is in production, v2 wires in the LLM call (one file change in the worker, isolated).

### 3.2 Other libraries evaluated

| Library                       | What it gives                 | Why we don't add it                                                                                                                                                     |
| ----------------------------- | ----------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `nltk` / `spaCy`              | Smarter sentence segmentation | Both are heavyweight (50+ MB downloads) for what `re.split(r"(?<=[.!?])\s+")` does at 95% the quality. Workers stay lean.                                               |
| `tiktoken`                    | Token‑aware truncation        | We're truncating for **display**, not for context budgeting. Char‑based truncation is sufficient. Tiktoken usage already exists on the model‑call side and stays there. |
| `summa` / `sumy`              | Extractive summary algorithms | Both depend on language detection + scikit‑learn‑class deps. Way over budget for "first + last sentence."                                                               |
| `@radix-ui/react-collapsible` | (frontend) animated collapse  | We continue with native `<details>` per PR 3.2.1 §3 / PR 3.2.2 §3.                                                                                                      |

**Decision: zero new deps.** Same posture as PR 3.2.1 / 3.2.2.

---

## 4 · File change summary

```
services/ai-backend/src/runtime_worker/
  stream_subagents.py                              ~+80 LoC   write objective_summary + execution_summary + audit + bound event payload
  result_summary.py                                ~+45 LoC   NEW heuristic
  audit.py                                         ~+25 LoC   3 new actions

services/ai-backend/src/runtime_api/http/
  subagents.py                                     ~+60 LoC   NEW cancel-subagent route
  routes.py                                        ~+5  LoC   mount the router

services/ai-backend/src/runtime_api/schemas/
  subagents.py                                     ~+25 LoC   request/response schemas

services/ai-backend/src/agent_runtime/api/
  runtime_api_service.py                           ~+30 LoC   cancel_subagent service entry

services/ai-backend/src/runtime_adapters/postgres/
  subagent_store.py                                ~+50 LoC   write paths for the two columns

services/ai-backend/tests/                          ~+5 new test files

services/backend-facade/src/backend_facade/routes/
  agent_proxy.py                                   ~+15 LoC   proxy route

packages/api-types/src/
  index.ts                                          ~+20 LoC   CancelSubagent types

apps/frontend/src/api/
  agentApi.ts                                      ~+20 LoC   cancelSubagent client

apps/frontend/src/features/chat/components/subagents/
  subagentCardViewModel.ts                         ~+10 LoC   tokenUsage passthrough
  SubagentCard.tsx                                 ~+50 LoC   token footer + cancel button
  SubagentCard.test.tsx                            ~+60 LoC   new ACs

apps/frontend/src/features/chat/components/workspace/
  WorkspacePane.tsx                                ~+5  LoC   forward onCancelSubagent
  AgentsTab.tsx                                    ~+10 LoC   pass cancel to card
  AgentsTab.test.tsx                               ~+30 LoC   cancel-button visibility tests

apps/frontend/src/features/chat/
  ChatScreen.tsx                                   ~+20 LoC   handler wiring

apps/frontend/src/styles.css                        ~+30 LoC   .subagent-card__tokens, .subagent-card__cancel

# nothing else changes
migrations/                                          0 changes
package.json                                         0 deps added
```

---

## 5 · Verification checklist

- [ ] **ai‑backend tests**: `cd services/ai-backend && PYTHONPATH=src:../../packages/service-contracts/src .venv/bin/python -m pytest tests/unit/runtime_worker/ tests/unit/runtime_api/`
- [ ] **facade tests**: `cd services/backend-facade && PYTHONPATH=src:../../packages/service-contracts/src .venv/bin/python -m pytest`
- [ ] **api-types**: `npm run typecheck --workspace @enterprise-search/api-types`
- [ ] **frontend**: `npm run typecheck --workspace @enterprise-search/frontend && npm run test --workspace @enterprise-search/frontend && npm run build --workspace @enterprise-search/frontend`. Bundle delta ≤ +1 KB gz.
- [ ] **make test** clean (cross‑service smoke).
- [ ] **make dev** end‑to‑end:
  - Run the prime‑checker scenario. Open the workspace pane Agents tab. Card now shows a 1–2 sentence finding (not the full code block). Disclosure summary row shows token meta. Cancel button is hidden (terminal).
  - Run a multi‑subagent fleet (FY26 Q1 launch). Verify three cards each carry their own token footer. Cancel one mid‑flight; verify status flips to `cancelled` and the partial timeline survives.
  - Verify `select * from runtime_audit_log where action like 'subagent_%'` returns rows with the expected shape.
  - Verify `select length(payload_json_redacted->>'summary') from runtime_events where event_type = 'subagent_completed'` is bounded ≤ 220.
  - Verify `select objective_summary, execution_summary from runtime_async_tasks t join runtime_subagent_results r on r.task_id=t.id` is non‑NULL for new rows.
- [ ] **Compliance walkthrough** per [`CLAUDE.md`](../../CLAUDE.md) §Compliance Reviews:
  - Who can dispatch a subagent? (the run owner; same posture as existing tool calls.) **Audited.**
  - Who approved the cancel? (the actor; identity verified by `RuntimeServiceAuthenticator`.) **Audited.**
  - What changed? (subagent run status, optionally the parent run.) **Persisted in `runtime_audit_log`.**
  - Where is it logged? (`runtime_audit_log`, append‑only chain.) **SIEM‑exportable.**
  - How long is it retained? (existing audit retention policy.)
  - How is it deleted? (existing audit retention policy.)
- [ ] No new entry in `package.json` `dependencies` or `services/ai-backend/requirements.txt`.

---

## 6 · Out of scope (follow‑ups)

Tracked, not in this PR:

- **PR 3.2.4 (proposed) — LLM result summary v2.** Wire a fast model (Haiku 4.5 / Sonnet 4) into the worker for higher‑quality result summaries. Triggered when heuristic complaints are concrete. ~150 LoC + retry/timeout policy + cost monitoring.
- **PR 3.2.5 (proposed) — Subagent deep links.** URL grammar `/chat/{cid}?subagent={task_id}&step={event_id}` opens the pane on the right card with the right disclosure expanded. ~80 LoC FE.
- **PR 3.2.6 (proposed) — Disclosure open state persistence.** Persist per‑task open state in conversation UI state slice. ~60 LoC.
- **PR 3.2.7 (proposed) — Tasks surface (cross‑conversation).** "All my running subagents" pane. New surface; new endpoint. ~400 LoC.
- **Cost field on `SubagentTokenUsage`.** Owned by `pr-7.2-per-connector-token-attribution.md`.
- **Subagent dispatch / cancel from outside Atlas.** API client integration tests for third‑party callers. Out of scope until external API surface stabilizes.
- **Promote `<SubagentCard>` to design‑system.** Per PR 3.2.2 §6 — premature.

---

## References

- [`docs/new-design/pr-3.2.1-agents-tab-expandable-timeline.md`](./pr-3.2.1-agents-tab-expandable-timeline.md), [`pr-3.2.2-subagent-card-shared-primitive.md`](./pr-3.2.2-subagent-card-shared-primitive.md), [`pr-1.5-subagent-discovery-workspace-feeds.md`](./pr-1.5-subagent-discovery-workspace-feeds.md).
- [`services/ai-backend/src/runtime_worker/stream_subagents.py`](../../services/ai-backend/src/runtime_worker/stream_subagents.py) — completion hook (lines 237–314), existing heuristic suite (lines 470–517).
- [`services/ai-backend/migrations/0001_initial_runtime_persistence.sql`](../../services/ai-backend/migrations/0001_initial_runtime_persistence.sql) — `runtime_async_tasks`, `runtime_subagent_results` schemas.
- [`services/ai-backend/src/runtime_worker/audit.py`](../../services/ai-backend/src/runtime_worker/audit.py) — existing audit action enum (lines 32–62).
- [`services/ai-backend/src/runtime_api/http/routes.py`](../../services/ai-backend/src/runtime_api/http/routes.py) — `cancel_run` handler shape (lines 283–298).
- [`packages/api-types/src/index.ts`](../../packages/api-types/src/index.ts) — `SubagentTokenUsage` (lines 1696–1701), `SubagentEntry` (1694–1708).
- [`apps/frontend/src/features/chat/components/subagents/SubagentCard.tsx`](../../apps/frontend/src/features/chat/components/subagents/SubagentCard.tsx) — primitive that grows the new footer + cancel button.
- [`apps/frontend/src/api/agentApi.ts`](../../apps/frontend/src/api/agentApi.ts) — `cancelRun` shape we mirror (lines 420–433).
