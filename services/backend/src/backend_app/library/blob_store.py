"""Library blob storage — Protocol port + dev/prod adapters.

Bytes for Library files and datasets live outside the relational store
so the row stays small and so production can swap a managed object
store (S3) in for the on-disk dev adapter without touching the
service layer. This module is the single source of truth for that
contract; it mirrors the ``TokenVault`` (managed-secrets) and
``SourceStorage`` (tier-2 adapter source) ports already shipped in
``backend_app``.

The ports + adapters here promise three correctness invariants the
sub-PRD §5 calls out:

1. **Bytes never proxy through the API.** ``BlobStorePort`` exposes
   only ``presign_upload`` and ``presign_download`` — there is no
   ``put_bytes`` / ``get_bytes`` on the port. Callers always go
   straight to the storage adapter's URL.
2. **Signed URLs are short-lived.** ``SIGNED_URL_MAX_TTL_SECONDS``
   caps the TTL at 60 minutes; both adapters refuse to mint a URL
   that lives longer.
3. **Tenant isolation is enforced inside the blob_ref.** A blob ref
   always carries ``tenant_id`` as a literal path segment. The
   ``ensure_tenant`` helper bails before signing if a caller from
   tenant A asks for a ref scoped to tenant B, so a stolen API
   handle can never produce a download URL for someone else's
   bytes.
4. **HMAC tokens are verified with ``hmac.compare_digest``.** Every
   signed URL the local adapter mints carries ``(blob_ref, expiry,
   op)`` under HMAC-SHA256; the verifier rejects mismatched
   operations, expired tokens, and tampered refs without leaking
   timing.

Production deploys inject ``S3BlobStore`` at ``create_app`` time —
the included class is a thin wrapper around ``boto3.client('s3')
.generate_presigned_url``. boto3 is imported lazily so the dev
image stays slim.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final, Literal, Protocol


# ---------------------------------------------------------------------------
# Per-mime size limits (sub-PRD §5)
# ---------------------------------------------------------------------------

DEFAULT_FILE_MAX_BYTES: Final[int] = 100 * 1024 * 1024  # 100 MB
"""Catch-all ceiling for File uploads when the MIME isn't in the table."""

DEFAULT_DATASET_MAX_BYTES: Final[int] = 2 * 1024 * 1024 * 1024  # 2 GB
"""Catch-all ceiling for Dataset uploads when the MIME isn't in the table."""

SIGNED_URL_MAX_TTL_SECONDS: Final[int] = 60 * 60  # 60 minutes
"""Hard ceiling on signed-URL TTL. Adapters refuse anything longer."""


# Per-PRD §5 table. Keep these conservative; an explicit deny is
# better than a silent OOM downstream.
MIME_SIZE_LIMITS: Final[dict[str, int]] = {
    # Images
    "image/png": 25 * 1024 * 1024,
    "image/jpeg": 25 * 1024 * 1024,
    "image/webp": 25 * 1024 * 1024,
    "image/gif": 25 * 1024 * 1024,
    "image/svg+xml": 1 * 1024 * 1024,
    # Documents
    "application/pdf": 50 * 1024 * 1024,
    "text/plain": 10 * 1024 * 1024,
    "text/markdown": 10 * 1024 * 1024,
    "text/csv": 25 * 1024 * 1024,
    "application/json": 25 * 1024 * 1024,
    # Office docs
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": 50
    * 1024
    * 1024,
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": 50
    * 1024
    * 1024,
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": 100
    * 1024
    * 1024,
    # Archives
    "application/zip": 100 * 1024 * 1024,
    # Binary catch-all (gated by ALLOWED_FILE_MIMES at the route layer)
    "application/octet-stream": 50 * 1024 * 1024,
}

DATASET_MIME_SIZE_LIMITS: Final[dict[str, int]] = {
    "text/csv": 2 * 1024 * 1024 * 1024,
    "application/json": 1 * 1024 * 1024 * 1024,
    "application/x-ndjson": 2 * 1024 * 1024 * 1024,
    "application/jsonl": 2 * 1024 * 1024 * 1024,
    "application/parquet": 2 * 1024 * 1024 * 1024,
    "application/vnd.apache.parquet": 2 * 1024 * 1024 * 1024,
    "application/x-parquet": 2 * 1024 * 1024 * 1024,
}


