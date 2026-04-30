# Frontend Testing

The frontend has focused Vitest coverage for pure view-model logic and API
stream parsing, plus typecheck and build coverage.

## Current Checks

Run from the repository root:

```bash
npm run typecheck --workspace @enterprise-search/frontend
npm run test --workspace @enterprise-search/frontend
npm run build --workspace @enterprise-search/frontend
```

The root `npm run test` command uses `--if-present`; this app contributes its
Vitest suite through `apps/frontend/package.json`.

## Expected Test Shape

When frontend behavior grows beyond simple composition, add focused tests in
this order:

- Unit tests for pure view-model logic such as chat message projection and API
  payload shaping.
- Contract-oriented tests for streaming API clients, including malformed SSE
  JSON and invalid runtime event envelopes.
- Component tests for screens with branching states, loading behavior, error
  messages, and disabled actions.
- End-to-end tests for OAuth callback and streaming chat flows once the local
  multi-service stack is reliable.

## Test Boundaries

- Mock network calls at the frontend API layer instead of importing backend
  modules.
- Use `@enterprise-search/api-types` in test fixtures so UI assumptions stay
  aligned with service contracts.
- Keep browser-only tests inside `apps/frontend`; shared package tests belong in
  the package that owns the code.

## Manual Smoke

For UI changes without a test runner, at minimum run typecheck and build, then
manually verify:

1. App shell renders.
2. Chat screen can create or resume a conversation through `/v1/agent/*`.
3. Connectors screen can load MCP servers through `/v1/mcp/*`.
4. OAuth callback returns to a stable connector state.
5. Settings screen renders without directly depending on service internals.
