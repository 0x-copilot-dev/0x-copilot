# Spec: Dynamic MCP Loading

## Purpose

Allow the agent to discover MCP server cards and load selected MCP tools/resources dynamically through typed clients and validation.

## Architecture

Implemented modules:

- `agent_runtime/capabilities/mcp/cards.py`: server card contracts.
- `agent_runtime/capabilities/mcp/registry.py`: list and lookup MCP server cards.
- `agent_runtime/capabilities/mcp/client.py`: protocol for connecting, listing tools, and listing resources.
- `agent_runtime/capabilities/mcp/loader.py`: validates discovered descriptors and exposes selected tools.
- `agent_runtime/capabilities/mcp/middleware/dynamic_loader.py`: agent-facing loader tool/middleware.
- `agent_runtime/capabilities/mcp/middleware/auth_mcp.py`: agent-facing MCP auth request tool.

MCP clients should be async-ready and replaceable with fakes.

Backend-backed remote MCP servers use JSON-RPC through `services/backend`
internal APIs. The AI backend calls the backend MCP RPC proxy for `initialize`,
`notifications/initialized`, `tools/list`, and `resources/list`; backend owns
OAuth discovery, dynamic client registration, token storage, refresh, and
outbound bearer headers to the remote MCP server.

## Pydantic Contracts

Required models:

- `McpServerCard`: `name`, `short_description`, `transport`, `auth_mode`, `required_scopes`, `health`, `load_cost`.
- `McpLoadRequest`: server name and runtime context.
- `McpToolDescriptor`: name, description, input schema, output shape, risk level.
- `McpResourceDescriptor`: URI, name, MIME type, description, access policy.
- `McpLoadResult`: descriptors, connection metadata, and typed warnings.

All server-provided schemas must be validated before they are shown to the agent.
Native MCP descriptors are mapped into these contracts at the client boundary:
`inputSchema` becomes `input_schema`, missing tool descriptions receive safe
fallback text, and resources receive read-only access policies based on server
card scopes.

## Design Rules

- Never trust MCP descriptors blindly; parse and validate at the boundary.
- Keep MCP loading separate from local tool loading but reuse naming and permission policy concepts.
- Prefer explicit collision policies over last-write-wins.
- External failures should not corrupt runtime state.

## Unit Tests

- Permission-filter server cards.
- Load healthy fake server and validate descriptors.
- Reject malformed schemas, duplicate names, and unsupported transports.
- Handle timeout, auth failure, and server unavailable errors.
- Ensure collision with local tools is deterministic.
- Verify backend-backed clients issue JSON-RPC through the internal proxy and map
  native MCP descriptors into validated runtime descriptors.

## Edge Cases

- Health state changes between list and load.
- Server returns a resource URI with unsupported scheme.
- MCP tool has no description.
- Auth token expires mid-load.
- Server returns many tools and exceeds load budget.