_BLOB_REF_PATTERN: Final = re.compile(
    r"^lib/(files|datasets)/([A-Za-z0-9_-]{1,64})/([A-Za-z0-9_-]{1,128})$"
)
"""Strict blob_ref grammar: ``lib/<kind>/<tenant_id>/<blob_id>``.

Tenant id is the second-last segment so cross-tenant signing
attempts fail the literal-substring check in ``ensure_tenant``.
"""


# ---------------------------------------------------------------------------
# DTOs
# ---------------------------------------------------------------------------


BlobKind = Literal["file", "dataset"]


@dataclass(frozen=True)
class BlobMeta:
    """Describes a stored blob without exposing the underlying URL form."""

    blob_ref: str
    size_bytes: int | None
    content_type: str | None
    sha256: str | None
    exists: bool


@dataclass(frozen=True)
class SignedUploadGrant:
    """A signed PUT URL plus the metadata the caller must echo back."""

    upload_url: str
    blob_ref: str
    method: str
    headers: dict[str, str] = field(default_factory=dict)
    max_size_bytes: int = 0
    content_type: str | None = None
    expires_at: int = 0
    """Unix seconds at which ``upload_url`` stops being honored."""


@dataclass(frozen=True)
class SignedDownloadUrl:
    """A signed GET URL plus its expiry.

    Routes can either return this verbatim (frontend redirects in JS) or
    issue a 302 to ``url``.
    """

    url: str
    blob_ref: str
    expires_at: int


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class BlobStoreError(RuntimeError):
    """Base error for blob-store failures."""


class BlobNotFoundError(BlobStoreError):
    """Raised when a HEAD / DELETE / DOWNLOAD targets a missing blob."""


class BlobSizeLimitExceededError(BlobStoreError):
    """Raised when an upload grant exceeds the per-mime size cap."""


class BlobMimeNotAllowedError(BlobStoreError):
    """Raised when the requested MIME isn't on the allow-list."""


class BlobTokenInvalidError(BlobStoreError):
    """Raised when a signed URL is tampered / expired / cross-tenant."""


# ---------------------------------------------------------------------------
# Port
# ---------------------------------------------------------------------------


class BlobStorePort(Protocol):
    """Adapter contract for Library blob storage.

    ``blob_ref`` is the stable opaque handle the caller stores in the
    row metadata. The exact form is up to the adapter, but all
    shipped adapters use ``lib/<kind>/<tenant_id>/<blob_id>``.
    """

    def presign_upload(
        self,
        *,
        blob_ref: str,
        tenant_id: str,
        content_type: str,
        size_bytes_max: int,
        ttl_seconds: int = SIGNED_URL_MAX_TTL_SECONDS,
    ) -> SignedUploadGrant:
        """Mint a short-lived signed PUT URL for ``blob_ref``.

        Adapters MUST refuse a ``ttl_seconds`` longer than
        ``SIGNED_URL_MAX_TTL_SECONDS`` and a ``size_bytes_max`` larger
        than the per-mime cap from ``MIME_SIZE_LIMITS`` /
        ``DATASET_MIME_SIZE_LIMITS`` for the kind embedded in
        ``blob_ref``.
        """

    def presign_download(
        self,
        *,
        blob_ref: str,
        tenant_id: str,
        ttl_seconds: int = SIGNED_URL_MAX_TTL_SECONDS,
    ) -> SignedDownloadUrl:
        """Mint a short-lived signed GET URL for ``blob_ref``."""

    def head(self, *, blob_ref: str, tenant_id: str) -> BlobMeta:
        """Return server-side metadata for ``blob_ref``."""

    def delete(self, *, blob_ref: str, tenant_id: str) -> None:
        """Idempotent delete; never raises on missing."""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def build_blob_ref(*, kind: BlobKind, tenant_id: str, blob_id: str) -> str:
    """Construct the canonical blob ref and validate its grammar.

    Used by the route layer when allocating a new row so the row stores
    a ref that survives a future ``ensure_tenant`` audit.
    """

    bucket = "files" if kind == "file" else "datasets"
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", tenant_id or ""):
        raise ValueError("tenant_id is not a safe path segment")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", blob_id or ""):
        raise ValueError("blob_id is not a safe path segment")
    ref = f"lib/{bucket}/{tenant_id}/{blob_id}"
    parse_blob_ref(ref)  # validate
    return ref


