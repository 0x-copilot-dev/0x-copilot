"""Assemble the fixed P0 middleware stack around one tool (migration P2-5).

Composed for real by the P2-8 registration flip
(:mod:`agent_runtime.capabilities.mcp.per_tool_registration`), behind a flag
defaulting OFF.

Three responsibilities, deliberately split:

* :class:`ToolSchemaIdentity` — the **single** definition of what "a wrapper is
  schema-identical to the tool it wraps" means. Every stage in this package
  builds its wrapper from :meth:`ToolSchemaIdentity.fields_of`, so the
  propagation rule is written once instead of five times, and
  :meth:`ToolSchemaIdentity.assert_preserved` is the composer's check that a
  stage actually honoured the P0 ``ToolMiddleware`` contract. P2-4 shipped a
  second, parallel propagation list inside ``policy_tool``; P2-8 unified them
  here, which is also what put ``extras`` into the enforced set (the POLICY
  stage propagated it, this rule did not, so the two disagreed about whether a
  dropped ``extras`` was a violation).
* :class:`ToolResultShape` — the matching rule for the *return* side: how a
  wrapper renders a payload it produced itself (a refusal, a normalized error)
  in whatever shape its own ``response_format`` promised.
* :class:`ToolMiddlewareComposer` — wraps a pair in the **fixed**
  :data:`~agent_runtime.capabilities.policy.contracts.MIDDLEWARE_ORDER`.

The order is asserted, never inferred. ``MIDDLEWARE_ORDER`` is written
outermost-first (POLICY sees the call before anyone; CITATIONS sits closest to
the inner tool), so composition iterates it **in reverse** — innermost stage
wrapped first, outermost stage wrapped last. A stack in the wrong order is not a
cosmetic problem: with EXEC_POLICY inside POLICY the retry budget would be spent
before the PDP ever saw the call, and with POLICY anywhere but outermost a
denied call could reach the connector. So a stack that is reordered, short, long,
or duplicated is refused with a typed
:class:`MiddlewareCompositionError` rather than silently composed.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Final

from langchain_core.tools import BaseTool

from agent_runtime.capabilities.policy.contracts import (
    MIDDLEWARE_ORDER,
    CapabilityDescriptor,
    MiddlewareStage,
    ToolMiddleware,
)


class MiddlewareCompositionError(ValueError):
    """A middleware stack is not the fixed pipeline, or a stage broke identity.

    Carries a safe message: stage names and tool names are configuration
    identity, not user data or secrets, and the message never contains a
    credential, an endpoint, or a traceback. It is raised at composition time —
    a wiring defect surfaces before any tool can be registered, never mid-call.
    """


class ToolSchemaIdentity:
    """What a middleware wrapper must reproduce from the tool it wraps.

    Two field sets, for two different reasons:

    * :attr:`MODEL_SURFACE` is what the model sees and what the P0
      ``ToolMiddleware`` contract calls schema-identical — a wrapped tool
      registers with the Deep Agent exactly like its inner. This is the set
      :meth:`assert_preserved` enforces. ``extras`` belongs to it: it is
      provider-facing tool configuration (``@tool(extras={"cache_control": …})``
      in ``langchain_core.tools.base``), so a stage that silently dropped it
      would change how the tool is sent to the model while every name /
      description / schema check still passed.
    * :attr:`DISPATCH_SURFACE` is what LangChain reads on the *outermost* tool
      when it turns a return value into a ``ToolMessage``. It is propagated but
      not enforced, because a stage owned by another increment may legitimately
      predate this rule. ``response_format`` is the load-bearing one:
      ``langchain-mcp-adapters`` builds every MCP tool with
      ``response_format="content_and_artifact"`` (``tools.py``), so its
      ``_arun`` returns a ``(content, artifact)`` tuple. A wrapper that
      defaulted back to ``"content"`` would hand the model the repr of the whole
      tuple — a silent, per-connector content corruption that no schema check
      would catch.

    ``args_schema`` is propagated **by identity, never rebuilt**. That is not a
    style preference: ``langchain-mcp-adapters`` passes the server's raw
    ``inputSchema`` (a JSON-Schema ``dict``, not a ``BaseModel``) as
    ``args_schema``, so any stage that tried to "extend" it with a synthesised
    pydantic model would replace every argument the connector declares with an
    empty object — a tool the model can see but can no longer call correctly.
    """

    #: The model-visible surface — propagated AND enforced.
    MODEL_SURFACE: tuple[str, ...] = ("name", "description", "args_schema", "extras")
    #: The dispatch-shaping surface — propagated.
    DISPATCH_SURFACE: tuple[str, ...] = (
        "response_format",
        "return_direct",
        "metadata",
        "tags",
    )

    @classmethod
    def fields_of(cls, tool: BaseTool) -> dict[str, Any]:
        """Return the constructor kwargs that make a wrapper identical to ``tool``.

        ``metadata`` / ``tags`` / ``extras`` are copied rather than shared: a
        wrapper and its inner must not be able to mutate each other's
        annotations, and the MCP annotations the source ingested ride in
        ``metadata``. ``args_schema`` is deliberately shared — see the class
        docstring; a copy would also break the "the composed tool presents the
        raw tool's own schema object" property the stack is asserted on.
        """

        fields: dict[str, Any] = {
            name: getattr(tool, name) for name in cls.MODEL_SURFACE
        }
        fields["extras"] = dict(tool.extras) if tool.extras is not None else None
        fields["response_format"] = tool.response_format
        fields["return_direct"] = tool.return_direct
        fields["metadata"] = dict(tool.metadata) if tool.metadata is not None else None
        fields["tags"] = list(tool.tags) if tool.tags is not None else None
        return fields

    @classmethod
    def assert_preserved(
        cls,
        *,
        inner: BaseTool,
        wrapped: BaseTool,
        stage: MiddlewareStage,
    ) -> None:
        """Raise when ``wrapped`` does not present ``inner``'s model surface."""

        for field in cls.MODEL_SURFACE:
            if getattr(wrapped, field) != getattr(inner, field):
                raise MiddlewareCompositionError(
                    f"middleware stage {stage.value!r} changed {field!r} of tool "
                    f"{inner.name!r}; a wrapped tool must stay schema-identical"
                )


