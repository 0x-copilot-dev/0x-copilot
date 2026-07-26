# chat-rich-cards — required live desktop matrix for tool and subagent cards

This is the desktop proof that the transcript renders real agent activity,
not a component-only approximation. It drives the packaged Electron app,
signs in locally, pastes the existing OpenAI BYOK value from
`services/ai-backend/.env` through the UI (never logs it), then performs the
following four **real runs** in one new conversation:

```bash
python3 tools/desktop-journeys/chat-rich-cards/rich_chat.py
```

Set `RICH_CHAT_PROVIDER=anthropic` to exercise the same matrix with Anthropic.
The default is `openai`.

## Required cases

| ID  | Exact user action                                                            | Desktop assertions                                                                                                                                          | Priority |
| --- | ---------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------- | -------- |
| R1  | “Use the `web_search` tool exactly once … Do not delegate this work.”        | Exactly one completed `web_search` card; its disclosure contains the accumulated `math.isqrt` args and non-empty result, with no invented connector source. | P0       |
| R2  | “Use exactly ONE subagent to check whether 97 is prime.”                     | Exactly one new fleet card, singular copy, one successfully completed child row, terminal `1/1 done`.                                                       | P0       |
| R3  | “Use exactly TWO subagents in parallel …”                                    | Exactly one parallel fleet with two successfully completed children; one child performs real nested `web_search`.                                           | P0       |
| R4  | “Use web search yourself … and dispatch exactly TWO subagents in parallel …” | In the **same sent message**, one direct `web_search` card and one two-child fleet card.                                                                    | P0       |
| R5  | Open a real tool card by click; close/open it with Space/Enter.              | The disclosure body appears/disappears and payload/source markup remains present.                                                                           | P0       |
| R6  | Click/Space/Enter the live web-search child in a multi-agent fleet.          | Its inline activity region opens/closes and contains the real nested `web_search` timeline entry.                                                           | P0       |
| R7  | Open the right-side **Agents** tab and click/Space/Enter that same child.    | Its native disclosure opens/closes and shows the matching live `web_search` activity, keyed by task id.                                                     | P0       |
| R8  | Send R3 after the completed R2 single-subagent run.                          | The R2 terminal child remains in **Agents** while the R3 run is active; a new run must not erase conversation history.                                      | P0       |
| R9  | Complete R2 and use its fleet-card header by click, Space, and Enter.        | The terminal fleet starts compact, expands/collapses semantically, and returns to its compact state without losing child detail.                            | P0       |
| R10 | Start R3 after the completed R2 single-subagent run.                         | The R2 compact fleet card remains in the **transcript**, reopens with its child result, and is not replaced by the newer run's temporary event stream.      | P0       |
| R11 | Start R3 after the completed R1 direct web-search run.                       | The R1 completed `web_search` card remains in the **transcript** and its args/result disclosure still opens after the newer run has bound.                  | P0       |

R1–R4 are intentionally strict. The test does **not** downgrade a model that
ignores “exactly” to a green `BLOCKED` result: an absent card, bad cardinality,
failed/cancelled child, stale/empty tool arguments, missing nested activity, or
a missing payload detail fails the
run and leaves screenshots plus the Electron/service logs in
`tools/desktop-journeys/runs/chat-rich-cards/`.

## Budget-overrun journey (`budget_overrun.py`)

A separate script in this folder covers the research-shaped turn that made
more tool calls than the per-run budget allows:

```bash
python3 tools/desktop-journeys/chat-rich-cards/budget_overrun.py
```

| ID  | Exact user action                                                                | Desktop assertions                                                                           | Priority |
| --- | -------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------- | -------- |
| B1  | “Catch me up on this week's AI agent releases, then draft a short update …”      | The turn reaches a real assistant answer: no errored tool card, no `RUN INTERRUPTED` banner. | P0       |
| B2  | “Check what shipped in Ethereum's latest upgrade, then draft a community update” | Same, sent as a follow-up in the existing conversation rather than a first run.              | P0       |
| B3  | Eight distinct topics, each demanded with its own search.                        | Same, under a turn that deliberately wants more searches than a tight cap allows.            | P0       |

These reproduce the reported failure where exceeding the tool budget raised a
run-fatal error out of the stream and the user lost every result the run had
already gathered. Hitting the cap must degrade to "the model is told to stop
and answers with what it has", never to a dead run.

To exercise the refusal path itself rather than relying on a model that
happens to search a lot, lower `DefaultToolBudget.MAX_CALLS_PER_RUN` in the
**staged** runtime under `apps/desktop/resources/runtime/<platform>-<arch>/`
before running, then restore it. `RUNTIME_TOOL_CALL_BUDGET` is deliberately
not in the desktop env passthrough allowlist, so it cannot be injected from
the launching shell.

## What the suite covers (and what it does not pretend to cover)

The current transcript’s non-message activity surfaces are the tool card and
the subagent fleet/rows. Normal assistant markdown and optional reasoning text
are message parts, not separate activity-card types. Reasoning summaries only
exist when the selected model emits them; they are covered by the streaming
journey when that capability is configured, but are not falsely manufactured
by this live suite.

`web_search` is a built-in tool, so a real R1 card must not claim an MCP/server
source it did not receive. Source/provenance rows are asserted in a connector
journey once an MCP tool is connected; the shared card intentionally renders
them only from trusted runtime provenance.

Approval and staged-table cards require an approval-producing or
effect-producing tool. They are deliberately a separate journey family: this
suite verifies the real tool/subagent combination requested here and does not
claim a card was tested when no backend event can produce it.

## Failure artifacts and repeatability

Every script invocation has a fresh Electron `userData` directory and writes:

- screenshots after each required card and interaction state;
- renderer and main-process/service logs; and
- the exact failure stack in the process output.

The scripts do not hardcode, print, write, or commit provider credentials.
