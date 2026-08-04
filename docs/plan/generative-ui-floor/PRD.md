# PRD — Kill "No spec matched": an always-renderable surface floor

**Status:** proposed · **Date:** 2026-08-04 · **Base:** `dev` @ `6b32bdce`
**Evidence:** [docs/audit/generative-ui/FINDINGS.md](../../audit/generative-ui/FINDINGS.md) ·
[HERMES-COMPARISON.md](../../audit/generative-ui/HERMES-COMPARISON.md)

## 1. Problem

A user asks about their Linear issues. `linear.list_issues.json` — a hand-authored spec that matches
perfectly — is resolved backend-side. The user then sees:

> **No spec matched `list_issues`**

The string is not merely unhelpful; **it is false**. We found the spec and dropped it. Three independent
defects sit between a matched spec and the screen, and a fourth often prevents the surface existing at all.

The deeper design fault is that the ladder treats _having a spec_ as the happy path and everything else as
failure — which is why the failure state needs copy at all. A user should never see the word "spec".

## 2. Goals

- **G1** No user-facing surface ever says a spec was not matched. The string is deleted, not reworded.
- **G2** Every mapping-shaped connector read renders a legible table or record **with zero model calls**.
- **G3** The 12 curated specs actually reach the renderer.
- **G4** A tool we have never seen, whose payload resembles one we have, reuses that rendering.
- **G5** The model becomes an _upgrade_, never a gate. Its absence costs polish, never the surface.

### Non-goals

- Adopting A2UI, AG-UI, or MCP Apps. Decided against — see
  [HERMES-COMPARISON.md §4](../../audit/generative-ui/HERMES-COMPARISON.md).
- Model-authored write forms. SurfaceSpec's read-only schema is a deliberate security posture.
- Reviving tier-2 (model-generated renderer code). Out of scope; still dark.
- New archetype renderers. Covered by the handshake in §3.7, not by new components.

## 3. The design

Invert the ladder: **the data is always renderable; a spec only makes it better.**

| Rung  | Source                             | Latency  | Can fail?                    |
| ----- | ---------------------------------- | -------- | ---------------------------- |
| 3     | learned cache (by shape)           | none     | no                           |
| 2     | LLM refinement                     | async    | yes → falls back to rung 0/1 |
| 1     | shape match (curated as templates) | none     | no                           |
| **0** | **deterministic inference**        | **none** | **no**                       |

Rungs 0 and 1 cannot fail. That is the whole point: the floor is unconditional.

### 3.1 Step 1 — stop dropping successful calls

`present_tool.py::_output_of` takes the MCP `content_and_artifact` tuple's **artifact** half and returns
`None` when it is `None`. In the pinned `langchain-mcp-adapters` the artifact is `None` unless the server
sent `structuredContent` — so for every text-only MCP server we emit **no surface at all**.

