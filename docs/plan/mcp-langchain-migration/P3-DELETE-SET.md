# P3 — the real delete set, re-derived from source

Status: AUDIT (read-only; nothing was deleted) · Scope: `services/ai-backend`, with
`services/backend` and `apps/desktop` read for flag ground-truth · Derived at
`claude/live-bugs-skills-and-mcp-errors` @ `15814fc1`.

This document **supersedes [PLAN.md](PLAN.md) §4 for every staging / revision row**.
PLAN.md §4 was written before P1b and P2-4/P2-8 landed and is wrong in ways that would
delete live code. [P2-PLAN.md](P2-PLAN.md) §2 already corrected the _transport_ half of
§4; this corrects the _staging and revision_ half, and re-checks the transport rows
against today's source.

Every verdict below cites `file:line`. A verdict with no citation is not in this
document.

---

## 0. TL;DR — the two corrections that matter

**Correction 1 — `surfaces_v2/gate.py` is the thing that replaced staging. Deleting it
deletes the fix.**

PLAN.md §4 row 4 lists `surfaces_v2/{gate,mcp_connector,…}` as **DELETE**. But
`ToolAccessGate.park_for_approval` — added in P1b as the second, non-OAuth mode of that
same class — **is** the LangGraph write-approval interrupt:

- [gate.py:342](../../../services/ai-backend/src/agent_runtime/surfaces_v2/gate.py) —
  `async def park_for_approval(...)`, the write gate (distinct from the OAuth-connect
  `park` at [gate.py:296](../../../services/ai-backend/src/agent_runtime/surfaces_v2/gate.py)).
- [call_tool.py:418](../../../services/ai-backend/src/agent_runtime/capabilities/mcp/middleware/call_tool.py)
  — the **default, flag-off** gateway calls it.
- [policy_tool.py:399](../../../services/ai-backend/src/agent_runtime/capabilities/mcp/middleware/policy_tool.py)
  — the P2-4 per-tool POLICY stage calls it.
- [factory.py:1830-1871](../../../services/ai-backend/src/agent_runtime/execution/factory.py)
  — `_tool_access_gate` builds it for every run with MCP servers, _including_ non-OAuth
  (`auth_mode == NONE`, stdio/local) cards, which is what lets their writes PARK instead
  of being refused.

`surfaces_v2/gate.py` is **KEEP**, permanently. P2-PLAN.md §2 already lists it under
PRESERVE ("the GATE interrupt — reused by the Policy stage"); PLAN.md §4 was never
updated to match.

**Correction 2 — MCP effect stages are still produced, and the producer is ON by default
on desktop.**

PLAN.md §5/P3 assumes staging is dead once the interrupt lands. It is dead **for the
model's MCP tool calls only**. Two other producers remain, both live on a default
desktop install:

| Producer                        | Gate                                                     | Desktop default |
| ------------------------------- | -------------------------------------------------------- | --------------- |
| Artifact draft-send (`/drafts`) | `SURFACES_V2` **and** `ARTIFACT_DRAFTS_V2`               | **ON**          |
| `stage_rowset_write` builtin    | `SURFACES_V2`                                            | **ON**          |
| Desktop-browser writes          | `OPERATION_GATEWAY_MODE=enforce` (via `effects_enabled`) | off             |
| Workspace effects               | `WORKSPACE_EFFECT_MODE=enforce`                          | off             |

Evidence for the two live ones is in §2.1 and §2.2.

---

## 1. Ground truth — the flag matrix everything else depends on

Nothing in this audit can be read without these. Service default vs. desktop default
**diverge**, and the divergence is what makes PLAN.md §4 wrong.

