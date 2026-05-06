# PR 3.2.2 — Subagent card: shared primitive across thread + workspace pane

> **Status:** Draft (PRD + Spec) · v1
> **Plan reference:** Wave 3 follow‑up to [`pr-3.2.1-agents-tab-expandable-timeline.md`](./pr-3.2.1-agents-tab-expandable-timeline.md). Closes the visual gap users surfaced after PR 3.2.1 shipped — both surfaces now look right, both expand to the same timeline.
> **Owner:** frontend (1 shared primitive + 2 callsite restyles + ~80 LoC CSS) · ai‑backend: **none** · facade: **none** · api‑types: **none**.
> **Size:** **S.** Extracts a single `<SubagentCard>` from existing pieces. Net code is roughly neutral (deletions in `SubagentTool` + `AgentsTab` ad‑hoc layout offset the new shared primitive).
> **Depends on:** ✅ PR 3.2.1 (`useSubagentActivities`, `SubagentActivityList` className prop, `<details>` disclosure pattern) — shipped.
> **Reads alongside:** [`pr-3.2.1-agents-tab-expandable-timeline.md`](./pr-3.2.1-agents-tab-expandable-timeline.md), [`pr-1.5-subagent-discovery-workspace-feeds.md`](./pr-1.5-subagent-discovery-workspace-feeds.md), [`apps/frontend/CLAUDE.md`](../../apps/frontend/CLAUDE.md).

---

## 0 · TL;DR

Today the subagent UI splits across **two divergent visuals**:

- **In‑thread:** `SubagentTool.tsx` renders a generic `<ActivityItem>` ("Subagent finished — Writing a clean Python 3… ▶ Details"). Looks identical to every other tool call. No task line, no finding line, no progress, no per‑subagent identity.
- **Workspace pane:** `AgentsTab.tsx` renders an ad‑hoc card layout that dumps `objective_summary` (the full user prompt) and `result_summary` (the full raw response, including code blocks) verbatim. No truncation, no line clamp, no information hierarchy. The disclosure (PR 3.2.1) often expands to "No detailed activity was reported." because single‑tool subagents have no inner steps.

Both surfaces should share **one card primitive** — same vocabulary, same disclosure → same timeline (`SubagentActivityList`). This PR extracts `<SubagentCard>`, retrofits both callsites, applies CSS line clamping, and fixes the "empty disclosure → confusion" UX.

**Zero backend.** The data is what it is; the projection quality (long `objective_summary` / `result_summary` instead of true LLM‑summarised one‑liners) is a separate backend follow‑up tracked in §6.

LoC estimate: frontend ≈ +220 (new primitive) − 100 (deletions in `SubagentTool` + `AgentsTab`) = **net ~+120 LoC** plus tests.

---

## 1 · PRD

### 1.1 Problem

User feedback after PR 3.2.1:

> _"the result in Click 'Agents' in the workspace pane tab strip → you'll see one card: status badge + 'Subagent finished' name + objective + result summary — sucks. Tell me why it's bad from a user's perspective."_

Concrete UX failures, observed in production with the prime‑checker scenario (one single‑shot subagent, code result):

