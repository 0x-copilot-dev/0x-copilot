"""Integration tests for Library upload-grant + finalize + download routes.

Exercises:
* Grant → byte-pump PUT → finalize round trip
* Per-mime size caps + mime-allowlist
* Signed-URL validity (HMAC verified, TTL enforced)
* Cross-tenant rejection (no signed URL handed out)
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend_app.app import create_app
from backend_app.contracts import OrganizationRecord, UserRecord
from backend_app.identity.store import InMemoryIdentityStore
from backend_app.library.blob_store import (
    LocalDiskBlobStore,
    MIME_SIZE_LIMITS,
)
from backend_app.library.upload_routes import InMemoryLibraryRowStore


_LONG_SECRET = "library-upload-test-secret-must-be-32-or-more-characters-long!"


def _identity_store() -> InMemoryIdentityStore:
    store = InMemoryIdentityStore()
    for slug in ("acme", "globex"):
        store.create_organization(
            OrganizationRecord(
                org_id=f"org_{slug}", display_name=slug.title(), slug=slug
            )
        )
    store.create_user(
        UserRecord(
            user_id="usr_alice",
            org_id="org_acme",
            primary_email="alice@acme.com",
            display_name="Alice",
            email_verified_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
    )
    store.create_user(
        UserRecord(
            user_id="usr_bob",
            org_id="org_globex",
            primary_email="bob@globex.com",
            display_name="Bob",
            email_verified_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
    )
    return store


@pytest.fixture
def client(tmp_path: Path) -> tuple[TestClient, LocalDiskBlobStore]:
    blob_store = LocalDiskBlobStore(
        data_dir=tmp_path,
        hmac_secret=_LONG_SECRET,
        base_url="http://testserver",
    )
    row_store = InMemoryLibraryRowStore()
    app = create_app(
        configure_logging_on_create=False,
        configure_telemetry_on_create=False,
        identity_store=_identity_store(),
        library_blob_store=blob_store,
        library_row_store=row_store,
    )
    return TestClient(app), blob_store


def _params(*, org_id: str = "org_acme", user_id: str = "usr_alice") -> dict[str, str]:
    return {"org_id": org_id, "user_id": user_id}


def _extract_token(url: str) -> tuple[str, str]:
    qs = url.split("?", 1)[1]
    parts = dict(part.split("=", 1) for part in qs.split("&"))
    return parts["sig"], parts["exp"]


class TestUploadGrant:
    def test_201_with_signed_url_and_max_size(
        self, client: tuple[TestClient, LocalDiskBlobStore]
    ) -> None:
        c, _ = client
        resp = c.post(
            "/v1/library/files/upload-grant",
            params=_params(),
            json={
                "name": "report.pdf",
                "content_type": "application/pdf",
                "size_bytes_max": 1024,
                "ttl_seconds": 300,
            },
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["item_id"].startswith("lf_")
        assert body["blob_ref"].startswith("lib/files/org_acme/")
        assert body["max_size_bytes"] == 1024
        assert body["upload_url"].startswith("http://testserver/_blobs/")
        # Expires in roughly 5 minutes
        import time

        assert body["expires_at"] - int(time.time()) <= 301

    def test_dataset_grant_uses_dataset_size_table(
        self, client: tuple[TestClient, LocalDiskBlobStore]
    ) -> None:
        c, _ = client
        resp = c.post(
            "/v1/library/datasets/upload-grant",
            params=_params(),
            json={
                "name": "events.csv",
                "content_type": "text/csv",
                "size_bytes_max": 1024 * 1024 * 1024,  # 1 GB, within 2 GB cap
                "ttl_seconds": 300,
            },
        )
        assert resp.status_code == 201, resp.text

    def test_415_on_disallowed_mime(
        self, client: tuple[TestClient, LocalDiskBlobStore]
    ) -> None:
        c, _ = client
        resp = c.post(
            "/v1/library/files/upload-grant",
            params=_params(),
            json={
                "name": "secret.exe",
                "content_type": "application/x-msdownload",
                "size_bytes_max": 1024,
            },
        )
        assert resp.status_code == 415

    def test_413_when_size_over_mime_cap(
        self, client: tuple[TestClient, LocalDiskBlobStore]
    ) -> None:
        c, _ = client
        resp = c.post(
            "/v1/library/files/upload-grant",
            params=_params(),
            json={
                "name": "huge.pdf",
                "content_type": "application/pdf",
                "size_bytes_max": MIME_SIZE_LIMITS["application/pdf"] + 1,
            },
        )
        assert resp.status_code == 413

    def test_grant_is_per_tenant(
        self, client: tuple[TestClient, LocalDiskBlobStore]
    ) -> None:
        c, _ = client
        first = c.post(
            "/v1/library/files/upload-grant",
            params=_params(org_id="org_acme", user_id="usr_alice"),
            json={
                "name": "a.pdf",
                "content_type": "application/pdf",
                "size_bytes_max": 1024,
            },
        ).json()
        second = c.post(
            "/v1/library/files/upload-grant",
            params=_params(org_id="org_globex", user_id="usr_bob"),
            json={
                "name": "b.pdf",
                "content_type": "application/pdf",
                "size_bytes_max": 1024,
            },
        ).json()
        # Refs encode tenant inline so cross-tenant access is impossible
        # without forging the HMAC.
        assert "/org_acme/" in first["blob_ref"]
        assert "/org_globex/" in second["blob_ref"]


class TestUploadRoundTrip:
    def test_grant_then_pump_put_then_finalize(
        self, client: tuple[TestClient, LocalDiskBlobStore]
    ) -> None:
        c, store = client
        # 1. Grant
        grant = c.post(
            "/v1/library/files/upload-grant",
            params=_params(),
            json={
                "name": "doc.pdf",
                "content_type": "application/pdf",
                "size_bytes_max": 4096,
            },
        ).json()
        # 2. PUT bytes to the dev byte-pump using the signed URL the
        #    grant produced. Bytes never go through the API; the pump is
        #    a separate route mounted only when the store is local-disk.
        upload_url = grant["upload_url"]
        # Strip the http://testserver prefix so TestClient handles it as a path
        pump_path = upload_url[len("http://testserver") :]
        payload = b"PDF-bytes-of-some-size"
        put = c.put(
            pump_path,
            content=payload,
            headers={"content-type": "application/pdf"},
        )
        assert put.status_code == 204, put.text
        # 3. Finalize
        finalize = c.post(
            f"/v1/library/files/{grant['item_id']}/finalize",
            params=_params(),
            json={"size_bytes": len(payload)},
        )
        assert finalize.status_code == 200, finalize.text
        body = finalize.json()
        assert body["finalized"] is True
        assert body["size_bytes"] == len(payload)
        assert body["sha256"] is not None

    def test_finalize_fails_if_no_bytes(
        self, client: tuple[TestClient, LocalDiskBlobStore]
    ) -> None:
        c, _ = client
        grant = c.post(
            "/v1/library/files/upload-grant",
            params=_params(),
            json={
                "name": "no.pdf",
                "content_type": "application/pdf",
                "size_bytes_max": 1024,
            },
        ).json()
        finalize = c.post(
            f"/v1/library/files/{grant['item_id']}/finalize",
            params=_params(),
            json={"size_bytes": 5},
        )
        assert finalize.status_code == 409

    def test_finalize_413_when_size_over_grant(
        self, client: tuple[TestClient, LocalDiskBlobStore]
    ) -> None:
        c, _ = client
        grant = c.post(
            "/v1/library/files/upload-grant",
            params=_params(),
            json={
                "name": "x.pdf",
                "content_type": "application/pdf",
                "size_bytes_max": 100,
            },
        ).json()
        finalize = c.post(
            f"/v1/library/files/{grant['item_id']}/finalize",
            params=_params(),
            json={"size_bytes": 1000},
        )
        assert finalize.status_code == 413


class TestDownload:
    def _finalized_item(
        self, c: TestClient, *, name: str = "rt.pdf"
    ) -> dict[str, object]:
        grant = c.post(
            "/v1/library/files/upload-grant",
            params=_params(),
            json={
                "name": name,
                "content_type": "application/pdf",
                "size_bytes_max": 4096,
            },
        ).json()
        upload_url = grant["upload_url"]
        pump_path = upload_url[len("http://testserver") :]
        c.put(pump_path, content=b"hello", headers={"content-type": "application/pdf"})
        c.post(
            f"/v1/library/files/{grant['item_id']}/finalize",
            params=_params(),
            json={"size_bytes": 5},
        )
        return grant

    def test_download_returns_signed_url_json(
        self, client: tuple[TestClient, LocalDiskBlobStore]
    ) -> None:
        c, _ = client
        grant = self._finalized_item(c)
        resp = c.get(
            f"/v1/library/files/{grant['item_id']}/download",
            params=_params(),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["url"].startswith("http://testserver/_blobs/")
        assert "expires_at" in body

    def test_download_redirect_mode_returns_302(
        self, client: tuple[TestClient, LocalDiskBlobStore]
    ) -> None:
        c, _ = client
        grant = self._finalized_item(c)
        # follow_redirects=False so we observe the 302 itself; the URL
        # carries the signed token in the query string.
        resp = c.get(
            f"/v1/library/files/{grant['item_id']}/download",
            params={**_params(), "redirect": 1},
            follow_redirects=False,
        )
        assert resp.status_code == 302
        assert "_blobs" in resp.headers["location"]

    def test_download_409_when_not_finalized(
        self, client: tuple[TestClient, LocalDiskBlobStore]
    ) -> None:
        c, _ = client
        grant = c.post(
            "/v1/library/files/upload-grant",
            params=_params(),
            json={
                "name": "u.pdf",
                "content_type": "application/pdf",
                "size_bytes_max": 1024,
            },
        ).json()
        resp = c.get(
            f"/v1/library/files/{grant['item_id']}/download",
            params=_params(),
        )
        assert resp.status_code == 409


class TestCrossTenantRejection:
    """Hard rule #4 — a tenant can never receive a signed URL for
    another tenant's blob."""

    def test_finalize_404s_for_other_tenant(
        self, client: tuple[TestClient, LocalDiskBlobStore]
    ) -> None:
        c, _ = client
        grant = c.post(
            "/v1/library/files/upload-grant",
            params=_params(org_id="org_acme", user_id="usr_alice"),
            json={
                "name": "a.pdf",
                "content_type": "application/pdf",
                "size_bytes_max": 1024,
            },
        ).json()
        # Bob (org_globex) tries to finalize Alice's item — 404 (we
        # mask existence on purpose).
        resp = c.post(
            f"/v1/library/files/{grant['item_id']}/finalize",
            params=_params(org_id="org_globex", user_id="usr_bob"),
            json={"size_bytes": 5},
        )
        assert resp.status_code == 404

    def test_download_404s_for_other_tenant(
        self, client: tuple[TestClient, LocalDiskBlobStore]
    ) -> None:
        c, _ = client
        # Alice creates + finalizes
        grant = c.post(
            "/v1/library/files/upload-grant",
            params=_params(org_id="org_acme", user_id="usr_alice"),
            json={
                "name": "secret.pdf",
                "content_type": "application/pdf",
                "size_bytes_max": 1024,
            },
        ).json()
        upload_url = grant["upload_url"]
        pump_path = upload_url[len("http://testserver") :]
        c.put(
            pump_path,
            content=b"top secret",
            headers={"content-type": "application/pdf"},
        )
        c.post(
            f"/v1/library/files/{grant['item_id']}/finalize",
            params=_params(org_id="org_acme", user_id="usr_alice"),
            json={"size_bytes": 10},
        )
        # Bob tries to download Alice's blob — must NEVER receive a
        # signed URL.
        resp = c.get(
            f"/v1/library/files/{grant['item_id']}/download",
            params=_params(org_id="org_globex", user_id="usr_bob"),
        )
        assert resp.status_code == 404
        body = resp.json()
        # Crucially, the response body must NOT contain a url field.
        assert "url" not in body

    def test_delete_404s_for_other_tenant(
        self, client: tuple[TestClient, LocalDiskBlobStore]
    ) -> None:
        c, _ = client
        grant = c.post(
            "/v1/library/files/upload-grant",
            params=_params(org_id="org_acme", user_id="usr_alice"),
            json={
                "name": "x.pdf",
                "content_type": "application/pdf",
                "size_bytes_max": 1024,
            },
        ).json()
        resp = c.delete(
            f"/v1/library/files/{grant['item_id']}",
            params=_params(org_id="org_globex", user_id="usr_bob"),
        )
        assert resp.status_code == 404


