# Data Flow

## Request Lifecycle

Every request starts with typed context and compact capability discovery. The model-facing runtime sees only authorized capability summaries until it explicitly chooses a tool, MCP server, skill, memory path, or subagent.

```mermaid
sequenceDiagram
  participant User
  participant Env as RuntimeSettings
  participant API as FastAPI Runtime API
  participant Resolver as ModelConfigResolver
  participant Store as Persistence/Event Ports
  participant Queue as Runtime Queue
  participant Worker as Runtime Worker
  participant Factory as create_agent_runtime
  participant Runtime as Deep Agents runtime
  participant Registries as Tool/MCP/Skill/Subagent registries
  participant Memory as MemoryRoutePlan
  participant UI as Work surface UI

  User->>API: Natural-language request
  API->>Env: Load provider keys and runtime defaults
  API->>Resolver: Resolve request model selection
  API->>Store: Create conversation message and queued run
  API->>Store: Append run_queued event
  API->>Queue: Enqueue runtime command
  Worker->>Queue: Claim command
  Worker->>Store: Load conversation history and run state
  Worker->>Factory: AgentRuntimeContext + messages
  Factory->>Registries: List authorized compact cards and definitions
  Registries-->>Factory: ToolCard, McpServerCard, skill directories, SubagentDefinition
  Factory->>Memory: Create scoped memory backend
  Factory-->>Runtime: Deep Agents graph from explicit build request
  Runtime-->>Worker: LangGraph v2 StreamPart events
  Worker->>Mapper: Adapt StreamPart chunks
  Mapper->>Producer: Append model_delta, tool, subagent, and progress events
  Producer->>Store: Persist envelope and latest run sequence
  Worker->>Store: Append final_response/run_completed or typed failure
  Store-->>UI: Replayable RuntimeEventEnvelope updates over /events or /stream
```

`RuntimeSettings.load()` reads `env_example`, `.env`, and process environment.
Provider credentials stay outside request bodies and events. `ModelConfigResolver`
validates provider selection against configured keys for OpenAI, Anthropic, and
Gemini before the API persists a run.

The worker path is async-first. `RuntimeWorker` claims queued commands with lock
expiration, limits active run handling with `RUNTIME_MAX_PARALLEL_RUNS`, applies
`RUNTIME_MAX_RETRIES`, loads conversation history, builds local runtime
dependencies, and calls `astream_runtime()` for streaming-capable model profiles
rather than running the model inline inside FastAPI. The worker consumes
documented LangGraph v2 `StreamPart` dictionaries (`type`, `ns`, `data`) through
`RuntimeStreamPartAdapter`. Provider text chunks are persisted as `model_delta`
events with the exact text in `payload.delta`; tool, subagent, custom, and
progress parts become replayable runtime event envelopes. Deep Agents namespaces
are parsed explicitly: `()` is main-agent output and `tools:<id>` identifies
subagent execution. Lifecycle, cancel, approval, and stream events are appended
through `RuntimeEventProducer` so the run latest sequence cursor is updated
consistently. The same run still ends with `final_response` and `run_completed`.

## Dynamic Capability Loading

The runtime uses a two-step pattern for large or risky capabilities:

- Tools: local/test mode currently uses `EmptyToolRegistry` until a dynamic tool backend is added.
- MCP: `DynamicMcpRegistry` returns compact `McpServerCard` objects when `MCP_BACKEND_REGISTRY_URL` is configured. `McpLoader` connects to the selected server and validates discovered tool/resource descriptors.
- Skills: `VirtualSkillRegistry` can load backend-hosted skills when `SKILLS_BACKEND_REGISTRY_URL` is configured; file-system skill directories still pass through `SkillSourceRegistry`.
- Subagents: local/test mode currently uses `EmptySubagentCatalog` until a dynamic subagent catalog is added.
- Memory: `ScopedMemoryBackendFactory` creates a `MemoryRoutePlan` for user, agent, and organization policy scopes.

## Context And Memory Flow

