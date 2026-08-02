# Deletions plan — two retirements

**Status:** plan, nothing applied · **Base:** `833e7d25` (P2-8 at HEAD)

**Scope:** `services/ai-backend` and `apps/desktop/main`.
**Companion docs:** [PRD.md](PRD.md) (this is the file-level execution detail for its §3),
[../mcp-langchain-migration/PLAN.md](../mcp-langchain-migration/PLAN.md) §5 P3, and
[../mcp-langchain-migration/P2-PLAN.md](../mcp-langchain-migration/P2-PLAN.md) §2.

Two independent retirements:

1. **Retirement 1 (program phase P3)** — the MCP effect-staging and descriptor-revision subsystems.
2. **Retirement 2 (PRD §3 / P2)** — the superseded desktop broker credential route from `3c9a0714`.

Everything below was read at `833e7d25`. Where a verdict rests on an inference rather than a line I
read, it says so.

---

## 0. The distinction that governs every row of Retirement 1

The master plan's §4 table reads as if "effect staging" were one subsystem owned by MCP. It is not.
`EffectExecutorKind` has **five** members —
[`surfaces_v2/ledger_models.py:351-356`](../../../services/ai-backend/src/agent_runtime/surfaces_v2/ledger_models.py):

```python
class EffectExecutorKind(StrEnum):
    MCP = "mcp"; WORKSPACE = "workspace"; BROWSER = "browser"
    SANDBOX = "sandbox"; BUILTIN = "builtin"
```

A4 staging (`effects/staging.py::EffectStager`) and A5 dispatch
(`effects/dispatch.py::EffectDispatchCoordinator`) are the **shared spine** for all five. Deleting
"the staging subsystem" would take the desktop workspace-write approval lane, the desktop-browser
lane, and the D2 row-set lane with it.

So the question for each file is never "is this staging?" — it is **"which executor kinds does it
serve, and is the MCP one still produced?"**

**Who still produces an `EffectExecutorKind.MCP` stage after P1b:**

| Producer                                                                                        | Live?                | Evidence                                                                                                                                                                                                                                                                                                                                            |
| ----------------------------------------------------------------------------------------------- | -------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `McpOperationAdapter.build_proposal` — the model-initiated MCP write                            | **NO — unreachable** | its only production construction sets `authorized_to_execute=True` ([`call_tool.py:272-285`](../../../services/ai-backend/src/agent_runtime/capabilities/mcp/middleware/call_tool.py)), and the gateway then always executes ([`operations/gateway.py:370-375`](../../../services/ai-backend/src/agent_runtime/capabilities/operations/gateway.py)) |
| `ArtifactDraftSendStager` — the **user**-initiated Studio "send this draft through a connector" | **YES**              | [`api/artifact_draft_send.py:220-247`](../../../services/ai-backend/src/agent_runtime/api/artifact_draft_send.py) stages `executor=EffectExecutorKind.MCP`, `actor=EffectActor.USER`                                                                                                                                                                |
| `RuntimeStagedWriteEffectDispatcher` — legacy staged-write (draft-send) commit                  | **YES**              | [`runtime_worker/staged_write_effect_dispatch.py:104-138`](../../../services/ai-backend/src/runtime_worker/staged_write_effect_dispatch.py)                                                                                                                                                                                                         |

**That single fact re-writes half the master plan's §4 table.** `McpEffectExecutor` and the
`/effect-stages/{id}/decision` route are _not_ dead: they are the apply engine and the approval
route for the **artifact-draft-send** product surface, which is on by default on desktop
(`ARTIFACT_DRAFTS_V2` defaults to `"true"` at
[`apps/desktop/main/services/service-env.ts:199-203`](../../../apps/desktop/main/services/service-env.ts),
and `effect_stage_decisions_enabled = surfaces_v2 and (artifact_drafts_v2 or gateway==enforce)` at
[`runtime_api/app.py:390-393`](../../../services/ai-backend/src/runtime_api/app.py)).

---

## 1. Retirement 1 — per-file verdicts

### 1.1 The files named in the task

