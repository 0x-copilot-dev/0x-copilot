# Auth state

`<AuthContext>` is the source of truth for whether the app can render.

See also:

- [01-network-layer.md](01-network-layer.md) — `AuthProvider` registers the
  bearer provider + 401 handler here
- [03-routing.md](03-routing.md) — `AuthGate` selects the screen
- [features/dev-idp.md](../features/dev-idp.md) — persona switching and
  the dev mint that recovers from 401s in development

Source: [`src/features/auth/AuthContext.tsx`](../../src/features/auth/AuthContext.tsx)

---

## State machine

```
                       ┌──────────┐
                       │ initial  │ (constructor)
                       └────┬─────┘
                            │ first render → refresh()
                            ▼
                       ┌──────────┐
                       │ loading  │
                       └────┬─────┘
              session OK  ┌─┴─────────┐  401 / network error
                          ▼           ▼
                  ┌──────────────┐   ┌────────────┐
                  │ authenticated │   │  anonymous │
                  └──────┬───────┘   └────┬───────┘
                         │                │ login()
                         │ logout()       ▼
                         ▼          ┌──────────────┐
                  ┌──────────────┐  │  loading     │
                  │  anonymous   │  └────┬─────────┘
                  └──────────────┘       │
                                  ┌──────┴──────────────┐
                                  │ requires_mfa?       │ workspaces.length > 1?
                                  ▼ yes                 ▼ yes
                          ┌─────────────┐       ┌─────────────────┐
                          │ mfa_pending │       │ workspace_pick  │
                          └─────┬───────┘       └────────┬────────┘
                                │ verify                  │ selectWorkspaceFromPick
                                ▼                         ▼
                         refresh() → authenticated   refresh() → authenticated
```

`error` is reached only when `refresh()` raises something **other** than a
401 (e.g. JSON parse failure, network refused). The login screen renders the
error string from `state.error`.

---

## The five statuses

| Status           | What rendered (`AuthGate`)          | What's true                                                                                   |
| ---------------- | ----------------------------------- | --------------------------------------------------------------------------------------------- |
| `initial`        | spinner                             | constructor only, before first `refresh()`                                                    |
| `loading`        | spinner                             | a transition is in flight (`refresh`, `login`, `consumeMagicLink`, `selectWorkspaceFromPick`) |
| `anonymous`      | `<LoginScreen>`                     | no bearer, no identity                                                                        |
| `error`          | `<LoginScreen>` + `app-auth-error`  | non-401 failure surfaced                                                                      |
| `mfa_pending`    | `<MfaPrompt>`                       | bearer minted but carries `permission_scopes=("mfa:pending",)`                                |
| `workspace_pick` | `<LoginScreen>` (workspace chooser) | magic link returned multiple workspaces; user picks one to exchange                           |
| `authenticated`  | `<CopilotApp>`                      | `state.identity` non-null; downstream code may assume `org_id` and `user_id`                  |

---

## Bearer storage

| Knob                                | Default                  | Source                                                                                                            |
| ----------------------------------- | ------------------------ | ----------------------------------------------------------------------------------------------------------------- |
| `persistBearer` on `<AuthProvider>` | `true`                   | Set to `false` in deploy profiles where browser-local bearer persistence is disallowed (e.g. single-tenant bank). |
| Storage key                         | `enterprise.auth.bearer` | `localStorage`; if `persistBearer=false` or storage throws, the bearer is in-memory only.                         |

`bearerRef` is the canonical handle; `localStorage` is a write-through cache.
A storage failure (private browsing, quota) is swallowed — the in-memory copy
keeps the tab alive.

---

## Magic-link callback flow (`consumeMagicLink`)

1. Email link lands at `/auth/magic-link/callback?token=<plaintext>`.
2. `<AuthGate>` sees the path, routes to `<LoginScreen>` even while status
   is `initial`/`loading`.
3. `<LoginScreen>` reads `?token=` and calls `auth.consumeMagicLink(token)`.
4. Server responds with one of:
   - `session_minted` → bearer set, `refresh()` flips to `authenticated`.
   - `workspace_pick_required` → state becomes `workspace_pick`; chooser shown.
5. On chooser submit, `selectWorkspaceFromPick(orgId)` exchanges the
   `pick_token` for a final bearer.
6. Any 401 along the path drops the bearer and flips to `anonymous`, but
   `refresh()` is careful **not** to stomp on a live `mfa_pending` or
   `workspace_pick` state — those are owned by `login` / `consumeMagicLink`
   and outlive a session-probe 401 (the bearer hasn't been minted yet).

---

## Workspace switching (`switchWorkspace`)

v1 hard-navigates to `?workspace=<orgId>` and lets `<AuthGate>` re-discover
the session on the next mount. The current bearer's claims still authorise
the user; the URL hint is informational while the auth team's
`POST /v1/auth/sessions { workspace_id }` rotation endpoint is in flight.
No-op when the requested org matches `state.identity.org_id`.

---

## Test surface

[`AuthContext.test.tsx`](../../src/features/auth/AuthContext.test.tsx) pins:

- Initial mount probes `/v1/auth/session`.
- 401 from session probe flips to `anonymous` (prod) **or** mints a dev
  bearer and re-probes (dev) before falling back.
- `mfa_pending` is preserved through a refresh 401.
- `consumeMagicLink` routes `session_minted` vs `workspace_pick_required`
  correctly.
- `switchWorkspace(currentOrg)` is a no-op (no `location.assign`).
