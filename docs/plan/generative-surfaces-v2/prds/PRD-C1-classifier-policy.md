# PRD-C1 — ActionClassifier + Approval Policy sync

**Goal.** Give the runtime a layered, fail-closed read/write classification for every MCP
tool call (curated catalog → protocol annotations as untrusted hints → default = write),
and make the hold/auto-run decision resolve against **one** policy source of truth: the
existing Settings → Model & behavior → Approval Policy (`tool_use_policies` in backend)
plus a new per-connector write-policy override stored on the backend connector record.
The `action.classified` ledger event (stubbed `basis: default` by PRD-A3) starts carrying
the real basis, and the data needed for the global write-posture chip (FR-B5) is exposed.
No UI ships in this PR; PRD-C2 (gate cards) and PRD-D1 (staging) consume what this PR
leaves behind.

## Implementer brief

You are implementing this in a **fresh git worktree branched off `main`** of the
`0x-copilot` monorepo. All paths below are repo-relative. Run `make setup` once if the
service venvs do not exist. This PR touches **services/ai-backend** and
**services/backend** only (no TS packages, no UI).

Test commands you will use:

```bash
# ai-backend (new + touched suites)
cd services/ai-backend && .venv/bin/python -m pytest tests/unit/agent_runtime/actions/
cd services/ai-backend && .venv/bin/python -m pytest tests/unit/agent_runtime/capabilities/mcp/
cd services/ai-backend && .venv/bin/python -m pytest          # full suite before merge

# backend
cd services/backend && .venv/bin/python -m pytest tests/integration/api/test_mcp_write_policy_routes.py
cd services/backend && .venv/bin/python -m pytest tests/test_runtime_policies_route.py
cd services/backend && .venv/bin/python -m pytest             # full suite before merge

# migration manifest (CI fails without this) — run from repo root; regenerates each
# service's migrations/MANIFEST.lock. Add `--service backend` to limit to this service.
python tools/check_migration_manifest.py --write
```

Read these files first (in order):

1. `docs/plan/generative-surfaces-v2/02-sdr.md` — §3 boundaries, §5 event vocabulary (authoritative), §10 fail-closed invariants.
2. `docs/plan/generative-surfaces-v2/03-prds.md` — PRD-C1 summary; its DoD is a binding minimum.
3. `docs/plan/generative-surfaces-v2/prds/` — PRD-A1 + PRD-A3 (they define the event constants and emission seam you wire into).
4. `services/ai-backend/src/agent_runtime/capabilities/tools/permissions.py` — `ToolUsePolicyKind/Mode/Snapshot`, deployment defaults.
5. `services/ai-backend/src/agent_runtime/capabilities/tools/tool_use_enforcement.py` — how Approval Policy already changes runtime behavior; don't duplicate or break it.
6. `services/ai-backend/src/agent_runtime/api/user_policies_resolver.py` — how the snapshot reaches `AgentRuntimeContext.user_policies_json` at run-create.
7. `services/ai-backend/src/agent_runtime/capabilities/mcp/backend_provider.py` — `BackendMcpClient._tool_descriptor` (line 336): the only place raw wire tool dicts (with MCP `annotations`) are seen; dropped today.
8. `services/ai-backend/src/agent_runtime/capabilities/mcp/descriptor_registry.py` — the per-run ContextVar registry pattern you mirror for annotations.
9. `services/ai-backend/src/agent_runtime/capabilities/surfaces/builtin.py` — `server_slug`/`tool_slug` + the load-at-import JSON-data-file pattern you mirror for the catalog.
10. `services/backend/src/backend_app/routes/runtime_policies.py` — the `/internal/v1/policies/runtime` aggregate you extend.
11. `services/backend/src/backend_app/contracts.py` — `McpServerRecord` (~257), `McpServerResponse` (~520), `UpdateMcpServerRequest` (~507).
12. `services/ai-backend/CLAUDE.md` + `services/ai-backend/tests/CLAUDE.md` — engineering + test rules.

## Context

Generative Surfaces v2 re-founds the agent's work product on an explicit, typed **Work
Ledger**: every consequential runtime event (gates, classified actions, reads, staged
writes, decisions, applies, view derivations, usage) is a typed event on the existing
per-run event log; everything the user sees is a projection of that ledger. See
`../02-sdr.md` §2–§3 for the architecture and `../01-problem-and-requirements.md` §2B/§2C
for this PR's requirements: FR-C0 (layered classification, fail-closed), FR-B4 (write
policy synced with Settings Approval Policy, per-connector override), FR-B5 (posture
indicator data).

This PR is Wave C's first PR. Wave A already landed: PRD-A1 (event vocabulary contracts,
ledger-id format `r<short>·<seq>`), PRD-A2 (UsageMeter — irrelevant here, this PR makes
no LLM calls), PRD-A3 (ledger emission behind the `SURFACES_V2` runtime flag, including
an `action.classified` event whose `basis` is hardcoded to `default`). C1 replaces that
stub with a real classifier and builds the policy-resolution machinery that PRD-C2
(gates), PRD-D1 (staging), and the posture chip need. The classification catalog is
**data, not code** (SDR §12 risk table): per-connector JSON files, fixable without code.

