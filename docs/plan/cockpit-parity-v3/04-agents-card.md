# Agents rich card — v3 parity plan

## Problem statement

**What a user sees today.** Open the Run cockpit → Studio → **Agents** tab while a
delegated run is in flight. Each subagent renders as a thin `SubagentCard`
(`packages/chat-surface/src/subagents/SubagentCard.tsx`): a small status icon, an
uppercase-ish name, a status `Badge`, a two-line task clamp, and a collapsed
`<details>` disclosure whose **running summary is the literal string "working…"**
(`SubagentCard.tsx:223`, `metaText`). There is no motion, no sub-line of run facts,
no headline of what the agent is doing _right now_, no token cost, and no controls.
A user watching a 10-minute research fan-out cannot tell, at a glance, which agent is
alive, how much work it has done, or what it is doing this second — the card looks
identical at second 2 and minute 8.

**The v3 intent** (`design/copilot-run-side.jsx` `AgentsPanel` + `copilot-v3.css`
`.ag`). Each agent is a _rich, legible status card_: a colored **status dot**
(`.dotk` jade running / amber waiting), a display-font name (`.nm` 12.5px/600), an
optional scope chip, a **mono sub-line** of live facts (`.sub` 9.5px —
"Claude Sonnet 4.5 · 4 tools · step 6 of 9 · 18m"), a **progress bar**
(`.bar > .bar__f`), a **current-action headline** (`.cur` 11.5px — "⚡ Drafting the
launch thread on X — streaming 64%"), and a **footer** of small controls
(`.ft > .cbtn--sm`: Open / Pause / Review swap / Stop run). `ag--warn` paints the
amber border for the waiting-on-you state.

**Why it matters now.** The chat now streams live in both modes and Sources is fed
off the stream; the Agents tab is the last cockpit surface still showing a
placeholder. It is _also_ the surface most likely to mislead: the v3 mockup draws
"step 6 of 9", "64%", and a model name that **our runtime cannot honestly produce**
(subagents are an open Deep-Agent tool loop — no planned total N, no percent; the
resolved model is not on the run or subagent contract). This plan rebuilds the card
to the v3 _form_ using only data the backend actually emits — an honest rich card —
and names the single genuine cross-stack contract add (resolved model) as an optional
field so the card degrades when it is absent. No band-aids, no faked fractions.

## Functional requirements

- **FR-1.** The card MUST render a leading **status dot** whose color derives from the
  normalized lifecycle status: jade (`--jade` / running-tone token) for
  `queued`/`running`, amber (`--amber` / warning token) for `paused`, and the
  terminal tones for `completed`/`cancelled`/`failed`/`timed_out`. The dot MUST carry
  an `aria-hidden` and the status MUST also be conveyed textually (badge or
  `aria-label`) so color is not the only signal.
- **FR-2.** The name MUST render in the v3 display type ramp (12.5px/600 via tokens),
  from `view.name` (already `display_title ?? formatAgentName(subagent_name)`).
- **FR-3.** The card MUST render a **mono sub-line** built ONLY from real data:
  `"{N} tools · {M} steps · {elapsed}"`, where `N` = count of activities with
  `kind === "tool"` for this `task_id`, `M` = total activity/progress count, and
  `elapsed` = live `started_at → now` while running, else `duration_ms` formatted.
  Any segment whose datum is missing MUST be omitted (no `"0 tools"` filler when the
  activity feed is absent) — the sub-line degrades to the segments it can back.
- **FR-4.** While `status === "running"` the card MUST render an **indeterminate,
  animated** progress bar (motion via a token-driven CSS animation), NOT a width
  bound to a fabricated percent. On any terminal status the bar MUST be removed (not
  frozen at a fake fraction). `paused` MUST render the bar in a stalled/amber state,
  not animating.
- **FR-5.** The card MUST promote the **current-action string** to a visible headline
  (v3 `.cur`, 11.5px, leading accent icon) sourced from
  `view.currentAction` — the latest `subagent_progress` `short_summary`, which
  `projectSubagents.onProgress` already merges into `display_title`
  (`subagentProjection.ts:325-341`). It MUST show only while non-terminal and MUST
  fall back to nothing (not "working…") when no progress frame has arrived.
- **FR-6.** The disclosure MUST remain: the full per-step timeline
  (`SubagentActivityList`) stays inside `<details>`; the headline is a _summary_, the
  timeline is the _detail_. The current single-mount / single-projection wiring MUST
  be preserved.
- **FR-7.** The card MUST surface **token usage** when `view.tokenUsage` is present:
  a compact `"{total_tokens} tok"` (or in/out split) rendered in the mono ramp. It
  MUST render nothing when `token_usage === null`.
- **FR-8.** The card footer MUST keep the two **backed** affordances: jump-to-thread
  (`onJumpToThread`, existing) and, when `status === "paused"` with a
  `pauseSourceEventId`, **Review approval →** (`onJumpToApproval`, existing). No
  per-subagent Pause / Stop / Run-now control may be rendered unless wired to a real
  endpoint (see FR-9 / Descopes).
- **FR-9.** If a run-level control is wired, a **Stop run** footer button MAY appear
  and MUST call the run-level cancel path (`onCancelRun`, host-owned) — never a
  non-existent per-subagent cancel. Absent that callback, no such button renders.
- **FR-10.** The Agents-tab section headers MUST describe the **this-conversation**
  reality (e.g. "Running · N", "Waiting on you · N") derived from the injected
  snapshot map — NOT the v3 mockup's cross-run "Scheduled" / background-fleet
  sections, which have no feed.
- **FR-11.** The rich card MUST be the SAME component at BOTH callsites (in-thread
  `SubagentCard` via `subagentCardFromArgs` and the Agents-tab pane via
  `subagentCardFromEntry`) — one component, two builders, unchanged (DRY per the
  file's existing contract).
- **FR-12.** `subagentCardViewModel` MUST expose the new fields
  (`currentAction`, `toolCount`, `stepCount`, `tokenUsage`, `liveElapsedFrom`) as a
  pure function of its inputs; the component MUST stay presentational (no timers that
  fetch, no globals — see NFR-3).
- **FR-13.** _(Optional, gated on the FR-16 contract add.)_ When
  `view.model` is present the sub-line MUST prepend the model name
  (`"{model} · {N} tools · …"`); when absent the sub-line MUST omit it with no gap.
- **FR-14.** The tool/step counts MUST be produced by a **pure selector over the
  single `session.events` array** (`projectSubagentActivities`), threaded through the
  existing `subagentActivitiesByTask` prop that `RunWorkspaceRail` → `AgentsTab`
  already accept — never a second SSE subscription or a second projector.
- **FR-15.** Live elapsed MUST tick without a re-fetch: a component-local
  `setInterval` (1s) that only runs while `status === "running"` and is cleared on
  unmount / terminal transition. It MUST NOT touch any port or global beyond the
  timer.
- **FR-16.** _(NEW-CONTRACT, separable.)_ The server MAY project the **resolved model
  id** onto `SubagentEntry` as an OPTIONAL field `model: string | null`. When added,
  the emit path, the archive read, the api-types mirror, and the FE view model MUST
  all carry it, and every consumer MUST treat absence as "unknown" (FR-13).

## Non-functional requirements

- **NFR-1 — one event projection (FR-3.3).** The tool/step counts come from
  `projectSubagentActivities(session.events)`, a NEW pure selector living beside
  `projectSubagents` in `packages/chat-surface/src/subagents/`. It MUST consume the
  SAME `session.events` array `ThreadCanvas` feeds `useEventProjector` — no
  `Transport.subscribe`, no second `useEventProjector`. `RunDestination` memoizes it
  against `session.events` exactly as it does `projectSubagents`
  (`RunDestination.tsx:419-422`).
- **NFR-2 — single mount (FR-3.9).** This change touches only the _contents_ of
  `SubagentCard` + its view model + the Agents-tab body. It MUST NOT move `TcChat`,
  add a mount, or change `RunWorkspaceRail`'s grid/panel structure. The `<details>`
  open-state stays component-local.
- **NFR-3 — substrate boundary.** All new code lives in `@0x-copilot/chat-surface`
  and MUST NOT touch `window`/`document`/`fetch`/`localStorage`/`EventSource`
  (eslint `no-restricted-globals`). `setInterval`/`clearInterval` are permitted
  (they are `globalThis` timers, not banned substrate) but MUST be component-local
  and cleaned up. The live-elapsed clock is view-only; it needs no port.
- **NFR-4 — host-fed / presentational.** The card + view model take normalized props.
  The activities selector output is threaded by the host binder
  (`RunDestination` → `RunWorkspaceRail.subagentActivitiesByTask`). No fetch/POST in
  the component. The FR-9 `onCancelRun` callback, if wired, is host-owned.
- **NFR-5 — honest data.** No sub-line segment, bar, or headline may render a value
  the backend does not produce. The bar is _indeterminate motion_, never a percent.
  "step X of Y", bounded "%", and model name (pre-FR-16) are DESCOPED, not faked.
- **NFR-6 — design tokens, both themes.** Dot colors, bar track/fill, mono ramp
  (9.5px sub-line), display ramp (12.5px name), accent icon, amber warn border MUST
  use design-system tokens and be verified in light AND dark. The animated bar MUST
  respect `prefers-reduced-motion` (static striped/pulse fallback).
- **NFR-7 — a11y.** The animated bar MUST be `role="progressbar"` with
  `aria-valuetext="in progress"` and NO `aria-valuenow` (indeterminate). The
  live-elapsed region MUST NOT be an `aria-live` announcer (it would spam SR every
  second) — the surrounding list already has `aria-live="polite"`
  (`AgentsTab.tsx:112`); elapsed carries `aria-hidden` on its ticking text with the
  status conveyed once via the badge. Footer buttons MUST have discernible names.
- **NFR-8 — perf.** The activities selector is O(events) and memoized; the 1s timer
  exists only for `running` cards. A fleet of N running subagents = N timers — cap by
  running only while the tab/card is mounted (Studio Agents tab is conditionally
  rendered, `RunWorkspaceRail.tsx:251-267`, so timers stop when the tab is left).
- **NFR-9 — tests required.** Every FR above ships with a unit test (view model +
  component render), and the selector ships with a projection test asserting
  idempotent replay and correct tool/step counts. See Test plan.

## Architecture & plan

### Components / hooks introduced

1. **`projectSubagentActivities(events)`** — NEW pure selector,
   `packages/chat-surface/src/subagents/subagentActivities.ts`. Returns
   `SubagentActivitiesByTask` (`workspace/types.ts:92-95`,
   `ReadonlyMap<string, readonly SubagentActivityRecord[]>`). It reduces
   `session.events` where `source === "subagent"` and `task_id` is set, building one
   `SubagentActivityRecord` (`subagentHelpers.ts:186-195`) per
   `subagent_progress`/tool frame with
   `kind = event.activity_kind ?? "system"`, `title = event.display_title ?? …`,
   `summary = event.summary ?? null`. Idempotent by `event_id` (mirror the dedupe in
   `projectSubagents`, `subagentProjection.ts:113-123`). This is the SAME dedupe /
   same-array discipline as `projectSubagents` — NFR-1.
   _Rationale:_ the cockpit today does **not** feed `subagentActivitiesByTask` at all
   (`RunDestination.tsx:535-546` omits it), so the Agents-tab timeline is already
   empty and the tool count has no source. This selector is the honest source for
   both the count (FR-3) and the disclosure timeline (FR-6) in one pass.

2. **`subagentCardViewModel` extension** — `subagents/subagentCardViewModel.ts`.
   Add to `SubagentCardViewModel` (interface at lines 35-68):
   - `currentAction: string | null` — the live headline. In `subagentCardFromEntry`
     (210-245) source it from `entry.display_title` **only while non-terminal**
     (the progress frames merge the running `short_summary` into `display_title`,
     `subagentProjection.ts:336`); when terminal, `null`. In `subagentCardFromArgs`
     (129-205) source from `shortSummary` while non-terminal.
   - `tokenUsage: SubagentTokenUsage | null` — pass through `entry.token_usage`
     (already on the entry, `api-types` 2292; the view model just never surfaced it).
   - `liveElapsedFrom: string | null` — `started_at` when `!terminal`, else `null`
     (drives the FR-15 clock).
   - `model: string | null` — pass through `entry.model` (present only after FR-16;
     `?? null` so pre-contract builds compile).
     _Note:_ `toolCount`/`stepCount` are NOT on the view model — they come from the
     activities list handed to the component (the view model has no activities), so the
     component derives them from its `activities` prop. This keeps the view model a pure
     function of a single `SubagentEntry`/`args` and avoids duplicating the count.

3. **`SubagentCard` rebuild** — `subagents/SubagentCard.tsx`. Restructure the header
   - body to the `.ag` layout:
   * **Head** (replace 78-104): `dot` span (FR-1) + `name` span (FR-2) + optional
     scope chip + keep `onJumpToThread` button. Keep the `Badge` for the accessible
     status text but restyle to the v3 chip.
   * **Sub-line** (NEW, below head): compute `toolCount = activities.filter(a =>
a.kind === "tool").length` and `stepCount = activities.length`; join the backed
     segments `[view.model, `${toolCount} tools`, `${stepCount} steps`, elapsed]`
     dropping empties (FR-3, FR-13). Elapsed uses `useLiveElapsed(view.liveElapsedFrom
?? null, view.durationMs)` (the FR-15 hook, local to the file).
   * **Bar** (NEW): render the indeterminate `role="progressbar"` bar only while
     `status ∈ {running, queued}`; amber-stalled while `paused`; absent when terminal
     (FR-4, NFR-7).
   * **Current-action headline** (NEW): when `view.currentAction` and non-terminal,
     render `.cur` with the accent icon (`Icon.bolt` equivalent from the icons SSOT).
   * Keep the finding line + `Review approval →` link + `<details>` timeline
     (105-147) unchanged in behavior; the disclosure summary drops the "working…"
     string (delete the running branch of `metaText`, 214-224) — the headline now
     carries that role.
   * **Footer** (NEW, optional): render Stop-run only if an `onCancelRun` prop is
     supplied (FR-9); otherwise footer holds only backed affordances.
   * Add `useLiveElapsed` as a small local hook (FR-15): `useState` seeded from the
     formatted duration, `useEffect` starting a 1s `setInterval` only when a live
     start ts is present, cleared on unmount / when it becomes null.

4. **`AgentsTab` / `RunWorkspaceRail` — wiring only.** `AgentsTab.renderEntry`
   (`AgentsTab.tsx:206-248`) already passes `activities={activitiesByTask?.get(
entry.task_id) ?? []}` into `SubagentCard` (223, 237) — no change needed there.
   `RunWorkspaceRail` already accepts + forwards `subagentActivitiesByTask`
   (`RunWorkspaceRail.tsx:114, 147, 263`). The ONLY host edit is to compute + pass it.

### Data flow

`session.events` (single source)
→ `projectSubagents(events)` → `subagentProjection.subagents` _(existing;
RunDestination.tsx:419-422)_ → `RunWorkspaceRail.subagents` → `AgentsTab` →
`subagentCardFromEntry` → view model (name/status/currentAction/tokenUsage/
elapsed) → `SubagentCard`.
→ `projectSubagentActivities(events)` _(NEW, memoized identically)_ →
`RunWorkspaceRail.subagentActivitiesByTask` → `AgentsTab.activitiesByTask` →
`SubagentCard.activities` → tool/step counts (FR-3) + disclosure timeline (FR-6).

Both selectors read the ONE array; neither subscribes. NFR-1 holds by construction.

### Exact edit points (verified file:line)

- `packages/chat-surface/src/subagents/subagentActivities.ts` — **NEW file**
  (selector #1). Export `projectSubagentActivities`.
- `packages/chat-surface/src/subagents/index.ts` — add the export (barrel used by
  `RunDestination.tsx:66` which already imports `{ projectSubagents }` from
  `"../../subagents"`).
- `packages/chat-surface/src/subagents/subagentCardViewModel.ts` — extend interface
  **35-68**; add `currentAction`/`tokenUsage`/`liveElapsedFrom`/`model` in
  `subagentCardFromArgs` return **169-204** and `subagentCardFromEntry` return
  **216-244**.
- `packages/chat-surface/src/subagents/SubagentCard.tsx` — rebuild head **78-104**,
  insert sub-line + bar + headline before `<details>` **118**, delete the running
  branch of `metaText` **214-224** (headline replaces "working…"); add local
  `useLiveElapsed`.
- `packages/chat-surface/src/destinations/run/RunDestination.tsx` — add
  `const subagentActivities = useMemo(() => projectSubagentActivities(session.events),
[session.events]);` beside **419-422**, and pass
  `subagentActivitiesByTask={subagentActivities}` into `<RunWorkspaceRail>` **535-546**
  (currently omitted). Import from `"../../subagents"` at **66**.
- CSS: add `.subagent-card` sub-line/bar/headline/footer rules + the indeterminate
  keyframes to the subagent card stylesheet (wherever `.subagent-card__*` is defined;
  co-locate with existing card styles), tokens only, both themes, reduced-motion
  fallback (NFR-6).

### Contract change (FR-16, separable — ship as its own commit / project)

Named files, in order — do NOT couple to the FE-only commits above; the FE degrades
without it (`model` optional):

1. `services/ai-backend/src/runtime_api/schemas/workspace.py` — add
   `model: str | None = Field(default=None, max_length=128)` to `SubagentEntry`
   (**35-50**).
2. `services/ai-backend/src/runtime_worker/stream_subagents.py` — include the resolved
   model in the `SUBAGENT_STARTED` payload built near **281** (and/or the completed
   payload near **297**); the resolved model must be threaded from the run/subagent
   launch config into `_ChildEventBuilder` (source: `CreateRunRequest.model` /
   workspace default resolved at run start — NOT the composer input directly).
3. `services/ai-backend/src/agent_runtime/api/workspace_feed_service.py` — set
   `model=snapshot.model` in `_to_entry` (**112-137**); requires adding `model` to the
   `SubagentSnapshot` record + its persistence projection (archive read path).
4. `packages/api-types/src/index.ts` — add optional `model?: string | null` to
   `SubagentEntry` (**2279-2300**) — non-breaking additive (api-types CLAUDE.md rule).
5. FE view model — already covered by the `model` field in edit #3 above.

### Ordered, independently-shippable commits

1. **C1 — activities selector + wiring.** New `projectSubagentActivities`, barrel
   export, memo in `RunDestination`, pass `subagentActivitiesByTask` to the rail.
   Ships value alone: lights up the (currently empty) Agents-tab disclosure timeline.
2. **C2 — view model extension.** Add `currentAction`/`tokenUsage`/`liveElapsedFrom`
   (+ `model` typed optional, always `null` until FR-16). Pure, unit-tested; no visual
   change yet.
3. **C3 — SubagentCard rich rebuild + CSS.** Dot, display name, mono sub-line (tool/
   step/elapsed), indeterminate bar, current-action headline, token usage, footer;
   delete "working…". This is the visible parity commit; depends on C1 (activities)
   - C2 (view model).
4. **C4 — Agents-tab section relabel** (FR-10): "Running · N" / "Waiting on you · N"
   from the snapshot map; drop any "Scheduled"/cross-run framing.
5. **C5 (separate project) — FR-16 resolved-model contract** across the five files
   above. Independently deployable; FE picks it up automatically (optional field).

## Descopes & rationale

- **Bounded progress % (`.bar__f` width, "streaming 64%").** DESCOPE. Subagents are
  an open Deep-Agent tool loop bounded by a deadline + token budget
  (`agent_runtime/delegation/subagents/runner.py`); the runtime never computes a total
  N, and `subagent_progress` carries no `percent`/`step` field
  (`stream_subagents.py:312-324` builds the payload from
  `StreamMessageParser.safe_activity_payload` — no fraction). Replaced by an
  **indeterminate animated bar** (FR-4) — honest motion, no number.
- **"step X of Y" / planned-step total.** DESCOPE. No planned-step total exists in the
  subagent contract (`api-types` `SubagentEntry` 2279-2300 has no `step`/`total`;
  `workspace.py` 35-50 likewise). We show `{M} steps` (a running _count_ of observed
  activities), never "of Y".
- **Model name in the sub-line (pre-contract).** DESCOPE → **NEW-CONTRACT** (FR-16).
  The model is absent from the run + subagent contract; it exists only as composer
  INPUT (`CreateRunRequest.model`) + workspace default. Rendered only if the optional
  `model` field lands (edit C5); the sub-line omits it otherwise (FR-13). Evidence:
  `workspace.py` `SubagentEntry` (35-50) and `_to_entry`
  (`workspace_feed_service.py:112-137) carry no model; the runner's `model`references are Pydantic`.model_validate`, not a model id.
- **Cross-run / "Scheduled" fleet sections** (`AgentsPanel` "Scheduled · 2",
  "Treasury watch · continuous", "next run in 2 days"). DESCOPE. There is no
  background/scheduled-agent feed behind this tab — the tab is fed by
  `projectSubagents(session.events)`, i.e. THIS conversation's run only
  (`RunDestination.tsx:419-422, 538`). Relabel to the this-conversation reality
  (FR-10). A scheduled-agents surface would be a separate destination with its own
  feed, out of scope.
- **Per-subagent Pause / Stop / Run-now footer controls.** DESCOPE (as per-subagent).
  A subagent cannot be paused/stopped independently of its parent run — there is no
  per-task control endpoint. Keep only backed affordances (jump-to-thread, Review
  approval). A single **Stop run** button MAY appear wired to the run-level cancel
  path (FR-9) if the host supplies `onCancelRun`; "Run now" has no backing here and is
  dropped.
- _(NEW-CONTRACT alternative, noted not adopted.)_ A genuine fraction is possible only
  if the delegation runner emits a **bounded plan total + step index** (planner emits
  N up front, each step increments i). That is a cross-stack change to
  `agent_runtime/delegation/subagents/runner.py` + a new `subagent_progress` payload
  field + api-types + view model — a separate project, explicitly not this plan.

## Test plan

Unit (vitest, `packages/chat-surface`):

- **`subagentActivities.test.ts`** (NEW): (a) counts `kind === "tool"` vs total
  activities per `task_id`; (b) idempotent on replayed `event_id`s (guards a
  double-count regression on SSE reconnect); (c) ignores frames with
  `source !== "subagent"` or no `task_id` (parity with `projectSubagents`,
  `subagentProjection.ts:266-274`). Regression guarded: the tool/step counts never
  double or leak across tasks.
- **`subagentCardViewModel.test.ts`** (extend): `currentAction` is the running
  `display_title`/`short_summary` while non-terminal and `null` when terminal;
  `tokenUsage` passes through and is `null` when absent; `liveElapsedFrom` is
  `started_at` while running, `null` when terminal; `model` is `null` pre-contract.
  Regression: terminal cards never keep a stale "current action" headline.
- **`SubagentCard.test.tsx`** (extend): (a) status dot color/attribute per status;
  (b) sub-line omits missing segments (no `"0 tools"` when activities empty; no model
  gap when `model === null`) — FR-3/FR-13; (c) indeterminate bar present + `role=
"progressbar"` with no `aria-valuenow` while running, ABSENT when terminal
  (FR-4/NFR-7); (d) headline shows `currentAction` while running, hidden when
  terminal, and the disclosure no longer prints "working…"; (e) token usage rendered
  only when present (FR-7); (f) Stop-run button renders only with `onCancelRun`; (g)
  `Review approval →` still renders for paused + `pauseSourceEventId` (FR-8, guards no
  regression of the existing approval jump).
- **`useLiveElapsed`**: advances with fake timers while running; freezes/omits on
  terminal; clears the interval on unmount (guards a timer leak / setState-after-
  unmount regression) — FR-15/NFR-8.

Integration:

- **`AgentsTab.test.tsx`** (extend): rendering with a live `subagents` map +
  `activitiesByTask` produces the rich card (dot + sub-line + bar) for a running
  entry and a static card for a terminal entry; empty `activitiesByTask` still renders
  the card (sub-line degrades) — proves the host-fed contract and the graceful-
  degradation path.
- **`RunDestination` binder test** (or the existing cockpit projection test): assert
  `projectSubagentActivities` is memoized off `session.events` and passed to
  `RunWorkspaceRail.subagentActivitiesByTask`, and that NO second Transport
  subscription is opened (guards FR-3.3 / NFR-1 — the whole point).

Contract (FR-16 commit only):

- ai-backend workspace-feed test: `_to_entry` maps `snapshot.model → entry.model`;
  archive read returns it; emit path includes it in `SUBAGENT_STARTED`.
- api-types typecheck: optional `model` compiles against existing consumers.

## Risks & gotchas

- **R1 — activities were never wired in the cockpit.** The single biggest gotcha:
  `RunDestination.tsx:535-546` does NOT pass `subagentActivitiesByTask`, so today the
  cockpit Agents-tab disclosure is empty and the tool count has no source. C1 is a
  prerequisite, not a nicety — do it first and verify the timeline lights up before
  touching card visuals.
- **R2 — `activity_kind` values.** The tool count depends on `event.activity_kind ===
"tool"`. Confirm the backend's projected `activity_kind` vocabulary actually uses
  `"tool"` for tool activities (root CLAUDE.md: do NOT derive activity kind from
  event-name prefixes). If the value differs, map it in the selector, not the
  component. Add a test fixture that pins the real emitted value.
- **R3 — timer storm.** N running subagents = N 1s intervals. Mitigated because the
  Studio Agents tab is conditionally rendered (`RunWorkspaceRail.tsx:251-267`), so
  timers unmount when the user leaves the tab; still, keep the interval strictly
  gated on `status === "running"` and clear on terminal transition.
- **R4 — reduced motion.** The indeterminate bar is pure decoration; it MUST have a
  static `prefers-reduced-motion` fallback or it becomes an accessibility defect
  (NFR-6/NFR-7). Ship the media query with the keyframes, not later.
- **R5 — headline vs. task-line duplication.** `display_title` currently drives BOTH
  `name` and (via progress) the running `currentAction`. Ensure the headline uses the
  _latest progress short_summary_ semantics and the name stays the stable dispatch
  label, or the card shows the same string twice. The view model already separates
  `name` from `task` (`subagentCardViewModel.ts:176-189, 222-229`); mirror that
  discipline for `currentAction`.
- **R6 — terminal "current action" bleed.** A completed subagent must not keep a
  running headline. Gate `currentAction` on `!terminal` in the view model (FR-5), and
  test it (R5/R6 are the same bug class the projection notes at
  `subagentProjection.ts:180-189`).
- **R7 — FR-16 archive path.** Adding `model` to the emit path is easy; the archive
  read (`workspace_feed_service._to_entry`) needs `model` on the `SubagentSnapshot`
  record + its stored projection, which is a persistence-schema touch. Keep FR-16 in
  its own commit/project so the FE parity work is not blocked on a store migration.
- **R8 — dual callsite parity.** The in-thread `SubagentCard` (via
  `subagentCardFromArgs`) shares the component; verify the rich chrome reads
  correctly in the narrow in-thread context too (the `compact` prop path,
  `SubagentCard.tsx:74`), not just the pane.
