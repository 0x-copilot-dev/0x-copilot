# PRD-08 — Activity surface: meta line, wall-clock time, navigation affordance, day dividers

## Problem

PRD-04 gives Activity real run titles; PRD-05 gives it real run history. This PRD is what
the rows then look like — and today every one of those presentation decisions is wrong in
a way a user can name.

1. **The second line of every row is blank.** The design's row reads
   `Launch Week ops` / **`4 apps · 7 steps · awaiting 1 approval`** / `11:44`. Ours renders
   the title, the chip, the time — and nothing where the summary goes. The slot exists; the
   data behind it does not. Both hosts try to synthesise it by scanning an audit feed whose
   rows do not join to runs, so in practice it comes back empty. And when a workspace turns
   RBAC enforcement on, the audit request 403s and both hosts convert that 403 into `[]` —
   so the record surface silently degrades to a list of titles with **no error, no badge, no
   log line**. On a page whose entire purpose is "here is what the agent did", a silent
   downgrade to "here is a list of nouns" is the worst possible failure mode.
2. **Every row says how long ago under a heading that already says the day.** A row under
   "Yesterday" reads "1d ago". Three rows under "Mon, Jul 14" all read "8d ago" — the same
   string, so the feed loses its within-day ordering cue entirely. The design shows the wall
   clock: `11:44`, `09:02`, `08:15`.
3. **Nothing tells you which rows you can click.** Exactly one row in a full feed is
   activatable. It is visually identical to the seven that are not — no chevron, no
   affordance — and nothing at all reacts to the pointer, because the row primitive is
   styled entirely with inline style objects and an inline style object cannot express
   `:hover`.
4. **The day divider shouts.** The design draws a quiet lowercase-cased mono `Mon, Jul 14`
   at 10px regular. We render bold uppercase **`JUL 14, 2026`** — 20% larger, semibold,
   letter-spaced, weekday dropped, year added — because the divider is wearing the section
   header's clothes. The class attribute literally says `act-day sect-h`: it claims to be
   both, and it is styled as the wrong one.
5. **The leading icon column stops reading as a column.** The design's 28×28 tile sits on a
   `--panel3` surface with a 7px radius. Ours has no background at all, and the jade "this
   run is live" tint is applied to an inner `<span>` wrapped around the glyph — so it
   colours the turbine and never reaches the tile it was meant to colour.
6. **Four of the five states have never been specified.** The design has exactly one state
   (populated). We ship five. Two of them are inventions no one reviewed — including
   "No activity yet", which PRD-05's data path makes the _default_ screen for a user with
   fifty completed runs, and an "unavailable" branch no binder can construct.

## Evidence

Every row opened and read in this worktree on `claude/design-parity-audit-7ec82a`.

