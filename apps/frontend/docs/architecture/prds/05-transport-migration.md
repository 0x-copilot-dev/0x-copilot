# PRD: Migrate `api/*` raw `fetch` to `getAppTransport().request`

**Status:** Draft → In implementation
**Owner:** Frontend platform
**Related audit:** [05-dry-audit.md §10](../05-dry-audit.md)

## Problem

There are two ways to make an HTTP request in `apps/frontend/src/api/*`:

```ts
// Path A — the "modern" path (transport singleton).
return getAppTransport().request<TRes>({ method: "POST", path, body });

// Path B — the "legacy" path (raw fetch + helpers).
const response = await fetch(path, {
  method: "POST",
  headers: jsonHeaders(),
  body: JSON.stringify(body),
});
await assertOk(response);
return (await response.json()) as TRes;
```

Bearer/correlation/401 handling go through the same `transport.ts`
singleton in both paths (because `jsonHeaders()` reads
`getAuthBearer()` from the same module). But every new behaviour we
want to add — request-level retry, telemetry, timeouts, content-type
negotiation, cancellation propagation — has to be implemented twice
unless we converge on one path.

Eight modules still use Path B:

- [api/meApi.ts](../../src/api/meApi.ts) — 16 endpoints
- [api/authApi.ts](../../src/api/authApi.ts) — already partially uses helpers
- [api/workspaceApi.ts](../../src/api/workspaceApi.ts)
- [api/workspaceMfaApi.ts](../../src/api/workspaceMfaApi.ts)
- [api/mfaApi.ts](../../src/api/mfaApi.ts)
- [api/avatarApi.ts](../../src/api/avatarApi.ts)
- [api/skillsApi.ts](../../src/api/skillsApi.ts)
- [api/mcpApi.ts](../../src/api/mcpApi.ts)
- Plus [features/auth/devIdp.ts](../../src/features/auth/devIdp.ts) — the
  only fetch caller outside `api/*`, in violation of frontend CLAUDE.md.

## Goals

1. One way to make a typed HTTP request from `api/*`.
2. No call-site changes — function signatures stay identical.
3. Avatar upload (multipart `FormData`) keeps working; transport must
   stay or get a multipart-friendly path.

## Non-goals

- Removing `correlationHeaders()` / `jsonHeaders()` / `assertOk()` —
  external callers (devIdp, tests) may still need them.
- Adding retry / timeout / cancellation behaviour. Those become possible
  after this lands, but are tracked separately.
- Converting SSE streams. Streaming already routes through
  `getAppTransport().subscribeServerSentEvents` (see streaming PRD).

## Design

`apps/frontend/src/api/http.ts` already exports thin typed wrappers
around `getAppTransport().request`:

```ts
httpGet<T>(path, identity, extra?)        // GET + identity query params
httpPost<T>(path, body)                    // POST without identity gating
httpPostQuery<T>(path, body, identity)     // POST + identity query
httpPatchQuery<T>(path, body, identity)
httpPutQuery<T>(path, body, identity)
httpDelete(path, identity, extra?)
```

These cover ~90% of api/\* endpoints. Add two more for the remaining
shapes:

```ts
// JSON request, no identity gating, custom method (DELETE/PUT body cases).
httpJson<T>(method, path, body?): Promise<T>;

// Multipart POST for avatar upload.
httpMultipart<T>(path, form: FormData): Promise<T>;
```

`httpMultipart` does not pass through `request()` (transport always
JSON-serialises `body`). It calls `getAppTransport()` to read the
bearer + 401 handler, then constructs the raw `fetch` itself. One
shared multipart helper, not eight.

### Per-module migration patterns

Replace this:

```ts
const response = await fetch(path, { headers: correlationHeaders() });
await assertOk(response);
return (await response.json()) as T;
```

With:

```ts
return httpGet<T>(path, /* identity */, /* extras */);
// or, when no identity is gated:
return httpJson<T>("GET", path);
```

Replace this:

```ts
const response = await fetch(path, {
  method: "POST",
  headers: jsonHeaders(),
  body: JSON.stringify(payload),
});
await assertOk(response);
return (await response.json()) as T;
```

With:

```ts
return httpPost<T>(path, payload);
// or, when identity is needed:
return httpPostQuery<T>(path, payload, identity);
```

DELETE without body → `httpDelete(path, identity)` (already exists).
DELETE with body → use the new `httpJson("DELETE", path, body)`.

### Avatar upload

`avatarApi.uploadAvatar(form: FormData)` keeps its callsite signature
and switches to `httpMultipart`. The function inside uses
`getAuthBearer()` + raw `fetch` (no JSON-stringify) and calls
`notifyUnauthorized` on 401 to keep the same 401-handling guarantee.

### `devIdp.ts`

The two `fetch(/v1/dev/...)` calls move into a new
`api/devIdpApi.ts` module so the CLAUDE.md rule "all HTTP clients live
in `src/api/*`" holds. `devIdp.ts` keeps only the substrate-agnostic
persona-slug persistence (`loadActivePersonaSlug`,
`persistActivePersonaSlug`).

## Migration plan

One file at a time, smallest first to build confidence:

1. **`api/avatarApi.ts`** — 2 endpoints, uses `httpMultipart` + `httpDelete`.
2. **`api/workspaceMfaApi.ts`** — 2 endpoints, vanilla GET/PUT.
3. **`api/mfaApi.ts`** — 6 endpoints, vanilla GET/POST/DELETE.
4. **`api/skillsApi.ts`** — small surface, paths already known.
5. **`api/workspaceApi.ts`** — moderate.
6. **`api/mcpApi.ts`** — 3 fetch sites.
7. **`api/meApi.ts`** — largest (16 endpoints).
8. **`features/auth/devIdp.ts`** — extract to `api/devIdpApi.ts`.

After each file: `npm run typecheck`. The function signatures don't
change so consumers don't move.

## Validation

- `npm run typecheck`, `npm run build`.
- Existing unit tests for each api module pass unchanged (they spy on
  `globalThis.fetch`, which is what `getAppTransport().request()`
  ultimately calls).
- Manual: log in, save profile, enroll TOTP, upload avatar.

## Risks

- **Tests that spy on `fetch`** still see the call, but the
  `Request.headers` shape changes from a `Record` to whatever
  `WebTransport` builds. Snapshot tests on headers may need an update.
  Audit before each migration.
- **Multipart** — `httpMultipart` is the only new code; cover it with
  one test that asserts the bearer header is attached and the body is
  not JSON-stringified.
- **Cancellation** — current raw `fetch` callers don't pass `signal`.
  Neither does this migration (signature unchanged). A future PR can
  thread signals through.

## Rollback

Per-file revert.