| Flag                                  | Service default                                                                                                                              | Desktop default                                                                                                      | Effect                                                                          |
| ------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------- |
| `SURFACES_V2`                         | **on** — [config.py:47](../../../services/ai-backend/src/agent_runtime/surfaces_v2/config.py) `_DEFAULT_WHEN_UNSET = "true"`                 | **on** — [service-env.ts:220](../../../apps/desktop/main/services/service-env.ts)                                    | v2 ledger emission, `/v1/agent/stages/*` routes, `stage_rowset_write` tool      |
| `ARTIFACT_EFFECTS_V2`                 | **off** — [settings.py:242](../../../services/ai-backend/src/agent_runtime/settings.py)                                                      | **on** — [service-env.ts:199](../../../apps/desktop/main/services/service-env.ts) `readBoolean(..., true)`           | artifact repository / effect claim store                                        |
| `ARTIFACT_DRAFTS_V2`                  | **off** — [settings.py:243](../../../services/ai-backend/src/agent_runtime/settings.py)                                                      | **on** — [service-env.ts:202-203](../../../apps/desktop/main/services/service-env.ts)                                | **the artifact-draft-send stage producer** + `/effect-stages/*` decision routes |
| `MCP_PER_TOOL_ENABLED`                | **off** — [per_tool_registration.py:115](../../../services/ai-backend/src/agent_runtime/capabilities/mcp/per_tool_registration.py)           | **off** (never set)                                                                                                  | P2 per-tool pipeline vs. the legacy `call_mcp_tool` gateway                     |
| `OPERATION_GATEWAY_MODE`              | `off` — [settings.py:246](../../../services/ai-backend/src/agent_runtime/settings.py)                                                        | `off` — [service-env.ts:211](../../../apps/desktop/main/services/service-env.ts)                                     | browser write staging; E2 cohort admission                                      |
| `WORKSPACE_EFFECT_MODE`               | `off` — [settings.py:250](../../../services/ai-backend/src/agent_runtime/settings.py)                                                        | `off` unless broker + gateway enforce — [service-env.ts:212-217](../../../apps/desktop/main/services/service-env.ts) | workspace `/effect-stages/{id}/decisions` route                                 |
| `RUNTIME_ENABLE_F8_MCP_CONTROL_PLANE` | **off** — [mcp_revision_composition.py:40-46](../../../services/ai-backend/src/runtime_worker/mcp_revision_composition.py) (empty ⇒ `False`) | **off** (never set)                                                                                                  | the whole descriptor-revision control plane                                     |

Two second-order facts:

- **The per-tool pipeline and the legacy gateway are mutually exclusive**, chosen in one
  `if/else`: [factory.py:932-950](../../../services/ai-backend/src/agent_runtime/execution/factory.py).
  With `MCP_PER_TOOL_ENABLED` off, `mcp_per_tool is None` and `CallMcpTool` is the
  registered model tool. **The live production path today is `call_tool.py`, not
  `policy_tool.py`.**
- **`SURFACES_V2=false` does not fall back to a legacy MCP path — it holds every MCP
  call.** [call_tool.py:122-136](../../../services/ai-backend/src/agent_runtime/capabilities/mcp/middleware/call_tool.py)
  `_held_without_gateway` returns `status: "held"` with no connector call. So
  "SURFACES_V2 off" is not a supported MCP posture; it is an MCP kill switch.

---

## 2. Verdicts, PLAN.md §4 row by row

Legend: **DELETE-NOW** = no live path reaches it under any supported flag combination ·
**KEEP** = a live path reaches it · **DELETE-AFTER** = unreachable only once a named
precondition holds.

| PLAN.md §4 row                                                                         | PLAN says     | Re-derived verdict                                | Why (short)                                                        |
| -------------------------------------------------------------------------------------- | ------------- | ------------------------------------------------- | ------------------------------------------------------------------ |
| `surfaces_v2/gate.py`                                                                  | DELETE        | **KEEP** (permanent)                              | it _is_ the interrupt GATE — §0 Correction 1                       |
| `surfaces_v2/mcp_connector.py`                                                         | DELETE        | **KEEP**                                          | the only MCP mutation transport for approved effects — §2.3        |
| `surfaces_v2/staging.py` (`WriteStager`, `StagedWriteFold`)                            | DELETE        | **KEEP**                                          | 22 src importers incl. the live rowset + stage-commit lanes — §2.2 |
| `runtime_worker/mcp_effect_executor.py`                                                | DELETE        | **KEEP**                                          | the A5 executor the approved-stage lane dispatches through — §2.3  |
| `runtime_worker/rowset_effect_staging.py`                                              | DELETE        | **KEEP**                                          | reached from the default-on `stage_rowset_write` tool — §2.2       |
| `/effect-stages/*` routes                                                              | DELETE        | **KEEP** (2 of 3), **DELETE-AFTER** (1 of 3)      | §2.4                                                               |
| `mcp/revision_{feed,resolver,wire}.py`                                                 | DELETE        | **DELETE-AFTER** (F8 decision)                    | default-off, but structurally imported — §2.5                      |
| `mcp/descriptor_revision_binding.py`                                                   | DELETE        | **DELETE-AFTER** (F8 decision + 1 edit)           | §2.5                                                               |
| `mcp/{client,backend_provider,loader,registry}.py`                                     | DELETE        | **KEEP** (per P2-PLAN §2) / **DELETE-AFTER** flip | §2.6                                                               |
| `mcp/middleware/call_tool.py` + `McpDispatcherUnwrap`                                  | DELETE        | **KEEP** / **DELETE-AFTER** flip                  | §2.6                                                               |
| `mcp/operation_adapter.py`, `capabilities/operations/gateway.py`, `gateway_context.py` | DELETE        | **KEEP**                                          | the gateway is capability-agnostic, not MCP-only — §2.7            |
| backend `mcp_transport.py`, `mcp_session_pool.py`                                      | DELETE/shrink | **KEEP**                                          | §2.6                                                               |

