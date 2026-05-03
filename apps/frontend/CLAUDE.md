# Frontend App

Vite + React. Calls `backend-facade` only — never `backend` or `ai-backend` directly.

## Before changing behavior

Read `apps/frontend/README.md`, `ARCHITECTURE.md`, and `TESTING.md` first.

## Network layer

- All HTTP and SSE clients live in `src/api/*`. Do **not** add new callers to legacy root-level API helper files.
- Browser → Vite proxy (or nginx in prod) → `backend-facade` (`/v1/*`). Never call `backend` (`:8100`) or `ai-backend` (`:8000`) directly, even in dev.

## Shared packages

- Use [`@enterprise-search/api-types`](../../packages/api-types) for app-facing payload shapes. When public contracts change, update api-types in the same change.
- Use [`@enterprise-search/design-system`](../../packages/design-system) primitives for reusable UI. **Feature workflows stay here**, not in design-system.

## Streaming

Events arrive with a monotonic `sequence_no` per run. Reconnect with the highest received `sequence_no` — `?after_sequence=N` resumes without replay. Use the backend's projected `activity_kind` / `display_title` / `summary` / `status` fields. Do not derive activity types from event-name prefixes on the client.

## Markdown rendering

Render assistant messages as Markdown via Streamdown. Other roles (user, system, tool) stay plain text unless a feature explicitly opts in.

## Validation

```bash
npm run typecheck --workspace @enterprise-search/frontend
npm run build --workspace @enterprise-search/frontend
```

Run typecheck/build for behavior changes and shared-package consumers when practical.
