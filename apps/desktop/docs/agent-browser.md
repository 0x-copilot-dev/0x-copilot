# Agent browser (AC8) — read-only foundation

This document describes the AC8 agentic-browser **foundation** as implemented in
`apps/desktop/main/browser/` and `services/ai-backend/src/agent_runtime/capabilities/browser/`.
It is the safe, read-only core: navigate, inspect (accessibility tree), and
screenshot, behind deny-by-default egress, isolated profiles, and a supervised
worker. **Downloads, uploads, form submits, and every other side effect are
deferred** — the seams are noted below.

> Security note: the Chromium process sandbox is **not** a VM or kernel-isolated
> execution environment. AC8 reduces SSRF/rebinding and ambient-authority risk;
> it does not claim Cowork-style hypervisor isolation.

## Feature gate

The whole subsystem is opt-in behind a single environment flag read once at boot:

```
RUNTIME_ENABLE_DESKTOP_BROWSER=1
```

Fail-closed parsing (`main/browser/feature-gate.ts`): only `1/true/yes/on/enabled`
enable it. When off, the worker never spawns, the broker never binds, and the
local MCP card is absent — every browser call fails closed. It is **off by
default**.

## Process topology

```
runtime_worker (ai-backend)
  -> DesktopBrowserMcpProvider          capabilities/browser/desktop_browser_provider.py
  -> Electron-main browser broker       main/browser/browser-broker.ts   (authenticated loopback)
  -> supervised worker child            main/browser/browser-supervisor.ts
  -> Playwright + bundled Chromium      main/browser/browser-engine.ts, browser-session.ts, worker/index.ts
  -> loopback egress policy proxy       main/browser/network-policy-proxy.ts (+ egress-policy.ts)
```

- **Electron main** owns process spawn, health, restart, version pin, teardown,
  the loopback broker, profile catalog/leases, and the (deferred) consent/run
  binding. It never imports Playwright.
- **Worker child** owns Playwright objects, the accessibility snapshot, element
  refs, screenshot capture, and the egress proxy. It has no Electron APIs, no
  backend/connector credentials, and no arbitrary host filesystem.
- **Renderer** is unprivileged and receives no broker URL/token, profile path,
  cookie, or page body.

## Controls implemented in this foundation

- **Deny-by-default egress** (`egress-policy.ts`, `network-policy-proxy.ts`):
  https-only top-level; metadata / `.local` / single-label / IP-literal hosts
  denied; a denied-range table for loopback, private, link-local, CGNAT,
  multicast, unspecified, benchmarking, documentation, reserved, IPv4-mapped
  IPv6, and cloud metadata across dotted/integer/octal/hex/IPv6 spellings. The
  proxy **resolves DNS itself, checks every resolved address, pins one permitted
  numeric address, and dials that** — defeating DNS rebinding — and re-checks on
  reconnect. Only `:443` and approved exact-origin hosts may `CONNECT`.
- **Typed read-only tools only** (`tool-schemas.ts`, `browser-session.ts`):
  `browser_navigate`, `browser_snapshot`, `browser_wait`, `browser_screenshot`,
  `browser_close`. No generic eval/JS/selector/coordinate/CDP escape hatch.
  Element refs are generation-bound; a navigation or fresh snapshot invalidates
  prior refs (`browser_element_stale`). Snapshots omit input values and are
  depth/node bounded.
- **Profile isolation** (`profile-store.ts`): ephemeral by default; persistent
  profiles are explicit, `0700`, derived from opaque ids (not workspace names),
  bound to exactly one workspace, with at most one automation lease
  (`browser_profile_busy`). Cross-workspace/version reopen is denied.
- **Supervised worker** (`browser-supervisor.ts`): spawn → health probe →
  version pin (a mismatch is fatal → `browser_unavailable`); crash restart with
  exponential backoff; crash-loop → unavailable; teardown escalates
  SIGTERM → SIGKILL and reaps the Chromium/crashpad/proxy descendants.
- **Authenticated broker** (`browser-broker.ts`): loopback-only, per-boot CSPRNG
  bearer (constant-time), protocol header, POST+JSON only, browser-metadata
  (CORS) rejection, and per-request binding — audience, single-use nonce +
  request id, and short expiry — so a wrong audience / expired / replayed
  envelope is rejected.
- **Screenshot staging** (`staging.ts`): screenshots land in a per-run staging
  directory outside any profile and are returned by opaque reference; the move
  to the AC4 object store is deferred.

## Deferred seams

- **Consent + run binding**: Electron main composes `BrowserRunBinding` (profile,
  approved origin policy, approval id) at consent time and injects it before the
  worker dispatches. The AI client currently sends only `{tool, arguments}`; the
  binding-injection point is `browser-broker.ts` → worker port.
- **Main ↔ worker action transport**: the supervisor stands up the child, proxy,
  and engine; wiring the broker's `BrowserWorkerPort` to the child's RPC is the
  next slice.
- **Downloads / uploads / side-effecting actions**: not exposed. Their action
  classes and error codes exist in `protocol.ts` so the policy layer is total.
- **Factory wiring**: the ai-backend exposes `build_browser_mcp(config)` (returns
  a provider only under `single_user_desktop` with a broker configured, else
  `None`). `execution/factory.py` is intentionally not edited; it consumes this
  seam.
- **AC4 object-store move, AC5 upload grants, takeover mode, retention/legal
  hold**: deferred to later slices.

## Testing

```
# desktop capability suite (fake engine + injected DNS/dialer — no real browser)
cd apps/desktop && npx vitest run main/browser

# ai-backend provider suite (fake broker via httpx MockTransport)
cd services/ai-backend && .venv/bin/python -m pytest tests/unit/agent_runtime/capabilities/browser
```