```mermaid
sequenceDiagram
  participant Runtime as Deep Agents runtime
  participant Budget as TokenBudgetEvaluator
  participant Payload as ContextPayloadManager
  participant Summary as ContextSummarizationManager
  participant Memory as Scoped memory backend
  participant Obs as Compression events

  Runtime->>Budget: Estimate active context and tool output size
  Budget-->>Runtime: TokenBudgetSnapshot
  alt Small payload
    Runtime->>Payload: Prepare inline payload
    Payload-->>Runtime: ManagedContextPayload(strategy=inline)
  else Oversized payload with writer
    Runtime->>Payload: Offload tool output
    Payload->>Memory: Write referenced payload
    Payload-->>Runtime: ManagedContextPayload(strategy=offload, reference)
  else Summarization failure
    Runtime->>Summary: Summarize context with deterministic fake/SDK summarizer
    Summary-->>Runtime: Fallback ContextSummary preserving objective, decisions, artifacts, next steps
  end
  Payload-->>Obs: ContextCompressionEvent with redacted metadata
  Summary-->>Obs: ContextCompressionEvent with redacted metadata
```

## Example User Inputs

These examples describe where the backend is today. They assume future Slack, calendar, Jira, and document connectors will satisfy the existing tool/MCP adapter contracts. Current tests use fakes at those boundaries.

1. `Hi`
2. `Check Slack and Jira. What all do I need to do today?`
3. `Summarize my meetings from last week, what was promised, drop a Slack message to all relevant people asking for updates, and share any relevant Jira tickets.`
4. `Find the latest launch plan, summarize the risks, and show me which sources support each risk.`
5. `Research customer escalations from Q3, compare Slack discussions with Jira tickets, and give me a concise action plan.`

## Mid-Conversation And Later-Turn Examples

The runtime API persists each user turn as a message and each accepted request as a separate run. Later turns reuse the same `conversation_id` while getting their own `run_id`, event sequence, approval state, cancellation state, and replay cursor.

Example conversation:

1. `Find the latest launch plan, summarize the risks, and show sources for each risk.`
2. `Now only show the risks that do not have a named owner.`
3. `For those ownerless risks, draft a Slack update to the launch channel, but ask me before sending.`
4. `Actually cancel that draft run; I want to change the tone.`
5. `Try again, make it executive-friendly, and keep the Jira links.`

The first turn should create the conversation and first queued run. Each later turn should append a new user message to the same conversation, enqueue a new run, and let clients replay or stream that run's events independently.

## Flow 1: Simple Greeting

For a small conversational request, the runtime still builds the same typed harness, but the model should answer directly without loading full tool specs or starting subagents.

```mermaid
sequenceDiagram
  participant User
  participant Facade as backend-facade
  participant Factory as Runtime factory
  participant Runtime as Deep Agents runtime
  participant Worker as Runtime worker
  participant UI

  User->>Facade: "Hi"
  Facade->>Factory: AgentRuntimeContext
  Factory-->>Runtime: Authorized compact capabilities + memory routes
  Runtime-->>Worker: v2 messages and values StreamParts
  Worker-->>UI: model_delta events followed by final_response
  Runtime-->>Facade: Direct greeting
  Facade-->>User: Friendly response, no connector calls
```

## Flow 2: Daily Work Check Across Slack And Jira

The model first sees compact capability cards. It can choose Slack and Jira search tools or MCP servers, then the loaders validate full schemas and permissions before any external call happens.

```mermaid
sequenceDiagram
  participant User
  participant Runtime
  participant Tools as Future DynamicToolRegistry
  participant Loader as ToolLoader
  participant Slack as Future Slack adapter
  participant Jira as Future Jira adapter
  participant Worker as Runtime worker

  User->>Runtime: "Check Slack and Jira. What all do I need to do today?"
  Runtime->>Tools: List authorized ToolCard summaries
  Tools-->>Runtime: slack_search, jira_search summaries
  Runtime->>Loader: Load slack_search
  Loader-->>Runtime: LoadedToolSpec after permission re-check
  Runtime->>Slack: Search assigned mentions and requests
  Runtime->>Loader: Load jira_search
  Loader-->>Runtime: LoadedToolSpec after permission re-check
  Runtime->>Jira: Search assigned tickets and due work
  Runtime-->>Worker: v2 messages, updates, and custom StreamParts
  Worker-->>Runtime: Redacted runtime event envelopes
  Runtime-->>User: Prioritized todo list with sources
```

## Flow 3: Meeting Summary Plus Follow-Up Messages

This is a multi-step workflow. Read actions can run through search/calendar tools; write actions, such as Slack messages, require a loaded write-capable tool with explicit policy and confirmation before connector-side effects.