| #   | File                                                                                                                         | Verdict                 | Evidence                                                                                                                                                                                                                                                                                    |
| --- | ---------------------------------------------------------------------------------------------------------------------------- | ----------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 1   | `capabilities/mcp/operation_adapter.py::build_proposal` (`:246-347`) + the `_policy_snapshot` / stage helpers it alone feeds | **DELETE** (method)     | unreachable: only prod construction passes `authorized_to_execute=True` (`call_tool.py:284`); gateway fork `bool(getattr(adapter,"authorized_to_execute",False))` (`gateway.py:375`) then never calls `build_proposal`. The **file** stays — `execute_read` is the live MCP read path.      |
| 2   | `runtime_worker/mcp_effect_executor.py`                                                                                      | **KEEP**                | still the A5 apply engine for artifact-draft-send (`mcp_operation_storage.py:298`) and legacy staged-write (`staged_write_effect_dispatch.py:128`). Deleting it breaks a live, default-on desktop surface.                                                                                  |
| 3   | `runtime_worker/legacy_mcp_effect_executor.py`                                                                               | **DELETE**              | pure alias shim (`:8-18`) re-exporting `McpEffectExecutor` under a legacy name; no production importer — only `tests/unit/runtime_worker/test_legacy_mcp_effect_executor.py` and the reachability allowlist name it.                                                                        |
| 4   | `runtime_worker/rowset_effect_staging.py`                                                                                    | **KEEP**                | not MCP. It stages `EffectExecutorKind.BUILTIN` / `EffectProposalKind.ROW_SET` (`:167-181`) for the **builtin** tool `capabilities/tools/builtin/stage_rowset_write.py`, wired live at `runtime_worker/handlers/run.py:2146-2156`.                                                          |
| 5   | `runtime_api/http/effect_stage_decisions.py` — `POST /effect-stages/{id}/decision`                                           | **KEEP**                | `allowed_executors={EffectExecutorKind.MCP}` (`:73`) is the approval route for artifact-draft-send stages, which are `EffectExecutorKind.MCP`. Mounted whenever `ARTIFACT_DRAFTS_V2` is on — desktop default (see §0).                                                                      |
| 6   | `runtime_api/http/effect_stages.py` — `POST /effect-stages/{id}/decisions`                                                   | **KEEP**                | the **workspace** (C3) receipt route, consumed by `apps/desktop/main/capabilities/workspace-approval.ts:277`. Plural vs singular is the whole distinction — see §1.3.                                                                                                                       |
| 7   | `runtime_api/http/rowset_effect_reviews.py` — `/effect-stages/{id}/rowset/*`                                                 | **KEEP**                | row-set review/apply/retry for the BUILTIN lane; frontend calls all four (`RunDestination.tsx:2419,3087,3193`).                                                                                                                                                                             |
| 8   | `capabilities/mcp/revision_feed.py`                                                                                          | **DELETE** (see §1.4)   | dark: only composed when `RUNTIME_ENABLE_F8_MCP_CONTROL_PLANE` is truthy (`runtime_worker/mcp_revision_composition.py:40-52,150-172`), which is set **nowhere** outside tests and docs.                                                                                                     |
| 9   | `capabilities/mcp/revision_resolver.py`                                                                                      | **DELETE** (see §1.4)   | same gate (`mcp_revision_composition.py:184-193`); `revision_resolver=None` on the off path.                                                                                                                                                                                                |
| 10  | `capabilities/mcp/revision_wire.py`                                                                                          | **DELETE** (see §1.4)   | same gate (`:183`). Its two backend endpoints exist (`services/backend/src/backend_app/app.py:1356,1379`) — they go with it, or are left orphaned deliberately.                                                                                                                             |
| 11  | `capabilities/mcp/freshness.py`                                                                                              | **KEEP — shrink only**  | `RevisionAwareMcpDiscoveryCache` is the discovery cache on **both** paths — it is constructed unconditionally, revision-less, on the flag-off branch (`mcp_revision_composition.py:152-161`) and satisfies `McpDiscoveryCachePort` for `loader.py:121,180`. Only its revision half is dark. |
| 12  | `capabilities/mcp/descriptor_revision_binding.py`                                                                            | **DELETE** (see §1.4)   | the F8 adopter of the shared `control_plane/revision_binding` primitive; instantiated only from `freshness.py:253` and only consulted on the revision-checks-enabled path.                                                                                                                  |
| 13  | `surfaces_v2/gate.py`                                                                                                        | **KEEP — load-bearing** | PLAN.md §4 lists it under DELETE; that line is **stale**. P1b _reused_ it: `ToolAccessGate.park_for_approval` (`:281-311`) is the interrupt GATE, called from `call_tool.py:373`.                                                                                                           |
| 14  | `surfaces_v2/mcp_connector.py`                                                                                               | **KEEP**                | `McpStageCommitConnector` is the connector transport for **two** executors — MCP (`mcp_operation_storage.py:299-302`) **and** `BuiltinRowSetEffectExecutor` (`:317-320`). Deleting it breaks the row-set lane too.                                                                          |
| 15  | `capabilities/mcp/{effect_material,material_resolver,target_ref}.py`                                                         | **KEEP**                | consumed by the artifact-draft-send lane (`capabilities/backends/artifact_draft_effect.py:29`) and the staged-write bridge (`staged_write_effect_dispatch.py:16-17`).                                                                                                                       |

