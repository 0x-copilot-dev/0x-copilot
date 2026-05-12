# Routing

How the app picks a screen from `window.location` and keeps the URL in sync
when the user navigates inside React.

See also:

- [02-auth-state.md](02-auth-state.md) — `<AuthGate>` gates everything
  below
- [features/oauth-callback.md](../features/oauth-callback.md) — the MCP
  OAuth callback round-trip

Source: [`src/app/App.tsx`](../../src/app/App.tsx)

---

## Why hand-rolled instead of react-router

The app has four routes and one callback URL. Pulling in a router for that
much surface costs more in transitive deps than the 60 LOC `routeFromLocation`

- `applyAppRoute` pair. The route reducer is a discriminated union, the
  history calls live in one helper, and the deep-link forms are unit-testable
  against the same function the live app uses.

---

## Route table

| URL                                  | `AppRoute`                                        | Screen                                                    |
| ------------------------------------ | ------------------------------------------------- | --------------------------------------------------------- |
| `/`                                  | `{ screen: "chat" }`                              | `<ChatScreen>`                                            |
| `/settings`                          | `{ screen: "settings", section: DEFAULT }`        | `<SettingsScreen>` (profile section)                      |
| `/settings#<section>`                | `{ screen: "settings", section }`                 | `<SettingsScreen>` on the named section                   |
| `/settings/<section>` (legacy)       | migrated → hashed form (one-shot)                 | `<SettingsScreen>`; URL rewritten via `replaceState`      |
| `/share/<token>`                     | `{ screen: "share", token }`                      | `<ShareScreen>` (recipient view of a shared conversation) |
| `/mcp/oauth/callback?state=…&code=…` | handled outside the route reducer (see below)     | OAuth completes, app reroutes to chat or settings         |
| `/auth/magic-link/callback?token=…`  | handled by `<AuthGate>` → `<LoginScreen>`         | LoginScreen consumes the token                            |
| `/login`                             | unauthenticated path; AuthGate forces LoginScreen | `<LoginScreen>`                                           |

`SETTINGS_SECTIONS` (the typed union of valid sections) lives in
[`src/features/settings/useSettingsSection.ts`](../../src/features/settings/useSettingsSection.ts).

---

## `routeFromLocation()`

Pure function. Reads `window.location.{pathname, hash}` and returns an
`AppRoute`. Used both for initial render (inside `useState`'s lazy initializer)
and for `popstate` / `hashchange` listeners.

```
path = pathname.replace(/\/+$/, "") || "/"
  "/settings"           → settings + hash section
  "/settings/<x>"       → settings + decoded x (legacy, rewritten in-place)
  "/share/<token>"      → share + decoded token
  default               → chat
```

Unknown section names fall through to `DEFAULT_SETTINGS_SECTION` rather than
404 — every settings URL is reachable even after a section is renamed.

---

## `applyAppRoute(route, setRoute, mode)`

The only function that mutates `window.history`. Builds the canonical
`{ path, hash }` pair from the route and either `pushState`s (default) or
`replaceState`s (used after OAuth callbacks so the back button doesn't
return to the callback URL). It compares against the current location and
**also** when `?search` is present, because OAuth callback URLs carry a
query string that should not be preserved.

`setRoute` is called unconditionally so React stays in sync even when the
URL is already correct (initial mount via the legacy-path migrator).

---

## Legacy `/settings/<section>` migration

`migrateLegacySettingsPath()` (in `useSettingsSection.ts`) runs once inside
the route state initializer. If the URL is on the legacy form, it rewrites
the URL in place via `history.replaceState` so old bookmarks survive
without a 404. The fallback branch in `routeFromLocation` (decode the
trailing segment) exists because there's a brief window between the
migrator's `replaceState` and React's first paint.

---

## OAuth callback handling

`/mcp/oauth/callback` is **not** in the route reducer because it's a
one-shot side effect, not a screen. `<EnterpriseSearchApp>` mounts an
`useEffect` that:

1. Reads `state`, `code`, `error`, `error_description` from the search.
2. Validates the params; on bad shape, sets `oauthStatus` and routes back to chat.
3. Calls `completeMcpOAuthOnce(state, code, error, error_description)`.
   `completeMcpOAuthOnce` dedupes by `JSON.stringify([state, code, …])` so
   StrictMode's double-effect cannot double-complete a single OAuth state.
4. If there's a pending in-chat MCP auth approval (`readPendingMcpAuthAction`),
   the callback routes back to chat and surfaces a `completedMcpAuthAction`
   for the chat UI to reconcile. Otherwise it routes to
   `/settings#connectors` so the user lands on the connector they just
   authed.
5. Discovery approvals (`mcp_discovery:<run_id>:<server_id>`) are not
   real `ApprovalRequest` rows — the `decideApproval` POST is skipped for
   those IDs (the backend would 404). The OAuth completion itself is the
   resolution.

See [features/oauth-callback.md](../features/oauth-callback.md) for the
end-to-end flow.

---

## Listener wiring

`<EnterpriseSearchApp>` registers `popstate` and `hashchange` listeners on
mount; both call `sync()` which reads `routeFromLocation()` and updates
`route` state. Both are needed:

- `popstate` fires for back/forward and `history.pushState`/`replaceState` —
  but **not** for hash-only navigations.
- `hashchange` fires for URL-paste and manual hash edits ("Manage" deep
  links from outside the SPA).
