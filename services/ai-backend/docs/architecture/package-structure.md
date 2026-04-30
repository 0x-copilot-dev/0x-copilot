# Package Structure

## Current Package

The AI backend uses an installable `src` layout with one runtime-core package and small sibling packages for deployable surfaces and adapters:

```text
services/ai-backend/
  pyproject.toml
  requirements.txt
  src/
    agent_runtime/
      execution/
      capabilities/
        tools/
        mcp/
        skills/
      context/
        memory/
      delegation/
        subagents/
      events/
        normalization/
        contracts/
        projection/
      observability/
      persistence/
        records/
        schema/
        ports.py
      api/
        # compatibility imports plus runtime producer service/ports/events
    runtime_api/
      app.py
      http/
      schemas/
      sse/
    runtime_adapters/
      in_memory/
      postgres/
      queue/
    runtime_worker/
      handlers/
  tests/
    unit/
      agent_runtime/
      runtime_api/
      runtime_adapters/
```

## Module Ownership

- `agent_runtime/`: reusable runtime domain and orchestration core. It owns execution contracts, Deep Agents/LangGraph wiring, capability discovery, context/memory policy, subagent delegation, event normalization, observability helpers, persistence records, and abstract ports.
- `runtime_api/`: deployable FastAPI surface for conversations, runs, event replay, SSE, cancellation, approvals, safe HTTP errors, and API request/response schemas.
- `runtime_adapters/`: concrete adapters for tests and future infrastructure, including the deterministic in-memory persistence/event/queue adapter. Future Postgres repositories and queue implementations belong here.
- `runtime_worker/`: future runtime command consumer process and handlers for run, cancel, and approval-resolution commands.

Compatibility modules remain under older paths such as `agent_runtime.agent.*`, `agent_runtime.tools.*`, and `agent_runtime.api.contracts` so existing imports keep working during the migration. New code should prefer the canonical packages above.

## Dependency Direction

High-level runtime modules depend on abstract ports and Pydantic contracts. Deployable API and worker packages compose runtime services with concrete adapters. Connector implementations depend on vendor SDKs; domain contracts must not import connector SDKs.

```mermaid
flowchart TD
  RuntimeApi[runtime_api] --> AgentRuntime[agent_runtime]
  RuntimeWorker[runtime_worker] --> AgentRuntime
  RuntimeApi --> RuntimeAdapters[runtime_adapters]
  RuntimeWorker --> RuntimeAdapters
  AgentRuntime --> Contracts[Pydantic Contracts]
  AgentRuntime --> Ports[Abstract Ports]
  RuntimeAdapters --> Stores[Persistence Event Queue Adapters]
  RuntimeAdapters --> Connectors[Future Connector Implementations]
```

## Testing Implication

Unit tests mirror source ownership:

- Runtime-domain behavior stays under `tests/unit/agent_runtime/`.
- FastAPI route and schema behavior lives under `tests/unit/runtime_api/`.
- Concrete adapter behavior lives under `tests/unit/runtime_adapters/`.

Shared fakes and helpers should live in non-test helper modules, while concrete `test_*.py` files contain focused behavior tests.