### 1.2 Adjacent files the retirement touches (not named in the task, found while reading)

| File / symbol                                                                                                              | Verdict                  | Evidence                                                                                                                                                                                                                                                                                                                                                                                                                  |
| -------------------------------------------------------------------------------------------------------------------------- | ------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `effects/composition.py:50-55` — the `("builtin","call_mcp_tool") → MCP/CANONICAL_ARGUMENTS` mapping                       | **DELETE** (entry)       | that mapping exists only to describe the model-write stage shape that row 1 removes. Its only consumer is `release/e2_final_conformance.py:26`.                                                                                                                                                                                                                                                                           |
| `capabilities/operations/gateway.py:353-375` — the `authorized_to_execute` fork                                            | **KEEP**                 | once row 1's `build_proposal` is gone this fork looks vestigial for MCP, but the **browser** adapter deliberately does not expose the attribute and keeps the `effect_class` staging rule (`operation_adapter.py:179-182`). Removing the fork would start staging MCP again.                                                                                                                                              |
| `runtime_worker/mcp_revision_composition.py`, `mcp_revision_poller.py`                                                     | **DELETE**               | the whole assembly is the F8 control plane; nothing else builds it. `dependencies.py:433-448` calls `McpRevisionControlPlaneBuilder.build(...).discovery_cache` — that one call must be re-pointed at a plain `McpDiscoveryCache` before the module goes.                                                                                                                                                                 |
| `runtime_worker/capability_descriptor_revisions.py`                                                                        | **DELETE**               | projects F8 revisions into the F3 catalog generation; its own docstring (`:14,66`) names `McpDescriptorRevisionResolver` as the only authority it reads. With the resolver gone it degrades to "unresolved narrows" for every server, i.e. it stops contributing. **Verify this is inert rather than deny-everything before deleting** — I did not read `RolloutCohortPolicy`'s reaction to an all-unresolved generation. |
| `runtime_adapters/file/mcp_revision_cursor.py` (`DesktopFilesystemMcpRevisionCursorStore`)                                 | **DELETE**               | imported only from `mcp_revision_composition.py:311-318`, on the enabled branch.                                                                                                                                                                                                                                                                                                                                          |
| `capabilities/mcp/control_plane_metrics.py`                                                                                | **UNSURE**               | consumed by `freshness.py`, `revision_feed.py`, `revision_resolver.py`. After the deletions the only remaining consumer is `freshness.py`'s `NoopMcpControlPlaneMetrics` default. Shrink rather than delete; I did not confirm there is no other reader.                                                                                                                                                                  |
| `services/backend` `/internal/v1/mcp/servers/{id}/revision` + `/internal/v1/mcp/descriptor-revisions` (`app.py:1356,1379`) | **DELETE — separate PR** | crosses a service boundary. Deleting the ai-backend client first leaves two unreachable backend routes; that is safe but must be tracked, not forgotten.                                                                                                                                                                                                                                                                  |

### 1.3 The trap this plan exists to prevent

`/v1/agent/effect-stages/{stage_id}/decisions` (**plural**) and `.../decision` (**singular**) are
different routes for different executors, registered from different modules, mounted under different
flags:

- **plural** → `effect_stages.py:106` → `WorkspaceApprovalDecisionService` → workspace effects, gated
  on `workspace_approval_enabled` (`app.py:379-385`).
- **singular** → `effect_stage_decisions.py:105` → `EffectStageDecisionService` with
  `allowed_executors={MCP}`, gated on `effect_stage_decisions_enabled` (`app.py:390-393`).

`effect_stage_decisions_enabled` **also** gates the row-set routes
([`runtime_api/http/routes.py:1195-1204`](../../../services/ai-backend/src/runtime_api/http/routes.py)) —
one `if`, three route families. Removing the MCP decision route must not remove that registration
block; the row-set registration has to move out of the `if` (or the flag has to be split) first.

The frontend has the mirror-image trap. `EffectStageCard`'s MCP branch
(`packages/chat-surface/src/destinations/run/RunDestination.tsx:3885-3897`) renders from
`projectMcpEffectStages`, which folds **every** `effect.staged` whose `payload.executor === "mcp"`
([`effectStageLifecycle.ts:124-128`](../../../packages/chat-surface/src/destinations/run/effectStageLifecycle.ts)) —
so it renders artifact-draft-send stages as well as the (now dead) model-write stages. **The card
cannot be deleted with the producer.** What P1's §5 "retire `EffectStageCard`" bullet actually means
after this reading is: retire it _for model-initiated MCP writes_, whose stages no longer exist, and
leave the draft-send lane rendering through it.

