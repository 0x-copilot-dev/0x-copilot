# MCP → `langchain-mcp-adapters` + Generic Tool-Policy Pipeline — Implementation Plan

Status: DRAFT for review · Owner: TBD · Target service: `services/ai-backend` (with a
thin `services/backend` credential seam and `packages/chat-surface` UI changes)

---

## 0. Why (one refactor, three payoffs)

These are the same change and must be sequenced together:

1. **Shed protocol maintenance.** Replace the hand-rolled MCP transport / session /
   JSON-RPC / pagination with `langchain-mcp-adapters` (a thin wrapper over the official
   `mcp` SDK). We stop maintaining protocol plumbing and get **Resources / Prompts** — and
   future MCP primitives — largely for free.
2. **Un-tangle the cross-cutting concerns.** Today permissions, read/write approval,
   retries, error taxonomy, streaming, and citations are welded into the MCP layer
   (permissions alone is enforced in **four** MCP sites). Extract them into a
   **capability-agnostic** tool pipeline that wraps MCP, builtins, and skills identically.
   The MCP layer goes back to "connect, list, call."
3. **Fix the approval model (Move 1 + bypass + render≠approve).** Reads from trusted
   connectors flow; writes gate via a run-parking **interrupt** (not fire-and-return
   staging); **bypass = full-auto in both Focus and Studio**; **Studio renders artifacts
   regardless of approval**.

### Two production bugs this closes

