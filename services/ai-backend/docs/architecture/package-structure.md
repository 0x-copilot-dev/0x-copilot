# Package Structure

## Current Package

The AI backend uses an installable `src` layout:

```text
services/ai-backend/
  pyproject.toml
  requirements.txt
  src/
    agent_runtime/
      __init__.py
      settings.py
      agent/
        contracts.py
        errors.py
        factory.py
        graph.py
        middleware/
        ports.py
        runtime.py
        state.py
        streaming.py
      memory/
      mcp/
      observability/
      skills/
      subagents/
      tools/
        builtin/
  tests/
    unit/
      agent_runtime/
        agent/
        memory/
        mcp/
        skills/
        subagents/
        tools/
```

## Module Ownership

- `agent/`: Deep Agents factory, LangGraph graph exports, runtime wiring, stream normalization, dependency ports, and middleware composition.
- `tools/`: dynamic tool cards, full tool specs, and built-in loader tools. Tools should call connector interfaces, not raw SDKs.
- `skills/`: local Agent Skills bundles and skill discovery helpers. `SKILL.md` remains the source of truth.
- `mcp/`: MCP server cards, connection clients, tool/resource discovery, and failure classification.
- `memory/`: backend routing, scoped memory policy, token budget metrics, and summarization observability.
- `subagents/`: sync/async subagent definitions, task/result contracts, and handoff policy.
- `observability/`: redaction, trace, and correlation helpers shared by stream and compression contracts.
- Future connector implementations should live outside the core runtime contracts and satisfy the existing provider/client/runner ports.
- Future API code should stay thin and delegate to runtime services. Product API ownership still belongs in `backend-facade` unless a later architecture decision creates a narrow exception.

## Dependency Direction

High-level runtime modules depend on abstract ports and Pydantic contracts. Connector implementations depend on vendor SDKs. Domain contracts must not import connector SDKs.

```mermaid
flowchart TD
  Facade[backend-facade] --> AgentRuntime[Agent Runtime]
  AgentRuntime --> Contracts[Pydantic Contracts]
  AgentRuntime --> Ports[Abstract Ports]
  Ports --> Connectors[Future Connector Implementations]
  Ports --> McpClients[MCP Clients]
  Ports --> Stores[Stores]
  Ports --> Runners[Subagent Runners]
```

## Testing Implication

The package structure must make it possible to unit test core behavior without Slack, Google Workspace, Atlassian, LangSmith, or live MCP servers. Fakes should satisfy the same interfaces as real implementations.

Unit tests mirror source ownership under `tests/unit/agent_runtime/<subpackage>/`.
Shared fakes and helpers should live in non-test helper modules, while concrete `test_*.py` files contain at most one test class.

