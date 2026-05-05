# PR 4.5 — Usage overlay refit (per-conversation + workspace 30-day) + Share popover

> **Status:** Draft (PRD + Spec + Architecture)
> **Plan reference:** Wave 4, PR 4.5 in [`/Users/parthpahwa/.claude/plans/fetch-this-design-file-resilient-pumpkin.md`](../../../.claude/plans/fetch-this-design-file-resilient-pumpkin.md)
> **Owner:** frontend (UsagePanel refit + workspace chart + Share popover) · ai-backend (zero new endpoints — every read off existing surfaces) · design-system (`<Popover>` primitive — Radix Popover wrapper)
> **Size:** **M.** Pure FE composition for usage. Sharing is **stub-only** in v1 — copy-link works through the existing clipboard path; persistence (share tokens, recipient ACLs, fork lineage) is Wave 6. The popover ships the UI shell so the topbar feels complete, with grey-disabled rows for not-yet-wired actions.
> **Depends on:** Existing `GET /v1/usage/me`, `/v1/usage/conversations/{id}`, `/v1/usage/org`, `/v1/budgets/me` (✅ all shipped) · existing `UsagePanel.tsx` (refit, not replace) · PR 2.1 topbar usage meter (the trigger)
> **Reads alongside:** [`pr-2.1-topbar-chrome-thinking-depth.md`](pr-2.1-topbar-chrome-thinking-depth.md), [`pr-1.2-per-chat-connector-scope.md`](pr-1-2-per-chat-connector-scope.md) (the source-ACL hint Wave 6 will rely on), [`apps/frontend/CLAUDE.md`](../../apps/frontend/CLAUDE.md), [`packages/design-system/CLAUDE.md`](../../packages/design-system/CLAUDE.md)
> **Sibling docs (Wave 4):** [`pr-4.1-settings-you-group.md`](pr-4.1-settings-you-group.md) · [`pr-4.2-settings-workspace-group.md`](pr-4.2-settings-workspace-group.md) · [`pr-4.3-settings-ai-and-data.md`](pr-4.3-settings-ai-and-data.md) · [`pr-4.4-mcp-overlay-test-connection.md`](pr-4.4-mcp-overlay-test-connection.md)

---

## 0 · TL;DR

Two surfaces, both small.

**1. Usage overlay refit.** Today's `UsagePanel.tsx` is conversation-only and table-only. We add a tab switch (`This conversation` | `Workspace`) and embed a 30-day stacked area chart driven by `recharts`. Every byte of data already lives in `usage_daily_rollups` (migration 0007); we just consume it.

**2. Share popover.** The existing topbar share button copies the URL to clipboard. We replace the click handler with a popover containing: copy link (works), share to Slack (deep-link), share to email (mailto), view-access radio (UI shell only — persistence is Wave 6), sources-visible-to-viewer toggle (UI shell only). The popover ships now so the design feels complete; sharing semantics arrive in Wave 6 and re-use the same shell.

| Surface          | New                                                                               | Reuses                                                                                                               |
| ---------------- | --------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------- |
| Usage panel tabs | `<UsageWorkspaceChart>` + a tab switch + `<UsageTopUsers>` table                  | Existing `UsagePanel` table primitives, `getMyUsage()`, `getMyTopConversations()`, `getOrgUsage()`, `getMyBudgets()` |
| Share popover    | `<SharePopover>` UI + `<Popover>` design-system primitive (Radix Popover wrapper) | Existing `onShare` clipboard path; `mailto:` and `slack://` links                                                    |

LoC estimate: frontend ≈ 480 (tab refit + chart + workspace tab + share popover + 2 hooks) · design-system ≈ 60 (`<Popover>` wrapper) · ai-backend ≈ 0 · backend ≈ 0 · api-types ≈ 0 (existing `UsageOrgResponse`, `UsageMeResponse`, `BudgetSummary` shapes already exported and documented).

---

## 1 · PRD

### 1.1 Problem

#### Usage overlay

Today's `UsagePanel.tsx` (`apps/frontend/src/features/chat/components/details/UsagePanel.tsx`) does the per-conversation view well but stops there. The Atlas design doc requires:

> **Two views**
>
> - **This conversation** (default) — token breakdown by message, context window utilization, cost-to-date, model used.
> - **Workspace** — past 30 days of usage as a stacked area chart by user, with seat count and plan-limit overlay.

The data exists. `GET /v1/usage/me?period=30d` returns `by_day[]`. `GET /v1/usage/org?period=30d` returns `by_user[]` and `by_day[]`. `GET /v1/budgets/me` returns plan limits. The FE just doesn't render a chart yet (`UsagePanel` lines 182-269 use only tables).

