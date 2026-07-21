# Generative UI — Implementation Plan (Studio Surfaces)

**Status:** engineering design, approved product PRD upstream (artifact `54ad6252`)
**Owner branch:** `claude/generative-ui-components-5b6ba7`
**Execution model:** PRD-per-workstream, implemented in parallel by scoped agents (Wave map at the bottom). Each PRD in this directory is self-contained: context, files, contracts, acceptance criteria, non-goals.

---

## 1. The one architectural pivot (D1)

The product PRD assumed tier-2 = "agent writes adapter source." A code-level read changes this:
`RenderAdapterGenerator` (`services/ai-backend/src/agent_runtime/capabilities/render_adapter_generator/capability.py`) **never calls an LLM** — `AdapterSourceBuilder.build()` deterministically stamps React.createElement source from a `LayoutTemplate` enum + a validated `SampleState`. The generative intelligence was never wired in, and it turns out it isn't needed where everyone assumed.

**Decision D1 — the generative artifact is a *SurfaceSpec*, not component source.**

A SurfaceSpec is a small, schema-validated JSON document that binds a connector tool's output shape to an archetype's slots:

```json
{
  "spec_version": 1,
  "archetype": "record",
  "source": { "server": "seed:linear", "tool": "get_issue" },
  "title_path": "issue.title",
  "subtitle_path": "issue.identifier",
  "fields": [
    { "label": "State",    "path": "issue.state.name" },
    { "label": "Assignee", "path": "issue.assignee.displayName" },
    { "label": "Priority", "path": "issue.priorityLabel" },
    { "label": "Updated",  "path": "issue.updatedAt", "format": "datetime" }
  ],
  "link": { "label": "Open in Linear", "url_path": "issue.url" }
}
```

A **generic, first-party ArchetypeRenderer** (one `SaaSRendererAdapter` per archetype scheme: `record://`, `table://`, `message://`, `doc://`, `board://`, …) interprets `{spec, data, diff}` at render time. The renderer is hand-written, tested, design-system-native, pure-render (D28). The *spec* is what gets generated — by a cheap model guided by a skill (see §3).

Why this beats shipping generated TS on every axis the effort cares about:

| Axis | Generated component source (old tier-2 default) | SurfaceSpec + generic renderer (new default) |
|---|---|---|
| **Cost** | 1–2k output tokens of TS per adapter; needs a capable model to write correct code | 300–800 output tokens of JSON; nano/mini-class model is sufficient; cached once per `(server, tool, output_shape_hash)` |
| **Security** | Executable code → Web Worker sandbox, AST allowlist, smoke render, quality gates, install lifecycle | Data, not code. Schema validation is the entire gate. No execution surface. React text-node escaping renders payload data inert |
| **Latency** | Worker round-trip per render; install pipeline before first paint | Pure synchronous render (<16 ms typical); zero added steady-state latency |
| **Failure mode** | Broken adapter → boundary error → demote | Invalid spec → rejected at validation → tier-3 fallback; never a runtime crash class |
| **Fixability** | Regenerate + re-gate code | Edit a JSON file or the skill; no deploy |

The existing executable tier-2 pipeline (Tier2Loader worker, AST allowlist, quality gates, Tier2Bridge) is **kept, not deleted** — demoted to the escape hatch for genuinely custom layouts the archetype vocabulary can't express. It completes in Wave 4, not Wave 1.

The tier ladder becomes:

- **Tier 1** — bespoke hand-built surfaces (`email://`, `sf-opp://`, `sheet-row://`, `slide://`). Unchanged.
- **Tier 1.5 (new, the workhorse)** — generic ArchetypeRenderers driven by SurfaceSpecs. Curated specs ship in-repo for catalog connectors; generated specs cover the long tail.
- **Tier 2** — executable generated adapters, sandboxed. Exception path, policy-gated, Wave 4.
- **Tier 3** — `GenericStructuredDiff` wildcard fallback. Unchanged; also the instant view while a spec generates.
- **Tier 4** — freeform sandboxed iframe canvas. Out of scope for this effort (product P4).