class TestDeleteCascade:
    def test_delete_removes_row_and_blob(
        self, client: tuple[TestClient, LocalDiskBlobStore]
    ) -> None:
        c, store = client
        grant = c.post(
            "/v1/library/files/upload-grant",
            params=_params(),
            json={
                "name": "delete-me.pdf",
                "content_type": "application/pdf",
                "size_bytes_max": 1024,
            },
        ).json()
        upload_url = grant["upload_url"]
        pump_path = upload_url[len("http://testserver") :]
        c.put(pump_path, content=b"x", headers={"content-type": "application/pdf"})
        c.post(
            f"/v1/library/files/{grant['item_id']}/finalize",
            params=_params(),
            json={"size_bytes": 1},
        )
        # Delete cascades to blob storage
        resp = c.delete(f"/v1/library/files/{grant['item_id']}", params=_params())
        assert resp.status_code == 204
        meta = store.head(blob_ref=grant["blob_ref"], tenant_id="org_acme")
        assert meta.exists is False
        # Idempotent — a second delete on a deleted row is 404 (row
        # gone) but doesn't blow up.
        resp = c.delete(f"/v1/library/files/{grant['item_id']}", params=_params())
        assert resp.status_code == 404


class TestSignedUrlIntegrity:
    def test_tampered_token_rejected_by_byte_pump(
        self, client: tuple[TestClient, LocalDiskBlobStore]
    ) -> None:
        c, _ = client
        grant = c.post(
            "/v1/library/files/upload-grant",
            params=_params(),
            json={
                "name": "t.pdf",
                "content_type": "application/pdf",
                "size_bytes_max": 1024,
            },
        ).json()
        upload_url = grant["upload_url"]
        # Tamper with the signature
        sig, exp = _extract_token(upload_url)
        bad_sig = "0" * len(sig)
        tampered = upload_url.replace(sig, bad_sig)
        pump_path = tampered[len("http://testserver") :]
        resp = c.put(
            pump_path,
            content=b"evil",
            headers={"content-type": "application/pdf"},
        )
        assert resp.status_code == 403
