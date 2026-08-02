# MCP Tool-Policy Pipeline — Spec

Status: **P0 — contracts + spec (inert)** · Service: `services/ai-backend` ·
Master plan: [`../../../../docs/plan/mcp-langchain-migration/PLAN.md`](../../../../docs/plan/mcp-langchain-migration/PLAN.md)

> This spec governs the **generic, capability-agnostic tool pipeline** the MCP
> migration extracts. P0 is deliberately **inert**: it defines the vocabulary
> and Protocols in
> [`agent_runtime/capabilities/policy/contracts.py`](../../src/agent_runtime/capabilities/policy/contracts.py)
> and nothing is registered with the runtime. Behaviour lands in P1–P4 (see the
> master plan §5). Read the master plan first for the _why_; read this for the
> _contract each phase implements against_.

---

## 1. Goal

One refactor, three payoffs (master plan §0), all sequenced together:

1. **Shed protocol maintenance.** Replace the hand-rolled MCP transport /
   session / JSON-RPC / pagination with `langchain-mcp-adapters`. MCP becomes
   just a **tool source**; the layer goes back to "connect, list, call."
2. **Un-tangle the cross-cutting concerns.** Today permissions, read/write
   approval, retries, error taxonomy, streaming, and citations are welded into
   the MCP layer (permissions alone is enforced in **four** MCP sites). Extract
   them into a **capability-agnostic** pipeline that wraps MCP, builtins, and
   skills identically.
3. **Fix the approval model** (Move 1 + bypass + render≠approve, §3 below).

### Two production bugs this closes