1. **Wall‑of‑text dumping.** `objective_summary` is the user's verbatim prompt (≈350 chars); `result_summary` is the verbatim response with embedded code fences (≈600 chars). Neither field is actually a summary. The card consumes more vertical space than the entire pane.
2. **No information hierarchy.** Two text blobs back to back, identical typography, no separation between _task_ and _finding_.
3. **No truncation.** With three parallel subagents, the pane scrolls forever. Multi‑subagent verification — the use case the pane exists to serve — is unusable.
4. **Disclosure dead‑end.** Single‑tool subagents have empty `args.activities`; expand reveals "No detailed activity was reported." right next to a visible result. To a non‑engineer that reads like: _"the system is lying to me."_
5. **Disclosure chevron in the wrong place.** Trailing the duration meta at the bottom right; convention puts it at the start of the row or in the header.
6. **Code in a "summary" field.** Fenced ` ```python ` in a sidebar card is a category error.
7. **`<ActivityItem>` in‑thread is too generic.** Subagents lose visual identity — they look the same as a `read_file` or `web_search` tool call. They are conceptually different (autonomous nested run, not a single tool invocation) and should look different.

### 1.2 Goals

1. **One card, two surfaces.** A single `<SubagentCard>` primitive consumed by `SubagentTool.tsx` (in‑thread) and `AgentsTab.tsx` (workspace pane). Same vocabulary, same disclosure semantics, same timeline rendering — DRY by construction.
2. **Truncate at the FE.** Line‑clamp task to 2 lines and finding to 3 lines via `-webkit-line-clamp`. Truncate raw text to ~160 chars (task) / ~280 chars (finding) before clamping so DOM payload is bounded. Never block on a backend better‑projection PR to ship correct UX.
3. **Disclosure that always pays off.** Empty‑activity body falls back to the truncated‑away tail of `result_summary` (so users see _the rest_ of what was hidden by the clamp) instead of "No detailed activity was reported." For genuinely silent subagents, surface a calm "Single‑shot response — no inner tool calls" instead of a negative null.
4. **Visual identity for subagents.** Distinct chrome from a tool call: subagent icon, status pill, role‑style name in caps (matches design), separated meta row.
5. **Click‑to‑expand parity with PR 3.2.1.** The in‑thread block, the pane card — same `<details>` mechanic, same `<SubagentActivityList>` body, same `aui-tool-card__timeline` (composed with a pane‑narrow variant in the workspace pane, per PR 3.2.1 §2.6).
6. **Zero new dep, zero backend, zero contract change.** This is a frontend visual unification PR, nothing more.

### 1.3 Non‑goals

- ❌ **Better backend projections** (true 1‑line LLM‑summarised `objective_summary` / `result_summary`). Tracked in §6 as a follow‑up. Once it lands, the FE clamp becomes belt‑and‑braces and the cards get tighter — no FE change needed.
- ❌ **Per‑step deep links** (per PR 3.2.1 §6 follow‑up).
- ❌ **Cancel from the card.** Per PR 1.5 §1.3 non‑goal.
- ❌ **Token/cost footer in the disclosure.** Per PR 3.2.1 §6 follow‑up.
- ❌ **A new design‑system primitive.** Subagent cards are a feature‑level concern, not a cross‑feature reusable. They live in `apps/frontend/src/features/chat/components/subagents/`.
- ❌ **Redesign of the in‑thread fleet card** (`SubagentFleetCard`). It already matches the design (PR A2/F1). Out of scope.
- ❌ **Single‑subagent → "fleet of 1" rendering.** Different conceptual frame. Out of scope.

### 1.4 Acceptance criteria

| #     | Criterion                                                                                                                                                                                                                                                                                                                                    | Verified by                |
| ----- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------- |
| AC‑1  | A single `<SubagentCard>` component is consumed by both `SubagentTool` (thread) and `AgentsTab` (pane). No second timeline / disclosure implementation exists.                                                                                                                                                                               | grep + integration test.   |
| AC‑2  | Task line is clamped to **2 lines** and ≤ 160 chars of payload.                                                                                                                                                                                                                                                                              | RTL test + computed style. |
| AC‑3  | Finding line (terminal subagents only) is clamped to **3 lines** and ≤ 280 chars of payload.                                                                                                                                                                                                                                                 | RTL test + computed style. |
| AC‑4  | When `args.activities` is empty AND `result_summary` exists, the disclosure body shows the full result text (truncated to ≤ 600 chars) — **not** "No detailed activity was reported."                                                                                                                                                        | RTL test.                  |
| AC‑5  | When `args.activities` is empty AND `result_summary` is empty (truly silent subagent), the disclosure body shows the calm fallback "Single‑shot response — no inner tool calls."                                                                                                                                                             | RTL test.                  |
| AC‑6  | When `args.activities` is non‑empty, the disclosure body renders `<SubagentActivityList>` (same primitive PR 3.2.1 wired). The pane callsite composes the `atlas-workspace-agent__timeline` variant; the thread callsite uses the default `aui-tool-card__timeline`.                                                                         | RTL test.                  |
| AC‑7  | Disclosure default state on first render is **closed**. `focusTaskId` (pane) auto‑opens the targeted card; in‑thread the user opens manually. Open state is component‑local (carries over PR 3.2.1 behavior).                                                                                                                                | RTL test.                  |
| AC‑8  | Status icon and badge tone match the lifecycle: `running` → accent + spinner; `completed` → success + ✓; `failed` → danger + ✕; `cancelled` → warning + −; `queued` → muted + dot.                                                                                                                                                           | RTL test (one per status). |
| AC‑9  | The pane card includes the existing **↗ jump‑to‑thread** affordance from PR 3.2; the in‑thread card omits it (already the focus). Both keep the disclosure.                                                                                                                                                                                  | RTL test.                  |
| AC‑10 | Bundle‑size delta ≤ +1 KB gz versus PR 3.2.1 baseline. Net code is approximately neutral.                                                                                                                                                                                                                                                    | `npm run build` size diff. |
| AC‑11 | `prefers-reduced-motion: reduce` honored — no expand animation, no chevron rotation transition. (Inherits PR 3.2.1 CSS.)                                                                                                                                                                                                                     | CSS audit.                 |
| AC‑12 | Existing PR 3.2.1 tests stay green: `useSubagentActivities.test.ts`, `AgentsTab.test.tsx` disclosure semantics. Existing `SubagentTool` thread tests stay green; we update them in place to assert the new card structure (which still satisfies their semantic expectations: name, status, summary, terminal vs running, optional details). | Full vitest suite.         |

### 1.5 Risks

| Risk                                                                                                                                                                                  | Mitigation                                                                                                                                                                                                                                    |
| ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Two callsites with two different upstream data shapes (`SubagentTool` reads `args`, `AgentsTab` reads `SubagentEntry`) drift apart over time.                                         | A small adapter (`subagentCardViewModel`) is the **only** entry into `<SubagentCard>`. New fields go through it. Adapter coverage tested.                                                                                                     |
| `result_summary` contains markdown / fenced code that breaks line clamping visually.                                                                                                  | Render the truncated result through a `<pre>`‑safe text mode (no markdown rendering inside the card body — bare text only). Markdown is a thread concern, not a sidebar‑summary concern. Code fences are rendered as text and clamp normally. |
| Changing in‑thread `SubagentTool` from `<ActivityItem>` to `<SubagentCard>` regresses surrounding visual rhythm in the thread (subagents now stand out where before they blended in). | _Intended._ Subagents are conceptually different from a single tool call (nested autonomous run) and should look different. Visual diff captured in PR.                                                                                       |
| Pane card visual changes break PR 3.2.1's screenshot tests.                                                                                                                           | We don't have screenshot tests today; we test against DOM structure + classes. Updated tests cover the new structure.                                                                                                                         |
| Backend ships a better `result_summary` projection later and the FE clamp now hides too little (or too much).                                                                         | Belt‑and‑braces: the backend can ship arbitrarily short/long summaries; the FE clamp protects the visual independent of payload length. When the backend ships well‑summarised text, the clamp simply rarely fires. No FE change needed.      |
| Truncation hides the actual answer for users who actually want the code.                                                                                                              | The full text is one click away in the disclosure body (AC‑4). The thread also has the full assistant response (already there, untouched). Truncation never destroys data.                                                                    |

### 1.6 Unit testing requirements

- **NEW** `apps/frontend/src/features/chat/components/subagents/SubagentCard.test.tsx` — AC‑2, 3, 4, 5, 6, 7, 8, 11. Driven by adapter‑built view models.
- **NEW** `apps/frontend/src/features/chat/components/subagents/subagentCardViewModel.test.ts` — adapter pure tests: builds VM from `args` (thread) and from `SubagentEntry` (pane); truncation; status normalization; missing field defaults.
- **EXTEND** `apps/frontend/src/features/chat/components/workspace/AgentsTab.test.tsx` — assert `↗ jump-to-thread` still works alongside the new card (AC‑9). Existing PR 3.2.1 tests already pass against the disclosure DOM structure; we just point them at the new component tree.
- **REPLACE** `SubagentTool` snapshot/structural tests if any exist (only update the structural assertions that referenced `<ActivityItem>` markup).

---

## 2 · Spec

### 2.1 Architecture

```
┌────────────────────────────────────────────────────────────────────┐
│  EXISTING (PR 3.2.1)                                               │
│   useSubagentActivities(items)  ──►  Map<task_id, activities[]>    │
│   SubagentActivityList                                             │
│   ActivityCollapsible (native <details>)                           │
└─────────────────────────┬──────────────────────────────────────────┘
                          │
                          │ this PR composes the existing pieces
                          ▼
