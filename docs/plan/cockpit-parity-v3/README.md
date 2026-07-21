# Run cockpit — v3 design-parity plan suite

Closing the remaining gaps between the shipped Run cockpit and the Claude Design
**v3 mockup** (`0xCopilot App v3` — `copilot-workspace3.jsx` / `copilot-run-side.jsx`
/ `copilot-composer2.jsx` + `copilot-v3.css` / `copilot.css`). This suite is the
PM + staff-engineer plan for the fronts that are **not yet at parity**, written
after a full-stack design-vs-impl audit (`docs/audit/`) and two deep contract
maps (approvals, subagents).

## Where we are

**Shipped to `main` (the functional / blocking tier is closed):**

| Front                   | State                                                                    |
| ----------------------- | ------------------------------------------------------------------------ |
| Type & color system     | 13px message anchor, tokens not hex, quiet scale (#130, #148)            |
| Composer                | Compact auto-height shell, 11px radius, mono model pill, v3 tiers (#130) |
| Tab strip               | 11.5px, `flex:1`, boxed-active, 9px inset, **v3 order** (#130, #136)     |
| Studio rail             | Draggable + persisted width (#136)                                       |
| **Streaming reply**     | Renders **live in both modes** — was completely dead (#148)              |
| **Focus chat**          | Real transcript + composer — was a static stub (#148)                    |
| **Sources tab**         | Fed off the stream — was permanently empty (#149)                        |
| Model picker ↔ Settings | Solved on `main` via facade-backed default (#131/#132/#134)              |

**Planned here (the remaining ~30–40%):**

| Plan                           | Front                                                                |
| ------------------------------ | -------------------------------------------------------------------- |
| [01](01-focus-details-rail.md) | Focus details rail (Agents/Approvals/Sources + 46px collapse strip)  |
| [02](02-rail-side.md)          | Studio rail side (left default + left/right toggle)                  |
| [03](03-approvals-inbox.md)    | Approvals tab: inline sign-off inbox                                 |
| [04](04-agents-card.md)        | Agents rich card                                                     |
| [05](05-visual-fidelity.md)    | v3 visual-fidelity pass (badge, who-label, ack line, source rowlist) |
| [06](06-reliability.md)        | Streaming reliability on the approval wait                           |

## The one architecture the whole cockpit runs on

Every plan builds on the invariant the cockpit already declares — **one event
projection** (FR-3.3). The cockpit reads exactly one source, `useRunSession.events`,
turned into UI by **pure selectors / binders**: `projectChatMessages` +
`useRunTranscript` (chat), `projectSubagents` (agents), `projectApprovals` /
`toApprovalsQueue` (approvals), `useRunSources` (sources). No plan may open a
second SSE or projector.

Non-negotiables carried into every plan:

- **Single mount (FR-3.9)** — the one `TcChat` never remounts on a Studio↔Focus or
  tab switch; it is positioned by CSS grid-area, never moved in the tree.
- **Substrate boundary** — `chat-surface` is framework-agnostic; substrate goes
  through ports; persisted prefs use the `KeyValueStore` port (mirror `useRunMode`
  / `useRailWidth`).
- **Host-fed / presentational** — tab bodies and cards take normalized props;
  fetching and `POST`s live in the host binder (`RunDestination`).
- **Honest data (no band-aids)** — the UI is never wired to data the backend does
  not produce.

## Unbackable as drawn — the cross-cutting honesty

The v3 mockup illustrates several fields the current contract **does not produce**.
Each plan calls these out; collected here so the scope is unambiguous. These are
either **DESCOPE** (drop the mockup element) or **NEW-CONTRACT** (a deliberate,
separately-scoped cross-stack addition) — never faked from unrelated data.

| Mockup element                                                 | Reality                                                                                                     | Disposition                                                            |
| -------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------- |
| Approvals **per-human-signer rows** ("Sarah/Marcus each sign") | No signer roster exists; only parallel-tool-call **batches** (`batch_id`) + one-at-a-time forwarding chains | **DESCOPE** → group by `batch_id`                                      |
| Approvals **"Auto-approved today" log**                        | Zero auto-approved signal anywhere; policy-allowed tools never raise an approval                            | **DESCOPE**                                                            |
| Agents **bounded progress %** / **"step X of Y"**              | Subagents are an open tool loop bounded by a timeout + token budget — no total _N_                          | **DESCOPE** → indeterminate bar + real counts                          |
| Agents **model name** on the card                              | Not on the run/subagent contract (only composer _input_)                                                    | **NEW-CONTRACT** → server projects resolved model onto `SubagentEntry` |
| **Cross-run / "Scheduled" agents** fleet                       | No background/scheduled feed for this tab                                                                   | **DESCOPE** → this-conversation reality                                |
| **Tick-mark plan** (`.plan-step`)                              | No plan/checklist projection on the run stream (to verify per plan 05)                                      | **DESCOPE or NEW-CONTRACT**                                            |

The net: **layout, type, composer, tabs, streaming, and sources are ~1:1 achievable**
(done or planned); a handful of the mockup's richest "receipt" flourishes are
illustrative and are honestly descoped or promoted to explicit new contracts.

## Recommended build order

1. **06 Reliability** — smallest blast radius, protects the HITL path already shipped.
2. **03 Approvals inbox** — highest user value; fixes a live dead-seam bug.
3. **01 Focus details rail** — restores Agents/Approvals/Sources in the default mode.
4. **04 Agents card** — includes the one new server field (model); sequence the
   cross-stack piece behind the FE reuse.
5. **05 Visual fidelity** — cheap, ships continuously alongside the above.
6. **02 Rail side** — smallest; a product decision on the default (left vs right).

Each plan lists independently-shippable commits; nothing here requires a big-bang
merge. Update `docs/audit` findings + the knowledge graph as each lands.
