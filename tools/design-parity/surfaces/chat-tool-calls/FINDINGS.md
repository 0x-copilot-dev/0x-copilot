# Chat & Tool Calls — parity findings

Baseline: `copilot-project-folder-copy/project/Chat & Tool Calls.dc.html`, its
Focus / Web search walkthrough state. The matching live fixture renders the
shipping `TcChat` inline tool card and `AgentsTab`, not a lookalike.

Run the reproducible comparison with:

```bash
node_modules/.bin/vitest run --config tools/design-parity/vitest.config.mjs \
  tools/design-parity/lib/render-live-chat-tool-calls.test.tsx
node tools/design-parity/lib/extract-playwright.mjs \
  --url 'http://127.0.0.1:8099/surfaces/chat-tool-calls/design/index.html?autoAdvance=false&parity=web-search' \
  --anchors tools/design-parity/surfaces/chat-tool-calls/anchors.json \
  --side design --out tools/design-parity/surfaces/chat-tool-calls/out/design-web-search.json
node tools/design-parity/lib/extract-playwright.mjs \
  --url http://127.0.0.1:8099/surfaces/chat-tool-calls/live/web-search.html \
  --anchors tools/design-parity/surfaces/chat-tool-calls/anchors.json \
  --side live --out tools/design-parity/surfaces/chat-tool-calls/out/live-web-search.json
node tools/design-parity/lib/compare.mjs \
  tools/design-parity/surfaces/chat-tool-calls/out/design-web-search.json \
  tools/design-parity/surfaces/chat-tool-calls/out/live-web-search.json \
  --anchors tools/design-parity/surfaces/chat-tool-calls/anchors.json \
  --state web-search --out tools/design-parity/surfaces/chat-tool-calls/out/report-web-search.md
```

The final measured run has **0 high**, 7 medium, and 31 low differences. The
remaining rows are semantic tag choices, exact sizing in the fixed-width
fixture, and implementation details such as flex/grid primitives; no
token/color/type-role defect remains.

## Tool card

`ToolCallCard` now uses the reference's `--panel` / 10px outer geometry and a
9×11px visual header that is itself the native `<summary>`. It renders a 22px
identity tile, source-backed MCP/access/duration metadata when present, and a
quiet 10×12px detail body with labelled `args`, `result`, `source`, and
delegated-work rows. Payloads stay bounded to 600 selectable characters.

## Agents panel

Focus now renders a purpose-built horizontal `AgentActivityRow`, not the rich
in-thread `SubagentCard`: normal-case 11.5px/500 names, 8.5px model chips, a
9px muted mono activity line, lifecycle glyphs, and 18px hierarchy indent. A
factual `supervisor` role becomes the presentation label “Orchestrator”; it is
not fabricated when no parent role is available. The detailed timeline remains
a compact, keyboard-accessible native disclosure.

The prior behavior fixes also remain: zero-pending runs do not create `Queued
“Hello”`, and card summaries flatten markdown/code before display.

## Interaction audit — exercised controls

This audit pressed every comparable tool/agent control in the Web search state,
then returned it to its initial state.

| Control                                   | Reference behavior                                                             | Live behavior                                                                                                                      | Verdict                                               |
| ----------------------------------------- | ------------------------------------------------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------- |
| Web-search tool header, pointer           | Opens the expanded body on first click and closes it on the second.            | The full compact header opens/closes the native disclosure.                                                                        | Live has the same visual target with safer semantics. |
| Web-search tool header, keyboard          | Not keyboard reachable (`tabIndex = -1`, no role); Enter and Space do nothing. | The full header is focusable (`tabIndex = 0`); Enter opens and Space closes.                                                       | Implemented; live improves accessibility.             |
| Expanded tool payload                     | Visible rows: `args`, `result`, `source`, and repaired child work.             | Labelled `args`, `result`, `source`, and `children`; safe MCP/access/duration metadata and task anchors are present when supplied. | Implemented.                                          |
| Research-agent row, pointer               | No click behavior, no role, and not focusable in this design state.            | Clicking the visual header does nothing.                                                                                           | Both rows are status display, not click targets.      |
| Research-agent detail, pointer + keyboard | No detail affordance in this design state.                                     | A small chevron summary opens/closes the two-step timeline; Enter opens and Space closes.                                          | Implemented as an explicit secondary affordance.      |

