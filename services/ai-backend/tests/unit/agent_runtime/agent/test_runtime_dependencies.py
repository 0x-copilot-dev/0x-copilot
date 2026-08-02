from __future__ import annotations

from typing import Any

import pytest
from langchain_core.messages import ToolMessage
from langchain_core.tools import BaseTool
from pydantic import ValidationError

from agent_runtime.capabilities.citation_capturing_tool import CitationHint
from agent_runtime.capabilities.conversation_ordinals import (
    ConversationOrdinalAllocator,
)
from agent_runtime.capabilities.middleware import wrap_tools_with_display
from agent_runtime.execution.contracts import RuntimeDependencies, SkillSourceConfig
from agent_runtime.settings import RuntimeSettings
from runtime_worker.dependencies import (
    DefaultRuntimeDependenciesFactory,
    WebSearchToolRegistry,
)
from tests.unit.agent_runtime.agent.helpers import MissingToolRegistryMethod
from tests.unit.fakes import (
    FakeMcpRegistry,
    FakeMemoryBackendFactory,
    FakeSubagentCatalog,
    FakeToolRegistry,
)


def test_runtime_dependencies_accept_fake_ports(
    fake_dependencies: RuntimeDependencies,
) -> None:
    assert isinstance(fake_dependencies.tool_registry, FakeToolRegistry)
    assert isinstance(fake_dependencies.mcp_registry, FakeMcpRegistry)
    assert fake_dependencies.skill_source_config.roots == ("skills",)


def test_runtime_dependencies_reject_missing_required_protocol_method() -> None:
    with pytest.raises(ValidationError):
        RuntimeDependencies(
            tool_registry=MissingToolRegistryMethod(),
            mcp_registry=FakeMcpRegistry(),
            skill_source_config=SkillSourceConfig(),
            memory_backend_factory=FakeMemoryBackendFactory(),
            subagent_catalog=FakeSubagentCatalog(),
        )


def test_default_runtime_dependencies_include_web_search_tool(
    runtime_context_admin,
) -> None:
    # The context fixture keeps the default web_search_enabled=True.
    assert runtime_context_admin.web_search_enabled is True
    tools = WebSearchToolRegistry().list_available_tools(runtime_context_admin)

    assert len(tools) == 1
    assert getattr(tools[0], "name", "") == "web_search"


