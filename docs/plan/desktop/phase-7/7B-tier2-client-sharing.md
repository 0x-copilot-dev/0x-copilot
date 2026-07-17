# Phase 7.B: tier2-client-sharing

## Vision

Close the loop between a tenant's locally-generated tier-2 adapter and other tenants. Per PRD §9.5.3:

- A locally-generated adapter that has held up under real use (zero render errors over N=10 sessions + zero user-reported issues) is anonymized and submitted to the server-side review queue (7A) as a candidate. Submission is idempotent: the same `(scheme, version)` cannot be submitted twice from the same client.
- On app start, the desktop fetches the registry's promoted (allowlisted) adapters and installs them locally — subject to **the same** Q1 → Q2 → Q3 quality gate as locally generated adapters (PRD §9.5.2: "downloads are pre-fetched via 7B, AST-scanned, persisted, _then_ loaded"). A promoted adapter is not trusted just because the server promoted it.
- A tenant-level opt-out is the strongest setting in this surface. When set, downloaded adapters are uninstalled and future starts skip the download. Opt-out also blocks harvest (the tenant chose not to participate in the sharing pool).

This phase ships three pure-logic modules under `apps/desktop/main/adapters/` with all I/O injected — `fetch` for HTTP, a `KeyValueStore` for state, the existing lifecycle audit log for the harvest trigger, and the existing registry-host + quality-gate as the install path. They are wired into `apps/desktop/main/index.ts` so download runs once on app-ready (post-auth) and harvest runs as an observer over the lifecycle audit log.

## Status

- Status: in-progress
- Agent slug: `phase-7-tier2-client-sharing`
- Branch: `desktop/phase-7-tier2-client-sharing`
- Worktree: `.claude/worktrees/agent-a804d88e64aa1516e`
- Created: 2026-05-17

## Scope

**In scope** (files this agent owns):

- `docs/plan/desktop/phase-7/7B-tier2-client-sharing.md` — this file.
- `apps/desktop/main/adapters/types.ts` — port shapes consumed from Phase 6 (lifecycle audit log reader, registry-host install/uninstall, quality-gate runner, AST scan). Defined here as the **contract** Phase 7B builds against; Phase 6 modules implement them. If a 6-prefix file has already merged with the same exported types, the orchestrator de-dupes.
- `apps/desktop/main/adapters/harvest.ts` — pure module + observer factory.
- `apps/desktop/main/adapters/harvest.test.ts` — unit tests.
- `apps/desktop/main/adapters/download.ts` — pure module + boot hook.
- `apps/desktop/main/adapters/download.test.ts` — unit tests.
- `apps/desktop/main/adapters/opt-out.ts` — pure module + side-effects via injected KV.
- `apps/desktop/main/adapters/opt-out.test.ts` — unit tests.
- `apps/desktop/main/index.ts` — wire-up (call sites only; auth gate, app-ready bootstrap).

**Out of scope** (do NOT touch):

- `services/backend/**` — Phase 7A backend (this agent calls its routes via HTTP).
- `apps/frontend/**` and `services/backend-facade/**` — Phase 7C admin UI.
- Sandbox / loader internals — Phase 6A.
- Codegen — Phase 6B.
- Quality gate Q1 / Q2 / Q3 internals — Phase 6D. Phase 7B **calls** the gate; it does not reimplement it.
- Lifecycle audit log schema — Phase 6C. Phase 7B **subscribes** to it.

**Coordination with Phase 6:**

Phase 6 (sandbox + codegen + quality-gate + lifecycle) is a prerequisite per PRD §5 ("Depends on: Phase 6 merged"). As of branch creation, Phase 6 has not landed in the worktree base — the adapters directory is empty. Phase 7B is built against the **contracts in `types.ts`** and includes an internal in-memory shim for tests; Phase 6's concrete implementations will satisfy these contracts when they land. If Phase 6 lands first, no production code in 7B changes — only the `// TODO: replace with @phase-6-impl import` lines in `index.ts` are swapped. Tests are entirely contract-level and do not depend on the shim.

