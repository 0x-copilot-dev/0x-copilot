"""MCP failure classes must survive the trip to the user's screen.

The defect this pins: a run asked Linear for issues, the backend MCP proxy
answered **400 in 834ms**, and the user was told

    "The Linear server isn't responding right now. This is a temporary
     connection issue. Try again in a moment..."

Every clause was false, and the advice could not succeed. The model was not
inventing it — it was paraphrasing, faithfully, what the runtime handed it: a
``connection_failed`` code, the message "The MCP server could not be reached.",
and ``retryable=True``. ``_post_rpc`` funnelled every non-2xx through one
``raise_for_status()`` into one error class, so a deterministic refusal and a
dead server were literally the same value by the time anything could describe
them.

So these tests assert on the two things that actually reach a user: the typed
class at the transport seam, and the ``retryable`` flag plus safe message the
loader hands the model. A 4xx must never be described as temporary, and must
never be marked retryable.
"""

from __future__ import annotations

import httpx
import pytest

from agent_runtime.capabilities.mcp import (
    DynamicMcpRegistry,
    McpLoadRequest,
    McpLoader,
)
from agent_runtime.capabilities.mcp.backend_provider import (
    BackendMcpClient,
    McpProxyStatusClassifier,
)
from agent_runtime.capabilities.mcp.cards import (
    McpAuthMode,
    McpLoadErrorCode,
    McpServerCard,
    McpServerHealth,
    McpTransport,
)
from agent_runtime.capabilities.mcp.client import (
    McpAmbiguousDispatchError,
    McpAuthError,
    McpConnectionError,
    McpNotFoundError,
    McpRequestRejectedError,
)
from agent_runtime.capabilities.mcp.constants import Messages
from agent_runtime.execution.contracts import AgentRuntimeContext, ModelConfig

from tests.unit.agent_runtime.mcp.helpers import DynamicMcpLoadingMixin

_SERVER_ID = "seed:linear"
_SERVER_NAME = "linear"

# Words that promise the failure will pass on its own. A 4xx message containing
# any of them is the defect, whatever else it says.
_TRANSIENCE_WORDS = (
    "temporary",
    "temporarily",
    "try again",
    "not responding",
    "could not be reached",
    "unreachable",
)