def parse_blob_ref(blob_ref: str) -> tuple[BlobKind, str, str]:
    """Return ``(kind, tenant_id, blob_id)`` or raise ``ValueError``."""

    match = _BLOB_REF_PATTERN.fullmatch(blob_ref)
    if match is None:
        raise ValueError(f"invalid blob_ref: {blob_ref!r}")
    bucket, tenant_id, blob_id = match.groups()
    kind: BlobKind = "file" if bucket == "files" else "dataset"
    return kind, tenant_id, blob_id


def ensure_tenant(blob_ref: str, *, tenant_id: str) -> tuple[BlobKind, str]:
    """Verify ``blob_ref`` belongs to ``tenant_id``.

    Raises ``BlobTokenInvalidError`` so the caller can map it to a 403
    without leaking whether the blob exists.
    """

    try:
        kind, ref_tenant, blob_id = parse_blob_ref(blob_ref)
    except ValueError as exc:
        raise BlobTokenInvalidError(str(exc)) from exc
    if not hmac.compare_digest(ref_tenant, tenant_id):
        raise BlobTokenInvalidError("blob_ref tenant mismatch")
    return kind, blob_id


def size_limit_for(kind: BlobKind, content_type: str) -> int:
    """Resolve the per-mime byte cap. Falls back to the kind-level default."""

    table = MIME_SIZE_LIMITS if kind == "file" else DATASET_MIME_SIZE_LIMITS
    return table.get(content_type, _kind_default(kind))


def _kind_default(kind: BlobKind) -> int:
    return DEFAULT_FILE_MAX_BYTES if kind == "file" else DEFAULT_DATASET_MAX_BYTES


def _now() -> int:
    return int(time.time())


def _clamp_ttl(ttl_seconds: int) -> int:
    if ttl_seconds <= 0:
        raise ValueError("ttl_seconds must be positive")
    if ttl_seconds > SIGNED_URL_MAX_TTL_SECONDS:
        raise ValueError(
            f"ttl_seconds={ttl_seconds} exceeds SIGNED_URL_MAX_TTL_SECONDS="
            f"{SIGNED_URL_MAX_TTL_SECONDS}"
        )
    return ttl_seconds


# ---------------------------------------------------------------------------
# LocalDiskBlobStore — dev adapter
# ---------------------------------------------------------------------------


_DEFAULT_DEV_HMAC_SECRET = "dev-only-library-blob-secret-NOT-FOR-PRODUCTION"


