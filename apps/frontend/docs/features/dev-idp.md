# Dev IdP

How the frontend mints bearers in development without ever shipping a
dev-bypass branch to production.

See also:

- [../architecture/02-auth-state.md](../architecture/02-auth-state.md) —
  where the dev mint hooks into the 401 path
- Backend docs: `services/backend/docs/features/identity-auth.md` and
  `services/backend-facade/docs/architecture/02-auth-identity.md` for the
  facade side

Source: [`src/features/auth/devIdp.ts`](../../src/features/auth/devIdp.ts),
[`src/features/chat/components/sidebar/DevPersonaSwitcher.tsx`](../../src/features/chat/components/sidebar/DevPersonaSwitcher.tsx)

---

## What it does

Two endpoints, both proxied by the facade in development only:

| Endpoint                     | Purpose                                      |
| ---------------------------- | -------------------------------------------- |
| `GET  /v1/dev/personas`      | List the configured dev personas             |
| `POST /v1/dev/identity/mint` | Mint a real HMAC bearer for a chosen persona |

The bearer is signed with `ENTERPRISE_AUTH_SECRET` and verified by the same
code path production uses — there is no `DEV_AUTH_BYPASS` shortcut anymore.

---

## Tree-shaking guarantee

Every caller in this module is guarded by `import.meta.env.DEV`:

- `_devEnsureBearer` (in `AuthContext.tsx`) returns `null` immediately in prod.
- `<DevPersonaSwitcher>` renders `null` in prod.

Production Vite builds therefore tree-shake the entire `devIdp.ts` module
plus the dev re-auth branch in `AuthContext`. The same applies to any
future caller — guard with `import.meta.env.DEV` and the bundle stays
clean.

---

## Persona selection

| Storage key                   | Default      | Owner                                                |
| ----------------------------- | ------------ | ---------------------------------------------------- |
| `enterprise.dev.persona_slug` | `sarah_acme` | `loadActivePersonaSlug` / `persistActivePersonaSlug` |

`<DevPersonaSwitcher>` writes the chosen slug back to localStorage and
triggers a re-mint via `mintDevBearer(slug)`. On switch, the existing
bearer is replaced and `refresh()` re-probes the session so the identity
matches the new bearer.

---

## How 401 recovery uses this

When any API helper hits a 401 in dev:

1. `assertOk` calls `_onUnauthorized` (registered by `<AuthProvider>`).
2. `<AuthProvider>`'s `_devReauthAndRestoreSession`:
   - `_devEnsureBearer()` reads the active persona slug and mints a fresh bearer.
   - `setBearer(minted)` updates the in-memory + localStorage handle.
   - `fetchCurrentSession()` re-probes `/v1/auth/session`.
3. On success the state stays `authenticated` and the original request can
   be retried by the caller. On failure, the bearer is dropped and the
   state flips to `anonymous`.

This means a dev developer can clear localStorage, restart the facade, or
let a bearer expire and the next protected request silently recovers
without a login round-trip.

---

## Hitting the dev surface from curl

The make targets and full curl recipes live at the repo root:

```bash
export TOKEN=$(make dev-bearer)                       # default: sarah_acme
export TOKEN=$(make dev-bearer PERSONA=marcus_admin)  # admin persona
curl -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8200/v1/me/profile
```

Always call the **facade** at `:8200` — never `:8100` or `:8000` directly.