┌────────────────────────────────────────────────────────────────────┐
│  NEW shared primitive                                              │
│                                                                    │
│   <SubagentCard view={vm} activities={activities} ↗onJump?         │
│                 timelineClassName?  defaultOpen?>                  │
│       ┌──── header row ─────────────────────────────┐              │
│       │ [icon] [name] [badge]            [↗ jump?]  │              │
│       ├──── task line (clamp 2) ────────────────────┤              │
│       │ {vm.task}                                   │              │
│       ├──── finding line (clamp 3, terminal only) ──┤              │
│       │ {vm.finding}                                │              │
│       ├──── meta row ────────────────────────────────┤             │
│       │ Dispatched · {when} ───── {durationLabel}   │              │
│       ├──── disclosure ─────────────────────────────┤              │
│       │  ▾  (clickable summary)                     │              │
│       │     activities.length                        │              │
│       │       ? <SubagentActivityList .../>          │              │
│       │       : result ? <pre>{truncated result}</pre>│            │
│       │       : "Single-shot response — no inner    │              │
│       │          tool calls."                       │              │
│       └─────────────────────────────────────────────┘              │
│                                                                    │
│   subagentCardViewModel(input)                                     │
│     adapter:                                                       │
│       fromArgs(args, status, isError) → vm   (in-thread callsite)  │
│       fromEntry(entry, isFocused?)    → vm   (pane callsite)       │
│                                                                    │
└────────────────────────┬───────────────────────────────────────────┘
                         │
              ┌──────────┴──────────┐
              ▼                     ▼
        SubagentTool.tsx       AgentsTab.tsx
        (thread)               (workspace pane)
        replaces ActivityItem  replaces ad-hoc card markup
