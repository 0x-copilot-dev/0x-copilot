from __future__ import annotations

import httpx
import pytest

from agent_runtime.execution.model_invocation.contracts import (
    ModelFailureClass,
    ModelFailureSignal,
)
from agent_runtime.execution.model_invocation.lifecycle import (
    ProviderAttemptLifecycle,
    ProviderLifecycleEvent,
    ProviderLifecycleReducer,
)
from agent_runtime.execution.model_invocation.policy import ProviderFailureClassifier
from agent_runtime.execution.providers.model_failure_adapters import (
    ProviderExceptionClass,
    ProviderExceptionTypes,
    ProviderFailureAdapterRegistry,
    TypedProviderFailureAdapter,
    anthropic_failure_adapter,
    classify_without_lifecycle,
    google_genai_failure_adapter,
    openai_failure_adapter,
)


class _TypedStatusError(Exception):
    def __init__(self, status_code: object, message: str = "ignored") -> None:
        super().__init__(message)
        self.status_code = status_code


class _UnknownError(Exception):
    pass


def _pre_dispatch() -> ProviderAttemptLifecycle:
    return ProviderAttemptLifecycle()


def test_only_reviewed_exception_types_and_numeric_status_are_used() -> None:
    adapter = TypedProviderFailureAdapter(
        ProviderExceptionTypes(
            status_error=(ProviderExceptionClass.from_type(_TypedStatusError),)
        )
    )
    misleading = _UnknownError("HTTP 429 overloaded authentication failed")
    unknown = adapter.observe(misleading, _pre_dispatch())
    assert unknown.signal is ModelFailureSignal.UNKNOWN
    assert (
        ProviderFailureClassifier().classify(unknown)
        is ModelFailureClass.AMBIGUOUS_PROVIDER_STATE
    )
    assert (
        adapter.observe(_TypedStatusError("429", "rate limit"), _pre_dispatch()).signal
        is ModelFailureSignal.UNKNOWN
    )
    assert (
        adapter.observe(_TypedStatusError(429, "auth invalid"), _pre_dispatch()).signal
        is ModelFailureSignal.RATE_LIMITED
    )


@pytest.mark.parametrize(
    ("factory", "provider_module", "error_name", "status", "expected"),
    [
        (
            openai_failure_adapter,
            "openai",
            "BadRequestError",
            400,
            ModelFailureSignal.REQUEST_INVALID,
        ),
        (
            anthropic_failure_adapter,
            "anthropic",
            "AuthenticationError",
            401,
            ModelFailureSignal.AUTH_INVALID,
        ),
    ],
)
def test_openai_compatible_sdk_typed_exception_mappings(
    factory,
    provider_module: str,
    error_name: str,
    status: int,
    expected: ModelFailureSignal,
) -> None:
    module = __import__(provider_module)
    response = httpx.Response(
        status, request=httpx.Request("POST", "https://provider.test")
    )
    error = getattr(module, error_name)(
        "misleading overloaded rate limit text", response=response, body=None
    )
    assert factory().observe(error, _pre_dispatch()).signal is expected


def test_google_genai_uses_typed_numeric_code() -> None:
    from google.genai.errors import ServerError

    error = ServerError(503, {"error": {"message": "authentication failed"}})
    assert (
        google_genai_failure_adapter().observe(error, _pre_dispatch()).signal
        is ModelFailureSignal.OVERLOADED
    )


def test_unknown_provider_is_conservative_and_preserves_lifecycle_progress() -> None:
    reducer = ProviderLifecycleReducer()
    lifecycle = ProviderAttemptLifecycle()
    for event in (
        ProviderLifecycleEvent.DISPATCH_STARTED,
        ProviderLifecycleEvent.DISPATCH_ACKNOWLEDGED,
        ProviderLifecycleEvent.STREAM_STARTED,
    ):
        lifecycle = reducer.reduce(lifecycle, event)
    observation = ProviderFailureAdapterRegistry().observe(
        "unreviewed-provider", RuntimeError("429"), lifecycle
    )
    assert observation.signal is ModelFailureSignal.UNKNOWN
    assert (
        ProviderFailureClassifier().classify(observation)
        is ModelFailureClass.AMBIGUOUS_PROVIDER_STATE
    )


