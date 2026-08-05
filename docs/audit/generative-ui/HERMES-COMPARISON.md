# Hermes Agent vs 0xCopilot — architecture + generative-UI comparison

**Date:** 2026-08-04
**Subject:** [NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent) (MIT, `--depth 1` clone at
`~/Documents/work/hermes-agent`, 214 MB, 3814 `.py` + 1979 `.ts/.tsx`) vs this repo.
**Companion docs:** [FINDINGS.md](FINDINGS.md) (our generative-UI wiring audit), [A2UI-NOTES.md](A2UI-NOTES.md).

## Method and coverage — read this first

11 comparison dimensions were dispatched, each agent reading **both** repos and citing file:line on both
sides, followed by an adversarial critic instructed to find claims that flatter our codebase without
evidence. Coverage was **not** clean:

| Dimension                                                     | Status                                                                                                                                                  |
| ------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------- |
| protocols, tool-result-render, agent-loop, tools-mcp, testing | ✅ first run                                                                                                                                            |
| desktop-ui, topology, approvals-safety, persistence, skills   | ✅ recovered on a second run with anti-stall constraints                                                                                                |
| **widget-sdk**                                                | ❌ **failed twice** (stalled mid-stream). Written below from my own first-hand reading of `ui-tui/src/sdk/` — narrower than the others, flagged inline. |
| critic                                                        | ✅                                                                                                                                                      |

Claims marked **[verified]** were re-derived by hand against the cited files. Everything else is a
subagent claim with citations, not independently checked — treat accordingly.

---

## The verdict in one paragraph

**Hermes is a simpler system that works; we are a more principled system that is partly dark.** They own
their agent loop (~27k lines) and buy their UI kit; we buy our agent loop (`create_deep_agent(**kwargs)`)
and hand-write our UI (~154k lines with essentially no kit). They have zero boundary enforcement and a
7-way cyclic import graph; we have machine-checked service boundaries — and our generative UI still went
dark in four places with 13k tests green, a failure mode their direct-import topology structurally cannot
produce. On the specific question that started this: **Hermes has no declarative generative UI at all, and
neither repo contains a single reference to A2UI, AG-UI, or MCP Apps.** There is nothing to copy from
Hermes on the format axis — but there is a great deal to copy on the _floor_, the _approval legibility_,
and the _skills lifecycle_.

---

## Dimension summary

| Dimension                  | Who's ahead            | The one-line reason                                                                                          |
| -------------------------- | ---------------------- | ------------------------------------------------------------------------------------------------------------ |
| Generative UI format       | **neither**            | Hermes has none; ours is real but doesn't reach the screen                                                   |
| Tool-result floor          | **Hermes**             | A wrapper-peeling JSON→prose summarizer that works on any tool with zero config; ours says "No spec matched" |
| Widget / code execution    | **different**          | Three distinct positions on one axis (below)                                                                 |
| Protocols                  | **Hermes**             | ACP + A2A + MCP, bidirectional; we speak one, outbound only                                                  |
| Agent loop                 | **different**          | They own it and get control; we rent it and get better plumbing                                              |
| Tool layer / MCP mechanics | **split**              | Our trust boundary is better; their MCP mechanics are far ahead                                              |
| Approvals / safety         | **split**              | We're safer on unknown ops; they're safer on catastrophic ones and _far_ more legible                        |
| Desktop UI                 | **Hermes**             | 91% of their renderer size while buying none of the kit                                                      |
| Topology                   | **ours (honestly)**    | Real, machine-checked boundaries vs a 7-way import cycle — but see the caveat                                |
| Persistence                | **Hermes**             | One SQLite file + two markdown files vs embedded Postgres + an 18.7k-line JSONL hybrid                       |
| Skills                     | **Hermes, decisively** | ~18.5k lines of real self-improvement vs ~1,400 lines and none                                               |
| Testing                    | **Hermes**             | They fake the external system; we fake the internal seam — which is exactly how our four seams went dark     |

---

## 1. Generative UI — the headline

**Hermes has no schema-driven or model-driven rendering of tool results.** No SurfaceSpec analogue, no
archetypes, no registry, no spec generator. Instead, two unrelated channels:

**(a) A hand-written projection with a generic floor.** `buildToolView(part, inlineDiff) -> ToolView` — a
fixed ~22-field struct — painted by one 997-line React component, with bespoke branches for ~10 tool names
and a 23-entry icon/tone table. Everything else falls to `formatToolResultSummary`
(`apps/desktop/src/lib/tool-result-summary.ts:463`), which:

