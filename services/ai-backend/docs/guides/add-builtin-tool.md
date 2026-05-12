# Guide: Add a Built-in Tool

Built-in tools are Python callables compiled into `agent_runtime/`. They are always
available to the model (subject to permission policy) without requiring an MCP server.

See also:

- [features/tool-calling.md](../features/tool-calling.md) — tool registry + middleware chain
- [guides/add-event-type.md](add-event-type.md) — if your tool emits a new event type

---

## When to add a built-in tool

Use a built-in tool when:

- The operation does not require an external MCP server.
- The tool needs direct access to domain state (persistence ports, event producer,
  the current `AgentRuntimeContext`).
- The tool must be available in every workspace without installation.

Use an MCP server tool instead when the operation is integration-specific (e.g.
Linear, Notion, Gmail).

---

## Step 1 — Create the tool file

`agent_runtime/capabilities/tools/builtin/my_tool.py`

```python
from __future__ import annotations
from dataclasses import dataclass
from agent_runtime.execution.contracts import AgentRuntimeContext, RuntimeContract
from pydantic import Field

class MyToolInput(RuntimeContract):
    query: str = Field(min_length=1, max_length=512)

@dataclass(frozen=True)
class MyTool:
    runtime_context: AgentRuntimeContext
    name: str = "my_tool"
    description: str = "One-sentence description for the model."

    async def ainvoke(self, raw_input: MyToolInput | dict) -> dict:
        if isinstance(raw_input, dict):
            raw_input = MyToolInput.model_validate(raw_input)
        # ... implementation ...
        return {"result": "..."}

    async def __call__(self, raw_input) -> dict:
        return await self.ainvoke(raw_input)
```

Rules:

- Use `RuntimeContract` (Pydantic) for the input type — never accept raw `dict`.
- Validate at the boundary: `model_validate(raw_input)` on the first line if the
  input isn't already the typed model.
- Return a plain `dict` — the stream adapter serialises it to JSON.
- The `name` field must match what you register in the registry (Step 2).
- `description` is injected into the model's system prompt verbatim — keep it concise.

---

## Step 2 — Register in the tool registry

`agent_runtime/capabilities/tools/registry.py`

Add a registration entry in `DynamicToolRegistry.load_builtin_tools()`:

```python
from agent_runtime.capabilities.tools.builtin.my_tool import MyTool, MyToolInput

registry.register(
    card=ToolCard(
        name="my_tool",
        display_template=ToolDisplayTemplate(
            icon="wrench",          # icon key for frontend
            label="My Tool",
            subtitle="Does the thing",
        ),
        input_schema=MyToolInput.model_json_schema(),
        requires_approval=False,    # set True if tool needs human confirmation
    ),
    factory=lambda ctx: MyTool(runtime_context=ctx),
)
```

---

## Step 3 — Add permission policy (if needed)

`agent_runtime/capabilities/tools/permissions.py`

If the tool should be restricted (admin-only, specific scope, feature flag):

```python
class ToolPermissionChecker:
    def is_card_authorized(self, card: ToolCard, context: AgentRuntimeContext) -> bool:
        if card.name == "my_tool":
            return context.user_policies.has_scope("my_tool:use")
        ...
```

---

## Step 4 — Write tests

`tests/unit/agent_runtime/capabilities/test_my_tool.py`

```python
import pytest
from agent_runtime.capabilities.tools.builtin.my_tool import MyTool, MyToolInput

@pytest.mark.asyncio
async def test_my_tool_basic():
    tool = MyTool(runtime_context=_fake_context())
    result = await tool.ainvoke(MyToolInput(query="hello"))
    assert "result" in result

@pytest.mark.asyncio
async def test_my_tool_invalid_input():
    tool = MyTool(runtime_context=_fake_context())
    with pytest.raises(ValidationError):
        await tool.ainvoke({})  # empty dict should fail validation
```

Test the tool directly (not through the full registry). Mock only external calls.

---

## Step 5 — Update docs

Add a row to the built-in tools table in [features/tool-calling.md](../features/tool-calling.md).

---

## Checklist

- [ ] `MyToolInput` extends `RuntimeContract`; all fields have `Field()` constraints
- [ ] `ainvoke` validates raw input before using it
- [ ] Tool is registered in `DynamicToolRegistry.load_builtin_tools()`
- [ ] Permission policy updated if the tool is not universally accessible
- [ ] Unit tests cover the happy path and invalid input
- [ ] [features/tool-calling.md](../features/tool-calling.md) updated