Prior art (this is a proven shape, not an invention): server-driven UI at Airbnb (Ghost Platform), Lyft, Shopify — a decade of "server sends a layout spec, client renders from a fixed component registry." Google A2UI and Thesys C1 are the same pattern with an LLM emitting the spec. Vercel AI SDK's "generative UI" is the tool→component special case. MCP Apps (2026-01 spec) is where connector-shipped UI is heading. Internal precedent: `ToolDisplayTemplate` (deterministic `str.format` chat cards) is this exact philosophy one level down, and `adapter_allowlist.json` in `service-contracts` is the shared-JSON-contract precedent the SurfaceSpec schema follows.

## 2. End-to-end wiring

```
                     services/ai-backend                                   frontend packages
┌─────────────────────────────────────────────────────┐   ┌──────────────────────────────────────────┐
│ CallMcpTool.ainvoke()                               │   │ eventProjector.project(events)           │
│   └─ result ─► SurfaceProjector.resolve(            │   │   tool_result payload.surface_uri        │
│        server, tool, output)                        │   │     └─► surfaceState[uri] = {spec,data}  │
│        │  1. builtin curated spec  (packaged JSON)  │   │                                          │
│        │  2. SurfaceSpecStorePort  (cached/generated)│  │ TcSurfaceMount(uri)                      │
│        │  3. miss ─► attach tier-3 payload now,     │   │   └─ resolveAdapter(uri)                 │
│        │            enqueue async generation        │   │        record:// table:// message:// …   │
│        ▼                                            │   │        = ArchetypeRenderer(spec, data)   │
│   tool_result payload += {surface_uri, archetype,   │──►│        else '*' GenericStructuredDiff    │
│                           state:{spec?, data}}      │SSE│                                          │
│                                                     │   │ RunDestination                           │
│ surface_spec_generator (async, cheap model + skill) │   │   tabs ⇐ surfaceState keys (by last seq) │
│   validate ─► lint ─► persist ─► emit               │   │   pendingDiff ⇐ approval projection      │
│   surface_spec_generated {uri, spec}                │──►│   approve/reject/edit ─► POST /decision  │
└─────────────────────────────────────────────────────┘   └──────────────────────────────────────────┘
```

Ten load-bearing decisions:

- **D2 — SurfaceSpec SSOT** lives as JSON Schema at `packages/service-contracts/src/copilot_service_contracts/surface_spec.schema.json` (beside `adapter_allowlist.json`, same precedent). Pydantic model in ai-backend validates against it; TS types + runtime guards in `packages/api-types`. A cross-language parity test pins them together.
- **D3 — emission chokepoint** is `CallMcpTool.ainvoke` (it already annotates citation hints post-call — surface projection is the same move) plus `DraftBackend` for the draft/email path. `runtime_api` stays projection-only; domain logic stays in `agent_runtime`. URI grammar: `<archetype>://<server-slug>/<tool-or-resource>/<id>`.
- **D4 — spec acquisition ladder**: builtin → store → generate-async. On miss the user is never blocked: the payload renders via tier-3 instantly, and when `surface_spec_generated` lands the projector merges the spec into `surfaceState[uri]` — the next render upgrades in place. No adapter hot-swap involved; it's just data arriving on the existing one-projection path.
- **D5 — generation is skills-guided and schema-constrained** (§3). Validation loop: JSON-schema → path-lint (every `*_path` resolves against the sample output; labels non-empty; enums legal) → persist → emit. ≤2 retries, then record the failure with the model's output for skill iteration and stay on tier-3.
- **D6 — model routing**: `SURFACE_SPEC_MODEL` env (nano/mini class — Haiku 4.5 / gpt-5-mini / gemini-flash tier) through the existing `init_chat_model` factory in `deep_agent_builder.py` (BYOK/OpenRouter/Ollama-aware for free). Structured output enforced via forced tool-call/JSON-schema mode where the provider supports it. Never a frontier model; never on the render path.
- **D7 — review loop**: approvals carry surface diffs; decision endpoint gains `approve_with_edits`; commit executor does idempotency-key + precondition re-check (re-read the remote resource before write; abort→re-propose on drift); every propose→decision→commit appended to the audit chain. Fail closed.
- **D8 — both hosts** register the archetype pack at bootstrap. Desktop already does (`bootstrap.tsx`); web gains a registration module + a flagged `RunDestination` route (coordinated with the frontend-parity effort).
- **D9 — injection posture**: tool output is untrusted. In generation prompts it is delimited and instructed-as-data; but the real defense is structural — the SurfaceSpec schema has **no side-effectful members** (no handlers, no free URLs except typed `url_path` fields the host sanitizes, no code). Worst-case injection = mislabeled display text. Renderers emit data exclusively as React text nodes (escaped by default). Write actions never originate in a renderer (D28 — host owns all controls).
- **D10 — caching & invalidation**: spec cache key = `(server_id, tool_name, output_shape_hash, spec_schema_version, skill_version)`. Skill bump or tool-schema change invalidates. Store port has `in_memory` (tests), `file` (desktop single-user), `backend-http` (team) adapters, mirroring the `RUNTIME_STORE_BACKEND` pattern.

