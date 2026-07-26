# Connectors Unification — implementation plan

**Status:** Draft · **Branch:** `claude/connectors-unification` · **Base:** `1a044e21`
**Design source:** _0xCopilot App v3_ — `ConnectorsSurface` (`tools/design-parity/design-kit/app-v3/copilot-app.jsx:43-71`), `ConnectModal` (`copilot-flows.jsx:186-268`), `connCard()` (`copilot-workspace3.jsx`)
**Parity harness:** `tools/design-parity/surfaces/tools/anchors.json`
**Product spec:** the surface/flow rationale this plan implements

---

## 1. The problem, stated once

A user asks one question — _what can my agent reach, and what will it be allowed to do?_ — and the product answers it from three places that disagree.

| Definition                | Where                              | Carries                                                              | Serves                           |
| ------------------------- | ---------------------------------- | -------------------------------------------------------------------- | -------------------------------- |
| `ConnectorCatalogEntry`   | `connectors/catalog.yaml`          | slug, name, description, icon                                        | Tools destination "Available"    |
| `CatalogEntry`            | `mcp_catalog.py:DEFAULT_CATALOG`   | ↑ + url, transport, auth_mode, brand, default_scopes                 | Composer pill, `/v1/mcp/catalog` |
| `DesktopConnectorProfile` | `connectors/desktop_profiles.yaml` | ↑ + permissions[required_for], tools[risk, approval], callback_modes | Desktop IPC only                 |

Nine advertised slugs, thirteen seeded servers, **three in common**. Slack, Salesforce and Google Calendar are advertised with no endpoint behind them; Linear, Sentry, Asana, Plaid, PayPal, Square, Zapier, Intercom and both Cloudflare servers are installable but appear on no destination.

### 1.1 Root cause — this is not "two lists"

Read the three types as a sequence and the actual shape appears: **each is a strict superset of the one above it.** `catalog.yaml` adds nothing to `CatalogEntry` except six slugs with no endpoint. `desktop_profiles.yaml` adds the only thing that describes what a connector may _do_.

So there is one concept — a connector — modelled as three unrelated types, resolved by three different code paths, with no owner for the identity that ties them together. Every symptom is downstream of that:

- **dead cards** — because nothing forbids advertising a slug that cannot resolve to an endpoint;
- **invisible servers** — because the destination reads the list that lacks them;
- **stub OAuth** — because `/v1/connectors/*` grew as a parallel surface instead of a projection, so it needed its own OAuth wiring and never got it;
- **no mute UI** — because discovery filters against a catalog no surface owns.

Fixing the drift by syncing the files is a bandaid: it restores agreement today and re-drifts on the next connector. **The fix is to delete the concept of a second catalog.**

### 1.2 What is already right (do not rebuild)

- `desktop_profiles.yaml`'s **reconciler** (`profile_catalog.DesktopProfileCatalog`) already fails closed on orphan cards, non-HTTPS endpoints and write tools missing risk/approval metadata. It is the correct model, scoped to one host by accident of delivery order.
- `ConnectorAvailability` already enumerates the states a card needs (`available`, `preview`, `admin_setup_required`, `tenant_disabled`, `unsupported_by_policy`, `tool_contract_mismatch`, `temporarily_unavailable`).
- `list_suggestible_connectors` already filters correctly on installed / paused / `discoverable` / per-user mute.
- `ConnectorConsentCard` (on `claude/tool-card-design-compare-3b5237`) already implements the four-state consent card to v3, with server-derived trust clauses and omit-rather-than-invent semantics.
- `PostgresConnectorsStore` **exists** (`connectors/store.py:528`). Only the wiring is missing — `app.py:1781` unconditionally constructs `InMemoryConnectorsStore()`.

---

## 2. Target architecture

**One definition, one resolver, N projections.**

```
                  ConnectorProfile          ← the only definition of a connector
                  ├── identity     slug · display_name · description · brand
                  ├── transport    endpoint · transport · auth_mode · callback_modes
                  ├── capability   permissions[required_for] · tools[risk, approval]
                  └── lifecycle    release_stage · discoverable · verified_at
                          │
                  ConnectorRegistry.resolve(org, user)     ← the only resolver
                          │  computes availability; never reads a second file
                          ▼
                  ResolvedConnector[]  (profile + availability + installed? + enabled_here?)
                          │
        ┌─────────────────┼──────────────────┬────────────────────────┐
        ▼                 ▼                  ▼                        ▼
  Tools destination   Composer pill     Agent suggestion        Settings
  all, with state     filter(installed) filter(!installed       defaults + mutes
                                          ∧ discoverable
                                          ∧ ¬muted)
```

### 2.1 Invariants the architecture enforces

1. **A connector that cannot resolve to an endpoint cannot be rendered as installable.** Generalised from the desktop reconciler; enforced at boot, not at request time.
2. **Availability is computed, never authored.** No file contains the word "available"; a row is available because a profile resolved.
3. **Every surface is a projection of one resolve() call.** A surface may filter. A surface may not consult a different source.
4. **Consent copy is server-derived or omitted.** Already the `ConnectorConsentCard` contract; the registry becomes its supplier so the omit-rather-than-invent rule has a single upstream.

### 2.2 The identity decision (the load-bearing one)

Installed rows are keyed `server_id`, and the two catalogs mint different ids: `seed:<slug>` versus `desktop:google:gmail`. Unifying naively renumbers ids and orphans every existing installation.

**Do not renumber. Demote `server_id` to an installation detail and promote `connector_slug` to the identity.**

- The registry keys on `connector_slug` — stable, human-meaningful, already the join key both YAMLs use.
- The store gains a `connector_slug` column, backfilled from the existing id patterns (`seed:<slug>` → `<slug>`; profile-owned ids → their declared `connector_slug`).
- Product logic keys on slug forever after. `server_id` remains whatever it historically was, per installation.

