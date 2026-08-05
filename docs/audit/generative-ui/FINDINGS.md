# Generative UI — wiring audit

**Date:** 2026-08-04 · **Branch:** `claude/dynamic-generative-ui-audit-28d256` (off `cf4fb3c2`)
**Method:** source trace + 6 parallel tracer agents + 3 adversarial verifiers (all three verdicts `CONFIRMED`).
**Scope:** what the "dynamic / generative UI" actually does on the **packaged desktop** path, which specs exist, and which are wired.

> Status of the claims below: every one was re-derived by hand against the files cited, not taken
> from a subagent summary. Where a subagent claim was refuted, the refutation is recorded inline.

---

## 1. The intended design

Generative UI here means **SurfaceSpec**: declarative JSON (an archetype + dot-paths into the payload)
rendered by generic archetype renderers from a client-side registry. No model-authored code executes.

Pipeline, four stages:

| Stage           | Where                                                                                                                                                                                           | Gate                                                                                                                 |
| --------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------- |
| 1. Emit         | `McpPresentMiddleware`, innermost stage of the per-tool MCP pipeline — [per_tool_registration.py:347](../../../services/ai-backend/src/agent_runtime/capabilities/mcp/per_tool_registration.py) | none — unconditional; `call_mcp_tool` + `MCP_PER_TOOL_ENABLED` deleted. Fires only for `Action.READ`                 |
| 2. Resolve spec | `SurfaceProjector` ladder: builtin → store → miss(+async generate) — [projector.py](../../../services/ai-backend/src/agent_runtime/capabilities/surfaces/projector.py)                          | —                                                                                                                    |
| 3. Ledger       | `WorkLedgerEmitter` → `action.classified` / `read.executed` / `surface.created` / `view.derived`                                                                                                | `SURFACES_V2`, **default ON** ([config.py:47](../../../services/ai-backend/src/agent_runtime/surfaces_v2/config.py)) |
| 4. Render       | client hydrates `GET /v1/agent/runs/{id}/surfaces` → `TcSurfaceMount` resolves by URI scheme                                                                                                    | `surfacesV2` prop, **default ON, opt-out** ([featureFlags.ts:19](../../../apps/desktop/renderer/featureFlags.ts))    |

Desktop chat **is** the Run cockpit: `DestinationOutlet` case `"run"` → `RunBinder` → `RunDestination`
([destinationBinders.tsx:1357](../../../apps/desktop/renderer/destinationBinders.tsx)). No second chat screen.

---

## 2. Spec inventory

| Layer                                                                                                                              | Count                                                                                                                                             | Wired?                                                                                                    |
| ---------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------- |
| Curated builtin specs (`builtin_specs/*.json`)                                                                                     | **12** — asana 1, atlassian 2, github 4, intercom 1, linear 2, notion 1, sentry 1                                                                 | matched backend-side; **never reach the renderer** (§3.1)                                                 |
| Catalog connectors (`mcp_catalog.py`)                                                                                              | **13** — asana, atlassian, cloudflare-bindings, cloudflare-observability, github, intercom, linear, notion, paypal, plaid, sentry, square, zapier | 6 have **no spec at all**                                                                                 |
| `SurfaceArchetype` enum ([spec_models.py:26](../../../services/ai-backend/src/agent_runtime/capabilities/surfaces/spec_models.py)) | **10** — record, table, message, doc, board, event, timeline, dashboard, file, **form**                                                           | **5** have renderers (record/table/message/doc/board)                                                     |
| Archetype renderers (`packages/surface-renderers/src/archetypes/`)                                                                 | 5                                                                                                                                                 | registered on **both** hosts at module scope                                                              |
| Registered adapters total                                                                                                          | 13 exact-scheme + 1 wildcard tier-3                                                                                                               | `registerAll()` in [surface-renderers/src/index.ts:129](../../../packages/surface-renderers/src/index.ts) |
| `spec-authoring` skill + few-shot examples                                                                                         | SKILL.md + 8 examples                                                                                                                             | real; feeds the generator only                                                                            |
| Backend spec registry `/internal/v1/surfaces/specs`                                                                                | org-scoped override lane                                                                                                                          | exists                                                                                                    |

