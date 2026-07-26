# Chat & Tool Calls — parity findings

Baseline: `copilot-project-folder-copy/project/Chat & Tool Calls.dc.html`, in
its Focus / Web search walkthrough state. The live fixture renders the
shipping shared `TcChat` tool card and `AgentsTab`; it is not a lookalike.

## Reproduce the current audit

Render the live fixture, serve the parity files, then run the interaction and
computed-style passes. The interaction command is assertive: a changed control
contract exits non-zero.

```bash
node_modules/.bin/vitest run --config tools/design-parity/vitest.config.mjs \
  tools/design-parity/lib/render-live-chat-tool-calls.test.tsx
(cd tools/design-parity && python3 -m http.server 8109)
node tools/design-parity/lib/audit-interactions-chat-tool-calls.mjs \
  --base-url http://127.0.0.1:8109 \
  --out tools/design-parity/surfaces/chat-tool-calls/out/interaction-web-search.json
node tools/design-parity/lib/extract-playwright.mjs \
  --url 'http://127.0.0.1:8109/surfaces/chat-tool-calls/design/index.html?autoAdvance=false&parity=web-search' \
  --anchors tools/design-parity/surfaces/chat-tool-calls/anchors.json \
  --side design --out tools/design-parity/surfaces/chat-tool-calls/out/design-web-search.json
node tools/design-parity/lib/extract-playwright.mjs \
  --url http://127.0.0.1:8109/surfaces/chat-tool-calls/live/web-search.html \
  --anchors tools/design-parity/surfaces/chat-tool-calls/anchors.json \
  --side live --out tools/design-parity/surfaces/chat-tool-calls/out/live-web-search.json
node tools/design-parity/lib/compare.mjs \
  tools/design-parity/surfaces/chat-tool-calls/out/design-web-search.json \
  tools/design-parity/surfaces/chat-tool-calls/out/live-web-search.json \
  --anchors tools/design-parity/surfaces/chat-tool-calls/anchors.json \
  --state web-search --out tools/design-parity/surfaces/chat-tool-calls/out/report-web-search.md
```

Latest run: **17/17** design anchors and **16/17** live anchors matched. The
sole absent live anchor is the documented, intended reference-only
Orchestrator. The computed-style report has **0 high**, **7 medium**, **28
low**, and **12 info** findings.

The medium differences are implementation primitives rather than token/type
role failures: grid versus flex centering for the tool and lifecycle glyphs,
and the reference's spinner shape. The low rows are mostly fixed-fixture width
and height differences plus native semantic elements (`details`, `summary`,
`span`, and `p`) replacing generic `div`s. The complete measured list is in
[`out/report-web-search.md`](out/report-web-search.md).

## Factual agent hierarchy

The reference draws an Orchestrator and indents Research below it. The runtime
event used by this state supplies only `parent_agent_role: "supervisor"`; it
does not emit an Orchestrator `task_id` or a `parent_task_id` relationship.
Focus therefore renders the real Research task as a depth-zero row. It must not
invent an Orchestrator task, nor show a misleading indent. `anchors.json`
records both the missing reference-only lead and the resulting margin as
intentional, narrowly scoped divergences; every other property still compares.

## Interaction audit — every exposed control

| Surface / control              | Pointer assertion                                                                                 | Keyboard assertion                                           | Result                                                |
| ------------------------------ | ------------------------------------------------------------------------------------------------- | ------------------------------------------------------------ | ----------------------------------------------------- |
| Design Web-search header       | First click reveals args, result, source, and child work.                                         | It has no role or tab stop; Enter and Space leave it closed. | Recorded as a source-design accessibility limitation. |
| Live Web-search native summary | Click reveals args, result, source, MCP provenance, read access, duration, and delegated task ID. | Enter opens and Space closes.                                | Pass.                                                 |
| Design Research scan row       | Click changes no DOM state.                                                                       | It has no role or tab stop.                                  | Correctly recorded as non-interactive.                |
| Live Research scan content     | Click does not pretend to be a disclosure control.                                                | No hidden keyboard interaction.                              | Pass.                                                 |
| Live Research detail summary   | Click opens the two-item tool timeline.                                                           | Enter opens and Space closes.                                | Pass.                                                 |

The runner verifies those observations before writing JSON, so a stale selector,
missing payload field, broken disclosure, or accidental interactive scan row is
a test failure rather than a hand-written audit claim.

## Current implementation status

| Area                                  | Status             | Guard                                                                                                                                     |
| ------------------------------------- | ------------------ | ----------------------------------------------------------------------------------------------------------------------------------------- |
| Tool disclosure and safe rich payload | Done               | Pointer/keyboard audit checks args, result, source, provenance, access, duration, and delegated task IDs.                                 |
| Agent activity disclosure             | Done               | Pointer/keyboard audit checks the two projected timeline entries and native disclosure state.                                             |
| Role-only orchestrator handling       | Done               | Fixture supplies the real `supervisor` role-only hint; the live selector requires the factual root task rather than a synthetic lead.     |
| Computed-style parity                 | Fresh and measured | Renderer, extractor, and comparison report run against 17 explicit anchors. Residual medium/low differences remain visible in the report. |
