"""Typed, content-safe Operation Gateway failures."""

from __future__ import annotations

from enum import StrEnum


class OperationGatewayErrorCode(StrEnum):
    CONTEXT_UNBOUND = "operation_context_unbound"
    IDENTITY_MISMATCH = "operation_identity_mismatch"
    ARGUMENTS_MISSING = "operation_arguments_missing"
    ARGUMENTS_DIGEST_MISMATCH = "operation_arguments_digest_mismatch"
    IDEMPOTENCY_CONFLICT = "operation_idempotency_conflict"
    INVALID_DESCRIPTOR = "operation_descriptor_invalid"
    ENFORCEMENT_NOT_READY = "operation_enforcement_not_ready"
    STAGE_CAPABILITY_INVALID = "operation_stage_capability_invalid"
    ADAPTER_FAILED = "operation_adapter_failed"
    ARTIFACT_FAILED = "operation_artifact_failed"


class OperationGatewayError(RuntimeError):
    """Base failure carrying only a stable code and safe public text."""

    def __init__(
        self,
        code: OperationGatewayErrorCode,
        safe_message: str,
        *,
        retryable: bool = False,
    ) -> None:
        super().__init__(safe_message)
        self.code = code
        self.safe_message = safe_message
        self.retryable = retryable


class OperationContextUnboundError(OperationGatewayError):
    def __init__(self) -> None:
        super().__init__(
            OperationGatewayErrorCode.CONTEXT_UNBOUND,
            "Operation context is unavailable for this run.",
        )


class OperationIdentityMismatchError(OperationGatewayError):
    def __init__(self) -> None:
        super().__init__(
            OperationGatewayErrorCode.IDENTITY_MISMATCH,
            "Operation identity does not match the active run.",
        )


class OperationArgumentsMissingError(OperationGatewayError):
    def __init__(self) -> None:
        super().__init__(
            OperationGatewayErrorCode.ARGUMENTS_MISSING,
            "Canonical operation arguments are unavailable.",
        )


class OperationArgumentsDigestMismatchError(OperationGatewayError):
    def __init__(self) -> None:
        super().__init__(
            OperationGatewayErrorCode.ARGUMENTS_DIGEST_MISMATCH,
            "Canonical operation arguments do not match their digest.",
        )


class OperationIdempotencyConflictError(OperationGatewayError):
    def __init__(self) -> None:
        super().__init__(
            OperationGatewayErrorCode.IDEMPOTENCY_CONFLICT,
            "Operation id was already used for different arguments.",
        )


class OperationEnforcementNotReadyError(OperationGatewayError):
    def __init__(self) -> None:
        super().__init__(
            OperationGatewayErrorCode.ENFORCEMENT_NOT_READY,
            "Operation Gateway enforcement requires durable canonical arguments "
            "and staged-effect dependencies.",
        )


class OperationStageCapabilityError(OperationGatewayError):
    """Raised when proposal staging did not receive gateway-issued authority."""

    def __init__(self) -> None:
        super().__init__(
            OperationGatewayErrorCode.STAGE_CAPABILITY_INVALID,
            "Operation stage authority is unavailable for this request.",
        )


__all__ = (
    "OperationArgumentsDigestMismatchError",
    "OperationArgumentsMissingError",
    "OperationContextUnboundError",
    "OperationEnforcementNotReadyError",
    "OperationGatewayError",
    "OperationGatewayErrorCode",
    "OperationIdempotencyConflictError",
    "OperationIdentityMismatchError",
    "OperationStageCapabilityError",
)