### 1.4 Why the descriptor-revision subsystem is a DELETE and where I am unsure

Confidence is high that it is **dark**, lower that it is **unwanted**:

- Dark: `RevisionControlPlaneEnvironment.ENABLED = "RUNTIME_ENABLE_F8_MCP_CONTROL_PLANE"`
  (`mcp_revision_composition.py:40`), default `False` (`:44-47`). Repo-wide the variable appears only
  in `docs/reference/env-vars.md:72`, `docs/runbooks/mcp-control-plane-operations.md:41,57,161`, the
  composition module itself, and `tests/unit/runtime_worker/test_mcp_revision_cache_ownership.py`.
  No desktop `service-env.ts` entry, no compose file, no deploy manifest sets it.
- Wanted-or-not: the subsystem is a **complete, two-sided** control plane — the backend half exists
  (`services/backend/src/backend_app/app.py:1356,1379`). It was built for a multi-tenant deployment
  where descriptors move under a long-lived process. Direct-connect (P2) re-lists tools live per run,
  which is why PLAN.md §4 marks it DELETE — but **P2-PLAN §2 marks the same three files PRESERVE**
  ("transport-independent, re-fed from ai-backend under direct-connect"). The two plans disagree.

**Recommendation: delete, and say why in the commit** — a control plane that has never been enabled
in any shipped configuration, whose only purpose was invalidating a cache that direct-connect no
longer populates, is carrying-cost without a user. But this is a **product decision, not a mechanical
one**, and it should be confirmed rather than inferred from a flag default. If it is kept instead,
keep all four modules together (`revision_feed`, `revision_resolver`, `revision_wire`,
`descriptor_revision_binding`) plus the composition and cursor store; they are a single graph and
half of it is not useful.

`freshness.py` is the exception either way. It is **not** part of that graph — it is the process
discovery cache, live on the flag-off path, with a revision-checking wrapper bolted on. The shrink is:
drop the `revision_resolver` / `active_subjects` / `revision_checks_enabled` / `revision_binder`
constructor parameters (`:238-243`) and the `_revision_checks_enabled` branches; keep the bounded
staleness + LRU + key-lock behaviour that `loader.py` depends on.

### 1.5 Audit and receipt behaviour that must survive

This is the part of the retirement that is easy to lose silently, because the interrupt GATE looks
like it already emits a receipt and mostly does not.

**What the staging path produced per approved write** (ledger types at
[`ledger_models.py:80-87`](../../../services/ai-backend/src/agent_runtime/surfaces_v2/ledger_models.py)):
`effect.staged` (digest-pinned proposal) → `effect.decision_recorded` (approval **bound to that exact
`proposal_digest`/`target_digest`/`revision`**) → `effect.claimed` → `effect.applied` /
`effect.indeterminate` / `effect.reconciled`, plus a `write_audit_log` row on every branch
(`runtime_worker/handlers/stage_commit.py:22,641-643`).

**What the P1b interrupt GATE produces today:**

- A durable `ApprovalRecord` and an `approval.accept` / `approval.reject` audit row via
  `write_audit_log` ([`api/approval_coordinator.py:424-440`](../../../services/ai-backend/src/agent_runtime/api/approval_coordinator.py)).
- The normal tool-call event quartet + a `ToolInvocationRecord` row.
- **No `gate.opened` and no `gate.resolved`.** Both emitters are keyed on the _OAuth-connect_ gate:
  `_maybe_emit_gate_opened` returns early unless the event is `MCP_AUTH_REQUIRED`
  (`runtime_worker/stream_events.py:820`), and `_maybe_emit_gate_resolved` returns early unless
  `approval_kind == mcp_auth` (`approval_coordinator.py:533`). The write GATE deliberately rides the
  _other_ payload shape — `APPROVAL_KIND_WRITE = "ask_a_question"`
  ([`surfaces_v2/gate.py:109,331`](../../../services/ai-backend/src/agent_runtime/surfaces_v2/gate.py)) —
  so neither branch fires for it.

So a compliance reviewer asking _"who approved which external write, against what arguments, and did
it apply?"_ can answer it for a staged effect and **cannot** answer it for an interrupt-gated one.
Three gaps, in severity order:

1. **No digest binding.** The staging decision was recorded against `proposal_digest` + `revision`;
   the interrupt approval carries only `approval_id`, a sanitized purpose line, `op`, `op_class`, and
   `scopes` (`gate.py:326-348`). Nothing proves the arguments that executed are the arguments that
   were shown. `GatePurposeBuilder.build` deliberately truncates and strips (`:139-194`) — it is
   display copy, not evidence.
