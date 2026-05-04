"""AWS KMS client for ai-backend's field encryption.

Separate from ``encryption.py`` so the boto3 import stays lazy: deploys
that don't enable ``RUNTIME_FIELD_ENCRYPTION=envelope_v1`` never pay the
cost of pulling boto3 into memory.

This is the ai-backend's own adapter — it does NOT import from
``services/backend/src``. The two services run independent KMS adapters
because the monorepo's hard service boundary forbids cross-service Python
imports. See ``packages/service-contracts`` for shared constants only.
"""

from __future__ import annotations


class AwsKmsClient:
    """Wraps boto3's KMS client for envelope encryption.

    ``wrap_data_key`` calls KMS Encrypt; ``unwrap_data_key`` calls
    KMS Decrypt. The returned ciphertext blob is self-describing for AWS
    symmetric CMKs, so ``unwrap`` doesn't strictly need ``key_id``;
    we pass it through anyway as defense-in-depth against ciphertext-swap
    when a fleet rotates between multiple CMKs.
    """

    def __init__(
        self,
        *,
        key_id: str,
        region_name: str | None = None,
        kms_client: object | None = None,
    ) -> None:
        self._key_id = key_id
        self._region_name = region_name
        self._client = kms_client or self._build_default_client()

    def _build_default_client(self) -> object:
        try:
            import boto3  # type: ignore[import-untyped]
        except ImportError as exc:  # pragma: no cover - optional dep
            raise RuntimeError(
                "boto3 is required for RUNTIME_KMS_BACKEND=aws_kms; install "
                "boto3 in the ai-backend image (or set "
                "RUNTIME_FIELD_ENCRYPTION=disabled for dev)."
            ) from exc
        kwargs: dict[str, object] = {}
        if self._region_name:
            kwargs["region_name"] = self._region_name
        return boto3.client("kms", **kwargs)

    def wrap_data_key(self, plaintext_dek: bytes) -> tuple[bytes, str]:
        response = self._client.encrypt(  # type: ignore[attr-defined]
            KeyId=self._key_id, Plaintext=plaintext_dek
        )
        return response["CiphertextBlob"], response.get("KeyId", self._key_id)

    def unwrap_data_key(self, wrapped_dek: bytes, *, key_id: str | None) -> bytes:
        kwargs: dict[str, object] = {"CiphertextBlob": wrapped_dek}
        if key_id:
            kwargs["KeyId"] = key_id
        elif self._key_id:
            kwargs["KeyId"] = self._key_id
        response = self._client.decrypt(**kwargs)  # type: ignore[attr-defined]
        return response["Plaintext"]