- **Read gated as a write.** `get_issues` was staged as a "PROPOSED CHANGE" because it
  wasn't in the curated `linear.json` catalog; classification fails closed to WRITE
  ([classifier.py:88](../../../services/ai-backend/src/agent_runtime/capabilities/actions/classifier.py)).
  Move 1 (trust the connector's `readOnlyHint`) fixes it structurally — no per-op curation.
- **"Decision recorded. Waiting for the run ledger." hang.** Staging returns a normal tool
  result to the model ([call_tool.py:285](../../../services/ai-backend/src/agent_runtime/capabilities/mcp/middleware/call_tool.py)),
  the run completes and **seals its ledger**, and the approved effect's `effect.applied`
  event is causal — it can never append after the seal
  ([effect_ledger.py:128](../../../services/ai-backend/src/agent_runtime/api/effect_ledger.py)).
  Interrupt-based gating (park + resume in the _same_ run) removes the orphaning.

---

## 1. Target architecture

Everything that isn't "connect, list, call" is a cross-cutting concern in a generic tool
pipeline. MCP (the library) becomes just a **tool source**; builtins and skills are other
sources; one middleware stack wraps them all.

```
SOURCES  (produce self-describing tools)
  mcp     → langchain-mcp-adapters + CredentialProvider   ┐
  builtin → native tools                                   ├─ each yields (BaseTool, CapabilityDescriptor)
  skill   → skills                                         ┘

CapabilityDescriptor { urn, action: READ|WRITE|DESTRUCTIVE, scopes, source, connector_state }
    urn    = "mcp:linear:create_issue" | "builtin:fs:write"
    action ← MCP annotations (readOnlyHint/destructiveHint) or the builtin's own tag

GENERIC MIDDLEWARE  (wraps EVERY tool, MCP or not, in order)
  1 Policy      → ALLOW | GATE(interrupt) | DENY        # permissions + approval, unified
  2 Exec-policy → retry rules (NO auto-retry on WRITE), timeout, resumption off for writes
  3 Observe     → emit started/result/completed into the run ledger
  4 Error-map   → {safe_message, code, retryable}
  5 Citations   → result → sources
       ↓
  tool.ainvoke(args)     # library for MCP, native for builtin
```

### Interfaces (illustrative, not final)

```python
class Action(StrEnum): READ; WRITE; DESTRUCTIVE

@dataclass(frozen=True)
class CapabilityDescriptor:
    urn: str                      # stable resource identity the policy is written against
    action: Action                # from MCP annotations OR a builtin's own tag
    scopes: tuple[str, ...]       # metadata only; PDP consumes, source does not decide
    source: Literal["mcp","builtin","skill"]
    connector_state: str          # "live" | "paused" | "off"  (availability, not authz)

class ToolSource(Protocol):
    async def load(self) -> list[tuple[BaseTool, CapabilityDescriptor]]: ...

class PolicyDecision(StrEnum): ALLOW; GATE; DENY

class PolicyService(Protocol):
    def decide(self, *, principal, descriptor: CapabilityDescriptor,
               args: Mapping, posture: Posture) -> tuple[PolicyDecision, str]: ...
    # permissions (allowlists/scopes) + approval (action × trust × posture), UNIFIED.
    # desktop: trivial impl; web/self-host: RBAC + tenant impl. Same seam.

class CredentialProvider(Protocol):
    async def auth_for(self, server_id: str) -> httpx.Auth | Mapping[str, str]: ...
    # desktop: OS keychain; web/self-host: scoped token from backend (isolation preserved)
```

`wrap(tool, descriptor, services)` returns a `BaseTool` with the _same schema_ whose
coroutine runs steps 1–5, then calls the inner tool. Wrapped tools are registered with the
Deep Agent — **no `call_mcp_tool` gateway, no `McpDispatcherUnwrap`**; the tool's name is
already `linear.create_issue`.

---

## 2. The approval model (precise)

Resolved per call from `action` (descriptor), `trust` (is the connector authenticated?),
and `posture` (Manual = "Writes wait for you" / Bypass = "writes auto").

| action ＼ posture                      | **Manual** ("writes wait")                        | **Bypass** ("writes auto")    |
| -------------------------------------- | ------------------------------------------------- | ----------------------------- |
| **READ** — trusted connector           | ALLOW (auto)                                      | ALLOW (auto)                  |
| **READ** — untrusted / `openWorldHint` | **GATE** _(fail-closed default; → ALLOW-visible)_ | ALLOW                         |
| **WRITE**                              | **GATE** (interrupt · Focus **and** Studio)       | **ALLOW** (auto)              |
| **DESTRUCTIVE**                        | **GATE** (always)                                 | **ALLOW** (auto) — _see note_ |

- **Trust tiers** (per MCP 2026-07-28 "untrusted unless from a trusted server"): catalog /
  first-party and OAuth-authenticated connectors are _trusted_ → their affirmative
  `readOnlyHint:true` auto-runs. Silence never means read (`readOnlyHint` default = false).
  `destructiveHint` default = true, so an un-annotated write gates harder.
- **GATE = LangGraph `interrupt`** (park + resume), reusing the existing mcp_auth interrupt
  seam ([gate.py:254](../../../services/ai-backend/src/agent_runtime/surfaces_v2/gate.py))
  and inline approval-card rendering. On approve → resume the **same** run → tool executes.
  Renders identically in Focus and Studio. **This is the hang fix.**
- **Bypass note:** bypass deliberately surrenders the destructive hard-gate — full-auto
  means full-auto, matching Codex `never` / Claude Code `bypassPermissions`. It is an
  explicit, labeled posture ("Bypass on · writes auto" chip
  [PostureChip.tsx](../../../packages/chat-surface/src/destinations/run/PostureChip.tsx)),
  not a default.

### Render ≠ approve (Studio / MCP Apps)

Rendering and gating are **separate concerns on separate middleware**:

- **Render** is driven by the **Observe** path (tool results / surface specs / MCP Apps).
  In Studio the artifact / gen-UI renders **regardless of posture**.
- **Approve** is driven by the **Policy** path (a `GATE` decision → inline interrupt card).
- Therefore: **Manual write** → artifact renders **and** an approval card appears.
  **Bypass write** → artifact renders, **no card**. Showing an artifact is never the same as
  asking to approve it. The old fused surface (`EffectStageCard` = render + gate) is retired.

---

## 3. Credential model — the boundary "fork" dissolved

The recurring "move the token into ai-backend or not?" decision becomes a single injected
port, chosen per deployment — no global fork:

- **Desktop (single-user, local):** `CredentialProvider` = OS keychain; ai-backend connects
  directly. The OAuth _connect_ flow (settings → consent → store) is unchanged and orthogonal.
- **Web / self-host (multi-tenant):** `CredentialProvider` = fetch a **scoped, short-lived
  token from `services/backend`** at connect time (token isolation preserved), or a managed
  vault. Encryption-at-rest + tenant permissions remain real controls **there**.

`langchain-mcp-adapters` accepts auth via `httpx.Auth`, so the provider plugs in at client
construction. Token refresh lives in the provider (~a small `httpx.Auth`), not a subsystem.

---

## 4. Delete / keep / re-hook

> **⚠️ THIS TABLE IS STALE AND ITS `DELETE` COLUMN IS WRONG.** A source-level
> audit of every row — [P3-DELETE-SET.md](P3-DELETE-SET.md), file:line evidence
> per verdict — found the deletable set is **empty**. Executing this table would
> remove live, reachable code. It is kept as the historical intent; take the
> audit as the record.
>
> The three corrections that matter most:
>
> 1. **`surfaces_v2/gate.py` is the REPLACEMENT, not the thing replaced.**
>    `ToolAccessGate.park_for_approval` (`gate.py:342`) is called from
>    `call_tool.py:418` and `policy_tool.py:399`, and `factory.py:1830-1871`
>    builds it for every MCP run — including non-OAuth (`auth_mode == NONE`)
>    cards. P2-PLAN.md §2 already listed it PRESERVE; this table was never
>    updated to agree.
> 2. **MCP stages are still produced, and default-ON where it counts.**
>    `draft_service.py:167-168` → `artifact_draft_send.py:189-217`, gated on
>    `artifact_drafts_v2` — which the desktop sets **true**
>    (`service-env.ts:202-203`) while the service default is `false`
>    (`settings.py:243`). Reading only the service default is what made this
>    table conclude staging was dead.
> 3. **There is a second live stage producer this table never mentions.**
>    `stage_rowset_write` is a model-visible builtin gated on `SURFACES_V2`
>    alone (`handlers/run.py:2145`, registered `factory.py:1091-1097`).
>
> Also: the live production MCP path is `call_tool.py`, not the P2 per-tool
> pipeline — `MCP_PER_TOOL_ENABLED` defaults **off**
> (`per_tool_registration.py:115`), and `factory.py:932-950` is a
> mutually-exclusive if/else. Any row whose fate assumes the per-tool path is
> live is answering about a branch that does not run.

| Legacy                                                                                                                                                 | Fate                                                                                                                         |
| ------------------------------------------------------------------------------------------------------------------------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------- |
| `mcp/{client,backend_provider,loader,registry}.py` transport/JSON-RPC/pagination                                                                       | **DELETE** → library                                                                                                         |
| `mcp/middleware/call_tool.py` (`CallMcpTool` single gateway) + `McpDispatcherUnwrap`                                                                   | **DELETE** → per-tool tools                                                                                                  |
| `mcp/operation_adapter.py`, `operations/gateway.py`, `gateway_context.py` (op-gateway)                                                                 | **DELETE** → generic pipeline                                                                                                |
| `surfaces_v2/{gate,mcp_connector,…}` effect-staging + `runtime_worker/mcp_effect_executor.py` + `rowset_effect_staging.py` + `/effect-stages/*` routes | **DELETE** → PolicyService `GATE` + interrupt                                                                                |
| `mcp/revision_*.py`, `descriptor_revision_binding.py` (descriptor-revision)                                                                            | **DELETE** → direct-connect re-lists live                                                                                    |
| `mcp/{session pool via backend}`, `backend/mcp_transport.py`, `backend/mcp_session_pool.py` (for the agent path)                                       | **DELETE / shrink** → library + stateless HTTP                                                                               |
| `actions/{classifier,policy,catalog}.py`                                                                                                               | **FOLD** into `PolicyService` (action + trust + posture); catalog demoted to a destructive-override, no longer the read gate |
| `mcp/permissions.py` (authz half: allowlists/scopes)                                                                                                   | **MOVE** into `PolicyService`                                                                                                |
| `mcp/permissions.py` (availability half: paused/access-mode/enabled)                                                                                   | **MOVE** into `CapabilityDescriptor.connector_state` / registry                                                              |
| `mcp/annotations.py` capture                                                                                                                           | **KEEP** (or shim) → feeds `descriptor.action` — **the Move 1 input**                                                        |
| `runtime_worker/stream_tools.py` envelope                                                                                                              | **RE-HOOK** as the Observe middleware (preserve the exact ledger event shape)                                                |
| `mcp/middleware/cite_mcp.py`                                                                                                                           | **RE-HOOK** as the Citations middleware                                                                                      |
| `mcp/middleware/auth_mcp.py` OAuth-connect interrupt                                                                                                   | **KEEP** (product infra; library has no connect story)                                                                       |

---

## 5. Phased plan

**Ordering rule: extract the generic pipeline FIRST (against the current MCP client), THEN
swap the source.** The scary library swap then lands _under_ an already-generic pipeline and
cannot break policy / observability / errors.

### P0 — Contracts + the one gate (no behavior change)

- [ ] **Gate check:** confirm `langchain-mcp-adapters` preserves MCP tool annotations
      (`readOnlyHint`) on the `BaseTool` (expected on `.metadata`). If not → a ~10-line
      capture shim from the raw `mcp` descriptor. **Blocks P2 only.**
- [ ] Define `CapabilityDescriptor`, `ToolSource`, `PolicyService`, `PolicyDecision`,
      `CredentialProvider`, `Posture`, and the **URN scheme** (`mcp:{server}:{tool}`,
      `builtin:{ns}:{op}`).
- [ ] Define the middleware interface + fixed order.
- [ ] Write the spec under `services/ai-backend/docs/specs/` (spec-first per repo rules).
- **Ships:** interfaces + spec merged; zero runtime change.

### P1 — Generic pipeline over the CURRENT MCP client (biggest cleanup, no library yet)

- [ ] Implement the 5 middlewares (Policy, Exec-policy, Observe, Error-map, Citations) as
      tool wrappers; register wrapped tools in
      [execution/factory.py](../../../services/ai-backend/src/agent_runtime/execution/factory.py).
- [ ] Implement `PolicyService` unifying permissions (`mcp/permissions.py` authz) + approval
      (`actions/*` + `tools/permissions.py`), encoding the §2 matrix incl. **trust tiers**
      and **Move 1**. Provide the trivial desktop impl.
- [ ] Remove permission checks from the four MCP sites (registry/loader/call_tool×2/adapter);
      the middleware owns it now.
- [ ] Replace the effect-staging GATE path with the **interrupt** GATE (park + resume).
      Wire **bypass** → skip GATE in both modes.
- [ ] Observe middleware reproduces the exact
      [stream_tools.py](../../../services/ai-backend/src/runtime_worker/stream_tools.py)
      event shape.
- [ ] **Frontend:** retire `EffectStageCard` / "Waiting for the run ledger"
      ([RunDestination.tsx:3405](../../../packages/chat-surface/src/destinations/run/RunDestination.tsx));
      route writes to the inline interrupt approval card; wire `PostureChip` bypass to suppress cards.
- **Ships (bug-fix milestone):** reads flow, writes gate via interrupt, **bypass = full-auto
  both modes**, the hang is gone — _all before the library swap_.

### P2 — Swap the MCP source to `langchain-mcp-adapters` (direct-connect)

- [ ] Add `mcp` + `langchain-mcp-adapters` to `services/ai-backend/requirements.txt`.
- [ ] Implement `McpToolSource` over `MultiServerMCPClient` + `CredentialProvider`
      (desktop keychain / web backend-token). Build descriptors from library tool metadata.
- [ ] Swap the source under the (already generic) pipeline.
- [ ] Exec-policy: enforce **no auto-retry on WRITE** and **disable stream resumption for
      writes** (the never-replay edge, now a deliberate one-line policy).
- [ ] **DELETE** the legacy transport/loader/pool/op-gateway/`CallMcpTool` in ai-backend and
      the backend RPC-proxy path for the agent.
- **Ships:** MCP served by the library; pipeline unchanged; Resources available next.

### P3 — Collapse the staging subsystem

- [ ] Delete A4/A5 effect-staging, rowset machinery, `mcp_effect_executor`,
      `/effect-stages/*` routes, descriptor-revision.
- [ ] Keep a **thin audit record** (who approved + what executed) if compliance needs a
      receipt — not the whole machine.
- [ ] Remove residual effect-stage UI.
- **Ships:** the heavy, buggy path is gone.

### P4 — Resources / Prompts + render≠approve polish

- [ ] Surface `list_resources`/`read_resource`/`list_prompts`/`get_prompt` via the library
      through the same descriptor + policy pipeline (a resource read is a READ capability).
- [ ] Implement **render≠approve** end to end: Observe drives Studio surface rendering
      independent of posture; Policy drives cards. Verify: bypass renders artifacts with **no
      card**; Manual write shows **card + render**.
- [ ] (Optional) MCP Apps (2026-07-28) inline UI.
- **Ships:** Resources live; Studio render decoupled from approval.

---

## 6. Gates / hard dependencies

1. **Annotations passthrough (P0 → P2).** If the library drops `readOnlyHint`, Move 1 has no
   input and everything fails closed to WRITE. Mitigation: capture shim. **Verify first.**
2. **Observability fidelity (P1).** The desktop UI + replay depend on the exact ledger event
   shape; the Observe middleware must reproduce it byte-faithfully. Mechanical, not free.

## 7. Risks & mitigations

- **Never-replay edge:** streamable-HTTP resumption could replay a write below the
  middleware. → disable resumption on write calls; no auto-retry on writes.
- **Desktop token in the agent process:** modestly larger exfil surface. → acceptable for
  single-user local; web uses the scoped-token provider. Deliberate, documented.
- **Web/self-host regressions:** encryption-at-rest + tenant permissions are real there. →
  `CredentialProvider` (backend-token) + `PolicyService` (RBAC impl) preserve them behind the
  same seams; add multi-tenant tests before enabling direct-connect on web.
- **Boundary/service rules:** direct-connect changes who holds the token — update the
  service-boundary docs (backend CLAUDE.md "owns OAuth/token state") to reflect the provider seam.

## 8. Testing strategy

- **Unit:** `PolicyService` matrix (every action × trust × posture cell); descriptor
  derivation from annotations; Exec-policy no-retry-on-writes; Error-map taxonomy.
- **Hermetic integration:** a fake MCP server (fixture) → real graph run → assert reads
  auto-run, writes park on interrupt, approve resumes + executes in the same run, bypass
  auto-runs, destructive gates in Manual.
- **Live journey:** extend `tools/desktop-journeys/filesystem-access/jF_linear_mcp.py` — real
  Linear read auto-runs (no card), a write parks + approves + executes, bypass runs both.
- **Regression:** the two motivating bugs become explicit tests (read-not-gated;
  approve-resumes-same-run-no-ledger-hang).

## 9. Open decisions

- [x] Untrusted-connector reads (esp. `openWorldHint`): **DECIDED — GATE by default** (fail-closed per §7; `untrusted_read_gate=True`), relaxable per deployment. Trusted reads always flow.
- [ ] Keep a durable approval **receipt** (compliance) after deleting A4/A5? Recommended: yes,
      thin.
- [ ] Web direct-connect timing: ship desktop first, gate web behind multi-tenant tests.
- [ ] Session pool for stdio servers: keep a minimal pool, or one process per call?
