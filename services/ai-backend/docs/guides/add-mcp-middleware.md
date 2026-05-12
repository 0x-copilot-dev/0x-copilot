# Guide: Add MCP Middleware

MCP middleware intercepts every `call_tool` dispatch to an MCP server. Use it to add
cross-cutting behaviour: logging, citation projection, retries, auth gates, budget caps.

See also:

- [features/tool-calling.md](../features/tool-calling.md) — middleware chain overview
- [features/citations.md](../features/citations.md) — existing cite_mcp.py middleware

---

## When to add MCP middleware

Use MCP middleware when you need to:

- Inspect or transform MCP tool call arguments before they reach the server.
- Inspect or transform MCP tool results before they reach the model.
- Gate the call based on some runtime condition (permission, budget, auth state).
- Project data from the result into domain state (e.g. citations, metrics).

Do **not** add middleware for tool-specific logic — that belongs in the tool's own
description or a dedicated MCP server. Middleware is for cross-cutting concerns.

---

## Step 1 — Understand the existing chain

`agent_runtime/capabilities/mcp/middleware/call_tool.py`

The current chain (innermost to outermost):

```
McpClient.call_tool()               ← actual RPC
  ↑ CitationProjectingMcpMiddleware ← projects result → SourceRef
  ↑ ToolBudgetMiddleware            ← per-run cap
  ↑ RetryingTool                    ← transient error retry
  ↑ CallMcpTool                     ← permission + auth gate
```

New middleware slots in between these layers.

---

## Step 2 — Write the middleware

`agent_runtime/capabilities/mcp/middleware/my_middleware.py`

```python
from __future__ import annotations
from collections.abc import Callable, Awaitable, Any

class MyMcpMiddleware:
    """One-line description of what this middleware does."""

    @classmethod
    async def intercept(
        cls,
        *,
        tool_name: str,
        arguments: dict[str, Any],
        next_call: Callable[[dict[str, Any]], Awaitable[object]],
        context: "AgentRuntimeContext",
    ) -> object:
        # Before the call:
        modified_args = cls._preprocess(arguments)

        result = await next_call(modified_args)

        # After the call:
        return cls._postprocess(result)

    @classmethod
    def _preprocess(cls, args: dict) -> dict:
        # transform or validate; return modified args
        return args

    @classmethod
    def _postprocess(cls, result: object) -> object:
        # inspect or transform result
        return result
```

Rules:

- Always call `next_call(args)` — never silently drop the call unless you have an
  explicit reason and emit a safe error result.
- Never raise broad exceptions — convert to typed errors or return error dicts.
- Keep the middleware stateless. If you need per-run state, use a `ContextVar`.

---

## Step 3 — Wire into `call_tool.py`

`agent_runtime/capabilities/mcp/middleware/call_tool.py`

Find the spot in `CallMcpTool._dispatch()` where the chain is assembled and add your
middleware. Order matters — document why it sits where it does:

```python
# Inside CallMcpTool._dispatch():
result = await MyMcpMiddleware.intercept(
    tool_name=tool_name,
    arguments=arguments,
    next_call=lambda args: CitationProjectingMcpMiddleware.intercept(
        tool_name=tool_name,
        arguments=args,
        next_call=lambda a: self._client.call_tool(server_name, tool_name, a),
        context=self._context,
    ),
    context=self._context,
)
```

---

## Step 4 — Write tests

`tests/unit/agent_runtime/capabilities/mcp/middleware/test_my_middleware.py`

```python
import pytest

@pytest.mark.asyncio
async def test_middleware_passes_through():
    called_with = []
    async def fake_next(args):
        called_with.append(args)
        return {"ok": True}

    result = await MyMcpMiddleware.intercept(
        tool_name="search",
        arguments={"q": "hello"},
        next_call=fake_next,
        context=_fake_context(),
    )
    assert result == {"ok": True}
    assert called_with == [{"q": "hello"}]

@pytest.mark.asyncio
async def test_middleware_does_not_call_next_on_deny():
    # test the denial/gate path
    ...
```

---

## Step 5 — Update docs

Add a row to the middleware chain table in [features/tool-calling.md](../features/tool-calling.md).

---

## Checklist

- [ ] Middleware is stateless or uses a `ContextVar` for per-run state
- [ ] `next_call` is always awaited (not silently skipped)
- [ ] Errors are typed, not bare exceptions
- [ ] Wired into `call_tool.py` with documented position in the chain
- [ ] Unit tests cover pass-through, transformation, and denial paths
- [ ] [features/tool-calling.md](../features/tool-calling.md) updated
