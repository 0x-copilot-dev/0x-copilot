"""Unit tests for the Library blob-store port + adapters.

Covers the four invariants the sub-PRD §5 calls out: no bytes
through the API, signed-URL TTL cap, HMAC verification, and tenant
isolation enforced inside the blob_ref.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from backend_app.library.blob_store import (
    BlobNotFoundError,
    BlobSizeLimitExceededError,
    BlobStorePort,
    BlobTokenInvalidError,
    DATASET_MIME_SIZE_LIMITS,
    LocalDiskBlobStore,
    MIME_SIZE_LIMITS,
    SIGNED_URL_MAX_TTL_SECONDS,
    build_blob_ref,
    ensure_tenant,
    parse_blob_ref,
    size_limit_for,
)


_SECRET = "test-library-blob-secret-must-be-at-least-32-chars-long!"


def _store(tmp_path: Path) -> LocalDiskBlobStore:
    return LocalDiskBlobStore(
        data_dir=tmp_path,
        hmac_secret=_SECRET,
        base_url="http://localhost:8100",
    )


def _put_via_helper(store: LocalDiskBlobStore, blob_ref: str, payload: bytes) -> None:
    grant = store.presign_upload(
        blob_ref=blob_ref,
        tenant_id=_tenant_of(blob_ref),
        content_type="application/pdf",
        size_bytes_max=len(payload) + 1024,
        ttl_seconds=60,
    )
    # Pull the token + exp from the URL the adapter minted, then call the
    # dev byte-pump entry point directly. Production bytes never pass
    # through the API; tests exercise the same path the dev sidecar
    # would use.
    sig, exp = _extract_token(grant.upload_url)
    store.write_bytes(blob_ref=blob_ref, payload=payload, token=sig, exp=exp)


def _tenant_of(blob_ref: str) -> str:
    _, tenant, _ = parse_blob_ref(blob_ref)
    return tenant


def _extract_token(url: str) -> tuple[str, int]:
    qs = url.split("?", 1)[1]
    parts = dict(part.split("=", 1) for part in qs.split("&"))
    return parts["sig"], int(parts["exp"])


class TestBlobRefGrammar:
    def test_build_blob_ref_canonical(self) -> None:
        ref = build_blob_ref(kind="file", tenant_id="org_acme", blob_id="abc123")
        assert ref == "lib/files/org_acme/abc123"

    def test_dataset_ref_uses_datasets_segment(self) -> None:
        ref = build_blob_ref(kind="dataset", tenant_id="org_acme", blob_id="ds1")
        assert ref == "lib/datasets/org_acme/ds1"
        kind, tenant, blob_id = parse_blob_ref(ref)
        assert kind == "dataset"
        assert tenant == "org_acme"
        assert blob_id == "ds1"

    def test_rejects_path_traversal_tenant(self) -> None:
        with pytest.raises(ValueError):
            build_blob_ref(kind="file", tenant_id="../escape", blob_id="x")

    def test_rejects_path_traversal_blob_id(self) -> None:
        with pytest.raises(ValueError):
            build_blob_ref(kind="file", tenant_id="org_a", blob_id="../escape")

    def test_parse_rejects_malformed_ref(self) -> None:
        with pytest.raises(ValueError):
            parse_blob_ref("not-a-real-ref")
        with pytest.raises(ValueError):
            parse_blob_ref("lib/bogus/org_a/b1")


class TestEnsureTenant:
    def test_passes_when_matching(self) -> None:
        ref = build_blob_ref(kind="file", tenant_id="org_acme", blob_id="x")
        kind, blob_id = ensure_tenant(ref, tenant_id="org_acme")
        assert kind == "file"
        assert blob_id == "x"

    def test_rejects_cross_tenant_ref(self) -> None:
        ref = build_blob_ref(kind="file", tenant_id="org_acme", blob_id="x")
        with pytest.raises(BlobTokenInvalidError):
            ensure_tenant(ref, tenant_id="org_globex")

    def test_rejects_unparseable_ref(self) -> None:
        with pytest.raises(BlobTokenInvalidError):
            ensure_tenant("garbage", tenant_id="org_a")


class TestSizeLimits:
    def test_file_uses_mime_table_when_known(self) -> None:
        assert (
            size_limit_for("file", "application/pdf")
            == MIME_SIZE_LIMITS["application/pdf"]
        )

    def test_file_falls_back_to_default_for_unknown_mime(self) -> None:
        # Default catch-all cap for files
        assert size_limit_for("file", "application/x-mystery") == 100 * 1024 * 1024

    def test_dataset_uses_dataset_table(self) -> None:
        assert (
            size_limit_for("dataset", "text/csv")
            == DATASET_MIME_SIZE_LIMITS["text/csv"]
        )


class TestPresignUpload:
    def test_returns_signed_url_with_short_ttl(self, tmp_path: Path) -> None:
        store = _store(tmp_path)
        ref = build_blob_ref(kind="file", tenant_id="org_a", blob_id="x")
        grant = store.presign_upload(
            blob_ref=ref,
            tenant_id="org_a",
            content_type="application/pdf",
            size_bytes_max=1024,
            ttl_seconds=120,
        )
        assert grant.blob_ref == ref
        assert grant.method == "PUT"
        assert grant.max_size_bytes == 1024
        # TTL is honored (within slack)
        assert grant.expires_at - int(time.time()) <= 121

    def test_refuses_ttl_over_max(self, tmp_path: Path) -> None:
        store = _store(tmp_path)
        ref = build_blob_ref(kind="file", tenant_id="org_a", blob_id="x")
        with pytest.raises(ValueError):
            store.presign_upload(
                blob_ref=ref,
                tenant_id="org_a",
                content_type="application/pdf",
                size_bytes_max=1024,
                ttl_seconds=SIGNED_URL_MAX_TTL_SECONDS + 1,
            )

    def test_refuses_size_over_mime_cap(self, tmp_path: Path) -> None:
        store = _store(tmp_path)
        ref = build_blob_ref(kind="file", tenant_id="org_a", blob_id="x")
        with pytest.raises(BlobSizeLimitExceededError):
            store.presign_upload(
                blob_ref=ref,
                tenant_id="org_a",
                content_type="image/png",
                size_bytes_max=MIME_SIZE_LIMITS["image/png"] + 1,
                ttl_seconds=60,
            )

    def test_refuses_zero_size(self, tmp_path: Path) -> None:
        store = _store(tmp_path)
        ref = build_blob_ref(kind="file", tenant_id="org_a", blob_id="x")
        with pytest.raises(BlobSizeLimitExceededError):
            store.presign_upload(
                blob_ref=ref,
                tenant_id="org_a",
                content_type="application/pdf",
                size_bytes_max=0,
                ttl_seconds=60,
            )

    def test_refuses_cross_tenant_signing(self, tmp_path: Path) -> None:
        store = _store(tmp_path)
        ref = build_blob_ref(kind="file", tenant_id="org_a", blob_id="x")
        with pytest.raises(BlobTokenInvalidError):
            store.presign_upload(
                blob_ref=ref,
                tenant_id="org_b",  # mismatch
                content_type="application/pdf",
                size_bytes_max=1024,
                ttl_seconds=60,
            )


class TestPresignDownload:
    def test_404_when_blob_absent(self, tmp_path: Path) -> None:
        store = _store(tmp_path)
        ref = build_blob_ref(kind="file", tenant_id="org_a", blob_id="absent")
        with pytest.raises(BlobNotFoundError):
            store.presign_download(blob_ref=ref, tenant_id="org_a", ttl_seconds=60)

    def test_round_trip(self, tmp_path: Path) -> None:
        store = _store(tmp_path)
        ref = build_blob_ref(kind="file", tenant_id="org_a", blob_id="rt")
        _put_via_helper(store, ref, b"hello world")
        signed = store.presign_download(blob_ref=ref, tenant_id="org_a", ttl_seconds=60)
        sig, exp = _extract_token(signed.url)
        payload = store.read_bytes(blob_ref=ref, token=sig, exp=exp)
        assert payload == b"hello world"

    def test_cross_tenant_download_rejected(self, tmp_path: Path) -> None:
        store = _store(tmp_path)
        ref = build_blob_ref(kind="file", tenant_id="org_a", blob_id="ct")
        _put_via_helper(store, ref, b"secrets")
        # A caller authenticated to org_b must never receive a signed
        # URL for org_a's blob.
        with pytest.raises(BlobTokenInvalidError):
            store.presign_download(blob_ref=ref, tenant_id="org_b", ttl_seconds=60)


class TestTokenVerification:
    def test_expired_token_rejected(self, tmp_path: Path) -> None:
        store = _store(tmp_path)
        ref = build_blob_ref(kind="file", tenant_id="org_a", blob_id="exp")
        _put_via_helper(store, ref, b"x")
        # Forge an expired token (one second in the past)
        past = int(time.time()) - 1
        sig = store._sign(op="get", blob_ref=ref, exp=past)
        with pytest.raises(BlobTokenInvalidError):
            store.read_bytes(blob_ref=ref, token=sig, exp=past)

    def test_tampered_signature_rejected(self, tmp_path: Path) -> None:
        store = _store(tmp_path)
        ref = build_blob_ref(kind="file", tenant_id="org_a", blob_id="t")
        _put_via_helper(store, ref, b"x")
        signed = store.presign_download(blob_ref=ref, tenant_id="org_a", ttl_seconds=60)
        sig, exp = _extract_token(signed.url)
        bad_sig = "0" * len(sig)
        with pytest.raises(BlobTokenInvalidError):
            store.read_bytes(blob_ref=ref, token=bad_sig, exp=exp)

    def test_op_mismatch_rejected(self, tmp_path: Path) -> None:
        """A PUT token must not be reusable as a GET token."""
        store = _store(tmp_path)
        ref = build_blob_ref(kind="file", tenant_id="org_a", blob_id="op")
        grant = store.presign_upload(
            blob_ref=ref,
            tenant_id="org_a",
            content_type="application/pdf",
            size_bytes_max=1024,
            ttl_seconds=60,
        )
        sig, exp = _extract_token(grant.upload_url)
        with pytest.raises(BlobTokenInvalidError):
            # Trying to use a put-token as a get-token fails the HMAC
            # because the message includes the op label.
            store.read_bytes(blob_ref=ref, token=sig, exp=exp)


class TestHeadAndDelete:
    def test_head_returns_exists_false_when_absent(self, tmp_path: Path) -> None:
        store = _store(tmp_path)
        ref = build_blob_ref(kind="file", tenant_id="org_a", blob_id="a")
        meta = store.head(blob_ref=ref, tenant_id="org_a")
        assert meta.exists is False
        assert meta.size_bytes is None

    def test_head_returns_size_and_sha256_when_present(self, tmp_path: Path) -> None:
        store = _store(tmp_path)
        ref = build_blob_ref(kind="file", tenant_id="org_a", blob_id="h")
        payload = b"hash me"
        _put_via_helper(store, ref, payload)
        meta = store.head(blob_ref=ref, tenant_id="org_a")
        assert meta.exists is True
        assert meta.size_bytes == len(payload)
        assert meta.sha256 is not None

    def test_delete_is_idempotent(self, tmp_path: Path) -> None:
        store = _store(tmp_path)
        ref = build_blob_ref(kind="file", tenant_id="org_a", blob_id="d")
        _put_via_helper(store, ref, b"bye")
        store.delete(blob_ref=ref, tenant_id="org_a")
        # Second delete is a no-op (sub-PRD §5 — cleanup is idempotent)
        store.delete(blob_ref=ref, tenant_id="org_a")

    def test_delete_rejects_cross_tenant(self, tmp_path: Path) -> None:
        store = _store(tmp_path)
        ref = build_blob_ref(kind="file", tenant_id="org_a", blob_id="d2")
        _put_via_helper(store, ref, b"bye")
        with pytest.raises(BlobTokenInvalidError):
            store.delete(blob_ref=ref, tenant_id="org_b")


class TestPortContract:
    def test_local_disk_satisfies_port(self, tmp_path: Path) -> None:
        # Structural check — LocalDiskBlobStore is callable as a BlobStorePort.
        store: BlobStorePort = _store(tmp_path)
        assert hasattr(store, "presign_upload")
        assert hasattr(store, "presign_download")
        assert hasattr(store, "head")
        assert hasattr(store, "delete")
