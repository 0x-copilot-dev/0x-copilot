"""Safe HTTP error mapping for the runtime API."""

from __future__ import annotations

import logging
from uuid import uuid4

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from starlette import status

from agent_runtime.execution.contracts import (
    JsonObject,
    RuntimeErrorCode,
    RuntimeErrorEnvelope,
)
from agent_runtime.api.constants import Messages
from runtime_api.schemas import ApiErrorResponse


class RuntimeApiError(Exception):
    """Exception carrying a safe API error envelope and HTTP status."""

    def __init__(
        self,
        code: RuntimeErrorCode,
        safe_message: str,
        *,
        http_status: int = status.HTTP_400_BAD_REQUEST,
        retryable: bool = False,
        correlation_id: str | None = None,
        details: JsonObject | None = None,
    ) -> None:
        super().__init__(safe_message)
        self.http_status = http_status
        self.details = details or {}
        self.envelope = RuntimeErrorEnvelope(
            code=code,
            safe_message=safe_message,
            retryable=retryable,
            correlation_id=correlation_id or uuid4().hex,
        )

    def to_response(self) -> ApiErrorResponse:
        """Return the safe response body."""

        return ApiErrorResponse.from_envelope(self.envelope, details=self.details)


class RuntimeApiErrorMapper:
    """Map internal exceptions to safe API responses."""

    @classmethod
    async def handle_runtime_api_error(
        cls,
        _request: Request,
        exc: RuntimeApiError,
    ) -> JSONResponse:
        """Serialize an expected runtime API error."""

        return JSONResponse(
            status_code=exc.http_status,
            content=exc.to_response().model_dump(mode="json"),
        )

    @classmethod
    async def handle_validation_error(
        cls,
        _request: Request,
        exc: ValidationError,
    ) -> JSONResponse:
        """Serialize Pydantic validation errors without raw internals."""

        response = ApiErrorResponse(
            code=RuntimeErrorCode.VALIDATION_ERROR,
            safe_message=Messages.Error.INVALID_REQUEST,
            retryable=False,
            correlation_id=uuid4().hex,
            details={"error_count": exc.error_count()},
        )
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content=response.model_dump(mode="json"),
        )

    @classmethod
    async def handle_request_validation_error(
        cls,
        _request: Request,
        exc: RequestValidationError,
    ) -> JSONResponse:
        """Serialize FastAPI request validation errors safely."""

        response = ApiErrorResponse(
            code=RuntimeErrorCode.VALIDATION_ERROR,
            safe_message=Messages.Error.INVALID_REQUEST,
            retryable=False,
            correlation_id=uuid4().hex,
            details={"error_count": len(exc.errors())},
        )
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content=response.model_dump(mode="json"),
        )

    @classmethod
    async def handle_unexpected_error(
        cls,
        _request: Request,
        _exc: Exception,
    ) -> JSONResponse:
        """Serialize unexpected failures as safe fallback errors."""

        logging.getLogger(__name__).exception(
            "Unhandled error in runtime API", exc_info=_exc
        )
        response = ApiErrorResponse(
            code=RuntimeErrorCode.RUNTIME_FACTORY_ERROR,
            safe_message=Messages.Error.SAFE_FALLBACK,
            retryable=True,
            correlation_id=uuid4().hex,
        )
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=response.model_dump(mode="json"),
        )
