# PR 20 — B6: /usage Slash Command and Panel

**Spec ID:** B6 | **Track:** Token Usage | **Wave:** 5 (Usage UX + Budgets) | **Estimated effort:** S/M
**Depends on:** B4 (read endpoints), B3 (cost columns)
**Required for:** none

---

## 1. Functional Specification

### 1.1 Goal

A `/usage` slash command that opens a panel showing the user's token spend over today/7d/30d/month with a model breakdown and top conversations. Cost shown when pricing is seeded.

### 1.2 User-visible behavior

- **End user:** types `/usage` → panel opens (no message sent) with period switcher and tables.
- **Single-tenant deploys without billing:** cost section auto-hidden when `cost_micro_usd` is null for all rows.

### 1.3 Out of scope

- Org-admin view (admin UI for `/v1/usage/org` deferred to a later admin-console PR).
- CSV export (left as a small follow-up).

---

## 2. Technical Specification

### 2.1 Architecture

- Frontend-only PR (consumes B4 endpoints).
- Single `formatMicroUsd(value: number | null): string` helper — never re-implemented inline. Locale-aware.
- Period switcher refetches; results cached for 60s in-memory per period.

### 2.2 Schema changes

None.

### 2.3 Endpoints used

- `GET /v1/usage/me?period={today|7d|30d|month}` (B4)
- `GET /v1/usage/me/conversations?period=&limit=10` (B4)

### 2.4 Code changes

**New:**

- `apps/frontend/src/features/chat/utils/formatMicroUsd.ts` — single helper.
- `apps/frontend/src/features/chat/components/details/UsagePanel.tsx`:
  - Period switcher (today / 7d / 30d / month).
  - Total card: tokens + cost (if non-null).
  - "By model" table.
  - "Top conversations" table.
- New API client functions in [apps/frontend/src/api/agentApi.ts](../../apps/frontend/src/api/agentApi.ts) — `getMyUsage(period)`, `getMyTopConversations(period, limit)`.

**Modify:**

- [apps/frontend/src/features/chat/components/composer/AssistantComposer.tsx](../../apps/frontend/src/features/chat/components/composer/AssistantComposer.tsx) — register `/usage` slash command.
- [apps/frontend/src/features/chat/utils/activityDataBuilders.ts:54-75](../../apps/frontend/src/features/chat/utils/activityDataBuilders.ts#L54-L75) `metricRows` — surface input + cached tokens (currently hidden).

### 2.5 Trust model & failure semantics

- Bearer carries identity; backend resolves user.
- Cost section hidden when API returns `cost_micro_usd: null` for all rows in the period.
- Network error → "couldn't load usage" state with retry button.

### 2.6 Tenant isolation

Backend already enforces (B4).

### 2.7 Observability

- Sentry breadcrumbs on panel open / period switch.

---

## 3. Requirements & Acceptance Criteria

### 3.1 Functional acceptance criteria

- [ ] `/usage` opens panel.
- [ ] Period switching refetches.
- [ ] Large numbers (>1M tokens) render with thousands separators per locale.
- [ ] Cost section hidden when all rows have `cost_micro_usd: null`.
- [ ] Cost rendered as `$X.XX USD` via `formatMicroUsd`.

### 3.2 Test plan

**Frontend unit (vitest + RTL):**

- Period switching triggers refetch.
- Cost-hidden state when all null.
- formatMicroUsd: rounding, locale, null handling.

**Visual / e2e:**

- Type `/usage` → panel renders with correct shape.
- Switch periods → values update.

### 3.3 Compliance evidence produced

- End-user transparency into their own usage and cost.

### 3.4 Rollout plan

Additive UI feature. Behind no flag.

### 3.5 Backout plan

Hide slash command via build-time flag.

### 3.6 Definition of done

- [ ] Slash command + panel + helper + tests land.
- [ ] activityDataBuilders updated to include input + cached tokens.

---

## 4. Critical files

- Modify: [apps/frontend/src/api/agentApi.ts](../../apps/frontend/src/api/agentApi.ts)
- Modify: [apps/frontend/src/features/chat/components/composer/AssistantComposer.tsx](../../apps/frontend/src/features/chat/components/composer/AssistantComposer.tsx)
- New: `apps/frontend/src/features/chat/components/details/UsagePanel.tsx`
- New: `apps/frontend/src/features/chat/utils/formatMicroUsd.ts`
- Modify: [apps/frontend/src/features/chat/utils/activityDataBuilders.ts:54-75](../../apps/frontend/src/features/chat/utils/activityDataBuilders.ts#L54-L75)
