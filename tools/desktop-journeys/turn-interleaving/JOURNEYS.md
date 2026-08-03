# turn-interleaving — prose the model spoke BEFORE it acted must survive

A real agent turn is `text → tools → text`, sometimes several times over. The
transcript used to fold that into one bucket per KIND — one text blob, one
reasoning blob — so two things were lost at once:

1. text emitted **before** a tool call was overwritten by the terminal
   `final_response` (which carries only the last turn's text), and
2. the surviving blob carried a single anchor — its first token — so every
   mid-turn tool / approval / subagent card sorted **after** the whole message.

The second symptom was already recorded, as an open question, in
[`transcript-density/long_run_grouping.py`](../transcript-density/long_run_grouping.py):
whether the streaming assistant message "anchors between activity items and
splits a turn's work into more than one group". This set answers it.

## Why a live journey and not a unit test

There are unit tests and a TS/Python differential corpus for the fold. They
cannot prove the thing that actually broke, because the loss happened across
three layers that only meet in the running app: the worker's fold at seal time,
the persisted `MessageRecord.content`, and the client's re-seed from
`/messages` when the run goes terminal. In particular the **post-settle** state
is the one users complained about — the transcript looked right while streaming
and collapsed to a single blob the moment the run finished, because history
replaced the live overlay.

Asserting **after settle** is therefore deliberate: at that point the DOM is
rendered from persisted `content` blocks, not from the live projection, so one
assertion covers the fold, the persistence, and the re-seed.

## J1 — `interleaved_turn.py`

| Step | Action                                                                     |
| ---- | -------------------------------------------------------------------------- |
| 1    | Sign in, add a BYOK key, send a prompt that forces speak → act → speak     |
| 2    | Wait for the run to settle (history has replaced the live overlay)         |
| 3    | Assert both prose halves are present in the transcript                     |
| 4    | Assert DOM order: STEP-ONE prose **above** the activity, STEP-TWO below it |
| 5    | Assert the persisted message carries ≥2 ordered `text` blocks with `seq`   |

### Expected outcome

- `STEP-ONE:` and `STEP-TWO:` both render. Before the fix, `STEP-ONE:` was
  destroyed by `final_response` and never reached the DOM.
- The activity (a `tool-run-group`, or a loose tool/fleet card) sits **between**
  them. Before the fix, both prose halves were one `<li>` and every card sorted
  after it.
- `GET /v1/agent/conversations/{id}/messages` returns an assistant message whose
  `content` is an ordered array of ≥2 `text` blocks, each carrying the `seq` it
  opened at. An empty `content` means the worker never folded the turn — the
  transcript would then survive only until the next reload.

### testIds asserted

`tc-chat`, `tc-chat-message-*` (incl. the `-part-N` items with `data-part-type` /
`data-part-seq`), `tool-run-group`, `tc-chat-tool-*`, `tc-chat-fleet-*`.

### What blocks full coverage

Nothing structural. The one soft spot is **model compliance**: the prompt asks
the model to speak before acting, and a model that calls the tool first produces
a turn with no pre-tool prose. That is not a product failure, so the journey
reports `blocked` (exit 2) rather than failing — it distinguishes "the shape
under test never occurred" from "the shape occurred and was mangled".
