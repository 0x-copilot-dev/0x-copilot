"""Tenant-scoped Artifact Repository HTTP routes.

The handlers are transport-only: verified identity is passed into the existing
``ArtifactService`` and multipart bodies are streamed from Starlette's spooled
``UploadFile``.  No handler buffers artifact content or publishes ledger events.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import cast

from fastapi import (
    APIRouter,
    Body,
    Depends,
    Header,
    HTTPException,
    Query,
    Request,
    Response,
    status,
)
from fastapi.responses import StreamingResponse
from pydantic import ValidationError
from starlette.datastructures import FormData, UploadFile

from copilot_service_contracts.scopes import RUNTIME_USE

from agent_runtime.artifacts import (
    ArtifactBlobUnavailableError,
    ArtifactConflictError,
    ArtifactCreateRequest,
    ArtifactDigestMismatchError,
    ArtifactError,
    ArtifactIdempotencyConflictError,
    ArtifactInvalidCursorError,
    ArtifactInvalidSourceError,
    ArtifactLimits,
    ArtifactNotFoundError,
    ArtifactPromotionRequest,
    ArtifactProvenance,
    ArtifactRangeError,
    ArtifactRevisionRequest,
    ArtifactService,
    ArtifactStorageError,
    ArtifactTooLargeError,
)
from agent_runtime.surfaces_v2.ledger_models import ArtifactAuthor, ArtifactKind
from runtime_api.http.artifact_content import ArtifactContentPolicy
from runtime_api.http.artifact_multipart import (
    ArtifactMultipartInvalid,
    ArtifactMultipartReader,
    ArtifactMultipartTooLarge,
    PROCESS_ARTIFACT_UPLOAD_ADMISSION,
)
from runtime_api.identity import Identity
from runtime_api.rbac import RequireScopes
from runtime_api.schemas.artifacts import (
    ArtifactCreateMetadata,
    ArtifactDetailResponse,
    ArtifactListResponse,
    ArtifactMutationResponse,
    ArtifactPromotionBody,
    ArtifactRevisionMetadata,
    ArtifactRevisionResponse,
)

_ARTIFACT_LIMITS = ArtifactLimits()
_ARTIFACT_KIND_FILE_LIMITS = {
    kind.value: _ARTIFACT_LIMITS.for_kind(kind).maximum_bytes for kind in ArtifactKind
}


class ArtifactRoutes:
    """Thin route handlers over the canonical artifact application service."""

    _LOGGER = logging.getLogger(__name__)

    class Messages:
        SERVICE_UNAVAILABLE = "Artifact service is not configured."
        MULTIPART_UNAVAILABLE = "Multipart upload support is not configured."
        INVALID_MULTIPART = "Artifact multipart metadata is invalid."
        INVALID_PROMOTION = "Artifact promotion metadata is invalid."
        CONTENT_REQUIRED = "Multipart field `content` is required."

    class Multipart:
        CONTENT = "content"
        MAX_FILES = 1
        MAX_FIELDS = 8
        MAX_METADATA_PART_BYTES = 16 * 1024
        CHUNK_BYTES = 64 * 1024
        KIND_FILE_LIMITS = _ARTIFACT_KIND_FILE_LIMITS
        MAXIMUM_FILE_BYTES = max(KIND_FILE_LIMITS.values())
        MAXIMUM_OVERHEAD_BYTES = 256 * 1024
        ADMISSION = PROCESS_ARTIFACT_UPLOAD_ADMISSION

    _ERROR_STATUS: dict[type[ArtifactError], int] = {
        ArtifactNotFoundError: status.HTTP_404_NOT_FOUND,
        ArtifactInvalidSourceError: status.HTTP_404_NOT_FOUND,
        ArtifactInvalidCursorError: status.HTTP_422_UNPROCESSABLE_CONTENT,
        ArtifactConflictError: status.HTTP_409_CONFLICT,
        ArtifactIdempotencyConflictError: status.HTTP_409_CONFLICT,
        ArtifactTooLargeError: status.HTTP_413_CONTENT_TOO_LARGE,
        ArtifactDigestMismatchError: status.HTTP_422_UNPROCESSABLE_CONTENT,
        ArtifactRangeError: status.HTTP_416_RANGE_NOT_SATISFIABLE,
        ArtifactBlobUnavailableError: status.HTTP_503_SERVICE_UNAVAILABLE,
        ArtifactStorageError: status.HTTP_503_SERVICE_UNAVAILABLE,
    }

    @classmethod
    async def create_artifact(
        cls,
        request: Request,
        run_id: str,
        identity: Identity,
        idempotency_key: str = Header(
            ..., alias="Idempotency-Key", min_length=1, max_length=255
        ),
    ) -> ArtifactMutationResponse:
        async with cls.Multipart.ADMISSION.slot():
            form, content = await cls._multipart(
                request,
                maximum_file_bytes=cls.Multipart.MAXIMUM_FILE_BYTES,
                kind_file_limits=cls.Multipart.KIND_FILE_LIMITS,
            )
            try:
                metadata = ArtifactCreateMetadata.model_validate(
                    cls._metadata_fields(form, excluding={cls.Multipart.CONTENT})
                )
                result = await cls._service(request).create_from_stream(
                    org_id=identity.org_id,
                    user_id=identity.user_id,
                    request=ArtifactCreateRequest(
                        run_id=run_id,
                        kind=metadata.kind,
                        title=metadata.title,
                        media_type=metadata.media_type,
                        suggested_filename=metadata.suggested_filename,
                        expected_digest=metadata.expected_digest,
                        idempotency_key=idempotency_key,
                    ),
                    provenance=ArtifactProvenance(
                        author=ArtifactAuthor.USER,
                        source_ref=None,
                    ),
                    chunks=cls._upload_chunks(content),
                )
            except ValidationError as exc:
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_CONTENT,
                    cls.Messages.INVALID_MULTIPART,
                ) from exc
            except ArtifactError as exc:
                raise cls._http(exc) from exc
            finally:
                await content.close()
                await form.close()
        return ArtifactMutationResponse.from_result(result)

    @classmethod
    async def list_artifacts(
        cls,
        request: Request,
        run_id: str,
        identity: Identity,
        kind: ArtifactKind | None = Query(default=None),
        limit: int = Query(default=50, ge=1, le=100),
        cursor: str | None = Query(default=None, min_length=1, max_length=2048),
    ) -> ArtifactListResponse:
        try:
            page = await cls._service(request).list_for_run(
                org_id=identity.org_id,
                user_id=identity.user_id,
                run_id=run_id,
                kind=kind,
                limit=limit,
                cursor=cursor,
            )
        except ArtifactError as exc:
            raise cls._http(exc) from exc
        return ArtifactListResponse.from_page(page)

    @classmethod
    async def get_artifact(
        cls,
        request: Request,
        artifact_id: str,
        identity: Identity,
    ) -> ArtifactDetailResponse:
        try:
            record = await cls._service(request).get_metadata(
                org_id=identity.org_id,
                user_id=identity.user_id,
                artifact_id=artifact_id,
            )
        except ArtifactError as exc:
            raise cls._http(exc) from exc
        return ArtifactDetailResponse.from_record(record)

    @classmethod
    async def get_revision(
        cls,
        request: Request,
        artifact_id: str,
        revision: int,
        identity: Identity,
    ) -> ArtifactRevisionResponse:
        try:
            stored = await cls._service(request).get_revision_metadata(
                org_id=identity.org_id,
                user_id=identity.user_id,
                artifact_id=artifact_id,
                revision=revision,
            )
        except ArtifactError as exc:
            raise cls._http(exc) from exc
        return ArtifactRevisionResponse.from_stored(stored)

    @classmethod
    async def get_revision_content(
        cls,
        request: Request,
        artifact_id: str,
        revision: int,
        identity: Identity,
        range_header: str | None = Header(default=None, alias="Range"),
        if_range: str | None = Header(default=None, alias="If-Range"),
    ) -> Response:
        service = cls._service(request)
        stored = None
        try:
            stored = await service.get_revision_metadata(
                org_id=identity.org_id,
                user_id=identity.user_id,
                artifact_id=artifact_id,
                revision=revision,
            )
            etag = ArtifactContentPolicy.etag(stored.revision.content_digest)
            effective_range = (
                None
                if if_range is not None and if_range.strip() != etag
                else range_header
            )
            byte_range = ArtifactContentPolicy.parse_range(
                effective_range,
                byte_size=stored.revision.byte_size,
                range_supported=stored.range_supported,
            )
            record, streamed, chunks = await service.stream_revision(
                org_id=identity.org_id,
                user_id=identity.user_id,
                artifact_id=artifact_id,
                revision=revision,
                byte_range=byte_range,
            )
        except ArtifactRangeError as exc:
            size = stored.revision.byte_size if stored is not None else 0
            raise HTTPException(
                status.HTTP_416_RANGE_NOT_SATISFIABLE,
                exc.safe_message,
                headers={"Content-Range": f"bytes */{size}"},
            ) from exc
        except ArtifactError as exc:
            raise cls._http(exc) from exc

        filename = ArtifactContentPolicy.filename(
            record.suggested_filename,
            record.artifact.title,
        )
        headers = ArtifactContentPolicy.response_headers(
            media_type=record.artifact.media_type,
            filename=filename,
            content_digest=streamed.revision.content_digest,
            byte_size=streamed.revision.byte_size,
            range_supported=streamed.range_supported,
            byte_range=byte_range,
        )
        return StreamingResponse(
            cls._client_stream(request, chunks),
            status_code=(
                status.HTTP_206_PARTIAL_CONTENT
                if byte_range is not None
                else status.HTTP_200_OK
            ),
            headers=headers,
        )

    @classmethod
    async def append_revision(
        cls,
        request: Request,
        artifact_id: str,
        identity: Identity,
        idempotency_key: str = Header(
            ..., alias="Idempotency-Key", min_length=1, max_length=255
        ),
    ) -> ArtifactMutationResponse:
        service = cls._service(request)
        try:
            record = await service.get_metadata(
                org_id=identity.org_id,
                user_id=identity.user_id,
                artifact_id=artifact_id,
            )
        except ArtifactError as exc:
            raise cls._http(exc) from exc
        maximum_file_bytes = cls.Multipart.KIND_FILE_LIMITS[record.artifact.kind.value]

        async with cls.Multipart.ADMISSION.slot():
            form, content = await cls._multipart(
                request,
                maximum_file_bytes=maximum_file_bytes,
            )
            try:
                metadata = ArtifactRevisionMetadata.model_validate(
                    cls._metadata_fields(form, excluding={cls.Multipart.CONTENT})
                )
                result = await service.append_revision_from_stream(
                    org_id=identity.org_id,
                    user_id=identity.user_id,
                    request=ArtifactRevisionRequest(
                        artifact_id=artifact_id,
                        parent_revision=metadata.parent_revision,
                        expected_digest=metadata.expected_digest,
                        idempotency_key=idempotency_key,
                    ),
                    provenance=ArtifactProvenance(
                        author=ArtifactAuthor.USER,
                        source_ref=None,
                    ),
                    chunks=cls._upload_chunks(content),
                )
            except ValidationError as exc:
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_CONTENT,
                    cls.Messages.INVALID_MULTIPART,
                ) from exc
            except ArtifactError as exc:
                raise cls._http(exc) from exc
            finally:
                await content.close()
                await form.close()
        return ArtifactMutationResponse.from_result(result)

    @classmethod
    async def promote_artifact(
        cls,
        request: Request,
        identity: Identity,
        payload: ArtifactPromotionBody = Body(...),
        idempotency_key: str = Header(
            ..., alias="Idempotency-Key", min_length=1, max_length=255
        ),
    ) -> ArtifactMutationResponse:
        try:
            result = await cls._service(request).promote_source(
                org_id=identity.org_id,
                user_id=identity.user_id,
                request=ArtifactPromotionRequest(
                    **payload.model_dump(),
                    idempotency_key=idempotency_key,
                ),
            )
        except (ValidationError, ValueError) as exc:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                cls.Messages.INVALID_PROMOTION,
            ) from exc
        except ArtifactError as exc:
            raise cls._http(exc) from exc
        return ArtifactMutationResponse.from_result(result)

    @classmethod
    async def delete_artifact(
        cls,
        request: Request,
        artifact_id: str,
        identity: Identity,
        idempotency_key: str = Header(
            ..., alias="Idempotency-Key", min_length=1, max_length=255
        ),
    ) -> Response:
        try:
            await cls._service(request).soft_delete(
                org_id=identity.org_id,
                user_id=identity.user_id,
                artifact_id=artifact_id,
                idempotency_key=idempotency_key,
            )
        except ArtifactError as exc:
            raise cls._http(exc) from exc
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @classmethod
    async def _multipart(
        cls,
        request: Request,
        *,
        maximum_file_bytes: int,
        kind_file_limits: dict[str, int] | None = None,
    ) -> tuple[FormData, UploadFile]:
        try:
            form = await ArtifactMultipartReader.parse(
                request,
                maximum_file_bytes=maximum_file_bytes,
                maximum_overhead_bytes=cls.Multipart.MAXIMUM_OVERHEAD_BYTES,
                maximum_files=cls.Multipart.MAX_FILES,
                maximum_fields=cls.Multipart.MAX_FIELDS,
                maximum_field_bytes=cls.Multipart.MAX_METADATA_PART_BYTES,
                kind_file_limits=kind_file_limits,
            )
        except ArtifactMultipartTooLarge as exc:
            raise HTTPException(
                status.HTTP_413_CONTENT_TOO_LARGE,
                ArtifactTooLargeError().safe_message,
            ) from exc
        except ArtifactMultipartInvalid as exc:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                cls.Messages.INVALID_MULTIPART,
            ) from exc
        except AssertionError as exc:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                cls.Messages.MULTIPART_UNAVAILABLE,
            ) from exc
        except Exception as exc:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                cls.Messages.INVALID_MULTIPART,
            ) from exc
        content = form.get(cls.Multipart.CONTENT)
        if not isinstance(content, UploadFile):
            await form.close()
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                cls.Messages.CONTENT_REQUIRED,
            )
        return form, content

    @classmethod
    def _metadata_fields(
        cls,
        form: FormData,
        *,
        excluding: set[str],
    ) -> dict[str, str]:
        fields: dict[str, str] = {}
        for key, value in form.multi_items():
            if key in excluding:
                continue
            if key in fields or isinstance(value, UploadFile):
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_CONTENT,
                    cls.Messages.INVALID_MULTIPART,
                )
            if value == "" and key in {
                "suggested_filename",
                "expected_digest",
            }:
                continue
            fields[key] = cast(str, value)
        return fields

    @classmethod
    async def _upload_chunks(cls, content: UploadFile) -> AsyncIterator[bytes]:
        while chunk := await content.read(cls.Multipart.CHUNK_BYTES):
            yield chunk

    @classmethod
    async def _client_stream(
        cls,
        request: Request,
        chunks: AsyncIterator[bytes],
    ) -> AsyncIterator[bytes]:
        try:
            async for chunk in chunks:
                if await request.is_disconnected():
                    break
                yield chunk
        except Exception:
            # Headers may already be committed, so a late blob failure can only
            # terminate the stream. Never serialize the adapter exception into
            # the body or logs: it may contain a physical path or blob key.
            cls._LOGGER.error("artifact.content_stream_failed")
        finally:
            close = getattr(chunks, "aclose", None)
            if close is not None:
                await close()

    @staticmethod
    def _service(request: Request) -> ArtifactService:
        service = getattr(request.app.state, "artifact_service", None)
        if service is None:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                ArtifactRoutes.Messages.SERVICE_UNAVAILABLE,
            )
        return cast(ArtifactService, service)

    @classmethod
    def _http(cls, exc: ArtifactError) -> HTTPException:
        return HTTPException(
            status_code=cls._ERROR_STATUS.get(
                type(exc), status.HTTP_500_INTERNAL_SERVER_ERROR
            ),
            detail=exc.safe_message,
        )


def register_artifact_routes(router: APIRouter) -> None:
    """Register the eight PRD-A2 endpoints on a ``/v1/agent`` router.

    The literal promotion route is intentionally first so a future broad
    artifact matcher cannot shadow it.
    """

    dependency = [Depends(RequireScopes(RUNTIME_USE))]
    router.add_api_route(
        "/artifacts:promote",
        ArtifactRoutes.promote_artifact,
        methods=["POST"],
        response_model=ArtifactMutationResponse,
        response_model_exclude_none=True,
        status_code=status.HTTP_201_CREATED,
        name="promote_artifact",
        dependencies=dependency,
    )
    router.add_api_route(
        "/runs/{run_id}/artifacts",
        ArtifactRoutes.create_artifact,
        methods=["POST"],
        response_model=ArtifactMutationResponse,
        response_model_exclude_none=True,
        status_code=status.HTTP_201_CREATED,
        name="create_artifact",
        dependencies=dependency,
    )
    router.add_api_route(
        "/runs/{run_id}/artifacts",
        ArtifactRoutes.list_artifacts,
        methods=["GET"],
        response_model=ArtifactListResponse,
        response_model_exclude_none=True,
        name="list_artifacts",
        dependencies=dependency,
    )
    router.add_api_route(
        "/artifacts/{artifact_id}/revisions/{revision}/content",
        ArtifactRoutes.get_revision_content,
        methods=["GET"],
        name="get_artifact_revision_content",
        dependencies=dependency,
    )
    router.add_api_route(
        "/artifacts/{artifact_id}/revisions/{revision}",
        ArtifactRoutes.get_revision,
        methods=["GET"],
        response_model=ArtifactRevisionResponse,
        response_model_exclude_none=True,
        name="get_artifact_revision",
        dependencies=dependency,
    )
    router.add_api_route(
        "/artifacts/{artifact_id}/revisions",
        ArtifactRoutes.append_revision,
        methods=["POST"],
        response_model=ArtifactMutationResponse,
        response_model_exclude_none=True,
        status_code=status.HTTP_201_CREATED,
        name="append_artifact_revision",
        dependencies=dependency,
    )
    router.add_api_route(
        "/artifacts/{artifact_id}",
        ArtifactRoutes.get_artifact,
        methods=["GET"],
        response_model=ArtifactDetailResponse,
        response_model_exclude_none=True,
        name="get_artifact",
        dependencies=dependency,
    )
    router.add_api_route(
        "/artifacts/{artifact_id}",
        ArtifactRoutes.delete_artifact,
        methods=["DELETE"],
        status_code=status.HTTP_204_NO_CONTENT,
        name="delete_artifact",
        dependencies=dependency,
    )


__all__ = ("ArtifactRoutes", "register_artifact_routes")
