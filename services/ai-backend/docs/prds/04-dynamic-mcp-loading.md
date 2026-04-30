# PRD: Dynamic MCP Loading

## Problem

MCP servers can expose tools and resources from many systems, but connecting to every server and presenting every tool up front wastes context and creates reliability risk.

## Goal

Allow the agent to discover compact MCP server cards, load a selected server dynamically, and expose only the selected server's relevant tools/resources.

## User Value

- Users can benefit from organization-specific MCP capabilities without knowing MCP exists.
- The agent avoids loading irrelevant or unavailable servers.
- Admins can audit and gate MCP access by user, organization, and scope.

## Scope

- MCP server cards with name, short description, transport, auth mode, scopes, and health state.
- Dynamic server connection and tool/resource discovery.
- Typed error handling for auth, connection, schema, and timeout failures.
- Permission filtering before server cards are visible.

## Non-Goals

- Building a full MCP hosting platform.
- Persisting secrets in specs or docs.
- Allowing MCP tool schemas to bypass validation.

## Acceptance Criteria

- MCP cards are concise and permission-filtered.
- Server load is explicit and typed.
- Discovered tools/resources become available only after validation.
- Connection failures degrade gracefully without poisoning the agent context.

## Edge Cases

- Server unavailable or slow.
- Auth expired.
- Server returns malformed tool schema.
- Tool names collide with local tools.
- Server health changes after cards were listed.

## Unit Testing Requirements

- Fake MCP server supports success, timeout, malformed schema, and auth failure.
- Registry filters by scope and health state.
- Loader normalizes and validates discovered tool/resource descriptors.
- Collision handling is deterministic.