We also need a chart library. The two contenders are `recharts` (simpler, declarative, ~50 KB gzipped) and `@visx/visx` (modular, smaller per-chart but more wiring). The Atlas surface needs one chart; `recharts` is the right choice. We accept the bundle hit.

#### Share popover

Today's `ChatScreen.tsx:706-717` `onShare()` writes `window.location.href` to the clipboard. The design wants:

> Topbar Share button opens a popover with: copy link, share to Slack, share to email, "View access" radio (anyone in workspace / specific people), "Sources visible to viewer" toggle.

The persistence story for "view access" + "sources visible" is Wave 6 (`conversation_shares` schema). v1 ships the popover **shell** with copy-link working and the rest grey-disabled with a tooltip "Coming with sharing v2." When Wave 6 lands the persistence, the same popover wires through.

### 1.2 Goals

1. **Usage panel — two tabs.** "This conversation" (existing tables) + "Workspace" (new chart + top-users table). Tab persists in component state for the panel session; doesn't survive navigation.
2. **30-day stacked area chart by user** rendered with `recharts`. Top-N users get distinct colours; the rest collapse into "Other." Plan-limit overlay as a horizontal threshold line. Period selector (today / 7d / 30d / month) reuses the existing one in `UsagePanel`.
3. **Top users table** under the chart for the same period — drill-in sortable.
4. **Share popover** mounting from the topbar share button. Copy-link works; Slack and email use deep-links (no API call); view-access + sources-visible toggles render but are no-op + tooltip in v1.
5. **`<Popover>` primitive** added to `@enterprise-search/design-system` wrapping `@radix-ui/react-popover`. Same Radix family as PR 4.4's `<Dialog>` and PR 4.2's `<DropdownMenu>`.
6. **Streaming and runtime untouched.** No new event types, no schema change.

### 1.3 Non-goals

- **Per-connector token attribution.** Listed under PR 7.2 in the wave plan. ai-backend doesn't carry `connector_id` on `runtime_model_call_usage` yet; surfacing this here without the column would mean fake data. We omit until 7.2 lands.
- **Sharing persistence.** `conversation_shares`, `conversation_share_recipients`, fork lineage — Wave 6.
- **Real Slack share** (link unfurl, channel picker, send-as-bot). Wave 6 + Slack OAuth setup. v1 uses `slack://` URL scheme for native deep-link — opens Slack with the prefilled message.
- **Real email send.** v1 uses `mailto:` — opens the user's mail client with prefilled subject/body. The design's "share to email" can be honestly delivered with mailto without backend wiring.
- **Forecast overage warnings.** Design "later" pill.
- **Per-conversation token attribution charts.** v1 keeps the per-conversation view as tables (existing).
- **Usage CSV export.** Not in the design.
- **Recipient view of shared thread + fork mechanics.** Wave 6 owns these (PRs 6.1, 6.2).
- **Topbar share button itself.** PR 2.1 ships the button; this PR replaces its click handler.

### 1.4 Success criteria

- ✅ `<UsagePanel>` renders two tabs: "This conversation" (existing) and "Workspace" (new).
- ✅ Workspace tab renders a 30-day stacked area chart driven by `GET /v1/usage/org?period=30d` `by_day` data.
- ✅ Plan-limit overlay (horizontal threshold line) sourced from `GET /v1/budgets/me`. When `org` budget is unset, the line is omitted.
- ✅ Top users table under the chart, sortable by tokens / cost, uses the org's `by_user` array.
- ✅ Period selector (today / 7d / 30d / month) refetches both me and org endpoints; the chart re-renders without flicker.
- ✅ `<Popover>` primitive added to design-system; `<SharePopover>` mounts off the topbar share button.
- ✅ Copy link works (existing clipboard path).
- ✅ Slack share opens `slack://share?url=…&text=…`; email share opens `mailto:?subject=…&body=…`.
- ✅ View-access radio + sources-visible toggle render disabled with tooltip "Sharing settings ship with v2."
- ✅ `recharts` adds <60 KB gzipped to the chat bundle (verify via `npm run build` + `dist/assets`).
- ✅ Streaming handshake byte-identical pre/post merge. `make test` green; frontend typecheck + build green.

### 1.5 User stories