**Net: zero DELETE-NOW.** Not one module in PLAN.md §4's staging/revision rows is
deletable today. The three that are _eventually_ deletable each have a named
precondition, listed in §4.

---

### 2.1 The artifact-draft-send stage producer — LIVE on desktop

`DraftService.send` still stages, and the branch is taken whenever the stager is wired
and `SURFACES_V2` is on:

- [draft_service.py:167-168](../../../services/ai-backend/src/agent_runtime/api/draft_service.py)
  `artifact_staging_enabled = self._artifact_draft_send_stager is not None and SurfacesV2Flag.enabled()`
- [draft_service.py:188-189](../../../services/ai-backend/src/agent_runtime/api/draft_service.py)
  → `_stage_send_v2(...)`
- [draft_service.py:217-218](../../../services/ai-backend/src/agent_runtime/api/draft_service.py)
  the legacy-row sibling `_stage_legacy_send_v2`, gated on `_write_stager` + the same flag.

The stager is wired iff `artifact_drafts_v2`:

- [app.py:932-942](../../../services/ai-backend/src/runtime_api/app.py) —
  `_default_artifact_draft_send_stager` returns `None` unless
  `settings.execution.artifact_drafts_v2` (and the queue / blob store / reference
  provider are present).

And it creates a real `EffectStager` stage:

- [artifact_draft_send.py:189-217](../../../services/ai-backend/src/agent_runtime/api/artifact_draft_send.py)
  — `EffectStager(...)` … `effect_stager.stage(...)`, emitting `effect.staged`.

Desktop sets `ARTIFACT_DRAFTS_V2=true`
([service-env.ts:202-203, 222](../../../apps/desktop/main/services/service-env.ts)), so on
a default desktop install this path is on. **PLAN.md's premise that "MCP stages are no
longer produced" is false.**

### 2.2 The rowset stage producer — LIVE on `SURFACES_V2` alone

`stage_rowset_write` is a **model-visible builtin tool** registered whenever
`SURFACES_V2` is on:

- [handlers/run.py:2145](../../../services/ai-backend/src/runtime_worker/handlers/run.py)
  `if not self.settings.execution.surfaces_v2 or run is None: return None` — the only gate.
- [handlers/run.py:2165-2175](../../../services/ai-backend/src/runtime_worker/handlers/run.py)
  imports and constructs `RuntimeRowSetEffectProposalPort` from
  `runtime_worker/rowset_effect_staging.py`.
- [factory.py:1091-1097](../../../services/ai-backend/src/agent_runtime/execution/factory.py)
  registers it as a `ModelToolDeclaration` when non-`None`.

So `rowset_effect_staging.py`, `surfaces_v2/rowset.py`, `surfaces_v2/rowset_policy.py`,
`surfaces_v2/staging.py` (`WriteStager`) and `effects/staging.py` (`EffectStager`) are all
on a live default path. `surfaces_v2/staging.py` alone has **22 src importers**
(measured: `grep -rl 'surfaces_v2\.staging\|surfaces_v2 import staging' src`), including
[handlers/stage_commit.py](../../../services/ai-backend/src/runtime_worker/handlers/stage_commit.py)
and [handlers/approval.py](../../../services/ai-backend/src/runtime_worker/handlers/approval.py).

### 2.3 The approved-stage execution lane — LIVE

An approved stage is committed by the worker, not the API, and the MCP transport it uses
is exactly the two modules PLAN.md marks DELETE:

