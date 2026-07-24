"""Artifact HTTP contract tests with a fake application service."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import replace

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from agent_runtime.artifacts import (
    ArtifactInvalidCursorError,
    ArtifactListPage,
    ArtifactMutationResult,
    ArtifactNotFoundError,
    ArtifactService,
    ArtifactStoredRecord,
    ArtifactStoredRevision,
)
from agent_runtime.surfaces_v2.entities import Artifact, ArtifactRevision
from agent_runtime.surfaces_v2.ledger_models import ArtifactAuthor, ArtifactKind
from agent_runtime.settings import RuntimeSettings
from runtime_adapters.factory import RuntimeAdapterFactory
from runtime_adapters.in_memory import InMemoryRuntimeApiStore
from runtime_api.app import RuntimeApiAppFactory
from runtime_api.http.artifacts import ArtifactRoutes, register_artifact_routes


class ArtifactRouteMixin:
    ORG = "org_artifacts"
    USER = "user_artifacts"
    RUN = "run_artifacts"
    ARTIFACT_ID = "art_123e4567-e89b-42d3-a456-426614174000"
    DIGEST = "a" * 64
    CONTENT = b"<script>alert('never execute')</script>"

    @classmethod
    def headers(cls, *, idempotency: bool = False) -> dict[str, str]:
        headers = {
            "x-enterprise-org-id": cls.ORG,
            "x-enterprise-user-id": cls.USER,
            "x-enterprise-permission-scopes": "runtime:use",
        }
        if idempotency:
            headers["Idempotency-Key"] = "idem_artifact_1"
        return headers

    @classmethod
    def record(
        cls,
        *,
        revision: int = 1,
        title: str = "Safe report",
        filename: str | None = "report.html",
        media_type: str = "text/html",
        range_supported: bool = True,
    ) -> ArtifactStoredRecord:
        revision_entity = ArtifactRevision(
            artifact_id=cls.ARTIFACT_ID,
            revision=revision,
            parent_revision=revision - 1 if revision > 1 else None,
            content_ref=(f"artifact://{cls.ARTIFACT_ID}/revisions/{revision}"),
            content_digest=cls.DIGEST,
            byte_size=len(cls.CONTENT),
            author=ArtifactAuthor.USER,
            source_ref=None,
            created_at="2026-07-24T00:00:00+00:00",
        )
        return ArtifactStoredRecord(
            artifact=Artifact(
                artifact_id=cls.ARTIFACT_ID,
                org_id=cls.ORG,
                user_id=cls.USER,
                conversation_id="conv_artifacts",
                run_id=cls.RUN,
                kind=ArtifactKind.CODE,
                title=title,
                media_type=media_type,
                current_revision=revision,
                created_by=ArtifactAuthor.USER,
                created_at="2026-07-24T00:00:00+00:00",
                updated_at="2026-07-24T00:00:00+00:00",
                deleted_at=None,
            ),
            current_revision=ArtifactStoredRevision(
                revision=revision_entity,
                blob_key=cls.DIGEST,
                range_supported=range_supported,
            ),
            suggested_filename=filename,
        )

    @classmethod
    def client(cls, service: object | None = None) -> TestClient:
        app = FastAPI()
        router = APIRouter(prefix="/v1/agent")
        register_artifact_routes(router)
        app.include_router(router)
        if service is not None:
            app.state.artifact_service = service
        return TestClient(app)

    @classmethod
    def multipart_body(
        cls,
        content: bytes,
        *,
        boundary: str = "artifact-test-boundary",
        kind: str = "code",
        content_first: bool = False,
    ) -> bytes:
        kind_part = (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="kind"\r\n\r\n'
            f"{kind}\r\n"
        ).encode()
        metadata_parts = (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="title"\r\n\r\n'
            "bounded.txt\r\n"
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="media_type"\r\n\r\n'
            "text/plain\r\n"
        ).encode()
        content_part = (
            (
                f"--{boundary}\r\n"
                'Content-Disposition: form-data; name="content"; '
                'filename="bounded.txt"\r\n'
                "Content-Type: text/plain\r\n\r\n"
            ).encode()
            + content
            + b"\r\n"
        )
        return (
            content_part + kind_part + metadata_parts
            if content_first
            else kind_part + metadata_parts + content_part
        ) + f"--{boundary}--\r\n".encode()

    @classmethod
    def real_client(
        cls,
        service: object | None = None,
        *,
        artifact_effects_v2: str | None = "true",
        complete_repository_ports: bool = False,
    ) -> TestClient:
        store = InMemoryRuntimeApiStore()
        ports = RuntimeAdapterFactory.from_store(store)
        if complete_repository_ports:
            ports = replace(
                ports,
                artifact_metadata_store=object(),  # composition-only contract fake
                artifact_blob_store=object(),  # composition-only contract fake
            )
        environ = {
            "OPENAI_API_KEY": "sk-test",
            "RUNTIME_DEFAULT_PROVIDER": "openai",
            "RUNTIME_DEFAULT_MODEL": "gpt-5.4-mini",
        }
        if artifact_effects_v2 is not None:
            environ["ARTIFACT_EFFECTS_V2"] = artifact_effects_v2
        settings = RuntimeSettings.load(environ=environ)
        app = RuntimeApiAppFactory.create_app(
            ports=ports,
            settings=settings,
            artifact_service=service,
            configure_logging_on_create=False,
            configure_telemetry_on_create=False,
        )
        return TestClient(app)


class FakeArtifactService(ArtifactRouteMixin):
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []
        self.current = self.record()
        self.content = self.CONTENT
        self.error: Exception | None = None
        self.stream_error: Exception | None = None

    def _raise(self) -> None:
        if self.error is not None:
            raise self.error

    def _require_scope(self, values: dict[str, object]) -> None:
        if values.get("org_id") != self.ORG or values.get("user_id") != self.USER:
            raise ArtifactNotFoundError()
        artifact_id = values.get("artifact_id")
        if artifact_id is not None and artifact_id != self.ARTIFACT_ID:
            raise ArtifactNotFoundError()

    async def create_from_stream(self, *, org_id, user_id, request, provenance, chunks):
        self._raise()
        self._require_scope({"org_id": org_id, "user_id": user_id})
        body = await self._read(chunks)
        self.calls.append(("create", (org_id, user_id, request, provenance, body)))
        return ArtifactMutationResult(record=self.current, replayed=False)

    async def list_for_run(self, **kwargs):
        self._raise()
        self._require_scope(kwargs)
        self.calls.append(("list", kwargs))
        return ArtifactListPage(artifacts=(self.current,), next_cursor="cursor_2")

    async def get_metadata(self, **kwargs):
        self._raise()
        self._require_scope(kwargs)
        self.calls.append(("get", kwargs))
        return self.current

    async def get_revision_metadata(self, **kwargs):
        self._raise()
        self._require_scope(kwargs)
        self.calls.append(("get_revision", kwargs))
        return self.current.current_revision

    async def stream_revision(self, **kwargs):
        self._raise()
        self._require_scope(kwargs)
        self.calls.append(("stream", kwargs))
        byte_range = kwargs.get("byte_range")
        body = self.content
        if byte_range is not None:
            body = body[byte_range.start : byte_range.end + 1]

        async def chunks() -> AsyncIterator[bytes]:
            yield body[:5]
            if self.stream_error is not None:
                raise self.stream_error
            yield body[5:]

        return self.current, self.current.current_revision, chunks()

    async def append_revision_from_stream(
        self, *, request, provenance, chunks, **kwargs
    ):
        self._raise()
        self._require_scope(kwargs)
        body = await self._read(chunks)
        self.calls.append(("revise", (kwargs, request, provenance, body)))
        return ArtifactMutationResult(record=self.record(revision=2), replayed=False)

    async def promote_source(self, *, request, **kwargs):
        self._raise()
        self._require_scope(kwargs)
        self.calls.append(("promote", (kwargs, request)))
        return ArtifactMutationResult(record=self.current, replayed=True)

    async def soft_delete(self, **kwargs):
        self._raise()
        self._require_scope(kwargs)
        self.calls.append(("delete", kwargs))

    @staticmethod
    async def _read(chunks: AsyncIterator[bytes]) -> bytes:
        parts: list[bytes] = []
        async for chunk in chunks:
            parts.append(chunk)
        return b"".join(parts)


class TestArtifactJsonRoutes(ArtifactRouteMixin):
    def test_missing_identity_is_401(self) -> None:
        response = self.client(FakeArtifactService()).get(
            f"/v1/agent/artifacts/{self.ARTIFACT_ID}"
        )
        assert response.status_code == 401

    def test_detail_and_list_never_expose_blob_key_or_nulls(self) -> None:
        service = FakeArtifactService()
        client = self.client(service)

        detail = client.get(
            f"/v1/agent/artifacts/{self.ARTIFACT_ID}",
            headers=self.headers(),
        )
        listing = client.get(
            f"/v1/agent/runs/{self.RUN}/artifacts?kind=code&limit=10",
            headers=self.headers(),
        )

        assert detail.status_code == 200
        assert detail.json()["artifact"]["artifact_id"] == self.ARTIFACT_ID
        assert "deleted_at" not in detail.json()["artifact"]
        assert "source_ref" not in detail.json()["current_revision"]
        assert "blob_key" not in detail.text
        assert listing.status_code == 200
        assert listing.json()["next_cursor"] == "cursor_2"
        assert service.calls[-1][1]["kind"] == ArtifactKind.CODE

    def test_promotion_literal_route_is_not_shadowed(self) -> None:
        service = FakeArtifactService()
        response = self.client(service).post(
            "/v1/agent/artifacts:promote",
            headers=self.headers(idempotency=True),
            json={
                "run_id": self.RUN,
                "source_ref": "message://msg_1",
                "kind": "document",
                "title": "Promoted notes",
            },
        )

        assert response.status_code == 201
        assert response.json()["replayed"] is True
        assert service.calls[0][0] == "promote"
        request = service.calls[0][1][1]
        assert request.source_ref == "message://msg_1"
        assert request.idempotency_key == "idem_artifact_1"

    def test_delete_forwards_only_verified_scope_and_idempotency(self) -> None:
        service = FakeArtifactService()
        response = self.client(service).delete(
            f"/v1/agent/artifacts/{self.ARTIFACT_ID}",
            headers=self.headers(idempotency=True),
        )
        assert response.status_code == 204
        call = service.calls[0][1]
        assert call == {
            "org_id": self.ORG,
            "user_id": self.USER,
            "artifact_id": self.ARTIFACT_ID,
            "idempotency_key": "idem_artifact_1",
        }

    def test_foreign_missing_artifact_is_indistinguishable_404(self) -> None:
        service = FakeArtifactService()
        service.error = ArtifactNotFoundError()
        response = self.client(service).get(
            f"/v1/agent/artifacts/{self.ARTIFACT_ID}",
            headers=self.headers(),
        )
        assert response.status_code == 404
        assert response.json() == {"detail": "Artifact was not found for this scope."}

    def test_malformed_artifact_cursor_is_safe_422(self) -> None:
        service = FakeArtifactService()
        service.error = ArtifactInvalidCursorError()
        response = self.client(service).get(
            f"/v1/agent/runs/{self.RUN}/artifacts?cursor=%25%25%25bad",
            headers=self.headers(),
        )
        assert response.status_code == 422
        assert response.json() == {"detail": "Artifact cursor is invalid."}


class TestArtifactMultipartRoutes(ArtifactRouteMixin):
    def test_create_streams_content_and_server_sets_user_author(self) -> None:
        service = FakeArtifactService()
        response = self.client(service).post(
            f"/v1/agent/runs/{self.RUN}/artifacts",
            headers=self.headers(idempotency=True),
            data={
                "kind": "code",
                "title": "demo.ts",
                "media_type": "text/typescript",
                "suggested_filename": "demo.ts",
            },
            files={"content": ("demo.ts", self.CONTENT, "text/typescript")},
        )

        assert response.status_code == 201, response.text
        org_id, user_id, request, provenance, content = service.calls[0][1]
        assert (org_id, user_id) == (self.ORG, self.USER)
        assert request.run_id == self.RUN
        assert not hasattr(request, "author")
        assert not hasattr(request, "source_ref")
        assert provenance.author == ArtifactAuthor.USER
        assert provenance.source_ref is None
        assert request.idempotency_key == "idem_artifact_1"
        assert content == self.CONTENT

    def test_revision_streams_content_with_explicit_parent(self) -> None:
        service = FakeArtifactService()
        response = self.client(service).post(
            f"/v1/agent/artifacts/{self.ARTIFACT_ID}/revisions",
            headers=self.headers(idempotency=True),
            data={"parent_revision": "1"},
            files={"content": ("demo.ts", b"revision two", "text/typescript")},
        )

        assert response.status_code == 201, response.text
        assert service.calls[0][0] == "get"
        _, request, provenance, content = service.calls[1][1]
        assert request.parent_revision == 1
        assert not hasattr(request, "author")
        assert not hasattr(request, "source_ref")
        assert provenance.author == ArtifactAuthor.USER
        assert provenance.source_ref is None
        assert content == b"revision two"
        assert response.json()["artifact"]["current_revision"] == 2

    def test_missing_content_is_422(self) -> None:
        response = self.client(FakeArtifactService()).post(
            f"/v1/agent/runs/{self.RUN}/artifacts",
            headers=self.headers(idempotency=True),
            files=[
                ("kind", (None, "code")),
                ("title", (None, "x")),
                ("media_type", (None, "text/plain")),
            ],
        )
        assert response.status_code == 422
        assert response.json()["detail"] == "Multipart field `content` is required."

    @pytest.mark.parametrize("declared_length", [None, "8"])
    def test_absolute_file_cap_ignores_missing_or_lying_content_length(
        self,
        monkeypatch: pytest.MonkeyPatch,
        declared_length: str | None,
    ) -> None:
        monkeypatch.setattr(
            ArtifactRoutes.Multipart,
            "KIND_FILE_LIMITS",
            {"code": 16},
        )
        monkeypatch.setattr(ArtifactRoutes.Multipart, "MAXIMUM_FILE_BYTES", 1024)
        service = FakeArtifactService()
        boundary = "bounded-cap"
        body = self.multipart_body(b"x" * 17, boundary=boundary)
        split = len(body) // 4

        def chunks():
            yield body[:split]
            yield body[split : split * 2]
            yield body[split * 2 : split * 3]
            yield body[split * 3 :]

        headers = {
            **self.headers(idempotency=True),
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        }
        if declared_length is not None:
            headers["Content-Length"] = declared_length
        response = self.client(service).post(
            f"/v1/agent/runs/{self.RUN}/artifacts",
            headers=headers,
            content=chunks(),
        )
        assert response.status_code == 413
        assert response.json()["detail"] == (
            "Artifact exceeds the configured size limit."
        )
        assert service.calls == []

    def test_create_rejects_content_before_kind_without_calling_service(self) -> None:
        service = FakeArtifactService()
        boundary = "content-before-kind"
        response = self.client(service).post(
            f"/v1/agent/runs/{self.RUN}/artifacts",
            headers={
                **self.headers(idempotency=True),
                "Content-Type": f"multipart/form-data; boundary={boundary}",
            },
            content=self.multipart_body(
                b"must-not-spool",
                boundary=boundary,
                content_first=True,
            ),
        )
        assert response.status_code == 422
        assert response.json() == {"detail": "Artifact multipart metadata is invalid."}
        assert service.calls == []

    def test_revision_authorizes_before_parsing_request_body(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        service = FakeArtifactService()
        service.error = ArtifactNotFoundError()

        async def fail_if_parsed(*args, **kwargs):
            raise AssertionError("foreign revision body must not be parsed")

        monkeypatch.setattr(ArtifactRoutes, "_multipart", fail_if_parsed)
        response = self.client(service).post(
            f"/v1/agent/artifacts/{self.ARTIFACT_ID}/revisions",
            headers=self.headers(idempotency=True),
            data={"parent_revision": "1"},
            files={"content": ("demo.ts", b"secret bytes", "text/typescript")},
        )
        assert response.status_code == 404
        assert response.json() == {"detail": "Artifact was not found for this scope."}

    @pytest.mark.parametrize("declared_length", [None, "8"])
    def test_revision_receipt_uses_authorized_artifact_kind_cap(
        self,
        monkeypatch: pytest.MonkeyPatch,
        declared_length: str | None,
    ) -> None:
        monkeypatch.setattr(
            ArtifactRoutes.Multipart,
            "KIND_FILE_LIMITS",
            {"code": 16},
        )
        service = FakeArtifactService()
        boundary = "revision-kind-cap"
        body = (
            (
                f"--{boundary}\r\n"
                'Content-Disposition: form-data; name="parent_revision"\r\n\r\n'
                "1\r\n"
                f"--{boundary}\r\n"
                'Content-Disposition: form-data; name="content"; '
                'filename="revision.ts"\r\n'
                "Content-Type: text/typescript\r\n\r\n"
            ).encode()
            + b"x" * 17
            + f"\r\n--{boundary}--\r\n".encode()
        )
        headers = {
            **self.headers(idempotency=True),
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        }
        if declared_length is not None:
            headers["Content-Length"] = declared_length

        response = self.client(service).post(
            f"/v1/agent/artifacts/{self.ARTIFACT_ID}/revisions",
            headers=headers,
            content=body,
        )
        assert response.status_code == 413
        assert response.json() == {
            "detail": "Artifact exceeds the configured size limit."
        }
        assert [name for name, _ in service.calls] == ["get"]

    @pytest.mark.parametrize(
        ("path", "data"),
        [
            (
                f"/v1/agent/runs/{ArtifactRouteMixin.RUN}/artifacts",
                {
                    "kind": "code",
                    "title": "x",
                    "media_type": "text/plain",
                    "author": "system",
                },
            ),
            (
                f"/v1/agent/artifacts/{ArtifactRouteMixin.ARTIFACT_ID}/revisions",
                {
                    "parent_revision": "1",
                    "source_ref": "message://forged",
                },
            ),
        ],
    )
    def test_caller_cannot_supply_authorship_or_provenance(
        self, path: str, data: dict[str, str]
    ) -> None:
        service = FakeArtifactService()
        response = self.client(service).post(
            path,
            headers=self.headers(idempotency=True),
            data=data,
            files={"content": ("x.txt", b"x", "text/plain")},
        )
        assert response.status_code == 422
        assert not {"create", "revise"} & {name for name, _ in service.calls}
        if path.endswith("/revisions"):
            assert [name for name, _ in service.calls] == ["get"]
        else:
            assert service.calls == []


class TestArtifactContentRoute(ArtifactRouteMixin):
    def test_full_download_has_safe_exact_headers(self) -> None:
        response = self.client(FakeArtifactService()).get(
            f"/v1/agent/artifacts/{self.ARTIFACT_ID}/revisions/1/content",
            headers=self.headers(),
        )

        assert response.status_code == 200
        assert response.content == self.CONTENT
        assert response.headers["content-type"] == "text/html"
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["etag"] == f'"{self.DIGEST}"'
        assert response.headers["accept-ranges"] == "bytes"
        disposition = response.headers["content-disposition"]
        assert "\r" not in disposition and "\n" not in disposition
        assert "../" not in disposition

    def test_single_range_is_206_and_exact(self) -> None:
        response = self.client(FakeArtifactService()).get(
            f"/v1/agent/artifacts/{self.ARTIFACT_ID}/revisions/1/content",
            headers={**self.headers(), "Range": "bytes=2-8"},
        )
        assert response.status_code == 206
        assert response.content == self.CONTENT[2:9]
        assert response.headers["content-range"] == (f"bytes 2-8/{len(self.CONTENT)}")
        assert response.headers["content-length"] == "7"

    def test_if_range_mismatch_returns_full_body(self) -> None:
        response = self.client(FakeArtifactService()).get(
            f"/v1/agent/artifacts/{self.ARTIFACT_ID}/revisions/1/content",
            headers={
                **self.headers(),
                "Range": "bytes=0-2",
                "If-Range": '"different"',
            },
        )
        assert response.status_code == 200
        assert response.content == self.CONTENT
        assert "content-range" not in response.headers

    @pytest.mark.parametrize("value", ["bytes=999-", "bytes=0-1,3-4", "items=0-1"])
    def test_invalid_or_unsatisfiable_range_is_416(self, value: str) -> None:
        response = self.client(FakeArtifactService()).get(
            f"/v1/agent/artifacts/{self.ARTIFACT_ID}/revisions/1/content",
            headers={**self.headers(), "Range": value},
        )
        assert response.status_code == 416
        assert response.headers["content-range"] == f"bytes */{len(self.CONTENT)}"

    def test_range_on_non_range_blob_is_416_with_exact_size(self) -> None:
        service = FakeArtifactService()
        service.current = self.record(range_supported=False)
        response = self.client(service).get(
            f"/v1/agent/artifacts/{self.ARTIFACT_ID}/revisions/1/content",
            headers={**self.headers(), "Range": "bytes=0-1"},
        )
        assert response.status_code == 416
        assert response.headers["content-range"] == f"bytes */{len(self.CONTENT)}"

    def test_late_blob_iterator_failure_never_leaks_internal_text(self) -> None:
        service = FakeArtifactService()
        service.stream_error = RuntimeError(
            "secret blob path: /private/storage/tenant/artifact"
        )
        response = self.client(service).get(
            f"/v1/agent/artifacts/{self.ARTIFACT_ID}/revisions/1/content",
            headers=self.headers(),
        )
        assert response.status_code == 200
        assert response.content == self.CONTENT[:5]
        assert b"secret blob path" not in response.content
        assert b"/private/storage" not in response.content


class TestArtifactRouteWiring(ArtifactRouteMixin):
    @staticmethod
    def _artifact_operation_count(paths: dict[str, object]) -> int:
        methods = {"get", "post", "delete", "put", "patch"}
        return sum(
            1
            for path, operations in paths.items()
            if "/artifacts" in path
            for method in operations
            if method in methods
        )

    @pytest.mark.parametrize("flag", [None, "false"])
    def test_unset_or_off_preserves_route_table_and_returns_route_404(
        self,
        flag: str | None,
    ) -> None:
        client = self.real_client(
            FakeArtifactService(),
            artifact_effects_v2=flag,
        )
        paths = client.get("/openapi.json").json()["paths"]
        response = client.get(
            f"/v1/agent/artifacts/{self.ARTIFACT_ID}",
            headers=self.headers(),
        )

        assert self._artifact_operation_count(paths) == 0
        assert response.status_code == 404

    def test_real_app_registers_all_routes_and_injects_service(self) -> None:
        service = FakeArtifactService()
        client = self.real_client(service)
        paths = client.get("/openapi.json").json()["paths"]
        assert self._artifact_operation_count(paths) == 8
        assert "post" in paths["/v1/agent/runs/{run_id}/artifacts"]
        assert "get" in paths["/v1/agent/runs/{run_id}/artifacts"]
        assert "post" in paths["/v1/agent/artifacts:promote"]
        assert (
            "get"
            in paths["/v1/agent/artifacts/{artifact_id}/revisions/{revision}/content"]
        )
        assert client.app.state.artifact_service is service

    def test_real_factory_composes_default_service_from_complete_ports(self) -> None:
        client = self.real_client(
            complete_repository_ports=True,
        )
        assert isinstance(client.app.state.artifact_service, ArtifactService)
        paths = client.get("/openapi.json").json()["paths"]
        assert self._artifact_operation_count(paths) == 8

    def test_real_app_foreign_and_missing_artifacts_share_safe_404(self) -> None:
        client = self.real_client(FakeArtifactService())
        foreign_headers = {
            **self.headers(),
            "x-enterprise-user-id": "user_foreign",
        }
        foreign = client.get(
            f"/v1/agent/artifacts/{self.ARTIFACT_ID}",
            headers=foreign_headers,
        )
        missing = client.get(
            "/v1/agent/artifacts/art_00000000-0000-4000-8000-000000000000",
            headers=self.headers(),
        )
        assert foreign.status_code == 404
        assert missing.status_code == 404
        assert (
            foreign.json()
            == missing.json()
            == {"detail": "Artifact was not found for this scope."}
        )

    @pytest.mark.parametrize(
        ("method", "path", "kwargs"),
        [
            (
                "POST",
                f"/v1/agent/runs/{ArtifactRouteMixin.RUN}/artifacts",
                {"headers": {"Idempotency-Key": "i"}},
            ),
            ("GET", f"/v1/agent/runs/{ArtifactRouteMixin.RUN}/artifacts", {}),
            ("GET", f"/v1/agent/artifacts/{ArtifactRouteMixin.ARTIFACT_ID}", {}),
            (
                "GET",
                f"/v1/agent/artifacts/{ArtifactRouteMixin.ARTIFACT_ID}/revisions/1",
                {},
            ),
            (
                "GET",
                f"/v1/agent/artifacts/{ArtifactRouteMixin.ARTIFACT_ID}"
                "/revisions/1/content",
                {},
            ),
            (
                "POST",
                f"/v1/agent/artifacts/{ArtifactRouteMixin.ARTIFACT_ID}/revisions",
                {"headers": {"Idempotency-Key": "i"}},
            ),
            (
                "POST",
                "/v1/agent/artifacts:promote",
                {
                    "headers": {"Idempotency-Key": "i"},
                    "json": {
                        "run_id": ArtifactRouteMixin.RUN,
                        "source_ref": "message://msg_1",
                        "kind": "document",
                    },
                },
            ),
            (
                "DELETE",
                f"/v1/agent/artifacts/{ArtifactRouteMixin.ARTIFACT_ID}",
                {"headers": {"Idempotency-Key": "i"}},
            ),
        ],
    )
    def test_every_endpoint_requires_identity(
        self,
        method: str,
        path: str,
        kwargs: dict[str, object],
    ) -> None:
        response = self.client(FakeArtifactService()).request(method, path, **kwargs)
        assert response.status_code == 401

    def test_runtime_use_scope_is_enforced(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("RBAC_MODE", "enforce")
        response = self.client(FakeArtifactService()).get(
            f"/v1/agent/artifacts/{self.ARTIFACT_ID}",
            headers={
                "x-enterprise-org-id": self.ORG,
                "x-enterprise-user-id": self.USER,
            },
        )
        assert response.status_code == 403

    def test_missing_service_is_safe_503(self) -> None:
        response = self.client().get(
            f"/v1/agent/artifacts/{self.ARTIFACT_ID}",
            headers=self.headers(),
        )
        assert response.status_code == 503
        assert response.json()["detail"] == "Artifact service is not configured."