```

### 2.2 Module boundaries

| Layer                                                                                            | Module                                                                                                                                                                                                                                   | Owns                                                                                                                                                                              |
| ------------------------------------------------------------------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `apps/frontend/src/features/chat/components/subagents/SubagentCard.tsx`                          | **NEW** — pure presentational. Renders the card from a view model + activities. No fetching, no event subscription.                                                                                                                      | UI composition. Truncation + line clamp via CSS. Disclosure via `<details>`.                                                                                                      |
| `apps/frontend/src/features/chat/components/subagents/subagentCardViewModel.ts`                  | **NEW** — adapter. Two builders: `fromArgs(args, status, isError)` and `fromEntry(entry)`. Pure; testable without React.                                                                                                                 | Single source of truth for "how do we shape arbitrary subagent data into the card's input." Truncates raw text **before** the DOM payload (defense in depth on top of CSS clamp). |
| `apps/frontend/src/features/chat/components/subagents/SubagentCard.css` (or extend `styles.css`) | **NEW** — `subagent-card*` BEM block. Line‑clamp (`-webkit-line-clamp`). Header / task / finding / meta / disclosure spacing.                                                                                                            | Visual contract. ~80 LoC. Inherits PR 3.2.1 disclosure chrome.                                                                                                                    |
| `apps/frontend/src/features/chat/components/tools/SubagentTool.tsx`                              | **REWRITE** — replaces `<ActivityItem>` with `<SubagentCard view={fromArgs(...)} activities={activities} />`. Keeps existing prop contract.                                                                                              | Adapter callsite for the thread. ~30 LoC of adapter wiring; deletes ~50 LoC of `<ActivityItem>` plumbing.                                                                         |
| `apps/frontend/src/features/chat/components/workspace/AgentsTab.tsx`                             | **REWRITE (card body)** — replaces ad‑hoc card layout with `<SubagentCard view={fromEntry(entry)} activities={...} timelineClassName="atlas-workspace-agent__timeline aui-tool-card__timeline" onJump={...} defaultOpen={isFocused} />`. | Adapter callsite for the pane. ~25 LoC; deletes ~40 LoC of bespoke card markup. The list/empty/loading shell around the cards stays; only the card body swaps.                    |
| `apps/frontend/src/features/chat/components/workspace/useSubagentActivities.ts` (PR 3.2.1)       | **UNCHANGED**. Still the source of truth for the pane.                                                                                                                                                                                   |                                                                                                                                                                                   |
| `apps/frontend/src/features/chat/components/tools/SubagentActivityList.tsx`                      | **UNCHANGED** (already accepts `className` per PR 3.2.1).                                                                                                                                                                                |                                                                                                                                                                                   |

**Not changed:** any backend file, any contract, any migration, any event reducer.

### 2.3 What we do NOT add

- ❌ A new dep. We continue with native `<details>` (per PR 3.2.1 §3 library evaluation — Radix Collapsible, Headless UI, react‑aria all rejected).
- ❌ A second timeline component. `SubagentActivityList` is the only timeline.
- ❌ A markdown renderer in the card body. Sidebar summaries are bare text. Streamdown stays in the thread proper.
- ❌ A design‑system primitive. Subagents are a chat feature; promoting too early calcifies an evolving shape.
- ❌ State for "open cards persist across navigation." (PR 3.2.1 §6 follow‑up.)

### 2.4 View‑model contract

```ts
// subagentCardViewModel.ts

