# Spec: B6 — `/usage` slash command (per-user spend panel)

**Roadmap PR:** [docs/roadmap/20-b6-usage-command.md](../../../../../docs/roadmap/20-b6-usage-command.md).
**Wave:** 5. **Depends on:** B3 (cost columns), B4 (read endpoints).

This document is the _implementation contract_ — see roadmap for full behavior.

## Architecture

**Frontend-only PR.** The `/v1/usage/me` and `/v1/usage/me/conversations` endpoints already exist (B4) and already return `cost_micro_usd: int | None`. The work is presentation:

- Slash command in `AssistantComposer` opens `<UsagePanel>` side panel (no run started).
- One `formatMicroUsd(value: number | null): string` helper, used everywhere — no inline `(x / 1_000_000).toFixed(2)` allowed.
- Period switcher (`today` / `7d` / `30d` / `month`) refetches; results cached for 60s per period in component state.
- Cost section auto-hides when **every** row in the response has `cost_micro_usd === null` (single-tenant deploys without pricing).

## Module boundaries

- New: `apps/frontend/src/features/chat/utils/formatMicroUsd.ts`
- New: `apps/frontend/src/features/chat/components/details/UsagePanel.tsx`
- Modify: `apps/frontend/src/api/agentApi.ts` — add `getMyUsage(period)`, `getMyTopConversations(period, limit)` thin wrappers around `apiFetch`.
- Modify: `apps/frontend/src/features/chat/components/composer/AssistantComposer.tsx` — register `/usage` slash command beside `/context`.
- Modify: `apps/frontend/src/features/chat/utils/activityDataBuilders.ts` (L54–75) — surface input + cached tokens on `metricRows` (currently hidden); fixes a long-standing display gap.

## Pydantic / TS contracts

No backend contracts change. New TS mirrors in `packages/api-types`:

```ts
export type UsagePeriod = "today" | "7d" | "30d" | "month";

export interface UsageTotals {
  input: number;
  output: number;
  cached_input: number;
  total: number;
  runs_count: number;
  cost_micro_usd: number | null;
}

export interface UsageMeResponse {
  period: { start: string; end: string };
  currency: "USD";
  total: UsageTotals;
  by_day: Array<UsageTotals & { day: string }>;
  by_model: Array<UsageTotals & { provider: string; model: string }>;
  cold_start_fallback: boolean;
}

export interface UsageConversationRow extends UsageTotals {
  conversation_id: string;
  title: string | null;
}
```

These mirror the existing backend Pydantic models verbatim — no field renames.

## Edge cases

- Period switch mid-flight → previous request cancelled via `AbortController`.
- Empty response (`runs_count === 0`) → "No usage in this period." card; cost section still hidden.
- `cold_start_fallback === true` → small footnote: "Aggregating live data; recent activity may take ~1 minute to appear."
- Locale: large numbers render with `Intl.NumberFormat(undefined)` (browser default locale).

## Security

Backend already enforces `(org_id, user_id)` scoping (B4). Frontend just consumes — no extra checks needed.

## Observability

- Sentry breadcrumbs on panel open / period switch (already wired in `apiFetch`; no new instrumentation).

## Tests

- **vitest + RTL**:
  - `formatMicroUsd` unit: rounding, locale fallback, null → "—" string.
  - `UsagePanel` interaction: period switch triggers refetch; cost section hidden when all-null.
  - Slash command: opens panel without dispatching a send (mirrors B5 test).
- **No backend tests** — no backend code changes.

## What we deliberately skip

- Org-admin view (`/v1/usage/org` already exists; admin UI is a future PR with admin-scope routing).
- CSV export (small follow-up).
- In-panel charting library — text totals + tables are sufficient for v1; chart can layer on later without rewriting the panel.