2. **The approval kind is indistinguishable from a question.** Every write approval is persisted as
   `ask_a_question`, so no audit query can separate "user answered the agent" from "user authorized an
   external write".
3. **No applied/indeterminate record.** The staging path distinguished _applied_ from _the connector
   outcome is unknown, never resend_ (`mcp_effect_executor.py:118-137,177-185`). The tool path
   collapses both into a tool result.

**The thin receipt this plan requires before the staging producer is removed** — deliberately three
fields and one event pair, not a machine:

- Stamp `arguments_digest` (the same canonical digest `OperationRequestFactory` already computes) onto
  the interrupt payload's `gate` block and onto the persisted approval record, and re-check it after
  resume before dispatch. That restores property 1 without reintroducing a stage.
- Give the write GATE its own `approval_kind` (e.g. `mcp_write`) instead of borrowing
  `ask_a_question`, and extend the two `_maybe_emit_gate_*` guards to it so `gate.opened` /
  `gate.resolved` fire for writes. Note `receipt_v2.py:471-480` already treats a `gate.resolved`
  without an open as a warning, so both halves must land together.
- Keep the `approval.accept` / `approval.reject` audit row exactly as it is — it is the one piece that
  already works, and `_audit_action_for_decision` (`approval_coordinator.py:554-571`) is the SIEM
  vocabulary other rules ride.

Non-negotiable: `write_audit_log` on the artifact-draft-send lane
(`handlers/stage_commit.py:636-646`, `handlers/approval.py:1727-1750`) is untouched by this
retirement and must stay.

### 1.6 Ordering constraints for Retirement 1

```
R1-a  Split the route registration:  move register_rowset_effect_review_routes out of the
      `if effect_stage_decisions_enabled` block (routes.py:1195-1204), or split the flag.
        └─ nothing deleted yet; pure refactor, keeps both families mountable independently
R1-b  Land the thin receipt (§1.5): arguments_digest on the gate payload + a dedicated
      write approval_kind + the two gate.* emitters extended.
        └─ MUST precede R1-c: after R1-c there is no staging trail to fall back on
R1-c  Delete the model-write staging producer:
        operation_adapter.build_proposal (+ its helpers), effects/composition.py's
        ("builtin","call_mcp_tool") mapping, legacy_mcp_effect_executor.py
R1-d  Frontend: stop routing model-initiated writes to EffectStageCard; leave the card
      mounted for draft-send stages (executor=="mcp" fold, effectStageLifecycle.ts:128)
R1-e  Descriptor revisions (independent of a–d, gated on the §1.4 product decision):
        e1  re-point dependencies.py:433-448 at a plain McpDiscoveryCache
        e2  shrink freshness.py to the bounded-staleness cache
        e3  delete revision_feed / revision_resolver / revision_wire /
            descriptor_revision_binding / mcp_revision_composition / mcp_revision_poller /
            capability_descriptor_revisions / file/mcp_revision_cursor
        e4  separate PR in services/backend: drop the two /internal/v1/mcp revision routes
```

`R1-e` must not be bundled with `R1-c`: they share only the word "MCP", and a regression in either
should not force reverting the other. `R1-a` is genuinely first — every other step assumes the row-set
routes survive the MCP-route decision.

---

## 2. Retirement 2 — the superseded desktop broker credential route

### 2.1 The finding that justifies it

`3c9a0714` shipped the P2-7a credential seam **inert, and said why in its own message**. Both halves
of that finding verify at HEAD:

- **Nothing ever writes a `SecretStorage` record of kind `mcp`.** The kind is declared
  ([`apps/desktop/main/auth/secret-storage.ts:5,26`](../../../apps/desktop/main/auth/secret-storage.ts),
  and again in [`auth/audit-log.ts:4`](../../../apps/desktop/main/auth/audit-log.ts)) and the broker
  route reads it (`broker.ts:136,671`), but every production `SecretStorage.set` call site is in
  `apps/desktop/main/auth/index.ts` and every one of them passes `BACKEND_KIND`
  (`index.ts:115` defines it; `:165-170, :306-311, :349-354, :468, :514-519` are the five writers).
  The `mcp` partition is written only by tests.
- **`oauth-coordinator.ts` states the rule deliberately** —
  _"Provider tokens never cross into main: the callback response carries only safe connection
  metadata"_
  ([`apps/desktop/main/connectors/oauth-coordinator.ts:25-26`](../../../apps/desktop/main/connectors/oauth-coordinator.ts)),
  restated at `:172-174`: _"the provider token stays server-side in the backend TokenVault and never
  enters this process."_