class LocalDiskBlobStore:
    """Filesystem-backed blob store with HMAC-signed pseudo URLs.

    Each signed URL has the shape::

        {base_url}/_blobs/{blob_ref}?op=<put|get>&exp=<unix>&sig=<hex>

    where ``sig = hmac_sha256(secret, f"{op}:{blob_ref}:{exp}")``.
    A companion helper (used by the test client + dev byte-pump
    sidecar) calls :meth:`verify_token` to authorize the actual
    upload / download. Verification uses ``hmac.compare_digest`` —
    no timing leak.

    This adapter is dev-only; production deploys inject
    :class:`S3BlobStore` (or another managed adapter) at
    ``create_app`` time. The deployment profile rejects this adapter
    under managed profiles, identical to how ``TokenVaultFactory``
    rejects the local Fernet vault outside dev.
    """

    backend_name = "local_disk"

    def __init__(
        self,
        *,
        data_dir: Path | str,
        hmac_secret: str | None = None,
        base_url: str = "http://localhost:8100",
    ) -> None:
        self._root = Path(data_dir).expanduser().resolve()
        self._root.mkdir(parents=True, exist_ok=True)
        secret = (
            hmac_secret
            or os.environ.get("LIBRARY_BLOB_HMAC_SECRET")
            or _DEFAULT_DEV_HMAC_SECRET
        )
        if len(secret) < 32:
            raise RuntimeError(
                "LIBRARY_BLOB_HMAC_SECRET must be at least 32 characters"
            )
        self._secret = secret.encode("utf-8")
        self._base_url = base_url.rstrip("/")

    @property
    def root(self) -> Path:
        return self._root

    # -- port methods -------------------------------------------------------

    def presign_upload(
        self,
        *,
        blob_ref: str,
        tenant_id: str,
        content_type: str,
        size_bytes_max: int,
        ttl_seconds: int = SIGNED_URL_MAX_TTL_SECONDS,
    ) -> SignedUploadGrant:
        kind, _ = ensure_tenant(blob_ref, tenant_id=tenant_id)
        cap = size_limit_for(kind, content_type)
        if size_bytes_max <= 0:
            raise BlobSizeLimitExceededError("size_bytes_max must be positive")
        if size_bytes_max > cap:
            raise BlobSizeLimitExceededError(
                f"size_bytes_max={size_bytes_max} exceeds per-mime cap={cap} "
                f"for kind={kind!r} content_type={content_type!r}"
            )
        ttl = _clamp_ttl(ttl_seconds)
        expires_at = _now() + ttl
        token = self._sign(op="put", blob_ref=blob_ref, exp=expires_at)
        url = self._build_url(blob_ref, op="put", exp=expires_at, sig=token)
        return SignedUploadGrant(
            upload_url=url,
            blob_ref=blob_ref,
            method="PUT",
            headers={"content-type": content_type},
            max_size_bytes=size_bytes_max,
            content_type=content_type,
            expires_at=expires_at,
        )

    def presign_download(
        self,
        *,
        blob_ref: str,
        tenant_id: str,
        ttl_seconds: int = SIGNED_URL_MAX_TTL_SECONDS,
    ) -> SignedDownloadUrl:
        ensure_tenant(blob_ref, tenant_id=tenant_id)
        # Refuse to mint a download URL for a blob that never finalized;
        # otherwise the consumer would 404 against the byte-pump and
        # the audit trail would imply the bytes existed.
        if not self._absolute(blob_ref).exists():
            raise BlobNotFoundError(blob_ref)
        ttl = _clamp_ttl(ttl_seconds)
        expires_at = _now() + ttl
        token = self._sign(op="get", blob_ref=blob_ref, exp=expires_at)
        url = self._build_url(blob_ref, op="get", exp=expires_at, sig=token)
        return SignedDownloadUrl(url=url, blob_ref=blob_ref, expires_at=expires_at)

    def head(self, *, blob_ref: str, tenant_id: str) -> BlobMeta:
        ensure_tenant(blob_ref, tenant_id=tenant_id)
        path = self._absolute(blob_ref)
        if not path.exists():
            return BlobMeta(
                blob_ref=blob_ref,
                size_bytes=None,
                content_type=None,
                sha256=None,
                exists=False,
            )
        raw = path.read_bytes()
        return BlobMeta(
            blob_ref=blob_ref,
            size_bytes=len(raw),
            content_type=None,
            sha256=hashlib.sha256(raw).hexdigest(),
            exists=True,
        )

    def delete(self, *, blob_ref: str, tenant_id: str) -> None:
        ensure_tenant(blob_ref, tenant_id=tenant_id)
        path = self._absolute(blob_ref)
        if path.exists():
            path.unlink()

    # -- dev byte-pump helpers (NOT part of the port) -----------------------

    def write_bytes(
        self, *, blob_ref: str, payload: bytes, token: str, exp: int
    ) -> None:
        """Used ONLY by the dev byte-pump (and tests).

        The real port doesn't expose this; bytes always go through the
        signed URL. The dev sidecar route that backs the URL calls
        through to this helper after verifying the token.
        """

        self.verify_token(op="put", blob_ref=blob_ref, exp=exp, supplied=token)
        path = self._absolute(blob_ref)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_bytes(payload)
        os.replace(tmp, path)

    def read_bytes(self, *, blob_ref: str, token: str, exp: int) -> bytes:
        """Used ONLY by the dev byte-pump (and tests)."""

        self.verify_token(op="get", blob_ref=blob_ref, exp=exp, supplied=token)
        path = self._absolute(blob_ref)
        if not path.exists():
            raise BlobNotFoundError(blob_ref)
        return path.read_bytes()

    # -- token helpers ------------------------------------------------------

    def _sign(self, *, op: str, blob_ref: str, exp: int) -> str:
        message = f"{op}:{blob_ref}:{exp}".encode("utf-8")
        return hmac.new(self._secret, message, hashlib.sha256).hexdigest()

    def verify_token(self, *, op: str, blob_ref: str, exp: int, supplied: str) -> None:
        """Verify a signed-URL token; raises ``BlobTokenInvalidError`` on fail."""

        if exp < _now():
            raise BlobTokenInvalidError("signed url expired")
        expected = self._sign(op=op, blob_ref=blob_ref, exp=exp)
        if not hmac.compare_digest(expected, supplied):
            raise BlobTokenInvalidError("signed url signature mismatch")
        # Defense-in-depth: also check the grammar so a malformed ref
        # can't reach the filesystem layer.
        parse_blob_ref(blob_ref)

    # -- filesystem helpers -------------------------------------------------

    def _absolute(self, blob_ref: str) -> Path:
        parse_blob_ref(blob_ref)
        absolute = (self._root / blob_ref).resolve()
        # Belt-and-suspenders: refuse paths that escape root even though
        # the regex grammar already blocks ``..``.
        if self._root != absolute and self._root not in absolute.parents:
            raise BlobTokenInvalidError("resolved path escapes data dir")
        return absolute

    def _build_url(self, blob_ref: str, *, op: str, exp: int, sig: str) -> str:
        return f"{self._base_url}/_blobs/{blob_ref}?op={op}&exp={exp}&sig={sig}"