| Claim                                                                                              | File:line                                                                                                                                                 | What the code actually does                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       |
| -------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----- | ----------- | ----- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| The meta slot is real and always empty in practice                                                 | `packages/chat-surface/src/destinations/activity/ActivityDestination.tsx:519-522`                                                                         | CONFIRMED. `row.meta.length > 0 ? <span data-testid="activity-row-meta">{row.meta}</span> : undefined` → `Row.tsx:184-188`. The component is fine; the string arriving is `""`.                                                                                                                                                                                                                                                                                                                                   |
| `meta` is client-derived from `/v1/audit` on web                                                   | `apps/frontend/src/features/activity/api/activityApi.ts:76-125, 163-167`                                                                                  | CONFIRMED. `auditLabel()` picks the first of `metadata.{connector_id, server_id, display_name, tool_name}`; `buildMetaIndex()` keys by `row.resource_id`; the row joins under **both** `runId` and `conversation_id` and `.join(" · ")`s the distinct labels. It is a name list, never counts.                                                                                                                                                                                                                    |
| …and duplicated verbatim on desktop                                                                | `apps/desktop/renderer/destinationBinders.tsx:242-321`                                                                                                    | CONFIRMED. `mapRunStatus` / `auditLabel` / `buildMetaIndex` / `projectActivityRows` re-declared line-for-line. Two copies of a projection that produces the wrong answer.                                                                                                                                                                                                                                                                                                                                         |
| Both hosts swallow an audit 401/403 into `[]`                                                      | `activityApi.ts:211`; `destinationBinders.tsx:344`                                                                                                        | CONFIRMED. Both are `.catch(() => [] as AuditEvent[])` on the audit half of a `Promise.all`. No status inspection, no logging, no user-visible signal.                                                                                                                                                                                                                                                                                                                                                            |
| The facade deliberately surfaces backend audit 401/403                                             | `services/backend-facade/src/backend_facade/audit_routes.py:137-141`                                                                                      | CONFIRMED. `if backend_resp.status_code in (401, 403): _raise_for_upstream(backend_resp)` with the comment "that's the canonical 'you're not allowed to read audit' answer". The clients throw that answer away.                                                                                                                                                                                                                                                                                                  |
| The backend audit list is admin-scope gated                                                        | `services/backend/src/backend_app/routes/audit_list.py:104-111`                                                                                           | CONFIRMED. `dependencies=[Depends(RequireScopes(ADMIN_AUDIT_EXPORT))]`. Latent, not live: `RBAC_MODE` defaults to `audit` (log-and-pass) at `services/backend/src/backend_app/identity/rbac.py:41-49`. Reading Activity should never have required an admin audit-export scope in the first place.                                                                                                                                                                                                                |
| The facade already reports partial degradation, and no client reads it                             | `audit_routes.py:148, 189`                                                                                                                                | CONFIRMED. `degraded_streams` is computed and returned. Zero reads in `apps/frontend/src/features/activity` or `apps/desktop/renderer`.                                                                                                                                                                                                                                                                                                                                                                           |
| **`runtime_tool_invocations` exists, fully modelled, with the right index — and has ZERO writers** | `services/ai-backend/migrations/0001_runtime_baseline.sql:586-607, 1039-1041`; `services/ai-backend/src/agent_runtime/persistence/records/tools.py:19-37` | CONFIRMED, and this is the load-bearing find. Table has `run_id, org_id, tool_name, connector_slug, status, approval_id, started_at, completed_at` + `idx_runtime_tool_invocations_org_run_started (org_id, run_id, started_at)`. `ToolInvocationRecord` is referenced only by `persistence/__init__.py` and `records/__init__.py` — no adapter INSERT, no service, no worker call. `grep -n "INSERT INTO runtime_tool_invocations"` across `src/` returns nothing. The correct model is built and dormant.       |
| Connector attribution is explicitly stubbed                                                        | `services/ai-backend/src/runtime_worker/tool_call_ledger.py:41-43`                                                                                        | CONFIRMED, verbatim comment: "Connector that owned this tool; currently always `None` until a tool-name → connector lookup is plumbed in." Therefore `runtime_model_call_usage.connector_slug` (`postgres/runtime_api_store.py:2540, 2570`) is always NULL too. **No truthful "4 apps" is computable anywhere today.**                                                                                                                                                                                            |
| The MCP server name IS recoverable at the tool-call seam                                           | `services/ai-backend/src/agent_runtime/capabilities/mcp/dispatcher.py:75-97`                                                                              | CONFIRMED. `effective_server_name()` reads `payload.args.server_name` for `call_mcp_tool` dispatcher events. The lookup the ledger comment says is missing is a resolve away from an existing helper.                                                                                                                                                                                                                                                                                                             |
| The tool lifecycle write seam already exists                                                       | `services/ai-backend/src/runtime_worker/stream_tools.py:327` (started), `:250` (settled)                                                                  | CONFIRMED. `self.ledger_for_run(run.run_id).started(...)` / `.observed_settled(settled_call_id)`. One in-flight ledger entry per tool call, already keyed by `call_id`.                                                                                                                                                                                                                                                                                                                                           |
| Approvals ARE persisted per run, and ARE indexed by run                                            | `migrations/0001_runtime_baseline.sql:187-211, 947`; `persistence/records/approvals.py:18-41`                                                             | CONFIRMED. `runtime_approval_requests(run_id, org_id, status)` with `status IN ('pending','approved','rejected','forwarded')` and `idx_runtime_approval_requests_org_run_status (org_id, run_id, status)`. "awaiting N approval" is a `COUNT(*) … WHERE status='pending'` on an existing index.                                                                                                                                                                                                                   |
| The only `tool_name` audit emitter is dead code                                                    | `services/ai-backend/src/runtime_worker/audit.py:194`                                                                                                     | CONFIRMED. `emit_tool_call_outcome` — the sole definition, zero call sites repo-wide. Confirms the audit-derived meta line can never have worked.                                                                                                                                                                                                                                                                                                                                                                 |
| `Row` has no trailing slot                                                                         | `packages/chat-surface/src/destinations/_shared/Row.tsx:32-53` (props), `:121-192` (render)                                                               | CONFIRMED. Props are `icon, title, chip, sub, meta, onActivate, ariaLabel`. Render order is icon → main → meta, then closes. Nothing after `meta`.                                                                                                                                                                                                                                                                                                                                                                |
| The chevron glyph is already in the icon SSOT, unused                                              | `packages/chat-surface/src/icons/paths.tsx:42, 157`                                                                                                       | CONFIRMED. `chevronRight: <path d="M9 6l6 6-6 6" />` — **byte-identical** to the design's `Icon.chevR` (`design-kit/app-v3/copilot-data.jsx:94-98`). Zero call sites repo-wide.                                                                                                                                                                                                                                                                                                                                   |
| `Row` has no hover treatment and structurally cannot get one                                       | `Row.tsx:55-66` (`rowStyle`), `:143-160`                                                                                                                  | CONFIRMED. Every visual property is an inline `CSSProperties`; the only pointer feedback is `cursor: interactive ? "pointer" : undefined` at `:156`. `RowList` already emits `className="rowlist"` (`RowList.tsx:56`), so a CSS hook exists but nothing targets it.                                                                                                                                                                                                                                               |
| The icon slot has no surface and the wrong radius                                                  | `Row.tsx:68-79`                                                                                                                                           | CONFIRMED. `iconSlotStyle` sets `width/height: 28`, `borderRadius: var(--radius-md)` (= 8px, `styles.css:109`), `display: inline-flex`, `color: var(--color-text-muted)` — and **no `background`**.                                                                                                                                                                                                                                                                                                               |
| The live tint is on an inner span, not the tile                                                    | `ActivityDestination.tsx:493-500` + `:675-680`                                                                                                            | CONFIRMED. `liveIconStyle = { display:"inline-flex", …, color:"var(--color-success)" }` wraps `<BrandMark size={18}/>` _inside_ the `Row` icon slot, so the 28×28 tile keeps `--color-text-muted`. Measured: `row.live.ic` computes `color: rgb(152,152,159)` where the design computes `rgb(87,199,133)` (`surfaces/activity/out/report-default.md:16-18`).                                                                                                                                                      |
| **DISPUTED — the audit prescribes the wrong tile token**                                           | `AUDIT.md` R3 says `background: var(--color-surface-muted)`; `packages/design-system/src/styles.css:171, 201`                                             | The design's tile is `--panel3 #1d1d23` (`copilot.css:12, 1623`). `--color-surface-muted` is `#16161a` = the design's `--panel2` — which is _also_ the hover colour (`copilot.css:1601-1603`). Using it for the tile would make the tile vanish on hover. The exact match is **`--color-surface-elevated: #1d1d23`** (`styles.css:201`; light `#ebebee` = `copilot.css:76`). `packages/chat-surface/src/onboarding/onboarding.css:15` already documents this mapping. **The code wins; the audit's R3 is wrong.** |
| The divider wears both classes                                                                     | `ActivityDestination.tsx:451`                                                                                                                             | CONFIRMED, verbatim: `className="act-day sect-h"`. Inert today (neither class has a rule), but it declares the confusion the inline style then implements.                                                                                                                                                                                                                                                                                                                                                        |
| The divider is styled as a section label                                                           | `ActivityDestination.tsx:654-666`                                                                                                                         | CONFIRMED. `fontWeight: 600` (`:659`), `letterSpacing: 0.4` (`:660`), `textTransform: "uppercase"` (`:661`), `fontSize: var(--font-size-2xs)` = 11.2px. Design `.act-day` is weight 400, no tracking, no transform, 10px.                                                                                                                                                                                                                                                                                         |
| Explicit-date dividers lose the weekday and gain a year                                            | `ActivityDestination.tsx:136-142`                                                                                                                         | CONFIRMED. `Intl.DateTimeFormat(locale, { year:"numeric", month:"short", day:"numeric" })` → "Jul 14, 2026". Design fixture: `"Mon, Jul 14"` (`copilot-data.jsx:646`).                                                                                                                                                                                                                                                                                                                                            |
| Row time is relative                                                                               | `ActivityDestination.tsx:532-540` → `packages/chat-surface/src/util/time.ts:24-53`                                                                        | CONFIRMED. `formatRelativeTime(row.started_at, now)` inside a semantic `<time dateTime>`. Measured text `"11:44" → "46m ago"`, width `31.5px → 47.05px` (`report-default.md:144, 176`).                                                                                                                                                                                                                                                                                                                           |
| The time column is content-sized on both sides — no alignment argument either way                  | `copilot.css:1655-1659` (`.lrow__time { flex: none }`); `Row.tsx:114-120` (`flex: "0 0 auto"`)                                                            | CONFIRMED. The audit's refutation of the "variable width breaks the column" claim holds. Format choice is a legibility decision, not a layout one.                                                                                                                                                                                                                                                                                                                                                                |
| Relative time is what the _old_ PRD specified                                                      | `docs/plan/desktop-redesign/phase-4/PRD.md:113, 133`                                                                                                      | CONFIRMED — FR-4.4/FR-4.15 specify `formatRelativeTime`. This is design-vs-PRD drift, and this PRD resolves it in the design's favour (see D3).                                                                                                                                                                                                                                                                                                                                                                   |
| The surface has five states; the design has one                                                    | `ActivityDestination.tsx:295-303` (`resolveDataState`), branches at `:352-425`                                                                            | CONFIRMED. `loading                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               | error | unavailable | empty | ready`. `activity-unavailable` (`:389`) is unreachable — neither binder ever constructs `status:"unavailable"` (`activityApi.ts:196-224`; `destinationBinders.tsx:322-348`). Retry renders **only** on the error branch (`:376-380`). |
| `paused` is absent from the runtime enum                                                           | `packages/api-types/src/index.ts:210-228`                                                                                                                 | CONFIRMED. `AgentRunStatus` = `queued, running, waiting_for_approval, cancelling, cancelled, completed, failed, timed_out`. No `paused`.                                                                                                                                                                                                                                                                                                                                                                          |
| …and no client ever produces `"paused"` either                                                     | `packages/api-types/src/activity.ts:46-52`; `activityApi.ts:56-71`; `destinationBinders.tsx:242-258`                                                      | CONFIRMED. `ACTIVITY_RUN_STATUSES` declares `paused`, but both `mapRunStatus` implementations fold `waiting_for_approval → needs_input` and never emit `paused`. It is unreachable at **both** layers — a value with no producer.                                                                                                                                                                                                                                                                                 |
| The design's "paused" _is_ waiting-for-approval                                                    | `copilot-data.jsx:625-630`                                                                                                                                | CONFIRMED. `{ title:"Rebalance LP positions", meta:"paused — needed your approval on a swap", status:"paused" }`. The design's own copy names the runtime state.                                                                                                                                                                                                                                                                                                                                                  |
| A second label source already exists and is bypassed                                               | `packages/chat-surface/src/shell/statusTone.ts:40-56` vs `ActivityDestination.tsx:86-98`                                                                  | CONFIRMED. `statusTone()` returns `{tone, label, showDot}` with `paused → warning "Paused"`, `needs_input → info "Needs you"`; `ActivityDestination` calls `runStatusTone()` for the tone (`:487`) but re-derives the label itself (`:488` → `activityStatusLabel`). Two label sources, one component.                                                                                                                                                                                                            |
| **CORRECTION — the audit's `copilot.css` line numbers are wrong throughout**                       | `tools/design-parity/design-kit/app-v3/copilot.css` is 2909 lines; `copilot-v3.css` (789 lines, the file `index.html:16` links) `@import`s it             | The audit cites `.lrow:hover` at `copilot.css:287`, `.lrow__ic` at `:289`, `.act-day` at `:300`. The real lines are **1601-1603**, **1617-1626**, **1683-1697**. `copilot-v3.css` only redefines `.lrow` under a `.sd` (sidebar) scope at `:783`. All literal _values_ the audit quotes are correct; only its line anchors are not. This PRD cites the verified lines.                                                                                                                                            |