class McpFailureFixtureMixin:
    """Card, context, and client wired to a scripted HTTP status."""

    @staticmethod
    def context() -> AgentRuntimeContext:
        return AgentRuntimeContext(
            user_id="user_123",
            org_id="org_456",
            roles={"employee"},
            model_profile=ModelConfig(
                provider="openai",
                model_name="gpt-4o-mini",
                max_input_tokens=4096,
                timeout_seconds=30,
                temperature=0.0,
            ),
            trace_id="trace_failure_class",
        )

    @staticmethod
    def card() -> McpServerCard:
        return McpServerCard(
            name=_SERVER_NAME,
            server_id=_SERVER_ID,
            short_description="Linear issues and projects.",
            transport=McpTransport.HTTP,
            auth_mode=McpAuthMode.OAUTH2,
            health=McpServerHealth.HEALTHY,
            load_cost=10,
        )

    @classmethod
    def client_answering(cls, status_code: int) -> BackendMcpClient:
        """A client whose proxy hop always answers ``status_code``."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(status_code, json={"detail": "scripted"})

        return BackendMcpClient(
            backend_url="http://backend.local",
            runtime_context=cls.context(),
            card=cls.card(),
            lease="lease-token-that-is-long-enough",
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        )


class TestProxyStatusClassification(McpFailureFixtureMixin):
    """The status class decides the error class — one mapping, no collapse."""

    @pytest.mark.parametrize(
        ("status_code", "expected"),
        [
            (400, McpRequestRejectedError),
            (409, McpRequestRejectedError),
            (422, McpRequestRejectedError),
            (401, McpAuthError),
            (403, McpAuthError),
            (404, McpNotFoundError),
            (500, McpAmbiguousDispatchError),
            (503, McpAmbiguousDispatchError),
        ],
    )
    async def test_rpc_status_raises_its_own_typed_error(
        self, status_code: int, expected: type[Exception]
    ) -> None:
        client = self.client_answering(status_code)
        with pytest.raises(expected):
            await client.call_tool(tool_name="list_issues", arguments={})

    async def test_a_400_is_not_a_connection_error_at_all(self) -> None:
        # The load-bearing assertion. ``McpRequestRejectedError`` deliberately
        # does NOT subclass ``McpConnectionError``: while it did, every
        # ``except McpConnectionError`` in the codebase silently reclassified a
        # refusal as an outage, which is how the 400 became "not responding".
        client = self.client_answering(400)
        with pytest.raises(McpRequestRejectedError) as caught:
            await client.call_tool(tool_name="list_issues", arguments={})
        assert not isinstance(caught.value, McpConnectionError)
        assert caught.value.status_code == 400

    def test_success_classifies_as_no_error(self) -> None:
        assert (
            McpProxyStatusClassifier.error_for(
                200,
                rejected_message=Messages.Proxy.RPC_REJECTED,
                unavailable_message=Messages.Proxy.RPC_UNAVAILABLE,
                dispatch_ambiguous=True,
            )
            is None
        )

    def test_server_error_is_ambiguous_only_on_a_dispatch_path(self) -> None:
        # A 5xx during lease acquisition cannot have reached the connector —
        # nothing was dispatched yet — so it is a plain connection failure.
        acquisition = McpProxyStatusClassifier.error_for(
            503,
            rejected_message=Messages.Proxy.SESSION_REJECTED,
            unavailable_message=Messages.Proxy.SESSION_UNAVAILABLE,
            dispatch_ambiguous=False,
        )
        assert isinstance(acquisition, McpConnectionError)
        assert not isinstance(acquisition, McpAmbiguousDispatchError)


class TestLoaderEndToEnd(DynamicMcpLoadingMixin):
    """The whole chain: transport error in, typed load failure out.

    This is the test that would have caught the reported defect. Every layer
    below was individually defensible; what shipped was their composition —
    a 400 arriving at the model as ``connection_failed`` + ``retryable=True``.
    """

    async def _load_with_discovery_error(self, error: Exception):
        """Run a real load whose ``list_tools`` fails with ``error``."""
        client = self.FakeMcpClient(
            tools=(self.make_tool(),),
            resources=(self.make_resource(),),
            list_tools_error=error,
        )
        card = self.make_card(name=self.TestValues.Names.DRIVE_MCP).model_copy(
            update={"server_id": "server-drive"}
        )
        loader = McpLoader(
            DynamicMcpRegistry(
                providers=(
                    self.FakeMcpProvider(
                        cards=(card,),
                        clients={self.TestValues.Names.DRIVE_MCP: client},
                    ),
                )
            )
        )
        return await loader.load_server(
            McpLoadRequest(
                server_name=self.TestValues.Names.DRIVE_MCP,
                runtime_context=AgentRuntimeContext(
                    user_id=self.TestValues.Ids.USER_123,
                    org_id=self.TestValues.Ids.ORG_456,
                    roles={self.TestValues.Roles.EMPLOYEE},
                    permission_scopes={self.TestValues.Scopes.DOCS_READ},
                    model_profile=ModelConfig(
                        provider="openai",
                        model_name="gpt-4o-mini",
                        max_input_tokens=4096,
                        timeout_seconds=30,
                        temperature=0.0,
                    ),
                    trace_id="trace_failure_e2e",
                    feature_flags={self.TestValues.FeatureFlags.DYNAMIC_MCP_LOADING},
                ),
            )
        )

    async def test_a_400_is_never_reported_as_retryable(self) -> None:
        result = await self._load_with_discovery_error(
            McpRequestRejectedError(Messages.Proxy.RPC_REJECTED, status_code=400)
        )
        assert result.error is not None
        # Was: CONNECTION_FAILED / "could not be reached" / retryable=True —
        # the three values the user's "temporary connection issue" was built on.
        assert result.error.code is McpLoadErrorCode.MCP_PROTOCOL_ERROR
        assert result.error.retryable is False
        assert result.error.safe_message == Messages.Loader.REQUEST_REJECTED

    async def test_a_404_reports_a_missing_connector_not_an_outage(self) -> None:
        result = await self._load_with_discovery_error(
            McpNotFoundError(Messages.Proxy.NOT_FOUND, status_code=404)
        )
        assert result.error is not None
        assert result.error.code is McpLoadErrorCode.UNKNOWN_SERVER
        assert result.error.retryable is False

    async def test_a_real_outage_stays_retryable(self) -> None:
        # The other half of the fix: narrowing 4xx must not make genuine
        # transport failures un-retryable. A 5xx still legitimately reads as
        # temporary, because it is.
        result = await self._load_with_discovery_error(
            McpAmbiguousDispatchError(Messages.Proxy.RPC_UNAVAILABLE)
        )
        assert result.error is not None
        assert result.error.code is McpLoadErrorCode.CONNECTION_FAILED
        assert result.error.retryable is True

    async def test_auth_failure_is_neither_an_outage_nor_retryable(self) -> None:
        result = await self._load_with_discovery_error(
            McpAuthError("MCP server is not authenticated.")
        )
        assert result.error is not None
        assert result.error.code is McpLoadErrorCode.AUTH_FAILURE
        assert result.error.retryable is False


class TestLoaderReportsFailuresHonestly(McpFailureFixtureMixin):
    """What the loader hands the model is what the user ends up reading."""

    def test_rejected_message_forecloses_a_retry(self) -> None:
        message = Messages.Loader.REQUEST_REJECTED
        lowered = message.lower()
        assert "not temporary" in lowered or "will not" in lowered
        for word in ("temporary connection", "not responding", "could not be reached"):
            assert word not in lowered

    def test_not_found_message_forecloses_a_retry(self) -> None:
        lowered = Messages.Loader.SERVER_NOT_FOUND.lower()
        assert "not temporary" in lowered
        assert "could not be reached" not in lowered

    @pytest.mark.parametrize(
        "message",
        [Messages.Loader.REQUEST_REJECTED, Messages.Loader.SERVER_NOT_FOUND],
    )
    def test_no_4xx_message_promises_the_problem_will_pass(self, message: str) -> None:
        lowered = message.lower()
        # "retrying will not change it" legitimately contains "retry"; what is
        # banned is the bare instruction to wait and try again.
        assert "try again" not in lowered
        assert "not responding" not in lowered

    @pytest.mark.parametrize(
        "message",
        [Messages.Loader.REQUEST_REJECTED, Messages.Loader.SERVER_NOT_FOUND],
    )
    def test_transience_words_are_absent_from_4xx_copy(self, message: str) -> None:
        # Explicit denials ("not temporary") are the point of these strings, so
        # strip them before scanning for the words they deny.
        lowered = message.lower()
        for word in _TRANSIENCE_WORDS:
            lowered = lowered.replace(f"not {word}", "")
        offenders = [word for word in _TRANSIENCE_WORDS if word in lowered]
        assert offenders == []
