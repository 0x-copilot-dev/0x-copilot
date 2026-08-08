# Generative UI — session state (compacted)

**Date:** 2026-08-07 · **Branch:** `claude/dark-code-cleanup-f2015984` ·
**PR:** [#581](https://github.com/0x-copilot-dev/0x-copilot/pull/581) → `dev` (open, CI running)
Companions: [FINDINGS.md](FINDINGS.md) · [ORPHAN-AUDIT.md](ORPHAN-AUDIT.md) ·
[../../plan/generative-ui-floor/NORMALISATION-DESIGN.md](../../plan/generative-ui-floor/NORMALISATION-DESIGN.md) ·
[../../plan/generative-ui-floor/EDITABLE-SURFACE-DESIGN.md](../../plan/generative-ui-floor/EDITABLE-SURFACE-DESIGN.md)

## One paragraph

An MCP tool result now renders as a shaped surface, the surface is editable in place, and edits
stage into a bounded connector write behind the existing approval gate. Earlier PR #562 is
merged to `dev`. Everything since is on #581.

## Verified live on the packaged app

- a Linear-shaped read (JSON in a text block) → table with the connector's own columns
- a JSON-array-at-root read → connector columns, not the MCP envelope
- a document artifact → editable blocks (heading, table, paragraph, list); `#artifact-editor-text`
  returns zero nodes, i.e. the raw markdown textarea is gone from the shipped bundle
- one identity: a tab key is byte-identical to a `surface_id` the ledger wrote

## Test state

Python **9,196** · chat-surface **4,251** · surface-renderers **557** · 4/4 typechecks ·
frontend + desktop build. The 2 red in chat-surface are worktree artefacts: the
`canvasLifecycle` / `turnParts` differentials `throw` when `services/ai-backend/.venv` is absent,
and a worktree never has one.

## The defects, and how each was actually found

| Defect                                                                                                                                               | Found by                               |
| ---------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------- |
| MCP text block never `json.loads`-ed — right spec, zero rows                                                                                         | reading a real run's `events.jsonl`    |
| `_unwrapped` discarded the decoded value unless it was a `Mapping`, so rung 0 bound the **envelope** — `ID / Type / Text`, whole payload in one cell | the 8-shape fixture matrix             |
| shaping subsystem OFF on every run: `ShapingCredentials` had no producer anywhere in `src/`                                                          | the packaged app's own log             |
| ledger recorded `basis='schema'` over a spec with `archetype: None`                                                                                  | reading the surface event on disk      |
| `gate.resolved.v2` — five readers, no producer                                                                                                       | producer-vs-reader scan                |
| staging stamp hashed only `*.py`, so a changed `work_ledger.json` shipped stale → `KeyError: 'writers'`                                              | the packaged app failing to boot       |
| deleting a table column silently destroyed the table on a pipe-less row                                                                              | remark-gfm as a gold oracle            |
| a one-line diff sent a five-field write                                                                                                              | my own attack against the real modules |

**None of these was caught by a unit test.** Every one needed either the real app, a real payload,
or an oracle outside our own model.

## The rules this session paid for

1. **Splice, never regenerate.** Tested as a property — `parseBlocks(s).map(slice).join("") === s`
   over 21 corpus documents, 500 generated, and 20,000 in an independent pass.
2. **Ask an oracle, not yourself.** `parseBlocks` is _more permissive than the renderer_:
   `a | b` over `- | ---` parses as a table here and as `["paragraph","list"]` in remark-gfm. Our
   own kind-assertions can pass over a document the user sees as prose. **Not fixed — reported.**
3. **The model chooses shape, never content.** Render side: `select` (paths into the payload) vs
   `generate` (model values), recorded as distinct `ViewBasis` members so a receipt can tell them
   apart. Write side: the model picks the tool and maps field _names_.
4. **Editability is a property of the renderer, not of the spec.** `SurfaceSpec` stays
   side-effect-free; the model authors specs, so a spec that could carry a handler would be a
   model-authored side effect.
5. **A test that passes on broken code proves nothing.** Mutation-verify: disable the fix, watch
   the test go red, restore.

## Inert by design — read before assuming the write lane works

`connector_write_ops` has **no producer**, so every connector Save 503s at the catalogue step.
**No connector write can dispatch from this branch.** The lane is complete and names the one
adapter it waits on.

Implementing that port is an **architectural decision, not an adapter**: it needs `input_schema`
per write op, and the curated `ActionCatalog` carries only `READ|WRITE`, descriptors are persisted
nowhere, and both `runtime_mcp_registry` and `mcp_discovery_cache` are read with
`getattr(..., None)` and set by nothing on that path. `input_schema` exists only on a live
`LoadedMcpServer`. Cheaper alternative: the surface came from a **read**, whose session already
had the descriptors — capture the write ops then, and the port becomes a lookup.

## Open, ranked

1. **The email path's adversarial pass never ran** (the agent stalled). `email_surface` mints
   `email://` and IS wired into `projector.py:74`, so the render half is live and unit-covered
   (23 py + 39 ts). Writes are inert only because the port is missing — the day someone
   implements it, an unattacked send path goes live.
2. **Two attack probes I never ran**: can a mapping declare _every_ field as a scoping key and
   turn the bound back into a rename; can a `Cc` reach the payload without appearing in the diff.
3. **`builtin_effect_executor.py:322`** does `json.loads(canonical_json(target_args))`, which
   renders a float that `is_integer()` as an int — so a user who typed `5.0` has `5` sent on that
   dispatch path. The D3 path is verbatim and unaffected.
4. **The 8-shape matrix journey needs re-homing.** `dev` consolidated 64 journey scripts into 9;
   my `floor_e2e.py` was deleted in that refactor and the matrix has not been ported into
   `artifacts_and_surfaces.py` (AS-9). The 8-shape **fixture** survived and is in the tree.
5. **~14,600 lines dark** behind default-off flags — a product decision, deliberately untouched.
   The sanctioned deletions from `PENDING-WIRINGS.md` were already done by someone else.

## Corrections I made to my own claims — kept because the pattern repeats

- "26 of 96 routes unreachable" → **13 of 114**. My extractor resolved one prefix per _file_;
  `routes.py` alone builds five routers. Ten of seventeen rows named paths the app never serves.
- "`/conversations/{id}/fork` is uncalled" → it **is** called; my spot-check read the minified
  `dist` bundle, not the source.
- "the doubled prefix is a bug" → my own extraction artefact.
- "`SurfaceSpec` must stay read-only, so the surface cannot be editable" → conflated _spec carries
  behaviour_ with _surface is editable_. Only the first is dangerous.
- "structural ops are not reachable from the splice primitive" → all seven are span operations.
- "`gpt-5.4-mini` does not exist" → the model id was fine; the credential was missing, and the
  fail-soft logged the id without the reason.
- "shape 4 must have gone through the model" → the ledger said `basis='schema'`. It had not.