**`form` is in the enum with no renderer** — and the SurfaceSpec schema is declaratively read-only
("zero side-effectful members: no handlers, no code"), so it could not express an input or a submit
target even if a renderer existed.

---

## 3. The four breaks

All four are independent. Any one of them alone would degrade the UI; together they make it inert.

### 3.1 Curated specs never cross the wire — **the decisive one**

`SurfaceContentProjection.fold` populates `spec_by_surface` **only** from `SURFACE_SPEC_GENERATED`
events — [content.py:116-122](../../../services/ai-backend/src/agent_runtime/surfaces_v2/content.py). There is no
branch that reads the builtin (rung-1) or store (rung-2) spec.

Meanwhile:

- `surface.created` payload = `{v, surface_id, kind, source, title, payload_ref}` — no spec
  ([emitter.py:332-338](../../../services/ai-backend/src/agent_runtime/surfaces_v2/emitter.py)).
- `view.derived` = `{v, surface_id, tier, basis}` — no spec ([emitter.py:343-350]).
- The v1 `result["surface"]` envelope that used to carry it was **retired in PRD-E3**
  ([surfaces/**init**.py:12](../../../services/ai-backend/src/agent_runtime/capabilities/surfaces/__init__.py)).

**Consequence:** a builtin match sets the archetype, the title, and a ledger label of
`tier: shaped, basis: registry` — then the spec vanishes. The client renders the spec-less
`NoSpecView`. **The ledger records "shaped" over a screen showing raw JSON.**

### 3.2 The spec generator can never get a BYOK credential

[generator.py:1227](../../../services/ai-backend/src/agent_runtime/capabilities/surfaces/generator.py) builds the
shaping model as `build_chat_model_from_id(model_id)` — **no `extra_kwargs`**, the only channel BYOK
keys travel on. For native openai/anthropic/gemini, `build_chat_model` sets no `api_key`
([deep_agent_builder.py:506-509]), leaving `os.environ` as the only source.

The packaged desktop forwards provider keys **only if already present in the launching process env** —
and says so: _"Model-provider keys (dev convenience; BYOK covers packaged installs)"_
([service-env.ts:46-49](../../../apps/desktop/main/services/service-env.ts)). Absent for a Finder/Dock launch.

Failure is silent and **self-perpetuating**: construction failure → scheduler `None`
([run.py:2774-2795]); invoke failure → `GenFailure` → `record_failure` written to the **durable** file
store, which then suppresses every future attempt for that output shape.

Also note `_ShapingDefaults` ([shaping_policy.py:52-58]) maps only `openai`/`anthropic`/`gemini`/`google`.
**OpenRouter, Ollama and custom OpenAI-compatible resolve to `None` — zero generation for those users**
even with a working key.

### 3.3 Tier-2 is a pipe with no source

`RenderAdapterGenerator` is **never constructed in production** — its only non-test reference in `src/`
is a namespace string in `capabilities/__init__.py:5`. So `adapter_generated` is never emitted.

`RUNTIME_TIER2_GENERATION` defaults **off** and is absent from `service-env.ts` **and its passthrough
allowlist** — an operator cannot reach it on a packaged install.

The **consumer** half is fully wired: desktop main constructs `RunFeedLifecycleEventSource`, taps the
run-feed SSE, starts `startTier2Lifecycle` ([main/index.ts:939,950,1032]), and the renderer attaches
`Tier2Bridge` with the real `createTier2WorkerFactory()` ([bootstrap.tsx:110-116]).

### 3.4 The MCP artifact half is dropped

`_output_of` takes `result[1]` — the artifact half — and returns `None` when it is `None`, short-circuiting
to `SKIPPED_NO_OUTPUT` with **no presenter call and no ledger events**
([present_tool.py:158-175, 132-136](../../../services/ai-backend/src/agent_runtime/capabilities/mcp/middleware/present_tool.py)).

In the pinned `langchain-mcp-adapters` (verified in the staged runtime's site-packages):

- `artifact` is `None` unless the server returns `structuredContent` (`tools.py:277-283`) → **no surface at all**;
- when present, artifact is `MCPToolArtifact`, a TypedDict → projector `output` is `{"structured_content": {…}}`.

**Nothing in the repo unwraps `structured_content`** — grep across `src/` _and_ `tests/` returns zero hits.
So `items_path: "issues"` resolves against the wrapper and misses; `TableRenderer` then renders
"0 rows / No rows to display" rather than falling back to raw.

---

## 4. The three journeys

### 4.1 "Tell me about my tasks in Linear" — verdict `CONFIRMED`

**No spec-shaped surface renders.** Best case the code allows is a spec-less generic surface whose body
is `NoSpecView` ("No spec matched _<tool>_"). Likelier on a real Linear server: **nothing is emitted at all**
(§3.4) and the canvas keeps its empty state.

Additional naming fragility, independent of §3.1:

- `tool_slug` does **no** prefix stripping and no `_`/`-` folding ([builtin.py:56-59]) — asymmetric with
  `server_slug`, which _does_ strip a prefix. `linear_list_issues` misses what `list_issues` hits. No alias table.
- The repo's own live-run evidence records Linear's real create tool as **`save_issue`**, not the catalog's
  `create_issue` ([bypass_write_probe.py:165-168], [gate.py:152-162]).
- A **manually-added** (non-catalog) server is named after its slugified URL host
  ([service.py:724-727, 2493-2497]) — never slugs to `linear`, so every builtin lookup misses.
- The same `(server_slug, tool_slug)` key drives **READ/WRITE classification**
  ([catalog.py:114], [descriptor_source.py:128-138] fail-closed to WRITE). A naming miss therefore does not
  merely lose the shape — it can **suppress the surface or misclassify a write**.
- "My tasks" plausibly routes the model to `list_my_issues`, which has no spec.

### 4.2 "Create a task in Linear" — verdict `CONFIRMED`

**No fill-in-the-fields UI exists.** The model authors the entire argument bag; the user gets binary
approve/decline. PDP returns GATE (write axis defaults to ASK), `ToolAccessGate.park_for_approval` parks
the run on an interrupt with a deterministic `mcp_write:<run>:<call>` id ([policy_tool.py:475]).

What renders:

- **`TcWriteGateRow`** — inline in chat, **both modes**. Routed at [TcChat.tsx:880] on the `mcp_write:`
  id prefix, _before_ the question branch and with no `mode` term. JSX is exactly: a 6px dot, a title span,
  a connector span, two buttons. **Zero `<input>`, `<textarea>`, `contentEditable` or `onChange`.**
- **`TcWriteGateCard`** — Studio canvas only ([RunDestination.tsx:4280-4302]). Read-only `<dl>` of params.
  **That `<dl>` is always empty**: `params: buildParams(payload.arguments)` ([approvalProjection.ts:325])
  but the gate payload has 11 keys and no `arguments` ([gate.py:413-436]).

The only argument the user ever sees is the **first scalar**, sanitised into an ≤80-char purpose line:
`"to run create_issue on Linear: <value>"` ([gate.py:198-217]).

Dead-end paths confirmed:

- `EditOverlay` (the real field editor) is mounted but unreachable — gated `surfacesV2 || isScrubbed ? undefined : …`
  ([RunDestination.tsx:3949-3952]); `surfacesV2` is default-true on desktop.
- `approve_with_edits` is allow-listed to **`draft_send` only** and 422s otherwise
  ([approval_coordinator.py:938-985]).
- `GateResume` derives approval purely from `decision`; `answer` and `edits` are **discarded** on this path.
- `stage_rowset_write` `REVIEWED_ROWSET_TARGETS` = `{('linear','update_issue')}` — create explicitly excluded.
- `SurfaceCommitExecutor` never constructed outside tests.

**Caveat that makes the answer less impressive, not more:** `ConnectorWritePolicyOverrides` can downgrade
ASK→AUTO, in which case **no UI renders at all** and the write just happens.

### 4.3 Focus mode — verdict `CONFIRMED`

**Zero spec-rendered surfaces render inline in Focus.** Structural, not incidental: the only three
`TcSurfaceMount` mount sites ([ThreadCanvas.tsx:529, :572], [ArtifactSurface.tsx:465]) all land inside one
div — `run-canvas-slot` ([ThreadCanvas.tsx:508-513]) — styled `display: visible ? "flex" : "none"` with
`showSurfaceColumn = mode === "studio"` ([ThreadCanvas.tsx:451]). The Focus grid template does not even
declare a `surface` area. Repo-wide grep confirms **no fourth mount**.

Note the subtree stays **mounted and hydrating** under `display:none` — cost without benefit.

Focus's entire generative-UI vocabulary:

1. `CanvasFocusCards` — per subject: an eyebrow string (ARTIFACT / RESULT / PROPOSED CHANGE / RUN RECEIPT),
   an `<h2>` title, an "Open in Studio" button. **No data, no rows, no preview.**
2. `GateFocusCard` — "A connection is waiting", **no button at all**.
3. Returns `null` entirely when there are no subjects and no gate.

Renders inline in Focus _and_ Studio (mode-independent): write-gate rows, question cards, approval receipts,
workspace-grant cards, MCP-auth connect cards, fleet cards, `TcTodoList`, `ToolCallCard`.
**Only mode-differing card:** `InlineToolResultCard` (Studio only) — and it only ever emits a CSV summary.

Studio-only: the gate region (`TcGateCard` / `TcWriteGateCard`) and the receipt launch card.

Default mode is **Studio** (`DEFAULT_RUN_MODE = "studio"`, [useRunMode.ts:39]), persisted per conversation
under `chats.thread.<conversationId>.run_mode`. Four handlers actively force `setMode("studio")`.

**Refuted subagent claim:** an earlier tracer said "an irreversible write gate in Focus offers a Review →
button that does nothing." The verifier refuted it — the `Review →` branch is guarded by `irreversible`,
and `buildCategory` ([approvalProjection.ts:475-486]) never emits `destructive`, so `irreversible` is
**always false** and the branch never renders. `onReviewWriteGate` being unsupplied is therefore moot.

---

## 4.4 Correction — we are **not** formless on interaction

An adversarial critic refuted the general framing "the user never gets a form", and it is right. Two
shipped, richer-than-Hermes interaction surfaces exist:

- **`ask_a_question`** ([tools/builtin/ask_a_question.py:24-37]) carries
  `header/question/hint/options[{label,description,recommended}]/multi_select/allow_free_text`, projected
  through a dedicated allow-list ([schemas/events.py:2198-2237]) and rendered by
  [QuestionCard.tsx](../../../packages/chat-surface/src/approvals/QuestionCard.tsx) with multi-select chips
  and a free-text box. Hermes' counterpart `clarify` reads only `{question, choices}` — **ours is strictly richer.**
- **`EditOverlay` + `approve_with_edits` + `SurfaceEdits`** is a real field-level form with **server-side
  merge authority** ([commit.py:292,324]) — `MessageEditForm` (textarea + per-hunk `accepted_hunk_ids`),
  `RecordEditForm` (one input per changed field).

**What §4.2 says remains true and is the narrower, correct claim:** neither surface is reachable _on the
MCP write lane_. `EditOverlay` is gated off whenever `surfacesV2` is true (default on desktop), and
`approve_with_edits` 422s for every kind except `draft_send`. So "create a task in Linear" still gets
approve/decline only.

**The genuine gap is a _model-authored arbitrary_ form — which is a deliberate security posture, not a
missing capability.** SurfaceSpec's read-only schema is what guarantees a model-authored spec can never
reach the write lane except through the PDP and a LangGraph interrupt.

## 4.5 A fifth candidate seam (partially refuted)

The critic claimed a fifth dark seam: `SURFACE_SPEC_MODEL` is set in **zero shipped configurations**
(only 8 PRD docs + `pyproject.toml`), so the generator is dark two gates deep.

**Verified as overstated.** `ShapingModelResolver.resolve` ([shaping_policy.py:85-95]) is a _ladder_:
explicit `SURFACE_SPEC_MODEL` → **else** `SurfacesV2Flag.enabled()` AND a non-`None` `run_provider` →
`_ShapingDefaults.cheapest_for(provider)`. With SURFACES_V2 default-on and a BYOK provider configured, a
model id **does** resolve. The unset env var is therefore not itself fatal — §3.2 (no credential) is the
operative failure. Recorded here so the claim is not re-derived as fact.

The critic's _related_ point does hold: because `SURFACES_V2` defaults ON and PRD-E3 deleted the v1
`result["surface"]` appendage in the same change, the curated-spec path has **no surviving route** to the
client — not merely a broken one.

## 4.6 New findings from the adversarial pass (all hand-verified)

**a) 5 of our 11 CI gates run in no workflow at all.** Verified by enumerating
`tools/check_*.py` against `.github/workflows/`:

| Gate                                       | Workflows referencing it                  |
| ------------------------------------------ | ----------------------------------------- |
| `check_audit_in_transaction.py`            | **0**                                     |
| `check_f_series_contract_authority_map.py` | **0**                                     |
| `check_llm_provider_imports.py`            | **0**                                     |
| `check_migration_manifest.py`              | **0**                                     |
| `check_reader_methods.py`                  | **0**                                     |
| `check_css_selector_shadowing.py`          | 1                                         |
| `check_dark_capabilities.py`               | 1                                         |
| `check_e2_final_conformance.py`            | 1 (path-filtered, non-failing by default) |
| `check_orphan_destinations.py`             | 1                                         |
| `check_route_scopes.py`                    | 2                                         |
| `check_service_boundaries.py`              | 2                                         |

Each of the five dark gates has a companion `tools/test_check_*.py` that also never runs. This is our own
dark-capability pathology, applied to the gates that exist to catch it.

**b) The dark-capabilities gate is structurally blind to every generative-UI flag.** Verified at
[check_dark_capabilities.py:61,87](../../../tools/check_dark_capabilities.py):
`_RUNTIME_TOKEN = re.compile(r"RUNTIME_[A-Z0-9_]+")` and
`_is_capability_flag = name.endswith("_BACKEND") or "_ENABLE_" in name`.

- `RUNTIME_TIER2_GENERATION` — matches the regex, **fails the predicate** → invisible.
- `SURFACES_V2`, `SURFACE_SPEC_MODEL` — **fail the regex outright** → invisible at any severity.

The gate's own docstring says _"New opt-in capabilities should adopt the `RUNTIME*ENABLE*_` name to stay
in scope"\* — every generative-UI flag violated that convention, and the gate could not notice.

**c) `recursion_limit` is never set.** Verified: the only occurrence under `services/` is an unrelated
Monty interpreter test. Every production run inherits LangGraph's default **25 super-steps**, while
`capabilities/tool_budget_guard.py:78-80` proves we knew the limit existed. Paired with the whole-graph
`asyncio.timeout(180s)` sourced from a per-model-call field ([handlers/run.py:2504]), the loop carries two
undeclared ceilings.

**d) Our own source admits the tier-3 floor reads as an error.**
[TcSurfaceMount.tsx:307-322](../../../packages/chat-surface/src/thread-canvas/TcSurfaceMount.tsx) short-circuits
to `SurfaceEmptyState` because tier-3's `renderCurrent({})` _"paints a card of placeholder tokens
('(unknown saas)', '(no resource id)', '(no fields)') that reads to a user like an error."_

**e) The consent-card substring heuristic corrupts three fields, not one.**
[stream_events.py:1300-1321](../../../services/ai-backend/src/runtime_worker/stream_events.py) classifies by a
token scan over `create|post|send|update|delete|write`. Notion's catalogued-destructive `archive_page`
therefore ships to the card as label `action`, `read_only=True`, `risk_level="low"` — three wrong facts
from one heuristic. This is a **safety** defect, not cosmetic.

**f) The 10-archetype enum is a liability, not a feature.** The generator is licensed to emit 10
archetypes ([spec_models.py:26-45]) against 5 registered adapters
([archetypes/index.ts:47-53](../../../packages/surface-renderers/src/archetypes/index.ts)). The registry
already knows the implemented set at runtime; **nothing reports it back to the generator**. A capability
handshake fixes this with no schema change and no A2UI decision.

## 4.7 LIVE e2e run — two further breaks that unit tests cannot see

Run on the real packaged app (`tools/desktop-journeys/surface-floor/floor_e2e.py`): real Electron,
embedded PostgreSQL, the three Python services, a real agent turn, and a real MCP read over a loopback
fixture connector (`tools/desktop-journeys/surface-floor/fixture_mcp.py` — a test double at the _network_ boundary only, because
every catalog connector needs an OAuth authorization a journey must not perform).

**What the live ledger proves works** (read from the run's own `events.jsonl`):

```
surface.created  {surface_id: table://incidents/list_incidents/…, kind: table,
                  spec: [spec_version, archetype, source, title_path],   ← §3.1 FIXED, live
                  payload_ref: "call:unattributed"}                       ← BROKEN
view.derived     {tier: shaped, basis: schema}                            ← honest provenance, live
```

So the contract widening, the transport allow-list pass-through, the inference floor and the tier/basis
mapping are all **confirmed live**. The surface still renders empty. Two new breaks:

### 4.7a `call_id` is lost on the live tool path — every surface is unjoinable — **STILL OPEN**

> **Attempted fix, re-run, still failing.** `McpPresentedTool._call_id` now falls back to the OBSERVE
> stage's `McpCallBindingScope`, mirroring `McpCitedTool`. Re-staged, re-ran the journey: `payload_ref` is
> still `call:unattributed`. The fallback cannot work, and `McpObservedTool._tool_call_id`'s own docstring
> says why — MCP tools built by `langchain-mcp-adapters` never declare
> `Annotated[str, InjectedToolCallId]`, so the kwarg "is normally `None`", deliberately, because adding the
> field would break the schema identity the composer enforces. The binding therefore carries no id either.
>
> **The id is not available anywhere in the tool layer.** It exists one layer out: the worker persists
> `tool_result.call_id = "call_ZZUK…"` because LangGraph emits the `ToolMessage` with it. So a real fix is
> architectural, not a patch — candidates: read it off `RunnableConfig` if LangGraph threads it there;
> correlate in the worker and rewrite the ref; or stop using a reference and have the surface carry the
> payload it was inferred from. **Do not attempt this under time pressure — pick the seam deliberately.**

[present_tool.py:136](../../../services/ai-backend/src/agent_runtime/capabilities/mcp/middleware/present_tool.py):

```python
call_id = str(kwargs.get("tool_call_id") or PresentValues.ANONYMOUS_CALL)
```

LangChain does not pass `tool_call_id` in `kwargs` for a `StructuredTool` on the agent path, so **every
live surface is stamped `payload_ref: "call:unattributed"`**. `SurfaceContentProjection` joins a surface to
its data by `payload_ref → call_id → tool_result.output`; with a constant sentinel that join can never
succeed, so `state.data` is always absent and the card renders "The tool returned an empty payload."

Unit tests pass `call_id` explicitly, so they never enter this state.

### 4.7b The persisted `tool_result.output` is the CONTENT half, not the artifact — **FIXED**

> `_McpContentDecoder` in `surfaces_v2/content.py` recovers the structured payload from the
> doubly-encoded content envelope the run persists
> (`{"content": "[{\"text\": \"{\\\"incidents\\\": …}\"}]"}`), verified against the real shape taken from a
> live run's `events.jsonl`, and total over every other input. **This fix is correct but currently
> unobservable**, because 4.7a means the fold never reaches the data-assignment line it guards.

Every `tool_result` event in the live run carries `output` with keys `['content']` — the model-facing text
half. But the projector infers the spec from the **artifact** half (verified: the adapter returns
`{'structured_content': {'incidents': [...]}}`, `_output_of` peels it, and the projector correctly yields
`table` / `rung=inferred` / `items_path=incidents`).

So the spec is derived from one representation and the data served from another. Even with 4.7a fixed,
`items_path: "incidents"` would not resolve against `{"content": …}`.

**Net:** the ladder is correct end to end; the **data delivery** join is broken on the live path. This is
the same seam class as the original four, found the same way the audit said it must be — by driving the
real thing.

## 5. Built-but-unwired inventory

Carrying cost at zero user value:

| Thing                                                  | Status                                                                                           |
| ------------------------------------------------------ | ------------------------------------------------------------------------------------------------ |
| `ViewDeriver.derive` (the v2 shaping ladder)           | never called in `src/`; only production `ViewDeriver` is built `scheduler=None` for `regenerate` |
| `SurfaceCommitExecutor`                                | never constructed outside tests; still an open follow-up in `STATUS.md`                          |
| `EditOverlay`                                          | mounted, unreachable (§4.2)                                                                      |
| `form` archetype                                       | in enum + JSON schema + api-types; no renderer, schema can't express inputs                      |
| `RenderAdapterGenerator` / tier-2 producer             | never constructed (§3.3)                                                                         |
| `workspaceStageProjection` `compact: mode === "focus"` | no production caller; sole mount omits `mode`                                                    |
| `onReviewWriteGate`                                    | declared + called; supplied by no host                                                           |
| `irreversible` / `Review →`                            | can never be true                                                                                |
| `skill.json` `model_hint: "nano"`                      | parsed, stored, never consulted for model selection                                              |
| `raw` view tier                                        | live emitter only ever writes `shaped` or `generic`                                              |

---

## 6. Why the tests didn't catch it

All four breaks sit at **injected seams**: a scheduler, a store, a completion port, an adapter tuple.
Tests inject past them.

- The only factory test injects a stub `completion`, so `build_chat_model_from_id` — the step that needs a
  credential — **never executes in CI**. `run.py::_build_surface_generation_scheduler` has **no test at all**.
- The P1b write-gate regression test ([RunDestination.test.tsx:1224]) seeds the id `"mcp-write-1"` (hyphens),
  which does **not** match the `mcp_write:` prefix — so it asserts the QuestionCard path that production
  never takes. [TcChat.test.tsx:1012] uses the real shape and covers the live branch.

This is the same failure mode already recorded three times in this codebase (injected deps hiding a dead
feature; in-memory adapters hiding Postgres-only bugs; a fix landing on a branch production never takes).

---

## 7. Scalability assessment

The spec-per-`(server, tool)` model is **O(servers × tools)** and cannot converge:
12 specs for 13 connectors, while Linear alone advertises 52 tools
(`docs/plan/mcp-tooling-program/PRD.md:63`) and the MCP ecosystem has thousands of servers.

The design's answer to the long tail is tier-1 generation — **which is exactly the piece that is dark**.
The scalable half is off; the unscalable half is what ships.

The join is also brittle across a boundary we do not own: no aliasing, no version tolerance, and a
connector renaming a tool silently downgrades the UI _and_ can flip its read/write classification.

---

## 8. Smallest high-value fix

Have `SurfaceContentProjection` source the spec from the **builtin/store rung**, not only from
`surface_spec_generated`. That single change lights up all 12 curated specs without touching the model
path, the credential problem, or tier-2.

§3.4 (`structured_content` unwrapping) is the necessary second fix — without it the specs light up but
their paths still miss.

---

## 9. Open questions

- Should `SurfaceSpec` be replaced by **A2UI** (Apache-2.0, v0.9.1, has the action/event model
  SurfaceSpec explicitly lacks)? See `HERMES-COMPARISON.md`.
- Is `ConnectorWritePolicyOverrides` ASK→AUTO reachable from the desktop UI? If so, writes can execute
  with no visible affordance.
