# A2UI — research notes and comparison to SurfaceSpec

**Date:** 2026-08-04 · **Sources:** web research (see bottom). Not yet validated against the A2UI repo on disk.

## What A2UI is

**A2UI (Agent-to-User Interface)** — an open protocol for agent-driven interfaces. Created by Google with
contributions from CopilotKit and the OSS community. **Apache-2.0**, currently **v0.9.1** (pre-1.0).
Repo: `github.com/a2ui-project/a2ui`. Site: `a2ui.org`.

Core idea, essentially identical to our SurfaceSpec: **an agent emits declarative JSON describing UI
intent; the client renders it with its own native components from a trusted catalog.** No arbitrary
model-authored code executes. The client's catalog is the security boundary.

## Architecture

1. **Generation** — the agent produces an A2UI response (JSON)
2. **Transport** — over A2A Protocol or AG-UI
3. **Resolution** — the client renderer parses it
4. **Rendering** — abstract components map to concrete host implementations
5. **Events** — user actions (clicks, form submits) go back to the agent as events

Core primitives: **Surfaces** (container), **Components** (from a catalog), **Data Binding**,
**Actions/Events**, **Catalogs** (registries).

Wire shape: _"a flat list of components with ID references"_ — designed for **incremental updates** and
**progressive/streaming rendering** ("users see the interface building in real time"). Described as a
_"flat, streaming JSON structure designed for easy generation"_ by LLMs.

Extensibility: developers register **"Smart Wrappers"** mapping server-side types to client
implementations; v0.9 documents "Defining Your Own Catalog".

Renderers shipped: **Lit**, **Flutter** (via GenUI SDK). **Planned:** React, Jetpack Compose, SwiftUI.
Tooling: an **A2UI Composer** for visual JSON generation.

## Side-by-side with our SurfaceSpec

|                      | A2UI                                          | SurfaceSpec (ours)                                                 |
| -------------------- | --------------------------------------------- | ------------------------------------------------------------------ |
| Format               | declarative JSON                              | declarative JSON                                                   |
| Code execution       | none                                          | none                                                               |
| Security boundary    | client component catalog                      | client renderer registry                                           |
| Component model      | open catalog, extensible, flat list + ID refs | **closed 10-value archetype enum** (5 implemented)                 |
| Data binding         | binding system                                | dot-paths (`items_path`, `title_path`, `columns[].path`)           |
| **Actions / events** | **yes — first-class, bidirectional**          | **none — schema has "zero side-effectful members"**                |
| Streaming            | yes — incremental by design                   | no — whole spec at once, arrives async _after_ data, merged by URI |
| Ownership            | Apache-2.0, Google + CopilotKit, multi-vendor | ours to maintain                                                   |
| Maturity             | v0.9.1, pre-1.0                               | in-repo, shipped-but-dark (see `FINDINGS.md`)                      |
| React renderer       | **planned, not shipped**                      | we have 5 archetype renderers                                      |

## Why this matters for us specifically

1. **The action model is the unlock.** Our audit's headline product gap is that "create a task in Linear"
   cannot render a form — and the _reason_ is that SurfaceSpec is read-only by construction. A2UI already
   specifies the input/submit/event path, which is the hard, security-sensitive part. Building our own
   action model means re-deriving exactly what A2UI got reviewed for.

2. **Streaming.** Our "generic → shaped upgrade toast" (`ViewUpgradeToast`) exists because a whole-spec
   format cannot stream — the spec lands after the data and swaps the view under the user. A2UI's flat
   list + ID refs makes that a non-problem.

3. **Model familiarity.** A nano-class model doing schema-constrained decode is far likelier to have seen
   A2UI than our bespoke schema. Our generator is a nano-model structured-output task — this is worth real
   accuracy.

4. **We stop maintaining a schema.** The 12 curated specs, the JSON schema, the pydantic mirror, the
   cross-language parity test, the injection linter, the eval corpus — a large maintenance surface for a
   format that a funded multi-vendor group is maintaining anyway.

## ⚠️ RECOMMENDATION SUPERSEDED — read [HERMES-COMPARISON.md §4](HERMES-COMPARISON.md) instead

**The recommendation below ("adopt the format") was written before the Hermes comparison and is WRONG.**
An adversarial pass refuted both of its load-bearing arguments, and the refutations were hand-verified.
It is preserved unedited so the reasoning trail survives — do not act on it.

**Current recommendation: build on what we have. Do NOT adopt A2UI now.** Take its component-catalog
_shape_ (an open, registry-advertised set replacing our closed 10-value enum against 5 renderers) inside
our own schema — no migration, no dependency. Revisit only when A2UI's React renderer has shipped **and**
we have committed to an inbound protocol (ACP or A2A) that gives a third party a reason to render us.

Why the argument below fails, in short:

1. **The "action model is the unlock" premise is false.** We already ship `ask_a_question` + `QuestionCard`
   (richer than Hermes' `clarify`) and `EditOverlay` + `approve_with_edits` + `SurfaceEdits` (a real
   field-level form with server-side merge authority). What the read-only schema buys is that a
   model-authored spec can never reach the write lane except through the PDP and a LangGraph interrupt.
   A2UI's action model does not hand us that gate — it hands us a second path into it that we must re-gate.
   The genuine gap is model-authored _arbitrary_ forms: a deliberate posture, not a missing capability.
2. **The streaming argument mistakes a wiring defect for a format defect.** Our spec does not arrive late;
   it does not arrive at all (see [FINDINGS.md §3.1](FINDINGS.md)).
3. **Interop — the only argument that would justify adoption — fails on transport.** A2UI rides A2A or
   AG-UI. We speak neither. Format without transport buys zero third-party reach.
4. **"We stop maintaining a schema" is inverted** — A2UI's React renderer is planned, not shipped.

---

## ~~Recommendation (provisional — pending the Hermes comparison)~~ — SUPERSEDED, see above

**Adopt the format; do not rewrite the system.**

- **Keep:** the acquisition ladder, the Work Ledger, the PDP/`ToolAccessGate` approval machinery, the
  desktop supervision model. A2UI does not cover any of it and it is the actual differentiator.
- **Replace:** `SurfaceSpec`-the-wire-format with A2UI. Our 5 archetype renderers become catalog entries;
  the nano-model generator emits A2UI instead of SurfaceSpec.
- **Gain:** an action/event model (unblocks write forms), streaming, a spec we don't own.

**Honest cost:** A2UI's React renderer is _planned_, not shipped. We are a React app on Lit/Flutter-only
support today, so adoption means writing the React renderer ourselves. That is real work — but it is
precisely the piece we have already written five times, and it is a contribution back rather than
private maintenance.

**Do not** adopt A2UI as a way to fix the four breaks in `FINDINGS.md`. Those are wiring defects, not
format defects — swapping formats over a dark pipeline just makes a different format dark.

## Sources

- [Introducing A2UI: An open project for agent-driven interfaces — Google Developers Blog](https://developers.googleblog.com/introducing-a2ui-an-open-project-for-agent-driven-interfaces/)
- [A2UI v0.9: The New Standard for Portable, Framework-Agnostic Generative UI — Google Developers Blog](https://developers.googleblog.com/a2ui-v0-9-generative-ui/)
- [a2ui.org](https://a2ui.org/)
- [github.com/a2ui-project/a2ui](https://github.com/a2ui-project/a2ui)
- [CopilotKit/generative-ui — examples for AG-UI, A2UI/Open-JSON-UI, MCP Apps](https://github.com/CopilotKit/generative-ui)
- [A2UI Introduction — A2A Protocol](https://a2aprotocol.ai/blog/a2ui-introduction)