Key existing fact: the protocol does not classify. `McpToolDescriptor`
(`.../capabilities/mcp/cards.py:345`) has no annotations field;
`BackendMcpClient._tool_descriptor` drops the wire `annotations` object
(`readOnlyHint`, `destructiveHint`, …) and hardcodes `risk_level=McpRiskLevel.MEDIUM`. Annotations
are optional and untrusted — per SDR §10 invariant 1, **annotations alone can never
grant auto-run; only catalog entries can**.

## Interfaces consumed / exposed

**Consumed (from earlier PRDs / existing code):**

- PRD-A1: the `action.classified` event-type value is `LedgerEventType.ACTION_CLASSIFIED`
  (`= "action.classified"`) in the `LedgerEventType` StrEnum
  (`agent_runtime/surfaces_v2/ledger_models.py`); the ordered value tuple
  `LEDGER_EVENT_TYPES` + `LEDGER_PAYLOAD_VERSION = 1` live in
  `copilot_service_contracts.work_ledger`. This PR adds **no** event type — it reuses A1's
  `action.classified` value at A3's emission site and only changes two payload fields.
  `VERIFY AT IMPL:` confirm the names as A1/A3 merged them (grep `ACTION_CLASSIFIED` under
  `services/ai-backend/src/agent_runtime/surfaces_v2/`).
- PRD-A3: the `SURFACES_V2` flag helper is `SurfacesV2Flag.enabled(environ)` in
  `agent_runtime/surfaces_v2/config.py`. The emission site is
  `WorkLedgerEmitter.on_tool_result` (`agent_runtime/surfaces_v2/emitter.py`) — the single
  method that today builds the `action.classified` payload with the hardcoded
  `Values.CLASS_UNKNOWN` / `Values.BASIS_DEFAULT` constants
  (`agent_runtime/surfaces_v2/constants.py`). The emitter is bound per-run in
  `RuntimeRunHandler._build_work_ledger_emitter` (`runtime_worker/handlers/run.py`),
  alongside the surface-generation scheduler bind. `VERIFY AT IMPL:` confirm as A3 merged
  them.
- Existing: `ToolUsePolicySnapshot` / `ToolUsePolicyKind` / `ToolUsePolicyMode`
  (`agent_runtime/capabilities/tools/permissions.py`), hydrated per-run from
  `GET /internal/v1/policies/runtime` via `HttpUserPoliciesResolver` onto
  `AgentRuntimeContext.user_policies_json["tool_use"]`.
- Existing: backend `tool_use_policies` storage + routes
  (`backend_app/routes/tool_use_policies.py`) — untouched; the single Approval Policy
  source of truth.

**Exposed (for later PRDs):**

- `ActionClassifier.classify(...)` + `EffectiveActionPolicyResolver.resolve(...)` (NEW)
  — consumed by PRD-C2 (gate display + park decision) and PRD-D1 (hold semantics).