## 3. Skills + cheap models (the generation subsystem)

The task "map this tool's output onto an archetype" is narrow classification + path extraction — exactly what small models do reliably **when constrained**. Three constraints make nano-class models dependable here:

1. **Schema-constrained decoding.** The model physically emits only SurfaceSpec-shaped JSON (forced tool call / response-format). No prose, no code.
2. **Mechanical validation with retry.** Schema check + path-lint against the real sample output are cheap and deterministic; a wrong path is caught before anything renders. ≤2 retries with the validator error fed back; then tier-3 and a logged failure.
3. **Off the hot path, cached forever.** Generation happens once per `(server, tool, shape)`, asynchronously. A wrong first attempt costs nothing user-visible (tier-3 held the fort).

**Skill bundles** are how we steer them. Layout (packaged in-repo, versioned):

```
services/ai-backend/src/agent_runtime/capabilities/surfaces/skills/spec-authoring/
  SKILL.md            # doctrine: how to choose an archetype; per-archetype slot
                      # cookbook; naming/label conventions; what NOT to map
  schema.json         # symlink-of-truth: the service-contracts SurfaceSpec schema
  examples/           # few-shot pairs: {tool_descriptor, sample_output} → golden spec
    linear.get_issue.json
    gmail.search.json
    github.list_issues.json
    ...
  fixtures/           # validator fixtures for the eval harness (PRD-11)
```

This makes surface quality a **data problem, not a deploy problem**: a bad mapping is fixed by editing the skill or hand-overriding the stored spec — no code change, no release. Per-connector skills can later specialize the generic one (same load path as the existing `capabilities/skills` subsystem).

**Model routing table:**

| Job | Model class | Tokens (in/out) | Cost/unit | When |
|---|---|---|---|---|
| SurfaceSpec generation | nano/mini (`SURFACE_SPEC_MODEL`) | ~3–5k / 300–800 | ~$0.001–0.003 | once per (server, tool, shape); async |
| Tier-2 element-tree/custom layout | small (Haiku-class) | ~6–10k / 1–2k | ~$0.01–0.05 | archetype vocabulary insufficient (rare, Wave 4) |
| Tier-4 freeform canvas | user's frontier model | user-visible | user-initiated | product P4, out of scope here |
| Skill authoring/iteration | frontier, offline | dev-time | dev-time | maintainers only |

Fleet math: all 23 catalog connectors × top ~5 tools ≈ 115 specs ≈ **well under $1 one-time**, and the curated ones ship in-repo generated at dev time — runtime generation is only for the true long tail.

## 4. Cost / security / latency budget (the three constraints, explicitly)

