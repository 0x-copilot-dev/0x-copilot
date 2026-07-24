# focus-mode — Run cockpit Focus-mode activity rendering

Live-smoke user journeys for the way the **Run cockpit renders an in-flight run**:
the streaming answer, inline tool cards, inline subagent fleet cards, and the
Run-details focus panel. These drive the **real** supervised desktop app (Electron

- embedded Postgres + the three Python services) through `driver.mjs`, adding a
  provider key in the FTUE and then sending three probe prompts, asserting on the
  **shipped** `tc-chat-*` / `tc-focus-*` testIds and screenshotting each state.

Shipped in PRs **#258 / #259**. The cockpit renders an active run as a transcript
(`tc-chat` → `tc-chat-messages` → one `tc-chat-message-<id>` per turn), with tool
cards and fleet cards **interleaved inline** at the point they ran.

> **Studio is OFF (`STUDIO_ENABLED=false`).** The cockpit is therefore **always
> Focus** and the run-mode switcher is hidden. The journey does **not** click any
> Studio/Focus toggle — it just asserts `thread-canvas[data-mode=focus]`
> (`s.run_mode() == "focus"`).

Runnable: [`focus_activity.py`](./focus_activity.py). Requires a keyed provider —
the script does the FTUE (`sign_in_local` → `ftue_add_key`) reading the key from
`services/ai-backend/.env` via `load_env_key` (never printed). Default provider
`openai`; override with `FOCUS_PROVIDER=anthropic`.

Result vocabulary: **PASS** (every asserted step held) · **BLOCKED** (a documented
tail that cannot be exercised without a capability the current stack lacks —
exit 0, noted) · **FAIL** (a step that should hold did not — exit 1).

---

## J1 — Streaming grows incrementally

**User story:** I ask for a long, tool-free answer and watch the reply _type
itself out_ — the text lengthens as the model streams, it does not pop in whole.

Prompt: `Write a detailed 220 word explanation of how a bicycle works, no tools.`

| Step                                                                       | Coverage                                                                                                                                  |
| -------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------- |
| FTUE key added → first message sent → lands on the run                     | ASSERTED (`sign-in-button` → `first-run-add-key` → `composer-textarea`/Send → `tc-chat` + `tc-chat-message-*`)                            |
| cockpit is Focus                                                           | ASSERTED (`thread-canvas[data-mode=focus]`, `s.run_mode()=="focus"`)                                                                      |
| the last assistant message's text **grows across polls** (≥3 growth steps) | ASSERTED (rapid poll of `[data-testid^=tc-chat-message-][data-role=assistant]` innerText length; strictly-increasing transitions counted) |
| growth is incremental, not one atomic jump                                 | ASSERTED (≥3 distinct increasing lengths observed before completion)                                                                      |

Root cause fixed in #258: the `model_delta` payload is `{delta,message}` — the
renderer previously read a non-existent `{text}` and so only repainted atomically.

## J2 — Inline tool card

**User story:** I ask something that needs the web; a compact tool card appears
**in the transcript** where the tool ran, shows the tool + a done state, and I can
expand its details.

Prompt: `Search the web for what deepagents are and summarize in 2 lines.`

| Step                                              | Coverage                                                                                   |
| ------------------------------------------------- | ------------------------------------------------------------------------------------------ |
| follow-up message sent in the run composer        | ASSERTED (`composer-textarea` + `Send message`, new assistant turn appears)                |
| an **inline** tool card appears in the transcript | ASSERTED (`[data-testid^=tc-chat-tool-]` present, between the user message and the answer) |
| the card names the tool (`web_search`)            | ASSERTED (card text contains `web_search`)                                                 |
| the card reaches a **done** state                 | ASSERTED (`[data-tool-status=done]`; status label `done`)                                  |
| the card exposes a **Details** expander           | ASSERTED (`<summary>Details</summary>` present when args/result exist)                     |

## J3 — Inline subagent fleet card

**User story:** I ask for exactly one subagent; an inline fleet card reads
"Dispatched a subagent" (singular) and progresses to done.

Prompt: `Use exactly ONE subagent to check whether 97 is prime.`

| Step                                  | Coverage                                                                                                 |
| ------------------------------------- | -------------------------------------------------------------------------------------------------------- |
| follow-up message sent                | ASSERTED (`composer-textarea` + `Send message`)                                                          |
| an **inline** fleet card appears      | ASSERTED (`[data-testid^=tc-chat-fleet-]` present in the transcript)                                     |
| singular copy for a 1-agent fleet     | ASSERTED (card text reads `Dispatched a subagent`; a ≥2 batch reads `Dispatched N subagents … 2/2 done`) |
| the fleet progresses `0/1 → 1/1 done` | ASSERTED-IF-OBSERVED (poll the card text for a running→done transition; done state asserted)             |

## J4 — Focus panel + collapse

**User story:** The Run-details panel shows Agents / Approvals / Sources; I can
collapse it to a slim icon rail and expand it back.

| Step                                    | Coverage                                                                                                                               |
| --------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------- |
| the Run-details panel is shown (~324px) | ASSERTED (`[data-testid=tc-focus-panel]`; `offsetWidth` ≈ 324)                                                                         |
| the panel exposes the active tab body   | ASSERTED (`[data-testid^=tc-focus-panel-]`, one of Agents/Approvals/Sources)                                                           |
| collapse shrinks it to a 46px icon rail | ASSERTED (`[data-testid=tc-focus-panel-collapse]` → `tc-focus-panel` gone, `[data-testid=tc-focus-strip]` present, `offsetWidth` ≈ 46) |
| re-expand restores the full panel       | ASSERTED (`[data-testid=tc-focus-strip-expand]` → `tc-focus-panel` present, `tc-focus-strip` gone)                                     |

---

## BLOCKED-until

- **Thinking / reasoning block** — the model's reasoning block
  (`Reasoning`, rendered from a `reasoning` message part) only appears when the
  **backing model emits reasoning summaries**. In this session a `gpt-5.4-mini`
  run emitted **0 reasoning events**, so the thinking block never rendered. This is
  a **BLOCKED-until** item: it needs a summary-emitting model (a reasoning model
  configured to stream reasoning summaries). The journey does **not** fail when the
  thinking block is absent — it prints a `BLOCKED` line noting the missing
  capability.
- **Tool card / fleet card assertions depend on the model actually choosing to
  call the tool / dispatch a subagent.** If a keyed model declines the tool for a
  given prompt, the corresponding journey prints `BLOCKED` (capability present, not
  exercised) rather than `FAIL`. J1 (streaming) and J4 (focus panel) are
  model-choice-independent and always assert hard.