- Backend `mcp_servers.write_policy` column; `write_policy` on `McpServerResponse`
  (FR-B5 posture data, rendered in C2); `PATCH /v1/mcp/servers/{id}` accepting it
  (Settings lane); NEW `PUT /internal/v1/mcp/servers/{server_id}/write-policy`
  (runtime lane, used by C2's gate resolution).
- `connector_write_policy` map on the `/internal/v1/policies/runtime` aggregate — the
  per-run snapshot lane for override resolution.
- The per-connector catalog JSON format + loader — extended per-connector over time.

## Design

### 1. Classification contracts (ai-backend, NEW package)

New package `services/ai-backend/src/agent_runtime/capabilities/actions/`. **This is the
home — do not relocate it.** PRD-C2 already references
`agent_runtime/capabilities/actions/classifier.py` by path; moving the modules under A3's
`surfaces_v2/` would break that consumer. The classifier is a **capability** (it classifies
MCP tool calls), it imports `server_slug`/`tool_slug` from
`capabilities/surfaces/builtin.py` and `ToolUsePolicy*` from `capabilities/tools/`, and it
is imported **by** the `surfaces_v2` emitter — the same one-way `surfaces_v2 → capabilities`
direction A3's emitter already uses (no cycle):

```python
# src/agent_runtime/capabilities/actions/contracts.py  (all NEW)
class ActionClass(StrEnum):
    READ = "read"; WRITE = "write"
    UNKNOWN = "unknown"   # legal wire value (SDR §5); the classifier never emits it —
                          # fail-closed collapses unknown → WRITE

class ClassificationBasis(StrEnum):
    CATALOG = "catalog"; ANNOTATION = "annotation"; DEFAULT = "default"

class CatalogActionKind(StrEnum):      # what catalog files may declare per op
    READ = "read"; WRITE = "write"; DESTRUCTIVE = "destructive"

class ClassifiedAction(RuntimeContract):
    connector: str                     # server_slug-normalized
    op: str                            # tool_slug-normalized
    action_class: ActionClass          # READ or WRITE (never UNKNOWN, see above)
    basis: ClassificationBasis
    catalog_kind: CatalogActionKind | None = None   # set iff basis == CATALOG

class ConnectorWritePolicy(StrEnum):   # per-connector override values (FR-B4)
    ASK_FIRST = "ask_first"; ALLOW_ALWAYS = "allow_always"

class EffectiveActionPolicy(RuntimeContract):
    classified: ClassifiedAction
    policy_kind: ToolUsePolicyKind     # axis used: read | write | destructive
    mode: ToolUsePolicyMode            # auto | ask | require | block (post-override)
    hold: bool                         # True unless mode == AUTO
    bypass: bool                       # True iff allow_always downgraded ask → auto
```

### 2. Catalog (data files, rung 1)

`src/agent_runtime/capabilities/actions/catalog_data/<connector>.json` — one file per
connector, launch set = the connectors covered by the builtin surface specs (SDR open
decision #3): `asana`, `atlassian`, `github`, `intercom`, `linear`, `notion`, `sentry`.
All twelve builtin-spec tools (`asana.list_tasks`, `atlassian.get_issue`,
`atlassian.search_issues`, `github.get_issue`, `github.get_pull_request`,
`github.list_issues`, `github.list_pull_requests`, `intercom.list_conversations`,
`linear.get_issue`, `linear.list_issues`, `notion.get_page`, `sentry.list_issues` — all
reads) MUST appear as `"read"`, plus each connector's common write ops as
`"write"`/`"destructive"`. File format:

```json
{
  "catalog_version": 1,
  "connector": "github",
  "operations": {
    "get_issue": "read",
    "list_issues": "read",
    "create_issue": "write",
    "delete_repository": "destructive"
  }
}
```

Loader `src/agent_runtime/capabilities/actions/catalog.py` (NEW class `ActionCatalog`)
mirrors `surfaces/builtin.py`'s load pattern: load + validate every file **once at import**
(malformed file ⇒ package unimportable — test-suite failure, never live degradation);
keys normalized via the existing `agent_runtime.capabilities.surfaces.builtin.server_slug`
/ `tool_slug`; duplicate `(connector, op)` raises; exact-match lookup only, no wildcards
(fail-closed). API: `ActionCatalog.lookup(connector, op) -> CatalogActionKind | None` +
`all_entries()` for tests.

**Module-level singleton (how the emitter gets it).** The "once at import" load binds a
process-wide instance — mirror `builtin.py`'s `_REGISTRY = load_builtin_specs(...)`
(builtin.py:107): `catalog.py` binds a module-level `ActionCatalog` and `classifier.py`
(§4) binds a module-level `ActionClassifier` over it. The emission site (§5) imports **that
singleton**; it never constructs `ActionCatalog()` or re-reads the JSON per tool call.
Because it is a module-level _instance_ (not a helper _function_), it satisfies the
"behavior lives in classes" rule. `surfaces/builtin.py` itself uses module-level helper
functions (`server_slug`, `lookup`, …) that predate that rule — do not copy that shape;
keep behavior on `ActionCatalog`/`ActionClassifier` methods and reuse only builtin.py's
importable `server_slug`/`tool_slug`.

### 3. Annotation capture (rung 2, untrusted hints)

New module `src/agent_runtime/capabilities/mcp/annotations.py`:

```python
class McpToolAnnotations(RuntimeContract):     # NEW; the 3 hints we model
    read_only_hint: bool | None = None         # readOnlyHint
    destructive_hint: bool | None = None       # destructiveHint
    idempotent_hint: bool | None = None        # idempotentHint

    @classmethod
    def from_wire(cls, raw: Mapping[str, object]) -> "McpToolAnnotations": ...
    #   Reads ONLY the three camelCase keys above; every other key
    #   (MCP spec ships `title`, `openWorldHint`, and vendors add more)
    #   is ignored; a non-bool value coerces to None. Never validate the
    #   raw wire dict against the model directly — RuntimeContract is
    #   `extra="forbid"`, so `model_validate(raw)` would RAISE on `title`
    #   / unknown keys and on the camelCase spelling. `from_wire` is the
    #   only entry point; the model stays `extra="forbid"`.

class McpToolAnnotationsRegistry:  # NEW; mirrors the McpDisplayRegistryContext
    # ContextVar bind/unbind/active/register/get *pattern* — but keyed on a
    # normalized COMPOSITE `(server_slug(server), tool_slug(tool))`, not on
    # tool_name alone. (McpDisplayRegistryContext keys by tool_name only and
    # documents a two-servers-same-tool-name collision; the composite key
    # avoids that and lets the classifier disambiguate by connector.)
    #   classmethods bind_for_run / unbind / active / register(server, tool, ann) /
    #   get(server, tool) — register + get both normalize via server_slug/tool_slug.
```

Capture point: `BackendMcpClient._tool_descriptor`
(`capabilities/mcp/backend_provider.py`, line 336) — build annotations via
`McpToolAnnotations.from_wire(tool["annotations"])` when `tool.get("annotations")` is a
mapping (skip otherwise) and register next to the existing
`McpDisplayRegistryContext.register(name, display)` call (line 369). Register the server
with `self.card.name` (the connector's slug — line 359 already reads it) and the tool with
`name`; both are normalized inside the registry.
**Server-identity alignment (why the composite key must normalize):** the register happens
here with `self.card.name`; the read (§5) happens with `parsed_input.server_name` (the
name the model passed to `call_mcp_tool`). Those two strings need not be byte-equal, so the
registry normalizes both sides through `server_slug` — the same normalization the catalog
and the `surface_uri` already use — so they resolve to one connector slug. If they still
miss, `get()` returns `None` ⇒ catalog/default ⇒ fail-closed (annotations only ever
_tighten_), so a miss is safe, never unsafe. Add a `test_tool_annotations` case pinning
`register(self.card.name, tool)` / `get(parsed_input.server_name, tool)` round-trip for a
seed-prefixed vs bare connector name.
**Do not add a field to `McpToolDescriptor`** — it is `extra="forbid"` (extends
`RuntimeContract`, confirmed) and its dumps reach model-visible listings; registry-only
capture keeps every existing payload byte-identical. Bind/unbind alongside the existing
`McpDisplayRegistryContext` bind in the worker run setup — mirror
`runtime_worker/handlers/run.py` line 374 (`McpDisplayRegistryContext.bind_for_run`) and
its unbind at line 574. Because `_tool_descriptor` runs _inside_ that bound context (the
display registry is populated the same way), the annotations registry is bound before any
descriptor is built.

### 4. Classifier + effective policy resolver

```python
# src/agent_runtime/capabilities/actions/classifier.py  (NEW)
class ActionClassifier:
    """Layered, fail-closed: catalog -> annotations (hints) -> default write."""
    def __init__(self, catalog: ActionCatalog) -> None: ...
    def classify(self, *, server: str, tool: str,
                 annotations: McpToolAnnotations | None) -> ClassifiedAction:
        # 1. catalog hit  -> class = READ if kind==READ else WRITE; basis=CATALOG
        # 2. annotations  -> read_only_hint is True  -> READ,  basis=ANNOTATION
        #                    destructive_hint is True -> WRITE, basis=ANNOTATION
        # 3. otherwise    -> WRITE, basis=DEFAULT   (FR-C0 fail-closed)

# src/agent_runtime/capabilities/actions/policy.py  (NEW)
class ConnectorWritePolicyOverrides:
    """Parsed from user_policies_json['tool_use']['connector_write_policy'] ({} on absence).
    Unknown values dropped (forward-additive, same discipline as ToolUsePolicySnapshot).
    Keys are normalized through `server_slug` on ingest AND `for_connector` normalizes its
    lookup arg the same way, so the backend map (keyed by the connector's `mcp_servers.name`
    slug, §6.6) aligns with `classified.connector` (also `server_slug`-normalized)."""
    @classmethod def from_user_policies(cls, user_policies_json: Mapping) -> "…"
    def for_connector(self, connector: str) -> ConnectorWritePolicy | None

class EffectiveActionPolicyResolver:
    def __init__(self, *, snapshot: ToolUsePolicySnapshot,
                 overrides: ConnectorWritePolicyOverrides) -> None: ...
    def resolve(self, classified: ClassifiedAction) -> EffectiveActionPolicy:
```

Resolution table (SDR §10 invariants 1 and 3 — encode exactly this):

| classified                                     | axis (`policy_kind`) | mode                           | notes                                                                   |
| ---------------------------------------------- | -------------------- | ------------------------------ | ----------------------------------------------------------------------- |
| READ, basis=CATALOG                            | `READ`               | snapshot read-axis mode        | the ONLY auto-run-eligible cell                                         |
| READ, basis=ANNOTATION                         | `WRITE`              | snapshot write-axis mode       | annotation never grants auto-run; label stays "read" for honest display |
| WRITE, basis=CATALOG, catalog_kind=WRITE       | `WRITE`              | snapshot write-axis mode       |                                                                         |
| WRITE, basis=CATALOG, catalog_kind=DESTRUCTIVE | `DESTRUCTIVE`        | snapshot destructive-axis mode |                                                                         |
| WRITE, basis=ANNOTATION (destructive_hint)     | `DESTRUCTIVE`        | snapshot destructive-axis mode | hint can only tighten                                                   |
| WRITE, basis=DEFAULT (unknown op)              | `WRITE`              | snapshot write-axis mode       | fail-closed default                                                     |

Then apply the per-connector override: `allow_always` downgrades **only** a `WRITE`-axis
`ASK` to `AUTO` (sets `bypass=True`); it never touches the `DESTRUCTIVE` axis, never
downgrades `REQUIRE`, and never overrides `BLOCK`. `hold = mode is not AUTO`.

### 5. Ledger event wiring (the `basis` swap)

SDR §5 vocabulary, verbatim — this PR fills `class` and `basis` truthfully:

```text
action.classified  {call_id, connector, op, class: read|write|unknown, basis: catalog|annotation|default}
```

At PRD-A3's emission site — `WorkLedgerEmitter.on_tool_result`
(`agent_runtime/surfaces_v2/emitter.py`), the single method that today emits
`action.classified` with `Values.CLASS_UNKNOWN` / `Values.BASIS_DEFAULT` behind
`SURFACES_V2` — replace the two hardcoded constants with a call to the module-level
classifier singleton (§2/§4). `on_tool_result` already receives `server_name` /
`tool_name` / `call_id`; add:
`classified = <module ActionClassifier>.classify(server=server_name, tool=tool_name, annotations=McpToolAnnotationsRegistry.get(server_name, tool_name))`,
then emit `class=classified.action_class`, `basis=classified.basis`. Pass the **raw**
`server_name`/`tool_name` (the classifier and registry normalize internally); the payload's
`connector`/`op` stay the slug-normalized values A3 already computes (identical to
`classified.connector`/`classified.op`). Emission stays flag-gated and best-effort
(try/except, log `[surfaces] action.classify_raised`, fall back to
`class=unknown`/`basis=default`, never fail the tool call — same discipline as A3's
`on_tool_result` and `CallMcpTool._attach_surface`). If the annotations registry is unbound
here, `get()` returns `None` and classification falls through to catalog/default — correct
and fail-closed.

**Scope note:** C1 wires the classifier only into this ledger-emission path. The
`EffectiveActionPolicyResolver` (§4) is **built + unit-tested but not wired into runtime
holding** in C1 — the actual hold/interrupt stays with the existing
`ToolUsePolicyEnforcer` (see Out of scope). C2/D1 consume the resolver.

Error behavior: catalog import errors fail the test suite (never live); classifier and
resolver are pure and total (no I/O, no exceptions for any string input); missing or
garbage `tool_use` policy JSON resolves to deployment defaults exactly as
`ToolUsePolicySnapshot` does today (never refuse a run on policy absence).

### 6. Backend: per-connector override storage + sync (open decision #4)

1. **Migration** `services/backend/migrations/0046_mcp_server_write_policy.sql` (+
   `.rollback.sql`; next free number is 0046):
   `ALTER TABLE mcp_servers ADD COLUMN write_policy TEXT NULL
CHECK (write_policy IN ('ask_first','allow_always'));` — NULL = no override.
   Regenerate `migrations/MANIFEST.lock`.
2. **Contracts** (`src/backend_app/contracts.py`): `write_policy: str | None = None` on
   `McpServerRecord`, `McpServerResponse` (wired in `from_record` ~544), and
   `UpdateMcpServerRequest` (validator restricts to the two values or `None`).
3. **Stores** (`src/backend_app/store.py`): `InMemoryMcpStore` (~226) stores the whole
   `McpServerRecord` object, so it persists `write_policy` automatically — **no change
   needed there**. `PostgresMcpStore` (~405) maps columns explicitly, so add `write_policy`
   in **four** places: the `INSERT` column list + `VALUES` (~440), the `UPDATE ... SET`
   clause (~468), `_server_params` (~784: `"write_policy": record.write_policy`), and
   `_row_to_server` (~812: `write_policy=cls._optional_str(row.get("write_policy"))`). The
   `SELECT *` reads already fetch the new column.
4. **Settings lane**: existing `PATCH /v1/mcp/servers/{server_id}` handler
   `update_server` (`app.py:1084`, `MCP_WRITE`) passes the field through
   `McpRegistryService.update_server` (`service.py:433`). In that service method, gate the
   new field on **`model_fields_set`**, not `is not None` — mirror the `oauth_client`
   branch (`service.py:447`, `if _Fields.OAUTH_CLIENT in request.model_fields_set:`), **not**
   the `display_name`/`enabled` branches. This is what lets `PATCH {"write_policy": null}`
   _clear_ the override (the DoD/test requires set **and** clear); an `is not None` guard
   would make `null` a no-op. Add a `_Fields.WRITE_POLICY` constant. The facade already
   forwards the PATCH body transparently — `update_mcp_server` (`backend_facade/app.py:325`)
   takes `payload: dict[str, object]` and passes it straight to `forward_json`, so no facade
   change is needed.
5. **Runtime lane (endpoint ships in C1; C2 calls it)**: NEW route in `app.py` beside
   `internal_start_auth` (`app.py:1272` precedent, an internal
   `/internal/v1/mcp/servers/{server_id}/…` route):
   `PUT /internal/v1/mcp/servers/{server_id}/write-policy`, body
   `{"write_policy": "ask_first" | "allow_always" | null}`, `RequireScopes(RUNTIME_USE)`.
   **Identity source:** take `org_id` / `user_id` as `Query(..., min_length=1)` params and
   pass them to `BackendServiceAuthenticator.internal_scoped_identity(request, org_id=…,
user_id=…)` — exactly as the aggregate route `get_runtime_policies` does
   (`routes/runtime_policies.py:124`). Under a valid service token the
   `x-enterprise-org-id`/`-user-id` **headers win** (dev falls back to the query params).
   The runtime caller (C2's `ConnectorWritePolicyClient`) must therefore send `org_id` /
   `user_id` as query params **alongside** the service-token headers — the same shape
   `HttpUserPoliciesResolver` already uses to call the aggregate route (params **and**
   headers). Keyed by the URL `{server_id}` (the row PK), it loads the row via
   `store.get_server(org_id=identity.org_id, server_id=…)` → **404** when `None` (covers
   both unknown server and cross-org, since the org filter yields `None` for another org's
   row).
6. **Aggregate**: `routes/runtime_policies.py` — add
   `connector_write_policy: dict[str, str] = {}` to `ToolUseSection` (`extra="forbid"`, so a
   declared field is required — confirmed at `runtime_policies.py:57`). **Key = each row's
   `record.name`** (already `normalize_skill_slug`-normalized, `contracts.py:294`), value =
   `record.write_policy`; **only non-NULL, `enabled` rows appear** (posture "Bypass on"
   folds over enabled rows). Do NOT key by `server_id` — the ai-backend looks the map up by
   `server_slug(server_name)`, which matches the `name` slug for the launch connectors, not
   the opaque `server_id`. Thread the map through `_compose_tool_use`
   (`runtime_policies.py:161`): `register_runtime_policies_routes` gains kwarg
   `mcp_store: object | None = None` (repo's injection pattern); `None` ⇒ empty map. Wire in
   `create_app` + `desktop_app.py` kwargs. Read rows with the store's
   `list_servers(org_id=…, user_id=…)` (records carry `write_policy` after step 3) — the
   same store call `McpRegistryService.list_servers` (`service.py:285`) makes. Confirmed
   inert for old runtimes: ai-backend's `ToolUsePolicyResolver` (`tool_use_enforcement.py:75`)
   reads the `tool_use` section via `.get(_Keys.WORKSPACE)` / `.get(_Keys.USER)`
   (lines 90–101), so an extra `connector_write_policy` sibling key is ignored by runtimes
   that don't know it; and `HttpUserPoliciesResolver` forwards the whole body verbatim
   (`user_policies_resolver.py:148–150`), so the key reaches `user_policies_json['tool_use']`
   unchanged for `ConnectorWritePolicyOverrides.from_user_policies`.

Posture data (FR-B5): "Bypass on" ⇔ any **enabled** server row has
`write_policy == "allow_always"`. Clients compute this from the `/v1/mcp/servers` list
response (now carrying `write_policy`); no new endpoint.

## Implementation plan

1. **Backend migration + records** — create
   `services/backend/migrations/0046_mcp_server_write_policy.sql` + `.rollback.sql`;
   regenerate `migrations/MANIFEST.lock`; modify
   `services/backend/src/backend_app/{contracts.py,store.py}`.
2. **Backend routes** — modify `services/backend/src/backend_app/app.py`
   (`update_server` passthrough + NEW internal write-policy route + aggregate wiring),
   `service.py` (`update_server` accepts the field), `routes/runtime_policies.py`,
   `desktop_app.py` (kwargs).
3. **ai-backend annotations** — create
   `services/ai-backend/src/agent_runtime/capabilities/mcp/annotations.py`; modify
   `.../capabilities/mcp/backend_provider.py` (`_tool_descriptor` capture) and
   `services/ai-backend/src/runtime_worker/handlers/run.py` (bind/unbind registry).
4. **ai-backend actions package** — create
   `services/ai-backend/src/agent_runtime/capabilities/actions/{__init__.py,contracts.py,catalog.py,classifier.py,policy.py}`
   and `catalog_data/{asana,atlassian,github,intercom,linear,notion,sentry}.json`.
5. **Wire the basis** — modify PRD-A3's `action.classified` emission site (path per
   VERIFY above) to call the classifier + annotations registry, behind `SURFACES_V2`.
6. **Tests** (next section), then full suites + migration-manifest check.

## Test plan

New/updated unit test files (ai-backend convention: `tests/unit/agent_runtime/<area>/`,
injectable `environ` dicts, no live network):

- `services/ai-backend/tests/unit/agent_runtime/actions/test_catalog.py` —
  `test_all_twelve_builtin_spec_tools_marked_read`, `test_duplicate_op_raises`,
  `test_malformed_catalog_file_breaks_import` (tmp-dir loader variant),
  `test_lookup_uses_slug_normalization` (`seed:linear` → `linear`).
- `services/ai-backend/tests/unit/agent_runtime/actions/test_action_classifier.py` —
  `test_unknown_op_classifies_write_basis_default` (**DoD**),
  `test_catalog_read_classifies_read_basis_catalog`,
  `test_readonly_hint_yields_read_basis_annotation`,
  `test_destructive_hint_yields_write_basis_annotation`,
  `test_catalog_wins_over_contradicting_annotation` (adversarial: catalog says write,
  `readOnlyHint: true` ⇒ WRITE/CATALOG), `test_classifier_never_returns_unknown_class`.
- `services/ai-backend/tests/unit/agent_runtime/actions/test_effective_policy.py` —
  `test_annotation_read_resolves_write_axis_held` (**DoD adversarial**: annotation-only
  read ⇒ `hold=True` under default policy; catalog read ⇒ `hold=False`),
  `test_allow_always_downgrades_only_write_ask` (destructive `REQUIRE`/`BLOCK`
  untouched, `bypass=True`), `test_missing_policy_json_uses_deployment_defaults`,
  `test_settings_mode_change_changes_resolution` (write=auto vs ask).
- `services/ai-backend/tests/unit/agent_runtime/capabilities/mcp/test_tool_annotations.py`
  — registry bind/get/unbind isolation; `_tool_descriptor` captures wire camelCase
  `annotations` and tolerates garbage (`"annotations": "lol"` ⇒ no entry); descriptor
  dump byte-identical to before (regression).
- Update PRD-A3's emission-site test — `basis` now reflects catalog/annotation/default;
  `SURFACES_V2` off ⇒ zero `action.classified` events (reuse A3's snapshot test).

Backend tests:

- `services/backend/tests/integration/api/test_mcp_write_policy_routes.py` (NEW; follow
  `test_surface_specs_routes.py`: `create_app(...)` + `TestClient`, dev posture via
  `monkeypatch.delenv("ENTERPRISE_SERVICE_TOKEN")`, identity query params) — PATCH
  sets/clears `write_policy`, round-trips on GET `/v1/mcp/servers`; invalid value ⇒ 422;
  internal PUT sets it (404 unknown server); org A's PUT cannot touch org B's server.
- Extend `services/backend/tests/test_runtime_policies_route.py` — aggregate carries
  `connector_write_policy` for overridden servers only; empty map when `mcp_store` is
  not wired (backward compat).
- Migration applies + rolls back via the existing `tests/test_migration_runner.py` harness.

**Live-smoke script** (dev stack; DoD "changes effective behavior without restart"):

1. `echo 'SURFACES_V2=true' >> services/ai-backend/.env`, then `make dev`;
   `export TOKEN=$(make dev-bearer)`.
2. Install + auth a catalog connector (`docs/dev-testing.md` recipes; facade `:8200` only).
3. Run a chat that calls a catalog read tool (e.g. linear `list_issues`); replay
   `GET :8200/v1/agent/runs/{run_id}/events`: expect `action.classified` with
   `class: "read", basis: "catalog"`; an uncataloged op yields `class: "write",
basis: "default"`.
4. In Settings → Model & behavior → Approval Policy flip the write axis (ask ⇄ auto);
   **without restarting any service**, start a new run and confirm the write-classified
   call's hold behavior changed (interrupt card appears/disappears).
5. `PATCH :8200/v1/mcp/servers/{id}` body `{"write_policy":"allow_always"}`; confirm it
   on `GET :8200/v1/mcp/servers`, and (service-lane check, direct `:8100` explicitly OK)
   `GET :8100/internal/v1/policies/runtime?org_id=..&user_id=..` shows
   `tool_use.connector_write_policy` carrying the override.
6. Unset the flag; rerun step 3; confirm zero `action.classified` events and a
   pre-C1-shaped event stream.

## Definition of done

From `03-prds.md` PRD-C1 (binding, never weakened):

- [ ] **Unknown op ⇒ class write (test)** — `test_unknown_op_classifies_write_basis_default` passes.
- [ ] **Annotation-only read ⇒ still held unless catalog says read (adversarial test)** — `test_annotation_read_resolves_write_axis_held` + `test_catalog_wins_over_contradicting_annotation` pass.
- [ ] **Changing Approval Policy in Settings changes effective behavior without restart (live)** — live-smoke step 4 (flip the write axis, confirm the interrupt card appears/disappears on a new run with **no** service restart) executed on the real stack, evidence in PR description.
- [ ] **Backend owns override storage; ai-backend consumes via internal API only** — override lives in `mcp_servers` (migration 0046); ai-backend reads it exclusively through `/internal/v1/policies/runtime` (no ai-backend DB access, no backend `src/` import — grep proves it).

Standard DoD:

- [ ] `services/ai-backend` and `services/backend` full suites green in their own venvs.
- [ ] Flags off ⇒ byte-identical: `SURFACES_V2` unset ⇒ event stream snapshot unchanged (A3 snapshot test green); no `write_policy` stored ⇒ every existing response byte-identical (assert in the routes test).
- [ ] No service-boundary violations (checklist in Guardrails).
- [ ] No new LLM call sites (classifier is deterministic; nothing to meter — state in PR description).
- [ ] Docs: update `../02-sdr.md` §3 ActionClassifier row if implementation diverges.
- [ ] `migrations/MANIFEST.lock` regenerated; migration + rollback tested.

(Not 🎨 — no UI in this PR, so the design-parity DoD does not apply.)

## Out of scope

- Gate UI, gate cards, park/resume, posture **chip** rendering (PRD-C2);
  `gate.opened`/`gate.resolved` events (C2 emits them — C1 only ships the storage +
  internal endpoint C2 will call).
- Staging/holding behavior changes at the interrupt seam; `write.staged` and decisions
  (PRD-D1/D2/D3). C1 resolves policy; it does not re-plumb `ToolUsePolicyEnforcer`.
- Catalog coverage beyond the launch seven connectors; wildcard/prefix matching.
- `connectors` read-model changes (`write_policy` is not mirrored there in this PR);
  facade `/v1/usage/*`, receipts, Sources (Wave E).

## Guardrails

- **Service boundaries are hard**: apps → facade only; ai-backend never imports
  `backend_app` (HTTP + `packages/service-contracts` constants only); backend never
  imports `agent_runtime`; no sibling `.venv` reuse, no cross-`src/` imports.
  Approval-policy **storage** is backend's; **evaluation** is ai-backend's — keep that
  split exactly.
- **Flag-off byte-identical**: with `SURFACES_V2` unset and no override stored, every
  event payload, tool listing, and HTTP response must be byte-for-byte today's output —
  which is why annotations are registry-captured, not descriptor fields.
- **ai-backend rules** (`services/ai-backend/CLAUDE.md`): Pydantic at every boundary
  (wire annotations are untrusted — validate, never trust); no module-level helper
  functions — behavior lives inside classes; no inline string keys — new payload/JSON
  keys go in `Keys`/`_Fields` constants classes; typed domain errors with safe public
  messages; never inject annotations/catalog data into model prompts.
- **Test rules** (`services/ai-backend/tests/CLAUDE.md`): hermetic unit tests — inject
  `environ` dicts and fakes; async tests are plain `async def`; no live network.
- **Backend conventions**: routes are closures inside `register_*_routes` / `create_app`
  (no `APIRouter`); injection kwargs typed `object | None`; `RequireScopes` on every new
  route (CI: `tools/check_route_scopes.py`); org/user always rebound from verified
  identity; migrations = numbered file + rollback + MANIFEST.lock (module-local
  `schema.sql` files are NOT in the chain — do not repeat the connectors split-brain).
- **Fail-closed, always** (SDR §10): unknown ⇒ write; annotations tighten, never loosen;
  `allow_always` never touches destructive/require/block; classifier errors must never
  fail or skip a tool call — they degrade to `basis: default`.

## Open questions

Genuinely-undecided design calls surfaced by the implementability pass (everything else was
determinable from the SDR / repo and is fixed inline above). Neither blocks C1's DoD.

1. **`read.executed` honesty for a write-classified call in Wave C.** A3's
   `WorkLedgerEmitter.on_tool_result` emits, unconditionally after `action.classified`, a
   `read.executed` event with summary `"auto-ran (read)"`. After C1, `action.classified`
   can carry `class: write` for a call that (in Wave C, before C2 gates / D1 stages) still
   executed through the existing enforcer — so the same call emits both `class: write` and a
   `read.executed`/`"auto-ran (read)"` beat. The `class` field on `action.classified` is
   truthful and is what E1's receipt fold reads, so this is cosmetic in the raw stream, but
   it reads oddly. **Decide:** leave `read.executed` firing verbatim in C1 (reconcile in
   D1, which owns the staging seam and would suppress/relabel the "ran" beat for staged
   writes), or gate its emission/summary on `classified.action_class` here. Recommendation:
   leave it to D1 — C1 explicitly does not re-plumb the interrupt seam — but the owner
   should confirm we accept the interim oddity rather than special-casing the summary now.

2. **What DoD item 3's live proof actually exercises.** C1 deliberately does **not** wire
   `EffectiveActionPolicyResolver` into runtime holding (that is C2/D1); the classifier only
   feeds the ledger. So live-smoke step 4 — "flip the write axis, confirm hold behavior
   changes without restart" — is satisfied by the **pre-existing** `ToolUsePolicyEnforcer`
   gating `call_mcp_tool` on the write axis (hydrated per-run by `HttpUserPoliciesResolver`),
   which C1 keeps working but does not add. **Confirm** that step-4 evidence of the existing
   enforcer is the intended proof of this DoD item (it is provable and not weakened), rather
   than an expectation that C1 route holding through the new resolver — the latter would pull
   C2's enforcement re-plumb into C1's scope.
