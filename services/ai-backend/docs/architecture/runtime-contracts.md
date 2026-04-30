# Runtime Contracts

## Contract Policy

Use Pydantic for every boundary where data enters, leaves, or crosses a subsystem. The model should parse and validate raw input once, then downstream code should receive typed objects.

## Core Contracts

The implemented contracts live under `src/agent_runtime/`:

- `AgentRuntimeContext`: user ID, organization ID, roles, permission scopes, connector scopes, model profile, trace ID, and feature flags.
- `RuntimeDependencies`: injected ports for tool registry, MCP registry, skill source config, memory backend factory, and subagent catalog.
- `RuntimeErrorEnvelope`: typed, retryable, correlation-aware errors safe for product surfaces.
- `ToolCard`: compact, model-visible tool summary with name, connector, scopes, risk, tags, and load cost.
- `LoadedToolSpec`: validated full tool description, argument schema, return schema, side-effect class, timeout, and permission policy.
- `McpServerCard`: compact MCP server summary with transport, auth mode, scopes, health, load cost, and optional user/org allowlists.
- `McpToolDescriptor` and `McpResourceDescriptor`: validated descriptors discovered only after explicit MCP server load.
- `SkillManifest`: parsed `SKILL.md` frontmatter for product indexing and source policy.
- `SkillSourceConfig` and `SkillSource`: configured skill roots, precedence, scope, and Deep Agents directory wiring.
- `MemoryScope`, `MemoryRoutePlan`, and `MemoryPathPolicy`: user, agent, and organization policy namespaces plus read/write authorization.
- `TokenBudgetPolicy`, `ManagedContextPayload`, and `ContextCompressionEvent`: token thresholds, offload/summarize decisions, and redacted compression telemetry.
- `SubagentDefinition`: compact model-visible subagent metadata.
- `SubagentTask`: compact objective, relevant summary, constraints, runtime context reference, allowed tools, allowed skills, and output contract.
- `SubagentResult`: response, execution summary, plan summary, artifacts, recent messages, or typed error.
- `AsyncTaskState` and `AsyncTaskLifecycleResult`: task IDs and lifecycle status stored outside message history.
- LangGraph v2 `StreamPart`: external Deep Agents stream input consumed by
  `runtime_worker`. It has `type`, `ns`, and `data` keys and is adapted before
  persistence.
- `RuntimeEventEnvelope`: API transport envelope with ordered sequence numbers, span correlation, task/subagent IDs, UI display titles, one-phrase summaries, visibility, redaction state, redacted payloads, and protocol versioning for replayable client timelines. Provider streaming chunks are represented as `model_delta` envelopes with exact text in `payload.delta`.
- `ConversationRecord`, `MessageRecord`, and `RunRecord`: API-facing persistence records for conversation shells, ordered messages, and queued/runtime execution state.
- `RuntimeRunCommand`, `RuntimeCancelCommand`, and `RuntimeApprovalResolvedCommand`: durable commands exchanged through the runtime queue port.
- `OutboxEventRecord`, `RuntimeWorkerClaim`, and `RuntimeWorkerResult`: persistence-layer queue records for claim, retry, completion, and dead-letter semantics.
- `MemoryScopeRecord`, `MemoryItemRecord`, `ContextPayloadRecord`, `CompressionEventRecord`, `CapabilitySnapshotRecord`, `AuditLogRecord`, and `CheckpointRecord`: durable metadata records for runtime memory, offloaded payloads, compression telemetry, capability snapshots, audit, and checkpoint references.

## Validation Rules

- IDs must be non-empty and normalized.
- Tool, skill, MCP, and subagent names must be stable slugs.
- Permission scopes must be explicit enums or literals.
- Token budgets must be positive integers with configured ceilings.
- Artifact paths and references must reject traversal and unsupported schemes.
- Model output that drives action must be parsed into typed contracts before use.
- Secrets and oversized payloads must be redacted or truncated before reaching stream events.
- API event payloads, event metadata, request metadata, persistence JSON payloads, and audit metadata must be redacted before storage.
- Runtime event sequence numbers are per run and must remain replayable after reconnect.
- Provider chunk streams must preserve exact chunk text in `payload.delta`; display summaries may normalize whitespace.
- Raw chain-of-thought, provider reasoning tokens, hidden scratchpads, and private prompt text must not be streamed or persisted as client-visible runtime events. Use `reasoning_summary` events with product-safe summaries instead.
- Subagent handoffs must not serialize raw conversation history by default.

## Error Strategy

Errors should be typed and user-safe:

- Runtime errors use `RuntimeErrorCode` and `AgentRuntimeError`.
- Tool load errors use `ToolLoadErrorCode`.
- MCP load errors use `McpLoadErrorCode`.
- Skill parsing errors use `SkillErrorCode`.
- Subagent lifecycle and result errors use `SubagentErrorCode`.
- Context and memory policy failures use typed runtime errors with safe public messages.

Unit tests should assert both the typed error and the safe public message.

## External Boundary Rule

Connector SDK objects, live MCP sessions, model provider objects, and persistence clients must stay behind ports. Runtime/domain contracts should contain normalized primitives and typed value objects only.