## Design intent

All literals from `tools/design-parity/design-kit/app-v3/`.

**Meta line** — `copilot-data.jsx:600-660`, the `ACTIVITY` fixture, is the spec:

| Row status | `meta` string                              |
| ---------- | ------------------------------------------ |
| `running`  | `4 apps · 7 steps · awaiting 1 approval`   |
| `done`     | `Sheets, Safe, Dune · 12 steps · balanced` |
| `done`     | `Docs · 5 steps · saved to Local files`    |
| `paused`   | `paused — needed your approval on a swap`  |
| `stopped`  | `stopped — you rejected 2 of 6 payouts`    |

Two shapes: a **counter triple** (`<apps> · <N> steps · <outcome>`) for healthy rows, and a
**prose reason** for interrupted ones. The counters are the invariant part; the outcome
clause is free text with no analogue in any persisted field. Rendered by `.lrow__sub`
(`copilot.css:1643-1648`) — `font-size: 11px`, `color: var(--mut2)` `#64646d`,
`margin-top: 1px`, `font-family: var(--mono)` — but the Activity call site **overrides the
family back to body** inline (`copilot-app.jsx:71-74`: `style={{fontFamily:"var(--body)"}}`).
Body font, 11px, `--mut2`. Live already matches family and colour (`Row.tsx:106-112`,
`--color-text-subtle` = `#64646d` = `--mut2`).

**Time** — `.lrow__time` (`copilot.css:1655-1659`): `font-family: var(--mono)`,
`font-size: 10.5px`, `color: var(--mut2)`, `flex: none`. Values are zero-padded 24-hour
wall clock: `11:44`, `09:02`, `08:15`, `18:30`, `14:07`, `11:20`, `16:44`, `10:03`.

**Navigation affordance** — `copilot-app.jsx:79`:

```jsx
{
  isLive ? <Icon.chevR /> : <span style={{ width: 16 }} />;
}
```

`.lrow > svg` (`copilot.css:1661-1666`): `width: 15px; height: 15px; flex: none;
color: var(--mut2)`. Every row reserves the trailing 16px; only the navigable one fills it.

**Hover** — `copilot.css:1601-1603`:

```css
.lrow:hover {
  background: var(--panel2);
}
```

`--panel2` = `#16161a` dark (`copilot.css:11`) / `#f6f6f8` light (`:75`) =
`--color-surface-muted` (`styles.css:171, 297`), exactly.

**Icon tile** — `.lrow__ic` (`copilot.css:1617-1626`): `28×28`, `border-radius: 7px`,
`display: grid; place-items: center`, `background: var(--panel3)` `#1d1d23`,
`color: var(--mut)` `#98989f`, `flex: none`. The live row overrides only the **tile's**
`color` to `var(--jade)` `#57c785` (`copilot-app.jsx:65-67`).

**Day divider** — `.act-day` (`copilot.css:1683-1697`): `font-family: var(--mono)`,
`font-size: 10px`, `color: var(--mut2)`, `margin: 18px 0 8px`, `display: flex`,
`align-items: center`, `gap: 10px`, plus `::after { flex: 1; height: 1px;
background: var(--line) }` — `--line` = `rgba(255,255,255,0.06)` = `--color-border`
(`styles.css:174`). **No `font-weight` (→ 400), no `letter-spacing`, no
`text-transform`.** Labels: `"Today"`, `"Yesterday"`, `"Mon, Jul 14"`.

Contrast `.sect-h` (`copilot.css:1563-1570`) — the _other_ treatment, the one we
accidentally shipped: `9.5px`, `letter-spacing: .12em`, `text-transform: uppercase`,
`margin: 22px 0 10px`. Two distinct roles; the live divider must stop claiming both.

**States** — `ActivitySurface` (`copilot-app.jsx:14-87`) maps `ACTIVITY` unconditionally.
No loading, error, unavailable, or empty branch. The design specifies exactly one state,
so four of ours are _unaudited_, not passing.

## Architectural decision

### D1 — The meta line is **server-projected integers on the run list**, composed into a string in `chat-surface`