- [handlers/stage_commit.py:18-22](../../../services/ai-backend/src/runtime_worker/handlers/stage_commit.py)
  — "sends it through `EffectDispatchCoordinator` via `McpEffectExecutor`… this handler
  never owns an MCP client or a second side-effecting executor."
- [loop.py:558](../../../services/ai-backend/src/runtime_worker/loop.py) — the handler is
  constructed unconditionally (`stage_commit_handler or RuntimeStageCommitHandler(...)`),
  and [loop.py:1080-1081](../../../services/ai-backend/src/runtime_worker/loop.py) claims
  `stage_commit_requested` commands off the queue.
- [mcp_operation_storage.py:297-311](../../../services/ai-backend/src/runtime_worker/mcp_operation_storage.py)
  — `EffectExecutorKind.MCP: lambda active_scope: McpEffectExecutor(...)` with
  `connector=McpStageCommitConnector(...)`, i.e.
  [surfaces_v2/mcp_connector.py](../../../services/ai-backend/src/agent_runtime/surfaces_v2/mcp_connector.py).

Deleting `mcp_effect_executor.py` or `surfaces_v2/mcp_connector.py` removes the only path
that _applies_ an approved effect. Both are **KEEP** until the producers in §2.1/§2.2 are
gone — which P3 does not propose.

### 2.4 `/effect-stages/*` routes — 2 KEEP, 1 DELETE-AFTER

Three distinct route families share the prefix; PLAN.md treats them as one.

| Route                                            | Module                                                                                                       | Registration gate                                                                                                                                                     | Desktop |
| ------------------------------------------------ | ------------------------------------------------------------------------------------------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------- |
| `POST /effect-stages/{id}/decision`              | [effect_stage_decisions.py:105](../../../services/ai-backend/src/runtime_api/http/effect_stage_decisions.py) | `effect_stage_decisions_enabled` = `surfaces_v2 AND (artifact_drafts_v2 OR gateway==enforce)` — [app.py:394-396](../../../services/ai-backend/src/runtime_api/app.py) | **ON**  |
| `GET/POST /effect-stages/{id}/rowset/*`          | [rowset_effect_reviews.py:177](../../../services/ai-backend/src/runtime_api/http/rowset_effect_reviews.py)   | same gate — [routes.py:1195-1204](../../../services/ai-backend/src/runtime_api/http/routes.py)                                                                        | **ON**  |
| `POST /effect-stages/{id}/decisions` (workspace) | [effect_stages.py:106](../../../services/ai-backend/src/runtime_api/http/effect_stages.py)                   | `workspace_approval_enabled` = `surfaces_v2 AND WORKSPACE_COMMIT is ENFORCE` — [app.py:383-389](../../../services/ai-backend/src/runtime_api/app.py)                  | off     |

Verdict: the singular `/decision` and the `/rowset/*` family are **KEEP** (they serve the
two live producers). The plural workspace `/decisions` family is **DELETE-AFTER
"workspace effects are formally dropped"** — it is unreachable on every default
deployment, but it is a deliberate cohort feature, not dead code, so deleting it is a
product decision rather than a cleanup.

Note also `/v1/agent/stages/*` ([routes.py:1183](../../../services/ai-backend/src/runtime_api/http/routes.py))
is mounted on plain `SurfacesV2Flag.enabled()` — i.e. **on** — and PLAN.md does not mention
it at all.

### 2.5 Descriptor-revision (`mcp/revision_*.py`, `descriptor_revision_binding.py`) — DELETE-AFTER

This is the closest thing to a genuine delete candidate, and it still is not DELETE-NOW.

Default-off:

- [mcp_revision_composition.py:39-52](../../../services/ai-backend/src/runtime_worker/mcp_revision_composition.py)
  — `RUNTIME_ENABLE_F8_MCP_CONTROL_PLANE`; empty string ⇒ `False`.
- [mcp_revision_composition.py:150-172](../../../services/ai-backend/src/runtime_worker/mcp_revision_composition.py)
  — when disabled the builder returns an assembly with
  `revision_client=None, resolver=None, subjects=None, cursor_store=None, catalog=None, invalidator=None, coordinator=None, runner=None, poller=None`.

But it is **structurally live**, not dead:

