# Spec: Dynamic MCP Loading

## Purpose

Allow the agent to discover MCP server cards and load selected MCP tools/resources dynamically through typed clients and validation.

## Architecture

Future modules:

- `mcp/cards.py`: server card contracts.
- `mcp/registry.py`: list and lookup MCP server cards.
- `mcp/client.py`: protocol for connecting, listing tools, and listing resources.
- `mcp/loader.py`: validates discovered descriptors and exposes selected tools.
- `agent/middleware/dynamic_mcp_loader.py`: agent-facing loader tool/middleware.

MCP clients should be async-ready and replaceable with fakes.

## Pydantic Contracts

Required models:

- `McpServerCard`: `name`, `short_description`, `transport`, `auth_mode`, `required_scopes`, `health`, `load_cost`.
- `McpLoadRequest`: server name and runtime context.
- `McpToolDescriptor`: name, description, input schema, output shape, risk level.
- `McpResourceDescriptor`: URI, name, MIME type, description, access policy.
- `McpLoadResult`: descriptors, connection metadata, and typed warnings.

All server-provided schemas must be validated before they are shown to the agent.

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

## Edge Cases

- Health state changes between list and load.
- Server returns a resource URI with unsupported scheme.
- MCP tool has no description.
- Auth token expires mid-load.
- Server returns many tools and exceeds load budget.

