# Frontend App

Vite + React. Calls `backend-facade` only — never `backend` or `ai-backend` directly.

## Before changing behavior

Read [docs/README.md](docs/README.md) to find the relevant doc, then read it before implementing.
Architecture, features, and reference docs are the source of truth.

## Network layer

- All HTTP and SSE clients live in `src/api/*`. Do **not** add new callers to legacy root-level API helper files.
- Browser → Vite proxy (or nginx in prod) → `backend-facade` (`/v1/*`). Never call `backend` (`:8100`) or `ai-backend` (`:8000`) directly, even in dev.

## Shared packages

- Use [`@0x-copilot/api-types`](../../packages/api-types) for app-facing payload shapes. When public contracts change, update api-types in the same change.
- Use [`@0x-copilot/design-system`](../../packages/design-system) primitives for reusable UI. **Feature workflows stay here**, not in design-system.

## Streaming

Events arrive with a monotonic `sequence_no` per run. Reconnect with the highest received `sequence_no` — `?after_sequence=N` resumes without replay. Use the backend's projected `activity_kind` / `display_title` / `summary` / `status` fields. Do not derive activity types from event-name prefixes on the client.

## Markdown rendering

Render assistant messages as Markdown via Streamdown. Other roles (user, system, tool) stay plain text unless a feature explicitly opts in.

## Chat surface invariants

Two regressions kept biting the chat surface; the rules below exist so they don't return. Both are pinned by tests — if you change the underlying code, update the tests in the same change rather than weakening the invariant.

### Planning-pulse visibility

- `<PlanningIndicator>` lives inside the `.aui-thread-viewport` flex column. That viewport must keep `min-height: 0` so `overflow-y: auto` can scroll past tall message lists, but `min-height: 0` also lets flex children shrink below their intrinsic size. The indicator must therefore opt out of flex compression with `flex: 0 0 auto`. Do not remove that rule from `.aui-planning-indicator` — the element will silently collapse to `height: 0` once the message list crosses a screen-height threshold, even though `data-visible="true"` and computed `max-height` are correct. The CSS comment in `apps/frontend/src/styles.css` explains the trap; keep it.
- The state derivation is in `apps/frontend/src/features/chat/chatRunState.ts`. The pulse is intentionally on for **every active phase** (`starting`, `working`, `acting`, `writing`, `reasoning`) — only `terminal`, `idle`, and `waiting_for_permission` suppress it. Don't re-introduce phase-specific suppression (e.g. "no pulse during writing" or "no pulse while a tool is running"); on fast models the affected phases pass in <1s and the user perceives the run as dead. The corresponding tests in `chatRunState.test.ts` pin every active phase to `showPlanningIndicator: true`.

### Composer hint row

The composer hint strip in `<AssistantComposer>` — now `/ skills · Sources cited inline` — is **stateless info** — it must render whether or not a run is active. Do not gate the `hint` prop on `running` (or any other run-state flag). Hiding it during a run was a real shipped regression; the user is mid-flight, can't see their shortcuts, and the composer looks broken. If you add a new hint or change the strip, render it unconditionally and let the affordance itself reflect availability (e.g. disable a button, don't unmount the row).

The `↵ send` / `⇧+↵ new line` keyboard hints and the duplicated `model` name were **intentionally removed** from this strip to match the Claude Design composer mock (its composer shows no send/newline hint, and the model name is one-source-of-truth in the `ModelPill` above the row). Don't "restore" them as a regression fix — the removal is deliberate; see the `hintRender` comment in `packages/chat-surface/src/composer/AssistantComposer.tsx`.

## Validation

```bash
npm run typecheck --workspace @0x-copilot/frontend
npm run build --workspace @0x-copilot/frontend
```

Run typecheck/build for behavior changes and shared-package consumers when practical.