So the P2-PLAN §1 premise ("desktop reads its MCP token from the keychain") was never true of this
codebase. The product owner's decision — backend owns credentials, ai-backend stays agnostic — makes
the mint endpoint (PRD §2) the single credential path for **both** deployments, and this route the
superseded one.

Both halves are also **provably unreached today**, so this is a low-risk removal once the mint
endpoint exists:

- TypeScript: `mcpSecrets` is optional and the only production caller of `createCapabilityService`
  (`apps/desktop/main/index.ts:417`) does not pass it, so `#mcpSecrets` is `null`, `/mcp/secret`
  answers `404 unsupported` (`broker.ts:647-650`) and `readMcpSecret` is not advertised (`:981`).
- Python: `mcp_per_tool_collaborators` is `object | None = None`
  ([`execution/contracts.py:875`](../../../services/ai-backend/src/agent_runtime/execution/contracts.py))
  and no composition root ever sets it, so `_mcp_per_tool_collaborators` returns `None`
  (`execution/factory.py:606-620`) and `McpPerToolRegistrar.build` returns `None` before touching a
  provider.

### 2.2 Per-file verdicts

| File                                                                                    | Verdict                 | Detail                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        |
| --------------------------------------------------------------------------------------- | ----------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `apps/desktop/main/capabilities/broker.ts`                                              | **DELETE — in part**    | remove `ROUTES.mcpSecret` (`:97`), `"readMcpSecret"` from `ADVERTISED_METHODS` (`:115`), `MCP_SECRET_METHOD` (`:131`), `MCP_SECRET_KIND` (`:136`), the six `MAX_MCP_*` caps (`:141-146`), `McpConnectionSecret` (`:170-181`), `McpSecretStore` (`:193-202`), `CapabilityBrokerConfig.mcpSecrets` (`:215`), `#mcpSecrets` (`:263,292`), the dispatch arm (`:558-559`), `#handleMcpSecret` and its doc block (`:625-677`), the advertise branch (`:975-982`), and `mcpSecretWire`/`mcpSecretString`/`mcpSecretExpiry`/`mcpSecretHeaders` (`:1310-1390`). Also the P2-7a paragraph in the module header (`:73-78`). **Do not touch** `requireOpaqueId` (`:1071`) or `requireRecord` (`:1098`) — the workspace routes share them. |
| `apps/desktop/main/capabilities/broker-mcp-secret.test.ts`                              | **DELETE** (whole file) | 486 lines, sole subject is the deleted route.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 |
| `apps/desktop/main/capabilities/index.ts`                                               | **DELETE — in part**    | the `type McpSecretStore` import (`:4`), `CreateCapabilityServiceConfig.mcpSecrets` + its doc block (`:43-49`), the pass-through (`:127`), and the two type re-exports (`:148-149`).                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          |
| `services/ai-backend/.../capabilities/mcp/credentials/desktop.py`                       | **DELETE** (whole file) | 531 lines; `DesktopKeychainCredentialProvider` at `:408`. No production importer — `per_tool_registration.py:19` names it in a **docstring only**.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            |
| `services/ai-backend/tests/.../mcp/credentials/test_desktop_provider.py`                | **DELETE** (whole file) | 607 lines including the golden-body test that pins the TypeScript wire shape; both sides of that contract go together.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        |
| `services/ai-backend/.../capabilities/desktop/broker_client.py`                         | **DELETE — in part**    | `Routes.MCP_SECRET` (`:110-114`), `Field_.SERVER_ID` (`:134-140`), `McpSecretResult` (`:440-486`), and `DesktopBrokerClient.mcp_secret` (`:676-691`). The rest of the client is the live host-fs + workspace-authority channel.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| `services/ai-backend/.../capabilities/desktop/__init__.py`                              | **DELETE — in part**    | the `McpSecretResult` import (`:38`) and `__all__` entry (`:136`).                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            |
| `services/ai-backend/.../capabilities/mcp/credentials/__init__.py`                      | **EDIT**                | drop the P2-7a paragraph (`:11-21`) describing the deliberately-unexported desktop module. `RefreshingBearerAuth` and friends **stay** — the mint provider uses them.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         |
| `services/ai-backend/.../capabilities/mcp/credentials/refreshing_auth.py`               | **KEEP**                | P2-2, transport-agnostic; PRD §2 AC5 has `BackendScopedTokenCredentialProvider` driving exactly this.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         |
| `apps/desktop/main/auth/secret-storage.ts` / `auth/audit-log.ts` — `ServerKind` `"mcp"` | **UNSURE — do last**    | with the reader gone, the member is unwritten and unread. Dropping it changes `assertServerKind` (`secret-storage.ts:214-217`) and could reject a historical on-disk path or audit row. PRD §3 AC2 allows either dropping it or recording why it stays; I did not audit the on-disk layout or existing audit rows, so **I recommend leaving both enums alone with a comment** rather than guessing.                                                                                                                                                                                                                                                                                                                           |