## Functional requirements

### Harvest

- [ ] FR-H1: `Harvester` observes lifecycle audit-log events. The events of interest (per PRD §9.5.3): `adapter.session.completed { scheme, version, render_error_count }` (a render-clean session bumps the counter) and `adapter.user_issue.reported { scheme, version }` (resets the eligibility flag for that version).
- [ ] FR-H2: Per `(scheme, version)`, the harvester tracks `sessions_observed`, `render_error_count` (total render errors across observed sessions; non-zero disqualifies forever), `user_issue_count` (total user issues; non-zero disqualifies forever), and `submitted_at` (idempotency).
- [ ] FR-H3: Submission criteria — submit when **all** of: `sessions_observed >= 10`, `render_error_count === 0`, `user_issue_count === 0`, `submitted_at === null`, opt-out is false. PRD §9.5.3 wording: "Zero render errors over N=10 sessions + zero user-reported issues."
- [ ] FR-H4: Submission reads the adapter source from `{userData}/adapters/{scheme}-v{version}.js` (path-template per PRD §3.4) and POSTs to `/v1/adapter_registry/candidates` with body:
  ```json
  {
    "scheme": "...",
    "version": 7,
    "layout": "form|table|kanban|definition-list",
    "source": "...adapter source string...",
    "harvest_metrics": {
      "sessions_observed": 10,
      "render_error_count": 0,
      "user_issue_count": 0,
      "generated_at": "ISO",
      "generator_model": "..."
    }
  }
  ```
  `layout`, `generated_at`, and `generator_model` come from the adapter's `metadata` field (Phase 6B codegen-backend writes them at install).
- [ ] FR-H5: **Anonymization gate**: before submit, re-run the AST allowlist scan on the source (defense in depth against any per-tenant string literal that might have slipped through Phase 6D's install-time scan). On failure, the candidate is **dropped** and a `adapter.harvest.rejected { scheme, version, reason: 'ast_scan_failed' }` lifecycle event is logged. The submission is **not** retried for that version; `submitted_at` is set to a sentinel `'rejected_local'` so the harvester does not loop.
- [ ] FR-H6: Idempotency — once `submitted_at` is set (success or local rejection), no further submission attempts for that `(scheme, version)`. Persisted in `{userData}/harvest-state.json` (atomic write via temp-rename) keyed by `scheme + '@' + version`. The Phase 6C audit-log SQLite table is the source of truth for session/error/issue counts; the JSON file is only the submission ledger.
- [ ] FR-H7: HTTP failures (5xx, network) are retried with bounded backoff (3 attempts, 1s/3s/10s); after exhaustion the candidate is **not** marked `submitted_at` (it stays eligible for the next opportunity). HTTP 4xx (the server rejected the candidate) **does** set `submitted_at` to a `'rejected_server'` sentinel.
- [ ] FR-H8: Opt-out (see §opt-out below) blocks all harvest activity — when opt-out is set, the harvester observer is a no-op for that tenant.

### Download

- [ ] FR-D1: On app-ready (after auth completes — needs a workspace bearer + base URL), call `GET /v1/adapter_registry/promoted` with the workspace bearer. Response shape (contract with 7A):
  ```json
  {
    "adapters": [
      {
        "scheme": "...",
        "version": 3,
        "layout": "form",
        "source": "...adapter source string...",
        "metadata": {
          "origin": "community",
          "generated_at": "ISO",
          "generator_model": "...",
          "schemaVersion": 1
        }
      }
    ]
  }
  ```
- [ ] FR-D2: If opt-out is set, the download phase is skipped entirely. No HTTP call. Any previously-installed promoted adapters were already uninstalled when opt-out was toggled on (see FR-O3).
- [ ] FR-D3: For each candidate, run the same install-time quality gate as locally-generated adapters — Q1 (schema), Q2 (AST allowlist), Q3 (smoke render). Reject any that fail; emit `adapter.download.rejected { scheme, version, reason: <quality_gate_code> }` to the lifecycle audit log. The gate is **non-bypassable** — server promotion does not skip it.
- [ ] FR-D4: Adapters that pass the gate are installed via the lifecycle's `registry-host.installAdapter` (Phase 6C contract). The install path persists the source under `{userData}/adapters/{scheme}-v{version}.js` and hot-swaps into the surface registry. `metadata.origin = 'community'` carries through (it came from the server; Phase 7A's promote step set it).
- [ ] FR-D5: A separate ledger tracks **which adapters were installed via download** (vs. locally generated), persisted at `{userData}/promoted-installs.json`. Keyed by `scheme + '@' + version`. The ledger is what `opt-out` consults when it needs to uninstall promoted adapters; the surface registry itself does not distinguish origin at resolution time.
- [ ] FR-D6: Network failure on the registry list call is logged but **not** fatal — the app continues. Locally-installed tier-1 and tier-2 adapters are unaffected. A subsequent app-start retries.

