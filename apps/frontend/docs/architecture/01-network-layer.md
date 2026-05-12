# Network layer

How every API client attaches the bearer, a request id, and tenant identity.

See also:

- [02-auth-state.md](02-auth-state.md) — `<AuthProvider>` registers the bearer provider and the 401 handler on mount
- [04-streaming.md](04-streaming.md) — SSE streams ride the same `correlationHeaders()` path
- [`reference/api-surface.md`](../reference/api-surface.md) — every route the frontend calls

Source: [`src/api/http.ts`](../../src/api/http.ts), [`src/api/config.ts`](../../src/api/config.ts)

---

## Two globals, one purpose

The HTTP module owns two process-globals — registered once by
`<AuthProvider>` on mount — so feature code never has to thread them through:

```ts
configureAuthBearerProvider((): string | null => bearerRef.current);
configureUnauthorizedHandler((response: Response) => {
  /* dev mint or → anonymous */
});
```

The provider is read on **every** call via `correlationHeaders()`; if a
bearer exists it goes out as `Authorization: Bearer <…>`. The 401 handler
fires once per response that comes back unauthenticated.

There is no fallback that emits requests _without_ a bearer. The previous
era of `DEV_AUTH_BYPASS` is gone; in dev `<AuthProvider>` mints one before
the first protected call (see [02-auth-state.md](02-auth-state.md)).

---

## `correlationHeaders()`

Returns the headers every protected `/v1/*` call should attach:

```ts
{
  "x-request-id": "req_<32 hex>",    // crypto.randomUUID(), strip dashes
  "authorization": "Bearer <token>", // only if a bearer is present
}
```

Public endpoints (e.g. `/v1/auth/discover`) ignore the bearer; protected
endpoints reject the call without it. Consumers should not branch on auth
state — just call `correlationHeaders()` and let the bearer ride along when
it exists.

`dynamicCorrelationHeaders()` returns a Proxy that re-reads the bearer on
every property access — used by long-lived consumers like the OTLP exporter
that need a fresh bearer per export (see [features/observability.md](../features/observability.md)).

---

## Request identity (`RequestIdentity`)

`{ orgId, userId }` is serialised into query params via `identityParams`
(`?org_id=…&user_id=…`). Some endpoints prefer this over deriving from the
bearer; both paths exist in the upstream because the facade can decode the
bearer when it needs to. The frontend treats the values as informational
and never invents them — `<AuthGate>` only mounts the app shell when
`auth.identity` is non-null.

---

## 401 recovery (dev only)

```
                  fetch /v1/* → 401
                       │
        assertOk() ── invokes _onUnauthorized
                       │
                   ┌───┴─────────────────────────────┐
            DEV?  │                                  │  PROD?
                  ▼                                  ▼
       mintDevBearer(activePersona)           drop bearer → anonymous
       refresh /v1/auth/session               (LoginScreen)
       on success: stay authenticated
       on failure: drop bearer → anonymous
```

The recovery path lives in [`AuthContext.tsx`](../../src/features/auth/AuthContext.tsx) `_devReauthAndRestoreSession`.
Production builds tree-shake `mintDevBearer` (every caller is guarded by
`import.meta.env.DEV`), so the prod path collapses to "drop bearer → anonymous".

---

## Error model

`assertOk(response)` is the only place where `Response → Error` happens:

| Response      | Behaviour                                                                                                                                                               |
| ------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `2xx`         | Return (caller awaits `.json()`)                                                                                                                                        |
| `401`         | Invoke `_onUnauthorized(response)` then `throw new UnauthorizedError(detail)`. Tests assert error class, **not** message text (see "Why a class, not a message" below). |
| Other 4xx/5xx | Parse `{"detail": "..."}` out of the body (FastAPI/Starlette shape); fall back to verbatim body for non-JSON errors; `throw new Error(message)`.                        |

### Why a class, not a message

`AuthContext.refresh` uses `err instanceof UnauthorizedError` to detect a 401
— sniffing the message string was brittle and broke once the facade started
returning `{"detail": "Missing bearer token"}` (no "401" or "unauthor"
substring). Every API helper routes 401 through `assertOk`, so the class
discriminator is reliable.

---

## HTTP helpers

The helpers are intentionally thin. Every one of them:

1. Appends `identityParams(identity)` (and any extra query keys) to the URL
2. Attaches `correlationHeaders()` (plus `content-type` for write methods)
3. Awaits `assertOk` (which routes 401s through the handler and parses error JSON)
4. Returns the JSON body or `void`

| Helper                       | Method | Identity in query?                               |
| ---------------------------- | ------ | ------------------------------------------------ |
| `httpGet<T>(path, identity)` | GET    | yes                                              |
| `httpPost<T>(path, body)`    | POST   | no — body carries it (e.g. `org_id` / `user_id`) |
| `httpPostQuery<T>(...)`      | POST   | yes                                              |
| `httpPatchQuery<T>(...)`     | PATCH  | yes                                              |
| `httpPutQuery<T>(...)`       | PUT    | yes                                              |
| `httpDelete(path, identity)` | DELETE | yes                                              |

Multipart uploads (`avatarApi`) and OAuth callback URLs build their own
`fetch` calls — they still attach `correlationHeaders()` so the bearer and
request id travel together.

---

## What this layer does NOT do

- Caching, retry, or deduplication. Each helper is a one-shot fetch.
  Component-level memoisation lives in feature hooks (`useResource`,
  `useConnectors`, etc.).
- Token refresh. The bearer is treated as opaque; rotation happens
  upstream and the frontend rediscovers identity via `/v1/auth/session`
  on 401.
- Cookie sessions. The browser never speaks cookies on the `/v1` surface
  — only bearer headers. (Local dev exception: `devIdp.ts` posts to
  `/v1/dev/identity/mint` with `credentials: "same-origin"` because that
  request **mints** the bearer; it doesn't depend on one.)