This is what makes the migration additive instead of destructive, and it removes the class of bug where two surfaces compute the same connector's id differently.

---

## 3. Phases

Ordered so each phase is independently shippable and leaves the tree better than it found it. Phase 0 exists so the later phases have a failing test to make pass.

### Phase 0 — Make the failure visible

A boot-time conformance check asserting invariant (1): every advertised slug resolves to an installable server.

**It fails on `main` today** — three orphans — and that is the deliverable. A guardrail added after the fix only documents it; added before, it defines "done".

- `services/backend/tests/test_connector_catalog_conformance.py`
- Extend `profile_catalog`'s existing fail-closed loader rather than writing a second validator.

**Done when:** the test names the three orphan slugs and CI is red for a reason everyone agrees with.

### Phase 1 — Make the web destination real

Unblocks the surface without touching the catalog model. Smallest diff, largest user-visible delta.

| Change                                                                                    | File                       |
| ----------------------------------------------------------------------------------------- | -------------------------- |
| Set `app.state.connector_oauth_start` → `McpRegistryService.start_auth`                   | `backend_app/app.py`       |
| Set `app.state.connector_oauth_callback` → `complete_auth`, then `write_through_from_mcp` | `backend_app/app.py`       |
| Construct `PostgresConnectorsStore` when a DSN is configured                              | `backend_app/app.py:1781`  |
| Delete `_default_oauth_start`                                                             | `connectors/routes.py:731` |

Deleting the stub is part of the phase, not a follow-up: a fallback that fabricates an `auth.example` URL is worse than a 500, because it fails silently and looks like a product bug rather than a wiring bug.

**Done when:** connect → OAuth → the connector appears in Connected on web, and the row survives a restart.

### Phase 2 — Collapse to one registry

1. Widen `DesktopConnectorProfile` → `ConnectorProfile`; drop the desktop-only naming and gating.
2. Move `DEFAULT_CATALOG`'s thirteen entries into profile form. They already carry endpoint + auth + brand; they gain capability metadata (which is also the moment their write tools get risk/approval declarations they currently lack).
3. Delete `connectors/catalog.yaml`. Its three real slugs are already profiles; its three orphans become either a verified endpoint or an explicit `coming_soon` lifecycle.
4. `ConnectorRegistry.resolve()` becomes the only reader.
5. One endpoint — `GET /v1/connectors/catalog` — replaces `/v1/mcp/catalog` for product surfaces. Keep `/v1/mcp/*` for MCP-protocol concerns; it stops being a product catalog.

**Done when:** Phase 0's test passes, `grep -r DEFAULT_CATALOG` returns only the registry, and the composer and the destination render the same set.

### Phase 3 — Identity migration

Per §2.2. Additive migration, backfill, then a follow-up that forbids new product code from reading `server_id`.

**Done when:** an existing install created before the change still resolves, proven by a migration test seeded with both historical id shapes.

### Phase 4 — Discovery lifecycle

The behaviour behind the card that already exists.

- **Connecting mid-run never restarts the run.** Restarting re-emits work the user is reading and re-spends tokens. The connector arms the next turn; the card converts to an explicit _"Retry that step with Linear"_ affordance. This is the contract `ConnectorConsentCard`'s `connected` state is currently missing.
- **Mute lands on the card** (`onDeny` → persist the existing per-user override), because that is where the intent forms. Settings gets the reversible list.
- **`RUNTIME_CONNECTOR_SUGGESTIONS`**: `off` · `unblock_only` (default) · `always`, read by `list_suggestible_connectors`, surfaced in Settings → Tools.

**Done when:** a muted connector is never suggested again, and connecting mid-run visibly changes what the next turn can do.

### Phase 5 — Permission escalation

Connection and permission are different axes; only the first has a journey. The profile model already carries `required_for: read|draft|write` and per-tool `risk`/`approval` — this exposes what the server already knows, layered under the existing global approval posture.

---

## 4. Risks

| Risk                                                  | Mitigation                                                                                                              |
| ----------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------- |
| **Orphaning installed connectors** on identity change | §2.2 — additive slug column, backfill, never renumber. Migration test with both historical shapes.                      |
| **Phase 2 regresses desktop**, which works today      | Desktop already consumes the reconciler; Phase 2 widens its input, not its contract. Desktop journeys run before merge. |
| **The three orphan slugs are load-bearing marketing** | `coming_soon` lifecycle keeps the card, drops the lie. Product decides per slug; the code stops caring.                 |
| **`/v1/mcp/catalog` has external consumers**          | Keep it serving MCP-protocol concerns. Only product surfaces migrate. Grep before deleting.                             |
| **Scope creep into the approvals redesign**           | `ConnectorConsentCard` is consumed, not modified. If it needs changes, they land on its own branch first.               |

---

## 5. Verification

Per the house rule that a setting existing is not the same as the runtime obeying it:

- **Phase 0 test** is the regression gate for the whole program.
- **Design parity**: `tools/design-parity/surfaces/tools/anchors.json` already maps design↔live for both `default` and `connect` states. Its `live: null` rows are real absences reported HIGH — run it before and after Phase 2.
- **Live desktop journey** (`tools/desktop-journeys/`): connect a connector through the real packaged app, restart, confirm it persists — the same harness that verified the tool-budget work.
- **Web**: the Connected tab is currently, provably empty. A journey that connects and reloads is the honest proof Phase 1 landed.

---

## 6. Sequencing

Phase 0 → 1 ship together as the first PR: a guardrail plus the wiring that makes the destination real. Phase 2 → 3 are one program and should not be split across releases, because a half-migrated identity is worse than either end state. Phases 4 and 5 are independent and can be scheduled by product priority.
