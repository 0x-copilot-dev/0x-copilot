# surface-floor — eight envelopes, one dataset

**User story.** "I connected Linear. The agent read five issues and described
them perfectly in the chat. The canvas beside it drew a table with the right
columns and no rows."

## Why the old version of this journey proved nothing

`floor_e2e.py` passed 10/10 while every real connector was broken.

The fixture's two tools both returned a Python `dict` and left FastMCP's
`structured_output` at its default. FastMCP then derives an output schema from
the return annotation and populates `structuredContent`;
`langchain-mcp-adapters` builds an `MCPToolArtifact` from that field, and the
runtime reads the structured half. So the journey exercised a payload that
arrived **already structured** — which is the one wire form effectively no real
MCP server produces.

What a real server sends is the model-facing half:

```json
{ "result": [{ "type": "text", "text": "{\"issues\": [...]}", "id": "…" }] }
```

The rows are right there, as a JSON _string_ inside a text block.

That is the blind spot this directory now closes: the fixture serves the same
three incidents in **eight** envelopes, and the journey drives one read per
envelope and prints what each one drew.

## The eight shapes

| #   | tool                        | wire form                           | modelled on          |
| --- | --------------------------- | ----------------------------------- | -------------------- |
| 1   | `list_incidents`            | one text block, JSON **object**     | Linear               |
| 2   | `get_incident`              | one text block, **nested** JSON doc | Figma / Notion       |
| 3   | `list_incidents_structured` | `structuredContent`                 | the well-behaved few |
| 4   | `list_incidents_array`      | one text block, JSON **array** root | Atlassian            |
| 5   | `summarize_incidents_prose` | one text block, **prose**           | Slack                |
| 6   | `list_incidents_markdown`   | one text block, **markdown table**  | chat-first servers   |
| 7   | `export_incidents_csv`      | one text block, **CSV**             | export tools         |
| 8   | `incident_briefing`         | **three** text blocks in one result | briefing tools       |

Only the envelope varies. The three incident rows are identical across all
eight, so any difference in what renders is caused by the envelope and nothing
else.

`structured_output=False` on every tool but #3 is what pins the wire form.
Returning a Python object instead would let FastMCP re-serialise it, and
`_convert_to_content` flattens a `list` into one content block **per element** —
silently turning shape 4 into shape 8.

## Measured, before the journey was written

Through the real `SurfaceProjector` over the real MCP client, with only the app
shell absent (`langchain_mcp_adapters._convert_call_tool_result` →
`McpPresentedTool._output_of` → `SurfaceProjector.resolve`):

| #   | archetype | `items_path` | rows | columns / fields drawn                                 |
| --- | --------- | ------------ | ---- | ------------------------------------------------------ |
| 1   | table     | `incidents`  | 3    | Status, Title, Assignee, Created At, Number, Urgency   |
| 2   | record    | —            | —    | Status, Assignee, Created At, Number, Urgency, Service |
| 3   | table     | `incidents`  | 3    | Status, Title, Assignee, Created At, Number, Urgency   |
| 4   | table     | **`result`** | 1    | **ID, Type, Text**                                     |
| 5   | table     | **`result`** | 1    | **ID, Type, Text**                                     |
| 6   | table     | **`result`** | 1    | **ID, Type, Text**                                     |
| 7   | table     | **`result`** | 1    | **ID, Type, Text**                                     |
| 8   | table     | **`result`** | 3    | **ID, Type, Text**                                     |

**Shapes 4–8 do not "render nothing".** They render a table over the MCP
_envelope_: one row per content block, the connector's entire payload stuffed
into a single `Text` cell. Shape 8 draws three rows — one per block — which from
across the room looks exactly like three incidents.

That is worse than an empty surface, because it looks like it worked. The
originating audit recorded these as "no spec", which is true of
`SurfaceSpecInferrer.infer` in isolation (it returns `None` for a non-`Mapping`)
but not of the pipeline: `McpPresentedTool._output_of` wraps every non-dict
payload as `{"result": payload}` first, so the inferrer never sees the bare
list — it sees a mapping whose only array of mappings is the content-block list,
and dutifully tabulates it.

## How the journey tells the two apart

A mounted, non-empty table is not evidence. The discriminator is **whose
vocabulary the drawn slots come from**:

- `TRANSPORT_SLOTS = {id, type, text}` — the fields
  `langchain-mcp-adapters` puts on a converted content block. Any of these
  among the drawn slots means the surface bound the envelope.
- `CONNECTOR_SLOTS = {number, title, status, urgency, assignee, …}` — fields
  that can only have come from the fixture's rows. Two or more, with no
  transport slot, is what "bound" means here.

Per shape the journey reports `rendered?` / `rows` / `bound?` / verdict, from
both the live DOM (`th`, `[data-testid^=table-row-]`, `field-*` label spans) and
the server (`GET /v1/agent/runs/{id}/surfaces`, matched on the snapshot's own
`op` so a run that called a neighbouring tool cannot be scored against the wrong
envelope).

## It is expected to FAIL

3/8 today. A green run means the instrument broke, not that the pipeline was
fixed. Shapes 4–8 go green when normalisation (N0 decode of array/multi-block
roots, N1 for prose/CSV/markdown) lands —
`docs/plan/generative-ui-floor/NORMALISATION-DESIGN.md`.

Shape 1 additionally carries the four-hop identity trace this journey has always
run (ledger on disk → surfaces endpoint → canvas tab keys → one `surface_id`).
It used to run on shape 3's `structuredContent` payload, which is why it never
caught anything.

## Running it

The matrix is no longer its own script. `floor_e2e.py` was deleted when 64
journeys became 9; the matrix now lives as **phase AS-9 of
`artifacts_and_surfaces.py`**, which is the journey that already boots what it
needs (source target, fresh profile, BYOK key, the artifact lane).

```bash
node tools/desktop-runtime/stage.mjs --platform darwin --arch arm64   # after any services/* change
npm run build --workspace @0x-copilot/desktop
python3 tools/desktop-journeys/surface-floor/fixture_mcp.py &         # keep it in its own shell
/opt/homebrew/bin/python3.13 tools/desktop-journeys/artifacts_and_surfaces.py
```

To drive only part of the matrix — eight real runs is the most expensive phase
in that file — set the shape list, which is the successor to `--shapes`:

```bash
SURFACE_FLOOR_SHAPES=1,4 \
  /opt/homebrew/bin/python3.13 tools/desktop-journeys/artifacts_and_surfaces.py
```

Needs a provider key in `services/ai-backend/.env` — the runs are real. AS-9
SKIPS (exit `3`, never a pass) when nothing is listening on the fixture port.

**Stop the fixture when you are done.** A server left listening on 8931 answers
the next registration happily and serves _its_ tool list; the journey then
measures a fixture nobody edited. This cost real time while the shapes were
being written. Two guards now exist: `SURFACE_FIXTURE_PORT` moves both halves
off a busy port, and AS-9 reads the fixture's `GET /shapes` manifest first and
skips rather than measure an unrecognised `revision`.
