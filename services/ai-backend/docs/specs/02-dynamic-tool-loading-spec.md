# Spec: Dynamic Tool Loading

## Purpose

Let the agent reason over compact tool cards and load full tool specs only when needed. This protects context budget and keeps connector details behind typed boundaries.

## Architecture

Implemented modules:

- `tools/cards.py`: `ToolCard` definitions and validation helpers.
- `tools/registry.py`: index and lookup interface.
- `tools/loader.py`: resolves cards to full specs.
- `tools/builtin/load_tool.py`: LangChain tool exposed to the agent.
- Future connector packages: concrete tool providers behind registry ports.

The registry returns cards only after permission filtering. The loader rechecks permissions before returning a full spec.

## Pydantic Contracts

Required models:

- `ToolCard`: `name`, `display_name`, `short_description`, `connector`, `tags`, `required_scopes`, `risk_level`, `load_cost`.
- `LoadedToolSpec`: `name`, `description`, `args_schema`, `return_schema`, `side_effects`, `timeout_ms`, `permission_policy`.
- `ToolLoadRequest`: selected tool name plus runtime context.
- `ToolLoadResult`: loaded spec or typed error.

Names must be stable slugs. Descriptions must have configured length limits. `args_schema` and `return_schema` must be JSON-schema-compatible Pydantic-derived schemas.

## Design Rules

- Interface segregation: listing cards and loading full specs are separate operations.
- DRY: share permission filtering logic through a policy helper, not copy-pasted conditionals.
- Side effects remain in connector adapters, never in card listing.
- Full tool descriptions must include when to use the tool and argument meanings.

## Unit Tests

- List returns only authorized cards.
- Tool load revalidates authorization.
- Unknown, duplicate, malformed, and disabled tools produce typed errors.
- Full loaded spec validates argument and return schemas.
- Risky tools require explicit policy metadata.

## Edge Cases

- Tool name collision across connectors.
- Card visible but connector unavailable at load time.
- User permission changes between list and load.
- Tool schema too large.
- Model requests a tool by display name instead of slug.