### Opt-out

- [ ] FR-O1: `getOptOut(tenantId): Promise<boolean>` — reads from the injected KV under key `adapter-registry.opt-out.{tenantId}`. Default false.
- [ ] FR-O2: `setOptOut(tenantId, optedOut: boolean): Promise<void>` — writes the new value under the same key.
- [ ] FR-O3: Setting opt-out to `true` (transition from false→true) triggers a one-time uninstall sweep: for every entry in `promoted-installs.json` for this tenant, call `registry-host.uninstallAdapter(scheme, version)` and remove the ledger entry. Emit `adapter.optout.uninstalled { scheme, version }` per adapter. The transition is logged: `adapter.optout.enabled { tenantId }`.
- [ ] FR-O4: Setting opt-out to `false` (transition from true→false) **does not** auto-trigger a redownload mid-session. The next app-start runs the normal download path. The transition is logged: `adapter.optout.disabled { tenantId }`.
- [ ] FR-O5: The opt-out **wins over server policy**: even if the registry lists an adapter, the download module checks opt-out first and skips. Even if a harvest candidate is eligible, opt-out blocks submission.

### Wire-up

- [ ] FR-W1: `apps/desktop/main/index.ts` constructs a `Harvester` once at app-ready (with the lifecycle audit-log subscriber from Phase 6C) and starts the observer. The observer is a long-lived listener — the harvester is event-driven, not polled.
- [ ] FR-W2: `apps/desktop/main/index.ts` calls `runDownloadOnStart` once at app-ready, **after** the auth gate produces a workspace + bearer. In Phase 1 (today), auth is not yet wired; the call site uses a `getAuthContext()` injection that returns `null` and skips download. Phase 5 swaps in the real auth-gate. The skip is logged once at startup so it is visible.
- [ ] FR-W3: The wire-up exposes no new IPC channels. Opt-out toggle UI is a Phase 7C concern; the main-process module exposes a pure API the renderer can reach via IPC once 7C wires it.

## Non-functional requirements