**Seam: `RunHistoryEntry` (PRD-05's `GET /v1/agent/runs`) grows three counters; nothing else
changes shape.**

```python
class RunHistoryEntry(RuntimeContract):
    ...                                   # PRD-05's fields, unchanged
    connector_count: int | None = None    # DISTINCT connector_slug touched by the run
    step_count: int | None = None         # tool invocations recorded for the run
    pending_approval_count: int = 0       # approvals still awaiting a human
```

Semantics, each pinned to a real column:

| Component             | Means                                                                                                      | Source                                                                                                     |
| --------------------- | ---------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------- |
| `4 apps`              | distinct connectors the run actually called                                                                | `COUNT(DISTINCT connector_slug) FILTER (WHERE connector_slug IS NOT NULL)` over `runtime_tool_invocations` |
| `7 steps`             | tool invocations attributed to the run (one row per tool call, retries included, sub-agent calls included) | `COUNT(*)` over `runtime_tool_invocations`                                                                 |
| `awaiting 1 approval` | approvals blocking the run right now                                                                       | `COUNT(*) … WHERE status = 'pending'` over `runtime_approval_requests`                                     |

**`None` is not `0`.** `connector_count`/`step_count` are nullable so a run recorded before
the tool-invocation writer exists (D1b) reports _unknown_, and the client omits the clause
rather than asserting "0 steps" about a run that did seven. `pending_approval_count` is a
plain `int` because approvals have been persisted since `0001` — zero there is a fact.

**Integers on the wire, string in the shell.** `packages/api-types/src/activity.ts:63-70`
already states the rule for `started_at` ("never pre-formatted on the wire"); the same rule
governs here. The composer is one exported function in `packages/chat-surface`
(`destinations/activity/meta.ts`), consumed by the shared projector PRD-06 lands, so both
hosts produce byte-identical strings from one implementation:

```
formatActivityMeta({connector_count, step_count, pending_approval_count}) →
  join(" · ", [
    connector_count  != null && >0 ? `${n} ${n===1?"app":"apps"}`             : ∅,
    step_count       != null       ? `${n} ${n===1?"step":"steps"}`           : ∅,
    pending_approval_count > 0     ? `awaiting ${n} approval${n===1?"":"s"}`  : ∅,
  ])
```

Empty result → `Row.sub` is `undefined` → the line does not render (existing behaviour,
`ActivityDestination.tsx:519-522`). No "0 apps · 0 steps".

**Why this seam:**

| Rejected                                                         | Why                                                                                                                                                                                                                                                                                                                                        |
| ---------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Keep deriving from `/v1/audit` in the hosts (status quo)         | It is the defect. Audit rows key on `resource_id`, which is a conversation id or a server id, not a run id; the sole `tool_name` emitter (`runtime_worker/audit.py:194`) is dead code; the route needs `admin:audit_export`; and both hosts `.catch(() => [])`. Four independent breakages in one path, duplicated across two hosts.       |
| New composite `GET /v1/activity` in the facade                   | `services/backend-facade/CLAUDE.md` forbids product projection in the facade, and PRD-05 already rejected the same idea for the run list. A second endpoint that re-fetches the run list to decorate it also doubles the query cost and creates two definitions of "an Activity row".                                                      |
| Add `run_id` to the audit wire model and keep the client join    | `services/ai-backend/src/runtime_api/http/audit_list_routes.py` rejects unknown fields (`extra="forbid"`), so this is a real contract change — and even with it the client still needs an admin scope to read Activity, still gets names not counts, and still fans out a second request per page. Fixes the join, keeps the architecture. |
| Denormalise `steps_count`/`connectors_touched` onto `agent_runs` | Write amplification on a hot row plus a second source of truth that drifts whenever a write is lost. The aggregates run on indexes that already exist; there is nothing to buy.                                                                                                                                                            |
| Client-side derivation from the run's event stream               | Requires opening an SSE/replay stream per row — N streams for one list render — and the event log is a stream, not a ledger: one tool call emits several events, so any count is a `DISTINCT` over a JSON dig.                                                                                                                             |

**Query.** One grouped aggregate per page, not N+1 — the page's run ids are already in hand:

```sql
SELECT run_id,
       COUNT(*)                                                        AS step_count,
       COUNT(DISTINCT connector_slug) FILTER (WHERE connector_slug IS NOT NULL)
                                                                       AS connector_count
  FROM runtime_tool_invocations
 WHERE org_id = %(org_id)s AND run_id = ANY(%(run_ids)s)
 GROUP BY run_id
```

```sql
SELECT run_id, COUNT(*) AS pending
  FROM runtime_approval_requests
 WHERE org_id = %(org_id)s AND run_id = ANY(%(run_ids)s) AND status = 'pending'
 GROUP BY run_id
```

Both are covered by indexes that exist today —
`idx_runtime_tool_invocations_org_run_started (org_id, run_id, started_at)` (`0001:1041`)
and `idx_runtime_approval_requests_org_run_status (org_id, run_id, status)` (`0001:947`).
**No migration.** Both run under `_tenant_connection(org_id=…)` so the `tenant_isolation`
RLS policy binds; `org_id` comes from `scoped_identity`, never from a query parameter.
Authorization is the run list's own `runtime:use` scope — reading your own activity must
not require `admin:audit_export`, which is the deeper bug behind symptom 1.

### D1b — Activate the dormant `runtime_tool_invocations` write path

`connector_count` and `step_count` have **no truthful source today** (evidence rows 8-9).
The honest answer is that this needs a backend capability, and the capability is already
modelled: table, columns, CHECK constraints, index, and `ToolInvocationRecord` all exist
and nothing writes them.

- **Port.** Add `record_tool_invocation(record: ToolInvocationRecord) -> None` and
  `count_tool_invocations_for_runs(*, org_id, run_ids) -> Mapping[str, tuple[int, int]]`
  to `agent_runtime/api/ports.py`, implemented in all three adapters. The conformance
  harness (`services/ai-backend/tests/unit/runtime_adapters/test_store_conformance.py:38-56`)
  already parametrises `in_memory | file | postgres`, which is where "all three stay in
  sync" is enforced.
- **Write seam.** `runtime_worker/stream_tools.py:327` (`ledger.started`) inserts the row
  with `status=queued|running`; `:250` (`observed_settled`) updates `status`,
  `completed_at`, `result_summary`. One write pair at the seam that already owns tool-call
  lifecycle — not a new lifecycle.
- **Connector resolution.** Fill `ToolCallEntry.connector_slug`
  (`tool_call_ledger.py:41-43`, the stub the comment promises) from
  `McpDispatcherUnwrap.effective_server_name(payload)` (`dispatcher.py:75-97`) resolved
  through `McpServerRegistry.resolve_server(name)` (`capabilities/mcp/registry.py:98`).
  Native (non-MCP) tools resolve to `None` and are counted as steps, not apps — which is
  the design's own distinction between "apps" and "steps".
- **Backfill: none.** Historical runs report `None` and render without the clause. A
  fabricated backfill would put invented counts into the record surface.

`emit_tool_call_outcome` (`runtime_worker/audit.py:194`) stays dead in this PRD — it is an
audit-stream emitter, and this decision removes Activity's dependency on the audit stream
entirely. Deleting it is a separate cleanup.

### D1c — The error path is fixed by deleting the swallow site, not by adding a channel

Once the counters ride on the run list, Activity issues **one** request. The
`.catch(() => [])` at `activityApi.ts:211` and `destinationBinders.tsx:344` are deleted
along with the audit fan-out, and a 401/403/500 on the run list surfaces through the
existing `SectionResult{status:"error"}` → error state + Retry. Rejected alternative: add a
`degraded?: string[]` field to `SectionResult` (`packages/api-types/src/refs.ts:109-116`) so
the swallow could report itself — that is designing a better warning light for a request
this PRD removes. A regression test asserts the audit endpoint is not called
(DoD #12).

### D2 — `paused` is deleted from `ActivityRunStatus`, not added to `AgentRunStatus`

The design's own copy resolves this: its `paused` row reads _"paused — needed your approval
on a swap"_ (`copilot-data.jsx:625-630`). The design's fourth state **is**
`waiting_for_approval`. We already have that state, already map it
(`waiting_for_approval → needs_input`), and already render it. Adding a `paused` member to
`AgentRunStatus` would introduce a runtime state with no transition, no writer, and no
meaning distinct from one that exists — a CHECK-constraint change and an eight-way fold
widened to nine, to describe a state the runtime cannot enter.

So: **remove `"paused"` from `ACTIVITY_RUN_STATUSES`** (`packages/api-types/src/activity.ts:46-52`).
The taxonomy becomes exactly four values — `running | done | needs_input | stopped` — which
is precisely the design's four-state pill, and every value has a producer:

| `AgentRunStatus`                   | `ActivityRunStatus` | Design chip                               |
| ---------------------------------- | ------------------- | ----------------------------------------- |
| `queued`, `running`, `cancelling`  | `running`           | `chip--ok` + `.dotk`                      |
| `completed`                        | `done`              | `chip--ok`                                |
| `waiting_for_approval`             | `needs_input`       | `chip--warn` (the design's "paused" slot) |
| `cancelled`, `failed`, `timed_out` | `stopped`           | `chip--off`                               |

Two consequences, both required for the design's colours:

- `statusTone.ts:48` `needs_input` moves from `info` (accent) to `warning` (amber
  `--color-warning #e8b45e` = the design's `--amber`, `copilot.css:25`), matching
  `chip--warn` (`copilot.css:599-602`). The `paused` entry at `:47` stays — `statusTone`
  accepts arbitrary status strings from other surfaces and falls back safely (`:66-72`).
- `activityStatusLabel` (`ActivityDestination.tsx:86-98`) is **deleted**; the label comes
  from `statusTone(status).label`, which is already the declared SSOT and is already called
  two lines away (`:487`). One label source, one tone source.

`ACTIVITY_RUN_STATUSES` is a five-member `as const` today, so removing a member is a
compile-time-checked change: PRD-05's totality test (all eight `AgentRunStatus` map into
`ACTIVITY_RUN_STATUSES`) still passes, and any residual `case "paused":` fails
`tsc --noEmit` on exhaustiveness.

### D3 — Wall-clock time, via a second formatter beside the relative one

**Rule: a row shows a wall clock when its container already establishes the date; it shows
relative time when nothing else does.** Activity is day-grouped, so it is wall clock. Chats
is not day-grouped, so it keeps `formatRelativeTime`. This is the seam-level rule, not an
Activity special case — any future day-grouped list inherits it.

Add to `packages/chat-surface/src/util/time.ts` (which is already declared the SOLE home of
destination time formatting, `:1-9`):

```ts
export function formatClockTime(
  iso: string,
  locale?: string,
  timeZone?: string,
): string;
// Intl.DateTimeFormat(locale, { hour: "2-digit", minute: "2-digit", timeZone })
// → "11:44" (h23 locales) / "11:44 AM" (h12 locales); "—" on unparseable input,
//   matching formatRelativeTime's existing bad-data contract (time.ts:29).
```

`timeZone` is an explicit test seam, exactly as `now` is for `formatRelativeTime` — without
it the DoD's numeric pin is machine-dependent. Locale is honoured rather than forced to
`h23`: the design's `11:44` is what an h23 locale produces, and hard-coding a 24-hour clock
for a US user to match a mock would be worse than the drift it fixes. The column is
content-sized on both sides (`copilot.css:1655-1659` `flex:none` vs `Row.tsx:114-120`
`flex:"0 0 auto"`), so an `AM/PM` suffix costs width, not alignment.

This resolves the drift against `docs/plan/desktop-redesign/phase-4/PRD.md:113, 133` in the
design's favour. The `<time dateTime={row.started_at}>` wrapper
(`ActivityDestination.tsx:533-539`) stays — machine-readable exactness is unaffected by
display format.

### D4 — `Row` gets a `trailing` slot; Activity fills it

Add `readonly trailing?: ReactNode` to `RowProps` and render it after `meta` in
`Row.tsx:184-190`, inside a wrapper that **always reserves 16px**:

```tsx
<span style={trailingSlotStyle} data-testid="row-trailing">
  {trailing}
</span>
// trailingSlotStyle: { flex:"0 0 auto", width:16, display:"inline-flex",
//                      alignItems:"center", justifyContent:"flex-end",
//                      color:"var(--color-text-subtle)" }
```

The slot is reserved unconditionally (an empty slot is the design's
`<span style={{width:16}}/>`); the caller supplies the chevron only for navigable rows. The
audit's warning holds: shipping the chevron without the reservation ragged-edges the time
column on the seven non-navigable rows. Activity passes
`trailing={isRunning ? <Icon name="chevronRight" size={15}/> : undefined}` — first call site
of a glyph that has been in `paths.tsx:157` unused, byte-identical to the design's.

It goes on `Row`, not on Activity, because Chats, Tools and Skills all consume the same
primitive and all have the same "which of these can I click" problem.

### D5 — The icon tile gets its surface, and the tone reaches the tile

`Row.tsx:68-79` `iconSlotStyle` gains `background: var(--color-surface-elevated)`,
`borderRadius: 7`, `display: "grid"`, `placeItems: "center"` (7px is a literal — the design
is 7px and `--radius-md` is 8px; a one-off literal is honest where no token matches, and a
new `--radius-tile: 7px` token belongs to PRD-01 if it wants one).

`Row` gains `readonly iconTone?: "default" | "success"`, applied to the **slot's** `color`.
`ActivityDestination` passes `iconTone={isRunning ? "success" : "default"}` and
`liveIconStyle` (`ActivityDestination.tsx:675-680`) plus its wrapper span
(`:494-500`) are **deleted** — the wrapper is the bug, not a thing to restyle.

Token choice per the DISPUTED evidence row: `--color-surface-elevated` (`#1d1d23`), not
`--color-surface-muted` (`#16161a`), because the latter is the hover colour from D6 and the
tile must survive hover.

### D6 — Hover lives in a `.ui-list-row` recipe in **design-system**

Inline style objects cannot express `:hover`, so this forces real CSS. Two homes were
possible:

- **A new `row.css` in `packages/chat-surface`** — rejected. chat-surface stylesheets
  require an explicit per-file import in every host, and that seam has already produced
  asymmetry: desktop imports three of them in `apps/desktop/renderer/bootstrap.tsx:5,9,13`
  while web imports `onboarding.css` from two feature files
  (`apps/frontend/src/features/run/RunRoute.tsx:60`,
  `apps/frontend/src/features/onboarding/FirstRunSurfaceMount.tsx:76`) and none of the
  others. A new file is a new chance for one host to forget it.
- **A `.ui-list-row` recipe in `packages/design-system/src/styles.css`** — chosen.
  `styles.css` is imported exactly once per host at the entry point
  (`apps/frontend/src/app/App.tsx:5`, `apps/desktop/renderer/bootstrap.tsx:1`), it already
  owns every sibling recipe (`.ui-pill`, `.ui-chip`, `.ui-badge`), and design-system is the
  declared owner of primitives. Zero new import wiring; structurally impossible for one
  host to miss.

```css
.ui-list-row {
  cursor: default;
}
.ui-list-row[role="button"] {
  cursor: pointer;
}
.ui-list-row[role="button"]:hover,
.ui-list-row[role="button"]:focus-visible {
  background: var(--color-surface-muted);
}
```

Hover applies only to activatable rows — the design's `.lrow` is a `<button>` on every row
even where the click is a no-op (`copilot-app.jsx:52-55`: `onClick={() => (isLive ?
navigate("workspace") : null)}`), which is a mock inconsistency, not a spec. `Row` emits
`className="ui-list-row"` merged with any caller `className`, and `Row.tsx:156`'s inline
`cursor` moves into the recipe so one place decides. `:focus-visible` is added because the
row is keyboard-activatable (`Row.tsx:135-143`) and the design gives no focus treatment at
all.

### D7 — The divider stops being a section label

`ActivityDestination.tsx:451`: `className="act-day sect-h"` → `className="act-day"`.
`dayDividerStyle` (`:654-666`) drops `fontWeight: 600`, `letterSpacing: 0.4`,
`textTransform: "uppercase"` and takes `fontSize: var(--font-size-mono-10)` — the 10px mono
rung that already exists at `styles.css:71`, documented as "canonical small-mono pill
metadata … deliberately off the main ladder", and is exactly the design's `.act-day` size.
`fontWeight: var(--font-weight-regular)`, `letterSpacing: var(--tracking-normal)`
(`styles.css:88`). Colour (`--color-text-subtle` = `--mut2`), mono family, gap 10, and the
`::after`-equivalent hairline span (`:668-672`) are already correct.

Label format (`ActivityDestination.tsx:136-142`):
`{ weekday: "short", month: "short", day: "numeric" }` → `"Mon, Jul 14"`. Year is appended
**only** when the row falls in a previous calendar year (`rowYear !== nowYear`), so a
January user reading December still gets an unambiguous date without every divider carrying
a redundant "2026". `"Today"` / `"Yesterday"` are unchanged.

The `DaySkeleton` reuses `dayDividerStyle` (`:576`) and inherits the fix for free.

### D8 — The four undesigned states, specified

| State                      | Decision                                                                                                                                                                                                                                                                                                                                                                                                                                           |
| -------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `loading` (`:352-367`)     | **Keep as-is.** Two skeleton day-groups, `role="status" aria-busy`. The design has no loading state because it has no data layer; a live feed must have one.                                                                                                                                                                                                                                                                                       |
| `error` (`:370-384`)       | **Keep, and widen Retry.** `role="alert"` + `EmptyState` + Retry is right. The hole is that Retry exists _only_ here — a successfully-loaded but stale list has no refresh control. Move Retry into the page header region so it renders in `error`, `empty`, and `ready`. Nothing else changes.                                                                                                                                                   |
| `unavailable` (`:387-398`) | **Delete.** Unreachable — no binder constructs `status:"unavailable"` (`activityApi.ts:196-224`, `destinationBinders.tsx:322-348`) — and Activity is not a licensable capability that can be off for a workspace. `SectionResult.status` keeps the member for other surfaces; Activity stops branching on it and folds it into `error` (an unavailable Activity _is_ a failure to load). Deleting a wrong abstraction beats keeping a dead branch. |
| `empty` (`:401-410`)       | **Keep, fix the copy's truthfulness dependency.** PRD-05 makes this state honest by making finished runs reachable; until then it lies. Copy becomes "Nothing here yet — start a run and it'll show up, grouped by day." (drops the "The agent hasn't run anything yet" assertion, which the client cannot know).                                                                                                                                  |
| `ready`                    | The design's only state; D1-D7 apply.                                                                                                                                                                                                                                                                                                                                                                                                              |

## Scope

### `packages/api-types`

- `src/activity.ts` — remove `"paused"` from `ACTIVITY_RUN_STATUSES` (D2); document the
  four-value taxonomy and its total mapping from `AgentRunStatus`.
- `src/index.ts` — no change. `AgentRunStatus` (`:210-228`) is deliberately untouched (D2).

### `packages/design-system`

- `src/styles.css` — add the `.ui-list-row` recipe (D6). No token additions; `--font-size-mono-10`
  (`:71`), `--tracking-normal` (`:88`), `--color-surface-elevated` (`:201`),
  `--color-surface-muted` (`:171`) all already exist.

### `packages/chat-surface`

- `src/destinations/_shared/Row.tsx` — `trailing` slot + always-reserved 16px (D4);
  `iconTone` prop; `iconSlotStyle` background/radius/grid (D5); emit
  `className="ui-list-row"` and move `cursor` out of the inline object (D6).
- `src/destinations/_shared/Row.test.tsx` — new: trailing renders, slot reserved when
  empty, `iconTone="success"` tints the slot not a child, `ui-list-row` present.
- `src/util/time.ts` — add `formatClockTime` (D3).
- `src/util/time.test.ts` — pin the design value under a fixed locale + timeZone.
- `src/destinations/activity/meta.ts` — **new**: `formatActivityMeta` (D1), the single
  string composer both hosts use.
- `src/destinations/activity/meta.test.ts` — **new**: pluralisation, omission on `null`,
  the design's exact string from the design's exact counts.
- `src/destinations/activity/ActivityDestination.tsx` — delete `activityStatusLabel`
  (`:86-98`) in favour of `statusTone().label` (D2); `dayLabel` weekday/year (D7);
  `dayDividerStyle` weight/tracking/transform/size (D7); `className="act-day"` (`:451`, D7);
  `formatClockTime` at `:538` (D3); `trailing` chevron + `iconTone` (D4/D5); delete
  `liveIconStyle` (`:675-680`) and its wrapper span (`:494-500`) (D5); delete the
  `unavailable` branch (`:387-398`), move Retry out of the error branch, retune empty copy (D8).
- `src/destinations/activity/ActivityDestination.test.tsx` — update + add the regression
  guards in DoD.
- `src/shell/statusTone.ts` — `needs_input` → `warning` (D2).

### `services/ai-backend`

- `src/runtime_api/schemas/runs.py` — three counter fields on `RunHistoryEntry` (D1).
- `src/agent_runtime/api/ports.py` — `record_tool_invocation`,
  `count_tool_invocations_for_runs`, `count_pending_approvals_for_runs` (D1/D1b).
- `src/agent_runtime/api/conversation_query_service.py` — attach counters to the run-list
  page in two grouped queries (D1).
- `src/runtime_adapters/{postgres,file,in_memory}/runtime_api_store.py` — implement the
  three port methods (D1/D1b).
- `src/runtime_worker/stream_tools.py` — write the invocation row at `:327` / `:250` (D1b).
- `src/runtime_worker/tool_call_ledger.py` — populate `connector_slug` (`:41-43`) (D1b).
- `tests/unit/runtime_adapters/test_store_conformance.py` — extend for the new port methods.
- `tests/unit/runtime_api/…` — counter projection + `pending_approval_count` semantics.
- **No migration.** Tables and indexes exist (`0001:586-607, 947, 1039-1041`).

### `services/backend-facade`

- No change. The facade proxies `GET /v1/agent/runs` verbatim (PRD-05); wider fields need no
  facade edit.

### `apps/frontend`

- `src/features/activity/api/activityApi.ts` — delete `auditLabel`, `buildMetaIndex`, the
  `listAuditEvents` call, and the `.catch(() => [])` (`:211`) (D1c). What remains folds into
  PRD-06's shared projector.
- `src/features/activity/ActivityRoute.tsx` / `.test.tsx` — drop audit mocking; assert the
  audit endpoint is never called.

### `apps/desktop`

- `renderer/destinationBinders.tsx` — delete the duplicated `auditLabel`/`buildMetaIndex`
  (`:260-290`), the `/v1/audit` request (`:337-344`) and its `.catch` (D1c).
- `renderer/destinationBinders.test.tsx` — same assertions as web, from the same fixtures.

### `tools/design-parity`

- `surfaces/activity/anchors.json` — bind `row.live.chevron` → `[data-status="running"]
[data-testid="row-trailing"] svg` and `row.done.spacer` → the non-running row's
  `[data-testid="row-trailing"]`; both are `live: null` today.
- `lib/render-live-activity.test.tsx` — fixture carries the counter fields.

## Non-goals

- **The status-chip recipe.** `StatusPill`'s filled/uppercase/sans chip vs the design's
  hairline mono outline is 12 of the surface's 20 measured HIGH rows and belongs to the chip
  PRD (`AUDIT.md` R1, and PRD-09 names it PRD-02). This PRD changes `needs_input`'s **tone
  token** and the **label source** only — not the chip's geometry, family, weight, casing,
  or fill.
- **The global type scale.** `--font-size-2xs` collapsing the design's 10px and 10.5px rungs
  is PRD-01. This PRD adopts the existing `--font-size-mono-10` for the divider and touches
  no token definition.
- **Run history, per-run rows, `started_at` correctness, ordering.** PRD-05. This PRD reads
  what that endpoint serves.
- **Run titles and click-through routing.** PRD-04.
- **Moving the projection into `packages/chat-surface` and cutting the binders over.**
  PRD-06 owns the shared projector; this PRD supplies `formatActivityMeta` for it to call
  and deletes the audit halves it is replacing.
- **The topbar subtitle** ("every action the agent has taken") — a shell-registry gap
  affecting all six destinations (`AUDIT.md` HIGH-4).
- **The outcome clause** — "balanced", "saved to Local files", "3 labeled, 1 escalated",
  "you rejected 2 of 6 payouts". No persisted field carries it, and no field is one query
  away. Inventing prose on the surface whose job is to be the record would be the exact
  failure this program exists to fix. Explicitly out; revisit only with a real run-summary
  capability.
- **Filter, search, sort, date range, pagination controls, per-row actions, auto-refresh.**
  The design has none and live has none. Parity holds; leave it alone. (Retry's placement
  change in D8 is a repair to an existing control, not a new one.)
- **Deleting `emit_tool_call_outcome`** (`runtime_worker/audit.py:194`) — dead, but it is an
  audit-stream concern, and this PRD removes Activity's dependency on that stream rather
  than editing it.
- **Backfilling counters for historical runs.** D1b: `None` renders as absent.

## Risks & rollback

| Risk                                                                                                                                                               | Guard                                                                                                                                                                                                                                                                           | Rollback                                                                                                                          |
| ------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------- |
| `Row` changes hit four destinations at once (Activity, Chats `ChatsArchive.tsx`, Tools, Skills) — an unreserved-but-now-16px trailing column shifts every list row | `packages/chat-surface/src/destinations/_shared/Row.test.tsx` + each destination's existing suite; the parity harness covers Chats and Tools independently                                                                                                                      | The `trailing` slot is additive and defaults to empty; reverting the `Row.tsx` diff restores byte-identical layout.               |
| The tile background lands on rows whose icon is a full-bleed logo (Tools connector marks), boxing a logo that was borderless                                       | Tools parity report re-run; `iconTone`/background are opt-outable per call site if a real conflict appears                                                                                                                                                                      | Revert `iconSlotStyle`; independent of D4/D6.                                                                                     |
| Removing `"paused"` from `ACTIVITY_RUN_STATUSES` breaks an unknown consumer                                                                                        | It is an `as const` tuple + derived union: `npm run typecheck --workspace @0x-copilot/api-types` and every consuming workspace fail at compile time, not at runtime. `statusTone`'s `paused` entry stays, and unknown strings already fall back safely (`statusTone.ts:66-72`). | One-line revert of the tuple.                                                                                                     |
| The tool-invocation writer adds two DB writes per tool call on a hot path                                                                                          | Writes are fire-and-forget at the existing ledger seam and must not fail the run: wrap in the same defensive posture as other worker persistence. Load-check against `services/ai-backend` run tests.                                                                           | Feature-flag the writer off at the seam; counters revert to `None` and the clause disappears — the client already handles `None`. |
| Two grouped queries per Activity page regress list latency                                                                                                         | Both hit existing covering indexes and are bounded by the page's `limit ≤ 200` run ids                                                                                                                                                                                          | Return `None` counters (skip the aggregates) without a contract change.                                                           |
| Wall-clock time in a non-h23 locale widens the time column                                                                                                         | `.lrow__time`/`row-meta` are content-sized on both sides (`copilot.css:1655-1659`; `Row.tsx:114-120`) — verified, not assumed                                                                                                                                                   | `formatClockTime` is a one-line swap back to `formatRelativeTime` at `ActivityDestination.tsx:538`.                               |
| Deleting the `unavailable` branch removes a state some future binder wants                                                                                         | `SectionResult.status` keeps the member (`api-types/src/refs.ts:110`); only Activity's branch goes                                                                                                                                                                              | Re-add the branch; it is 12 lines.                                                                                                |
| Existing Activity tests assert relative time, Title-Case labels, and the audit fetch                                                                               | `packages/chat-surface/src/destinations/activity/ActivityDestination.test.tsx:368-372`; `apps/frontend/src/features/activity/ActivityRoute.test.tsx:320, 372, 394`; `apps/desktop/renderer/destinationBinders.test.tsx` — all in scope and updated deliberately, not deleted    | —                                                                                                                                 |

## Definition of Done

1. `packages/api-types/src/activity.ts` `ACTIVITY_RUN_STATUSES` has exactly four members
   `["running","done","needs_input","stopped"]`, and
   `npm run typecheck --workspace @0x-copilot/api-types` passes.
2. `grep -rn '"paused"' packages/chat-surface/src/destinations/activity packages/api-types/src/activity.ts`
   returns no hits, and `npm run typecheck --workspace @0x-copilot/frontend` passes
   (exhaustive-switch proof that no `case "paused"` survives).
3. `packages/chat-surface/src/destinations/activity/meta.test.ts` asserts
   `formatActivityMeta({connector_count:4, step_count:7, pending_approval_count:1})` ===
   `"4 apps · 7 steps · awaiting 1 approval"` — the design's literal string from
   `design-kit/app-v3/copilot-data.jsx:606`.
4. The same file asserts `formatActivityMeta({connector_count:null, step_count:null,
pending_approval_count:0})` === `""`, and that `ActivityDestination` renders **no**
   `[data-testid="row-sub"]` node for such a row (never "0 apps · 0 steps").
5. `packages/chat-surface/src/util/time.test.ts` asserts
   `formatClockTime("2026-07-22T11:44:00Z","en-GB","UTC")` === `"11:44"` — pinning the
   design's literal time value (`copilot-data.jsx:607`) — and that unparseable input returns
   `"—"`.
6. `packages/chat-surface/src/destinations/activity/ActivityDestination.test.tsx` asserts
   `[data-testid="activity-row-time"]` has text content `"11:44"` (not matching
   `/ago|just now/`) for a row whose `started_at` is that instant under a pinned locale +
   timeZone. **Regression guard for the redundant-relative-time bug.**
7. The same file asserts a `running` row renders
   `[data-status="running"] [data-testid="row-trailing"] svg` and a `done` row renders
   `[data-testid="row-trailing"]` **with no child**, and that
   `getComputedStyle(trailing).width` is `"16px"` on both. **Regression guard for the
   ragged-time-column risk.**
8. The same file asserts the day divider for an explicit date renders exactly `"Mon, Jul 14"`
   (locale `en-GB`, pinned `now`), that its `className` is `"act-day"` with no `sect-h`, and
   that its computed `fontWeight` is `400`, `textTransform` is `"none"`, `letterSpacing` is
   `"normal"`, `fontSize` is `"10px"`. **Pins the design values from `copilot.css:1683-1691`.**
9. The same file asserts a row one calendar year older renders a divider containing the year,
   and a row in the current year does not.
10. `packages/chat-surface/src/destinations/_shared/Row.test.tsx` asserts the row element
    carries `className` containing `ui-list-row`, and that
    `packages/design-system/src/styles.css` contains a
    `.ui-list-row[role="button"]:hover` rule whose `background` is
    `var(--color-surface-muted)`.
11. The same file asserts `iconTone="success"` sets `color: var(--color-success)` on
    `[data-testid="row-icon"]` **itself** (not a descendant), that the slot's `background` is
    `var(--color-surface-elevated)` and `border-radius` is `7px`, and
    `grep -n "liveIconStyle" packages/chat-surface/src/destinations/activity/ActivityDestination.tsx`
    returns nothing.
12. `apps/frontend/src/features/activity/ActivityRoute.test.tsx` and
    `apps/desktop/renderer/destinationBinders.test.tsx` each assert that loading Activity
    issues **zero** requests to `/v1/audit`, and
    `grep -rn "catch(() => \[\] as AuditEvent\[\])" apps/frontend/src/features/activity apps/desktop/renderer`
    returns nothing. **Regression guard for the silently-swallowed 403.**
13. `apps/desktop/renderer/destinationBinders.test.tsx` asserts that a run-list response with
    `connector_count: 4, step_count: 7, pending_approval_count: 1` renders the row sub-line
    `"4 apps · 7 steps · awaiting 1 approval"`, and the identical assertion exists in the web
    route test from the identical fixture — proving one composer, two hosts.
14. `cd services/ai-backend && .venv/bin/python -m pytest tests/unit/runtime_adapters/test_store_conformance.py`
    passes with `record_tool_invocation` + `count_tool_invocations_for_runs` +
    `count_pending_approvals_for_runs` exercised across all three adapter params
    (`in_memory`, `file`, `postgres`).
15. `cd services/ai-backend && .venv/bin/python -m pytest tests/unit/runtime_api` passes,
    including a test that a run with 7 tool invocations across 4 distinct
    `connector_slug`s and 1 `pending` approval projects
    `step_count=7, connector_count=4, pending_approval_count=1`, and that a run with **no**
    tool-invocation rows projects `step_count=None, connector_count=None` (never `0`).
16. `cd services/ai-backend && .venv/bin/python -m pytest tests/unit/runtime_worker` passes,
    including a test that a completed MCP tool call writes one
    `runtime_tool_invocations` row whose `connector_slug` equals the resolved MCP server
    slug, and a native tool call writes one row with `connector_slug=None`.
17. `git diff --stat services/ai-backend/migrations/` is empty — the aggregates use
    `idx_runtime_tool_invocations_org_run_started` and
    `idx_runtime_approval_requests_org_run_status`, which already exist.
18. `grep -n 'data-testid="activity-unavailable"' packages/chat-surface/src/destinations/activity/ActivityDestination.tsx`
    returns nothing, and a test asserts `status:"unavailable"` renders the **error** branch
    with a working Retry.
19. A test asserts the Retry control is present in the `ready` state (not only in `error`).
20. The design-parity report for `activity` shows **0 HIGH rows** for anchor group `Row/live`
    on `row.live.ic` and **no `missing-in-live` rows** for `row.live.chevron` /
    `row.done.spacer`, and **0 MEDIUM** on `day.head` for `fontSize` / `fontWeight` /
    `letterSpacing` / `textTransform` (re-run per
    `/Users/parthpahwa/Documents/work/enterprise-search/.claude/worktrees/adoring-rosalind-939b76/tools/design-parity/SKILL.md`,
    after updating `surfaces/activity/anchors.json` to bind the two previously-`null` anchors).
21. `node_modules/.bin/vitest run --config tools/design-parity/vitest.config.mjs` passes
    (the live-render harness still renders the real component).
22. `npm run typecheck --workspace @0x-copilot/frontend`, `--workspace @0x-copilot/desktop`,
    and the `chat-surface` + `design-system` suites all pass.

## Dependencies

**Must land first:**

- **PRD-05 (run history)** — hard blocker. This PRD adds three fields to `RunHistoryEntry`
  and reads a run list that PRD-05 creates. It also supersedes PRD-05's forward reference to
  a facade-side `GET /v1/activity` composite for the meta counters (PRD-05 §Non-goals,
  "PRD-07"): the counters ride on `GET /v1/agent/runs`, for the reason PRD-05 itself gives —
  the facade must not own product projection.
- **PRD-04 (run identity)** — soft. Without it every row title is "Run", so the meta line and
  chevron land on rows the user still cannot identify. Not a code conflict.
- **PRD-06 (shared Activity projection in `chat-surface`)** — coordinate. PRD-06 owns the
  single projector and the binder cut-over; this PRD supplies `formatActivityMeta` for it to
  call and deletes the audit halves (`activityApi.ts:76-125, 211`;
  `destinationBinders.tsx:260-290, 337-344`) that PRD-06 is replacing. **Land PRD-06 first
  or in the same batch** — the deletions are the same lines.
- **PRD-01 (tokens)** — soft. Consumes `--font-size-mono-10`, `--tracking-normal`,
  `--color-surface-elevated`, `--color-surface-muted`, all of which exist on `main` today.
  Nothing here blocks on PRD-01, and nothing here defines a token.

**Explicitly not a dependency:** the chip PRD (PRD-02). D2's `needs_input → warning` tone
change is a one-line edit in `statusTone.ts` and is correct under both the current
`StatusPill` and its replacement.

**This PRD unblocks:**

- The Activity surface's parity close-out — after this, the remaining measured HIGHs on
  `activity` are the chip cluster (PRD-02) and the topbar subtitle (shell registry).
- A truthful per-run tool ledger. D1b makes `runtime_tool_invocations` live, which is the
  prerequisite for per-run cost/step attribution, the compliance answer to "what did this run
  actually touch", and the deletion of the dead
  `runtime_worker/audit.py:194` emitter.
- The `Row` trailing slot and `.ui-list-row` recipe are consumed by Chats, Tools, and Skills
  for the same "which rows are navigable" defect.
