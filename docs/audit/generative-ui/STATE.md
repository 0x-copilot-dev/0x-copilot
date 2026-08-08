# Generative UI — session state

**Date:** 2026-08-08 · **Branch:** `claude/dark-code-cleanup-f2015984` ·
**PR:** [#581](https://github.com/0x-copilot-dev/0x-copilot/pull/581) → `dev`
Companions: [FINDINGS.md](FINDINGS.md) · [ORPHAN-AUDIT.md](ORPHAN-AUDIT.md) ·
[WRITE-LANE-ATTACK.md](WRITE-LANE-ATTACK.md) ·
[../../plan/generative-ui-floor/NORMALISATION-DESIGN.md](../../plan/generative-ui-floor/NORMALISATION-DESIGN.md) ·
[../../plan/generative-ui-floor/EDITABLE-SURFACE-DESIGN.md](../../plan/generative-ui-floor/EDITABLE-SURFACE-DESIGN.md)

## One paragraph

An MCP tool result renders as a shaped surface, the surface is editable in place, and edits
stage into a bounded connector write behind the approval gate. The read path is live and
verified on the packaged app. The write path is implemented, attacked twice, and hardened —
and one class of op (send/create, where the required set IS the content) now refuses by
design rather than dispatching more than the reviewer saw.

## Test state

Python **9,377** · chat-surface **4,301** · api-types **124** · 4/4 typechecks · CI green
across all 12 workflows. The 2 red in chat-surface are worktree artefacts: the
`canvasLifecycle` / `turnParts` differentials `throw` when `services/ai-backend/.venv` is
absent, and a worktree never has one.

## Verified live on the packaged app

- a Linear-shaped read (JSON in a text block) → table with the connector's own columns
- a JSON-array-at-root read → connector columns, not the MCP envelope
- a document artifact → editable blocks; `#artifact-editor-text` returns zero nodes, i.e.
  the raw markdown textarea is gone from the shipped bundle
- one identity: a tab key is byte-identical to a `surface_id` the ledger wrote

## The invariant the write lane now holds

> **The object the user approves must be the object that is sent.**

It did not hold. `StagedRow.changes` is the only thing a human sees before dispatch —
`StageRowView` is `extra="forbid"` and omits `target_args`, and the client projected
`changes` alone — while `target_args` is what dispatches. The worked exploit: a user edits
**only the Cc** on a drafted reply and the recipient, the subject and an entire
model-authored **body** dispatch under a one-line `cc: "" → legal@acme.example` diff.

The fix is a total account, not another guard. Every `StagedRow` carries
`sends: tuple[StagedArg, ...]` — one entry per key in `target_args`, keyed by the
connector's own arg name, tagged `edited` / `carried` / `proposed`. `RowsetValidator`
enforces it at the choke point every staging lane already passes through: ordered
bijection, type-tagged value identity (fingerprint, not `==`, so `True` is not `1`), every
`changes` field is an arg actually sent, uniqueness, non-vacuity. The model cannot author
the account — `stage_rowset_write` takes a `ProposedRow` with no `sends` field and
`RuntimeContract` is `extra="forbid"`, so a model that emits one gets `MALFORMED`.

## What this costs: the email compose lane is dark, deliberately

A `send_reply`-class op declares `required = [to, subject, body]` — the required set **is**
the content. There is no signal in `input_schema` distinguishing "addresses a record" from
"is the message", so the op refuses as `UNBOUNDED_OP` before anything stages. Pinned by
`test_the_worked_exploit_is_refused_before_anything_stages`.

Email renders and edits; it cannot send. The durable fix is a connector- or
catalogue-authored list naming the addressing args, captured beside `input_schema` in
`write_ops_capture.py`. **No heuristic was substituted** — every heuristic in this area is
what four independent attackers walked through.

## The defects, and how each was actually found

| Defect                                                                                               | Found by                                    |
| ---------------------------------------------------------------------------------------------------- | ------------------------------------------- |
| MCP text block never `json.loads`-ed — right spec, zero rows                                         | reading a real run's `events.jsonl`         |
| `_unwrapped` discarded the decoded value unless it was a `Mapping`, so rung 0 bound the **envelope** | the 8-shape fixture matrix                  |
| shaping subsystem OFF on every run: `ShapingCredentials` had no producer anywhere in `src/`          | the packaged app's own log                  |
| ledger recorded `basis='schema'` over a spec with `archetype: None`                                  | reading the surface event on disk           |
| `gate.resolved.v2` — five readers, no producer                                                       | producer-vs-reader scan                     |
| staging stamp hashed only `*.py`, so a changed `work_ledger.json` shipped stale                      | the packaged app failing to boot            |
| deleting a table column silently destroyed the table on a pipe-less row                              | remark-gfm as a gold oracle                 |
| a one-line diff sent a five-field write                                                              | my own attack against the real modules      |
| a one-line **Cc** diff sent a model-authored **email body**                                          | four adversarial agents on the real modules |
| the disclosure that fixes the above is **clipped off screen** with no ellipsis                       | Playwright + the real CSS, measuring pixels |
| a test green on every laptop, red on the first CI runner                                             | CI                                          |
| 3 of 25 rules had tests that passed with the rule disabled                                           | a mutation sweep                            |

**Not one was caught by a unit test.** Every one needed the real app, a real payload, real
layout, or an oracle outside our own model.

## The rules this session paid for

1. **Splice, never regenerate.** Tested as a property — `parseBlocks(s).map(slice).join("")
=== s` over 21 corpus documents, 500 generated, 20,000 in an independent pass.
2. **Ask an oracle, not yourself.** `parseBlocks` was more permissive than remark-gfm, so
   our own kind-assertions could pass over a document the reader sees as prose. Five
   divergence classes closed; two measured, documented and deliberately left, each pinned
   so closing it later fails loudly.
3. **The model chooses shape, never content.** Render: `select` vs `generate`, recorded as
   distinct `ViewBasis` members. Write: the model picks the tool and maps field _names_,
   and `arg == key` is unconditional with no alias.
4. **Editability is a property of the renderer, not of the spec.** `SurfaceSpec` stays
   side-effect-free; the model authors specs.
5. **A test that passes on broken code proves nothing.** Mutation-verify every rule.
   21 of 21 in the accounting pass; a later sweep found 3 of 25 elsewhere that were vacuous.
6. **A green suite over a DOM assertion is not a green screen.** jsdom performs no layout.
   `toHaveTextContent` asserts presence — the exact thing CSS then hides. Layout contracts
   need `getComputedStyle` against the real stylesheet.
7. **Injected deps hide dead features.** Twice this session: a float fix whose tests only
   passed because the fake resolver bypassed the two production gates, and a write-ops
   capture whose tests inject at the seam the live path would break before reaching.

## Open, ranked

1. **Live verification.** The port's capture producer sits in the real MCP registration
   path but has never seen a real connector's descriptors make the round trip; AS-9 has
   never run end to end. Both need a staged runtime. This is the largest remaining risk and
   it is the "injected deps hide a dead feature" shape.
2. **The email send lane needs a declared-identity signal** before it can dispatch.
3. **`canonical_json._render_float`** renders a float whose `is_integer()` is true as an
   int, at STORAGE — so `5.0` is `5` in the body the approval digest pins, and the user
   reviews `5` too. Fixing it re-renders every digest in the system: architectural, not a
   call-site edit. My earlier "fix" was reverted as a no-op on a dead branch.
4. **134 test files call `RuntimeSettings.load()`** with no conftest neutralising `.env`,
   so the green-on-laptop / red-on-CI hazard is repo-wide. One test depended on it.
5. **~14,600 lines dark** behind default-off flags — a product decision, untouched.

## Corrections I made to my own claims — kept because the pattern repeats

- "26 of 96 routes unreachable" → **13 of 114**. My extractor resolved one prefix per file.
- "`/conversations/{id}/fork` is uncalled" → it **is**; I read the minified `dist` bundle.
- "the doubled prefix is a bug" → my own extraction artefact.
- "`SurfaceSpec` must stay read-only, so the surface cannot be editable" → conflated _spec
  carries behaviour_ with _surface is editable_. Only the first is dangerous.
- "structural ops are not reachable from the splice primitive" → all seven are span ops.
- "`gpt-5.4-mini` does not exist" → the id was fine; the credential was missing.
- "shape 4 must have gone through the model" → the ledger said `basis='schema'`.
- "a user who typed 5.0 gets 5 sent **at dispatch**" → refuted by my own verifier; the
  collapse is at storage and there is no review/dispatch divergence. Reverted.
- "`npm audit fix` is the right tool for the CVE gate" → it broke SBOM by demoting three
  packages this repo deliberately overrides. Replaced with a 6-line surgical bump.