1. peels wrapper keys `data|result|output|response|payload` up to **4 levels deep** (`unwrapPayload`, :321-340);
2. renders records as `- Title Case Key: value` bullets ordered by a priority list
   (`title,name,path,file,url,status,id,message,summary,description`);
3. renders arrays as lists capped at 6 with `… N more items`, recursing to depth 4;
4. infers a count (`results|items|matches|files|documents|sources|rows`) and puts a "12 issues" chip on the row.

**This is the single most important thing in the comparison.** It is ~200 LOC, needs no model call and no
configuration, and it means **Hermes never renders "No spec matched."** Its `unwrapPayload` peel would, on
its own, defeat the `structured_content` wrapper that breaks our spec paths (FINDINGS.md §3.4).

**(b) Model-authored HTML/SVG in a sandbox.** `detectArtifact(language, code)`
(`apps/desktop/src/lib/artifact-detect.ts:106-155`) uses size/shape thresholds (HTML ≥160 chars if a
document, SVG ≥2000, code ≥48 lines) to promote a fenced code block to an artifact card, wired
**unconditionally, no flag**, into the markdown renderer. HTML renders in
`<iframe sandbox="allow-scripts" srcDoc={...}>`; SVG through DOMPurify.

The TUI has no structured rendering at all: `Tool Name("ctx") (1.2s) :: detail ✓`.

Their own worst spot is the mirror of ours: **no `mcp__` prefix stripping anywhere in the UI**, so a Linear
MCP call titles itself _"Mcp Linear List Issues"_.

### The code-execution axis — three positions

| Position                         | Who                                                | Author | Trust                                         |
| -------------------------------- | -------------------------------------------------- | ------ | --------------------------------------------- |
| Declarative data, client catalog | **ours** (SurfaceSpec), A2UI                       | model  | no code executes                              |
| User-authored trusted code       | **Hermes TUI widgets**                             | _user_ | full TUI privileges                           |
| Model-authored sandboxed code    | **our tier-2** (dead), **Hermes artifacts** (live) | model  | Worker w/ scrubbed globals · `iframe sandbox` |

**Widget-sdk detail** _(first-hand, narrower than other dimensions)_: `ui-tui/src/sdk/userWidgets.ts` —
drop `<name>.mjs` into `$HERMES_HOME/tui-widgets/`, default-export `register(sdk)`, and it appears in `/`
completions automatically ("the registry is the catalog"). The SDK is a closed object passed _into_
`register()` — `Box, Text, Dialog, Overlay, WidgetGrid, GridAreas, Accordion, Shimmer, React,
h: React.createElement, gauge, hbars, sparkline, sparkRows, openWidget, updateWidget, defineWidgetApp,
isCtrl, useShimmerPhase` — because "user files have no resolvable import path to the bundle." Trust model
is explicit and unapologetic: _"files under HERMES_HOME execute with the TUI's privileges."_ **No
sandboxing.** That is defensible precisely because the author is the _user_, not the model — a different
threat model from our tier-2.

Note what this means for our dead tier-2: **our position on this axis is the most ambitious of the three,
and it is the only one not shipping.**

---

## 2. Where Hermes is meaningfully ahead

**Skills — decisively, and it is not marketing.** _(GEPA does not exist: zero `\bGEPA\b` matches in the
repo.)_ But the loop it presumably names is real and ~18,500 lines: a post-turn **forked review agent that
writes skills** (`agent/background_review.py`, 1065 lines) plus an inactivity-triggered **Curator** that
ages, archives, backs up and consolidates them (`agent/curator.py` 2019 + `hermes_cli/curator.py` 850 +
`tools/skill_usage.py` 1145). 71 in-repo `SKILL.md` packages across 14 categories plus 111 opt-in, with a
two-layer-cached, **conditionally filtered**, truncated index in the system prompt and progressive
disclosure. Ours: ~1,400 lines, two disjoint skill systems that needed a projection shim to appear in one
UI list, 5 seeded + 2 filesystem skills, no conditional visibility, no usage telemetry, no lifecycle, and
**no self-improvement of any kind** — our runtime skill provider is GET-only by construction and
`write_skill` has zero production callers.