# ---------------------------------------------------------------------------
# S3BlobStore — prod adapter (skeleton)
# ---------------------------------------------------------------------------


class S3BlobStore:
    """boto3-backed adapter for production deploys.

    Wires ``presign_upload`` to ``generate_presigned_url('put_object')``
    with server-side encryption enforced (``ServerSideEncryption='AES256'``
    by default; ``aws:kms`` when ``kms_key_id`` is provided). The
    download URL uses ``generate_presigned_url('get_object')``.

    boto3 is imported lazily so dev images don't pay the cost; the
    deployment profile rejects ``LocalDiskBlobStore`` under managed
    profiles so production never silently ships the dev adapter.
    """

    backend_name = "s3"

    def __init__(
        self,
        *,
        bucket: str,
        region_name: str | None = None,
        kms_key_id: str | None = None,
        s3_client: object | None = None,
    ) -> None:
        if not bucket:
            raise RuntimeError("S3BlobStore requires a bucket name")
        self._bucket = bucket
        self._region_name = region_name
        self._kms_key_id = kms_key_id
        self._client = s3_client or self._build_default_client()

    @classmethod
    def from_env(cls) -> "S3BlobStore":
        bucket = os.environ.get("LIBRARY_BLOB_S3_BUCKET", "").strip()
        if not bucket:
            raise RuntimeError(
                "LIBRARY_BLOB_S3_BUCKET is required for S3BlobStore.from_env()"
            )
        return cls(
            bucket=bucket,
            region_name=os.environ.get("AWS_REGION")
            or os.environ.get("AWS_DEFAULT_REGION"),
            kms_key_id=os.environ.get("LIBRARY_BLOB_S3_KMS_KEY_ID") or None,
        )

    def _build_default_client(self) -> object:
        try:
            import boto3  # type: ignore[import-untyped]
        except ImportError as exc:  # pragma: no cover - optional dep
            raise RuntimeError(
                "boto3 is required for S3BlobStore; install boto3 in the "
                "backend image (or use LocalDiskBlobStore for dev)."
            ) from exc
        kwargs: dict[str, object] = {}
        if self._region_name:
            kwargs["region_name"] = self._region_name
        return boto3.client("s3", **kwargs)

    # -- port methods -------------------------------------------------------

    def presign_upload(
        self,
        *,
        blob_ref: str,
        tenant_id: str,
        content_type: str,
        size_bytes_max: int,
        ttl_seconds: int = SIGNED_URL_MAX_TTL_SECONDS,
    ) -> SignedUploadGrant:
        kind, _ = ensure_tenant(blob_ref, tenant_id=tenant_id)
        cap = size_limit_for(kind, content_type)
        if size_bytes_max <= 0 or size_bytes_max > cap:
            raise BlobSizeLimitExceededError(
                f"size_bytes_max={size_bytes_max} outside [1,{cap}]"
            )
        ttl = _clamp_ttl(ttl_seconds)
        params: dict[str, object] = {
            "Bucket": self._bucket,
            "Key": blob_ref,
            "ContentType": content_type,
        }
        # Always-on SSE; default to AES256 with KMS upgrade when configured.
        if self._kms_key_id:
            params["ServerSideEncryption"] = "aws:kms"
            params["SSEKMSKeyId"] = self._kms_key_id
        else:
            params["ServerSideEncryption"] = "AES256"
        url = self._client.generate_presigned_url(  # type: ignore[attr-defined]
            "put_object",
            Params=params,
            ExpiresIn=ttl,
            HttpMethod="PUT",
        )
        return SignedUploadGrant(
            upload_url=url,
            blob_ref=blob_ref,
            method="PUT",
            headers={"content-type": content_type},
            max_size_bytes=size_bytes_max,
            content_type=content_type,
            expires_at=_now() + ttl,
        )

    def presign_download(
        self,
        *,
        blob_ref: str,
        tenant_id: str,
        ttl_seconds: int = SIGNED_URL_MAX_TTL_SECONDS,
    ) -> SignedDownloadUrl:
        ensure_tenant(blob_ref, tenant_id=tenant_id)
        ttl = _clamp_ttl(ttl_seconds)
        url = self._client.generate_presigned_url(  # type: ignore[attr-defined]
            "get_object",
            Params={"Bucket": self._bucket, "Key": blob_ref},
            ExpiresIn=ttl,
            HttpMethod="GET",
        )
        return SignedDownloadUrl(url=url, blob_ref=blob_ref, expires_at=_now() + ttl)

    def head(self, *, blob_ref: str, tenant_id: str) -> BlobMeta:
        ensure_tenant(blob_ref, tenant_id=tenant_id)
        try:
            response = self._client.head_object(  # type: ignore[attr-defined]
                Bucket=self._bucket, Key=blob_ref
            )
        except Exception as exc:  # pragma: no cover - boto3 client error
            # boto3 raises ClientError on 404. Translate so callers don't
            # need to know about boto3 internals.
            message = str(exc).lower()
            if "404" in message or "not found" in message or "nosuchkey" in message:
                return BlobMeta(
                    blob_ref=blob_ref,
                    size_bytes=None,
                    content_type=None,
                    sha256=None,
                    exists=False,
                )
            raise BlobStoreError(f"S3 head_object failed: {exc}") from exc
        return BlobMeta(
            blob_ref=blob_ref,
            size_bytes=response.get("ContentLength"),
            content_type=response.get("ContentType"),
            sha256=(response.get("Metadata") or {}).get("sha256"),
            exists=True,
        )

    def delete(self, *, blob_ref: str, tenant_id: str) -> None:
        ensure_tenant(blob_ref, tenant_id=tenant_id)
        try:
            self._client.delete_object(  # type: ignore[attr-defined]
                Bucket=self._bucket, Key=blob_ref
            )
        except Exception as exc:  # pragma: no cover - boto3 client error
            # Idempotent: don't raise on missing.
            message = str(exc).lower()
            if "404" in message or "not found" in message or "nosuchkey" in message:
                return
            raise BlobStoreError(f"S3 delete_object failed: {exc}") from exc


__all__ = [
    "BlobKind",
    "BlobMeta",
    "BlobMimeNotAllowedError",
    "BlobNotFoundError",
    "BlobSizeLimitExceededError",
    "BlobStoreError",
    "BlobStorePort",
    "BlobTokenInvalidError",
    "DATASET_MIME_SIZE_LIMITS",
    "DEFAULT_DATASET_MAX_BYTES",
    "DEFAULT_FILE_MAX_BYTES",
    "LocalDiskBlobStore",
    "MIME_SIZE_LIMITS",
    "S3BlobStore",
    "SIGNED_URL_MAX_TTL_SECONDS",
    "SignedDownloadUrl",
    "SignedUploadGrant",
    "build_blob_ref",
    "ensure_tenant",
    "parse_blob_ref",
    "size_limit_for",
]
