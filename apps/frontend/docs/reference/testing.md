# Testing

What gets tested, where, and how. The frontend has focused Vitest coverage
for pure logic and stream parsing, plus typecheck + build coverage.

See also:

- [features/chat-surface-invariants.md](../features/chat-surface-invariants.md) — invariants pinned by tests
- [architecture/04-streaming.md](../architecture/04-streaming.md) — SSE protocol-error coverage
- `src/features/chat/chatModel/citationStore.invariant.test.ts` — the dual citation store guard

---

## Commands

```bash
npm run typecheck --workspace @0x-copilot/frontend
npm run test      --workspace @0x-copilot/frontend
npm run build     --workspace @0x-copilot/frontend
```

A single file or test:

```bash
cd apps/frontend
npx vitest run src/features/chat/chatRunState.test.ts
npx vitest run src/features/chat/chatRunState.test.ts -t "planning indicator"
```

`npm run test` at the repo root uses `--if-present` and folds the
frontend suite in automatically.

---

## Where tests live

Tests live next to the code, **never** in a top-level `tests/` directory.

| Pattern                         | What it covers                                             |
| ------------------------------- | ---------------------------------------------------------- |
| `<module>.test.ts(x)`           | Pure module — reducer, helper, hook                        |
| `<screen>.integration.test.tsx` | Multi-component flow (e.g. `Sidebar.integration.test.tsx`) |
| `<reducer>.invariant.test.ts`   | Cross-reducer consistency guards (citation store, etc.)    |

The chat reducers in `src/features/chat/chatModel/` are the most heavily
covered area; that's intentional — they are the contract between the SSE
stream and the rendered surface.

---

## Test boundaries

- **Mock at the API layer.** Replace `src/api/*` calls in tests; never
  import backend modules.
- **Use `@0x-copilot/api-types` for fixtures.** UI assumptions stay
  aligned with service contracts because every payload is shape-checked at
  type-check time.
- **Browser-only tests live in `apps/frontend`.** Shared package tests
  belong to the package that owns the code.
- **Pinned invariants stay pinned.** The chat-surface invariants doc lists
  every behavior that has its own test. If you change the underlying code,
  update the test in the same change rather than weakening the invariant.

---

## What to add when

| Behaviour grows in                | Add                                                                                   |
| --------------------------------- | ------------------------------------------------------------------------------------- |
| `chatModel/*` reducer             | unit + invariant test under same folder                                               |
| `src/api/*` client                | contract test for happy path + 401 + malformed body                                   |
| SSE parser                        | protocol-error test for malformed JSON + invalid envelope (see `agentApi.test.ts`)    |
| New screen branch / loading state | component test asserting the visible state, error path, and disabled actions          |
| OAuth or magic-link callback flow | component / integration test simulating the URL shape; component reads the URL itself |

End-to-end tests for OAuth callback and streaming chat are deferred until
the local multi-service stack is reliable enough to be CI-stable.

---

## Manual smoke

If you ship a UI change without a test runner, at minimum run
`typecheck` + `build`, then verify in `npm run dev`:

1. App shell renders, no console `[app-error]` entries.
2. Chat screen creates or resumes a conversation via `/v1/agent/*`.
3. Connectors screen loads MCP servers via `/v1/mcp/*`.
4. OAuth callback returns to a stable state (chat or settings) and the
   address bar shows the canonical URL (not `/mcp/oauth/callback?…`).
5. Settings screen renders all sections without errors.
6. Vite proxy log (`[vite-proxy]`) shows only `200`/`304` for `/v1/*`
   during the flow.
