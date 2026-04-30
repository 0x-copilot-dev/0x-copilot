# Runtime Contracts

## Contract Policy

Use Pydantic for every boundary where data enters, leaves, or crosses a subsystem. The model should parse and validate raw input once, then downstream code should receive typed objects.

## Core Contracts

Future specs should define these contracts in detail:

- `AgentRuntimeContext`: user, organization, permissions, connector availability, model config, tracing metadata, and feature flags.
- `ToolCard`: compact name, short description, tags, connector, permission scopes, risk level, and load cost.
- `LoadedToolSpec`: full tool name, description, argument schema, return schema, safety policy, and callable binding.
- `McpServerCard`: server name, description, transport, auth mode, available scopes, and health state.
- `SkillManifest`: parsed `SKILL.md` frontmatter for product indexing only; Deep Agents remains responsible for skill loading.
- `MemoryScope`: user, agent, organization, and policy namespace choices.
- `SubagentTask`: compact objective, summary, constraints, permitted tools, permitted skills, output contract, and deadline.
- `SubagentResult`: response, execution summary, plan summary, artifacts, optional recent messages, and error state.
- `StreamEvent`: normalized event source, event type, subagent ID, payload, timestamps, and trace IDs.

## Validation Rules

- IDs must be non-empty and normalized.
- Tool, skill, MCP, and subagent names must be stable slugs.
- Permission scopes must be explicit enums or literals.
- Token budgets must be positive integers with configured ceilings.
- Artifact paths and references must reject traversal and unsupported schemes.
- Model output that drives action must be parsed into typed contracts before use.

## Error Strategy

Errors should be typed and user-safe:

- `ValidationError`: malformed input or model output.
- `PermissionDeniedError`: missing user, organization, or connector permission.
- `CapabilityNotFoundError`: unknown tool, skill, MCP server, or subagent.
- `CapabilityLoadError`: discovered capability failed to load.
- `ExternalServiceError`: connector or MCP failure.
- `ContextBudgetExceededError`: context could not be compressed safely.

Unit tests should assert both the typed error and the safe public message.