export type SubagentCardStatus =
  | "queued"
  | "running"
  | "completed"
  | "failed"
  | "cancelled"
  | "timed_out";

export interface SubagentCardViewModel {
  /** task_id — used for data-testid + a11y labels. */
  taskId: string | null;
  /** Display name (e.g. "Doc reader" or "research"). */
  name: string;
  /** Lifecycle status (normalised). */
  status: SubagentCardStatus;
  /** Whether the lifecycle is terminal (anything except queued/running). */
  terminal: boolean;
  /** What the subagent was asked to do. Truncated to ≤ 160 chars. */
  task: string | null;
  /** What the subagent reports. Truncated to ≤ 280 chars. Only when terminal. */
  finding: string | null;
  /** Full result, used by the disclosure when activities is empty. ≤ 600 chars. */
  fullResult: string | null;
  /** ISO timestamp; used to render "Dispatched · 2m ago" via a small helper. */
  startedAt: string | null;
  /** ISO timestamp; null while running. */
  completedAt: string | null;
  /** Server-projected duration (PR 1.5 token rollup). */
  durationMs: number | null;
  /** Did the run end in error (drives danger badge). */
  isError: boolean;
}

export function subagentCardFromArgs(
  args: Record<string, unknown>,
  status: { type: string },
  isError: boolean | undefined,
): SubagentCardViewModel { ... }

