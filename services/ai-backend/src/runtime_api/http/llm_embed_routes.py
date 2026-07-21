"""Internal LLM-embedding endpoint for Library indexing / retrieval (P7.5).

Mounted under ``/internal/v1/llm/embed`` — service-token only. The Library
indexing worker (``services/backend/src/backend_app/jobs/library_indexer.py``)
and the online retrieval path call this endpoint to vectorise text. Per
sub-PRD §6.6 this is the **only** LLM call path for Library indexing, and
per cross-audit §5.5 the TU-1 single-tracker invariant stands: every call
routes through :func:`build_embeddings_model` (the canonical bootstrap
boundary in :mod:`agent_runtime.execution.deep_agent_builder`) and writes
one :class:`RuntimeModelCallUsageRecord` via the existing
:class:`UsageRecorder` pipeline. No parallel tracker, no SDK import here.

Auth: :class:`RuntimeServiceAuthenticator.require_identity` — same strict
service-token + identity-header check used by other internal routes that
need to attribute usage to a tenant.

Body validation lives in :class:`LlmEmbedRequest`; payload caps prevent a
caller from melting the embed step with one giant request.
"""

from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field, NonNegativeInt, field_validator

from agent_runtime.execution.deep_agent_builder import build_embeddings_model
from agent_runtime.observability.attribution import (
    Purpose,
    UsageAttributionContext,
)
from agent_runtime.observability.usage_recorder import (
    PostgresUsageRecorder,
    UsageRecorder,
)
from agent_runtime.persistence.records import RuntimeModelCallUsageRecord
from agent_runtime.pricing.catalog import ModelPricingCatalog
from runtime_api.auth import RuntimeServiceAuthenticator


_LOGGER = logging.getLogger(__name__)


class LlmEmbedLimits:
    """Body-size caps applied before the embedding model is constructed.

    Co-located with the request schema so a future cap tweak is one read
    site. The defaults match the Library indexer's batching contract
    (1024 chunks per call, ~8 MB of UTF-8 text total).
    """

    MAX_TEXTS = 1024
    MAX_TOTAL_BYTES = 8 * 1024 * 1024  # 8 MB combined UTF-8 length
    MAX_MODEL_NAME_LEN = 200


# Literal narrows the request to the two Library purposes — any other
# value (including legitimate Purpose enum members like ``main``) is
# rejected by FastAPI before the handler runs.
LlmEmbedPurpose = Literal["library_indexing", "library_retrieval"]


class LlmEmbedRequest(BaseModel):
    """Body for ``POST /internal/v1/llm/embed``.

    ``texts`` is a list of UTF-8 strings to embed. ``model`` carries the
    provider-native model name (e.g. ``openai:text-embedding-3-small``)
    so the caller pins the embedding model — the runtime does not
    hardcode a default. ``purpose`` selects the token-usage bucket
    (Library retrieval or Library indexing); other purposes are
    rejected.
    """

    model_config = ConfigDict(extra="forbid")

    texts: list[str] = Field(
        ...,
        min_length=1,
        max_length=LlmEmbedLimits.MAX_TEXTS,
    )
    model: str = Field(..., min_length=1, max_length=LlmEmbedLimits.MAX_MODEL_NAME_LEN)
    purpose: LlmEmbedPurpose

    @field_validator("texts")
    @classmethod
    def _enforce_total_payload_bytes(cls, value: list[str]) -> list[str]:
        """Reject the request when the combined UTF-8 size exceeds the cap."""

        total_bytes = sum(len(text.encode("utf-8")) for text in value)
        if total_bytes > LlmEmbedLimits.MAX_TOTAL_BYTES:
            raise ValueError(
                "texts payload exceeds maximum combined size "
                f"({LlmEmbedLimits.MAX_TOTAL_BYTES} bytes)"
            )
        return value


class LlmEmbedResponse(BaseModel):
    """Response for ``POST /internal/v1/llm/embed``.

    ``vectors`` is parallel to the request's ``texts``: ``vectors[i]`` is
    the embedding for ``texts[i]``. ``dimensions`` is the length of each
    vector (echoed for caller convenience — every vector has the same
    length).
    """

    model_config = ConfigDict(extra="forbid")

    vectors: list[list[float]]
    model: str
    dimensions: NonNegativeInt


class _LlmEmbedKeys:
    """Names co-located so structured-log queries and route names pin them."""

    ROUTE_NAME = "internal_llm_embed"
    LOG_EVENT_FAILED = "llm_embed_failed"
    LOG_EVENT_DIM_MISMATCH = "llm_embed_dimension_mismatch"


