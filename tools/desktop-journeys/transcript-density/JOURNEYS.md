# transcript-density — the answer must win (PRD-03)

Live journeys for
[`docs/plan/windowed-mode/PRD-03-transcript-density.md`](../../../docs/plan/windowed-mode/PRD-03-transcript-density.md).

## The user story

> Sarah asks for something that takes real work — a web search, a directory
> listing, and a delegated subagent. She watches it happen, then reads the
> answer. She should not have to scroll past the process to find the conclusion.

Before PRD-03 the run rendered every activity item as its own bordered card, so
a six-step run took roughly 55% of a 640px transcript and the one-line answer was
the least prominent thing on screen.

## `long_run_grouping.py`

Sends ONE deliberately long prompt (web search + filesystem listing + exactly one
subagent) and asserts what actually rendered:

| #   | Assertion                                                                 | PRD ref   |
| --- | ------------------------------------------------------------------------- | --------- |
| 1   | While the run is live, the group is **expanded** — you watch the work     | D-3.2     |
| 2   | Once every member settles, the group is **collapsed** to one line         | D-3.2     |
| 3   | A settled group's label reads `Worked for … · N steps`                    | §5        |
| 4   | A **failed** run keeps its group open instead                             | D-3.5     |
| 5   | The final answer is the last transcript item and is **outside** the group | §1        |
| 6   | A collapsed group is ≈ one card tall regardless of member count           | FR-3.10   |
| 7   | The disclosure still opens on click — folded, never hidden                | Non-goals |
| 8   | The contract holds at 640px and the document never scrolls                | D-3.6     |

### Two findings it RECORDS rather than asserts

PRD-03 D-3.1 leaves one question open on purpose, because it is empirical:
`chatProjection` stamps the synthesized assistant message with the **first**
delta's timestamp, and `mergeStream` slots activity by timestamp — so a run that
emits text before later tool calls could anchor the streaming answer _between_
activity items and split one turn's work into two groups.

The journey logs `FINDING single group …` or `FINDING N groups — the assistant
message SPLIT the run`, plus the count of loose (ungrouped) activity cards. A
split is not a failure of this journey; it is the input to deciding whether the
fold needs to look through a still-streaming message.

### Running it

```bash
# frontend-only change → rebuild the bundle, reuse the primary checkout's stage
npm run build --workspace @0x-copilot/desktop
COPILOT_HOME=/path/to/primary/apps/desktop/resources \
  python3 tools/desktop-journeys/transcript-density/long_run_grouping.py
```

Needs `OPENAI_API_KEY` (or `DENSITY_PROVIDER`'s key) in
`services/ai-backend/.env`. Exit `3` means the key or a staged runtime is absent —
a skip, not a pass.