class ToolResultShape:
    """Render a wrapper-authored payload in the return shape it declared.

    The mirror image of :class:`ToolSchemaIdentity`: identity governs what goes
    *in*, this governs what comes *out*. ``BaseTool.arun`` unpacks a two-tuple
    when ``response_format`` is ``content_and_artifact`` and raises
    ``ValueError`` otherwise, and every MCP tool ``langchain-mcp-adapters``
    builds declares exactly that format. So a stage that returns a value of its
    own — a POLICY refusal, an ERROR_MAP result — has to match the format its
    wrapper propagated, or the refusal crashes the run it was refusing to run.

    The artifact half is ``None`` by construction: a call that never dispatched
    produced no raw tool output, and inventing one would put a fabricated
    artifact on the ``ToolMessage``.
    """

    CONTENT_AND_ARTIFACT: Final[str] = "content_and_artifact"

    @classmethod
    def render(cls, payload: Any, *, response_format: str) -> Any:
        """Return ``payload``, tupled with a ``None`` artifact when required."""

        if response_format == cls.CONTENT_AND_ARTIFACT:
            return (payload, None)
        return payload


class ToolMiddlewareComposer:
    """Wrap one ``(tool, descriptor)`` pair in the fixed middleware order."""

    @classmethod
    def compose(
        cls,
        pair: tuple[BaseTool, CapabilityDescriptor],
        stack: Sequence[ToolMiddleware],
    ) -> BaseTool:
        """Return the tool wrapped by every stage, POLICY outermost.

        ``stack`` must be exactly :data:`MIDDLEWARE_ORDER`, outermost-first.
        Anything else — reordered, missing a stage, carrying a duplicate, or
        carrying an extra — raises :class:`MiddlewareCompositionError` before a
        single wrapper is built.
        """

        cls._assert_order(stack)
        tool, descriptor = pair
        # MIDDLEWARE_ORDER is outermost-first, so build inwards-out: the last
        # entry (CITATIONS) wraps the raw tool, the first (POLICY) wraps
        # everything and is what the model ultimately calls.
        for middleware in reversed(tuple(stack)):
            wrapped = middleware.wrap(tool, descriptor)
            ToolSchemaIdentity.assert_preserved(
                inner=tool, wrapped=wrapped, stage=middleware.stage
            )
            tool = wrapped
        return tool

    @classmethod
    def _assert_order(cls, stack: Sequence[ToolMiddleware]) -> None:
        """Refuse any stack whose stages are not exactly ``MIDDLEWARE_ORDER``."""

        stages = tuple(middleware.stage for middleware in stack)
        if stages == MIDDLEWARE_ORDER:
            return
        expected = ", ".join(stage.value for stage in MIDDLEWARE_ORDER)
        received = ", ".join(stage.value for stage in stages) or "<empty>"
        raise MiddlewareCompositionError(
            f"middleware stack must be exactly ({expected}); received ({received})"
        )


__all__ = [
    "MiddlewareCompositionError",
    "ToolMiddlewareComposer",
    "ToolResultShape",
    "ToolSchemaIdentity",
]