- TypeScript strict. **No `any`.** Every interface field `readonly`. Type-only imports use `import type`.
- Functional style by default. The `Harvester` is a class because it owns mutable observer state and a teardown function — that matches the neighbor pattern (`TransportBridge`).
- Comments per PRD §6.1: default to none. Add one only where the WHY is non-obvious (e.g., the `'rejected_local'` / `'rejected_server'` sentinels' semantics).
- Substrate discipline: all I/O is injected — `fetch` (or `HttpFetch`), the KV, the audit-log subscriber, the registry-host, the quality-gate runner. The module under test never opens a real network connection.
- Persistence: `harvest-state.json` and `promoted-installs.json` write atomically — write to `*.tmp`, then `rename`. No partial writes after a crash.
- Anonymization: the AST-scan helper is reused, not reimplemented. The harvest pre-submit gate calls the same allowlist scanner Phase 6D ships.

## Interfaces consumed

```ts
// apps/desktop/main/adapters/types.ts (NEW — contract this agent depends on)

// Lifecycle audit-log event shape (subset relevant to 7B). Phase 6C owns
// the full union; 7B only cares about these four event kinds.
export type LifecycleEvent =
  | {
      kind: "adapter.session.completed";
      scheme: string;
      version: number;
      renderErrorCount: number;
    }
  | {
      kind: "adapter.user_issue.reported";
      scheme: string;
      version: number;
    }
  | {
      kind: "adapter.installed";
      scheme: string;
      version: number;
      origin: "agent-generated" | "community";
    }
  | {
      kind: "adapter.broken";
      scheme: string;
      version: number;
      reason: string;
    };

export interface LifecycleAuditLog {
  readonly subscribe: (handler: (e: LifecycleEvent) => void) => () => void;
  readonly emit: (e: LifecycleEvent) => void;
}

// Surface-registry install / uninstall facade (Phase 6C `registry-host`).
export interface RegistryHost {
  readonly installAdapter: (args: {
    scheme: string;
    version: number;
    source: string;
    metadata: AdapterMetadata;
  }) => Promise<void>;
  readonly uninstallAdapter: (scheme: string, version: number) => Promise<void>;
  readonly readAdapterSource: (
    scheme: string,
    version: number,
  ) => Promise<string>;
}

export interface AdapterMetadata {
  readonly origin: "agent-generated" | "community";
  readonly generatedAt: string;
  readonly generatorModel: string;
  readonly schemaVersion: number;
  readonly layout: "form" | "table" | "kanban" | "definition-list";
}

// Quality gate (Phase 6D). 7B calls this on every download candidate.
export type QualityGateOutcome =
  | { ok: true }
  | {
      ok: false;
      code: "schema" | "allowlist" | "smoke_render";
      detail: string;
    };

export interface QualityGate {
  readonly runAll: (args: {
    source: string;
    metadata: AdapterMetadata;
  }) => Promise<QualityGateOutcome>;
}

// AST allowlist scanner (Phase 6D). 7B's harvest re-runs this as defense
// in depth before submitting.
export interface AstAllowlistScan {
  readonly scan: (
    source: string,
  ) => { ok: true } | { ok: false; reason: string };
}

// Pure KV used for opt-out. Implementation in production is the existing
// safeStorage-backed adapter (Phase 5); tests inject an in-memory map.
export interface KeyValueStore {
  readonly get: (key: string) => Promise<string | null>;
  readonly set: (key: string, value: string) => Promise<void>;
  readonly delete: (key: string) => Promise<void>;
}

// Atomic file I/O for the harvest + promoted-installs ledgers.
export interface AdapterStateStore {
  readonly readJson: <T>(name: string) => Promise<T | null>;
  readonly writeJsonAtomic: <T>(name: string, value: T) => Promise<void>;
}

// HTTP shape — a Pick of fetch's interface so callers can substitute.
export type HttpFetch = (
  url: string,
  init: { method: string; headers: Record<string, string>; body?: string },
) => Promise<{ status: number; text: () => Promise<string> }>;
```

## Interfaces produced

```ts
// apps/desktop/main/adapters/harvest.ts
export interface HarvesterDeps {
  readonly auditLog: LifecycleAuditLog;
  readonly registryHost: RegistryHost;
  readonly astScan: AstAllowlistScan;
  readonly stateStore: AdapterStateStore;
  readonly http: HttpFetch;
  readonly registryBaseUrl: string;
  readonly bearer: () => string | null;
  readonly tenantId: string;
  readonly kv: KeyValueStore;
  readonly clock?: () => number;
}

export class Harvester {
  constructor(deps: HarvesterDeps);
  start(): Promise<void>;
  stop(): void;
}

// apps/desktop/main/adapters/download.ts
export interface DownloadDeps {
  readonly registryHost: RegistryHost;
  readonly qualityGate: QualityGate;
  readonly stateStore: AdapterStateStore;
  readonly auditLog: LifecycleAuditLog;
  readonly http: HttpFetch;
  readonly registryBaseUrl: string;
  readonly bearer: string;
  readonly tenantId: string;
  readonly kv: KeyValueStore;
}

export interface DownloadResult {
  readonly considered: number;
  readonly installed: number;
  readonly rejected: number;
  readonly skippedOptOut: boolean;
  readonly networkError: boolean;
}

export function runDownloadOnStart(deps: DownloadDeps): Promise<DownloadResult>;

// apps/desktop/main/adapters/opt-out.ts
export interface OptOutDeps {
  readonly kv: KeyValueStore;
  readonly registryHost: RegistryHost;
  readonly stateStore: AdapterStateStore;
  readonly auditLog: LifecycleAuditLog;
}

export function getOptOut(deps: OptOutDeps, tenantId: string): Promise<boolean>;
export function setOptOut(
  deps: OptOutDeps,
  tenantId: string,
  optedOut: boolean,
): Promise<void>;
```

## Open questions

- **Q1 — Tenant identity in main.** The harvester and download both need a tenant id. In Phase 7B's wiring, the tenant id is read from the auth context at app-ready (placeholder: null in Phase 1 → skip both). When auth lands (Phase 5), the same context surfaces `workspace_id` which doubles as `tenant_id` per PRD D10 ("1:1 with backend workspace"). Recorded so the orchestrator confirms 5A's auth context exposes a stable tenant identifier.
- **Q2 — Registry base URL.** 7A's backend lives behind backend-facade per CLAUDE.md ("Apps must call only the facade"). 7B's `registryBaseUrl` must point at the facade's `/v1/adapter_registry/*` proxy, not at backend directly. Wired from build-time config (existing pattern in `apps/desktop`).
- **Q3 — Opt-out scope (tenant vs. user).** PRD wording is "tenant-level". Phase 7B keys opt-out by `tenantId`, not `userId`. If product later wants per-user, change the key prefix; the module's API doesn't need to.
- **Q4 — Harvest cadence.** Event-driven (no poll). Submission triggers on the same audit-event tick that causes the counter to cross the threshold. The submission is awaited but the audit subscriber returns immediately so back-pressure doesn't block other listeners (in practice: kick off submission via a queued Promise the observer doesn't await).
- **Q5 — What if Phase 6 hasn't landed when 7B's PR opens?** The orchestrator merges 7B's `types.ts` contract as the source of truth and Phase 6 implements against it. If Phase 6 already shipped a different shape, the orchestrator's pre-merge step picks the canonical names and 7B's `types.ts` is replaced with a re-export. Recorded.

