# AI Backend — Knowledge Base

Agent-first documentation. Every node answers one question and links to adjacent nodes.
Read this file first; all other paths branch from here.

## What this service does

`ai-backend` runs agentic conversations. It accepts a user message via HTTP, queues a worker
run, drives a LangGraph execution against an LLM provider, streams typed events back over
SSE, and persists everything durably. It owns no auth, no tenant management, no billing UI —
those live in `backend`. It calls `backend`'s `/internal/v1/` routes for MCP server lists,
auth session creation, and skill bundles.

## Navigation — which doc answers which question

| Question                                                         | Read                                                                         |
| ---------------------------------------------------------------- | ---------------------------------------------------------------------------- |
| How is the code organised? What does each module own?            | [architecture/00-system-map.md](architecture/00-system-map.md)               |
| How does a request travel from browser to SSE stream?            | [architecture/01-request-lifecycle.md](architecture/01-request-lifecycle.md) |
| What Pydantic shapes and port protocols exist at every boundary? | [architecture/02-contracts.md](architecture/02-contracts.md)                 |
| How do in-memory and Postgres adapters differ? How do I add one? | [architecture/03-adapters.md](architecture/03-adapters.md)                   |
| How does SSE streaming and resume work?                          | [features/streaming-sse.md](features/streaming-sse.md)                       |
| How do built-in tools and MCP tools get loaded and called?       | [features/tool-calling.md](features/tool-calling.md)                         |
| How do citations flow from tool results into model output?       | [features/citations.md](features/citations.md)                               |
| How does memory compression and context management work?         | [features/memory-context.md](features/memory-context.md)                     |
| How do subagents / delegation work?                              | [features/subagents.md](features/subagents.md)                               |
| How does the MCP auth interrupt → approval → resume cycle work?  | [features/approvals.md](features/approvals.md)                               |
| How are token budgets enforced and costs charged?                | [features/budgets.md](features/budgets.md)                                   |
| How does draft creation and the draft send flow work?            | [features/drafts.md](features/drafts.md)                                     |
| How does data retention sweep and backfill work?                 | [features/retention.md](features/retention.md)                               |
| How are token usage metrics recorded and queried?                | [features/usage-metrics.md](features/usage-metrics.md)                       |
| How does the reasoning / thinking model stream work?             | [features/thinking-reasoning.md](features/thinking-reasoning.md)             |
| How do I add a new built-in tool?                                | [guides/add-builtin-tool.md](guides/add-builtin-tool.md)                     |
| How do I add new MCP middleware?                                 | [guides/add-mcp-middleware.md](guides/add-mcp-middleware.md)                 |
| How do I add a new event type?                                   | [guides/add-event-type.md](guides/add-event-type.md)                         |
| Full `RuntimeEventEnvelope` type + payload reference             | [reference/event-types.md](reference/event-types.md)                         |
| All port protocol method signatures                              | [reference/persistence-ports.md](reference/persistence-ports.md)             |
| Every environment variable and what it controls                  | [reference/env-vars.md](reference/env-vars.md)                               |

## Feature map — user action → docs node

| User does                                 | Feature doc                                             | Flow diagram                                                              |
| ----------------------------------------- | ------------------------------------------------------- | ------------------------------------------------------------------------- |
| Send a message (no tools)                 | [streaming-sse.md](features/streaming-sse.md)           | [f1-single-turn](architecture/diagrams/flows/f1-single-turn.puml)         |
| Model calls a built-in tool               | [tool-calling.md](features/tool-calling.md)             | [f2-multi-turn-tool](architecture/diagrams/flows/f2-multi-turn-tool.puml) |
| Browser reconnects to SSE mid-run         | [streaming-sse.md](features/streaming-sse.md)           | [f3-sse-resume](architecture/diagrams/flows/f3-sse-resume.puml)           |
| User cancels a running run                | [streaming-sse.md](features/streaming-sse.md)           | [f4-cancel](architecture/diagrams/flows/f4-cancel.puml)                   |
| Model cites sources from MCP / web search | [citations.md](features/citations.md)                   | [f5-citations](architecture/diagrams/flows/f5-citations.puml)             |
| Model uses a reasoning/thinking mode      | [thinking-reasoning.md](features/thinking-reasoning.md) | [f6-thinking](architecture/diagrams/flows/f6-thinking.puml)               |
| User adds an MCP connector                | [tool-calling.md](features/tool-calling.md)             | [f7-mcp-add](architecture/diagrams/flows/f7-mcp-add.puml)                 |
| Model hits an unauthenticated MCP tool    | [approvals.md](features/approvals.md)                   | [f8-mcp-auth](architecture/diagrams/flows/f8-mcp-auth.puml)               |
| User queries token usage / /context       | [usage-metrics.md](features/usage-metrics.md)           | [f9-usage-metrics](architecture/diagrams/flows/f9-usage-metrics.puml)     |

## Architecture cluster diagrams

Visual cluster maps live in [architecture/diagrams/clusters/](architecture/diagrams/clusters/).
Each diagram covers one system layer; the index below maps cluster to diagram.

| Cluster                                           | Diagram                                                                               |
| ------------------------------------------------- | ------------------------------------------------------------------------------------- |
| Full system overview (all clusters)               | [01-system-overview.puml](architecture/diagrams/clusters/01-system-overview.puml)     |
| Runtime API edge (FastAPI, auth, SSE)             | [02-runtime-api.puml](architecture/diagrams/clusters/02-runtime-api.puml)             |
| Runtime Worker (queue loop, handlers, jobs)       | [03-runtime-worker.puml](architecture/diagrams/clusters/03-runtime-worker.puml)       |
| Capabilities (tools, MCP, skills, citations)      | [04-capabilities.puml](architecture/diagrams/clusters/04-capabilities.puml)           |
| Runtime Services (domain services, coordinators)  | [05-runtime-services.puml](architecture/diagrams/clusters/05-runtime-services.puml)   |
| Persistence (ports, records, schema)              | [06-persistence.puml](architecture/diagrams/clusters/06-persistence.puml)             |
| Adapters (in-memory + Postgres)                   | [07-adapters.puml](architecture/diagrams/clusters/07-adapters.puml)                   |
| Agent execution + provider streams                | [08-execution-prompts.puml](architecture/diagrams/clusters/08-execution-prompts.puml) |
| Delegation / subagents                            | [09-delegation.puml](architecture/diagrams/clusters/09-delegation.puml)               |
| Context / memory                                  | [10-context-memory.puml](architecture/diagrams/clusters/10-context-memory.puml)       |
| Cross-cutting (observability, budgets, retention) | [11-cross-cutting.puml](architecture/diagrams/clusters/11-cross-cutting.puml)         |

## Spec and rule docs (unchanged)

- `docs/specs/` — feature specs (authoritative for implementation decisions)
- `docs/rules/` — engineering rules (Python, testing, architecture principles)
- `docs/prds/` — product requirement docs for work not yet shipped
- `docs/testing/` — test strategy, edge-case matrix, fixtures

## Definition of done

A change is complete when: code, tests, and the relevant doc in this KB all agree.