class WebSearchDispatchMixin:
    """Fake ``ddgs`` backend, constants, and builders for the built-in tool."""

    QUERY = "desktop install performance"
    TOOL_CALL_ID = "call_web_search_1"
    ARTIFACT_ONLY_FIELD = "artifact-only, never model-visible"
    RAW_RESULT: dict[str, str] = {
        "body": "Result summary",
        "title": "Result title",
        "href": "https://example.test/result",
        "extra": ARTIFACT_ONLY_FIELD,
    }
    MODEL_RESULT: dict[str, str] = {
        "snippet": "Result summary",
        "title": "Result title",
        "link": "https://example.test/result",
    }
    EXPECTED_DDGS_CALL: dict[str, object] = {
        "query": QUERY,
        "region": "wt-wt",
        "safesearch": "moderate",
        "timelimit": "y",
        "max_results": 4,
        "backend": "auto",
    }

    def _install_fake_ddgs(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> list[dict[str, object]]:
        calls: list[dict[str, object]] = []
        raw_result = self.RAW_RESULT

        class FakeDDGS:
            def __enter__(self) -> "FakeDDGS":
                return self

            def __exit__(self, *_args: object) -> None:
                return None

            def text(self, query: str, **kwargs: object) -> list[dict[str, str]]:
                calls.append({"query": query, **kwargs})
                return [dict(raw_result)]

        monkeypatch.setattr("ddgs.DDGS", FakeDDGS)
        return calls

    def _web_search_tool(self, context: object) -> BaseTool:
        tools = WebSearchToolRegistry().list_available_tools(context)
        return tools[0]  # type: ignore[return-value]

    def _dispatched_web_search_tool(self, context: object) -> BaseTool:
        """Return the exact tool a worker run dispatches, built by the factory.

        Not a restatement of the production stack — the stack itself. LangChain
        reads ``response_format`` off the **outermost** tool, so a composition
        assembled by hand in the test (and, as this helper previously did,
        assembled without the outer error-policy decorator) asserts on a stack
        production never builds: every inner layer can be correct while the one
        layer that decides how the return value is unpacked is not.
        """

        return self._production_tool_registry().list_available_tools(context)[0]  # type: ignore[return-value]

    @staticmethod
    def _production_tool_registry() -> object:
        """Build the run's tool registry through the production factory."""

        settings = RuntimeSettings.load(
            environ={
                "RUNTIME_ENVIRONMENT": "production",
                "OPENAI_API_KEY": "sk-test",
            }
        )
        return DefaultRuntimeDependenciesFactory(settings)(None).tool_registry

    @staticmethod
    def _wrapper_chain(tool: BaseTool) -> tuple[BaseTool, ...]:
        """Return every layer of a wrapper chain, outermost first."""

        chain: list[BaseTool] = []
        current: object = tool
        while isinstance(current, BaseTool):
            chain.append(current)
            current = getattr(current, "inner", None)
        return tuple(chain)

    def _bind_allocator(self) -> object:
        return ConversationOrdinalAllocator.bind_for_run(
            ConversationOrdinalAllocator(
                org_id="org_web_search",
                conversation_id="conv_web_search",
                run_id="run_web_search",
            )
        )

    def _tool_call(self) -> dict[str, Any]:
        return {
            "name": WebSearchToolRegistry.Values.WEB_SEARCH_TOOL_NAME,
            "args": {"query": self.QUERY},
            "id": self.TOOL_CALL_ID,
            "type": "tool_call",
        }


class TestWebSearchDispatchSurface(WebSearchDispatchMixin):
    def test_calls_ddgs_with_the_configured_search_parameters(
        self,
        runtime_context_admin,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        calls = self._install_fake_ddgs(monkeypatch)

        self._web_search_tool(runtime_context_admin).invoke({"query": self.QUERY})

        assert calls == [self.EXPECTED_DDGS_CALL]

    def test_retry_wrapper_declares_the_inner_response_format(
        self, runtime_context_admin
    ) -> None:
        # The live defect: the wrapper defaulted to "content" while the inner
        # declared "content_and_artifact", so LangChain never unpacked the pair.
        tool = self._web_search_tool(runtime_context_admin)

        assert tool.response_format == "content_and_artifact"
        assert tool.inner.response_format == "content_and_artifact"  # type: ignore[attr-defined]

    def test_invoke_returns_only_the_model_visible_half(
        self,
        runtime_context_admin,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        self._install_fake_ddgs(monkeypatch)

        result = self._web_search_tool(runtime_context_admin).invoke(
            {"query": self.QUERY}
        )

        assert result == [self.MODEL_RESULT]

    def test_tool_call_dispatch_carries_raw_results_as_the_artifact(
        self,
        runtime_context_admin,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        self._install_fake_ddgs(monkeypatch)

        message = self._web_search_tool(runtime_context_admin).invoke(self._tool_call())

        assert isinstance(message, ToolMessage)
        assert message.artifact == [self.RAW_RESULT]
        assert self.ARTIFACT_ONLY_FIELD not in message.content

    def test_every_layer_of_the_dispatched_chain_declares_the_inner_format(
        self, runtime_context_admin
    ) -> None:
        # A per-layer assertion, because the defect lived in exactly one layer:
        # retry and citations both declared "content_and_artifact" while the
        # outermost error-policy wrapper — the one LangChain reads — defaulted
        # back to "content".
        chain = self._wrapper_chain(
            self._dispatched_web_search_tool(runtime_context_admin)
        )

        assert [type(layer).__name__ for layer in chain] == [
            "ToolErrorPolicyTool",
            "CitationCapturingTool",
            "RetryingTool",
            "StructuredTool",
        ]
        assert [layer.response_format for layer in chain] == [
            "content_and_artifact"
        ] * len(chain)

    async def test_artifact_survives_the_whole_dispatched_chain(
        self,
        runtime_context_admin,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Every wrapper in the built-in chain, driven the way the graph drives
        # it: one tool call in, one ToolMessage out. The hint has to reach the
        # model without costing the artifact — appending it to the pair would
        # make the pair a triple and BaseTool.arun refuses to unpack anything
        # but two values.
        self._install_fake_ddgs(monkeypatch)
        token = self._bind_allocator()
        try:
            tool = self._dispatched_web_search_tool(runtime_context_admin)
            message = await tool.ainvoke(self._tool_call())
        finally:
            ConversationOrdinalAllocator.unbind(token)

        assert isinstance(message, ToolMessage)
        assert message.artifact == [self.RAW_RESULT]
        assert self.ARTIFACT_ONLY_FIELD not in message.content
        assert CitationHint.NOTE_PREFIX in message.content

    async def test_artifact_survives_the_factory_display_decoration(
        self,
        runtime_context_admin,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # The graph does not dispatch the registry tool directly: the factory
        # decorates every model tool with the display schema first. That
        # decorator rebuilds a non-StructuredTool as a fresh StructuredTool
        # declaring plain "content", so the artifact only survives because the
        # decorator delegates through ``ainvoke`` and LangChain passes a
        # returned ToolMessage straight out. Pinned here so a change to either
        # half cannot quietly re-break web search.
        self._install_fake_ddgs(monkeypatch)
        token = self._bind_allocator()
        try:
            tool = wrap_tools_with_display(
                [self._dispatched_web_search_tool(runtime_context_admin)]
            )[0]
            message = await tool.ainvoke(self._tool_call())
        finally:
            ConversationOrdinalAllocator.unbind(token)

        assert isinstance(message, ToolMessage)
        assert message.artifact == [self.RAW_RESULT]
        assert self.ARTIFACT_ONLY_FIELD not in message.content

    async def test_surfaced_tool_error_still_reaches_the_model_as_content(
        self,
        runtime_context_admin,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # The trap that comes with propagating response_format: the error-policy
        # wrapper authors its own return value (the sanitized error string), and
        # a bare string cannot be unpacked as (content, artifact). If the
        # surfacing path is not rendered in the shape the wrapper now declares,
        # the error handler crashes on the very call it was handling.
        def _explode(*_args: object, **_kwargs: object) -> None:
            raise RuntimeError("ddgs exploded")

        monkeypatch.setattr(WebSearchToolRegistry, "_search", _explode)
        monkeypatch.setattr("random.uniform", lambda *_a: 0.0)
        token = self._bind_allocator()
        try:
            tool = self._dispatched_web_search_tool(runtime_context_admin)
            message = await tool.ainvoke(self._tool_call())
        finally:
            ConversationOrdinalAllocator.unbind(token)

        assert isinstance(message, ToolMessage)
        assert message.artifact is None
        assert "RuntimeError" in message.content


class FakeEnrichment:
    """Stand-in for the local extraction pipeline, scripted per test."""

    def __init__(
        self,
        *,
        result: tuple[list[dict[str, Any]], list[dict[str, Any]]] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.result = result
        self.error = error
        self.calls: list[list[dict[str, Any]]] = []

    def enrich(
        self, raw_results: list[dict[str, Any]]
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        self.calls.append(list(raw_results))
        if self.error is not None:
            raise self.error
        assert self.result is not None
        return self.result


class TestWebSearchExtractionWiring(WebSearchDispatchMixin):
    """The seam between DuckDuckGo discovery and local page extraction."""

    WINDOWED = {
        "snippet": "A window of the real page, containing the actual answer.",
        "title": "Result title",
        "link": "https://example.test/result",
    }
    ENRICHED_ARTIFACT = {
        **WebSearchDispatchMixin.RAW_RESULT,
        "extracted_text": "The whole extracted article.",
        "extraction_status": "extracted",
        "passage_origin": "window",
    }

    def test_no_workspace_root_leaves_the_tool_on_engine_snippets(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Desktop-only, expressed as the absence of a workspace directory rather
        # than a second feature flag that could drift from it.
        self._install_fake_ddgs(monkeypatch)
        monkeypatch.delenv("RUNTIME_STORE_BACKEND", raising=False)
        monkeypatch.delenv("RUNTIME_FILE_STORE_ROOT", raising=False)
        registry = WebSearchToolRegistry()

        result = registry.list_available_tools(None)[0].invoke({"query": self.QUERY})  # type: ignore[union-attr]

        assert result == [self.MODEL_RESULT]
        assert registry._resolve_enrichment() is None

    def test_enriched_content_and_artifact_reach_the_tool_message(
        self,
        runtime_context_admin,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        self._install_fake_ddgs(monkeypatch)
        enrichment = FakeEnrichment(result=([self.WINDOWED], [self.ENRICHED_ARTIFACT]))
        tool = WebSearchToolRegistry(enrichment=enrichment).list_available_tools(
            runtime_context_admin
        )[0]

        message = tool.invoke(self._tool_call())  # type: ignore[union-attr]

        assert isinstance(message, ToolMessage)
        assert enrichment.calls == [[self.RAW_RESULT]]
        assert message.artifact == [self.ENRICHED_ARTIFACT]
        assert "the actual answer" in message.content
        # The full article is the artifact's job; it must not cost prompt tokens.
        assert "The whole extracted article." not in message.content

    def test_enrichment_failure_returns_exactly_the_pre_extraction_output(
        self,
        runtime_context_admin,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # AC1: a failure of the new path is a fallback, never an error. This is
        # the one assertion that has to hold whatever else breaks.
        self._install_fake_ddgs(monkeypatch)
        enrichment = FakeEnrichment(error=RuntimeError("extraction exploded"))
        tool = WebSearchToolRegistry(enrichment=enrichment).list_available_tools(
            runtime_context_admin
        )[0]

        message = tool.invoke(self._tool_call())  # type: ignore[union-attr]

        assert isinstance(message, ToolMessage)
        assert message.artifact == [self.RAW_RESULT]
        assert self.ARTIFACT_ONLY_FIELD not in message.content
        assert self.MODEL_RESULT["snippet"] in message.content

    def test_discovery_parameters_are_unchanged_by_extraction(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls = self._install_fake_ddgs(monkeypatch)
        enrichment = FakeEnrichment(result=([self.WINDOWED], [self.ENRICHED_ARTIFACT]))

        WebSearchToolRegistry(enrichment=enrichment).list_available_tools(None)[
            0
        ].invoke({"query": self.QUERY})  # type: ignore[union-attr]

        assert calls == [self.EXPECTED_DDGS_CALL]

    def test_the_registry_is_still_free_to_construct(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The production capability guard constructs this registry on a bare
        # ``None`` context; resolving a workspace directory there would put
        # filesystem work on a path that only asks "is web search configured".
        monkeypatch.setenv("RUNTIME_STORE_BACKEND", "file")
        monkeypatch.setenv("RUNTIME_FILE_STORE_ROOT", "/tmp/copilot-store-unused")

        registry = WebSearchToolRegistry()

        assert registry._enrichment is None
        assert registry._enrichment_resolved is False


def test_web_search_registry_omits_tool_when_disabled(runtime_context_admin) -> None:
    disabled = runtime_context_admin.model_copy(update={"web_search_enabled": False})

    tools = WebSearchToolRegistry().list_available_tools(disabled)

    assert tools == ()


def test_web_search_registry_defaults_on_for_bare_context() -> None:
    # A bare object / None (older callers, the capability-mode probe) has no
    # web_search_enabled attribute and must keep the historic always-on default.
    assert len(WebSearchToolRegistry().list_available_tools(None)) == 1
    assert len(WebSearchToolRegistry().list_available_tools(object())) == 1


def test_production_capability_guard_holds_when_run_disables_web_search(
    runtime_context_admin,
) -> None:
    # Disabling web search for a single run must NOT trip the production
    # "no capability sources configured" guard — the deployment still composes
    # web search as a source (the guard probes with a default context).
    settings = RuntimeSettings.load(
        environ={
            "RUNTIME_ENVIRONMENT": "production",
            "OPENAI_API_KEY": "sk-test",
        }
    )
    disabled = runtime_context_admin.model_copy(update={"web_search_enabled": False})

    dependencies = DefaultRuntimeDependenciesFactory(settings)(disabled)

    # No exception raised; the run's own tool list is empty (web search off).
    assert dependencies.tool_registry.list_available_tools(disabled) == ()


def test_default_runtime_dependencies_allow_production_with_default_web_search_tool(
    runtime_context_admin,
) -> None:
    settings = RuntimeSettings.load(
        environ={
            "RUNTIME_ENVIRONMENT": "production",
            "OPENAI_API_KEY": "sk-test",
        }
    )

    dependencies = DefaultRuntimeDependenciesFactory(settings)(runtime_context_admin)
    tools = dependencies.tool_registry.list_available_tools(runtime_context_admin)

    assert getattr(tools[0], "name", "") == "web_search"


def test_default_runtime_dependencies_keep_web_search_when_empty_capabilities_allowed(
    runtime_context_admin,
) -> None:
    settings = RuntimeSettings.load(
        environ={
            "RUNTIME_ENVIRONMENT": "production",
            "RUNTIME_ALLOW_EMPTY_CAPABILITIES": "true",
            "OPENAI_API_KEY": "sk-test",
        }
    )

    dependencies = DefaultRuntimeDependenciesFactory(settings)(runtime_context_admin)

    tools = dependencies.tool_registry.list_available_tools(runtime_context_admin)
    assert getattr(tools[0], "name", "") == "web_search"