| #    | Persona             | Story                                                                                                                                                                                 |
| ---- | ------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| US-1 | Sarah               | I click the topbar usage meter. The "This conversation" view shows my breakdown. I switch to "Workspace" — a 30-day stacked chart shows the team's usage with my segment highlighted. |
| US-2 | Marcus (admin)      | I hit "Workspace" and see the plan-limit overlay at $1000/month. We're at $860. I order it by cost; my top user is Priya at $310.                                                     |
| US-3 | Sarah               | I click "Share" in the topbar. The popover offers Copy link / Slack / Email / View access / Sources visible. I click Copy — the URL goes to clipboard, "Copied" tooltip flashes.      |
| US-4 | Sarah               | I click "Share to Slack". My Slack desktop opens with the link pre-filled in a message draft.                                                                                         |
| US-5 | Sarah               | I click "Share to email". My mail client opens with subject "FY26 Q1 launch announcement draft" (chat title) + body containing the link.                                              |
| US-6 | Sarah (curious)     | I see the "View access" radio: "Anyone in workspace" / "Specific people". Both disabled. Hover tooltip: "Sharing settings ship with v2." OK, makes sense.                             |
| US-7 | Marcus (compliance) | I want to know my training-opt-out is honoured for the 30-day usage. I scroll the chart's tooltip — the rows include a "training: off" badge if applicable. (Future polish; v1 skip.) |

---

## 2 · Spec

### 2.1 Wire — usage (no changes)

Every call already exists:

- `GET /v1/usage/me?period={today|7d|30d|month}` — returns `UsageMeResponse` with `by_day[]`, `by_model[]`, `total`.
- `GET /v1/usage/me/conversations?period=…&limit=10` — top conversations (already used by `UsagePanel`).
- `GET /v1/usage/conversations/{id}?period=…` — per-conversation totals (already used by per-conversation view).
- `GET /v1/usage/org?period=…` — admin/auditor; returns `UsageOrgResponse` with `by_day[]`, `by_model[]`, `by_user[]`. **This is the workspace chart's data source.**
- `GET /v1/budgets/me` — returns active budgets (org + user) with `limit_micro_usd`, `current_spend_micro_usd`.

The FE just consumes them.

### 2.2 Wire — share (no API; deep links only in v1)

- **Copy link** — `navigator.clipboard.writeText(window.location.href)`. Existing.
- **Slack** — `window.open('slack://share?url=' + encodeURIComponent(url) + '&text=' + encodeURIComponent(text))`. The Slack URL scheme is documented and registered with the OS by the Slack desktop client.
- **Email** — `window.location.href = 'mailto:?subject=' + encodeURIComponent(subject) + '&body=' + encodeURIComponent(body)`. Universal.

When Wave 6 lands the share-create endpoint, the popover swaps the deep-link handlers for an API call that mints a token; the UI shape doesn't change.

### 2.3 Persistence

**No new schema.** This PR is pure FE composition for usage and pure UI shell for share. `recharts` is added as an `apps/frontend` dependency.

### 2.4 Audit

**No new audit actions** in this PR. When sharing actually persists in Wave 6, that PR adds the audit rows (`share.create`, `share.access`, `share.revoke`).

The deep-link share is a client-side action with no server interaction; we don't audit it (the URL is already in the user's clipboard if they want it; copy is not a privileged action).

### 2.5 Permissions

| Caller                  | Read me/usage | Read org/usage | Read me/budgets | Copy share link | Slack/email deep-link | View-access toggle |
| ----------------------- | ------------- | -------------- | --------------- | --------------- | --------------------- | ------------------ |
| Workspace member        | ✅            | ❌ (admin)     | ✅              | ✅              | ✅                    | grey (v1)          |
| Workspace admin         | ✅            | ✅             | ✅              | ✅              | ✅                    | grey (v1)          |
| Auditor (existing role) | ✅            | ✅             | ✅              | ❌              | ❌                    | n/a                |

The existing endpoint guards (admin / auditor for org-usage) stay as-is. The Workspace tab in the usage panel **conditionally renders**: members see "Workspace usage is admin-only" in place of the chart; admins/auditors see the chart.

### 2.6 Error semantics

| Condition                                     | Behaviour                                                                              |
| --------------------------------------------- | -------------------------------------------------------------------------------------- |
| `GET /v1/usage/org` returns 403 for a member  | Workspace tab body shows the "admin-only" empty state with link to learn more.         |
| `GET /v1/budgets/me` returns no budgets       | Plan-limit overlay omitted; chart renders normally.                                    |
| `GET /v1/usage/org` returns empty `by_day`    | Chart renders empty (zero stacks); empty state copy "No usage in the last 30 days."    |
| Slack deep-link not registered (no Slack app) | Browser falls back to its handler-not-found UI; we don't override.                     |
| Mailto blocked by browser                     | Same fallback.                                                                         |
| Clipboard write blocked (Permissions API)     | Existing fallback in `onShare`: status message "Copy this page URL to share the chat." |

### 2.7 Frontend contract (`@enterprise-search/api-types`)

**No new types in this PR.** Existing types are sufficient:

```ts
// already exported (verified in the FE inventory):
//   UsageMeResponse, UsageOrgResponse, UsageDailyRow, UsageModelRow,
//   UsageConversationRow, UsageRunRow, ConversationUsageResponse, BudgetSummary
```