class TestAProvider404IsPermanentAndSaysSo:
    """A vendor 404 for a model id used to classify as "we cannot tell".

    It fell through ``_status_signal`` to ``UNKNOWN`` and then to
    ``AMBIGUOUS_PROVIDER_STATE`` — the one verdict a 404 never warrants, since
    the provider answered definitively that it will not serve the addressed
    model. Every assertion below drives a REAL SDK exception, because the whole
    contract is that classification reads the exception's own class identity and
    numeric status rather than its prose.
    """

    @staticmethod
    def _http_404(provider_module: str) -> BaseException:
        module = __import__(provider_module)
        response = httpx.Response(
            404, request=httpx.Request("POST", "https://provider.test")
        )
        return module.NotFoundError(
            # Deliberately misleading text: if anything pattern-matched a
            # string, these words would move the verdict.
            "overloaded rate limit try again later",
            response=response,
            body=None,
        )

    @pytest.mark.parametrize(
        ("factory", "provider_module"),
        [
            (openai_failure_adapter, "openai"),
            (anthropic_failure_adapter, "anthropic"),
        ],
    )
    def test_the_sdk_404_class_yields_not_found(
        self, factory, provider_module: str
    ) -> None:
        error = self._http_404(provider_module)
        assert (
            factory().observe(error, _pre_dispatch()).signal
            is ModelFailureSignal.NOT_FOUND
        )

    def test_google_genai_reaches_the_same_table_through_its_code_field(self) -> None:
        from google.genai.errors import ClientError

        error = ClientError(404, {"error": {"message": "overloaded, try again"}})
        assert (
            google_genai_failure_adapter().observe(error, _pre_dispatch()).signal
            is ModelFailureSignal.NOT_FOUND
        )

    @pytest.mark.parametrize("provider_module", ["openai", "anthropic"])
    def test_the_shipped_unattributed_path_classifies_it(
        self, provider_module: str
    ) -> None:
        """This, not ``observe(provider, ...)``, is the path production takes.

        ``FeatureModeSet.f10`` ships OFF, so the live dispatcher has no verified
        route and cannot name a provider — it calls ``observe_unattributed``. A
        test that named the provider would pass over a fix that never runs.
        """

        observation = ProviderFailureAdapterRegistry.defaults().observe_unattributed(
            self._http_404(provider_module), _pre_dispatch()
        )

        assert observation.signal is ModelFailureSignal.NOT_FOUND
        assert (
            ProviderFailureClassifier().classify(observation)
            is ModelFailureClass.MODEL_NOT_FOUND
        )

    def test_a_404_shaped_message_on_an_unreviewed_class_stays_unknown(self) -> None:
        # Pins that nothing string-matched its way to the new verdict.
        observation = ProviderFailureAdapterRegistry.defaults().observe_unattributed(
            _UnknownError("Error code: 404 - not_found_error: model: gone"),
            _pre_dispatch(),
        )
        assert observation.signal is ModelFailureSignal.UNKNOWN
        assert (
            ProviderFailureClassifier().classify(observation)
            is ModelFailureClass.AMBIGUOUS_PROVIDER_STATE
        )

    def test_a_string_404_status_is_not_a_status(self) -> None:
        adapter = TypedProviderFailureAdapter(
            ProviderExceptionTypes(
                status_error=(ProviderExceptionClass.from_type(_TypedStatusError),)
            )
        )
        assert (
            adapter.observe(_TypedStatusError("404"), _pre_dispatch()).signal
            is ModelFailureSignal.UNKNOWN
        )


class TestClassifyingWithoutALifecycleRefusesWhatItCannotSee:
    """The run boundary holds an exception and nothing else.

    ``classify_without_lifecycle`` exists for it, and its blind spot IS the
    contract: ``None`` means "not decidable from the exception alone", never
    "no failure". Returning a default-shaped guess for the lifecycle-dependent
    classes would be exactly the kind of unmeasured verdict this program bans.
    """

    def test_a_404_is_decidable_from_the_exception_alone(self) -> None:
        import anthropic

        error = anthropic.NotFoundError(
            "model: claude-3-haiku-20240307",
            response=httpx.Response(
                404, request=httpx.Request("POST", "https://provider.test")
            ),
            body=None,
        )
        assert classify_without_lifecycle(error) is ModelFailureClass.MODEL_NOT_FOUND

    def test_connectivity_returns_none_because_its_class_depends_on_progress(
        self,
    ) -> None:
        import anthropic

        error = anthropic.APIConnectionError(
            request=httpx.Request("POST", "https://provider.test")
        )
        # With a lifecycle this is PRE_DISPATCH_TRANSIENT or
        # AMBIGUOUS_PROVIDER_STATE depending on how far the attempt got. Without
        # one, the honest answer is that we do not know.
        assert classify_without_lifecycle(error) is None

    def test_an_exception_from_no_reviewed_sdk_returns_none(self) -> None:
        assert classify_without_lifecycle(ValueError("404 not found")) is None