### 2.3 Ordering constraint

**This retirement lands strictly after the mint endpoint is live-validated on desktop.** Not merely
merged — validated, because the inert route is currently the _only_ thing occupying the
`CredentialProvider` seam, and deleting it before a replacement exists creates a window with no
credential path at all. The per-tool flag (`MCP_PER_TOOL_ENABLED`) must stay **OFF** for any profile
whose provider is not yet wired; `McpPerToolRegistrar` already treats a missing provider as
"fall back to the legacy gateway" (`per_tool_registration.py:18-28`), which is the safety net, not
the plan.

```
PRD §2  backend mint endpoint  ──▶  P2-7b BackendScopedTokenCredentialProvider
                                          ──▶  live desktop journey: real MCP tool call
                                                     ──▶  THIS retirement
```

---

## 3. Tests that must stay green

### 3.1 Green without modification (these are the regression net)

| Test                                                                                                                                                                                  | What it protects                                                                             |
| ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------- |
| `tests/unit/runtime_worker/test_mcp_write_gate_e2e.py`                                                                                                                                | P1b's proof: trusted read auto-runs; a write parks → approve → executes once in the same run |
| `tests/unit/runtime_worker/test_mcp_per_tool_gate_e2e.py`                                                                                                                             | the per-tool flip's gate matrix                                                              |
| `tests/unit/agent_runtime/execution/test_mcp_per_tool_flip.py`                                                                                                                        | 48 flag/decline/reserved-name/composed-surface cases; flag-OFF byte-identity                 |
| `tests/unit/agent_runtime/api/test_artifact_draft_send.py`                                                                                                                            | **the KEEP proof for `McpEffectExecutor`** — draft-send stages and applies through it        |
| `tests/unit/runtime_worker/test_mcp_post_approval_authorization.py`                                                                                                                   | post-approval re-authorization before dispatch                                               |
| `tests/unit/runtime_worker/test_rowset_effect_staging.py`                                                                                                                             | **the KEEP proof for `rowset_effect_staging.py`**                                            |
| `tests/unit/runtime_api/test_rowset_effect_review_routes.py`, `tests/unit/agent_runtime/api/test_rowset_effect_review.py`, `tests/unit/runtime_adapters/test_rowset_effect_review.py` | the row-set route family survives the §1.3 flag split                                        |
| `tests/unit/runtime_api/test_workspace_approval_decision_route.py`                                                                                                                    | the **plural** `/decisions` workspace route                                                  |
| `tests/unit/runtime_api/test_effect_stage_decision_route.py`                                                                                                                          | the **singular** `/decision` route (draft-send approvals)                                    |
| `tests/unit/agent_runtime/capabilities/mcp/test_loader_cache_integration.py`                                                                                                          | discovery caching after `freshness.py` is shrunk                                             |
| `packages/chat-surface/.../RunDestination.workspaceStage.test.tsx`, `RunDestination.surfacesV2.test.tsx`                                                                              | the frontend's three effect-stage branches stay distinct                                     |
| `apps/desktop/main/capabilities/workspace-approval.test.ts`                                                                                                                           | the desktop workspace approval host, which calls the plural route                            |

### 3.2 Tests that must be **updated** as part of the change (not silently deleted)