We may add type narrowings at the FE for chart-specific shapes (`{ day: string; users: Record<string, number> }`) but those live as private `apps/frontend` types, not in the shared package.

### 2.8 Frontend wiring — usage refit

```
apps/frontend/src/features/chat/components/details/
├── UsagePanel.tsx                   (existing — refit with tab switch)
├── usage/
│   ├── UsageConversationView.tsx    (existing per-conversation tables, lifted out of UsagePanel for clarity)
│   ├── UsageWorkspaceView.tsx       (NEW — chart + top-users table)
│   ├── UsageWorkspaceChart.tsx      (NEW — recharts AreaChart, ~120 LOC)
│   ├── UsageTopUsersTable.tsx       (NEW — sortable table, ~80 LOC)
│   └── usagePalette.ts              (NEW — colour assignment for top-N users)
```

**Tab switch** lives in `UsagePanel.tsx`, just under the period selector. Two pills, controlled component, default `conversation`.

**Chart specifics:**

```tsx
// UsageWorkspaceChart.tsx — sketch
import {
  AreaChart,
  Area,
  XAxis,
  YAxis,
  Tooltip,
  Legend,
  ReferenceLine,
  ResponsiveContainer,
} from "recharts";

export function UsageWorkspaceChart({
  orgUsage,
  budget,
}: {
  orgUsage: UsageOrgResponse;
  budget: BudgetSummary | null;
}) {
  const data = pivotByDayByUser(orgUsage); // ~40 LOC, returns [{ day: '2026-04-15', priya: 100, marcus: 50, …, other: 30 }, …]
  const topUsers = pickTopUsers(orgUsage, 6); // returns the 6 users with highest cost; rest fold into 'Other'
  const colors = usagePalette(topUsers); // assigns from the design-system accent ramp

  return (
    <ResponsiveContainer width="100%" height={300}>
      <AreaChart data={data} stackOffset="none">
        <XAxis dataKey="day" />
        <YAxis />
        <Tooltip />
        <Legend />
        {topUsers.map((u) => (
          <Area
            key={u.user_id}
            type="monotone"
            dataKey={u.user_id}
            stackId="1"
            stroke={colors[u.user_id]}
            fill={colors[u.user_id]}
          />
        ))}
        <Area
          type="monotone"
          dataKey="other"
          stackId="1"
          stroke={colors.other}
          fill={colors.other}
        />
        {budget?.limit_micro_usd && (
          <ReferenceLine
            y={budget.limit_micro_usd / 1_000_000}
            stroke="var(--color-warn)"
            strokeDasharray="3 3"
            label="Plan limit"
          />
        )}
      </AreaChart>
    </ResponsiveContainer>
  );
}
```

`recharts` is a 50 KB gzipped lib that we accept for one chart. Alternative considered: rolling our own SVG with `d3-shape`'s `stack()` (~5 KB but ~250 LOC of axes / tooltip / legend). For one chart, `recharts` wins on labour; for five charts, we'd reconsider.

**Top-users table** is a normal sortable table (existing patterns; no virtualization required at <100 rows).

The chart is the same component embedded inside PR 4.2's Billing card — one component, two consumers.

### 2.9 Frontend wiring — share popover

```
apps/frontend/src/features/share/
├── SharePopover.tsx
└── useShareLinkText.ts             (composes title + URL into share copy)
```

**Mounting:** the topbar share button (PR 2.1) accepts an optional `slot` prop or render-prop. PR 4.5 wires `<SharePopover />` as that slot.

```tsx
// SharePopover.tsx — sketch
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@enterprise-search/design-system";

export function SharePopover({
  chatTitle,
  chatUrl,
  anchor,
}: {
  chatTitle: string;
  chatUrl: string;
  anchor: ReactNode;
}) {
  const [open, setOpen] = useState(false);
  const text = `Atlas — ${chatTitle}: ${chatUrl}`;

  const onCopy = async () => {
    await navigator.clipboard.writeText(chatUrl); /* toast */
  };
  const onSlack = () =>
    window.open(
      `slack://share?url=${encodeURIComponent(chatUrl)}&text=${encodeURIComponent(text)}`,
    );
  const onEmail = () => {
    window.location.href = `mailto:?subject=${encodeURIComponent(chatTitle)}&body=${encodeURIComponent(text)}`;
  };

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>{anchor}</PopoverTrigger>
      <PopoverContent>
        <button className="sp-row" onClick={onCopy}>
          <Icon.copy /> Copy link
        </button>
        <button className="sp-row" onClick={onSlack}>
          <Icon.slack /> Share to Slack
        </button>
        <button className="sp-row" onClick={onEmail}>
          <Icon.mail /> Share to email
        </button>
        <hr />
        <fieldset
          className="sp-fieldset"
          disabled
          aria-describedby="sp-disabled-tt"
        >
          <legend>View access</legend>
          <label>
            <input type="radio" name="va" defaultChecked /> Anyone in workspace
          </label>
          <label>
            <input type="radio" name="va" /> Specific people
          </label>
        </fieldset>
        <label
          className="sp-toggle"
          data-disabled
          aria-describedby="sp-disabled-tt"
        >
          <Switch disabled />
          Sources visible to viewer
        </label>
        <span id="sp-disabled-tt" className="sp-disabled-tt">
          Sharing settings ship with v2.
        </span>
      </PopoverContent>
    </Popover>
  );
}
```

Disabled radios + switch use the standard HTML `disabled` attribute; the tooltip is a single `<span aria-describedby>`. Accessible, no extra primitives.

### 2.10 Design-system change — `<Popover>`

```tsx
// packages/design-system/src/popover.tsx (NEW)