## Done criteria

- [ ] All FRs met.
- [ ] `npm test --workspace @0x-copilot/desktop` passes (existing 36 tests + new 7B tests).
- [ ] `npm run typecheck --workspace @0x-copilot/desktop` passes.
- [ ] `npm run lint --workspace @0x-copilot/desktop` passes — no new lint errors, no `any`.
- [ ] No new third-party dependency in `apps/desktop/package.json` (zod is already there for schema validation if needed).
- [ ] No imports outside the desktop app's deployable boundary (per `apps/desktop/eslint.config.mjs`).
- [ ] Wire-up in `apps/desktop/main/index.ts` is gated on auth (off in Phase 1; ready to flip in Phase 5).

## Notes for orchestrator review

- The three modules are deliberately tiny. Each is ~100–150 lines of pure logic; the bulk of the work is the contracts in `types.ts` and the tests that exercise the success-criteria / quality-gate / opt-out / idempotency rules.
- The biggest delta is **defense-in-depth anonymization**: the harvester does not trust Phase 6D's install-time AST scan as enough — it re-runs the scan at submit time, so any drift between install and submit (someone hand-edits the file on disk, a Phase 6B regression slips per-tenant string literals through, etc.) is caught before the source leaves the host. Same module, same allowlist, two enforcement points.
- Opt-out is the **strongest** setting in this surface, per the agent brief. It wins over both server promote and local harvest. The transition true→false does **not** auto-redownload mid-session — we trade a slight UX inconsistency for a much cleaner state machine. The next app-start picks up the new state.
- All three modules are tested with injected dependencies — no Electron, no real filesystem, no real HTTP. The tests run in the existing `vitest` node environment without needing a mock for `app.getPath`.