| Test                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         | Why                                                                                                                                                           |
| ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `tests/unit/architecture/effect_execution_reachability.py`                                                                                                                                                                                                                                                                                                                                                                                                                                                                   | `runtime_worker.legacy_mcp_effect_executor` is an allowlisted executor module (`:106`); removing the shim requires removing the entry                         |
| `tests/unit/architecture/test_single_effect_dispatch_path.py`                                                                                                                                                                                                                                                                                                                                                                                                                                                                | asserts `McpEffectExecutor` appears in `staged_write_effect_dispatch.py` (`:48`) — still true after this plan; it will **catch** a wrong delete               |
| `tests/unit/agent_runtime/effects/test_effect_no_executor.py`                                                                                                                                                                                                                                                                                                                                                                                                                                                                | fixtures name `runtime_worker/mcp_effect_executor.py` by path (`:61-80`)                                                                                      |
| `tests/unit/runtime_worker/test_legacy_mcp_effect_executor.py`                                                                                                                                                                                                                                                                                                                                                                                                                                                               | deleted with its subject                                                                                                                                      |
| `tests/unit/agent_runtime/capabilities/mcp/test_operation_gateway_adapter.py`                                                                                                                                                                                                                                                                                                                                                                                                                                                | constructs `McpOperationAdapter` (`:399`) and exercises `build_proposal`                                                                                      |
| `tests/unit/runtime_api/test_e1_authorization_inventory.py` + `packages/service-contracts/src/copilot_service_contracts/e1_authorization.py`                                                                                                                                                                                                                                                                                                                                                                                 | route removals must update `E1_SENSITIVE_ROUTES` **and** `E1_SENSITIVE_ROUTE_COUNT`; this is a shared constants-only package, so it is a cross-service change |
| `services/backend-facade/src/backend_facade/app.py:1606-1700`                                                                                                                                                                                                                                                                                                                                                                                                                                                                | the facade proxies all six `/effect-stages/*` routes explicitly; any route removal is a three-service change (ai-backend + contracts + facade)                |
| revision suite: `test_revision_feed.py`, `test_revision_resolver.py`, `test_revision_cache_composition.py`, `test_descriptor_revision_binding.py`, `test_freshness.py`, `test_mcp_revision_cache_ownership.py`, `test_mcp_revision_poller_lifecycle.py`, `test_control_plane_metrics.py`, `test_cross_service_contracts.py`, `test_step8_exit_criteria.py`, `tests/unit/agent_runtime/control_plane/test_f8_revision_binding_conformance.py`, `test_capability_discovery_composition.py`, `test_capability_bridge_wiring.py` | ~13 files go with `R1-e`. `test_freshness.py` **shrinks, not deletes** — the bounded-staleness cases stay                                                     |
| `apps/desktop/main/services/service-env.test.ts`                                                                                                                                                                                                                                                                                                                                                                                                                                                                             | if the §1.3 flag split changes which env combination mounts which route family                                                                                |

### 3.3 Gate for the whole thing

Full suites, not subsets: `cd services/ai-backend && .venv/bin/python -m pytest` (baseline at
`3c9a0714`: 10203 passed) and `npm run test --workspace @0x-copilot/desktop` (baseline 1549 passed),
plus `npm run typecheck`. Then a **live** desktop journey — `tools/desktop-journeys/` — for one real
MCP read and one real MCP write approval, because every one of the KEEP verdicts above rests on a
default-on flag combination that unit tests configure explicitly and a real boot resolves.

---

## 4. Judged too risky to delete — explicit hold list

Ranked by how badly a wrong delete would hurt:

1. **`runtime_worker/mcp_effect_executor.py`** — named in the task as a deletion target; it is the
   live apply engine for artifact-draft-send. Deleting it breaks a default-on desktop surface.
2. **`runtime_api/http/effect_stage_decisions.py`** (`/effect-stages/{id}/decision`) — same reason;
   it is the approval route for those stages, and its removal is also a three-service change.
3. **`surfaces_v2/gate.py`** — PLAN.md §4 lists it DELETE; that entry predates P1b, which built the
   interrupt GATE _on_ it. Deleting it removes the mechanism that replaced staging.
4. **`surfaces_v2/mcp_connector.py`** — shared with `BuiltinRowSetEffectExecutor`, so a delete lands
   on the row-set lane, not the MCP one.
5. **`runtime_worker/rowset_effect_staging.py`** — `EffectExecutorKind.BUILTIN`; not MCP at all.
6. **`capabilities/mcp/freshness.py`** — shrink only; it is the discovery cache on the live path.
7. **`ServerKind`'s `"mcp"` member** (both desktop enums) — unwritten and, after Retirement 2,
   unread, but `assertServerKind` would start throwing for any historical on-disk path or audit row.
   I did not audit either, so this stays until someone does.
8. **`runtime_worker/capability_descriptor_revisions.py`** — listed DELETE above on the strength of
   its own docstring, but I did not verify how `RolloutCohortPolicy` behaves when _every_ server's
   revision is unresolved. If "unresolved narrows" turns into "generation never matches", this
   becomes a deny-everything, not an inert module. Verify before deleting.
9. **The `authorized_to_execute` fork** (`operations/gateway.py:353-375`) — looks like dead scaffolding
   after §1.1 row 1, and is not: the browser adapter's staging depends on _not_ exposing the attribute.

**One open decision this plan cannot make:** whether the descriptor-revision control plane is
_obsolete_ or merely _not yet enabled_ (§1.4). PLAN.md §4 and P2-PLAN §2 give opposite answers about
the same three files. Everything else here is settled by code; that one needs a product call.
