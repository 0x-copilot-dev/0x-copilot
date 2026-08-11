# End-to-End Use-Case Flows

Each doc in this folder traces one user-visible behavior end-to-end: React
component → API call → facade → ai-backend route → worker → persistence → SSE →
frontend reducer. The goal is to make every cross-cutting flow concrete: every
step links to a specific file:line so an engineer can trace the system without
reverse-engineering it.

## How these complement existing docs

- [`services/ai-backend/docs/architecture/01-request-lifecycle.md`](../../services/ai-backend/docs/architecture/01-request-lifecycle.md)
  — the happy-path request lifecycle at a high level. Read that first for the
  big picture.
- [`services/ai-backend/docs/architecture/00-system-map.md`](../../services/ai-backend/docs/architecture/00-system-map.md)
  — what each module is and where it sits.
- This folder — **edge-case-aware** scenarios with explicit failure modes and
  recovery paths.

## Scenario index

|   # | Scenario                                                                                             | Class          |
| --: | ---------------------------------------------------------------------------------------------------- | -------------- |
|  01 | [New conversation, simple text response](01-new-conversation-simple.md)                              | happy-path     |
|  02 | [SSE reconnect after a network blip](02-sse-reconnect-after-blip.md)                                 | error/recovery |
|  03 | [Clicking "New thread" while an interrupt is active](03-new-thread-while-interrupt-active.md)        | edge-case      |
|  04 | [Switching conversation while a run is in-flight](04-switch-conversation-during-run.md)              | edge-case      |
|  05 | [Cancel a run mid-stream](05-cancel-run-mid-stream.md)                                               | error/recovery |
|  06 | [MCP server installed but not authenticated, user invokes it](06-mcp-installed-not-authenticated.md) | interrupt      |
|  07 | [Single subagent that calls one tool](07-single-subagent-plus-tool.md)                               | happy-path     |
|  08 | [Parallel: two subagents plus one direct tool](08-parallel-subagents-plus-tool.md)                   | edge-case      |
|  09 | [Single ask-a-question approval](09-single-ask-a-question.md)                                        | interrupt      |
|  10 | [Two ask-a-questions in a row](10-multiple-ask-a-questions-queued.md)                                | interrupt      |
|  11 | [Tool call with streaming args](11-tool-call-streaming-args.md)                                      | edge-case      |
|  12 | [Attachment upload and send](12-attachment-upload-and-send.md)                                       | happy-path     |
|  13 | [Skill invocation (system + user-defined)](13-skill-invocation.md)                                   | happy-path     |

## Reading these safely

These trace the code as it stood when each was written. A `file:line` reference
is a starting point, not a guarantee — confirm the line still says what the doc
claims before relying on it. If you find one that has drifted, fix it in place
rather than adding a caveat.
