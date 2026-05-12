# Frontend — Knowledge Base

Agent-first documentation for `apps/frontend`. Every node answers one question
and links to adjacent nodes. Read this file first; all other paths branch from
here.

## What this app does

Vite + React 19 web client. The single allowed upstream is `backend-facade`
(`/v1/*`) — never `backend:8100` or `ai-backend:8000`, even in dev.
The app shell lives in [`src/app/App.tsx`](../src/app/App.tsx); feature
folders under `src/features/*` own their own UI and hooks; HTTP/SSE clients
live in `src/api/*` and nowhere else.

**What it does:**

- Boots through an `<AuthProvider>` that probes `/v1/auth/session` and gates
  the rest of the app on a real identity
- Routes between three top-level screens: chat, settings, and the recipient
  share view (`/`, `/settings#<section>`, `/share/<token>`)
- Renders streaming agent runs via a `fetch`-based SSE reader that carries
  the bearer header (the browser `EventSource` cannot)
- Projects runtime events into per-conversation reducers in
  [`src/features/chat/chatModel/`](../src/features/chat/chatModel/) — the
  pure state layer
- Emits browser OTEL spans via a same-origin OTLP/HTTP exporter; a
  hard-coded attribute allowlist prevents any user content from leaving
  the browser

**What it does NOT do:**

- Call `backend` or `ai-backend` directly
- Hold or render any product persistence the facade hasn't already shaped
- Derive activity types from event-name prefixes (use the backend's
  projected `activity_kind` / `display_title` / `summary` / `status`)
- Add a second auth scheme — cookies, `?token=` URL params, etc. are not
  used; the bearer rides every request via `correlationHeaders()`

## Navigation

| Question                                                            | Read                                                                       |
| ------------------------------------------------------------------- | -------------------------------------------------------------------------- |
| How is the app code organised? What does each folder own?           | [architecture/00-system-map.md](architecture/00-system-map.md)             |
| How does the HTTP layer attach the bearer and recover from 401?     | [architecture/01-network-layer.md](architecture/01-network-layer.md)       |
| How does the auth state machine work end-to-end?                    | [architecture/02-auth-state.md](architecture/02-auth-state.md)             |
| How does the app route between chat, settings, share, and OAuth?    | [architecture/03-routing.md](architecture/03-routing.md)                   |
| How does the SSE stream resume? Why `fetch` instead of EventSource? | [architecture/04-streaming.md](architecture/04-streaming.md)               |
| What chat-surface invariants exist? (planning pulse, composer hint) | [features/chat-surface-invariants.md](features/chat-surface-invariants.md) |
| How does the dev IdP persona switcher work?                         | [features/dev-idp.md](features/dev-idp.md)                                 |
| What is in OTEL spans? How are extension errors classified?         | [features/observability.md](features/observability.md)                     |
| How does the connector OAuth callback round-trip work?              | [features/oauth-callback.md](features/oauth-callback.md)                   |
| Full `/v1/*` surface the frontend calls                             | [reference/api-surface.md](reference/api-surface.md)                       |
| Build-time and runtime env vars                                     | [reference/env-vars.md](reference/env-vars.md)                             |
| What gets tested, where, and how                                    | [reference/testing.md](reference/testing.md)                               |

## Local loop

```bash
npm install                                            # repo root
npm run dev    --workspace @enterprise-search/frontend # Vite on :5173
npm run typecheck --workspace @enterprise-search/frontend
npm run test   --workspace @enterprise-search/frontend
npm run build  --workspace @enterprise-search/frontend
```

Vite proxies `/v1/*` to `http://127.0.0.1:8200` (the facade). In prod the
nginx image only serves the SPA — ingress must route `/v1/*` to the facade.

## Hard rules

- **Single upstream.** Browser → Vite proxy / nginx → `backend-facade`. Never
  `:8100` / `:8000` directly.
- **Single HTTP layer.** All clients live in `src/api/*`. No new feature
  callers in root-level legacy helpers.
- **No service imports.** Use `@enterprise-search/api-types` for shapes;
  reach the runtime over HTTP/SSE.
- **No prefix-based activity inference.** Backend already projects
  `activity_kind` / `display_title` / `summary` / `status`.