**Persistence.** One SQLite file (`$HERMES_HOME/state.db`, 9 tables) + two **user-editable markdown files**
(`memories/MEMORY.md`, `USER.md`) gives them FTS5 full-text search over every message, cost accounting,
compaction, export/import and multi-instance isolation. We run an **embedded PostgreSQL cluster** plus a
bespoke **18.7k-line JSONL + content-addressed-blobs + SQLite-catalog hybrid** for one user, with `org_id`
threaded through every on-disk path of a solo app. Tell-tale cost: the zonky bundle ships no `psql`, so we
stage a Python interpreter solely to talk to our own database.

**Testing — the finding that explains our four dark seams.** Both are unit-heavy at similar scale (Hermes
23,150 `def test_`; ours ~10,000 Python + 689 TS files). The difference is _where the fake is drawn_.
Hermes fakes the **external** system: `apps/desktop/e2e/boot.spec.ts` launches real Electron into a real
`hermes serve` backend pointed at a real HTTP mock inference server — everything internal is production
wiring. We fake the **internal seam**: `services/ai-backend/tests/CLAUDE.md:3` mandates "Focused unit tests
with fakes", and each of our four dark seams has a test that injects past the exact join that broke.

**Desktop UI economics.** Their renderer is 187,506 LOC on **69 runtime dependencies**; ours is 154,312 LOC
on essentially none (`apps/desktop/package.json` declares exactly one runtime dep: `playwright`). We are
~91% of their size while buying none of the kit — the ~13k LOC of thread/composer/branching machinery,
virtualization, and a11y-by-construction from radix, we hand-wrote or **do not have**. We have no
virtualization anywhere; they virtualize both the message list and the session sidebar.

**Protocols.** ACP 0.9.0 (agent server for Zed/VS Code/JetBrains), A2A v1.0 (both directions), MCP as
client _and_ stdio server. They define their own protocol only where no standard exists.

**MCP mechanics.** They namespace every tool `mcp__<server>__<tool>` — we don't, so **two connectors both
exposing `search` means one is silently dropped for the whole run**. They repair broken vendor schemas
(`_normalize_mcp_input_schema`: `definitions`→`$defs`, nullable-`anyOf` collapse, pruning `required` names
no property defines). Their `mcp_tool.py:5029-5041` has **no branch that drops a successful call** — the
direct fix for our §3.4.

---

## 3. Where we are genuinely ahead

- **The Work Ledger.** 34 pinned cross-language event types (`effect.staged/claimed/applied/reconciled`,
  `gate.opened/resolved`, `receipt.emitted`) answering _"what did the agent change, who approved it, was it
  applied exactly once."_ Hermes has **no counterpart** — its `messages` table records that a tool ran and
  what it returned, full stop. And unlike the generative-UI layer, **this one is live.**
- **Trust boundary on MCP.** The vendor credential never enters the agent process; every MCP frame is
  tunnelled through `backend`'s proxy. Hermes stores OAuth tokens on disk in the agent process.
- **Renderer has no network.** A zod-validated IPC channel allowlist crosses to main, which does the
  HTTP/SSE — the renderer never holds a URL or bearer. Hermes' renderer owns the WebSocket client directly.
- **Streaming contract.** Typed 60+ event vocabulary persisted with monotonic `sequence_no`, resumable via
  `?after_sequence=N`.
- **Interaction richness.** Our `ask_a_question` (`options` with descriptions + `recommended` flags,
  `multi_select`, `allow_free_text`) is **strictly richer** than Hermes' `clarify`, which reads only
  `{question, choices}`. **[verified]**
- **Artifacts.** We already ship a richer system: `publish_artifact`/`revise_artifact` tools,
  `ArtifactKind` with a causal-lane sealing model, server-persisted revisions, editor, revision review,
  bounded restore, five renderers. Hermes' counterpart is a renderer-side nanostore.
- **Boundaries.** Hermes has _zero_ enforcement — no import-linter, no tach, no pre-commit,
  `ruff select = ["PLW1514"]` (exactly one rule), no `no-restricted-imports` — producing a 7-way cyclic
  import graph and single-file monoliths of **17,700 / 13,905 / 12,540 lines**.

---

## 4. The A2UI decision — I was wrong, and here is the correction

**My earlier recommendation ("adopt the format, don't rewrite the system") was wrong.** The adversarial
critic refuted both load-bearing arguments in [A2UI-NOTES.md](A2UI-NOTES.md), and I verified the
refutations. **Revised recommendation: build on what we have. Do not adopt A2UI now.**