import * as RadixPopover from "@radix-ui/react-popover";

export function Popover(props: RadixPopover.PopoverProps) {
  return <RadixPopover.Root {...props} />;
}
export const PopoverTrigger = RadixPopover.Trigger;
export function PopoverContent({
  children,
  ...rest
}: RadixPopover.PopoverContentProps & { children: ReactNode }) {
  return (
    <RadixPopover.Portal>
      <RadixPopover.Content
        className="ds-popover-content"
        sideOffset={6}
        {...rest}
      >
        {children}
      </RadixPopover.Content>
    </RadixPopover.Portal>
  );
}
export const PopoverClose = RadixPopover.Close;
```

Plus ~30 LOC of CSS in `packages/design-system/src/styles.css` (positioning, shadow, fade-in animation that respects `[data-reduce-motion]` from PR 4.1). Same Radix family already adopted by PR 4.4 (`<Dialog>`) and PR 4.2 (`<DropdownMenu>`); we add Popover here, completing the trio.

---

## 3 · Architecture

### 3.1 Where this lives in the system

```
   ┌─────────────────────────────┐                                ┌──────────────────────┐
   │ apps/frontend               │                                │ ai-backend           │
   │                             │  GET /v1/usage/me?period=…    │                      │
   │ Topbar usage meter (PR 2.1) │ ─────────────────────────────► │  /v1/usage/me        │
   │ ▼                            │                               │  /v1/usage/me/conv   │
   │ <UsagePanel>                │  GET /v1/usage/conversations/{id}│ /v1/usage/conv/{id}│
   │   <Tabs>                    │ ─────────────────────────────► │                      │
   │     <UsageConversationView />│  GET /v1/usage/org?period=…   │  /v1/usage/org       │
   │     <UsageWorkspaceView />  │ ─────────────────────────────► │                      │
   │       <UsageWorkspaceChart>  │  GET /v1/budgets/me          │                      │
   │       <UsageTopUsersTable>  │ ─────────────────────────────► │  /v1/budgets/me      │
   │                             │                                │                      │
   │ Topbar share button (PR 2.1) │                                │                      │
   │ ▼                            │                                │                      │
   │ <SharePopover>              │  navigator.clipboard / slack:// / mailto: — no API     │
   │                             │                                │                      │
   └─────────────────────────────┘                                └──────────────────────┘
