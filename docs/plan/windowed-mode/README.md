# Windowed Mode

What breaks when the 0xCopilot desktop app runs in a **normal window** instead of
full-screen — and how to fix each of it.

Opened from a three-way comparison of the same moment in Claude Desktop, Codex,
and 0xCopilot at comparable window sizes. Every finding below is grounded in a
specific file and line, not in a screenshot alone.

## The one-line diagnosis

Claude and Codex treat a small window as a **budget problem** and spend the
scarce pixels on navigation and the answer. 0xCopilot spends them on chrome and
process — and structurally cannot do otherwise, because the shared surface has
**zero media queries in `shell/` and `composer/`, zero container queries anywhere,
and a fixed 48px rail**. There is no narrow layout; there is one layout, cropped.

## Read in this order

| PRD                                       | Finding                                           | Severity | Wave |
| ----------------------------------------- | ------------------------------------------------- | -------- | ---- |
| [PRD-00](./PRD-00-overview.md)            | Overview + the shared responsive substrate        | —        | A    |
| [PRD-01](./PRD-01-thread-switching.md)    | No thread list, and no way to get one             | P0       | B    |
| [PRD-02](./PRD-02-run-header.md)          | Run header is decoration; the goal is `clip()`-ed | P0       | B    |
| [PRD-03](./PRD-03-transcript-density.md)  | Transcript is all process, no answer              | P0       | B    |
| [PRD-04](./PRD-04-recovered-failures.md)  | A recovered failure stays red forever             | P1       | C    |
| [PRD-05](./PRD-05-content-grid.md)        | Two content grids in one column                   | P2 ⚠️    | D    |
| [PRD-06](./PRD-06-chip-anchoring.md)      | Card-header chips anchor to nothing               | P2       | D    |
| [PRD-07](./PRD-07-tool-metadata.md)       | Tool metadata is inconsistent                     | P2       | C    |
| [PRD-08](./PRD-08-timeline-legibility.md) | The bottom timeline reads as unlabeled dots       | P1       | C    |
| [PRD-09](./PRD-09-edge-affordances.md)    | No message actions; bare rail identity            | P2       | D    |

⚠️ **PRD-05 is downgraded from P1.** On closer reading the right-aligned user
bubble is deliberate, matches both references and the v3 design, and is not a
bug. See its §1.1 — most of that finding did not survive contact with the code,
and the PRD says so.

## What each wave costs

- **Wave A** — one hook, one constant file, one `data-` attribute. Small, and
  everything structural depends on it.
- **Wave B** — the three P0s. All three are _reveal_ work, not build work: the
  thread data already exists (`useChatsArchive`), the run goal already exists
  (clipped to 1×1), and the grouping pattern already exists (`ReasoningGroup`).
- **Wave C** — independent of Wave A. Correctness bugs that are worse in a small
  window but not caused by width. **Start these immediately, in parallel.**
- **Wave D** — polish. PRD-05 can be cut to two requirements if the program runs
  long.

## The recurring theme

Five of the nine findings are the same shape: **the capability is already built
and is not reaching the user.**

| PRD    | Already exists                                        | Why you can't see it                                  |
| ------ | ----------------------------------------------------- | ----------------------------------------------------- |
| PRD-01 | `useChatsArchive`, `toChatArchiveRow`, `ContextPanel` | mounted only in a destination the cockpit can't reach |
| PRD-02 | run goal, kicker, status pulse, scrub label           | `clip: rect(0,0,0,0)`                                 |
| PRD-03 | `ReasoningGroup` — the exact accordion needed         | applied to reasoning parts, never to tool calls       |
| PRD-08 | full scrubber: keyboard chords, tooltips, ARIA        | rendered as 8px unlabelled dots                       |
| PRD-09 | `ClipboardPort`, implemented by both hosts            | no button calls it                                    |

That is a good problem to have — the fixes are mostly small — but it is also a
pattern worth naming: **this codebase builds capability faster than it exposes
it.** Two of the PRDs (PRD-04, PRD-07) add instrumentation for exactly this
reason: a silent fallthrough means nobody ever learns the gap exists.

## Corrections to the original review

Two things I got wrong in the first pass, corrected in the PRDs:

1. **`"0xCopilot couldn't complete this step."` is not a model paraphrase.** It is
   a literal template default at
   `services/ai-backend/.../presentation_templates.py:78`, sitting under a good
   ten-code taxonomy. Seeing it means the error carried no mapped `error_code` —
   and the fallthrough is silent, so the taxonomy can't grow. (PRD-04)
2. **The bottom dot strip is not decoration.** It is `TcMiniTimeline`, a working
   time-travel scrubber. The bug is that it is illegible as one, which is a
   different and more interesting problem. (PRD-08)