- [**main**.py:137](../../../services/ai-backend/src/runtime_worker/__main__.py) and
  [app.py:1344](../../../services/ai-backend/src/runtime_api/app.py) call
  `McpRevisionControlPlaneBuilder.build(...)` **unconditionally**, so every import in the
  module executes on every boot.
- [freshness.py:46-58](../../../services/ai-backend/src/agent_runtime/capabilities/mcp/freshness.py)
  imports `McpDescriptorRevisionBinder`, `ActiveMcpRevisionSubjectRegistry`,
  `McpRevisionSubject` and `McpDescriptorRevisionResolverPort` at module level, and
  [freshness.py:253](../../../services/ai-backend/src/agent_runtime/capabilities/mcp/freshness.py)
  **constructs** `McpDescriptorRevisionBinder()` unconditionally — even with F8 off. Its
  methods are only reached behind `self._revision_checks_enabled`
  ([freshness.py:326](../../../services/ai-backend/src/agent_runtime/capabilities/mcp/freshness.py),
  [:408](../../../services/ai-backend/src/agent_runtime/capabilities/mcp/freshness.py)).
- `RevisionAwareMcpDiscoveryCache` (also in `freshness.py`) **is** used on the default
  path — [dependencies.py:16](../../../services/ai-backend/src/runtime_worker/dependencies.py) —
  so `freshness.py` itself is KEEP regardless.

Also note P2-PLAN.md §2 lists `capabilities/mcp/{revision_feed,revision_resolver,freshness}.py`
under **PRESERVE** ("transport-independent, re-fed from ai-backend under direct-connect") —
directly contradicting PLAN.md §4's DELETE. P2-PLAN is the later document; treat PLAN.md
§4's revision row as retracted.

**DELETE-AFTER precondition:** an explicit decision that F8 (backend-driven descriptor
revisions) is abandoned rather than merely unshipped. That decision also requires editing
`freshness.py` to drop the unconditional binder construction — an edit outside a pure
delete.

### 2.6 Transport rows — already corrected by P2-PLAN, still not deletable

- `call_tool.py` is the **live default model tool**
  ([factory.py:950](../../../services/ai-backend/src/agent_runtime/execution/factory.py)),
  and it is _where the interrupt GATE actually runs today_
  ([call_tool.py:178-181](../../../services/ai-backend/src/agent_runtime/capabilities/mcp/middleware/call_tool.py),
  [:385-430](../../../services/ai-backend/src/agent_runtime/capabilities/mcp/middleware/call_tool.py)).
  **KEEP** until `MCP_PER_TOOL_ENABLED` defaults on (P2-9).
- `McpDispatcherUnwrap` still has three importers:
  [stream_tools.py:14](../../../services/ai-backend/src/runtime_worker/stream_tools.py),
  [stream_tools.py:989](../../../services/ai-backend/src/runtime_worker/stream_tools.py),
  [events.py:827-829](../../../services/ai-backend/src/runtime_api/schemas/events.py).
  **DELETE-AFTER** those three seams move to `ToolConnectorResolver`.
- `mcp/{client,loader,registry}.py` — P2-PLAN §2 marks all three **PRESERVE / reshape**,
  not delete. `client.py` alone has 13 src importers (incl.
  [mcp_connector.py:31-38](../../../services/ai-backend/src/agent_runtime/surfaces_v2/mcp_connector.py)
  for the error taxonomy). **KEEP.**
- `backend_provider.py` — **DELETE-AFTER** direct-connect is active for a deployment; it
  is the JSON-RPC proxy client the default path still uses
  ([backend_provider.py:294, 331, 357, 423](../../../services/ai-backend/src/agent_runtime/capabilities/mcp/backend_provider.py)).
- backend `mcp_transport.py` / `mcp_session_pool.py` — imported by
  [service.py:99, 108](../../../services/backend/src/backend_app/service.py) and driven by
  [app.py:472](../../../services/backend/src/backend_app/app.py). They back
  `proxy_internal_rpc` ([service.py:1462](../../../services/backend/src/backend_app/service.py)),
  which is what `backend_provider` calls. **KEEP** until direct-connect ships for _both_
  desktop and web (P2-PLAN §2 says the same).

