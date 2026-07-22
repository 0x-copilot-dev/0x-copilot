# PRD-I — Connector write-through hardening

**Status:** Draft · **Owner:** platform · **Packages:** `services/backend`,
`apps/desktop` (wiring only) · **Follows:** PR #193 (PR-E.3 write-through) ·
**Blocked by:** — · **Blocks:** PRD-J J2 (live-PG verification covers I3's adapter)

## 1. Context & problem

PR #193 made `/v1/connectors` the single honest read model over catalog
connectors **and** custom MCP servers by wiring the designed
`write_through_from_mcp` substitution point. Three gaps were consciously
deferred in that PR and are now due — each is a _completion_ of the shipped
architecture, not new surface:

- **P-I1 — Internal MCP auth-start doesn't write through.** The ai-backend-driven
  start (`/internal/v1/mcp/servers/{id}/auth/start` + the internal test-token
  upsert) leaves the connector row at its prior status until `complete_auth`
  converges it. A chat-driven connect therefore shows a stale row (not
  `pending`) for the duration of the OAuth round-trip — dishonest read model
  during the one window the user is watching it.
- **P-I2 — SSE delivery is poll-sliced, not immediate.** The sync MCP handlers
  publish via `InMemoryConnectorActivityBus.publish_nowait`
  (`connectors/sse.py:131`), which appends without waking async waiters;
  consumers see events within the ≤5 s poll slice. The web `ConnectModal`
  custom-add closes on the `connector.created` envelope — today that close can
  lag up to 5 s. The projects bus shares the same semantics.
- **P-I3 — No durable ConnectorsStore.** The connectors read model is
  `InMemoryConnectorsStore` only — rows (and their audit trail) vanish on
  restart, while the MCP registry itself persists. After a desktop restart the
  read model silently re-diverges from the MCP truth until the next mutation:
  the exact split-brain PR #193 exists to prevent.

## 2. Goals / Non-goals

**Goals**

- G1 — Every MCP mutation path, including the internal ones, write-throughs an
  honest status transition.
- G2 — Bus events wake SSE waiters immediately when an event loop is available;
  the poll slice remains the fallback, never the primary.
- G3 — A `PostgresConnectorsStore` at parity with the hardened
  `PostgresProjectsStore` pattern (audit-chain signing + RLS session-var
  stamping from day one), env-selected, wired for desktop.

**Non-goals**

- NG1 — New endpoints, event kinds, or wire-shape changes (contract frozen).
- NG2 — Re-architecting the bus (in-memory per-process is correct for the
  desktop topology; this is a wakeup-latency fix).
- NG3 — Migrating the projects bus is a same-pattern option (FR-I2.3) — do it
  only if it is a mechanical reuse of the same helper, else defer.

## 3. Functional requirements

### I1 — internal-route write-through

- **FR-I1.1** — `/internal/v1/mcp/servers/{id}/auth/start` write-throughs
  `status=pending, status_reason=auth_pending` via the existing
  `_connector_write_through` glue (`backend_app/app.py:336`), identical
  discipline (post-commit, log-and-continue).
- **FR-I1.2** — The internal test-token upsert path write-throughs its resulting
  auth state (connected when a valid token lands). Audit `action` values follow
  the taxonomy in `connectors/__init__.py`.
- **FR-I1.3** — Tests: internal auth-start flips the row to pending; the
  complete-auth listener still converges to connected; tenant isolation holds.

### I2 — immediate SSE wakeup

- **FR-I2.1** — `InMemoryConnectorActivityBus` gains a loop binding
  (`bind_loop(loop)` called from the app lifespan startup). When bound,
  `publish_nowait` schedules waiter wakeup via `loop.call_soon_threadsafe`;
  when unbound (unit tests, non-server contexts) behavior is unchanged
  (poll-slice pickup). No public API change for subscribers.
- **FR-I2.2** — Tests: a subscriber awaiting the stream receives a published
  envelope in well under the poll slice (assert < 1 s with a real loop);
  unbound-bus behavior unchanged; no cross-tenant wakeup leakage.
- **FR-I2.3** _(optional, only if mechanical)_ — Apply the same binding to the
  projects bus; identical tests.

### I3 — durable connectors store

- **FR-I3.1** — `PostgresConnectorsStore` implements the full `ConnectorsStore`
  protocol against `connectors/schema.sql`. Check whether the connectors tables
  are in the migration chain (post-#165 squashed baseline); if absent, add the
  migration (+ rollback + MANIFEST.lock) rather than relying on module-local
  schema application.
- **FR-I3.2** — Hardened from day one, mirroring the projects adapter
  (PR #182): per-tenant audit-chain signing (seq / prev_hash / signature /
  key_version via the shared audit-chain primitives) and RLS session-var
  stamping on connection acquisition.
- **FR-I3.3** — Store selection follows the existing env-switch pattern;
  `desktop_app.py` selects Postgres (durable) like the projects store;
  in-memory remains the `create_app` default for tests/dev.
- **FR-I3.4** — Tests: protocol conformance across in-memory + Postgres
  (fake-conn Python paths, mirroring `test_projects_store_selection.py`);
  desktop composition asserts the PG store; write-through over the PG store
  round-trips; signing chain links; RLS SET statements captured. Live-PG SQL
  execution is PRD-J J2's job.

## 4. Non-functional requirements

- **NFR-I.1** — Write-through failure discipline unchanged: the primary MCP
  transaction never fails because of read-model or bus errors (lock-in test
  exists; keep it green).
- **NFR-I.2** — No new latency on the MCP mutation request path > O(ms) (the
  wakeup is `call_soon_threadsafe`, the signing is per-row local crypto).
- **NFR-I.3** — Tenant isolation provable by test at every new seam (internal
  routes, wakeup, PG store).

## 5. Architecture (principal-engineer notes — no bandaids)

- **One glue, no forks.** I1 reuses `_connector_write_through` — if the internal
  route can't reach it cleanly, refactor the glue's home so it can; do not
  duplicate projection logic.
- **Loop binding at composition, not discovery.** The bus must not sniff for a
  running loop at publish time (`asyncio.get_event_loop()` in threads is the
  classic bandaid). The lifespan owns the loop; it binds the bus once. Unbound =
  legacy semantics, explicitly.
- **The adapter is a peer, not a port-in-progress.** I3 lands with the same
  invariants as the projects adapter (signing, RLS, transaction composition via
  the store's `transaction()` contextvar pattern) — a second unhardened adapter
  would recreate the exact debt PR #182 just paid off.

## 6. PR breakdown

- **PR-I.1** — I1 internal-route write-through + tests (S).
- **PR-I.2** — I2 loop binding + wakeup + tests (S/M; +projects bus only if
  mechanical).
- **PR-I.3** — I3 PG adapter + migration + selection + tests (M/L).

## 7. Definition of done

- [ ] All FR tests green; full backend connectors+mcp subset green; no
      regressions (1820-suite spot-check).
- [ ] Failure-discipline lock-in test still green.
- [ ] `connectors/__init__.py` taxonomy updated for any new audit actions.
- [ ] STATUS.md hardening section updated.
