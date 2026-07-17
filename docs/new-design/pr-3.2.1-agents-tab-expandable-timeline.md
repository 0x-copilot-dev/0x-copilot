# PR 3.2.1 — Agents tab: expandable per‑subagent timeline

> **Status:** Draft (PRD + Spec) · v1
> **Plan reference:** Wave 3 follow‑up to [`pr-3.2-workspace-pane-right-rail.md`](./pr-3.2-workspace-pane-right-rail.md). Picks up the deferred "subagent‑internal tool listing" non‑goal from [`pr-1.5-subagent-discovery-workspace-feeds.md`](./pr-1.5-subagent-discovery-workspace-feeds.md) §1.3.
> **Owner:** frontend (1 selector hook + 1 component extension + ~80 LoC CSS) · packages: **none** (no new dep) · ai‑backend: **none** · backend‑facade: **none** · api‑types: 1 small contract addition (re‑export an existing type).
> **Size:** **S.** No migration. No new event type. No new endpoint. No new dep. The entire feature is composed from primitives already in the tree — `upsertSubagentActivity` ([`contentBuilders.ts:274`](../../apps/frontend/src/features/chat/chatModel/contentBuilders.ts#L274)), `SubagentActivityList` ([`SubagentActivityList.tsx`](../../apps/frontend/src/features/chat/components/tools/SubagentActivityList.tsx)), `ActivityCollapsible` ([`ActivityCollapsible.tsx`](../../apps/frontend/src/features/chat/components/activity/ActivityCollapsible.tsx)), and the chat tree itself.
> **Depends on:**
>
> - ✅ PR 1.5 subagent feed endpoint + `useSubagents` hook — **shipped** ([`workspace.py:31`](../../services/ai-backend/src/runtime_api/http/workspace.py#L31), [`useSubagents.ts:32`](../../apps/frontend/src/features/chat/components/workspace/useSubagents.ts#L32)).
> - ✅ PR 3.2 workspace pane shell + AgentsTab card layout — **shipped** ([`AgentsTab.tsx`](../../apps/frontend/src/features/chat/components/workspace/AgentsTab.tsx)).
> - ✅ `upsertSubagentActivity` already nests `parent_task_id`‑linked tool/reasoning events into `run_subagent.args.activities` — **shipped** ([`eventReducer.ts:103`](../../apps/frontend/src/features/chat/chatModel/eventReducer.ts#L103)).
>
> **Reads alongside:**
>
> - [`pr-1.5-subagent-discovery-workspace-feeds.md`](./pr-1.5-subagent-discovery-workspace-feeds.md) — feed contract for `SubagentEntry`.
> - [`pr-3.2-workspace-pane-right-rail.md`](./pr-3.2-workspace-pane-right-rail.md) — pane host pattern, auto‑open semantics, single‑source‑of‑truth principle.
> - [`apps/frontend/CLAUDE.md`](../../apps/frontend/CLAUDE.md) — facade‑only network rule, projection field rule (no event‑name‑prefix derivation).
> - [`services/ai-backend/CLAUDE.md`](../../services/ai-backend/CLAUDE.md) — `parent_task_id` semantics, encrypted‑at‑rest fields.
>
> **Sibling PRDs (Wave 3 follow‑ups):** [`pr-3.5-w1-w3-gap-closure.md`](./pr-3.5-w1-w3-gap-closure.md). No merge order — independent.

---

## 0 · TL;DR

The Agents tab in the workspace pane currently renders a flat list of `SubagentEntry` cards (one per dispatched subagent) with status, objective, result, and a "↗" jump‑to‑thread button. The design's prototype ([`composer.jsx:233-265`](../../docs/new-design/0-OVERALL_PLAN.md) — bundle reference; `AgentsPane`) shows each card click‑expanding to reveal **the per‑step timeline of what that subagent did internally** — its tool calls, reasoning beats, and intermediate results, in order.

The feature is **already plumbed end‑to‑end**. We just don't show it.

| Layer                 | What's there today                                                                                                                                                                                   | Gap                                                                                       |
| --------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------- |
| Worker emit           | `runtime_worker/stream_subagents.py` emits `tool_call_started/completed`, `reasoning_summary*` etc. with `parent_task_id` set to the subagent's `task_id`. Already happens.                          | None.                                                                                     |
| Persistence           | `runtime_events.parent_task_id` indexed; `runtime_tool_invocations.task_id` FK to `runtime_async_tasks`. Already happens.                                                                            | None.                                                                                     |
| Replay on re‑open     | `GET /v1/agent/runs/{run_id}/events` already replays in `sequence_no` order; FE reducer is idempotent on `event_id`.                                                                                 | None.                                                                                     |
| FE reducer            | `upsertSubagentActivity(items, event)` mutates the assistant message tree — finds the `run_subagent` tool part by `toolCallId === parent_task_id` and appends to `args.activities`. Live + replayed. | None.                                                                                     |
| In‑thread render      | `SubagentTool.tsx` reads `args.activities` and feeds `SubagentActivityList`, which renders `aui-tool-card__timeline`.                                                                                | None.                                                                                     |
| Pane render (this PR) | `AgentsTab.tsx` only sees `SubagentEntry` (objective + result + duration). It does **not** see `args.activities` because that data lives on the chat tree, not on `SubagentSnapshotMap`.             | **One missing selector** that walks the chat tree and projects `Map<task_id, activities>` |
| Disclosure/expand UX  | `ActivityCollapsible` (native `<details>` / `<summary>`) is the codebase's standard expand primitive — already accessible, keyboard‑navigable, no extra dep.                                         | None.                                                                                     |
| Timeline visual       | `aui-tool-card__timeline` BEM block already styled (left rail, dot, connector). Used in `SubagentTool` today.                                                                                        | A second variant class for the pane's narrower width.                                     |

**Net change:** one tiny derived selector hook (`useSubagentActivities`), one prop addition on `AgentsTab` (`activitiesByTask`), one collapsible wrapper inside the existing card, ~80 LoC of CSS to scope the timeline to the pane width. **Zero backend, zero contract migration, zero new dep.**

LoC estimate (excluding tests): frontend ≈ 180 (hook + AgentsTab edits + CSS variant) · api‑types ≈ 5 (re‑export `SubagentActivityRecord` for completeness) · backend ≈ 0 · facade ≈ 0. **Net new ≈ 185 LoC** plus tests.

---

## 1 · PRD

### 1.1 Problem

PR 1.5 (subagent feed) explicitly deferred this:

> **Non‑goal — Subagent‑internal tool listing.** The Agents tab card shows objective + status + result summary + duration + token usage. The full step‑by‑step timeline lives inside the chat thread (`SubagentTool` already renders it); the workspace pane links there. Designing a duplicate timeline in the pane is out of scope.

The user's call: that "link to the thread" interaction is wrong for non‑engineer enterprise users (the persona Atlas was built for — see [`docs/new-design/0-OVERALL_PLAN.md`](./0-OVERALL_PLAN.md)). When Sarah dispatches three subagents in parallel and later wants to verify _what the doc reader actually pulled from Notion_, today she has to:

1. Notice the subagent in the Agents tab.
2. Click "↗" — the thread scrolls (sometimes hundreds of lines) to a `<SubagentTool>` block.
3. Find that block visually, expand it, read the timeline.
4. Scroll back up to the conversation she was reading.

The thread is the wrong location for verification. The pane is the right one — it is **the workspace's verification surface**, exactly as Sources, Draft, and Approvals are. Every other tab in the pane is self‑sufficient (Sources lists docs with snippets, Draft shows the artifact, Approvals queues decisions). Agents is the only one that asks the user to leave the pane to read its own data.

A second concern: the existing in‑thread `SubagentTool` is sized for the chat column width (≥ 600 px). On narrow viewports (< 1100 px, when the workspace pane is pinned and the chat column is squeezed), `SubagentTool`'s timeline already wraps awkwardly. A pane‑native rendering avoids that.

### 1.2 Goals

1. **One click to expand inside the pane.** Each Agents tab card becomes an `<details>` disclosure. Closed: title + status + objective + result summary + duration (today's layout). Open: append the timeline of nested tool calls / reasoning beats, in order, with title + summary + result + status, exactly as `SubagentTool` shows in‑thread.
2. **Reuse, do not duplicate.** The timeline rendering is `SubagentActivityList` — already shipped, already tested, already styled, already feeds off `SubagentActivityRecord`. We **do not** introduce a parallel `WorkspaceSubagentTimeline` component.
3. **Zero new fetch.** The data is already on the chat tree. We add a derived selector that walks all assistant messages, finds `run_subagent` tool parts, and returns `Map<task_id, SubagentActivityRecord[]>`. The selector is pure and runs against the same store the thread reads from — single source of truth.
4. **Zero new dep.** `<details>`/`<summary>` (via `ActivityCollapsible`) is the existing pattern. Radix `Collapsible`, Headless UI `Disclosure`, React Aria `useDisclosure` evaluated and rejected (see §4).
5. **Live updates without bookkeeping.** When a subagent is mid‑flight and a new tool call event arrives, `upsertSubagentActivity` already mutates the tree → React re‑renders → the selector re‑computes → the pane card's timeline grows in place. No additional subscription. No additional reducer.
6. **Archive reads work via existing replay.** Opening an old conversation triggers the existing message + run‑event replay path. The reducer is idempotent on `event_id`. By the time the user clicks expand, the activities are already on the tree — same as for live runs.
7. **Auto‑open is unchanged.** PR 3.2's auto‑open trigger (first running subagent or first source) is preserved verbatim. Disclosure default state is **closed**; the user opts in per card.
8. **Accessibility on by default.** Native `<details>` is keyboard accessible (Tab + Enter/Space) and screen‑reader announces "expanded/collapsed". No ARIA glue needed. `aria-controls` / `aria-expanded` are handled by the user agent.

### 1.3 Non‑goals

- **No new backend endpoint.** Specifically: we do **not** add `GET /v1/agent/subagents/{task_id}/timeline` or `GET /v1/agent/runs/{run_id}/events?parent_task_id=…`. The replay path already populates the chat tree; building a second read path duplicates state and creates a divergence risk. If a future feature (e.g. Tasks surface — cross‑conversation aggregation per [`pr-1.5-subagent-discovery-workspace-feeds.md`](./pr-1.5-subagent-discovery-workspace-feeds.md) §1.3) needs subagent step history independent of the chat tree, it owns its own read port.
- **No persistence change.** `runtime_events.parent_task_id` already carries the linkage; we don't add a `subagent_timeline` materialised view.
- **No new event variant.** The existing `tool_call_started/delta/result/completed` and `reasoning_summary*` events with `parent_task_id` set carry every step; we don't introduce `subagent_step` as a peer event type.
- **No "live progress bar inside the disclosure."** PR 1.5's per‑card `progress` field is sufficient; we don't render a per‑step progress bar inside the timeline (would just add motion noise during streaming).
- **No timeline filtering / search.** The full activity list is the v1 surface. If a subagent fires 50+ tool calls, the disclosure scrolls. A filter UI lives in a follow‑up if it shows up in usage data.
- **No "kill this subagent from the timeline."** Cancel is run‑scoped (`POST /v1/agent/runs/{id}/cancel`), already deferred in PR 1.5.
- **No payload‑level redaction tweaks.** `SubagentActivityRecord.summary` / `inputSummary` / `result` are already sourced from server‑projected `summary` / payload fields that go through `FieldCodec` decryption at the persistence boundary and through the `presentation_templates.py` projection (already shipped). This PR consumes; it does not re‑project.
- **No copy‑link‑to‑step deep link.** A future affordance ("copy a permalink that opens the pane on this subagent + scrolls to the third step") is out of scope.

### 1.4 Acceptance criteria

| #     | Criterion                                                                                                                                                                                                                                                                                                          | Verified by                                                   |
| ----- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ------------------------------------------------------------- |
| AC‑1  | Each `<li>` card in the Agents tab wraps its detail body in a single `<details>` element. The summary row stays visible (title + badge + duration); expanding reveals the timeline beneath.                                                                                                                        | RTL test: `expect(screen.getByRole('group'))` toggles `open`. |
| AC‑2  | Default state is **closed** for every card on first render. Opening a card persists across re‑renders within the conversation (kept in component state); switching conversations resets to closed.                                                                                                                 | RTL test.                                                     |
| AC‑3  | When the active conversation has a `run_subagent` tool part with `args.activities` of length _N_, the corresponding pane card's open state shows _N_ `aui-tool-card__timeline-item` rows (the existing `SubagentActivityList` rendering) wrapped in the workspace variant class `atlas-workspace-agent__timeline`. | RTL test on `AgentsTab` rendering with a fixture.             |
| AC‑4  | Live update: a `tool_call_completed` event with `parent_task_id = T` arrives while the card for `task_id = T` is open; the timeline appends one row without remounting the open `<details>` element.                                                                                                               | RTL test driving an event through the reducer.                |
| AC‑5  | Archive read: opening a conversation that completed yesterday and contains 3 subagents with 4–7 inner tool calls each produces three pane cards; expanding each shows its full timeline. The data path is the existing message + run‑event replay (no new fetch).                                                  | Integration test in `apps/frontend/tests/`.                   |
| AC‑6  | Subagent with **zero** activities (very short subagent that never called a tool) shows the disclosure but the body renders the `SubagentActivityList` empty‑text fallback (`"No detailed activity was reported."`). Disclosure is keyboard‑expandable still.                                                       | RTL test.                                                     |
| AC‑7  | Selector `useSubagentActivities()` returns a `ReadonlyMap<TaskId, readonly SubagentActivityRecord[]>` that is referentially stable across renders when the tree did not change (`useMemo` over a structurally stable dep). Verified by `expect(prev).toBe(next)` after no‑op renders.                              | Hook unit test.                                               |
| AC‑8  | The pane‑variant CSS does **not** affect in‑thread `SubagentTool` rendering. The timeline inside `SubagentTool` keeps `aui-tool-card__timeline` only; the pane wraps with an additional class.                                                                                                                     | Visual regression diff (existing snapshot test).              |
| AC‑9  | `prefers-reduced-motion: reduce` honored — no expand animation, instantaneous reveal. (Native `<details>` already does this; we add no JS animation.)                                                                                                                                                              | CSS audit.                                                    |
| AC‑10 | Bundle size delta ≤ 2 KB gzipped (selector + ~80 LoC CSS). No new npm dependency in `apps/frontend/package.json`.                                                                                                                                                                                                  | `npm run build` size diff.                                    |
| AC‑11 | RLS / tenant isolation unchanged. The selector reads only from the chat tree, which is fed by the already‑authenticated event stream and message fetch; no new backend surface.                                                                                                                                    | Security review checklist (no new write or read).             |
| AC‑12 | When `SubagentEntry.status = 'failed'` and the subagent's last activity has `status = 'failed'`, the timeline last‑item badge tone is `danger`. When `cancelled`, the timeline shows the partial activities up to the cancel point with a trailing "Cancelled · Xs" row injected by the existing event projector.  | RTL test.                                                     |

### 1.5 Risks

| Risk                                                                                                                                                                                                                                                             | Mitigation                                                                                                                                                                                                                                                                                                                                                                                                                                       |
| ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Selector walks the entire message list on every render → re‑render storms during streaming.                                                                                                                                                                      | The selector is `useMemo` over the message list reference. The chat tree uses immutable updates (`updateAssistantContent` returns a new array only when content changes), so the dep is structurally stable. We additionally short‑circuit per message: if a message has no `run_subagent` tool part, skip. Profiling target: ≤ 1 ms p95 per recompute on a 200‑message thread (Chrome perf bench in `apps/frontend/perf/`).                     |
| Activities not reconstructed on archive reads (because we rely on event replay, and replay has a bug).                                                                                                                                                           | If real, this is a **pre‑existing bug** in the in‑thread `SubagentTool` view — the same data source. AC‑5 verifies parity with `SubagentTool`. If parity fails, the fix is in the replay path / persistence (same fix benefits both surfaces), not in this PR's selector. We add a regression test that loads a finished conversation fixture and asserts both `SubagentTool` (thread) and the pane card (workspace) show the same activity set. |
| Disclosure open state lost when the user navigates away and back to the same conversation.                                                                                                                                                                       | v1 keeps open state in component‑local state — intentional. Persisting per‑task open state across navigation is a paper cut that PR 3.2's "open Agents tab on a specific subagent" prop already partially addresses (`focusTaskId` opens the pane on a card). Auto‑open of the disclosure for the focused task is added in §2.2 (one line). Anything beyond that is a follow‑up.                                                                 |
| Timeline visual clashes with pane width (`SubagentActivityList` was built for ≥600 px chat columns).                                                                                                                                                             | Add `atlas-workspace-agent__timeline` class with: smaller font‑size (12 px → 11.5 px), tighter row padding, the existing left‑rail dot/connector preserved (already 20 px wide). CSS lives next to the existing `atlas-workspace-agent__*` rules. ~50 LoC.                                                                                                                                                                                       |
| User clicks "↗ jump to thread" while disclosure is open → both expansions visible at once, looks redundant.                                                                                                                                                      | Keep both behaviors. The "↗" button's purpose is _scroll the chat thread_ (so the user sees the same subagent inline alongside surrounding assistant text); the disclosure's purpose is _verify in place_. They serve different intents. Confirmed in §2.5 UX walk‑through.                                                                                                                                                                      |
| Activity list shows stale data because `args.activities` was populated by an old event, then a newer `tool_call_completed` arrives but `upsertSubagentActivity` cannot find the parent (e.g., the parent assistant message turn is no longer the "current" one). | `upsertSubagentActivity` already short‑circuits to `items` (no‑op) when the parent isn't found ([`contentBuilders.ts:306`](../../apps/frontend/src/features/chat/chatModel/contentBuilders.ts#L306)). This means orphaned events vanish silently — the same behavior the in‑thread view has today. v1 inherits that semantic. If we see real‑world orphans we add a fallback bucket in a follow‑up; tracked but not in scope.                    |
| Bundle bloat creep — every "small UI tweak" adds a primitive.                                                                                                                                                                                                    | This PR adds **zero npm deps** (see §4) and reuses three existing components. The CSS additions are scoped via BEM under `atlas-workspace-agent__`. No new global tokens, no new design‑system primitive.                                                                                                                                                                                                                                        |
| Tests are flaky because the disclosure relies on the `<details>` `toggle` event (synthesized differently across jsdom / happy‑dom).                                                                                                                              | We test the rendered DOM structure (presence of `<details>`, presence of timeline rows when `open=true` is set as an attribute), not the toggle event. Where toggle behavior matters, we drive `<summary>.click()` and assert the `[open]` attribute changes. Both jsdom and happy‑dom implement this. Two existing tests in `ActivityCollapsible.test.tsx` cover this pattern — we reuse them.                                                  |

### 1.6 Unit testing requirements

Per [`apps/frontend/CLAUDE.md`](../../apps/frontend/CLAUDE.md) (typecheck + build are mandatory; behavior tests are encouraged).

**New tests** (frontend only — no backend code path changes):

- `apps/frontend/src/features/chat/components/workspace/useSubagentActivities.test.ts` — selector purity. Cases: empty thread; thread with one `run_subagent` and one inner tool call; thread with two `run_subagent`s on different parents; reference equality across no‑op renders; reference change after a new activity is appended.
- `apps/frontend/src/features/chat/components/workspace/AgentsTab.test.tsx` (extend) — disclosure default closed (AC‑2); expanding reveals the timeline (AC‑3); live append while open (AC‑4); empty‑activities fallback (AC‑6); pane‑variant class is applied; `↗ jump to thread` still works alongside the disclosure (AC‑5 supporting).
- `apps/frontend/src/features/chat/integration/AgentsTab.archive.test.tsx` — load a completed‑conversation fixture, assert pane and thread show identical activities (AC‑5).

**No new backend tests** — no backend code path changes.

---

## 2 · Spec

### 2.1 Architecture

```
┌──────────────────────────────────────────────────────────────────────────────┐
│  EXISTING (no change in this PR, drawn for orientation)                      │
│                                                                              │
│  agent_runtime worker                                                        │
│   spawns subagent → emits tool_call_started/completed,                       │
│   reasoning_summary*, … with parent_task_id = <subagent task_id>             │
│                                ▼                                             │
│   runtime_events  (sequence_no, parent_task_id, payload_json_redacted)       │
│                                ▼                                             │
│   SSE: GET /v1/agent/runs/{run_id}/stream?after_sequence=N    (live)         │
│   Replay: GET /v1/agent/runs/{run_id}/events                  (archive)      │
│                                ▼                                             │
│   apps/frontend/.../chatModel/eventReducer.ts                                │
│     ├─ if event.parent_task_id && activity_kind ∈ {tool, reasoning}          │
│     │     ──► upsertSubagentActivity(items, event)                           │
│     │           finds run_subagent tool part by toolCallId === parent_task_id│
│     │           appends event into part.args.activities                      │
│     │                                                                        │
│     └─ in‑thread SubagentTool reads part.args.activities                     │
│                                ▲                                             │
│   ChatScreen                                                                 │
│     hosts thread runtime ── messages tree ── single source of truth          │
│                                                                              │
└──────────────────────────────────────────────────────────────────────────────┘
                                │
                                │  this PR adds the dotted arrow:
                                │
                                ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│  NEW (≈ 180 LoC frontend, this PR)                                           │
│                                                                              │
│   useSubagentActivities()                            ★                       │
│     pure selector: walk messages tree                                        │
│       for each assistant message,                                            │
│         for each run_subagent tool part,                                     │
│           map.set(part.toolCallId, part.args.activities)                     │
│     return ReadonlyMap<TaskId, readonly SubagentActivityRecord[]>            │
│       (memoised over messages reference)                                     │
│                                ▼                                             │
│   ChatScreen passes activitiesByTask  to  WorkspacePane                      │
│                                ▼                                             │
│   AgentsTab                                          ★                       │
│     for each entry in subagents (from PR 1.5)                                │
│       <Card>                                                                 │
│         <header summary row />                       (unchanged)             │
│         <details>                                    ★ new                   │
│           <summary>{durationMeta}</summary>                                  │
│           <SubagentActivityList                      (existing — reused)     │
│             className="atlas-workspace-agent__timeline"                      │
│             activities={activitiesByTask.get(entry.task_id) ?? []} />        │
│         </details>                                                           │
│         <jump‑to‑thread button />                    (unchanged)             │
│       </Card>                                                                │
└──────────────────────────────────────────────────────────────────────────────┘
```

The whole PR is the two ★ boxes plus the prop wiring on `WorkspacePane` → `AgentsTab`. Everything else is reused.

### 2.2 Module boundaries

| Layer                                                                                | Module                                                                                                                                                                                                       | Owns                                                                                                                                                                                                       |
| ------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `apps/frontend/src/features/chat/components/workspace/useSubagentActivities.ts`      | **NEW** ★ — pure selector hook                                                                                                                                                                               | Walks the thread runtime's messages array, finds every `run_subagent` tool part, returns a `Map<task_id, SubagentActivityRecord[]>`. `useMemo` over messages reference. Read‑only; never mutates the tree. |
| `apps/frontend/src/features/chat/components/workspace/AgentsTab.tsx`                 | **EXTEND** ★ — add `activitiesByTask` prop, wrap the body of each card in `<details>` with `SubagentActivityList` inside                                                                                     | UI composition only. No data ownership.                                                                                                                                                                    |
| `apps/frontend/src/features/chat/components/workspace/AgentsTab.css`                 | **EXTEND** — add `atlas-workspace-agent__timeline` BEM block + collapsible chrome rules; ~80 LoC                                                                                                             | Pane‑width‑specific timeline styling. Does not touch `aui-tool-card__timeline` (which the in‑thread renderer keeps).                                                                                       |
| `apps/frontend/src/features/chat/ChatScreen.tsx`                                     | **EXTEND** — host `useSubagentActivities()` and pass result down to `<WorkspacePane subagentActivitiesByTask={…} />`. Two lines.                                                                             | Hook hoisting per PR 3.2 §351 single‑source‑of‑truth principle.                                                                                                                                            |
| `apps/frontend/src/features/chat/components/workspace/WorkspacePane.tsx`             | **EXTEND** — accept `subagentActivitiesByTask` prop and forward to `AgentsTab`. One prop. One forward.                                                                                                       | Pure pass‑through; no state.                                                                                                                                                                               |
| `apps/frontend/src/features/chat/components/tools/SubagentActivityList.tsx`          | **EXTEND** — accept optional `className` prop; default to `"aui-tool-card__timeline"` (existing); pane callsite passes `"atlas-workspace-agent__timeline aui-tool-card__timeline"` (compose, don't replace). | DRY via class composition. Avoids two parallel components.                                                                                                                                                 |
| `packages/api-types/src/index.ts`                                                    | **EXTEND** — re‑export `SubagentActivityRecord` (currently a frontend‑internal type in `activityDataBuilders.ts`) so the api‑types contract has a single name for the pane callsite to import.               | Single source of truth for the FE/BE contract. Note: this is a re‑export of a type already shaped by the projection layer (`presentation_templates.py`); we're not introducing a new wire shape.           |
| `apps/frontend/src/features/chat/components/workspace/AgentsTab.test.tsx`            | **EXTEND** — disclosure tests (AC‑1 / 2 / 3 / 4 / 6 / 12).                                                                                                                                                   |                                                                                                                                                                                                            |
| `apps/frontend/src/features/chat/components/workspace/useSubagentActivities.test.ts` | **NEW** — hook unit tests (AC‑7).                                                                                                                                                                            |                                                                                                                                                                                                            |
| `apps/frontend/src/features/chat/integration/AgentsTab.archive.test.tsx`             | **NEW** — archive replay parity test (AC‑5).                                                                                                                                                                 |                                                                                                                                                                                                            |

**No changes:** `services/ai-backend/*`, `services/backend-facade/*`, `services/backend/*`, `packages/design-system/*`, every migration file, every event schema, every persistence record. The agent harness is untouched. Streaming is untouched. RLS is untouched.

### 2.3 What we do NOT add

- ❌ A `SubagentTimeline` design‑system primitive. The existing `SubagentActivityList` is generic enough; we compose with className. Promoting to design‑system would mean defining a stable contract for an internal representation that is still evolving.
- ❌ A second store / context for activities. `ChatScreen` already hosts the message tree via the assistant‑ui thread runtime; we read from there.
- ❌ A new event variant. `SUBAGENT_STARTED/PROGRESS/COMPLETED` plus the inner `tool_call_*` / `reasoning_*` events are already sufficient — they carry every step the timeline needs via `parent_task_id`.
- ❌ A new endpoint. PR 1.5's two `GET`s already cover the seed; live and archive both flow through the existing event stream.
- ❌ A new persistence column. `runtime_events.parent_task_id` is indexed and already encrypted‑at‑rest where applicable.
- ❌ A new dep. See §4.

### 2.4 TypeScript contracts

#### 2.4.1 Re‑export `SubagentActivityRecord` in api‑types

Currently `SubagentActivityRecord` is internal to the frontend ([`activityDataBuilders.ts`](../../apps/frontend/src/features/chat/utils/activityDataBuilders.ts)). It is constructed from the server‑projected `RuntimeEventEnvelope` fields (`event_id`, `display_title`, `summary`, `status`, `payload.input_summary`, `payload.result_text`) — all of which are already part of the api‑types contract. The shape is:

```ts
// packages/api-types/src/index.ts (extend)
export interface SubagentActivityRecord {
  /** Stable per-event id. Mirrors RuntimeEventEnvelope.event_id. */
  id: string;
  /** RuntimeActivityKind — "tool" | "reasoning" — what kind of step. */
  kind: "tool" | "reasoning";
  /** Server-projected display_title or fallback. */
  title: string;
  /** Server-projected summary; 160-char truncation by the renderer. */
  summary: string | null;
  /** Server-projected payload.input_summary; renderer falls back when summary is empty. */
  inputSummary: string | null;
  /** Server-projected payload.result_text; 160-char truncation by the renderer. */
  result: string | null;
  /** RuntimeStatus — passes through unchanged. */
  status: string;
  /** ISO timestamp from the source event. */
  occurredAt: string;
}
```

The frontend type currently in `activityDataBuilders.ts` is the canonical shape; we **move** it to api‑types and have the FE import from there. No data shape change. Backend doesn't need to know about this name — it never serialises this struct (it serialises `RuntimeEventEnvelope`); but moving the type aligns with the rule "public contracts move first" from [`docs/new-design/0-OVERALL_PLAN.md`](./0-OVERALL_PLAN.md) §Architecture principles.

#### 2.4.2 New hook signature

```ts
// apps/frontend/src/features/chat/components/workspace/useSubagentActivities.ts

import { useMemo } from "react";
import type { SubagentActivityRecord } from "@0x-copilot/api-types";
import { useThreadRuntime } from "@assistant-ui/react"; // already a dep
import {
  isToolCallPart,
  asRecord,
  recordArray,
} from "../../chatModel/contentBuilders";
import { subagentActivityFromRecord } from "../../utils/activityDataBuilders";

export type SubagentActivitiesByTask = ReadonlyMap<
  string,
  readonly SubagentActivityRecord[]
>;

const EMPTY: SubagentActivitiesByTask = new Map();

/**
 * Pure selector over the active conversation's message tree.
 * Returns task_id → activities[] for every `run_subagent` tool part.
 * Memoised; reference-stable across no-op renders.
 */
export function useSubagentActivities(): SubagentActivitiesByTask {
  const messages = useThreadRuntime().getState().messages;
  return useMemo(() => {
    if (!messages?.length) return EMPTY;
    const out = new Map<string, readonly SubagentActivityRecord[]>();
    for (const message of messages) {
      if (message.role !== "assistant") continue;
      for (const part of message.content ?? []) {
        if (!isToolCallPart(part)) continue;
        if (part.toolName !== "run_subagent") continue;
        const args = asRecord(part.args);
        const raw = recordArray(args.activities);
        out.set(part.toolCallId, raw.map(subagentActivityFromRecord));
      }
    }
    return out.size === 0 ? EMPTY : out;
  }, [messages]);
}
```

Three tiny helpers (`isToolCallPart`, `asRecord`, `recordArray`) already exist in `contentBuilders.ts` and are already exported. `subagentActivityFromRecord` is the inverse of the existing `subagentActivityRecord(event)` shape converter — extracted from the rendering site of `SubagentTool` for reuse.

#### 2.4.3 Updated `AgentsTab` props

```ts
// apps/frontend/src/features/chat/components/workspace/AgentsTab.tsx (delta)

export interface AgentsTabProps {
  subagents: SubagentSnapshotMap; // unchanged
  loading?: boolean; // unchanged
  error?: string | null; // unchanged
  focusTaskId?: string | null; // unchanged
  onJumpToSubagent?: (subagent: SubagentEntry) => void; // unchanged

  // NEW — provided by ChatScreen via useSubagentActivities().
  activitiesByTask: SubagentActivitiesByTask;
}
```

Inside the existing `ordered.map((entry) => …)` body, replace the `<Card>` body with:

```tsx
<Card>
  <div className="atlas-workspace-agent">
    <div className="atlas-workspace-agent__header">
      {/* unchanged: badge + name + jump-to-thread button */}
    </div>
    {entry.objective_summary ? (
      <p className="atlas-workspace-agent__objective">
        {entry.objective_summary}
      </p>
    ) : null}
    {entry.result_summary ? (
      <p className="atlas-workspace-agent__result">{entry.result_summary}</p>
    ) : null}

    <details
      className="atlas-workspace-agent__details"
      // Auto-expand the focused card on first render only:
      open={isFocused || undefined}
      data-testid={`workspace-agent-details-${entry.task_id}`}
    >
      <summary className="atlas-workspace-agent__details-summary">
        <span className="atlas-workspace-agent__meta">
          {running ? (
            <span className="atlas-workspace-agent__working">working…</span>
          ) : entry.duration_ms !== null ? (
            <span>Completed in {formatDuration(entry.duration_ms)}</span>
          ) : null}
        </span>
        <span
          className="atlas-workspace-agent__disclosure-hint"
          aria-hidden="true"
        >
          ▾
        </span>
      </summary>
      <SubagentActivityList
        className="atlas-workspace-agent__timeline aui-tool-card__timeline"
        activities={[...(activitiesByTask.get(entry.task_id) ?? [])]}
      />
    </details>
  </div>
</Card>
```

The disclosure `<summary>` carries the duration meta that today lives in a sibling `<div className="atlas-workspace-agent__meta">`. Promoting that row into the summary line keeps the closed card visually identical to today, while making the entire row a click‑target for expansion.

`open={isFocused || undefined}` is a one‑line auto‑open for the focused subagent (PR 3.2 already passes `focusTaskId`). Using `undefined` (not `false`) keeps `<details>` uncontrolled afterwards — the user's manual toggle is respected.

### 2.5 UX walk‑through (cross‑references the design)

Pulled from the prototype (`/tmp/design-fetch/extracted/0x-copilot/project/composer.jsx:233-265`, design's `AgentsPane`):

**Closed (default).** Card shows:

```
┌────────────────────────────────────────────┐
│  ✓  DOC READER                          ↗  │
│  Read positioning + GTM plan,              │
│  extract claims                            │
│  Hero claim: time-to-answer + citation…    │
│  Completed in 18s              ▾           │
└────────────────────────────────────────────┘
```

**Open.** Same card, expanded body shows the timeline:

```
┌────────────────────────────────────────────┐
│  ✓  DOC READER                          ↗  │
│  Read positioning + GTM plan,              │
│  extract claims                            │
│  Hero claim: time-to-answer + citation…    │
│  Completed in 18s              ▴           │
│  ┌─────────────────────────────────────┐   │
│  │ ●  Searched Notion for "aurora"     │   │
│  │      → 4 hits                  done │   │
│  │ │                                   │   │
│  │ ●  Read GTM/FY26-Q1 plan            │   │
│  │      → 3 candidates            done │   │
│  │ │                                   │   │
│  │ ●  Reasoning: extract hero claim    │   │
│  │      Time-to-answer + citation…done │   │
│  └─────────────────────────────────────┘   │
└────────────────────────────────────────────┘
```

The dot/connector left rail is the existing `aui-tool-card__timeline` styling. The `atlas-workspace-agent__timeline` class adds: tighter row padding (8 px → 6 px), smaller font (12 px → 11.5 px), narrower left rail (20 px → 16 px). Everything else is reused.

**Interaction with "↗ jump to thread."** The "↗" remains in the header row; it scrolls the chat thread to the same subagent inline. Two distinct intents: _verify in place_ (disclosure) vs _read in conversational context_ (jump). Both kept.

### 2.6 CSS additions

Single block, scoped under `atlas-workspace-agent__`:

```css
.atlas-workspace-agent__details {
  margin-top: 6px;
}

.atlas-workspace-agent__details > summary {
  display: flex;
  align-items: center;
  justify-content: space-between;
  list-style: none;
  cursor: pointer;
  padding: 4px 0;
  font-size: 11.5px;
  color: var(--text-dim);
  user-select: none;
}
.atlas-workspace-agent__details > summary::-webkit-details-marker {
  display: none;
}

.atlas-workspace-agent__disclosure-hint {
  transition: transform 120ms ease;
}
.atlas-workspace-agent__details[open]
  > summary
  .atlas-workspace-agent__disclosure-hint {
  transform: rotate(180deg);
}

@media (prefers-reduced-motion: reduce) {
  .atlas-workspace-agent__disclosure-hint {
    transition: none;
  }
}

.atlas-workspace-agent__timeline {
  /* compose with aui-tool-card__timeline */
  margin-top: 6px;
  padding-left: 16px; /* narrower rail than thread (20px) */
  font-size: 11.5px; /* down from 12px in thread */
}
.atlas-workspace-agent__timeline .aui-tool-card__timeline-item {
  padding: 6px 0; /* down from 8px */
}
```

That's the entire CSS budget. No new tokens; all values either keep tokens or reduce existing pixel values for the narrower column. The pane styling sits next to the existing `atlas-workspace-agent__*` rules — same file, same neighborhood.

### 2.7 Streaming + persistence walk‑through

Per the user's reminder ("remember how streaming works, we have an agent harness, streaming events, frontend, a database schema. How the entire system will come along.") — here is the end‑to‑end story for one inner tool call inside a running subagent:

1. **Worker** (`runtime_worker/stream_subagents.py`) — the deepagents subagent fires `tools.search_corpus(args)`. The tool wrapper writes a row to `runtime_tool_invocations` (`task_id = <subagent task_id>`, `status='started'`, `args_json_redacted`) and emits a `tool_call_started` event into `runtime_events` (`run_id = <subagent run_id>`, `parent_task_id = <subagent task_id>`, `parent_event_id`, `payload` with input summary, `activity_kind='tool'`, projector populates `display_title` + `summary` via `presentation_templates.py`). The event row is also pushed to the SSE queue.

2. **API** (`runtime_api/http/runs.py:stream`) — the open SSE `GET /v1/agent/runs/{run_id}/stream?after_sequence=N` flushes the new event to the connected client. The `sequence_no` is monotonic per run; reconnection with the highest seen sequence resumes without re‑replay.

3. **Facade** (`backend-facade/agent_proxy.py`) — pass‑through with identity headers preserved. No content forwarding edits.

4. **Frontend network layer** (`apps/frontend/src/api/runtimeStream.ts`) — yields the typed `RuntimeEventEnvelope`. Same pipeline that already drives `SubagentTool` in‑thread.

5. **Reducer** (`apps/frontend/src/features/chat/chatModel/eventReducer.ts:103`) — sees `event.parent_task_id !== null && event.activity_kind === 'tool'` and calls `upsertSubagentActivity(items, event)`. The function ([`contentBuilders.ts:274`](../../apps/frontend/src/features/chat/chatModel/contentBuilders.ts#L274)) walks the assistant message content, finds the `run_subagent` tool part with `toolCallId === parent_task_id`, and appends the event into `args.activities` (idempotent on `event.event_id` via `upsertActivityRecord`).

6. **Tree update** — the assistant message tree is replaced with a new array (immutable update). React's reconciler re‑renders consumers. Two consumers exist now:

   a. `SubagentTool.tsx` (in‑thread, existing) — re‑reads `args.activities` via `subagentActivityRecords()`, re‑renders `SubagentActivityList`.

   b. `useSubagentActivities()` (new, this PR) — `useMemo` dep changes (the `messages` array reference is new), recomputes `Map<task_id, activities>`, returns. `AgentsTab` re‑renders the affected card. If the card's `<details open>`, the new row appears at the bottom of the timeline; if closed, no visible change (the disclosure simply has more rows when the user opens it).

7. **Persistence** — on `tool_call_completed`, the `runtime_tool_invocations` row is updated (`completed_at`, `status='completed'`, `result_summary_json_redacted`). The matching event is appended to `runtime_events`. Both rows have `org_id` enforced by RLS (migration 0008).

8. **Reconnect / replay** — if the client drops the SSE and reconnects, the URL `?after_sequence=N` makes the server replay only events since `N`. Reducer is idempotent on `event_id`, so duplicate replay (after_sequence ≤ already‑seen) is safe.

9. **Archive** — when the user opens this conversation tomorrow, `ChatScreen` fetches the message list (`GET /v1/agent/conversations/{cid}/messages`) and drives the existing run‑event replay (`GET /v1/agent/runs/{run_id}/events`) so the `args.activities` are reconstructed exactly as they were live. The pane card's disclosure, when expanded, shows the same timeline.

10. **Tenant isolation** — every read in steps 2 / 5 / 8 / 9 goes through the existing `org_id` GUC and RLS policies. This PR introduces no new read; same posture.

### 2.8 Failure modes

| Failure                                                                   | Rendered behavior                                                                                                                                                                                                                                          |
| ------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Subagent never produced any inner activity (very short result).           | Disclosure expands to `SubagentActivityList`'s empty‑text fallback ("No detailed activity was reported.").                                                                                                                                                 |
| Activities arrive out of order due to network jitter.                     | `upsertActivityRecord` keeps insertion order (no sort). Out‑of‑order events appear in arrival order. v1 accepts this — it matches in‑thread behavior. Future: add a `sequence_no` sort key if users complain. Tracked.                                     |
| Subagent fails (`status='failed'`).                                       | `SubagentEntry.status='failed'` → header badge tone `danger`. Last activity from the timeline carries its own `status`; rendered with default badge color from `aui-tool-card__timeline`. Pane shows what happened (the partial steps) and that it failed. |
| Subagent cancelled.                                                       | Header badge tone `warning`. Timeline shows partial activities up to the cancellation. Existing event projector emits a "Cancelled · Xs" item — already handled by `SubagentActivityList`.                                                                 |
| Pane closed (workspace pane not open).                                    | Selector still runs (cheap), but the `AgentsTab` is unmounted; no work is performed inside it. When the user opens the pane, `AgentsTab` mounts with the latest map.                                                                                       |
| 0 subagents in conversation.                                              | Existing empty‑tab message ("Subagents run here when Atlas dispatches parallel work."). Disclosure code never executes.                                                                                                                                    |
| Subagent fleet card in‑thread is collapsed; user wants to verify quickly. | Pane disclosure works regardless of fleet card state. Pane is the verification surface; it is not coupled to thread‑side fleet expand state.                                                                                                               |

---

## 3 · Library evaluation

The user's instruction was explicit: _"If a pkg or prebuilt middleware exists check internet and use it."_ Here are the candidates and the verdict:

| Library                                 | What it gives                                          | Why we don't add it                                                                                                                                                                                                                                                                                                                                                                                                               |
| --------------------------------------- | ------------------------------------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `@radix-ui/react-collapsible`           | Headless animated collapsible with `data-state` hooks. | We already use the **native `<details>` / `<summary>` element** via `ActivityCollapsible.tsx`. Native is keyboard‑accessible, screen‑reader announces "expanded/collapsed" without any ARIA glue, and the user agent owns the open state. Radix Collapsible adds ~3 KB gz + a runtime dep for behavior the platform already provides. The codebase has only `@radix-ui/react-popover`; we don't import the Collapsible side‑pack. |
| `@headlessui/react` `Disclosure`        | Accessible disclosure with render‑prop API.            | Headless UI is **not in `package.json`** today. Adding it for one widget is a net loss. Same accessibility story as native `<details>`.                                                                                                                                                                                                                                                                                           |
| `react-aria` / `useDisclosure`          | Hook + `aria-expanded` / `aria-controls` glue.         | Already implemented by the user agent for `<details>`. Adding ~10 KB of `@react-aria/*` for an aria‑attribute polyfill we don't need is anti‑DRY.                                                                                                                                                                                                                                                                                 |
| `framer-motion` `AnimatePresence`       | Animated reveal.                                       | Out‑of‑scope. CSS `transition` on the chevron rotation is enough; the body reveal is instantaneous on purpose (matches design's tightness; respects `prefers-reduced-motion`). Adding framer‑motion for one chevron is overkill.                                                                                                                                                                                                  |
| `react-vertical-timeline-component`     | A pre‑styled vertical timeline.                        | Heavy (~30 KB gz), opinionated visuals (cards on alternating sides, intended for landing pages), and we already have `aui-tool-card__timeline` perfectly tuned for our content.                                                                                                                                                                                                                                                   |
| `@tanstack/react-query` (already a dep) | Cache + revalidate semantics.                          | Not relevant — there is no fetch in this PR. Activities are derived state from the chat tree.                                                                                                                                                                                                                                                                                                                                     |

**Decision:** zero new deps. `<details>` + existing `SubagentActivityList` + a 80‑line CSS variant is the elegant primitive for this job.

---

## 4 · File change summary

```
apps/frontend/src/features/chat/components/workspace/
  AgentsTab.tsx                                  ~+50 LoC   wrap card body in <details>; render SubagentActivityList inside
  AgentsTab.test.tsx                             ~+90 LoC   AC-1, 2, 3, 4, 6, 12
  AgentsTab.css                                  ~+80 LoC   atlas-workspace-agent__timeline + chrome
  WorkspacePane.tsx                              ~+3  LoC   add prop, forward to AgentsTab
  useSubagentActivities.ts                       ~+45 LoC   NEW selector hook
  useSubagentActivities.test.ts                  ~+70 LoC   AC-7

apps/frontend/src/features/chat/
  ChatScreen.tsx                                 ~+2  LoC   call useSubagentActivities(); pass to WorkspacePane
  utils/activityDataBuilders.ts                  ~+20 LoC   export subagentActivityFromRecord (inverse of existing record builder)
  components/tools/SubagentActivityList.tsx      ~+3  LoC   accept optional className prop (default unchanged)

apps/frontend/src/features/chat/integration/
  AgentsTab.archive.test.tsx                     ~+60 LoC   NEW archive replay parity test (AC-5)

packages/api-types/src/
  index.ts                                       ~+15 LoC   export SubagentActivityRecord interface

# nothing else changes
services/ai-backend/                             0 changes
services/backend-facade/                         0 changes
services/backend/                                0 changes
packages/design-system/                          0 changes
migrations/                                      0 changes
package.json                                     0 deps added
```

---

## 5 · Verification checklist

Before merging:

- [ ] `npm run typecheck --workspace @0x-copilot/frontend` clean.
- [ ] `npm run typecheck --workspace @0x-copilot/api-types` clean.
- [ ] `npm run build --workspace @0x-copilot/frontend` clean; bundle delta ≤ 2 KB gz.
- [ ] `npm run test --workspace @0x-copilot/frontend` clean; 4 new tests; existing tests untouched and green.
- [ ] `make test` clean (cross‑service smoke unaffected).
- [ ] Manual walk‑through `make dev`:
  - Run the launch‑announcement scenario; while subagents stream, open Agents tab; expand "Doc reader"; observe `search_notion` row appear without remounting the disclosure.
  - Cancel mid‑run; observe failed subagent shows partial timeline + "Cancelled · Xs".
  - Reload the page after run completes; reopen the same conversation; expand the same card; observe identical timeline (archive parity, AC‑5).
  - Resize viewport from 1400 px to 920 px; pane stays pinned; timeline still legible (CSS variant verified).
- [ ] No new entry in `package.json` `dependencies`.
- [ ] No `// TODO` or `// HACK` introduced.
- [ ] `prefers-reduced-motion: reduce` honored (chevron does not rotate; reveal still instantaneous).

---

## 6 · Out of scope (follow‑ups)

Tracked, but not in this PR:

- **Persist disclosure open state across navigation.** v1 keeps it component‑local. If the same user repeatedly returns to the same conversation, persisting per‑task open state in the conversation's UI state slice is a quality‑of‑life follow‑up.
- **Per‑step deep links.** Copy‑link‑to‑subagent‑step → opens pane on the right card with the right disclosure open and the right step highlighted. Needs URL grammar; tracked but out of scope.
- **Per‑subagent token / cost panel.** PR 1.5 already returns `token_usage` per subagent (`SubagentTokenUsage`). v1 of this PR doesn't render it inside the disclosure (the result + meta row is enough for verification). A follow‑up adds a "1,247 input · 423 output · $0.012" footer line inside the disclosure body.
- **Cancel from the timeline.** Per PR 1.5 §1.3 non‑goal. If/when needed, add a cancel button in the disclosure footer that calls `POST /v1/agent/runs/{subagent_run_id}/cancel`.
- **Cross‑conversation subagent surface (Tasks tab).** Different surface, different fetch semantics, different UX. Owns its own PR.
- **Materialised view for subagent step history.** Only worth it if read latency on archive shows up as a hot path. Not a v1 concern.

---

## References

- [`docs/new-design/0-OVERALL_PLAN.md`](./0-OVERALL_PLAN.md) — wave plan, architecture principles.
- [`docs/new-design/pr-1.5-subagent-discovery-workspace-feeds.md`](./pr-1.5-subagent-discovery-workspace-feeds.md) — feed contract; explicit non‑goal we pick up.
- [`docs/new-design/pr-3.2-workspace-pane-right-rail.md`](./pr-3.2-workspace-pane-right-rail.md) — pane host pattern, single‑source‑of‑truth principle.
- [`apps/frontend/src/features/chat/chatModel/eventReducer.ts`](../../apps/frontend/src/features/chat/chatModel/eventReducer.ts) — the existing `parent_task_id` routing.
- [`apps/frontend/src/features/chat/chatModel/contentBuilders.ts`](../../apps/frontend/src/features/chat/chatModel/contentBuilders.ts) — `upsertSubagentActivity` (lines 274–307).
- [`apps/frontend/src/features/chat/components/tools/SubagentActivityList.tsx`](../../apps/frontend/src/features/chat/components/tools/SubagentActivityList.tsx) — the timeline renderer we reuse verbatim.
- [`apps/frontend/src/features/chat/components/activity/ActivityCollapsible.tsx`](../../apps/frontend/src/features/chat/components/activity/ActivityCollapsible.tsx) — the existing `<details>` wrapper pattern.
- [`apps/frontend/src/features/chat/components/workspace/AgentsTab.tsx`](../../apps/frontend/src/features/chat/components/workspace/AgentsTab.tsx) — the file we extend.
- [`apps/frontend/src/features/chat/components/workspace/useSubagents.ts`](../../apps/frontend/src/features/chat/components/workspace/useSubagents.ts) — the seed‑+‑live entry hook from PR 1.5.
- [`services/ai-backend/migrations/0001_initial_runtime_persistence.sql`](../../services/ai-backend/migrations/0001_initial_runtime_persistence.sql) — `runtime_async_tasks`, `runtime_subagent_results`, `runtime_tool_invocations`, `runtime_events` schemas.
- Design prototype subagent reference: `0x-copilot/project/composer.jsx` (`AgentsPane`, ~lines 233–266) and `0x-copilot/project/messages.jsx` (`SubagentFleet`, ~lines 207–243) from the Anthropic Design handoff bundle.
