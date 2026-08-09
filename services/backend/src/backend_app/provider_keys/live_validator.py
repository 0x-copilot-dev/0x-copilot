"""Live provider-key validation (BYOK PR-B).

Replaces the "format check == validated" story with a REAL check: call
the provider's cheapest AUTHENTICATED endpoint using the submitted key.
Any 2xx proves the key works, and — where that endpoint is the model
listing — doubles as discovery of the model ids the key can actually
reach (the add-key wizard consumes those in a later PR).

**Any 2xx, not 200.** Virtuals answers a successful completion with ``201``.
A 200-only check called a valid key "couldn't check" and stored it as
``skipped_unreachable``, so the probe validated nothing while looking
healthy — the failure mode this module exists to prevent. It was invisible
to the whole unit suite, which had assumed the OpenAI convention in every
mock, and surfaced only when a real key was run against the real gateway.

Probe endpoints:

* ``openai``      ``GET https://api.openai.com/v1/models`` (Bearer)
* ``anthropic``   ``GET https://api.anthropic.com/v1/models?limit=1000``
                  (``x-api-key`` + ``anthropic-version``)
* ``google``      ``GET https://generativelanguage.googleapis.com/v1beta/models``
                  (``x-goog-api-key`` header)
* ``openrouter``  ``GET https://openrouter.ai/api/v1/key`` (Bearer).
                  OpenRouter's ``/models`` listing is PUBLIC — it cannot
                  validate anything — so we probe the authenticated
                  ``/key`` endpoint instead; it returns usage metadata,
                  never model ids (``model_ids`` stays empty).
* ``virtuals``    ``POST https://compute.virtuals.io/v1/chat/completions``
                  (Bearer), one token. Virtuals has the same public-listing
                  problem as OpenRouter — ``GET /v1/models`` answers **200 with
                  no credential**, so probing it would report "passed" for a
                  typo — but unlike OpenRouter it exposes no authenticated
                  metadata endpoint (``/key``, ``/credits``, ``/me`` are all
                  404). The cheapest authenticated call it *does* answer is a
                  completion, so that is the probe — and it returns **201**.

Security invariants (mirrors ``service.py``):

* The key travels ONLY in request headers — never in a URL. Google's
  documented ``x-goog-api-key`` header is used instead of the ``?key=``
  query form precisely so the key can never leak through httpx's
  request log line, an exception message embedding the URL, or a proxy
  access log.
* No code path logs, raises, or returns key material. Transport and
  parse failures collapse into typed outcomes; upstream exception text
  (which may embed URLs or response fragments) is never propagated.
* ``validate`` NEVER raises — routes always receive a typed result.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from enum import StrEnum

import httpx
from pydantic import BaseModel, ConfigDict

from backend_app.provider_keys.ssrf_guard import (
    SsrfBlockReason,
    SsrfGuard,
    SsrfValidationError,
)
from backend_app.provider_keys.store import ProviderName


class LiveCheckStatus(StrEnum):
    """Tri-state verdict of one live probe."""

    VALID = "valid"
    INVALID_KEY = "invalid_key"
    PROVIDER_UNREACHABLE = "provider_unreachable"


class ProviderKeyLiveCheckResult(BaseModel):
    """Typed outcome of :meth:`ProviderKeyLiveValidator.validate`.

    ``model_ids`` is populated only for ``VALID`` outcomes where the
    probe endpoint was a model listing; it may legitimately be empty
    (e.g. openrouter, whose authenticated probe returns usage metadata).
    Never carries key material.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    status: LiveCheckStatus
    model_ids: tuple[str, ...] = ()


