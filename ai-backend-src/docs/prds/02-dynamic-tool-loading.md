# PRD: Dynamic Tool Loading

## Problem

Enterprise agents may have hundreds of possible tools. Sending every full tool description, schema, and connector instruction to the LLM wastes context and increases confusion.

## Goal

Expose compact tool cards first, then lazily load the full tool spec only when the LLM selects a tool by name.

## User Value

- Users get better tool selection because the model compares concise capability summaries.
- The system preserves context for the actual task.
- Admins can control which tools are visible by user, org, connector, and permission scope.

## Scope

- `ToolCard` index with name, short description, tags, connector, scopes, risk level, and load cost.
- Loader tool that resolves a selected card into a `LoadedToolSpec`.
- Permission filtering before cards are visible to the model.
- Tool load errors that are safe to show to the user.

## Non-Goals

- Implementing real Slack/GWS/Atlassian calls in this phase.
- Letting the LLM invent tool schemas.
- Loading tools that the user cannot call.

## Acceptance Criteria

- Tool cards are small and permission-filtered.
- Full schemas load only after explicit selection.
- Duplicate names and missing tools are deterministic errors.
- Loaded tool specs are validated through Pydantic before use.

## Edge Cases

- Duplicate tool name across connectors.
- Tool description exceeds configured size.
- User loses permission between card listing and full load.
- Connector is down when the tool loads.
- Model requests a nonexistent tool.

## Unit Testing Requirements

- Registry returns only authorized cards.
- Loader rejects unknown, duplicate, or unauthorized tools.
- Loader validates full argument schema.
- Failure modes return typed errors and safe messages.

