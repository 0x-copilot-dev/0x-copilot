# End-to-End Use-Case Flows

Each doc in this folder traces one user-visible behavior end-to-end: React component → API call → facade → ai-backend route → worker → persistence → SSE → frontend reducer. The goal is to make every cross-cutting flow concrete: every step links to a specific file:line so an engineer can trace the system without reverse-engineering it.

## How these complement existing docs

- [`/services/ai-backend/docs/architecture/data-flow.md`](../../services/ai-backend/docs/architecture/data-flow.md) — five **happy-path** flows, high-level sequence diagrams. Read that first for the big picture.
- [`/docs/decomp/`](../decomp/) — **per-file** structural inventory of bespoke logic. The use-case docs link into specific decomp sections at each step.
- This folder — **edge-case-aware** scenarios with explicit failure modes and recovery paths.

## Scenario index

|   # | Scenario                                                                                                        | Class          |
| --: | --------------------------------------------------------------------------------------------------------------- | -------------- |
|  01 | [Cold start: user sends first message in a new conversation](01-cold-start-first-message.md)                    | happy-path     |
|  02 | [Simple greeting "hi" — no tools, minimal token budget](02-simple-greeting-no-tools.md)                         | happy-path     |
|  03 | [Tool call requiring approval (deterministic card render)](03-tool-call-with-approval.md)                       | interrupt      |
|  04 | [`ask_a_question` interrupt — single answer cycle](04-ask-a-question-single.md)                                 | interrupt      |
|  05 | [Multiple consecutive `ask_a_question` interrupts](05-ask-a-question-consecutive.md)                            | interrupt      |
|  06 | [MCP server installed but not authenticated; user explicitly invokes it](06-mcp-installed-not-authenticated.md) | interrupt      |
|  07 | [MCP token expired mid-call — refresh + resume](07-mcp-token-expired-mid-call.md)                               | error/recovery |
|  08 | [User cancels run mid-stream](08-user-cancels-mid-stream.md)                                                    | error/recovery |
|  09 | [User clicks "new thread" while interrupt is active](09-new-thread-while-interrupt-active.md)                   | edge-case      |
|  10 | [Single subagent delegation](10-single-subagent-delegation.md)                                                  | happy-path     |
|  11 | [Two subagents + one tool concurrently in one assistant turn](11-multi-subagent-plus-tool.md)                   | edge-case      |
|  12 | [Stream disconnect + reconnect with `after_sequence`](12-stream-disconnect-and-resume.md)                       | error/recovery |
|  13 | [Memory compression triggered by token budget](13-memory-compression-token-budget.md)                           | edge-case      |
|  14 | [Subagent fails / returns invalid output contract](14-subagent-fails-output-contract.md)                        | error/recovery |

## Per-doc template

Each scenario follows this structure:

1. **Trigger** — what user action / system condition kicks this off, what the user sees.
2. **Pre-conditions** — required state (existing conversation? MCP server registered? tokens cached? interrupt pending?).
3. **End-to-end call graph** — numbered sequence with file:line links and function/method names. Each step shows: layer, file:line, what changes (state, DB rows, events appended).
4. **Sequence diagram** (mermaid) — actors: User, UI, Facade, ApiSvc, Worker, Postgres, LLM, MCP. Lanes for SSE backflow.
5. **Events emitted** — table: `sequence_no`, event_kind, payload shape, FE reducer that consumes it.
6. **State transitions** — Run.status, Conversation.state, Approval.state, Subagent.state moves; State enum + mutating function.
7. **Persistence touch points** — tables inserted/updated; append-only flags; optimistic-lock retries; outbox rows.
8. **Failure modes & recovery** — what happens if X fails (network, worker crash, DB conflict, tool timeout); recovery; what user sees.
9. **Cross-cluster references** — links to relevant decomp docs.

## Glossary

- **Run** — one execution of an agent over a conversation, owned by a single worker claim.
- **Event** — a typed `RuntimeEventEnvelope` row appended to the event store with monotonic `sequence_no` per run.
- **Approval** — paused-run record awaiting a user decision (covers `ask_a_question`, MCP auth, tool-call approval).
- **Outbox** — durable command queue (run / cancel / approval) consumed by the worker.
- **Card** — UI presentation metadata attached to an event (deterministic template or LLM-polished).
- **Capability** — a tool, MCP server, or skill exposed to the agent for a given run.
- **Subagent** — a narrowed-scope agent invoked by the supervisor; runs as a durable async task.