```mermaid
sequenceDiagram
  participant User
  participant Runtime
  participant Skills as SkillSourceRegistry
  participant ToolLoader
  participant Calendar as Future calendar adapter
  participant Slack as Future Slack adapter
  participant Jira as Future Jira adapter
  participant Memory as ContextPayloadManager
  participant UI

  User->>Runtime: "Summarize my meetings from last week..."
  Runtime->>Skills: Provide relevant skill directories to Deep Agents
  Runtime->>ToolLoader: Load calendar_search
  ToolLoader-->>Runtime: Validated read spec
  Runtime->>Calendar: Fetch last week's meetings and notes
  Runtime->>ToolLoader: Load jira_search
  ToolLoader-->>Runtime: Validated read spec
  Runtime->>Jira: Find relevant tickets
  Runtime->>Memory: Offload or summarize oversized notes
  Runtime->>ToolLoader: Load slack_send_message
  ToolLoader-->>Runtime: Validated write spec requiring confirmation
  Runtime-->>UI: RuntimeEventEnvelope requesting user-visible confirmation
  UI-->>Runtime: User confirms send
  Runtime->>Slack: Send update requests to relevant people
  Runtime-->>User: Meeting promises, Jira links, sent-message summary
```

## Flow 4: Source-Backed Launch Risk Summary

For source-backed answers, the runtime can combine enterprise document search with MCP-discovered resources and stream traceable progress as it works.

```mermaid
sequenceDiagram
  participant User
  participant Runtime
  participant Tools as Tool registry
  participant MCP as MCP registry
  participant McpLoader
  participant Docs as Future document adapter
  participant DriveMcp as Future Drive MCP server
  participant Worker

  User->>Runtime: "Find the latest launch plan and risks."
  Runtime->>Tools: List authorized document search cards
  Tools-->>Runtime: doc_search summary
  Runtime->>MCP: List authorized healthy MCP cards
  MCP-->>Runtime: drive_mcp summary
  Runtime->>McpLoader: Load drive_mcp
  McpLoader-->>Runtime: Validated MCP tools/resources
  Runtime->>Docs: Search launch plans and source snippets
  Runtime->>DriveMcp: Discover linked resources
  Runtime-->>Mapper: Tool calls, results, observations
  Mapper-->>Runtime: Runtime API events with trace_id and redacted payloads
  Runtime-->>User: Risk summary with source references
```

## Flow 5: Delegated Escalation Research

Long, research-heavy requests can be delegated to a subagent. The supervisor passes a compact handoff and keeps async task IDs outside message history.

```mermaid
sequenceDiagram
  participant User
  participant Supervisor as Supervisor runtime
  participant Catalog as Future DynamicSubagentCatalog
  participant Handoff as SubagentHandoffBuilder
  participant Lifecycle as AsyncSubagentLifecycle
  participant Runner as SubagentRunner
  participant Researcher as Research subagent
  participant Worker as Runtime worker

  User->>Supervisor: "Research customer escalations from Q3..."
  Supervisor->>Catalog: List authorized SubagentDefinition summaries
  Catalog-->>Supervisor: researcher
  Supervisor->>Handoff: Build SubagentTask
  Handoff-->>Supervisor: Objective, relevant summary, constraints, allowed tools/skills
  Supervisor->>Lifecycle: Start async task
  Lifecycle->>Runner: Start researcher with compact task
  Runner->>Researcher: Execute delegated research
  Lifecycle-->>Supervisor: AsyncTaskState(task_id, status=running)
  Supervisor-->>Worker: Subagent progress StreamPart with task namespace
  Researcher-->>Runner: SubagentResult(response, execution_summary, plan_summary)
  Supervisor->>Lifecycle: Check task
  Lifecycle-->>Supervisor: Succeeded state + SubagentResult
  Supervisor-->>User: Concise action plan and what the subagent did
```

## Failure And Safety Flow

All capability failures return typed, user-safe errors rather than raw adapter exceptions.

```mermaid
sequenceDiagram
  participant Runtime
  participant Loader
  participant Adapter as Future external adapter
  participant Redactor
  participant UI

  Runtime->>Loader: Load selected capability
  Loader->>Adapter: Connect or fetch schema
  Adapter--xLoader: Auth failure, timeout, malformed descriptor, or connector down
  Loader-->>Runtime: Typed load result with safe_message and correlation_id
  Runtime-->>Worker: Safe error event data
  Worker-->>UI: RuntimeEventEnvelope(type=error, redacted payload)
```

