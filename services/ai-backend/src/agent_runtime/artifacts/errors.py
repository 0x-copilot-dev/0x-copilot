"""Typed, content-safe artifact repository failures."""

from __future__ import annotations

from enum import StrEnum


class ArtifactErrorCode(StrEnum):
    NOT_FOUND = "artifact_not_found"
    CONFLICT = "artifact_conflict"
    IDEMPOTENCY_CONFLICT = "artifact_idempotency_conflict"
    TOO_LARGE = "artifact_too_large"
    DIGEST_MISMATCH = "artifact_digest_mismatch"
    INVALID_SOURCE = "artifact_invalid_source"
    BLOB_UNAVAILABLE = "artifact_blob_unavailable"
    RANGE_NOT_SATISFIABLE = "artifact_range_not_satisfiable"
    STORAGE_FAILURE = "artifact_storage_failure"


class _Messages:
    NOT_FOUND = "Artifact was not found for this scope."
    CONFLICT = "Artifact changed since the requested revision."
    IDEMPOTENCY_CONFLICT = "Idempotency key was already used for different content."
    TOO_LARGE = "Artifact exceeds the configured size limit."
    DIGEST_MISMATCH = "Artifact content did not match the declared digest."
    INVALID_SOURCE = "Artifact source is not available for this scope."
    BLOB_UNAVAILABLE = "Artifact content is temporarily unavailable."
    RANGE_NOT_SATISFIABLE = "Requested byte range is not satisfiable."
    STORAGE_FAILURE = "Artifact storage is temporarily unavailable."


class ArtifactError(RuntimeError):
    """Base error carrying only a stable code and safe public message."""

    def __init__(
        self,
        code: ArtifactErrorCode,
        safe_message: str,
        *,
        retryable: bool = False,
    ) -> None:
        super().__init__(safe_message)
        self.code = code
        self.safe_message = safe_message
        self.retryable = retryable


class ArtifactNotFoundError(ArtifactError):
    def __init__(self) -> None:
        super().__init__(ArtifactErrorCode.NOT_FOUND, _Messages.NOT_FOUND)


class ArtifactConflictError(ArtifactError):
    def __init__(self) -> None:
        super().__init__(ArtifactErrorCode.CONFLICT, _Messages.CONFLICT)


class ArtifactIdempotencyConflictError(ArtifactError):
    def __init__(self) -> None:
        super().__init__(
            ArtifactErrorCode.IDEMPOTENCY_CONFLICT,
            _Messages.IDEMPOTENCY_CONFLICT,
        )


class ArtifactTooLargeError(ArtifactError):
    def __init__(self) -> None:
        super().__init__(ArtifactErrorCode.TOO_LARGE, _Messages.TOO_LARGE)


class ArtifactDigestMismatchError(ArtifactError):
    def __init__(self) -> None:
        super().__init__(
            ArtifactErrorCode.DIGEST_MISMATCH,
            _Messages.DIGEST_MISMATCH,
        )


class ArtifactInvalidSourceError(ArtifactError):
    def __init__(self) -> None:
        super().__init__(
            ArtifactErrorCode.INVALID_SOURCE,
            _Messages.INVALID_SOURCE,
        )


class ArtifactBlobUnavailableError(ArtifactError):
    def __init__(self) -> None:
        super().__init__(
            ArtifactErrorCode.BLOB_UNAVAILABLE,
            _Messages.BLOB_UNAVAILABLE,
            retryable=True,
        )


class ArtifactRangeError(ArtifactError):
    def __init__(self) -> None:
        super().__init__(
            ArtifactErrorCode.RANGE_NOT_SATISFIABLE,
            _Messages.RANGE_NOT_SATISFIABLE,
        )


class ArtifactStorageError(ArtifactError):
    def __init__(self) -> None:
        super().__init__(
            ArtifactErrorCode.STORAGE_FAILURE,
            _Messages.STORAGE_FAILURE,
            retryable=True,
        )


__all__ = (
    "ArtifactBlobUnavailableError",
    "ArtifactConflictError",
    "ArtifactDigestMismatchError",
    "ArtifactError",
    "ArtifactErrorCode",
    "ArtifactIdempotencyConflictError",
    "ArtifactInvalidSourceError",
    "ArtifactNotFoundError",
    "ArtifactRangeError",
    "ArtifactStorageError",
    "ArtifactTooLargeError",
)
