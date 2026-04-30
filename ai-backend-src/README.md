# AI Backend

Python backend workspace for the enterprise search AI layer.

This backend is currently in a spec-first phase. Future implementation agents must read the relevant PRD, technical spec, testing guidance, and engineering rules before writing code.

## Workspace Context

`ai-backend-src` is one component inside the larger `enterprise-search` workspace. It is not the whole product.

The workspace is intended to become one GitHub monorepo with multiple deployable components. The future canonical location for this service is expected to be `services/ai-backend`; `ai-backend-src` is the current transitional path and should not be moved until docs, rules, CI paths, imports, and setup scripts are updated together.

Planned sibling components:

- `services/backend-facade`: stable product-facing API layer that frontend and native apps call.
- `services/backend`: core backend services for persistence, auth integration, permissions, admin workflows, and jobs.
- `apps/frontend`: web work surface for enterprise search and agent interaction.
- `apps/windows`: Windows desktop client.
- `apps/mac`: macOS desktop client.

This service owns AI orchestration concerns only: Deep Agents runtime, LangGraph execution, LangChain tool wiring, dynamic tool and MCP loading, skills, context/memory management, subagents, streaming, and typed agent contracts. Product API boundaries should flow through `backend-facade`; durable product state and non-agent backend concerns should live in `backend`.

Read the workspace architecture before changing runtime APIs:

- `../docs/architecture/workspace-topology.md`
- `../docs/architecture/service-boundaries.md`
- `../docs/decisions/0001-monorepo-with-deployable-services.md`

## What Enterprise Search Means Here

Enterprise search is the user-facing entry point for a broader enterprise work surface. It should help executives and employees ask natural-language questions, find context, understand source-backed answers, and eventually take action across the systems where work already lives.

In this project, enterprise search means:

- Searching across Slack, Google Workspace, Atlassian, internal APIs, MCP servers, and future enterprise knowledge indexes.
- Respecting user, organization, connector, document, and action permissions before any capability is visible to the model.
- Returning grounded answers with source context, confidence signals, and enough traceability for users to trust the result.
- Dynamically loading tools, skills, MCP servers, memories, and subagents so the agent has the right capability without bloating every prompt.
- Managing long-running work through context compression, summarization, memory, streaming updates, and subagent delegation.
- Serving non-engineer users first: the system should hide backend complexity and present a clear work surface.

The long-term product is closer to a trusted operating layer for enterprise work than a simple keyword search box.

## Stack

- Python
- LangChain for LLM integrations, tools, retrievers, and agent building blocks
- LangGraph for stateful agent workflows and graph orchestration
- Deep agents for longer-running research, planning, and multi-step agent behavior
- Pydantic for typed contracts and validation at IO boundaries
- Vector search and retrieval components for enterprise knowledge search

## Documentation-First Workflow

Start here:

- `docs/README.md` for the documentation index and implementation handoff workflow
- `docs/prds/` for product requirements
- `docs/specs/` for technical architecture and typed contracts
- `docs/testing/` for unit test strategy, edge cases, and fixtures
- `docs/rules/` for engineering rules every agent must follow

Do not implement a backend feature until its PRD and technical spec are accepted. Each feature implementation must include focused unit tests, edge-case coverage, and Pydantic contracts where data crosses runtime, tool, MCP, memory, or subagent boundaries.

## Repo Rules

Rules for future agents live in two places:

- `docs/rules/` contains human-readable engineering rules for this backend.
- `../.cursor/rules/` contains Cursor rule files scoped to `ai-backend-src` and future `services/ai-backend` paths.

Core rules:

- Spec first: read `docs/README.md`, the relevant PRD, the matching technical spec, testing guidance, and rule docs before implementation.
- Pydantic first: validate runtime context, tool specs, MCP descriptors, memory scopes, subagent tasks/results, and stream events with typed contracts.
- Tests required: every feature needs focused unit tests, malformed-input tests, permission-denial tests, external-failure tests, and edge-case coverage.
- Architecture boundaries matter: keep orchestration separate from connector side effects; depend on protocols and ports, not vendor SDKs.
- Least privilege: never expose unauthorized tools, MCP servers, memories, documents, or actions to the model.
- Context discipline: do not pass full conversation history to subagents by default; use compact task summaries and return response plus execution/plan summaries.
- Safe observability: stream useful progress and trace IDs, but redact secrets and oversized payloads before emission.

## Local Setup

The virtual environment lives inside this folder at `.venv`.

```bash
source .venv/bin/activate
python -m pip install --upgrade pip
```

Install project dependencies as the backend takes shape:

```bash
python -m pip install -r requirements.txt
```

## Intended Direction

This backend will host the AI orchestration layer for an enterprise work surface: one place connected to Slack, Google Workspace, Atlassian, internal APIs, MCP servers, and enterprise knowledge. The first shipped artifacts are PRDs and specs so later implementation agents can build the runtime deliberately rather than improvising architecture.

