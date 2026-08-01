# agent-todos — the agent's working checklist in the desktop transcript

The agent's `write_todos` calls used to surface as a raw tool card (args + result
JSON, with the todo list flattened into one run-on string), while Focus rendered
a "Plan" the client invented from tool-call frames. Both were replaced by one
checklist pinned above the composer, fed by the server's `todo_list_updated`
snapshots.

Unit tests cover the projection, and a hermetic in-process run covers the
emission. Neither runs the packaged app, so neither can catch the failure that
matters here: **the panel not appearing.** Every layer between the worker and the
pixel — SSE transport, the `RuntimeApiEventType` allowlist the client parser
enforces (`isRuntimeEventEnvelope` drops an unknown `event_type` _silently_), the
projector, the mount point — can drop the event without a single test going red.

## T1 — the checklist renders from a real keyed run

**User story.** I ask for work with several distinct steps. I want to see the
agent's plan as a live checklist, not a wall of tool JSON.

**Steps.** Sign in → add a BYOK key through first-run → send a prompt that asks
for three tracked steps carried out one at a time.

**Expected.**

- `tc-todo-list` appears, pinned as the immediate previous sibling of
  `tc-chat-composer-slot`.
- At least three `tc-todo-row`s, each carrying a `data-status` from the
  `pending` / `in_progress` / `completed` union.
- The `tc-todo-list-count` chip reads `N/M` against the rendered rows.

## T2 — rows advance from spinner to tick

**Expected.** A row observed as `in_progress` (rendering `tc-todo-spinner`)
later reads `completed` in the SAME panel, and the completed count rises. This is
the transition the redesign is about, so the journey fails rather than passes if
the list only ever renders one static state.

## T3 — the raw `write_todos` card never appears

**Expected.** No `tc-chat-tool-*` card mentioning `write_todos` at any point.
The backend marks those frames `visibility: "internal"`; the client projector
must honour it. This is a regression guard for the exact card in the bug report.

## T4 — the invented "Plan" is gone

**Expected.** `focus-plan` is absent in Studio AND after switching to Focus (⌘M
/ the mode control), where it used to render inside the Activity panel.

## T5 — the backend actually emitted the snapshots

**Expected.** The run's replay endpoint carries at least one
`todo_list_updated` event whose payload has a `list_id`, a `generation`, and a
`todos` array of `{content, status}` objects — read back through the app's own
authenticated transport. Asserting only the DOM would leave "the panel rendered
something" and "the server sent a checklist" indistinguishable; asserting only
the event would repeat what the hermetic test already proves.

## Blocked / not covered

- **List rollover (generation 2).** Needs a run whose first list completes and
  is then followed by a fresh one. A real model decides that, so it cannot be
  forced without scripting the model — which would stop this being a live
  journey. Covered deterministically in `test_todo_list_events.py` instead.
- **Subagent checklists.** Deep Agents gives each subagent its own
  `TodoListMiddleware`; the cockpit deliberately shows only the main agent's
  list. No desktop surface renders a subagent checklist yet.

## Running

```bash
python3 tools/desktop-journeys/agent-todos/todo_panel.py
# AGENT_TODOS_PROVIDER=anthropic to switch the BYOK key
```
