# PRD-C2 — ToolAccessGate: park/resume + gate cards 🎨

**Goal.** When a run reaches an MCP tool whose connector is not usable _right now_ —
never authenticated, auth skipped, or credentials expired/failed — the run **parks in
place** on a gate card on the Studio canvas, resumes automatically at the exact same tool
call once the user connects, and fails closed (the dependent call never executes) on
cancel. Non-read connectors surface a write-policy choice at the gate (`ask_first`
default vs `allow_always`), persisted as the per-connector override of the global
Approval Policy (PRD-C1's storage) and reflected in a posture chip. Every gate is a
ledger event pair (`gate.opened`/`gate.resolved`) behind the `SURFACES_V2` runtime flag.
Already-authenticated connectors never gate.

## Implementer brief

You are implementing one PR in a monorepo (`0x-copilot`). Work in a fresh git worktree
branched off `main`. Run `make setup` once if the per-service `.venv`s / `node_modules`
are missing. Components touched: `services/ai-backend` (gate + ledger events + decision
plumbing), `packages/chat-surface` (gate card + posture chip), `packages/api-types`
(decision request mirror). `services/backend` is **consumed only** via PRD-C1's internal
API — do not modify it here.

Test commands (run per component, from repo root unless noted):

- `cd services/ai-backend && .venv/bin/python -m pytest tests/unit/agent_runtime/surfaces_v2/ tests/unit/runtime_worker/ tests/unit/runtime_api/ tests/unit/agent_runtime/capabilities/mcp/`
- Single test: `cd services/ai-backend && .venv/bin/python -m pytest <path>::TestClass::test_name`
- `npm run test --workspace @0x-copilot/chat-surface` and `npm run typecheck --workspace @0x-copilot/chat-surface` and `npm run lint --workspace @0x-copilot/chat-surface`
- `npm run test --workspace @0x-copilot/api-types` and `npm run typecheck --workspace @0x-copilot/api-types`
- Design parity (UI DoD): `node_modules/.bin/vitest run --config tools/design-parity/vitest.config.mjs`, then follow `tools/design-parity/SKILL.md`.

Read these files first (all paths repo-relative):

1. `docs/plan/generative-surfaces-v2/01-problem-and-requirements.md` — FR-B1…B6 = the contract.
2. `docs/plan/generative-surfaces-v2/02-sdr.md` — §5 event vocabulary (authoritative), §7 S2, §10 invariant 5.
3. `services/ai-backend/src/agent_runtime/capabilities/mcp/middleware/auth_mcp.py` — the existing mcp_auth interrupt tool (`AuthMcpTool.ainvoke`, `langgraph_interrupt`, deterministic `mcp_auth:<run_id>:<server_id>` approval id); your gate reuses this seam.
4. `services/ai-backend/src/agent_runtime/capabilities/mcp/middleware/call_tool.py` — `CallMcpTool.ainvoke` (L58): the interception point; `resolution.card.auth_state` is in hand before `client.call_tool`.
5. `services/ai-backend/src/agent_runtime/capabilities/mcp/cards.py` — `McpServerCard` (L119), `McpAuthState` (L62), `McpLoadErrorCode.AUTH_FAILURE`.
6. `services/ai-backend/src/runtime_worker/stream_events.py` — mcp_auth batch path (L417–428): interrupt → `MCP_AUTH_REQUIRED` event + ApprovalBatch + `WAITING_FOR_APPROVAL` park.
7. `services/ai-backend/src/runtime_worker/handlers/approval.py` — mcp_auth resume shape `{approval_id, decision}` in `_resume_payload` (L640–669).
8. `services/ai-backend/src/agent_runtime/api/approval_coordinator.py` — `record_approval_decision` (L286): where `write_policy` is validated and persisted.
9. `services/ai-backend/src/runtime_api/schemas/approvals.py` — `ApprovalDecisionRequest` (L59); copy its model-validator pattern (L129) for `write_policy`.
10. `packages/chat-surface/src/destinations/run/approvalProjection.ts` + `mcpAuthPort.ts` (L46: `beginAuth`/`skipAuth`) — how mcp_auth gates project today.
11. `packages/chat-surface/CLAUDE.md`, `services/ai-backend/CLAUDE.md`, `services/ai-backend/tests/CLAUDE.md` — engineering/test/substrate rules.

## Context

Generative Surfaces v2 re-founds the agent's work-product layer on an explicit, typed
**Work Ledger**: every consequential runtime event is a typed event on the existing
per-run event stream; everything the user sees (canvas, receipt, approvals queue) is a
projection of it (`../02-sdr.md` §2–§3). This PR is **Wave C, PRD-C2**
(`../03-prds.md`). Requirements: `../01-problem-and-requirements.md` FR-B1–B6,
NFR-4/NFR-8; SDR sequence S2.

The runtime already has everything needed to park and resume: tool-side
`langgraph.types.interrupt` (proven by `AuthMcpTool` and `ask_a_question`), the
`StreamOrchestrator` that turns `__interrupt__` chunks into `MCP_AUTH_REQUIRED` events +
approval records + `WAITING_FOR_APPROVAL` status, checkpointer-held graph state, the
`POST /v1/agent/approvals/{id}/decision` endpoint (facade-proxied,
`services/backend-facade/src/backend_facade/app.py` L1131), and the worker's resume via
`Command(resume=...)`. Today it only fires when the **model chooses** to call the
`auth_mcp` tool. C2's delta: (a) gate **automatically** at the dispatch boundary in
`CallMcpTool` when auth is unusable, (b) enrich the interrupt payload into a full gate
card (purpose, scopes, read-only pledge, write-policy choice), (c) emit
`gate.opened`/`gate.resolved` ledger events, (d) persist the gate-time write-policy
override through PRD-C1's backend storage, (e) render the gate card on the v2 canvas +
the posture chip.

## Interfaces consumed / exposed

**Consumed (from earlier PRDs — every item here must exist on the base branch; reconcile
names against the merged PRDs before coding):**

- PRD-A1: event-type constants for `gate.opened` / `gate.resolved` + payload contracts
  (py + ts) + the ledger-id format `r<short>·<seq>` + the golden-event fixture file.
  `VERIFY AT IMPL:` exact module paths (expected: constants in
  `packages/service-contracts/src/copilot_service_contracts/`, pydantic mirrors in
  ai-backend, ts mirrors in `packages/api-types/src/`).
- PRD-A3: the `SURFACES_V2` flag helper (style mirrors `SurfaceEmissionFlag.enabled`,
  `services/ai-backend/src/agent_runtime/capabilities/surfaces/config.py`) and the
  server-side **ledger append seam**. `VERIFY AT IMPL:` the helper name and emitter
  symbol A3 shipped; C2 must not invent a second append path.
- PRD-B1: the v2 client-side ledger projector in `packages/chat-surface` + the canvas
  mount behind its feature flag. `VERIFY AT IMPL:` projector module path (expected
  under `packages/chat-surface/src/thread-canvas/`).
- PRD-C1: the classifier and the backend per-connector write-policy override. Concretely
  (as C1 ships them — reconcile only if C1's merged code renamed a symbol):
  - **Classifier:** `ActionClassifier.classify(*, server, tool, annotations) ->
ClassifiedAction` in `agent_runtime/capabilities/actions/classifier.py`; the
    `.action_class` field (`ActionClass.READ`/`WRITE`) drives the read-only pledge and the
    gate's `op_class`. Annotations come from C1's `McpToolAnnotationsRegistry.get(server,
tool)` (`agent_runtime/capabilities/mcp/annotations.py`); `None` when unbound (falls
    through to catalog/default — fail closed).
  - **Override storage endpoint (runtime lane):**
    `PUT /internal/v1/mcp/servers/{server_id}/write-policy`, body
    `{"write_policy": "ask_first" | "allow_always" | null}`, `RequireScopes(RUNTIME_USE)`,
    identity via `BackendServiceAuthenticator.internal_scoped_identity` (service-token lane:
    `SERVICE_TOKEN_HEADER`/`ORG_HEADER`/`USER_HEADER`). **Keyed by `server_id`, not
    connector slug** — C1 stores the override as `mcp_servers.write_policy`. 404 on unknown
    server.
  - **Posture read (FR-B5):** there is **no** posture endpoint. C1 exposes `write_policy`
    on the `McpServerResponse` returned by `GET /v1/mcp/servers` (facade-forwarded); the
    chip derives "Bypass on" ⇔ any **enabled** server row has
    `write_policy == "allow_always"`. `VERIFY AT IMPL:` confirm C1's merged field name on
    `McpServerResponse` is `write_policy`.
- Existing (verified in this worktree): `AuthMcpTool` / `McpAuthSessionCreator`
  (`auth_mcp.py` L19–L38), `CallMcpTool.ainvoke`, `McpServerCard.auth_state`,
  `StreamOrchestrator` mcp_auth batch path, mcp_auth resume `{approval_id, decision}`
  (approval.py L665–669), `ApprovalDecisionRequest` validator pattern, `McpAuthPort`,
  facade decision passthrough.

**Exposed (later PRDs rely on these — keep names stable):**

- `gate.opened` / `gate.resolved` ledger events flowing on live runs → PRD-E1 (receipt
  rows, Sources), PRD-E2 (Approvals-queue gate cards, pending counter).
- The persisted per-connector `write_policy` override + posture projection → Wave D
  (FR-C8: `allow_always` auto-apply honoring pre-holds) and Settings Approval Policy UI.
- `TcGateCard` + `PostureChip` components (chat-surface barrel exports) → PRD-E2 reuses
  the card in the Approvals rail.
- The at-dispatch re-gate seam for `McpAuthError` (see Design) → Phase-2 failure-path
  designs (gate re-entry on mid-run revocation is this PR; styling iterates later).

## Design

### Ledger events (SDR §5, verbatim — do not rename fields)

```text
gate.opened        {gate_id, connector, purpose, scopes[], auth_state: missing|expired|insufficient}
gate.resolved      {gate_id, outcome: connected|cancelled, write_policy?: ask_first|allow_always}
```

Payload models (pydantic, in the v2 package; ts mirrors in api-types — reconcile with
A1's contracts if already defined there):

```python
class GateAuthState(StrEnum):
    MISSING = "missing"          # McpAuthState.UNAUTHENTICATED | AUTH_SKIPPED
    EXPIRED = "expired"          # McpAuthState.AUTH_FAILED | AUTH_PENDING, or McpAuthError at dispatch
    INSUFFICIENT = "insufficient"  # reserved: vendor scope rejection (see note)

class GateOpenedV2(RuntimeContract):
    v: Literal[1] = 1
    gate_id: str                 # deterministic: "mcp_auth:<run_id>:<server_id>" (== approval_id)
    connector: str               # server slug (card.name)
    purpose: str                 # bounded task-terms line, see GatePurposeBuilder
    scopes: tuple[str, ...]      # card.required_scopes, sorted
    auth_state: GateAuthState

class GateResolvedV2(RuntimeContract):
    v: Literal[1] = 1
    gate_id: str
    outcome: Literal["connected", "cancelled"]
    write_policy: Literal["ask_first", "allow_always"] | None = None
```

`gate_id` deliberately equals the existing deterministic approval id
(`AuthMcpTool._approval_id`, auth_mcp.py L112–114) so ledger, approval record, and the
client Connect-card recognition (`approvalProjection.ts` L106–113) join on one key.
Display id `r<short>·<seq>` is pure presentation over the `gate.opened` event's
`run_id` + `sequence_no` (A1's formatter). `INSUFFICIENT` is emitted only when the
vendor auth error is attributably a scope shortfall. Verified: `McpAuthError` (defined
`services/ai-backend/src/agent_runtime/capabilities/mcp/client.py` L25, caught in
call_tool.py L112) is a bare `McpClientError` subclass with no fields, so it carries no
scope detail — map at-dispatch auth failures to `EXPIRED` and leave `INSUFFICIENT`
unemitted (the enum member still ships).

### Server: ToolAccessGate (new module)

New file `services/ai-backend/src/agent_runtime/surfaces_v2/gate.py` (`VERIFY AT IMPL:`
put it in whatever package A1/A3 established for v2 domain code; do not create a second
v2 package). All helpers live inside classes per `services/ai-backend/CLAUDE.md`.

```python
@dataclass(frozen=True)
class ToolAccessGate:
    """Decides, at the connector-dispatch boundary, whether a tool call must park."""
    auth_session_creator: McpAuthSessionCreator          # existing protocol, auth_mcp.py L19
    runtime_context: AgentRuntimeContext
    interrupt_handler: Callable[[dict[str, Any]], object] = langgraph_interrupt
    classifier: object | None = None                      # PRD-C1 ActionClassifier port

    def gate_state(self, card: McpServerCard) -> GateAuthState | None:
        """None ⇒ no gate (usable, or auth_mode NONE / AUTH_UNSUPPORTED)."""

    async def park(self, *, card: McpServerCard, tool_name: str,
                   arguments: Mapping[str, Any], state: GateAuthState) -> GateResume:
        """Create the auth session, raise the interrupt with the v2 gate payload,
        interpret the resume value. At most ONE interrupt call per tool invocation."""

class GatePurposeBuilder:
    """'to run {tool_name} on {display_name}' + primary scalar argument if present.
    Arguments are UNTRUSTED: length-cap 80 chars, strip newlines/markdown/URLs."""

class GateResume(RuntimeContract):
    approved: bool                                          # decision ∈ {APPROVED, APPROVE_WITH_EDITS}
    write_policy: Literal["ask_first", "allow_always"] | None = None  # reserved; always None in v2 (see resume note)
```

`approved` is derived purely from the resume dict's `decision` value
(`decision in {APPROVED, APPROVE_WITH_EDITS}` ⇒ True; `REJECTED`/skip ⇒ False), reusing
the existing `APPROVE_WITH_EDITS`→`APPROVED` coercion. `write_policy` is **not** threaded
through the resume (see "Server: write policy at the gate" — it is persisted
coordinator-side by the decision endpoint, never read by the gate), so it stays `None`;
the field is reserved for a future inline-resume design.

**Gate state mapping** (`gate_state`): `auth_mode == NONE` or
`auth_state == AUTH_UNSUPPORTED` → `None` (never gate — nothing to connect);
`AUTHENTICATED` → `None`; `UNAUTHENTICATED`/`AUTH_SKIPPED` → `MISSING`;
`AUTH_FAILED`/`AUTH_PENDING` → `EXPIRED`.

**Interrupt payload** = the existing `mcp_auth_required` payload (auth_mcp.py L81–93:
`api_event_type`, `event_type`, `approval_id`, `action_id`, `approval_kind: "mcp_auth"`,
`server_id`, `server_name`, `display_name`, `auth_url`, `expires_at`, `message`)
**plus** additive v2 keys: `gate: {v, purpose, scopes, auth_state, op, op_class}`. `op` is
the tool/op slug (`tool_name`). `op_class` is
`gate.classifier.classify(server=card.name, tool=tool_name,
annotations=McpToolAnnotationsRegistry.get(card.name, tool_name)).action_class.value`
(`"read"`/`"write"`) when `gate.classifier` is set; `"write"` when the classifier is
absent — fail closed, FR-C0. Additive keys keep `StreamOrchestrator`'s existing mcp_auth
handling
(stream_events.py L417–428, L562–584) and the legacy in-chat Connect card working
unchanged during the compat window.

**Interception** in `CallMcpTool.ainvoke` (call_tool.py): after the permission re-check
(L83) and **before** `resolution.provider.create_client` (L95):

```python
if SurfacesV2Flag.enabled() and (state := gate.gate_state(resolution.card)) is not None:
    resume = await gate.park(card=resolution.card, tool_name=..., arguments=..., state=state)
    if not resume.approved:
        return McpToolCallResult.fail(McpLoadErrorCode.AUTH_FAILURE,
            Messages.Loader.AUTH_FAILED, server_name=..., tool_name=..., ...)  # fail closed
    # approved: fall through to dispatch — the OAuth completed while parked
```

`CallMcpTool` gains an optional `gate: ToolAccessGate | None = None` dataclass field
(None ⇒ pre-C2 bytes, the flag-off path). Wire it at the sole `CallMcpTool` construction
site — `agent_runtime/execution/factory.py` L374 (verified the only `CallMcpTool(` call
in the repo; `AuthMcpTool` is constructed right below it at L385, receiving its
`auth_session_creator`). Reuse the same OAuth-capable provider `AuthMcpTool` gets: the one
discovered by the `provider.create_auth_session` duck-probe in `factory._auth_session_creator`
(factory.py L508–514) and mirrored in `runtime_worker/handlers/run.py` L1679.

**Resume-re-entry semantics** (LangGraph): on resume the tool node re-executes from the
top; `registry.resolve_server` returns a **fresh** card. Auth now valid ⇒ `gate_state`
returns `None`, the interrupt call is never reached, the stored resume value is dropped,
dispatch proceeds — this _is_ "resume re-enters the parked call". Auth still unusable
(cancelled, or OAuth failed) ⇒ `park` runs again and `interrupt(payload)` immediately
**returns** the stored resume value instead of re-parking (LangGraph matches by
interrupt index) — the gate returns the typed failure. No infinite gate loop; a
cancelled gate can never dispatch. Adversarial tests pin both branches.

**Mid-run revocation (at-dispatch)**: flag on, `client.call_tool` raises `McpAuthError`
(card _said_ AUTHENTICATED but the vendor rejected) ⇒ re-enter the gate with
`GateAuthState.EXPIRED` instead of returning the terminal `AUTH_FAILURE` result
(call_tool.py L112–119 branch). Flag off ⇒ existing behavior byte-identical.

### Server: ledger emission + park status

No new park mechanics: the interrupt flows through `StreamOrchestrator`
(`native_interrupt_payloads` → mcp_auth ApprovalBatch of size 1 → `MCP_AUTH_REQUIRED`
event → `AgentRunStatus.WAITING_FOR_APPROVAL`, run.py L426–433) exactly as today.
C2 adds, gated on `SURFACES_V2`:

- `gate.opened`: emitted by the orchestrator immediately after it appends the
  `MCP_AUTH_REQUIRED` event for a payload carrying the v2 `gate` key — via A3's ledger
  append seam, `source=SYSTEM`. Fields lift straight from the interrupt payload.
- `gate.resolved`: emitted by `ApprovalCoordinator.record_approval_decision` for
  mcp_auth approvals, after the decision persists (and after the write-policy override
  persists — below). `outcome: "connected"` iff decision approved; `"cancelled"` on
  reject/skip.

### Server: write policy at the gate (FR-B4)

`ApprovalDecisionRequest` (runtime_api/schemas/approvals.py L59) gains
`write_policy: Literal["ask_first", "allow_always"] | None = None` with a model
validator (mirror `_validate_edits`, L129): allowed only when `decision == APPROVED`,
rejected otherwise with 422. Mirror the field in `packages/api-types` (the decision
request is app-facing through the facade passthrough).

`ApprovalCoordinator.record_approval_decision`: when the target approval's
`metadata["approval_kind"] == "mcp_auth"` and `write_policy` is present →
`await self._connector_policy.put_override(org_id=..., user_id=..., server_id=...,
write_policy=...)` **before** recording the decision; on failure raise a typed error →
HTTP 502 `gate_policy_persist_failed`, decision untouched (the user retries — consent
and its policy are one atomic act, fail closed). The `server_id` is read from the
approval record's `metadata["server_id"]` (present on every mcp_auth interrupt payload,
auth_mcp.py L81–93) — C1's endpoint keys the override by `server_id`, not connector slug.
`write_policy` on a non-mcp_auth approval → 422 (coordinator-side check; the request
validator can't see the kind). `ConnectorWritePolicyClient` is a small port in
`agent_runtime/api/ports.py`:

```python
class ConnectorWritePolicyClient(Protocol):
    async def put_override(self, *, org_id: str, user_id: str, server_id: str,
                           write_policy: Literal["ask_first", "allow_always"]) -> None: ...
```

with an httpx impl (new file `agent_runtime/api/connector_policy_client.py`) that does
`PUT {backend_base}/internal/v1/mcp/servers/{server_id}/write-policy` with body
`{"write_policy": write_policy}` on the service-token lane
(`SERVICE_TOKEN_HEADER`/`ORG_HEADER`/`USER_HEADER` from
`copilot_service_contracts.headers`, same base-URL + header pattern as
`agent_runtime/capabilities/surfaces/backend_store.py`). Non-2xx ⇒ raise the typed
`gate_policy_persist_failed` error.

The worker resume path is untouched: mcp_auth resume stays the flat
`{approval_id, decision}` dict (approval.py L665–669). The gate builds `GateResume` from
`decision` alone (`approved` per the coercion above); it never reads a `write_policy` off
the resume. The write policy travels the **decision-request** path (client →
`POST /v1/agent/approvals/{id}/decision` → `ApprovalCoordinator`), which persists the
override and emits `gate.resolved{write_policy}` — the gate and the policy are decoupled.

### Client: gate card + posture chip (chat-surface) 🎨

- `packages/chat-surface/src/thread-canvas/TcGateCard.tsx` (new). Pure presentational.
  Props: `{gate: GateCardModel, onConnect(serverId), onSkip(serverId), onPolicyChange(p),
writePolicy, busy}`. Renders FR-B3 contents: connector display name + host + auth
  method; purpose line; scopes as plain chips; the read-only pledge **only when**
  `op_class === "read"`; the write-policy radio (`ask_first` default) **only when**
  `op_class !== "read"`; provenance footer with the `r<short>·<seq>` ledger id; parked
  copy: "the run is parked here until you connect — nothing runs without it". Built
  from design-system kit recipes (`.ui-card`, `.ui-badge`, `.ui-pill`, `ui-section-label`
  — see `packages/design-system/SKILL.md`); no raw font-size/letter-spacing.
- Projection: extend PRD-B1's v2 ledger projector to fold `gate.opened` into a canvas
  gate card (SDR §5 `surface.created` reserves `kind: gate`; the gate card renders from
  the `gate.opened` event directly — do not synthesize a fake surface) and
  `gate.resolved` into resolved/dismissed state + posture input. One projector,
  selectors only — never a second SSE subscription (`FR-3.3` invariant, chat-surface
  CLAUDE.md).
- Wiring: `RunDestination` passes the existing `McpAuthPort` (`beginAuth`/`skipAuth`)
  into the card; the policy choice + approve post through the existing decision endpoint
  with the new `write_policy` field (host binders in `apps/frontend/src/features/run/`
  and `apps/desktop/renderer/destinationBinders.tsx` — update BOTH).
- `packages/chat-surface/src/destinations/run/PostureChip.tsx` (new): renders
  "Writes wait for you" normally; warning-styled "Bypass on · writes auto" when any
  connector override is `allow_always`. Data: a host-supplied `bypassOn: boolean` prop
  the binder derives from `GET /v1/mcp/servers` (C1 added `write_policy` to each
  `McpServerResponse` — "Bypass on" ⇔ any **enabled** server row has
  `write_policy === "allow_always"`; there is no dedicated posture endpoint),
  optimistically updated by `gate.resolved{write_policy}` events from the ledger.
  Export both components through the barrel (`src/index.ts`, new delimited block).

### Flags & error behavior

- `SURFACES_V2` (runtime, from A3) gates: interception, at-dispatch re-gate, both ledger
  events. Off ⇒ `CallMcpTool` byte-identical (gate field None / short-circuit before any
  behavior change); `write_policy` field still parses but the coordinator rejects it with
  422 when the flag is off. A3's `SurfacesV2Flag` mirrors `SurfaceEmissionFlag`
  (`agent_runtime/capabilities/surfaces/config.py`) — a self-contained env reader
  (`SurfacesV2Flag.enabled(environ)` over `SURFACES_V2`, injectable for tests). It needs
  no special "exposure": `ApprovalCoordinator` calls `SurfacesV2Flag.enabled()` directly,
  the same way the worker/orchestrator do. `VERIFY AT IMPL:` A3's env-var name + class
  name (expected `SURFACES_V2` / `SurfacesV2Flag`).
- Canvas gate card rides PRD-B1's chat-surface canvas flag; flag off ⇒ legacy in-chat
  Connect card only (which keeps working regardless — compat window).
- Gate emission failures (ledger append raises) are logged and swallowed — parking and
  approval correctness never depend on ledger emission (mirror `_attach_surface`'s
  swallow pattern, call_tool.py L234).

## Implementation plan

1. **Contracts.** Add/confirm `GateOpenedV2`/`GateResolvedV2`/`GateAuthState` py models
   in `services/ai-backend/src/agent_runtime/surfaces_v2/gate.py` (new; or A1's
   contracts module) + event-type constants (A1's home). Mirror `write_policy` on the
   decision request in `packages/api-types/src/index.ts`.
2. **Gate core.** `ToolAccessGate`, `GatePurposeBuilder`, `GateResume` + unit tests.
3. **Interception.** Modify
   `services/ai-backend/src/agent_runtime/capabilities/mcp/middleware/call_tool.py`
   (add `gate` field, pre-dispatch check, `McpAuthError` re-gate) and the sole
   `CallMcpTool` construction site (`agent_runtime/execution/factory.py` L374; reuse the
   OAuth provider from the `create_auth_session` duck-probe at factory.py L508–514 /
   `runtime_worker/handlers/run.py` L1679). At that site, construct the `ToolAccessGate`
   with `classifier=ActionClassifier(ActionCatalog())` (C1's
   `agent_runtime/capabilities/actions/`) and pass it as `CallMcpTool(gate=...)`; leave
   `gate=None` on the flag-off / pre-C2 path. `VERIFY AT IMPL:` C1's exact
   `ActionClassifier`/`ActionCatalog` constructor + package path.
4. **Ledger tap.** Modify `services/ai-backend/src/runtime_worker/stream_events.py`
   (emit `gate.opened` beside the mcp_auth batch insert) and
   `services/ai-backend/src/agent_runtime/api/approval_coordinator.py` (emit
   `gate.resolved`; persist write policy via new `ConnectorWritePolicyClient`).
5. **Decision plumbing.** Modify
   `services/ai-backend/src/runtime_api/schemas/approvals.py` (field + validator),
   `services/ai-backend/src/agent_runtime/api/ports.py` (port), new httpx client file
   `services/ai-backend/src/agent_runtime/api/connector_policy_client.py`, wiring in
   `services/ai-backend/src/runtime_api/app.py` (coordinator construction, ~L427).
6. **UI.** New `packages/chat-surface/src/thread-canvas/TcGateCard.tsx`,
   `packages/chat-surface/src/destinations/run/PostureChip.tsx`; extend B1's projector;
   export via `packages/chat-surface/src/index.ts`; wire both host binders
   (`apps/frontend/src/features/run/RunRoute.tsx`,
   `apps/desktop/renderer/destinationBinders.tsx`).
7. **Parity + smoke.** Vendor the gate-card mock region into
   `tools/design-parity/surfaces/gate-card/`, run the harness to 0 HIGH; run the live
   smoke below. Update SDR §7 S2 if implementation diverged.

## Test plan

ai-backend (`cd services/ai-backend && .venv/bin/python -m pytest <file>`; fakes in
mixins, typed-error assertions — conventions in `tests/CLAUDE.md`):

- `tests/unit/agent_runtime/surfaces_v2/test_tool_access_gate.py` (new dir; mirror the
  test home A1/A3 established for v2 domain code — `VERIFY AT IMPL`):
  - `test_authenticated_card_never_gates` (DoD regression)
  - `test_auth_mode_none_and_unsupported_never_gate`
  - `test_unauthenticated_maps_missing_expired_maps_expired`
  - `test_park_raises_single_interrupt_with_v2_gate_payload`
  - `test_resume_rejected_returns_not_approved` /
    `test_resume_approved_or_approve_with_edits_maps_approved` (decision coercion;
    `write_policy` stays None — not threaded through resume)
  - `test_purpose_builder_caps_length_and_strips_markdown_urls` (untrusted args)
  - `test_op_class_defaults_write_when_classifier_absent` (fail closed, FR-C0)
- `tests/unit/agent_runtime/capabilities/mcp/test_call_tool_gate.py` (beside
  `test_call_tool_surface.py`):
  - `test_flag_off_call_tool_bytes_identical` (snapshot vs pre-C2 result dict)
  - `test_gate_blocks_before_client_creation` (fake provider asserts `create_client`
    never called — the adversarial "cancelled gate ⇒ dependent branch does not
    execute" DoD test)
  - `test_cancelled_gate_returns_typed_auth_failure_no_dispatch`
  - `test_resumed_authenticated_card_dispatches_without_second_interrupt`
  - `test_still_unauthenticated_after_approve_fails_closed_no_loop` (interrupt called
    at most once per invocation)
  - `test_mcp_auth_error_regates_when_flag_on_terminal_failure_when_off`
- `tests/unit/runtime_worker/test_gate_ledger_events.py`:
  - `test_gate_opened_emitted_beside_mcp_auth_event_flag_on`
  - `test_flag_off_no_gate_events_stream_byte_identical`
  - `test_ledger_emit_failure_swallowed_park_still_happens`
- `tests/unit/runtime_api/test_gate_decision_write_policy.py`:
  - `test_write_policy_requires_approved_decision_422`
  - `test_write_policy_on_non_mcp_auth_kind_422`
  - `test_override_persisted_before_decision_and_gate_resolved_ordering`
  - `test_policy_persist_failure_502_decision_not_recorded` (fail closed)
  - `test_gate_resolved_cancelled_on_reject`

chat-surface (`npm run test --workspace @0x-copilot/chat-surface`):

- `packages/chat-surface/src/thread-canvas/TcGateCard.test.tsx` — read-only pledge only
  for read class; policy radio only for non-read; default `ask_first`; ledger id
  rendered; connect/skip callbacks fire.
- projector extension tests beside B1's projector test file — `gate.opened` folds to a
  card, `gate.resolved` resolves it, golden-event fixture (A1) round-trips, posture
  derives `allow_always` ⇒ bypass.
- `packages/chat-surface/src/destinations/run/PostureChip.test.tsx` — normal vs amber
  states.

api-types: extend the decision-request test (`packages/api-types/src/` sibling
`*.test.ts`) for the `write_policy` field + guard.

**Live smoke (desktop stack, step by step):**

1. `make dev` with `SURFACES_V2=true` exported to ai-backend (and
   `RUNTIME_START_IN_PROCESS_WORKER=true` for a single process);
   `export TOKEN=$(make dev-bearer)`.
2. Install + authenticate an OAuth connector via the UI (catalog install →
   `POST /v1/mcp/servers/{id}/auth/start` flow). Run a prompt that uses it → **no gate
   card appears**; run completes (already-authenticated regression, DoD).
3. Force unusable auth: `curl -X POST -H "Authorization: Bearer $TOKEN"
http://127.0.0.1:8200/v1/mcp/servers/{id}/auth/skip` (flips `auth_state` off
   AUTHENTICATED). `VERIFY AT IMPL:` if skip is rejected for an authenticated server,
   delete + reinstall the server instead — the goal is a card whose `auth_state` is not
   AUTHENTICATED.
4. Prompt the same tool → gate card appears on the canvas with purpose/scopes/policy;
   `GET /v1/agent/runs/{run_id}/events` shows `gate.opened` with
   `auth_state: "missing"`; run status `waiting_for_approval`.
5. Click Connect → complete OAuth → choose "Ask me first" → approve. Run resumes at the
   same call and completes; events show `gate.resolved {outcome: "connected",
write_policy: "ask_first"}`; posture chip stays "Writes wait for you".
6. Repeat 3–5 choosing "Allow always" → chip flips amber "Bypass on · writes auto";
   confirm the override landed in backend: `curl -H "Authorization: Bearer $TOKEN"
http://127.0.0.1:8200/v1/mcp/servers` shows the server row with
   `"write_policy": "allow_always"` (C1's field on `McpServerResponse` — no separate
   posture endpoint).
7. Cancel path: re-force unusable auth, prompt, click Skip → gate.resolved
   `{outcome: "cancelled"}`; the tool result is a typed auth failure; the dependent
   action did not execute; run finishes without it.
8. Reload the app mid-park (step 4) → replay reconstructs the gate card (ledger replay).

## Definition of done

From `03-prds.md` PRD-C2 (binding minimums, never weakened):

- [ ] **Live:** revoke a connector token mid-run → gate card appears, run parks;
      reconnect → run resumes at the same call and completes. _Artifact: smoke steps
      3–5 transcript + `GET /v1/agent/runs/{id}/events` excerpt showing
      `gate.opened` → `mcp_auth_required` → `gate.resolved` → `run_completed`._
- [ ] Already-authenticated connectors never gate (regression test on a normal run).
      _Artifact: `test_authenticated_card_never_gates` + smoke step 2._
- [ ] Gate-time policy choice lands in backend override and flips the posture chip.
      _Artifact: `test_override_persisted_before_decision_and_gate_resolved_ordering` +
      smoke step 6 screenshot._
- [ ] Cancelled gate ⇒ dependent branch does not execute (test). _Artifact:
      `test_gate_blocks_before_client_creation` +
      `test_cancelled_gate_returns_typed_auth_failure_no_dispatch`._

Standard DoD (every PRD):

- [ ] Unit tests in ai-backend `.venv`, chat-surface + api-types workspaces pass;
      typecheck + build green.
- [ ] Flags off ⇒ byte-identical behavior. _Artifact:
      `test_flag_off_call_tool_bytes_identical` +
      `test_flag_off_no_gate_events_stream_byte_identical`._
- [ ] No service-boundary violations (apps→facade only; no cross-`src/` imports;
      backend consumed via internal HTTP only).
- [ ] No new LLM call sites (none in this PR — nothing to meter; assert no model client
      construction added).
- [ ] SDR §7 S2 updated if the implementation diverges.

UI DoD (🎨):

- [ ] Gate card + posture chip built from design-system/chat-surface kit recipes only
      (no host-app one-off styling; no raw font-size/letter-spacing).
- [ ] `tools/design-parity/` run against the staged v2 mock's gate-card region:
      **0 HIGH drift**. _Artifact: `tools/design-parity/surfaces/gate-card/out/report.md`._
- [ ] Live desktop smoke of the full flow on the real stack (script above), not just
      tests.

## Out of scope

- ActionClassifier internals, catalog files, and the override **storage** (PRD-C1 —
  consumed here, not built).
- Staged writes / what "held" means downstream of the policy (Wave D).
- Approvals-queue rail cards and pending counter chip (PRD-E2 — the events it needs
  ship here).
- Failure-path visual polish: OAuth cancel/expiry mid-flow UX wording, gate re-entry
  styling (Phase-2 designer track; the states and events must exist and be correct now).
- Removing the legacy in-chat mcp_auth Connect card or v1 `result["surface"]` emission
  (compat window ends at PRD-E3).
- Receipt/Sources rendering of gate rows (PRD-E1).

## Guardrails

- **Service boundaries are hard:** apps → facade only; chat-surface talks through its
  `Transport` port; ai-backend calls backend over internal HTTP with the service token
  (`copilot_service_contracts.headers`) — never import backend code, never share a
  `.venv`, never add a sibling to `PYTHONPATH`.
- **Flag-off byte-identical:** with `SURFACES_V2` unset, every event stream, tool
  result dict, and API response is byte-for-byte what ships today (snapshot tests).
  The additive `gate` payload key and `write_policy` field appear only flag-on.
- **ai-backend rules** (`services/ai-backend/CLAUDE.md`): pydantic at every boundary;
  typed domain errors with safe public messages (never leak vendor OAuth errors);
  helpers inside classes; keys/messages in `Keys`/`Messages` classes; tool payloads
  and arguments are untrusted (purpose line: cap + strip). Tests: fakes only, no
  network/live LLM; assert typed error classes and safe messages; mixins for fakes.
- **Approval integrity:** do not touch the batch primitive, the
  `APPROVE_WITH_EDITS`→`APPROVED` coercion (approval.py L238), or the mcp_auth resume
  shape — the gate rides beside them. Resume is never inline (queue → worker);
  `gate.resolved{connected}` only ever follows a recorded approved decision.
- **chat-surface rules** (`packages/chat-surface/CLAUDE.md`): substrate-agnostic (no
  window/fetch/localStorage — eslint enforces); presentational components + host
  binders (update BOTH hosts); barrel exports only; gate state is a selector over the
  same `session.events` array — never a second SSE subscription.
- **Do not** hand-assemble gate state client-side from the legacy `mcp_auth_required`
  event when the v2 flag is on — ledger events are the source; the legacy event is
  compat-window only.

## Open questions

These are genuinely-deferred design choices, not blockers — the code path and ledger
schema below are correct for launch; each is a future refinement.

1. **`GateAuthState.INSUFFICIENT` is never emitted at launch.** The at-dispatch
   `McpAuthError` (`client.py` L25) is a bare `McpClientError` subclass carrying no scope
   detail, so a vendor scope-shortfall is indistinguishable from an expired token — C2
   maps every at-dispatch auth failure to `EXPIRED` and ships the `INSUFFICIENT` enum
   member unused (SDR §5 keeps it a legal wire value). Open: what signal would let the
   gate attribute a failure to a scope shortfall (a typed `McpScopeError`? an OAuth
   `insufficient_scope` error-code parse?) so `insufficient` can be emitted honestly.
   Deferred to the Phase-2 failure-path track.

2. **Connector slug ↔ `server_id` mapping when two servers expose the same connector.**
   The `gate.opened.connector` ledger field is the server **slug** (`card.name`, SDR §5),
   but C1 keys the write-policy override by `server_id` (`mcp_servers.write_policy`). For
   the single-user desktop launch posture (one installed server per connector) this is
   1:1 and unambiguous. Open: if a user installs two servers advertising the same
   connector slug, the posture chip ("Bypass on") and the per-server override could
   diverge from what the slug-keyed ledger row implies. Revisit only if multi-server-per-
   connector installs become supported; not in scope for launch.