**A blocking constraint P2-9 must respect:** the per-tool source refuses stdio —
[tool_source.py:51-52](../../../services/ai-backend/src/agent_runtime/capabilities/mcp/tool_source.py)
("**stdio remains deferred** and is refused with a typed `unsupported_transport`
failure"). Flipping `MCP_PER_TOOL_ENABLED` on before stdio lands silently removes
local/stdio MCP servers. That is a precondition for every transport-row deletion, and it
is not recorded in PLAN.md §4.

### 2.7 The operation gateway is not MCP-only — KEEP

PLAN.md §4 folds `capabilities/operations/gateway.py` and `gateway_context.py` into an
"op-gateway → generic pipeline" delete. The gateway is already the generic pipeline for
**non-MCP** capabilities:

- 14 src importers, including the builtins
  [stage_rowset_write.py](../../../services/ai-backend/src/agent_runtime/capabilities/tools/builtin/stage_rowset_write.py),
  [revise_artifact.py](../../../services/ai-backend/src/agent_runtime/capabilities/tools/builtin/revise_artifact.py),
  [publish_artifact.py](../../../services/ai-backend/src/agent_runtime/capabilities/tools/builtin/publish_artifact.py),
  plus [sandbox/execute_tool.py](../../../services/ai-backend/src/agent_runtime/capabilities/sandbox/execute_tool.py),
  [workspace/operation_port.py](../../../services/ai-backend/src/agent_runtime/capabilities/workspace/operation_port.py)
  and [interpreter/policy_invoker.py](../../../services/ai-backend/src/agent_runtime/capabilities/interpreter/policy_invoker.py).

What _did_ change in P1b is narrower and worth recording precisely: MCP no longer
**stages** through the gateway, because the adapter declares it is pre-authorized —

- [call_tool.py:282-284](../../../services/ai-backend/src/agent_runtime/capabilities/mcp/middleware/call_tool.py)
  `authorized_to_execute=True` ("Post-PDP-authorized: the gateway executes (read or
  write) instead of routing a write to the retired staging path").
- [gateway.py:405-409](../../../services/ai-backend/src/agent_runtime/capabilities/operations/gateway.py)
  `_executes_now` reads that directive via `getattr`, so an adapter that does not expose
  it (the browser) keeps the `effect_class` staging rule.

So the correct §4 row is not "delete the gateway" but "MCP writes no longer take the
gateway's STAGED branch". The branch itself is still reachable —
[call_tool.py:318-329](../../../services/ai-backend/src/agent_runtime/capabilities/mcp/middleware/call_tool.py)
still renders `status: "staged"` for the browser lane.

---

## 3. Adjacent findings (not in PLAN.md §4)

- **`agent_runtime.surfaces_v2.retention` is already a declared orphan** —
  [orphan_ratchet_baseline.txt:30](../../../services/ai-backend/tests/unit/orphan_ratchet_baseline.txt).
  Zero src importers (measured). It is the only surfaces_v2 module with none. It is not a
  staging module, so it is out of P3's stated scope, but it is a free deletion.
- **Three P2 modules are landed-but-unwired**, correctly enrolled in the ratchet:
  `capabilities.mcp.connection`, `capabilities.mcp.connector_resolver`,
  `capabilities.mcp.credentials.backend` —
  [orphan_ratchet_baseline.txt:19-21](../../../services/ai-backend/tests/unit/orphan_ratchet_baseline.txt).
  They become live on the `MCP_PER_TOOL_ENABLED` flip; do not delete them as "dead".
- **The frontend half is already done and does not need P3.** `EffectStageCard` survives
  in `packages/chat-surface`, but only for rowset + workspace stages; the MCP branch was
  removed with an explicit note —
  [RunDestination.tsx:3754-3763](../../../packages/chat-surface/src/destinations/run/RunDestination.tsx)
  ("MCP writes no longer stage on this canvas — P1b parks them on an inline approval
  interrupt"). The "Waiting for the run ledger" copy also survives, but scoped to the
  **workspace** decision path only —
  [RunDestination.tsx:3268-3270](../../../packages/chat-surface/src/destinations/run/RunDestination.tsx).
  PLAN.md §5 P3's "remove residual effect-stage UI" is therefore already satisfied for
  MCP and **must not** be extended to the rowset/workspace renderers.

---

## 4. What P3 should actually say

Replace PLAN.md §5's P3 bullet list with this ordered set. Nothing here is a
"collapse the staging subsystem" step, because the staging subsystem has two live
producers that P3 never proposed removing.

1. **DELETE-NOW: nothing.** Say so explicitly, so the next reader does not re-derive it.
2. **Free cleanup (independent of MCP):** `surfaces_v2/retention.py`, and prune its
   ratchet line in the same change.
3. **DELETE-AFTER `MCP_PER_TOOL_ENABLED` defaults on _and_ stdio is supported by
   `McpToolSource`:** `middleware/call_tool.py`, `mcp/dispatcher.py` (after the three
   stream seams move to `ToolConnectorResolver`), `mcp/operation_adapter.py`'s MCP
   adapter, `backend_provider.py` for desktop. This is P2-9, already specified in
   P2-PLAN.md §2 — P3 should defer to it rather than restate it.
4. **DELETE-AFTER an explicit F8 abandonment decision:** `mcp/revision_feed.py`,
   `mcp/revision_resolver.py`, `mcp/revision_wire.py`,
   `mcp/descriptor_revision_binding.py`, `runtime_worker/mcp_revision_poller.py`, the
   revision half of `mcp_revision_composition.py`. Requires an edit to `freshness.py`
   (drop the unconditional binder construction at line 253) — keep
   `RevisionAwareMcpDiscoveryCache`.
5. **DELETE-AFTER the workspace-effects cohort is formally dropped:**
   `runtime_api/http/effect_stages.py` (the plural `/decisions` route) and its decision
   service.
6. **Never:** `surfaces_v2/gate.py`, `surfaces_v2/mcp_connector.py`,
   `surfaces_v2/staging.py`, `effects/staging.py`, `runtime_worker/mcp_effect_executor.py`,
   `runtime_worker/rowset_effect_staging.py`, `capabilities/operations/gateway.py`,
   `capabilities/mcp/gateway_context.py`, and both the singular
   `/effect-stages/{id}/decision` route and the `/effect-stages/{id}/rowset/*` family —
   unless and until the artifact-draft-send and rowset producers are themselves removed,
   which is a separate product decision.

---

## 5. Method, and what this audit did **not** establish

**Method.** For each module: (a) enumerate src importers with
`grep -rl '<dotted.module>' src --include='*.py'`; (b) follow each importer to the
construction site; (c) find the flag or settings field gating that site; (d) read the
flag's _service_ default in `settings.py` / the flag-reader class **and** its _desktop_
override in `apps/desktop/main/services/service-env.ts`, because they diverge.

**Verified by running tests** (all from `services/ai-backend`, `-q -p no:randomly`):

| Command                                                                                                                                                                                                                                            | Result        |
| -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------- |
| `pytest tests/unit/agent_runtime/surfaces_v2/test_tool_access_gate.py tests/unit/agent_runtime/capabilities/mcp/middleware/test_policy_tool.py tests/unit/agent_runtime/capabilities/mcp/test_call_tool_gate.py tests/unit/test_orphan_ratchet.py` | **59 passed** |
| `pytest tests/unit/agent_runtime/api/test_artifact_draft_send.py tests/unit/runtime_api/test_effect_stage_decision_route.py tests/unit/runtime_worker/test_rowset_effect_staging.py`                                                               | **27 passed** |
| `pytest tests/unit/agent_runtime/execution/test_mcp_per_tool_flip.py tests/unit/agent_runtime/capabilities/mcp/test_operation_gateway_adapter.py`                                                                                                  | **81 passed** |

These confirm the gate/policy/staging modules are exercised and green; they do **not**
prove reachability on a live desktop install — that is what §1's flag matrix is for.

**Not established:**

- **No live run was driven.** Every reachability claim is derived from source and flag
  defaults, not observed on a packaged desktop app. A live confirmation would be
  `tools/desktop-journeys/` (see `tools/desktop-journeys/README.md`) with a real Linear
  connector — the write-gate journey is still unrun for this branch.
- **The full ai-backend suite was not run** (only the 167 tests above). This is a
  read-only audit; no source file was modified, so a full run would prove nothing new
  about this document.
- **Web/self-host deployment defaults were not audited.** §1 covers the service defaults
  and the desktop overrides only. `deploy/self-host/` and the Docker compose env were not
  read; a web deployment that sets `ARTIFACT_DRAFTS_V2=false` would have a different (and
  smaller) live surface than the one described here.
- **`revise_artifact` / `publish_artifact` effect classification was not traced.** They go
  through the same gateway; whether they can reach the STAGED branch was not established.
  If they can, they are a _third_ live stage producer and §0's table is incomplete.
