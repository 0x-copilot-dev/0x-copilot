# Making the payload structured — design

**Date:** 2026-08-05 · Supersedes the spec-acquisition framing in
[PRD.md](PRD.md) §2 · Evidence: [../../audit/generative-ui/ORPHAN-AUDIT.md](../../audit/generative-ui/ORPHAN-AUDIT.md)

## The premise that was wrong

Every rung of the spec ladder — builtin, store, shape-match, rung-0 inference, model refinement
— assumes **the payload arrives structured and our job is choosing dot-paths into it**.

It does not. `content_and_artifact` returns `(content, artifact)`, and `artifact` is `None`
unless the server sends `structuredContent`. Effectively no server does. What arrives is:

```python
{"result": [{"type": "text", "text": "<a JSON document, as a string>", "id": "…"}]}
```

Dot-paths into that resolve to nothing, which is why a correctly-matched spec drew a correctly
shaped table over **zero rows** while the model — reading the same bytes — wrote a perfect
five-row table in the chat beside it.

## What the live evidence actually says

From a real Linear run (`table://linear/list_issues/1532a206699e`):

|                       |                                                                               |
| --------------------- | ----------------------------------------------------------------------------- |
| `spec.items_path`     | `"issues"`                                                                    |
| `data`                | `{"result": [{"type": "text", "text": "…", "id": …}]}`                        |
| `data.result[0].text` | **7,173 chars of valid JSON** → `{"issues": [5 items], "hasNextPage": false}` |

**The spec was right.** Parse the text block and `items_path: "issues"` binds five rows on the
first try. The entire defect is a missing `json.loads`.

That matters for the design: the model is **not** the primary mechanism. It is the fallback for
payloads that genuinely carry no structure.

## The normalisation ladder

Normalisation runs **before** spec acquisition and is a separate concern from it. Two tiers.

### N0 — deterministic (always runs, no model, no latency)

1. **Peel envelope keys** — `result` / `data` / `structured_content`.
   `EnvelopeUnwrapper` already does this, but refuses to peel to a non-`Mapping`
   ([infer.py:400](../../../services/ai-backend/src/agent_runtime/capabilities/surfaces/infer.py:400)),
   which blocks the list case that matters most. The guard exists so
   `{"data": [row, row]}` stays bindable by `items_path: "data"` — so the fix is not to drop it
   but to run decoding **before** the final peel decision.
2. **Decode MCP content blocks** — a list of `{"type": "text", "text": …}`. For each block, if
   `text` parses as JSON, take the parsed value; otherwise keep the string. One block → that
   value. Many blocks → a list of them.
3. **Re-peel** — the decoded value is frequently itself an envelope.
4. Bind the spec against the result.

N0 alone fixes Linear, and every server that JSON-serialises into a text block — which is the
overwhelming majority.

### N1 — model normalisation (only when N0 yields nothing bindable)

For payloads that are genuinely prose, CSV-in-text, or an unparseable blob.

**Input:** the decoded text plus the tool name.
**Output:** a closed contract — not free-form, not a spec over someone else's shape:

```json
{
  "archetype": "table",
  "title": "Open incidents",
  "rows": [{ "id": "PAR-9", "title": "…", "priority": "High" }],
  "columns": [
    { "label": "ID", "path": "id" },
    { "label": "Priority", "path": "priority", "format": "badge" }
  ]
}
```

**Why data-and-spec together, not a spec alone.** We generate `rows`, so every `column.path` is
a key of a row we just produced. The paths cannot miss. This is the principle already recorded
at [ledger_models.py:571](../../../services/ai-backend/src/agent_runtime/surfaces_v2/ledger_models.py:571)
— "spec and data are two halves of ONE resolution" — applied one stage earlier. Today we carry
them together but still _derive_ them apart, which is the gap that let a right spec meet a wrong
payload.

**Where it runs.** The async shaping seam that exists today and currently refines a _spec_.
Its job changes; its plumbing (BYOK credential, archetype handshake, follow-up event) does not.

### Degradation

`surface.created` fires immediately with N0's best effort, so a surface always exists. If N1
returns nothing usable the surface stays on its generic view. **There is no empty state** —
that property is what rung-0 was built for and it must survive this change.

## Why not have the model emit it inline

The original suggestion was that the model write the structured block as part of its turn, the
way it writes the final message. Same instinct, and N1 keeps it — but the trigger differs, for
two reasons:

- **Timing.** The surface is created at tool-result time; the model's prose lands much later. A
  tab that appears empty and fills a minute later is the bug we are fixing, not a new design.
- **Determinism.** A model that forgets to call a render tool produces no surface at all. We
  trigger N1 ourselves on a failed N0, so the surface never depends on the model choosing to
  cooperate.

`write_todos` is the counter-example worth naming: it _is_ model-called, and it works because a
missing todo list is invisible rather than a hole where a table should be.

## Generic vs Shaped, restated

The toggle already models this ladder and its labels were always meant to mean:

- **Generic** — N0's structure with a shape-matched or inferred spec.
- **Shaped** — a curated spec, or N1's model-produced rows.

The observed bug — "Shaped" selected over an empty table — was the shaped path yielding a spec
with no bindable data. Under this design the two halves are produced together, so that state is
unrepresentable.

## Test strategy — the fixture was the blind spot

`tools/desktop-journeys/surface-floor/fixture_mcp.py` returns something bindable, so the journey
passed 10/10 while every real connector was broken. It proved a path no MCP server takes.

1. Change the fixture to return **JSON-in-a-text-block**, exactly like Linear. The journey must
   go red before N0 lands, and green after.
2. Add a second fixture tool returning **prose only**, to exercise N1 and the degradation path.
3. Keep one tool returning `structuredContent`, so the well-behaved path stays covered.

## Out of scope, tracked

- The status strip sticks on "Shaping…" because it counts `receipt://` surfaces as unresolved —
  they never earn a `view.derived`. Separate one-line fix.
- `read.executed` still carries `payload_ref: "call:unattributed"`. Cosmetic now that state
  rides the event, but it is the last remnant of the call-id join.
