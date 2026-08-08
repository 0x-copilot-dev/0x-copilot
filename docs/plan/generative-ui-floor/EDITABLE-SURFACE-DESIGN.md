# One editable surface — design

**Date:** 2026-08-06 · Follows [NORMALISATION-DESIGN.md](NORMALISATION-DESIGN.md)

## The instruction

> merge them into 1 and make them editable. You can batch the edits, and when someone clicks
> save, the llm can figure out which mcp tool or general tool is needed. Also no raw text
> editing, the surface should be editable.

## Correcting a claim I made against it

I argued `SurfaceSpec` must stay read-only "by construction" because the model now authors specs,
so a spec able to carry behaviour would be a model-authored side effect.

That objection was **wrong as stated**, and the distinction matters:

|               |                                                                                                                                                                                  |
| ------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Dangerous** | the spec carries behaviour — `{"on_click": …}`, a URL, a template. The model wrote it, so the model authored a side effect.                                                      |
| **Safe**      | the spec stays declarative; **the HOST renderer decides a surface is editable**; the values are user-typed; the model only picks the tool at save time, behind an approval gate. |

Editability is a property of the renderer, not a member of the spec. `spec_models.py:255` stays
true — no handlers, no URLs, no templates — and the surface is still editable.

## Three lanes become one

Today a user meets three unrelated things: a read-only **surface**, an **artifact** edited as raw
markdown in a textarea, and a **staged write** nobody has seen. They render differently, edit
differently, and save differently.

**A surface is a list of blocks.** That is the whole merge.

- A connector read produces a **one-block** surface (a table).
- An artifact produces an **n-block** surface (heading, table, paragraph).
- Same renderer, same edit affordances, same Save button.

`SurfaceSpec` becomes the description of a _table block_ rather than of a whole surface, which is
what it always effectively was.

## Editing

Every block is edited **in place**. The markdown textarea is deleted — that is the literal
reading of "no raw text editing", and it is also the only way a table becomes clickable, because
today the artifact body is ONE STRING rendered through markdown → HTML. There is no table
component to click into; there is text that looks like a grid.

Edits accumulate as **block-scoped deltas** in client state. Nothing leaves the client until
Save. A cell edit is `(block, row, field, old → new)` — which is already
`RowFieldChange` (`surfaces_v2/rowset.py:36`).

## Save — the origin decides, and only one path needs a model

| Origin        | What Save does                                                             | Model? | Approval?                                                                  |
| ------------- | -------------------------------------------------------------------------- | ------ | -------------------------------------------------------------------------- |
| **artifact**  | splice the deltas into the source document → `artifact.revised`            | No     | No — local and reversible through the revision history that already exists |
| **connector** | model maps deltas → tool + per-row args → staged → diff → approve → commit | Yes    | Yes                                                                        |

Two consequences worth stating:

- **Artifact editing works with no provider key.** It is a local document revision; nothing needs
  a model. Only connector write-back does.
- **Splice, never regenerate.** The document carries prose outside the table ("All five issues
  are currently in Cool status"). Parsing the whole document and re-emitting it would reformat or
  drop that. Splicing the exact edited span cannot. Same principle as the write lane: capture the
  delta, never let anything re-author the whole.

## The safety property already exists — do not re-invent it

`StagedRow` (`surfaces_v2/rowset.py:44`) is documented as _"one proposed row change — the WYSIWYG
unit a user approves/holds"_, where **`target_args` are the EXACT connector-op args the shared
dispatcher sends for THIS row, verbatim** and `changes` are _display diffs only_.

That is the property that makes an LLM-selected write safe: **the object the user approves is the
object that is sent.** There is no re-composition step between the diff and the request, so a
model cannot paraphrase a value between what you saw and what left the machine.

The model's job at Save is therefore bounded:

- it picks the **tool** (which MCP op writes this),
- it maps **field names** onto that tool's schema,
- it **never retypes a value** — user-typed values pass through verbatim into `target_args`.

Same line as the render side's select-vs-generate: the model chooses shape, never content.

`ApplyResult {applied, partial, failed}` and `effect.row_decisions_recorded` already exist for the
case where three of five rows land.

## Sequencing

**Phase 1 — the merge and the safe half.** Block model; editable table and prose renderers;
artifact origin saving by splice to a new revision; delete the textarea. No model, no approval,
no connector risk. This is what delivers "no raw text editing" and "the surface is editable".

**Phase 2 — connector write-back.** Model picks the tool and emits `target_args` per row; stage;
diff; approve through the existing write gate; commit; record per-row outcomes. This is where the
dark staging lane wakes up.

Phase 1 cannot damage anything outside the app — its worst failure is a bad revision the user
restores. Phase 2 can write to a real system, which is why it lands second and behind a gate.

## Open questions, named rather than assumed

1. **Where do blocks live on the wire?** An artifact body is a plain string today; there is no
   block model anywhere in the tree (checked). Parsing to blocks on read and splicing back on
   write keeps the string authoritative — no migration, no new persistence. The alternative
   (blocks as the stored form) is cleaner long-term and much more expensive now.
2. **Prose editing without a markdown box.** Inline contenteditable satisfies "no raw text
   editing", but round-tripping rich text to markdown is its own class of bug. Narrowest useful
   start: plain-text-with-formatting-preserved, splice by span.
3. **A connector row the user ADDS or DELETES**, not just edits. `StagedRow` models a change to a
   row; create/delete need their own ops and are not obviously the same gesture.