- **Read gated as a write.** `get_issues` was staged as a "PROPOSED CHANGE"
  because it was not in the curated `linear.json` catalog; classification fails
  closed to WRITE. Move 1 (trust the connector's `readOnlyHint`) removes the
  per-op curation dependency structurally.
- **"Decision recorded. Waiting for the run ledger." hang.** Fire-and-return
  staging returns a normal tool result, the run completes and **seals its
  ledger**, and the approved effect's `effect.applied` event is causal — it can
  never append after the seal. A run-parking **interrupt** (GATE) approves and
  executes in the _same_ run, so nothing is orphaned.

---

## 2. Architecture — sources → descriptor → middleware → tool

Everything that is not "connect, list, call" is a cross-cutting concern in a
generic pipeline. Each **source** yields self-describing tools; **one** ordered
middleware stack wraps them all; the wrapped tool registers with the Deep Agent
under its own name (`linear.create_issue`) — **no `call_mcp_tool` gateway, no
`McpDispatcherUnwrap`**.

```
SOURCES  (produce self-describing tools)
  mcp     → langchain-mcp-adapters + CredentialProvider  ┐
  builtin → native tools                                 ├─ each yields (BaseTool, CapabilityDescriptor)
  skill   → skills                                       ┘

CapabilityDescriptor { urn, action, trust, scopes, source, connector_state }
    urn    = "mcp:linear:create_issue" | "builtin:fs:write"
    action ← MCP annotations (readOnlyHint/destructiveHint) or the builtin's tag
    trust  ← connector is authenticated / first-party (set by the source at load)

GENERIC MIDDLEWARE  (MIDDLEWARE_ORDER — outermost first; wraps EVERY tool)
  1 Policy      → ALLOW | GATE(interrupt) | DENY        # permissions + approval, unified
  2 Exec-policy → retry rules (NO auto-retry on WRITE), timeout, resumption off for writes
  3 Observe     → emit started/result/completed into the run ledger
  4 Error-map   → {safe_message, code, retryable}
  5 Citations   → result → sources
       ↓
  tool.ainvoke(args)     # library for MCP, native for builtin
```

### The contracts (P0)

All in
[`capabilities/policy/contracts.py`](../../src/agent_runtime/capabilities/policy/contracts.py):

| Name                                        | Kind                                 | Notes                                                                                                                                       |
| ------------------------------------------- | ------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------- |
| `Action`                                    | StrEnum `READ`/`WRITE`/`DESTRUCTIVE` | the approval axis; **stops collapsing DESTRUCTIVE→WRITE** (today's `actions.ActionClass` has no destructive member — `classifier.py:60-61`) |
| `Posture`                                   | StrEnum `MANUAL`/`BYPASS`            | value-identical to `FilesystemBypassMode` (`execution/filesystem_bypass.py:81-89`)                                                          |
| `Trust`                                     | StrEnum `TRUSTED`/`UNTRUSTED`        | connector-trust tier (§3)                                                                                                                   |
| `PolicyDecision`                            | StrEnum `ALLOW`/`GATE`/`DENY`        | generalizes `ToolGateAction` (`tools/runtime_gate.py:24-29`)                                                                                |
| `ConnectorState`                            | StrEnum `LIVE`/`PAUSED`/`OFF`        | availability, not authz                                                                                                                     |
| `CapabilitySource`                          | `Literal["mcp","builtin","skill"]`   | which source produced the tool                                                                                                              |
| `CapabilityDescriptor`                      | frozen Pydantic                      | `urn, action, trust, scopes, source, connector_state`; `urn` validated at the boundary via `CapabilityUrn.parse`                            |
| `CapabilityUrn` / `ParsedUrn` / `UrnScheme` | class + model + StrEnum              | build/parse `mcp:{server}:{tool}` and `builtin:{ns}:{op}`, **reusing** `server_slug`/`tool_slug` (`surfaces/builtin.py:40-59`)              |
| `MIDDLEWARE_ORDER`                          | `tuple[MiddlewareStage, ...]`        | Policy → Exec-policy → Observe → Error-map → Citations (outermost first)                                                                    |
| `Principal`                                 | Protocol                             | identity surface the PDP reads; `AgentRuntimeContext` satisfies it                                                                          |
| `ToolSource`                                | Protocol                             | `async load() -> list[(BaseTool, CapabilityDescriptor)]`                                                                                    |
| `PolicyService`                             | Protocol                             | `decide(...) -> (PolicyDecision, str)`                                                                                                      |
| `CredentialProvider`                        | Protocol                             | `async auth_for(server_id) -> httpx.Auth \| Mapping`                                                                                        |
| `ToolMiddleware`                            | Protocol                             | `wrap(tool, descriptor) -> BaseTool` (schema-identical)                                                                                     |

`ToolMiddleware.wrap` returns a **schema-identical** `BaseTool`, so a wrapped
tool registers exactly like the one it wraps. Services (the policy service,
event producer, credential provider) are **constructor-injected** into each
concrete middleware; the uniform seam is `(tool, descriptor) -> tool`.
`MIDDLEWARE_ORDER` is outermost-first: Policy sees the call before anyone,
Citations sits closest to the inner tool.

### URN identity

`CapabilityUrn` normalises segments through the **same** slug helpers the
surface layer uses (`server_slug`/`tool_slug`,
[`surfaces/builtin.py:40-59`](../../src/agent_runtime/capabilities/surfaces/builtin.py)),
so a URN segment resolves byte-identically to the surface URI's server/tool
segments — one connector, one identity, everywhere. `parse` keeps everything
past the second colon as the trailing `name` (because `tool_slug` lowercases
without stripping the delimiter) and raises the typed `CapabilityUrnError` on an
unrecognised scheme or a missing segment.

---

## 3. The approval decision matrix

Resolved per call from `descriptor.action`, `descriptor.trust` (a static
per-connector fact — authenticated / first-party — set by the source at
`load()`, so `decide()` takes no loose `trust` arg), and `posture` (Manual =
"Writes wait for you" / Bypass = "writes auto"). Faithful to master plan §2:

| action ＼ posture                      | **Manual** ("writes wait")                        | **Bypass** ("writes auto")       |
| -------------------------------------- | ------------------------------------------------- | -------------------------------- |
| **READ** — trusted connector           | ALLOW (auto)                                      | ALLOW (auto)                     |
| **READ** — untrusted / `openWorldHint` | **GATE** _(fail-closed default; → ALLOW-visible)_ | ALLOW                            |
| **WRITE**                              | **GATE** (interrupt · Focus **and** Studio)       | **ALLOW** (auto)                 |
| **DESTRUCTIVE**                        | **GATE** (always)                                 | **ALLOW** (auto) — _bypass note_ |

- **Move 1.** A _trusted_ connector's affirmative `readOnlyHint:true` auto-runs.
  This is the read-gated-as-write fix: no per-op catalog curation is required
  for a read to flow. Silence never means read (`readOnlyHint` default = false),
  and `destructiveHint` default = true, so an un-annotated write gates harder.
- **Trust tiers** (MCP 2026-07-28 "untrusted unless from a trusted server"):
  catalog / first-party and OAuth-authenticated connectors are `TRUSTED`; the
  descriptor derives `action` from annotations only when trust admits it.
- **GATE = LangGraph `interrupt`** — park + resume, reusing the existing
  mcp_auth interrupt seam and inline approval-card rendering. On approve →
  resume the **same** run → tool executes. **This is the hang fix**, and it
  renders identically in Focus and Studio.
- **Bypass note.** Bypass deliberately surrenders even the destructive
  hard-gate — full-auto means full-auto (matching Codex `never` / Claude Code
  `bypassPermissions`). It is an explicit, labeled posture ("Bypass on · writes
  auto" chip,
  [`PostureChip.tsx`](../../../../packages/chat-surface/src/destinations/run/PostureChip.tsx)),
  never a default.

### Render ≠ approve (Studio / MCP Apps)

Rendering and gating are **separate concerns on separate middleware**:

- **Render** is driven by the **Observe** path (tool results / surface specs /
  MCP Apps). In Studio the artifact / gen-UI renders **regardless of posture**.
- **Approve** is driven by the **Policy** path (a `GATE` → inline interrupt card).
- Therefore: a **Manual write** → artifact renders **and** an approval card
  appears; a **Bypass write** → artifact renders, **no card**. Showing an
  artifact is never the same as asking to approve it. The old fused surface
  (`EffectStageCard` = render + gate) is retired.

---

## 4. Credential model — the boundary "fork" dissolved

`CredentialProvider.auth_for(server_id) -> httpx.Auth | Mapping[str, str]` is a
single injected port, chosen **per deployment** — no global "move the token into
ai-backend or not" fork (master plan §3):

- **Desktop (single-user, local):** provider = OS keychain; ai-backend connects
  directly. The OAuth _connect_ flow (settings → consent → store) is unchanged
  and orthogonal.
- **Web / self-host (multi-tenant):** provider = fetch a **scoped, short-lived
  token from `services/backend`** at connect time (token isolation preserved),
  or a managed vault. Encryption-at-rest + tenant permissions remain real
  controls **there**.

`langchain-mcp-adapters` accepts auth via `httpx.Auth`, so the provider plugs in
at client construction; token refresh lives in the provider (a small
`httpx.Auth`), not a subsystem. The boundary doc (`services/backend/CLAUDE.md`
"owns OAuth/token state") must be updated when P2 lands direct-connect.

---

## 5. Middleware → live-seam mapping

Each middleware **re-hooks or subsumes** existing code. Refs are `file:line`
under `services/ai-backend/src/` (from the P0 grounding dossiers).

### Registration seam (where the stack drops in)

The single composition site is `_model_visible_tools`
(`execution/factory.py:543-793`), called once by `_assemble_harness`
(`factory.py:235`) which runs a fixed post-pipeline: compose (`factory.py:303`) →
`wrap_tools_with_display` (`factory.py:323`) → `ToolUsePolicyEnforcer.enforce`
(`factory.py:334-342`) → `builder(DeepAgentBuildRequest(...))` (`factory.py:453-517`).
The existing idiom `ModelToolDeclaration.declared(_structured_tool(adapter,
schema), owner=...)` (`factory.py:560-580`, list-form `.declared_all` at 574-580 /
722-735) **already is** `wrap(tool, descriptor)` in two halves — a `ToolSource`
replaces the ~13 hand-written `if <dep> is not None:` append blocks with a
uniform map. `interrupt_on` is a `dict[tool_name → config]` merged into the
build request (`factory.py:468-469`) and mechanically supports many keys — today
only the single `call_mcp_tool` key is ever emitted.

### 1 · Policy → `ALLOW | GATE | DENY`

Subsumes **three** questions today answered by three mechanisms, evaluated
**DENY-first** (availability → authorization → posture):

- **Availability (A).** `McpPermissionPolicy` (`mcp/permissions.py:12-64`):
  `enabled`, `health ∈ {HEALTHY,DEGRADED}` (`:15-20`), `paused_connectors`
  (`:52`), `ConnectorAccessMode.OFF` (`:54-59`). Moves to
  `CapabilityDescriptor.connector_state` (master plan §4).
- **Authorization (B).** Scopes (session ⊆ `permission_scopes`, plus the
  tools-lane connector level `has_scopes_for_connector`,
  `tools/permissions.py:194-209`), org/user allowlists (`mcp/permissions.py:60-62`).
- **Approval posture (C).** `action × trust × posture` via the resolution table
  in `EffectiveActionPolicyResolver._policy_kind` (`actions/policy.py:132-157`,
  **built but not yet wired** — `actions/policy.py:8-9`), the snapshot
  `ToolUsePolicySnapshot` (`tools/permissions.py:34-104`, defaults
  read=auto/write=ask/destructive=require at `:107-111`), and the per-connector
  `ConnectorWritePolicy` override (`actions/contracts.py:51-61`) that **only**
  ever downgrades `WRITE+ASK→AUTO`.

The tri-state it emits already exists as `ToolGateAction`
(`tools/runtime_gate.py:24-29`) / `ToolGateDecision` (`:32-89`); the mapping is
`BLOCK→DENY`, `ASK|REQUIRE→GATE`, `AUTO→ALLOW`, carrying `one_time` (ASK caches
per `(run, tool)`; REQUIRE re-prompts). Today it reaches Deep Agents through
`ToolUsePolicyEnforcer.enforce` (`tools/tool_use_enforcement.py:169`) →
`EnforcedToolSurface{tools, interrupt_on}` (`:140`), whose gated map
`_GATED_TOOL_SIDE_EFFECTS` (`:165-167`) has **exactly one** static entry
(`call_mcp_tool → EXTERNAL_CALL`) and whose `delegated_tool_names` (`:194`)
carves out the single gateway. Per-tool tools make this map **descriptor-driven**
instead of name-keyed on one constant. The GATE itself replaces the effect-
staging path (master plan §4) with the interrupt.

### 2 · Exec-policy → retries / timeout / resumption

Enforces **no auto-retry on WRITE** and **stream-resumption off for writes**
(the never-replay edge, master plan §7). Re-homes the retry concern currently in
`retrying_tool.py`; the write policy is a one-line rule keyed on
`descriptor.action`.

### 3 · Observe → the run ledger (reproduce byte-faithfully)

Reproduces the exact envelope from `runtime_worker/stream_tools.py`. Four event
kinds, `RuntimeApiEventType` (`runtime_api/schemas/common.py:112-115`):
`tool_call_started` / `tool_call_delta` / `tool_result` / `tool_call_completed`,
ordered `started → delta* → tool_result → tool_call_completed`. Emission sites:
started/delta `stream_tools.py:431-439` (type chosen `:422-426`), result
`:285-293`, completed `:339-347`. Payloads: `tool_call_payload_from_state`
(`:735-749`) + `add_tool_presentation_fields` (`:934-980`), `tool_result_payload`
(`:823-889`), completed inline (`:312-338`). **Failures ride the `status` field**
(`completed`/`failed`/`unavailable`) — there is no `tool_call_error` kind.

Durable invocation row `ToolInvocationRecord` (`persistence/records/tools.py:19-61`)
via the single upsert port `record_tool_invocation` (`api/ports.py:792-799`):
mint RUNNING in the started branch (`stream_tools.py:464-501`, fired `:456-461`),
close terminal (`:540-594`) computing kwargs through the projector
`ToolInvocationOutcome.from_result_payload` (`execution/tool_outcomes.py:112-134`,
folding `timed_out`/`abandoned`/`rejected`→`failed` at `:83-94`). Orphan
reconciliation on terminal failure `_reconcile_inflight_tool_calls`
(`runtime_worker/handlers/run.py:2654-2728`, close at `:2716-2720`) drains the
per-run `ToolCallLedger` (`stream_tools.py:130-151`); Observe must keep this
dual-writer ledger contract intact.

**`McpDispatcherUnwrap` dissolves.** `McpDispatcherUnwrap`
(`capabilities/mcp/dispatcher.py:26-114`) exists only because everything funnels
through `call_mcp_tool` and the real tool/server hide inside `payload.args`. Its
two uses — `effective_server_name` (`stream_tools.py:445`, the **only** source of
`connector_slug` on the ledger row) and `add_tool_presentation_fields`
(`:953,961`) — become no-ops per-tool. The connector identity it _recovers from
the payload_ must instead be _supplied by the per-tool binding_ and threaded into
`connector_slug` and `provenance.server_name` / `access_mode`; dropping it
without that replacement would blank `connector_slug` and break Activity's
app-vs-step counter (`connector_count = COUNT(DISTINCT connector_slug)`,
`api/ports.py:801-809`).

> **Gate (annotations passthrough — verified).** `langchain-mcp-adapters==0.3.1`
> preserves all four MCP annotation hints **flat** on `BaseTool.metadata` (e.g.
> `.metadata["readOnlyHint"]`) — `convert_mcp_tool_to_langchain_tool`,
> `langchain_mcp_adapters/tools.py:510-536`. **No capture shim is needed.** Three
> reader caveats for descriptor derivation: keys are **flat** (not
> `metadata["annotations"][...]`); `.metadata` can be **`None`** (guard
> `(tool.metadata or {}).get(...)`); values are **tri-state** (treat only
> `readOnlyHint is True` as READ — fail closed to WRITE otherwise). This collapses
> the master-plan §4 "keep (or shim) `mcp/annotations.py`" row to **not needed**.

### 4 · Error-map → typed taxonomy

Normalises to `{safe_message, code, retryable}`. Aligns to
`RuntimeErrorCode` (`execution/contracts.py:81-93`) and the persisted
`safe_error_code`/`safe_error_message` on the invocation row; never leaks
internal detail (per `services/ai-backend/CLAUDE.md`).

### 5 · Citations → result → sources

Re-hooks `capabilities/mcp/middleware/cite_mcp.py` as the innermost stage,
capability-agnostic (builtin and skill results cite through the same seam).

### Kept, not folded

`capabilities/mcp/middleware/auth_mcp.py` (the OAuth-**connect** interrupt) is
product infra the library has no story for — **kept** (master plan §4).

---

## 6. Descriptor sourcing (how each field is populated)

From the descriptor dossier — the headline is that **`action` is not stored
anywhere today; it is derived per-call**, and P2 folds that derivation into the
descriptor:

| Descriptor field                      | Source today                                                                                                                                                      | Ref                                                                                                                                                                                                                                                                   |
| ------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `action` (READ/WRITE/**DESTRUCTIVE**) | _derived_: `ACTION_CATALOG` (JSON) ∨ `McpToolAnnotations` (per-run registry) ∨ fail-closed WRITE, via `ActionClassifier` — **stop collapsing** DESTRUCTIVE        | `actions/classifier.py:38-94`; catalog `actions/catalog.py:104,127`; annotations captured `mcp/backend_provider.py:703-709`, modeled `mcp/annotations.py:32-63`; DESTRUCTIVE only in `CatalogActionKind` (`actions/contracts.py:48`), collapsed `classifier.py:60-61` |
| `trust` (TRUSTED/UNTRUSTED)           | _derived by the source at `load()`_: connector is catalog/first-party ∨ OAuth-authenticated ⇒ TRUSTED, else UNTRUSTED — a static per-connector fact, not per-call | connector auth-state / catalog membership (`actions/catalog.py`, `mcp/cards.py` auth)                                                                                                                                                                                 |
| `scopes`                              | per-**server** `McpServerCard.required_scopes` (no per-tool scope exists)                                                                                         | `mcp/cards.py:136`; resource inherits `backend_provider.py:746-749`                                                                                                                                                                                                   |
| identity (`Principal`)                | `AgentRuntimeContext.{user_id, org_id, roles, permission_scopes, connector_scopes}`                                                                               | `execution/contracts.py:358-362`                                                                                                                                                                                                                                      |
| `connector_state`                     | `AgentRuntimeContext.{paused_connectors, connector_access_modes}` + `McpServerCard.access_mode`; `ConnectorAccessMode{READ,READ_ACT,OFF}`                         | `contracts.py:367,376,339-352`; `mcp/cards.py:147`                                                                                                                                                                                                                    |
| `urn` slugs                           | `server_slug` / `tool_slug` normalisation                                                                                                                         | `surfaces/builtin.py:40-59`                                                                                                                                                                                                                                           |

---

## 7. Invariants every phase must keep

- **Fail-open on missing _policy_** (defaults read=auto / write=ask /
  destructive=require, `tools/permissions.py:107-111`); **fail-closed on missing
  _classification_** (unknown op → WRITE, held, `classifier.py:89-93`) and on
  missing **availability / authorization** (paused / access-mode OFF / disabled /
  unhealthy / scope-miss ⇒ DENY).
- **Annotations tighten-only** and never grant auto-run on their own — only a
  trusted READ is auto-eligible (Move 1); catalog is the authoritative rung.
- **The per-connector override only ever downgrades `WRITE+ASK→AUTO`** — never
  destructive, never `require`/`block` (`actions/policy.py:132-157`).
- **Posture removes the _pause_, never the _ledger row_ nor the scope /
  access-mode gates.** A bypassed write is still recorded (authored by POLICY,
  not USER) — the same semantics the host-write lane already keeps
  (`execution/filesystem_bypass.py` module header;
  `FilesystemBypassBound.permits` `:272-284`).
- **Render ≠ approve** (§3): Observe renders; Policy gates; the two never fuse.

---

## 8. Scope — what P0 ships and does not

- **Ships:** the contracts in
  [`capabilities/policy/contracts.py`](../../src/agent_runtime/capabilities/policy/contracts.py)
  - barrel, and this spec. Zero runtime change — no source registered, no
    middleware run, no import added to `execution/factory.py` or any live module.
- **Does NOT:** implement any Protocol, wire any middleware, swap the MCP client,
  or touch the frontend. Those are P1 (generic pipeline over the current client),
  P2 (`langchain-mcp-adapters` source), P3 (collapse staging), P4 (Resources /
  Prompts + render≠approve polish) — master plan §5.

---

## 9. Open decisions (master plan §9)

- Untrusted-connector reads (esp. `openWorldHint`): **DECIDED — GATE by
  default** (fail-closed per §7, `untrusted_read_gate=True`); a deployment may
  relax to auto-run + visible card. A trusted read always flows regardless.
- Keep a durable approval **receipt** (compliance) after deleting the staging
  subsystem? Recommended: yes, thin.
- Web direct-connect timing: ship desktop first, gate web behind multi-tenant
  tests.
- Session pool for stdio servers: keep a minimal pool, or one process per call?
- **Skill-URN scheme.** P0 defines only `mcp:` and `builtin:` URN forms;
  `CapabilitySource` still admits `"skill"`. A skill-URN form is TBD (skills
  surface through other rungs today).