**Change:** when the artifact half is `None`, project the **content** half instead. Never return `None` for
a call that succeeded. (Hermes' `mcp_tool.py:5029-5041` has no branch that drops a successful call.)

- **File:** `services/ai-backend/src/agent_runtime/capabilities/mcp/middleware/present_tool.py`
- **AC1** A tool returning only text content produces a `surface.created` event.
- **AC2** A test drives a real MCP tool result with `structuredContent = None` end to end.

### 3.2 Step 2 — unwrap `structured_content`, and deliver the spec

Two defects, one file each.

**(a)** When `structuredContent` _is_ present the artifact is `MCPToolArtifact`, so `output` is
`{"structured_content": {...}}`. Nothing in the repo unwraps it, so `items_path: "issues"` resolves against
the wrapper and misses. Unwrap at the projector boundary.

**(b)** `SurfaceContentProjection.fold` populates `spec_by_surface` **only** from `surface_spec_generated`.
A builtin (rung-1) or store (rung-2) spec is computed backend-side, stamped `basis: registry` on the
ledger, and then never put on the wire.

**Change:** carry the resolved spec on `surface.created` (or a sibling `spec_ref` the fold resolves), and
have the fold source it from the builtin/store rung as well as from generation.

- **Files:** `surfaces_v2/content.py`, `surfaces_v2/emitter.py`, `capabilities/surfaces/projector.py`
- **AC3** With Linear installed, `list_issues` renders the curated table — columns ID / Title / State /
  Assignee / Updated.
- **AC4** The ledger's `tier`/`basis` agrees with what rendered. No `basis: registry` over a raw payload.

### 3.3 Step 3 — rung 0: deterministic inference (the floor)

A pure function from arbitrary JSON to a real `SurfaceSpec`. No model, no I/O, no failure mode.

1. **Unwrap** envelope keys `data|result|items|output|response|payload`, recursively, depth-capped.
   (Also subsumes 3.2(a).)
2. **Find the subject.** The dominant array-of-mappings becomes `items_path` → `table`. Otherwise the
   top-level mapping becomes a `record`.
3. **Rank columns** by `density × name_priority × type_signal`, cap at 6:
   - _density_ — fraction of rows where the key is present and non-null
   - _name_priority_ — `id|key|identifier` · `title|name|summary` · `state|status` ·
     `assignee|owner|user` · `updated|created`
   - _type_signal_ — ISO-8601 → `datetime`; low-cardinality string → `badge`; mapping with
     `displayName|name|avatar` → `user`; number → `number`
4. **Title** — first present of `title|name|summary|identifier|key`, else the tool's display name.

- **New file:** `capabilities/surfaces/infer.py` (pure, no I/O — same posture as `projector.py`)
- **AC5** Given a Linear-shaped payload **with no builtin spec available**, inference produces a table with
  ≥3 correctly typed columns.
- **AC6** Property test: never raises, never returns `None`, for scalars, nulls, empty arrays, deep
  nesting, heterogeneous arrays, and 1 MB payloads.
- **AC7** Inference is measurably under 5 ms at p99 for a 200-row payload.

### 3.4 Step 4 — rung 1: shape matching

`OutputShapeHasher` already produces a value-free structural fingerprint, but it is only used _inside_ a
`(server, tool, hash)` cache key. Promote the shape hash to a **first-class lookup key**, and treat the 12
curated specs as **shape templates** rather than exact-match entries.

Lookup order: exact `(server, tool)` → exact shape hash → nearest-neighbour over key skeletons above a
similarity threshold → rung 0.

This directly retires the naming brittleness the audit found: `save_issue` reuses `create_issue`,
`list_my_issues` reuses `list_issues`, and a manually-added server (named after its URL host) stops missing
everything.

- **Files:** `capabilities/surfaces/shape_hash.py` (add skeleton similarity), `builtin.py`, `store.py`
- **AC8** A tool named `list_my_issues` with a `list_issues`-shaped payload renders the curated table.
- **AC9** A shape-matched surface is labelled as such in provenance — never presented as an exact match.
- **AC10** Similarity is thresholded; a false match is a _worse_ outcome than rung 0 and must be tunable.

### 3.5 Step 5 — rung 2: the model refines, never authors

Today the generator authors from a blank page and is the _only_ supplier — so one missing credential blanks
the feature. Invert it: hand the model the **inferred spec plus a payload sample** and ask it to improve
labels, pick `title_path`, drop noise columns, and choose formats.

Strictly easier for a nano model, and it degrades to rung 0/1 on any failure.

- **Files:** `capabilities/surfaces/generator.py`, `skills/spec-authoring/SKILL.md`
- **AC11** With no provider credential, the surface still renders at rung 0/1 and no error reaches the user.
- **AC12** Refinement arrives as an in-place upgrade; the user never sees a flash of un-shaped content.
- **AC13** Fix the credential defect: `build_chat_model_from_id` must receive BYOK `extra_kwargs`.

### 3.6 Step 6 — rung 3: learn

Persist refined specs keyed by **shape hash** (not `(server, tool)`), so the second encounter with that
shape — by any tool, any connector, any user on that install — is instant and free.

- **Files:** `capabilities/surfaces/store.py`, `backend_store.py`
- **AC14** A second run over the same payload shape issues zero model calls.
- **AC15** A `record_failure` for one shape never suppresses a _different_ shape.

### 3.7 Step 7 — tell the generator what the renderer can draw

The generator is licensed to emit 10 archetypes; 5 have renderers. `form`, `dashboard`, `timeline`,
`event`, `file` silently collapse to a generic view. The registry knows the implemented set at runtime and
reports it to nobody.

**Change:** a capability handshake — the client advertises its registered archetypes; the generator is
prompted with that set. No schema change, no migration.

- **AC16** Removing an adapter removes it from the generator's licensed set without a code change.

### 3.8 Step 8 — delete the copy

`NoSpecView` and the `(unknown saas)` / `(no resource id)` / `(no fields)` placeholders are removed. With
rung 0 unconditional there is nothing to apologise for. Our own source already concedes the current
fallback _"reads to a user like an error."_

- **AC17** `grep -ri "no spec matched"` over `packages/` returns zero hits.

## 4. Sequencing

Steps 1–2 are prerequisites: rung 0 is worthless if nothing reaches the client.

| #   | Step                          | Size       | Unblocks                  |
| --- | ----------------------------- | ---------- | ------------------------- |
| 1   | `_output_of` content fallback | one branch | everything                |
| 2   | unwrap + deliver the spec     | small      | the existing 12 specs     |
| 3   | rung 0 inference              | ~300 LOC   | G1, G2 — kills the string |
| 4   | shape matching                | ~200 LOC   | G4                        |
| 5   | refinement + BYOK credential  | medium     | G5                        |
| 6   | learned cache                 | small      | cost                      |
| 7   | archetype handshake           | ~1 day     | correctness               |
| 8   | delete the copy               | trivial    | G1                        |

**Steps 1–3 alone deliver the user-visible outcome.** 4–8 are compounding.

## 5. Risks

- **A wrong shape match is worse than no match.** Mitigation: threshold, label the provenance, and prefer
  rung 0 when confidence is low (AC9, AC10).
- **Inference could mis-pick the subject array** on payloads with several arrays. Mitigation: rank by row
  count × mapping-ness; expose the choice in provenance so it is debuggable.
- **The ledger's `tier`/`basis` vocabulary needs a value for "inferred".** This touches the 34-event pinned
  cross-language contract — a contract change, not a runtime fix. Budget for it.

## 6. How we will know it worked

Not "tests pass" — that is exactly what was green over all four breaks.

- A **live packaged-desktop journey** (`tools/desktop-journeys/`) that installs a connector with **no
  curated spec**, runs a read, and asserts a table renders with ≥3 columns.
- The same journey with **no provider credential configured** — must still render.
- A CI gate asserting `grep -ri "no spec matched"` finds nothing.