Why the original argument fails:

1. **"The action model is the unlock" — false premise.** We are not formless on interaction. We already
   ship `ask_a_question` + `QuestionCard` (richer than Hermes' `clarify`) and `EditOverlay` +
   `approve_with_edits` + `SurfaceEdits` — a real field-level form with **server-side merge authority**.
   What SurfaceSpec's read-only schema _buys_ is that a model-authored spec can never reach the write lane
   except through the PDP and a LangGraph interrupt. A2UI's action model doesn't hand us that gate; it
   hands us a second path _into_ it that we then have to re-gate. Same hard work, plus a foreign schema.
   **The genuine gap is model-authored _arbitrary_ forms — a deliberate posture, not a missing capability.**
2. **"Streaming" — mistakes a wiring defect for a format defect.** Our spec doesn't arrive late because
   it's monolithic. It doesn't arrive _at all_ (FINDINGS.md §3.1). A flat streaming format changes nothing
   about a projection that receives no spec.
3. **Interop — the one argument that would justify adoption — fails on transport.** A2UI rides A2A or
   AG-UI. **We speak neither.** Our only clients are our own Electron app over SSE and our own IPC
   allowlist. Format without transport buys literally zero third-party reach.
4. **"We stop maintaining a schema" is inverted.** A2UI's React renderer is _planned, not shipped_. We'd
   start maintaining someone else's pre-1.0 renderer while still owning the 12 curated specs (rewritten),
   the archetype→catalog mapping, the injection linter and the eval corpus.

**Take A2UI's component-catalog _shape_, not the dependency.** Our real, code-proven defect is a closed
10-value enum against 5 registered adapters — the generator is licensed to emit `form`, `dashboard`,
`timeline`, `event`, `file`, and every one falls to a generic view. **The registry already knows the
implemented set at runtime; nothing reports it back to the generator.** An open, registry-advertised
catalog with a capability handshake — modelled on Hermes' relay `CapabilityDescriptor`
(`gateway/relay/descriptor.py:36-38`: frozen dataclass, `contract_version`, explicit per-capability flags
negotiated once at connect) — fixes this **inside our own schema, with no migration**.

**Revisit A2UI only when both hold:** its React renderer has shipped, _and_ we've committed to an inbound
protocol (ACP or A2A) that gives a third-party client a reason to render our surfaces.

> The one line in A2UI-NOTES.md that was exactly right, and which its own recommendation then ignored:
> _migrating a format over a dark pipeline just produces a different dark pipeline._

---

## 5. What to steal — ranked

1. **Fix `_output_of` by mirroring `mcp_tool.py:5029-5041`** — never return `None` for a call that
   succeeded; fall back to wrapping the content half. One branch. Nothing else in the stack matters until
   an MCP read reliably reaches the ledger.
2. **Port `unwrapPayload` + `formatToolResultSummary`** (~200 LOC, no model call) as the tier-3 floor. The
   4-deep `data|result|output|response|payload` peel alone defeats the `structured_content` wrapper. Sized
   honestly: it returns a _string_ and replaces our one-line detail — not `NoSpecView` wholesale.
3. **Emit `arguments` in the write-gate payload.** The client is already written and waiting:
   `buildParams`/`buildTarget` exist, are wired, and return empty on every write today. One backend key
   turns a dead code path live. Also render `gate.purpose`, which the backend already computes and the row
   drops.
4. **Namespace MCP tool names** `mcp__<server>__<tool>`. Today two connectors exposing `search` means one
   is silently dropped for the whole run.
5. **Make the archetype set registry-advertised** so the generator is told 5, not 10. No schema change, no
   A2UI decision. _(This is the A2UI catalog-shape idea, implemented locally.)_
6. **Move the fake boundary out to the external system.** One Playwright-Electron spec launching the built
   app against the real supervised runtime with only a fake inference server. This is the change that would
   have caught all four dark seams.
7. **Skills: conditional visibility → usage telemetry → lifecycle.** In that order. Conditional visibility
   (`_skill_should_show`) is the single change that lets the library grow past 5 skills without a linear
   token tax. Then a usage sidecar (a few hundred lines) — the precondition for any curation. Take the
   provenance ContextVar (78 lines) _before_ any self-improvement work, and the **do-not-capture list
   verbatim** (`background_review.py:275-297`) — hard-won prompt content that transfers with zero code.
8. **Scoped approvals** — once / this-session / always / decline, plus a user-local never-list. Ours is
   binary, so every repeated write re-prompts: the classic path to users flipping on BYPASS wholesale. Also
   distinguish **timeout from denial**, and give the write gate an expiry (ours has none, so an abandoned
   run parks forever).
9. **A hardline floor beneath `Posture.BYPASS`** — a small closed set of destructive op-classes that BYPASS
   cannot lift.
10. **Set `recursion_limit`** and split `ModelConfig.timeout_seconds` into a per-call timeout and a
    run-level deadline. **[verified]** — see FINDINGS.md §4.6(c).
11. **Virtualize the message list and sidebar**, and steal `syncRepositoryIncrementally` as an idea
    (cache normalized messages by source identity; write only the tail that moved).
12. **SQLite for `single_user_desktop`** behind the existing `RUNTIME_STORE_BACKEND` registry seam — that
    seam exists precisely for this. Plus FTS5 over the transcript, which is the highest-leverage thing a
    persistent store buys a desktop user and which we don't advertise at all.
13. **User-editable markdown memory.** A user can open, diff, correct and delete what the agent believes
    about them. Our `memory_items`/`memory_proposals` are unreachable without the facade — worse privacy
    ergonomics for strictly more machinery.
14. **ACP as an inbound adapter** — puts the runtime inside Zed / VS Code / JetBrains without us building
    an editor, and our mapping work is _smaller_ than theirs because we're already event-driven.

---

## 6. What NOT to steal

- **The open union `| (string & {})`** on their gateway event type — it makes any Python-invented event
  type-check on the TS side. Our 34 pinned event types are strictly better.
- **`ruff select = ["PLW1514"]`** — exactly one lint rule, everything else off.
- **Zero boundary enforcement.** Their 7-way import cycle and 17,700-line monoliths are the cost.
- **"Adopt the artifact ladder as a second generative-UI path."** We already have a richer artifact system.
  The real gap is exactly one capability: `CodeArtifactRenderer.tsx:8` states it _"never evaluates,
  highlights via HTML, or mounts a preview iframe."_ The correct steal is an **opt-in sandboxed preview on
  the renderer we already own**.

---

## 7. Defects found in OUR code during this comparison

All recorded in [FINDINGS.md §4.6](FINDINGS.md) with verification status. Summary:

- **5 of our 11 CI gates run in no workflow at all** **[verified]** — our own dark-capability pathology,
  applied to the gates that exist to catch it.
- **`check_dark_capabilities.py` is structurally blind to every generative-UI flag** **[verified]** —
  `RUNTIME_TIER2_GENERATION` fails its predicate; `SURFACES_V2` and `SURFACE_SPEC_MODEL` fail its regex.
- **`recursion_limit` is never set** **[verified]** — every run inherits LangGraph's default 25 super-steps.
- **The consent-card substring heuristic corrupts three fields** — Notion's catalogued-destructive
  `archive_page` ships as label `action`, `read_only=True`, `risk_level="low"`. A **safety** defect.
- **Tier-2 is not "a consumer waiting for a producer"** — it is a complete, AST-allowlisted, smoke-gated,
  worker-sandboxed adapter _distribution pipeline_, wired live in `apps/desktop/main/index.ts:638-639`,
  with zero suppliers.
- **`agent_runtime/persistence/schema/postgres.py`** declares 30 tables for a backend that commit
  `e03840ed` removed, and **executes a read of a deleted file at import time**. Nothing imports it.
- **`ci-ai-backend.yml` is path-filtered** to Python paths — so a change to `packages/surface-renderers`,
  `packages/chat-surface` or `apps/desktop` (the entire client half of the generative-UI pipeline) runs
  neither the fake-model harness nor any Python test.

---

## 8. The uncomfortable summary

Hermes ships a _simpler_ system that _works_. Our differentiators — the Work Ledger, the PDP, the trust
boundary, the service split — are real and are things Hermes genuinely lacks. But we paid for them with a
hand-written UI kit at 91% the size of theirs, an 18.7k-line persistence hybrid plus embedded Postgres for
one user, and a test strategy that faked exactly the seams that broke.

The generative UI is the sharpest instance: **we designed the most ambitious of the three positions on the
code-execution axis, and it is the only one not shipping** — while Hermes' 200-line heuristic summarizer,
which needs no model and no configuration, does a strictly better job than our dark ladder for every
uncurated connector.