### Reference binding repair

The design's `tool("web.search", …)` fixture had `spawn` and `children` data
and a corresponding template section, but `renderVals()` failed to pass either
through (and its plain string children did not match the template's `{ t }`
shape). The local design source and vendored parity baseline now map those
fields correctly, so the reference's expanded child-work tree is exercised by
the interaction gate.

## Measured CSS / component specification

The typefaces already match: both sides use the platform system sans stack and
JetBrains Mono. The mismatch is the **type role, size, weight, color, layout,
and component primitive**, not missing font files.

| Surface element              | Implemented live recipe                                                                                    | Verification                                                                              |
| ---------------------------- | ---------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------- |
| Tool outer/header            | `#111114`, 10px radius, 9×11px header; full native `<summary>` visual target with hidden marker.           | Card/header computed styles match the reference token/geometry; Enter/Space are tested.   |
| Tool identity/summary        | 22px tile; mono 11px/500 name; quiet sans 10.5px/15.75px summary with 3px separation.                      | No high/medium type or color delta remains.                                               |
| Tool detail body             | `#0d0d10`, top hairline, 10×12px padding; 66px mono 9.5px labels and mono 10px values.                     | Labelled payload and source rows are exercised, with 600-character selectable output cap. |
| Provenance/duration/children | Optional safe projection: MCP server, frozen access mode, duration, and delegated task IDs.                | Emitted only from runtime facts; fixture shows all four and child anchor navigation.      |
| Focus agent row              | Shared horizontal flex row, 9×11px / 9px geometry, 18px child indent; rich `SubagentCard` stays in-thread. | No token/color/type-role delta remains.                                                   |
| Agent identity/activity      | Sans 11.5px/500 normal case; mono 8.5px model chip; mono 9px/13.5px muted current activity.                | Factual `supervisor` renders as Orchestrator; progress summary is retained.               |
| Lifecycle/detail             | 14px spinner/check/error treatment; compact native chevron disclosure for timeline/result/approval.        | Pointer, Enter, and Space are exercised in the interaction recorder.                      |

## Implementation status

| Area / interaction              | Current status                                                                                                                     | Implementation / guard                                                                                  | Priority     |
| ------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------- | ------------ |
| Phantom `Queued “Hello”` row    | **Done.** Zero-pending active runs are omitted.                                                                                    | Backend regression test protects the predicate.                                                         | P0 — done    |
| Markdown-heavy summary          | **Done.** Markdown/code is flattened for compact summaries.                                                                        | Shared view-model tests protect it.                                                                     | P0 — done    |
| Tool card, payload, metadata    | **Done.** Full native header disclosure; labelled body; source-backed MCP/access/duration fields.                                  | `ToolCallCard`, runtime/API contracts, projector tests, 600-char safe payload cap.                      | P0 — done    |
| Tool → child work               | **Done.** Design fixture binding repaired; live event projection exposes delegated task IDs and links them to Focus anchors.       | No child/provenance fact is inferred from a tool name.                                                  | P0 — done    |
| Agent data and row layout       | **Done.** Projection retains role/model/current activity; Focus has compact `AgentActivityRow`.                                    | Missing lead name stays absent; factual `supervisor` maps only to the presentation role “Orchestrator”. | P0 — done    |
| Agent lifecycle/detail          | **Done.** Compact spinner/check/error and a native secondary disclosure for timeline/result/approval.                              | Pointer, Enter, and Space are recorded in the interaction audit.                                        | P1 — done    |
| Acceptance and shared ownership | **Done.** Deterministic web/desktop-shared fixture exercises tool, delegated work, and agent disclosure; CSS is in `chat-surface`. | 17/17 anchors per side; 0 high computed-style differences.                                              | P1/P2 — done |