export function subagentCardFromEntry(
  entry: SubagentEntry,
): SubagentCardViewModel { ... }
```

Truncation happens in the adapter: `truncateText(rawTask, 160)`, `truncateText(rawFinding, 280)`, `truncateText(rawFinding, 600)` for the disclosure fallback. The CSS `-webkit-line-clamp` provides the visual second layer (covers wrapping + multi‑line content).

### 2.5 `<SubagentCard>` props

```ts
export interface SubagentCardProps {
  view: SubagentCardViewModel;
  /** Inner activities to render in the disclosure (PR 3.2.1 selector). */
  activities: readonly SubagentActivityRecord[];
  /** Optional className override for the timeline. Pane uses
   *  `"atlas-workspace-agent__timeline aui-tool-card__timeline"`; thread
   *  uses the default `"aui-tool-card__timeline"`. */
  timelineClassName?: string;
  /** Optional jump-to-thread affordance (workspace pane only). */
  onJumpToThread?: () => void;
  /** Auto-expand the disclosure on first render. Used by the pane's
   *  focus-task target. Component-local thereafter. */
  defaultOpen?: boolean;
}
```

### 2.6 CSS sketch

```css
/* subagent-card — shared visual for in-thread + workspace pane */
.subagent-card {
  background: var(--color-surface);
  border: 1px solid var(--color-border-subtle);
  border-radius: var(--radius-md);
  padding: var(--space-sm);
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.subagent-card__head {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.04em;
}

.subagent-card__name {
  font-weight: 600;
  flex: 1 1 auto;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.subagent-card__task {
  color: var(--color-text);
  font-size: 13px;
  line-height: 1.45;
  margin: 0;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.subagent-card__finding {
  color: var(--color-text-muted);
  font-size: 12.5px;
  line-height: 1.5;
  margin: 0;
  display: -webkit-box;
  -webkit-line-clamp: 3;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.subagent-card__meta {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-size: 11px;
  color: var(--color-text-subtle);
}

.subagent-card__details {
  margin-top: 2px;
}

.subagent-card__details-summary {
  align-items: center;
  cursor: pointer;
  display: flex;
  gap: 6px;
  list-style: none;
  padding: 2px 0;
  user-select: none;
  font-size: 11px;
  color: var(--color-text-subtle);
}

.subagent-card__details-summary::-webkit-details-marker {
  display: none;
}

.subagent-card__disclosure-hint {
  font-size: 10px;
  transition: transform 120ms ease;
}

.subagent-card__details[open]
  > .subagent-card__details-summary
  .subagent-card__disclosure-hint {
  transform: rotate(180deg);
}

@media (prefers-reduced-motion: reduce) {
  .subagent-card__disclosure-hint {
    transition: none;
  }
}

.subagent-card__empty {
  color: var(--color-text-subtle);
  font-style: italic;
  font-size: 12px;
  margin: 4px 0 0;
}

.subagent-card__full-result {
  font-family: ui-monospace, SFMono-Regular, monospace;
  font-size: 11.5px;
  white-space: pre-wrap;
  word-break: break-word;
  color: var(--color-text);
  background: var(--color-surface-raised);
  border-radius: var(--radius-sm);
  padding: var(--space-xs);
  margin: 4px 0 0;
  max-height: 240px;
  overflow: auto;
}
```

### 2.7 Streaming + persistence walk‑through (unchanged from PR 3.2.1)

The data path is identical to PR 3.2.1. Worker emits events with `parent_task_id`; reducer nests them into `args.activities`; selector projects `Map<task_id, activities[]>`; pane reads from selector; thread reads from `args.activities` directly. This PR only changes the **rendering layer** of two callsites and adds one shared component. No new event, no new endpoint, no new persistence column. RLS / encryption / tenant isolation invariant.

### 2.8 Failure modes

| Failure                                                 | Rendered behavior                                                                                                                                     |
| ------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------- |
| `args` missing entirely (defensive)                     | Adapter returns a view model with `name = "Subagent"`, `status = "running"`, all text fields `null`. Card renders "working…" header only.             |
| `objective_summary` is empty                            | Task line not rendered (no empty `<p>`).                                                                                                              |
| `result_summary` is empty + activities empty (terminal) | Disclosure body shows the calm empty fallback (AC‑5).                                                                                                 |
| `result_summary` contains markdown / code fences        | Truncated as plain text, rendered through `<pre>` in the disclosure (preserves whitespace, no markdown parsing — sidebar summary, not thread output). |
| Cancelled subagent with partial activities              | Activities visible; status badge `warning`; finding shows the truncated result if present.                                                            |
| Activities update live while the disclosure is open     | New row appears at the bottom; `<details>` keeps `open` state (PR 3.2.1 behavior preserved).                                                          |

---

## 3 · Library evaluation

Per PR 3.2.1 §3 — the same evaluation applies. We evaluated and rejected `@radix-ui/react-collapsible`, `@headlessui/react`, `react-aria/useDisclosure`, `framer-motion`, `react-vertical-timeline-component`. **Zero new deps.**

One additional candidate evaluated for this PR:

| Library                                | What it gives                  | Why we don't add it                                                                                                                                           |
| -------------------------------------- | ------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `react-clamp-lines` / `react-truncate` | JS line clamping with ellipsis | CSS `-webkit-line-clamp` (Webkit/Blink/Firefox) covers our targets and is zero‑runtime. `truncateText` already exists in `utils/jsonUtils.ts` for char‑level. |

---

## 4 · File change summary

```
apps/frontend/src/features/chat/components/subagents/             NEW directory
  SubagentCard.tsx                                  ~+150 LoC   shared primitive
  SubagentCard.test.tsx                             ~+180 LoC   AC-2..8, 11
  subagentCardViewModel.ts                          ~+85 LoC    adapter
  subagentCardViewModel.test.ts                     ~+110 LoC   adapter unit
  index.ts                                          ~+5  LoC    barrel

apps/frontend/src/features/chat/components/tools/
  SubagentTool.tsx                                  ~+30/-65    swap to <SubagentCard>

apps/frontend/src/features/chat/components/workspace/
  AgentsTab.tsx                                     ~+25/-45    swap card body to <SubagentCard>
  AgentsTab.test.tsx                                ~+10/-5     adjust to new DOM (jump-button still works)

apps/frontend/src/styles.css                         ~+85 LoC   subagent-card BEM block

# nothing else changes
services/*                                            0
packages/*                                            0
migrations/*                                          0
package.json                                          0 deps
```

---

## 5 · Verification checklist

- [ ] `npm run typecheck --workspace @enterprise-search/frontend` clean.
- [ ] `npm run test --workspace @enterprise-search/frontend` clean; new tests pass; PR 3.2.1 tests still pass.
- [ ] `npm run build --workspace @enterprise-search/frontend` clean; bundle delta ≤ +1 KB gz vs PR 3.2.1.
- [ ] Manual `make dev`:
  - Single‑shot subagent (e.g. prime checker): in‑thread block now shows name + task + finding + meta + disclosure; pane card shows the same; expanding the disclosure (with no activities) reveals the truncated full result, **not** "No detailed activity was reported."
  - Multi‑subagent fleet (FY26 Q1 launch): each fleet child + each pane card uses the same `<SubagentCard>`. Activities expand to the timeline.
  - Failed subagent: danger badge tone; disclosure shows the partial timeline.
  - Mobile / narrow viewport: line clamps engaged; cards stay legible at < 380 px column width.
- [ ] No new entry in `package.json` `dependencies`.
- [ ] `prefers-reduced-motion: reduce` honored.

---

## 6 · Out of scope (follow‑ups)

Tracked, not in this PR:

- **Backend better projection** — `objective_summary` / `result_summary` should be true LLM‑summarised one‑liners, not raw passthroughs. Owns its own PR (touch `services/ai-backend/src/runtime_api/schemas/events.py:RuntimeEventPresentationProjector` and the worker emit path).
- **Persist disclosure open state across navigation** (PR 3.2.1 §6).
- **Per‑step deep links** (PR 3.2.1 §6).
- **Per‑subagent token / cost footer** (PR 3.2.1 §6 — `SubagentEntry.token_usage` already returned by PR 1.5).
- **Promote `<SubagentCard>` to `packages/design-system`** if other features (Tasks tab, Audit log) end up consuming the same shape. Premature today.

---

## References

- [`docs/new-design/pr-3.2.1-agents-tab-expandable-timeline.md`](./pr-3.2.1-agents-tab-expandable-timeline.md) — disclosure mechanism + selector hook this PR composes with.
- [`docs/new-design/pr-1.5-subagent-discovery-workspace-feeds.md`](./pr-1.5-subagent-discovery-workspace-feeds.md) — `SubagentEntry` shape consumed by `subagentCardFromEntry`.
- [`apps/frontend/src/features/chat/components/tools/SubagentActivityList.tsx`](../../apps/frontend/src/features/chat/components/tools/SubagentActivityList.tsx) — timeline primitive reused verbatim.
- [`apps/frontend/src/features/chat/components/activity/ActivityCollapsible.tsx`](../../apps/frontend/src/features/chat/components/activity/ActivityCollapsible.tsx) — native `<details>` pattern.
- [`apps/frontend/src/features/chat/utils/jsonUtils.ts:truncateText`](../../apps/frontend/src/features/chat/utils/jsonUtils.ts) — char‑level truncation helper used by the adapter.
- Design prototype reference: `enterprise-search/project/composer.jsx` (`AgentsPane`, `Subagent` ~lines 233–266 / 280–315) and `enterprise-search/project/messages.jsx` (`SubagentFleet` ~lines 207–243).