class ProviderKeyLiveValidator:
    """Async live-probe client, one method per concern, no module-level
    helpers. Construction is cheap; a fresh ``httpx.AsyncClient`` is
    created per probe (or taken from ``client_factory`` — tests inject
    ``lambda: httpx.AsyncClient(transport=httpx.MockTransport(h))``).
    """

    _DEFAULT_TIMEOUT_SECONDS = 6.0
    _ANTHROPIC_VERSION = "2023-06-01"
    # Query strings below are static pagination hints — NEVER key
    # material. Keys ride exclusively in headers.
    _ENDPOINTS: Mapping[ProviderName, str] = {
        ProviderName.OPENAI: "https://api.openai.com/v1/models",
        ProviderName.ANTHROPIC: "https://api.anthropic.com/v1/models?limit=1000",
        ProviderName.GOOGLE: (
            "https://generativelanguage.googleapis.com/v1beta/models?pageSize=1000"
        ),
        ProviderName.OPENROUTER: "https://openrouter.ai/api/v1/key",
        ProviderName.VIRTUALS: "https://compute.virtuals.io/v1/chat/completions",
    }
    #: The model the Virtuals probe names. Only the AUTH verdict is read from
    #: the response, so which model this is does not matter — but it must exist,
    #: and Virtuals may retire it. That is why :meth:`_outcome_for` maps a
    #: 400/404 to "couldn't check" rather than to a failure: a stale probe model
    #: must never be reported to a user as a bad key.
    _VIRTUALS_PROBE_MODEL = "z-ai-glm-4-7-flash"
    #: One token of the cheapest model on the gateway. A completion is the only
    #: authenticated call Virtuals answers, so the probe cannot be free.
    _VIRTUALS_PROBE_MAX_TOKENS = 1

    def __init__(
        self,
        *,
        client_factory: Callable[[], httpx.AsyncClient] | None = None,
        timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
        ssrf_guard: SsrfGuard | None = None,
    ) -> None:
        self._client_factory = client_factory
        self._timeout_seconds = timeout_seconds
        # Applied to the custom ``openai_compatible`` slug's user-supplied
        # base_url right before the probe fetch (defense-in-depth — routes also
        # guard, but validate() is the actual outbound-fetch site). ``None``
        # blocks every custom probe (fails closed) so a mis-wired deployment
        # never fetches an unguarded URL.
        self._ssrf_guard = ssrf_guard

    async def validate(
        self,
        *,
        provider: ProviderName,
        api_key: str,
        base_url: str | None = None,
    ) -> ProviderKeyLiveCheckResult:
        """One authenticated probe → typed outcome. Never raises.

        ``base_url`` is required for the ``openai_compatible`` custom provider
        (the probe target is ``{base_url}/models``) and ignored for the four
        native providers, whose endpoints are fixed in ``_ENDPOINTS``.
        """

        try:
            url = self._probe_url(provider=provider, base_url=base_url)
        except SsrfValidationError:
            # Blocked/missing custom base_url — never fetch. Reported as
            # "couldn't check" (never a false "valid"), matching the tri-state.
            return ProviderKeyLiveCheckResult(
                status=LiveCheckStatus.PROVIDER_UNREACHABLE
            )
        headers = self._auth_headers(provider=provider, api_key=api_key)
        body = self._probe_body(provider=provider)
        client = (
            self._client_factory()
            if self._client_factory is not None
            else httpx.AsyncClient(timeout=self._timeout_seconds)
        )
        try:
            async with client as session:
                # A provider with a probe BODY is checked by POST; the rest are
                # GETs against a metadata endpoint. The key stays in headers on
                # both paths — it is never placed in a URL or a body.
                response = (
                    await session.get(url, headers=headers)
                    if body is None
                    else await session.post(url, headers=headers, json=body)
                )
        except Exception:
            # Timeouts, DNS/connect failures, TLS errors, transport
            # bugs. The exception text may embed the URL or request
            # detail — deliberately discarded, never logged or re-raised.
            return ProviderKeyLiveCheckResult(
                status=LiveCheckStatus.PROVIDER_UNREACHABLE
            )
        return self._outcome_for(provider=provider, response=response)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _probe_url(self, *, provider: ProviderName, base_url: str | None) -> str:
        """Resolve the probe URL, applying the SSRF guard for the custom slug.

        Raises :class:`SsrfValidationError` when the custom endpoint is missing
        its base_url, has no guard wired, or the guard rejects it — so the
        caller fails closed to ``PROVIDER_UNREACHABLE`` without any fetch.
        """

        if provider is not ProviderName.OPENAI_COMPATIBLE:
            return self._ENDPOINTS[provider]
        if not base_url or self._ssrf_guard is None:
            raise SsrfValidationError(SsrfBlockReason.MISSING_HOST)
        self._ssrf_guard.check(base_url)
        # ``{base_url}/models`` is the OpenAI-compatible listing endpoint; the
        # guard validated the host so appending a fixed path can't re-point it.
        return f"{base_url.rstrip('/')}/models"

    def _probe_body(self, *, provider: ProviderName) -> dict[str, object] | None:
        """The POST body for providers probed by completion, else ``None``.

        Only Virtuals needs one today. The body carries no user content — a
        single space and a one-token cap — because nothing about the completion
        is read except the HTTP status.
        """

        if provider is not ProviderName.VIRTUALS:
            return None
        return {
            "model": self._VIRTUALS_PROBE_MODEL,
            "messages": [{"role": "user", "content": " "}],
            "max_tokens": self._VIRTUALS_PROBE_MAX_TOKENS,
        }

    def _auth_headers(self, *, provider: ProviderName, api_key: str) -> dict[str, str]:
        if provider is ProviderName.ANTHROPIC:
            return {
                "x-api-key": api_key,
                "anthropic-version": self._ANTHROPIC_VERSION,
            }
        if provider is ProviderName.GOOGLE:
            return {"x-goog-api-key": api_key}
        return {"Authorization": f"Bearer {api_key}"}

    def _outcome_for(
        self, *, provider: ProviderName, response: httpx.Response
    ) -> ProviderKeyLiveCheckResult:
        code = response.status_code
        # ANY 2xx is proof the credential was accepted, not 200 specifically.
        # Virtuals answers a successful completion with **201**, so a
        # 200-only check reported a perfectly good key as "couldn't check" and
        # stored it with `live_check: skipped_unreachable` — the probe silently
        # validating nothing. Found only by running the real key against the
        # real gateway; every mock in the suite had assumed the OpenAI 200.
        if 200 <= code < 300:
            return ProviderKeyLiveCheckResult(
                status=LiveCheckStatus.VALID,
                model_ids=self._model_ids_from(provider=provider, response=response),
            )
        if code in (401, 403):
            return ProviderKeyLiveCheckResult(status=LiveCheckStatus.INVALID_KEY)
        if provider is ProviderName.GOOGLE and code == 400:
            # generativelanguage rejects bad keys with 400
            # ``API_KEY_INVALID`` rather than a 401.
            return ProviderKeyLiveCheckResult(status=LiveCheckStatus.INVALID_KEY)
        if provider is ProviderName.VIRTUALS and code in (400, 404):
            # The completion probe names a model (see _VIRTUALS_PROBE_MODEL). A
            # 400/404 is the gateway rejecting that MODEL, not the key — the key
            # already cleared the auth guard to get this far. Reporting it as
            # "couldn't check" is what stops a retired probe model from telling
            # every user their perfectly good key is invalid.
            return ProviderKeyLiveCheckResult(
                status=LiveCheckStatus.PROVIDER_UNREACHABLE
            )
        # 5xx, 429, and anything else non-verdictive: the provider did
        # not tell us the key is bad, so "couldn't check" — never a
        # failure verdict.
        return ProviderKeyLiveCheckResult(status=LiveCheckStatus.PROVIDER_UNREACHABLE)

    def _model_ids_from(
        self, *, provider: ProviderName, response: httpx.Response
    ) -> tuple[str, ...]:
        """Best-effort model-id extraction. A 200 whose body we cannot
        parse is still a VALID key — auth is what was probed."""

        try:
            payload = response.json()
        except Exception:
            return ()
        if not isinstance(payload, dict):
            return ()
        if provider in (ProviderName.OPENROUTER, ProviderName.VIRTUALS):
            # Neither probe is a model listing: OpenRouter's ``/api/v1/key``
            # returns usage metadata, and the Virtuals probe returns a
            # completion. Virtuals' model discovery is the runtime's job
            # (VirtualsModelSource), not the credential check's.
            return ()
        if provider is ProviderName.GOOGLE:
            entries = payload.get("models")
            if not isinstance(entries, list):
                return ()
            names = (entry.get("name") for entry in entries if isinstance(entry, dict))
            return tuple(
                name.removeprefix("models/")
                for name in names
                if isinstance(name, str) and name
            )
        # openai + anthropic both use ``{"data": [{"id": ...}, ...]}``.
        entries = payload.get("data")
        if not isinstance(entries, list):
            return ()
        ids = (entry.get("id") for entry in entries if isinstance(entry, dict))
        return tuple(
            model_id for model_id in ids if isinstance(model_id, str) and model_id
        )


__all__ = [
    "LiveCheckStatus",
    "ProviderKeyLiveCheckResult",
    "ProviderKeyLiveValidator",
]
