"""Exact immutable argument material shared with the worker MCP executor.

The model can reference an approved MCP operation only through a stage.  This
value is produced from the canonical argument store and rechecked by the
worker before it calls a connector; it is not a model-facing transport.
"""

from __future__ import annotations

from pydantic import Field, field_validator, model_validator

from agent_runtime.execution.contracts import JsonObject, RuntimeContract
from agent_runtime.surfaces_v2.canonical_json import (
    CanonicalJsonError,
    canonical_json_sha256,
)
from agent_runtime.surfaces_v2.ledger_models import (
    Sha256Hex,
    validate_immutable_content_ref,
)


class McpEffectMaterial(RuntimeContract):
    """Canonical connector arguments bound to one immutable proposal revision.

    ``proposal_digest`` is normally the digest of ``arguments`` for operation
    argument proposals.  Artifact-backed proposals deliberately differ: their
    proposal digest names the immutable Artifact revision, while
    ``arguments_digest`` proves the connector argument object reconstructed from
    that revision.  Keeping those two facts distinct prevents a transport-only
    body copy from becoming the approval truth.
    """

    target_connector: str = Field(min_length=1, max_length=255)
    target_op: str = Field(min_length=1, max_length=255)
    arguments: JsonObject
    target_ref: str = Field(min_length=1, max_length=2048)
    target_digest: Sha256Hex
    proposal_ref: str = Field(min_length=1, max_length=2048)
    proposal_content_ref: str = Field(min_length=1, max_length=2048)
    proposal_digest: Sha256Hex
    # Optional for wire/backward compatibility with existing canonical-args
    # material, where the proposal itself is the argument object.
    arguments_digest: Sha256Hex | None = None

    @field_validator("target_connector", "target_op")
    @classmethod
    def _stable_operation_name(cls, value: str) -> str:
        if value != value.strip() or "\n" in value or "\r" in value:
            raise ValueError("MCP target names must be stable identifiers")
        return value

    @field_validator("proposal_content_ref")
    @classmethod
    def _immutable_proposal_content(cls, value: str) -> str:
        return validate_immutable_content_ref(value)

    @model_validator(mode="after")
    def _matches_canonical_digest(self) -> "McpEffectMaterial":
        try:
            digest = canonical_json_sha256(self.arguments)
        except CanonicalJsonError as exc:
            raise ValueError("canonical MCP arguments are invalid") from exc
        expected = self.arguments_digest or self.proposal_digest
        if digest != expected:
            raise ValueError("canonical MCP arguments do not match their digest")
        return self


__all__ = ["McpEffectMaterial"]
