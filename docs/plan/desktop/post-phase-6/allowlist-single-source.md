# Post-Phase-6 — Allowlist Single Source of Truth

## Problem

The tier-2 adapter allowlist was defined in two places that could drift:

- TypeScript (Phase 6A) — `apps/desktop/main/adapters/ast-allowlist.ts`
  - `ALLOWED_IMPORTS` (modules + named specifiers)
  - `FORBIDDEN_GLOBALS` set (identifier-reference rejection)
  - Syntax-aware checks for `eval`, `Function`, `__proto__`, dynamic `import()`, `require()`, prototype writes
- Python (Phase 6B) — `services/ai-backend/src/agent_runtime/capabilities/render_adapter_generator/capability.py`
  - `_ImportAllowlist.ALLOWED` (modules only)
  - `_ForbiddenPattern.TOKENS` (identifier regex rejection)
  - `_ForbiddenPattern.LITERAL` (substring rejection for `new Function`, `import(`, `require(`)

If the TS side grew a new forbidden global and Python did not, the codegen would emit code the desktop rejects — wasted round trip and confusing failure. If Python were stricter, TS could admit code the backend would never have produced.

## Fix

Move the allowlist into `packages/service-contracts/src/copilot_service_contracts/adapter_allowlist.json` — the same shared-contracts package both runtimes already use. Both sides load the JSON at module load:

- TypeScript: `packages/api-types/src/adapterAllowlist.ts` re-exports `ADAPTER_ALLOWLIST` from JSON via `resolveJsonModule`. The 6A scanner now imports `ADAPTER_ALLOWLIST` from `@0x-copilot/api-types` and derives its working sets from it.
- Python: `copilot_service_contracts.adapter_allowlist.load_adapter_allowlist()` reads the JSON via `importlib.resources`. The 6B `_ForbiddenPattern` / `_ImportAllowlist` classes now derive their class-level tuples / frozensets from the loaded data at module import.

## Union strategy

The JSON ships the **union** of what 6A and 6B previously enforced. Drift in either direction is a security risk; widening to the union strictly tightens both sides and prevents silent admission of code the other side would reject.

### `allowed_imports`

| module                      | source                                                                          | resolution                                                                                                                                                                                                        |
| --------------------------- | ------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `react`                     | TS lists `createElement, Fragment, useState`; Python only lists the module name | Use the **TS named-specifier list** — Python previously enforced no named-specifier check, so taking the stricter TS list narrows nothing the backend was producing and locks the named-specifier discipline now. |
| `react-dom`                 | TS lists module with empty named-specifier list (effectively a tombstone)       | Keep empty array — anything imported from `react-dom` will fail the named-specifier check, matching today's TS behaviour. Python previously did not whitelist `react-dom`, so this is a no-op for codegen.        |
| `@0x-copilot/design-system` | TS lists 15 named primitives; Python lists module only                          | Use the **TS named-specifier list**.                                                                                                                                                                              |

### `forbidden_globals`

Identifier names that are rejected when referenced. Union of both sides plus `location` (in the task spec, neither side; safe addition):

```
window, document, fetch, XMLHttpRequest, WebSocket, EventSource,
localStorage, sessionStorage, navigator, history, location, crypto,
process, global, globalThis,
child_process, fs, net, http, https, path, os,
Buffer, setImmediate, clearImmediate, require
```

| name             | TS had it | Python had it | Resolution                                  |
| ---------------- | --------- | ------------- | ------------------------------------------- |
| `localStorage`   | no        | yes           | union ⇒ include                             |
| `sessionStorage` | no        | yes           | union ⇒ include                             |
| `navigator`      | no        | yes           | union ⇒ include                             |
| `history`        | no        | yes           | union ⇒ include                             |
| `location`       | no        | no            | task spec ⇒ include (safe)                  |
| `net`            | yes       | no            | union ⇒ include                             |
| `http`           | yes       | no            | union ⇒ include                             |
| `https`          | yes       | no            | union ⇒ include                             |
| `path`           | yes       | no            | union ⇒ include                             |
| `os`             | yes       | no            | union ⇒ include                             |
| `crypto`         | yes       | no            | union ⇒ include (Python now rejects it too) |
| `Buffer`         | yes       | no            | union ⇒ include                             |
| `setImmediate`   | yes       | no            | union ⇒ include                             |
| `clearImmediate` | yes       | no            | union ⇒ include                             |

### `forbidden_syntax`

Identifiers handled by **syntax-aware** AST checks on the TS side and by the Python regex check. These three names ride alongside `forbidden_globals` in the Python loader (regex-based) and are matched structurally by the TS scanner's existing `CallExpression` / `NewExpression` / `MemberExpression` handlers:

- `eval` — TS detects via `CallExpression(callee.name === "eval")`; Python detects via identifier regex
- `Function` — TS detects via `Function(...)` call and `new Function(...)`; Python detects via the `new Function` literal substring
- `__proto__` — TS detects via `MemberExpression(property.name === "__proto__")`; Python did **not** previously check this — adding it to the JSON tightens the Python auditor (existing 25 tests still pass; no fixture uses `__proto__`)

`__proto__` is the only **behaviour change** beyond consolidation, and it is strictly tighter on the Python side. We document it here rather than hide it; future tier-2 fixtures must continue to avoid `__proto__`.

### `budget_ms`

`100` — matches the smoke render-executor budget used in `apps/desktop/main/adapters/smoke-render-executor.test.ts` and reflected in the PRD's §9.5 Q3 render-budget guidance. Currently advisory in this spec; not consumed by either auditor yet. Encoded so future tier-2 timing checks reference one number.

## Migration steps applied

1. Wrote `packages/service-contracts/src/copilot_service_contracts/adapter_allowlist.json` with the union.
2. Added Python loader `packages/service-contracts/src/copilot_service_contracts/adapter_allowlist.py`.
3. Exposed the JSON via `setuptools` package-data so `importlib.resources` finds it.
4. Added TS loader `packages/api-types/src/adapterAllowlist.ts` plus re-export from `packages/api-types/src/index.ts`.
5. Added `@0x-copilot/api-types` to `apps/desktop/package.json` dependencies (desktop did not consume it before).
6. Updated 6A `ast-allowlist.ts` to derive `ALLOWED_IMPORTS` and `FORBIDDEN_GLOBALS` from the loaded data; AST scanner logic unchanged.
7. Updated 6B `capability.py` so `_ForbiddenPattern.TOKENS` and `_ImportAllowlist.ALLOWED` are derived from the loaded JSON; auditor logic unchanged.
8. Added one canary test on each side asserting the loaded values match a soft snapshot — anyone editing the JSON sees a visible signal in both runtimes' test suites.

## How to add a new rule

One PR:

1. Edit `packages/service-contracts/src/copilot_service_contracts/adapter_allowlist.json`.
2. Update the canary tests (`packages/api-types/src/adapterAllowlist.test.ts` and `services/ai-backend/tests/unit/agent_runtime/capabilities/render_adapter_generator/test_adapter_allowlist_loader.py`) if the change crosses a snapshot threshold.
3. Both sides pick the change up at next module import — no other code change required.

Changing the allowlist is security-sensitive and requires the same review level as Phase 6A/6B core changes.
