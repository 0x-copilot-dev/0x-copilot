"""Deterministic supervisor-call to subagent-operation identity mapping.

The deep-agent task tool already gives every delegated graph the supervisor's
``tool_call_id``.  This module turns that stable, trusted correlation value
into two canonical operation IDs without ordering heuristics or process-local
FIFO state:

* the parent delegation operation (``builtin.task``); and
* the child-root operation (``subagent.dispatch``).

The same run/call pair therefore always resolves to the same pair of IDs on a
retry or replay, while different concurrent tool calls cannot cross-wire their
operation trees.
"""

from __future__ import annotations

from hashlib import sha256
from typing import Final
from uuid import UUID

from pydantic import Field, field_validator

from agent_runtime.capabilities.operations.context import (
    OperationContext,
    VerifiedOperationIdentity,
)
from agent_runtime.execution.contracts import RuntimeContract
from agent_runtime.surfaces_v2.canonical_json import canonical_json_bytes
from agent_runtime.surfaces_v2.ledger_ids import OperationIdCodec
from agent_runtime.validation import ValueNormalizer

SUPERVISOR_TASK_CALL_ID_KEY: Final = "supervisor_task_call_id"
SUBAGENT_PARENT_OPERATION_ID_KEY: Final = "subagent_parent_operation_id"
SUBAGENT_ROOT_OPERATION_ID_KEY: Final = "subagent_root_operation_id"
SUBAGENT_DELEGATION_OPERATION_ID_KEY: Final = "subagent_delegation_operation_id"


class SubagentOperationLink(RuntimeContract):
    """Verified identity and deterministic operation IDs for one task call."""

    org_id: str = Field(min_length=1, max_length=255)
    user_id: str = Field(min_length=1, max_length=255)
    conversation_id: str = Field(min_length=1, max_length=255)
    run_id: str = Field(min_length=1, max_length=255)
    supervisor_task_call_id: str = Field(min_length=1, max_length=200)
    delegation_operation_id: str = Field(min_length=1, max_length=128)
    child_root_operation_id: str = Field(min_length=1, max_length=128)

    @field_validator("supervisor_task_call_id")
    @classmethod
    def _validate_supervisor_task_call_id(cls, value: object) -> str:
        return ValueNormalizer.normalize_nonempty_string(
            value, "supervisor_task_call_id"
        )

    @field_validator("delegation_operation_id", "child_root_operation_id")
    @classmethod
    def _validate_operation_id(cls, value: object) -> str:
        if not isinstance(value, str):
            raise ValueError("subagent operation id must be a string")
        OperationIdCodec.parse(value)
        return value


class SubagentOperationIdentityFactory:
    """Construct deterministic operation links from trusted runtime identity."""

    _DELEGATION_PURPOSE: Final = "delegation"
    _CHILD_ROOT_PURPOSE: Final = "child-root"

    @classmethod
    def from_identity(
        cls,
        *,
        identity: VerifiedOperationIdentity,
        supervisor_task_call_id: str,
    ) -> SubagentOperationLink:
        """Build the one-to-one mapping for a verified run and task call."""

        call_id = ValueNormalizer.normalize_nonempty_string(
            supervisor_task_call_id, "supervisor_task_call_id"
        )
        return SubagentOperationLink(
            org_id=identity.org_id,
            user_id=identity.user_id,
            conversation_id=identity.conversation_id,
            run_id=identity.run_id,
            supervisor_task_call_id=call_id,
            delegation_operation_id=cls._operation_id(
                run_id=identity.run_id,
                supervisor_task_call_id=call_id,
                purpose=cls._DELEGATION_PURPOSE,
            ),
            child_root_operation_id=cls._operation_id(
                run_id=identity.run_id,
                supervisor_task_call_id=call_id,
                purpose=cls._CHILD_ROOT_PURPOSE,
            ),
        )

    @classmethod
    def for_active_context(
        cls, *, supervisor_task_call_id: str
    ) -> SubagentOperationLink | None:
        """Return the active run's link, or ``None`` outside a run context."""

        context = OperationContext.active()
        if context is None:
            return None
        return cls.from_identity(
            identity=context.identity,
            supervisor_task_call_id=supervisor_task_call_id,
        )

    @staticmethod
    def _operation_id(
        *, run_id: str, supervisor_task_call_id: str, purpose: str
    ) -> str:
        # UUID4-shaped digest bits satisfy the canonical operation-id contract
        # while remaining a deterministic function of *trusted* run identity
        # plus the supervisor tool-call correlation id.
        digest = bytearray(
            sha256(
                canonical_json_bytes(
                    {
                        "run_id": run_id,
                        "supervisor_task_call_id": supervisor_task_call_id,
                        "purpose": purpose,
                    }
                )
            ).digest()[:16]
        )
        digest[6] = (digest[6] & 0x0F) | 0x40
        digest[8] = (digest[8] & 0x3F) | 0x80
        return OperationIdCodec.format(UUID(bytes=bytes(digest)))


__all__ = (
    "SUBAGENT_DELEGATION_OPERATION_ID_KEY",
    "SUBAGENT_PARENT_OPERATION_ID_KEY",
    "SUBAGENT_ROOT_OPERATION_ID_KEY",
    "SUPERVISOR_TASK_CALL_ID_KEY",
    "SubagentOperationIdentityFactory",
    "SubagentOperationLink",
)