```

### 3.2 Streaming impact — explicitly **none**

| Subsystem                                | Touched?                                 |
| ---------------------------------------- | ---------------------------------------- |
| `runtime_events`, `RuntimeEventEnvelope` | No                                       |
| SSE handshake                            | No                                       |
| Worker job loop                          | No                                       |
| Capabilities / tools / MCP loaders       | No                                       |
| Citations, drafts, approvals, subagents  | No                                       |
| Audit chain                              | No (sharing audit ships in Wave 6)       |
| Retention sweeper                        | No                                       |
| Usage rollup loop                        | No (we **read** rollups, we don't write) |

This PR is pure presentation. The worker, the harness, and the wire are unchanged.

### 3.3 Why `recharts` and not a custom SVG

| Alternative                        | Cost                                                                                             | Why not                                                                                                                                 |
| ---------------------------------- | ------------------------------------------------------------------------------------------------ | --------------------------------------------------------------------------------------------------------------------------------------- |
| `recharts` (chosen)                | ~50 KB gzipped; declarative; ResponsiveContainer + Area + ReferenceLine give us 90% of the chart | Wins on labour. Industry standard for React charts. MIT, weekly downloads >2M.                                                          |
| `@visx/visx` (modular)             | ~15 KB per used module; less wiring magic                                                        | More wiring overhead for one chart. We'd need `@visx/scale`, `@visx/shape`, `@visx/axis`, `@visx/tooltip` — sums to similar size + LOC. |
| `d3` direct                        | ~40 KB; full power                                                                               | ~250 LOC of axes / scales / tooltip / legend that adds nothing over `recharts`.                                                         |
| Custom SVG with `d3-shape.stack()` | ~5 KB for `d3-shape` alone                                                                       | Most of the wiring is axes + interactivity, not stacking. Building this from scratch is a budget waste.                                 |
| `chart.js`                         | ~70 KB; canvas-based                                                                             | Canvas not SVG; harder to style with our design tokens; the React wrapper (`react-chartjs-2`) adds further layer.                       |
| `apex-charts`                      | ~140 KB                                                                                          | Larger; not worth it.                                                                                                                   |

The deciding rule: when one chart is the only chart we'll ship for several quarters, `recharts` is the right tradeoff. If chart count grows past ~6, we revisit (visx becomes attractive).

### 3.4 Why share popover ships now (with stubs) instead of waiting for Wave 6

| Argument                                                                                                                                                    |
| ----------------------------------------------------------------------------------------------------------------------------------------------------------- |
| The topbar share button (PR 2.1) is shipping now. Without the popover, clicking it does only "copy link" — feels unfinished.                                |
| Slack / email deep-links are a real, useful capability today; they don't depend on Wave 6.                                                                  |
| The "view access" + "sources visible" rows render the design's intent and prepare users for v2; greying them with a clear tooltip is honest.                |
| When Wave 6 lands, swapping the disabled fieldset for a wired form is a 30 LOC change inside `<SharePopover>` — no API or shape change at the topbar level. |

The shape of v1 → v2 migration: the popover keeps its rows; v1's Slack/email handlers can stay (they're useful even after Wave 6); v1's disabled fieldset becomes a real form posting to `POST /v1/conversations/{id}/share`.

### 3.5 DRY — what we reuse vs. what we add

| Concern                  | Reuse                                                               | Add                                                                                    |
| ------------------------ | ------------------------------------------------------------------- | -------------------------------------------------------------------------------------- |
| Usage data               | All `/v1/usage/*` and `/v1/budgets/me` endpoints already shipped    | —                                                                                      |
| Usage panel host         | `UsagePanel.tsx` (refit, not replace)                               | Tab switch + workspace-view body                                                       |
| Per-conversation tables  | Existing tables in `UsagePanel`                                     | Lift them into `<UsageConversationView>` for clarity                                   |
| Period selector          | Existing                                                            | —                                                                                      |
| Chart library            | `recharts` (new top-level dep)                                      | One `<UsageWorkspaceChart>` (~120 LOC) that PR 4.2 also embeds inside the Billing card |
| Top-users table          | Existing table primitives                                           | One `<UsageTopUsersTable>` component                                                   |
| Palette                  | Existing accent ramp in `packages/design-system`                    | One `usagePalette()` mapper                                                            |
| Popover primitive        | `@radix-ui/react-popover` (new dep, used here + by future popovers) | One `<Popover>` wrapper in design-system                                               |
| Clipboard                | Existing `onShare` clipboard path                                   | —                                                                                      |
| Slack / email deep-links | None                                                                | Two button handlers (`slack://`, `mailto:`)                                            |
| View-access UI           | Native `<input type="radio">` + `disabled` + `aria-describedby`     | One disabled fieldset with tooltip                                                     |
| Sources-visible toggle   | Existing `<Switch disabled>` from design-system                     | —                                                                                      |
| FE state                 | Two thin hooks (`useUsageOrg`, `useShareCopy`)                      | ~40 LOC each                                                                           |
| Topbar mount             | PR 2.1's share button render-prop                                   | Pass `<SharePopover>` as the prop                                                      |

### 3.6 Pre-built libraries — what we considered, what we use

| Need                            | Considered                                                      | Decision                                                                                          |
| ------------------------------- | --------------------------------------------------------------- | ------------------------------------------------------------------------------------------------- |
| Chart                           | `recharts`, `@visx/visx`, `chart.js`, `apex-charts`, custom SVG | **`recharts`.** See §3.3.                                                                         |
| Popover primitive               | `@radix-ui/react-popover`, `floating-ui`, `react-tippy`         | **Radix Popover.** Same family as PR 4.4 (`<Dialog>`) and PR 4.2 (`<DropdownMenu>`). One install. |
| Tooltip (for the disabled rows) | `@radix-ui/react-tooltip`, native title attr, custom            | **Native `<span aria-describedby>`.** One disabled fieldset, one tooltip, native works.           |
| Slack deep-link                 | Slack web SDK, `@slack/web-api`                                 | **`slack://share` URL scheme.** v1 doesn't need API auth.                                         |
| Email send                      | `nodemailer`, server-side                                       | **`mailto:`** — opens user's mail client; honest, no backend.                                     |
| Clipboard                       | `clipboard.js`                                                  | **`navigator.clipboard.writeText`** — already used.                                               |
| Date formatting                 | `date-fns`, native `Intl.DateTimeFormat`                        | **`Intl.DateTimeFormat`.** Same as PR 1.6's sidebar grouping.                                     |
| Number/cost formatting          | `numeral`, native `Intl.NumberFormat`                           | **`Intl.NumberFormat`.** Stdlib.                                                                  |

Two new top-level deps in this PR: `recharts` and `@radix-ui/react-popover`. Both are mainstream React libs with clear long-term support. We accept the bundle delta.

### 3.7 Sequence — Sarah opens the usage panel

```
Sarah               FE                                         ai-backend
 │                    │                                          │
 │ click meter        │                                          │
 │ ───────────────► │ open <UsagePanel>                         │
 │                    │ tab = 'conversation' (default)            │
 │                    │ GET /v1/usage/me?period=30d              │
 │                    │ GET /v1/usage/me/conversations?period=30d │
 │                    │ GET /v1/usage/conversations/{id}?period=30d
 │                    │ ─────────────────────────────────────► │ (parallel)
 │                    │ ◄───────────────────────────────────── │
 │                    │ render tables                            │
 │                    │                                          │
 │ click "Workspace" tab                                          │
 │                    │ GET /v1/usage/org?period=30d             │
 │                    │ GET /v1/budgets/me                       │
 │                    │ ─────────────────────────────────────► │ (parallel)
 │                    │ ◄───────────────────────────────────── │
 │                    │ pivot by-day-by-user → recharts data    │
 │                    │ render <UsageWorkspaceChart>             │
 │                    │ render <UsageTopUsersTable>              │
 │                    │                                          │
 │ change period → 7d                                              │
 │                    │ refetch all four endpoints with period=7d│
 │                    │ chart re-renders without flicker          │
```

### 3.8 Edge cases

| Case                                                     | Behaviour                                                                                                                                                |
| -------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Member opens Workspace tab                               | `GET /v1/usage/org` returns 403; we render the admin-only empty state with a "Learn more" link.                                                          |
| No budgets configured                                    | Plan-limit overlay omitted; chart fits its own y-axis.                                                                                                   |
| Org has 1 user                                           | "Top users" table has 1 row; chart has 1 stack.                                                                                                          |
| Org has 200 users                                        | We render top-N (default N=6); rest fold into "Other" bucket. Chart and table both honour the same N.                                                    |
| `usage_daily_rollups` is stale (rollup loop disabled)    | We render whatever the endpoint returns; the loop's freshness is not the panel's concern.                                                                |
| User clicks "Share to Slack" but Slack isn't installed   | Browser fallback; the popover stays open so the user can pick another option.                                                                            |
| User has DnD on for clipboard (Permissions API denies)   | Existing `onShare` fallback message renders; we don't re-prompt.                                                                                         |
| Two tabs open the popover and one closes                 | Each tab has its own popover state; closing one doesn't affect the other.                                                                                |
| Chart re-renders during `prefers-reduced-motion: reduce` | `recharts` animations honour the system preference (it queries `matchMedia` internally); plus our `[data-reduce-motion]` overrides any remaining tweens. |
| URL contains a token (e.g. for shared chats in Wave 6)   | v1 doesn't generate token URLs; the popover copies whatever the page URL is. Wave 6 swaps the source URL.                                                |
| User on `/settings#…` clicks share                       | The share popover doesn't render on Settings (no chat URL to share). The topbar share button hides on Settings (PR 2.1's responsibility).                |
| Top-users table sort by cost when costs are all NULL     | Existing `showCosts` logic in `UsagePanel` (lines 82-86) — column hides; sort defaults to tokens.                                                        |

### 3.9 Test plan

**Frontend (`apps/frontend/src/features/`)**

- `chat/components/details/UsagePanel.test.tsx` — tab switch; period selector still works.
- `chat/components/details/usage/UsageWorkspaceChart.test.tsx` — pivots data correctly; renders the threshold line when budget present; omits when absent; admin-only empty state.
- `chat/components/details/usage/UsageTopUsersTable.test.tsx` — sortable; "Other" row collapses tail.
- `chat/components/details/usage/usagePalette.test.ts` — deterministic colours from accent ramp.
- `share/SharePopover.test.tsx` — copy works (jsdom mock); Slack handler invokes `window.open` with the right URL; email handler sets `window.location.href`; disabled rows render with tooltip.
- `bundle.size.test.ts` — `dist/assets` total < some sane upper bound (catch accidental regressions from chart libs).

**Cross-service smoke (`make test`)** — none required (no backend changes).

### 3.10 Rollout

- **Flag-free.** The Workspace tab is hidden for non-admins (server returns 403 → empty state). Admins see it immediately.
- **No migration.**
- **Bundle delta.** `recharts` + `@radix-ui/react-popover` ≈ 60 KB gzipped together. Track in CI bundle assertion.
- **Backout.** Revert frontend; `UsagePanel` returns to the conversation-only table; topbar share returns to clipboard-only. No persistent state to clean up.
- **Forward compatibility.** Wave 6 sharing wires through the same `<SharePopover>` shell — disabled rows become enabled, new actions (forward, fork) compose in.

### 3.11 Open questions

1. **Per-user privacy of org usage.** The org-level chart shows other users' totals. Some workspaces may want to anonymise. For v1, admins/auditors only — they're the audience that needs identifiable totals. If user-level anonymisation becomes necessary, a `?anonymise=true` query param is the path.
2. **Top-N choice (6 or 10).** Six fits visually for stacked colours; ten fits the table well. We pick 6 for the chart, 25 for the table — defensible.
3. **Plan-limit calibration.** `GET /v1/budgets/me` returns budgets of multiple kinds (token vs. cost). v1 prefers cost (USD); falls back to tokens if cost limit absent. The threshold-line label adjusts.
4. **CSV export.** Some admins want raw exports; `?format=csv` on the org endpoint is one possible add. Out of scope.
5. **Forecast overage.** Design "later." Linear extrapolation from current-period spend is straightforward; we revisit when a workspace asks.

---

## 4 · Acceptance checklist

- [ ] `<UsagePanel>` shows two tabs (`conversation` / `workspace`), default `conversation`.
- [ ] Workspace tab renders `<UsageWorkspaceChart>` driven by `GET /v1/usage/org?period={selected}`.
- [ ] Plan-limit overlay shows when `GET /v1/budgets/me` returns an org budget; omitted otherwise.
- [ ] `<UsageTopUsersTable>` renders below the chart, sortable by tokens / cost.
- [ ] Member opening the Workspace tab sees the admin-only empty state (server 403 surfaced cleanly).
- [ ] Period selector refetches both me and org endpoints; chart re-renders.
- [ ] `recharts` + `@radix-ui/react-popover` added as `apps/frontend` deps.
- [ ] `<Popover>` primitive added to `@enterprise-search/design-system`; CSS includes the reduce-motion-respecting fade.
- [ ] `<SharePopover>` mounts off the topbar share button (PR 2.1 slot).
- [ ] Copy / Slack / Email deep-links work; view-access + sources-visible rows render disabled with tooltip.
- [ ] No new event types, no new wire variants, no SSE schema change, no backend change.
- [ ] `npm run typecheck`, `npm run build` (with bundle-size assertion) all green.

---

## 5 · References

- Design Doc · Usage overlay (two views) + Share popover — bundle at `/tmp/design-doc/enterprise-search/project/Design Doc.html` lines 599-612, 655-662.
- [`apps/frontend/src/features/chat/components/details/UsagePanel.tsx`](../../apps/frontend/src/features/chat/components/details/UsagePanel.tsx) — current per-conversation panel.
- [`services/ai-backend/migrations/0007_usage_daily_rollups.sql`](../../services/ai-backend/migrations/0007_usage_daily_rollups.sql) — `runtime_usage_daily_user` + `runtime_usage_daily_org`.
- [`services/ai-backend/migrations/0009_usage_budgets.sql`](../../services/ai-backend/migrations/0009_usage_budgets.sql) — `usage_budgets` for the plan-limit overlay.
- [`services/ai-backend/src/runtime_worker/usage_rollup_loop.py`](../../services/ai-backend/src/runtime_worker/usage_rollup_loop.py) — the upstream that keeps rollups fresh.
- [`apps/frontend/src/features/chat/ChatScreen.tsx`](../../apps/frontend/src/features/chat/ChatScreen.tsx) lines 706-717 — current `onShare` clipboard path that the popover wraps.
- [Recharts](https://recharts.org/) — chart library adopted.
- [Radix UI · Popover](https://www.radix-ui.com/primitives/docs/components/popover) — primitive added to design-system.
- [Slack URL scheme](https://api.slack.com/reference/deep-linking#share) — `slack://share?url=…&text=…`.
- [Mozilla Web Docs · `mailto`](https://developer.mozilla.org/docs/Web/HTML/Element/a#mailto_links) — email deep-link.
- [`docs/new-design/pr-2.1-topbar-chrome-thinking-depth.md`](pr-2.1-topbar-chrome-thinking-depth.md) — topbar share/usage buttons we mount into.
- [`docs/new-design/pr-4.2-settings-workspace-group.md`](pr-4.2-settings-workspace-group.md) — second consumer of `<UsageWorkspaceChart>` inside the Billing card.
- [`docs/new-design/pr-4.4-mcp-overlay-test-connection.md`](pr-4.4-mcp-overlay-test-connection.md) — sibling that adopts `<Dialog>`; this PR adopts `<Popover>`.