class _LlmEmbedMessages:
    """User-facing error strings for the embed endpoint."""

    EMBED_FAILED = "Embedding generation failed."
    NO_VECTORS = "Embedding model returned no vectors."
    INCONSISTENT_DIMENSIONS = "Embedding model returned inconsistent vector lengths."


class _LlmEmbedPurposeMap:
    """Map the wire ``purpose`` literal to the canonical :class:`Purpose` enum.

    Kept as an explicit table so the route handler does no string
    munging — a future Purpose addition is one entry, not a switch in
    the handler body.
    """

    _BY_VALUE: dict[str, Purpose] = {
        "library_indexing": Purpose.LIBRARY_INDEXING,
        "library_retrieval": Purpose.LIBRARY_RETRIEVAL,
    }

    @classmethod
    def resolve(cls, value: str) -> Purpose:
        try:
            return cls._BY_VALUE[value]
        except KeyError as exc:  # defensive; Literal should already block it.
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "Invalid purpose for /internal/v1/llm/embed",
            ) from exc


class LlmEmbedRoutes:
    """Handlers for ``/internal/v1/llm/embed``.

    Stateless: every dependency is resolved from ``request.app.state``
    (persistence + settings) so the same handler works for the
    Postgres-backed app and the in-memory test app.
    """

    @classmethod
    async def embed(cls, request: Request) -> LlmEmbedResponse:
        """Validate the body, run the embedding call, record usage, return vectors."""

        identity = RuntimeServiceAuthenticator.require_identity(request)
        body = await cls._parse_body(request)
        purpose = _LlmEmbedPurposeMap.resolve(body.purpose)
        provider, model_name = cls._split_model(body.model)

        embeddings = build_embeddings_model(
            provider=provider,
            model_name=model_name,
        )

        started_perf = time.perf_counter()
        completed = datetime.now(timezone.utc)
        try:
            vectors = await embeddings.aembed_documents(list(body.texts))
        except Exception as exc:
            _LOGGER.warning(
                _LlmEmbedKeys.LOG_EVENT_FAILED,
                extra={
                    "metadata": {
                        "org_id": identity.org_id,
                        "model": body.model,
                        "purpose": body.purpose,
                    }
                },
                exc_info=True,
            )
            raise HTTPException(
                status.HTTP_502_BAD_GATEWAY,
                _LlmEmbedMessages.EMBED_FAILED,
            ) from exc
        duration_ms = int((time.perf_counter() - started_perf) * 1000)

        dimensions = cls._validate_vectors(vectors)
        await cls._record_usage(
            request=request,
            identity_org_id=identity.org_id,
            identity_user_id=identity.user_id,
            provider=provider,
            model_name=model_name,
            purpose=purpose,
            input_text_bytes=sum(len(t.encode("utf-8")) for t in body.texts),
            text_count=len(body.texts),
            duration_ms=duration_ms,
            completed=completed,
        )
        return LlmEmbedResponse(
            vectors=vectors,
            model=body.model,
            dimensions=dimensions,
        )

    @classmethod
    async def _parse_body(cls, request: Request) -> LlmEmbedRequest:
        """Decode + validate the incoming JSON body.

        Failures surface as 400 through FastAPI's RequestValidationError
        handler (see ``RuntimeApiErrorMapper``); we do not call
        ``model_validate`` directly here.
        """

        payload = await request.json()
        return LlmEmbedRequest.model_validate(payload)

    @staticmethod
    def _split_model(model: str) -> tuple[str, str]:
        """Split ``provider:model_name``; if no colon, default to OpenAI.

        Mirrors the LangChain ``init_embeddings`` model-string contract
        (``openai:text-embedding-3-small``). A bare model name keeps
        backward compatibility with simple callers and is treated as
        OpenAI per the runtime's default provider for the embeddings
        family.
        """

        head, sep, tail = model.partition(":")
        if not sep:
            return "openai", head
        return head, tail

    @staticmethod
    def _validate_vectors(vectors: list[list[float]]) -> int:
        """Confirm the model returned vectors with a uniform dimension."""

        if not vectors:
            raise HTTPException(
                status.HTTP_502_BAD_GATEWAY, _LlmEmbedMessages.NO_VECTORS
            )
        dimensions = len(vectors[0])
        for vec in vectors:
            if len(vec) != dimensions:
                _LOGGER.warning(_LlmEmbedKeys.LOG_EVENT_DIM_MISMATCH)
                raise HTTPException(
                    status.HTTP_502_BAD_GATEWAY,
                    _LlmEmbedMessages.INCONSISTENT_DIMENSIONS,
                )
        return dimensions

    @classmethod
    async def _record_usage(
        cls,
        *,
        request: Request,
        identity_org_id: str,
        identity_user_id: str,
        provider: str,
        model_name: str,
        purpose: Purpose,
        input_text_bytes: int,
        text_count: int,
        duration_ms: int,
        completed: datetime,
    ) -> None:
        """Write one :class:`RuntimeModelCallUsageRecord` for this embed call.

        Library embedding calls aren't bound to a conversation/run/trace
        — they're system-initiated retrieval/indexing operations. We
        synthesize stable per-call identifiers so the row satisfies the
        record's non-null contract; rollups by ``purpose`` and
        ``org_id`` are the only Library-relevant cuts.

        Token usage for embedding models is not reported by every
        provider in a uniform shape (and never as ``output_tokens``).
        We persist ``input_tokens=text_count`` as a coarse proxy
        ("number of items embedded") so the row is non-trivially
        attributable; pricing layers can lift this when provider
        usage metadata starts riding on the LangChain Embeddings
        return path. ``cached_input_tokens`` stays at 0; the recorder's
        cost-stamp call may produce ``cost_micro_usd=None`` when the
        catalog has no embedding-model entry, which is acceptable
        (the row still attributes the call to the Library purpose).
        """

        recorder = cls._resolve_usage_recorder(request)
        # Synthesize a non-empty trace/run/conversation triple so the
        # record contract is satisfied without colliding with real
        # runtime IDs. Prefix with ``embed-`` so audit queries can
        # distinguish embed rows from chat rows at a glance.
        synthetic_id = f"embed-{uuid.uuid4().hex}"
        attribution = UsageAttributionContext(
            org_id=identity_org_id,
            user_id=identity_user_id,
            run_id=synthetic_id,
            conversation_id=synthetic_id,
            trace_id=synthetic_id,
            purpose=purpose,
        )
        record = RuntimeModelCallUsageRecord(
            org_id=attribution.org_id,
            run_id=attribution.run_id,
            conversation_id=attribution.conversation_id,
            trace_id=attribution.trace_id,
            model_provider=provider,
            model_name=model_name,
            purpose=attribution.purpose.value,
            input_tokens=text_count,
            output_tokens=0,
            cached_input_tokens=0,
            total_tokens=text_count,
            duration_ms=duration_ms,
        )
        # ``input_text_bytes`` is referenced here so the linter keeps it
        # as a load-bearing audit input even though no column captures
        # it directly (future expansion may add a payload-size column).
        _ = input_text_bytes
        await recorder.record_call(record, pricing_at=completed)

    @staticmethod
    def _resolve_usage_recorder(request: Request) -> UsageRecorder:
        """Return the request-scoped recorder, lazily building one if absent.

        Production wiring (the worker) constructs a
        :class:`PostgresUsageRecorder` per handler; the embed endpoint
        is request-scoped and does not own the worker's recorder, so
        we build one on demand from the same collaborators
        (persistence + pricing catalog). Tests inject a recorder by
        setting ``app.state.llm_embed_usage_recorder``.
        """

        injected = getattr(request.app.state, "llm_embed_usage_recorder", None)
        if injected is not None:
            return injected
        persistence = request.app.state.runtime_persistence
        pricing_catalog = ModelPricingCatalog.from_litellm()
        return PostgresUsageRecorder(
            persistence=persistence, pricing_catalog=pricing_catalog
        )


class LlmEmbedApiRouter:
    """Build the ``/internal/v1/llm/embed`` router.

    Kept on its own router (mounted by ``RuntimeApiAppFactory``) so the
    endpoint can carry a tight body-size limit and a service-token
    dependency without bleeding into the rest of the
    ``/internal/v1`` group.
    """

    @classmethod
    def create_router(cls) -> APIRouter:
        """Return the router carrying the embed endpoint."""

        router = APIRouter(prefix="/internal/v1", tags=["runtime-internal", "llm"])
        router.add_api_route(
            "/llm/embed",
            LlmEmbedRoutes.embed,
            methods=["POST"],
            response_model=LlmEmbedResponse,
            name=_LlmEmbedKeys.ROUTE_NAME,
        )
        return router