- **Cost.** Steady-state rendering = **0 model tokens** (deterministic renderers + cached specs). Marginal cost exists only at first encounter of an unmapped tool, and is nano-priced + cached. The expensive generation modes (tier-2 code, tier-4 freeform) are exception paths behind explicit triggers.
- **Security.** Default path executes **no generated code**. One schema gate, no worker, no iframe. The heavy machinery (AST allowlist, worker budget, quality gates) already exists and now guards only the rare tier-2 path. Write side effects remain behind the approval token regardless of tier (independent axis). Registry stays module-global for now; per-tenant scoping is a hardening item (PRD-11) gated before any community-origin adapter ships.
- **Latency.** Render budget 100 ms already enforced by `TcSurfaceMount`; archetype renders are sync and small. First-encounter UX: tier-3 paints in the same frame as the tool result; the archetype upgrade lands ~2–6 s later via the event stream. Spec generation is never awaited by the run loop (fire-and-forget task with a per-run generation cap, `SURFACE_SPEC_MAX_GEN_PER_RUN`).

## 5. Wave map & PRD index

Dependency rule: **Wave 0 freezes every cross-PRD interface.** Wave 1 PRDs touch disjoint paths and may run fully in parallel. A PRD must not edit files outside its listed scope.

| Wave | PRD | Title | Scope area | Depends on |
|---|---|---|---|---|
| 0 | [PRD-01](PRD-01-surface-contract.md) | Surface contract (schema + types + event) | service-contracts, api-types, ai-backend schemas | — |
| 1 | [PRD-02](PRD-02-backend-emission.md) | Backend surface emission + builtin curated specs | ai-backend | 01 |
| 1 | [PRD-03](PRD-03-archetype-renderers.md) | ArchetypeRenderer pack | surface-renderers, chat-surface (types only) | 01 |
| 1 | [PRD-04](PRD-04-cockpit-wiring.md) | Cockpit wiring: tabs, activeUri, pendingDiff, decisions | chat-surface | 01 |
| 1 | [PRD-05](PRD-05-web-registration.md) | Web host registration + flagged Run route | apps/frontend | 01 |
| 1 | [PRD-06](PRD-06-text-diff.md) | Word-level text diff + email/doc renderDiff | chat-surface, surface-renderers | 01 |
| 2 | [PRD-07](PRD-07-spec-generator.md) | Spec generator capability + spec-authoring skill + store port | ai-backend | 01, 02 |
| 2 | [PRD-08](PRD-08-spec-registry.md) | Spec persistence: backend registry + backend-http adapter | backend, ai-backend client | 07 |
| 3 | [PRD-09](PRD-09-edit-and-commit.md) | Edit-on-surface + commit gate (approve_with_edits) | api-types, facade, ai-backend, chat-surface | 01, 03, 04 |
| 4 | [PRD-10](PRD-10-tier2-completion.md) | Tier-2 completion: worker 6C + lifecycle unstub | chat-surface, apps/desktop | 03 |
| 4 | [PRD-11](PRD-11-hardening-evals.md) | Hardening: eval harness, metering, injection lint, registry scoping | ai-backend, chat-surface | 07 |

Sub-agent guidance (repo norms): one PRD per agent, ≤~1000 LOC each; PRD-03 is split-friendly (one agent per archetype) if it runs long. Interface freeze: anything defined in PRD-01 is read-only for Waves 1+ — needed changes go back through a PRD-01 amendment, not a local edit.

## 6. What we are explicitly NOT doing (in this effort)

- No tier-4 freeform iframe canvas (product P4; separate effort with its own sandbox design).
- No community adapter marketplace, signing, or install UX (product P5).
- No replacement/removal of the existing tier-2 executable pipeline — it completes (Wave 4) and narrows.
- No new SSE/event transport concepts — everything rides `RuntimeEventEnvelope` + the existing one-projection rule.
- No per-connector bespoke React apps. That is the anti-goal this architecture exists to avoid.
