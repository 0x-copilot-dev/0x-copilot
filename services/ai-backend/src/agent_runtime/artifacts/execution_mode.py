"""Server-derived execution mode recorded on every artifact-domain operation.

PRD-03 D2.  An auto-send/auto-execute mode is planned that will let a user
switch gating off per tool or per chat.  A gate the user can switch off cannot
carry the audit story on its own: "was this operation gated?" has to be a fact
recorded *when it ran*, not one reconstructed later from whatever the settings
happen to say by then.

That mode does not exist yet.  This module is the recording seam built ahead of
it, so its arrival cannot silently lose the record — every artifact command
already carries the field, and :class:`ArtifactExecutionModeResolver` is the
single place that decides its value.  Today that value is always ``staged``,
which is the truth (nothing can turn the effect gate off) rather than a
placeholder waiting to be filled in.

The value is derived from server-held facts only.  No request contract carries
it, and every request contract forbids unknown fields, so a client or a model
cannot propose the mode its own operation will be audited under.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import ClassVar

from pydantic import Field, PositiveInt

from agent_runtime.execution.contracts import JsonObject, RuntimeContract
from agent_runtime.surfaces_v2.ledger_models import ArtifactAuthor, ArtifactCausalLane


class ArtifactExecutionMode(StrEnum):
    """Whether an artifact operation ran behind the effect gate or past it."""

    #: Egress from this operation is gated: content leaving the system still
    #: has to pass the effect stager, so a user saw or will see it before it
    #: goes anywhere. Every operation runs in this mode today.
    STAGED = "staged"
    #: The user deliberately disabled gating for this tool or chat, so the
    #: operation ran without an approval step. Nothing produces this value yet;
    #: it exists so the auto-execute mode has a name to record itself under
    #: instead of arriving as an untyped afterthought.
    AUTO = "auto"


class ArtifactOperation(StrEnum):
    """The artifact-domain operations that record an execution mode.

    One member per durable route, so a new operation cannot be added without
    deciding what mode it records.
    """

    CREATE = "create"
    PUBLISH = "publish"
    REVISE = "revise"
    PROMOTE = "promote"
    DELETE = "delete"

    @property
    def audit_event_type(self) -> str:
        """The ``action`` this operation is appended to the audit log under."""

        return f"artifact.{self.value}"


class ArtifactExecutionModeResolver:
    """Derives the effective execution mode from server-held facts only.

    The signature is the seam: it names everything the decision is allowed to
    depend on — which operation ran, who authored it, and which causal lane it
    belongs to — and no request, tool argument, or model output is among them.
    """

    @classmethod
    def resolve(
        cls,
        *,
        operation: ArtifactOperation,
        author: ArtifactAuthor,
        lane: ArtifactCausalLane,
    ) -> ArtifactExecutionMode:
        """Return the mode this operation actually ran under.

        No auto-execute mode exists in the service yet — nothing can turn the
        effect gate off — so every artifact operation ran behind it, whoever
        authored it and whatever it was caused by. That makes ``STAGED`` the
        honest answer today rather than a provisional one.

        When the mode ships, this method is the only place that learns to read
        it: callers already record whatever it returns.
        """

        return ArtifactExecutionMode.STAGED


class ArtifactOperationAudit(RuntimeContract):
    """One durable answer to "was this artifact operation gated?".

    Appended to the runtime's HMAC hash-chained audit log after the operation's
    own transaction commits, in the shape every other runtime audit row uses
    (tenant columns plus a ``metadata`` object), so it exports to a customer
    SIEM through the existing path rather than a new one.

    Constructing this does not decide the mode — :class:`ArtifactExecutionModeResolver`
    does, and the caller passes what it returned.
    """

    class Fields:
        ORG_ID = "org_id"
        USER_ID = "user_id"
        ACTOR_TYPE = "actor_type"
        RESOURCE_TYPE = "resource_type"
        RESOURCE_ID = "resource_id"
        RUN_ID = "run_id"
        TRACE_ID = "trace_id"
        OUTCOME = "outcome"
        METADATA = "metadata"
        OPERATION = "operation"
        EXECUTION_MODE = "execution_mode"
        CONVERSATION_ID = "conversation_id"
        LANE = "lane"
        REVISION = "revision"
        OCCURRED_AT = "occurred_at"

    RESOURCE_TYPE: ClassVar[str] = "artifact"
    OUTCOME_SUCCESS: ClassVar[str] = "success"

    operation: ArtifactOperation
    execution_mode: ArtifactExecutionMode
    org_id: str = Field(min_length=1, max_length=255)
    user_id: str = Field(min_length=1, max_length=255)
    conversation_id: str = Field(min_length=1, max_length=255)
    #: Absent for CONVERSATION-lane work and for deletion, neither of which a
    #: run caused. Recording the artifact's creating run instead would name a
    #: run that did not perform this operation.
    run_id: str | None = Field(default=None, min_length=1, max_length=255)
    trace_id: str | None = Field(default=None, min_length=1, max_length=255)
    lane: ArtifactCausalLane
    artifact_id: str = Field(min_length=1, max_length=255)
    revision: PositiveInt | None = None
    author: ArtifactAuthor
    occurred_at: datetime

    @property
    def event_type(self) -> str:
        return self.operation.audit_event_type

    def to_audit_record(self) -> JsonObject:
        """Render the row in the shape ``write_audit_log`` persists.

        Tenant identity and the resource stay top-level because the durable
        adapters store them as columns; everything specific to this domain —
        the execution mode above all — travels in ``metadata``, which is the
        object those adapters redact, encrypt, and export.
        """

        fields = self.Fields
        return {
            fields.ORG_ID: self.org_id,
            fields.USER_ID: self.user_id,
            fields.ACTOR_TYPE: self.author.value,
            fields.RESOURCE_TYPE: self.RESOURCE_TYPE,
            fields.RESOURCE_ID: self.artifact_id,
            fields.RUN_ID: self.run_id,
            fields.TRACE_ID: self.trace_id,
            fields.OUTCOME: self.OUTCOME_SUCCESS,
            fields.METADATA: {
                fields.OPERATION: self.operation.value,
                fields.EXECUTION_MODE: self.execution_mode.value,
                fields.CONVERSATION_ID: self.conversation_id,
                fields.LANE: self.lane.value,
                fields.REVISION: self.revision,
                fields.OCCURRED_AT: self.occurred_at.isoformat(),
            },
        }

    @classmethod
    def parse_audit_record(cls, record: object) -> ArtifactOperationAudit:
        """Recover the typed operation from a persisted audit row.

        An auditor asking "was this gated?" reads rows, not service calls, so
        the answer has to survive the round trip as a typed value rather than
        as a string someone remembers the meaning of.

        A row this domain did not write fails as a ``ValidationError`` naming
        the fields it lacks — one failure mode for every malformed shape, since
        a hand-rolled type check ahead of Pydantic would report the same defect
        two different ways.
        """

        fields = cls.Fields
        row: dict[object, object] = record if isinstance(record, dict) else {}
        raw_metadata = row.get(fields.METADATA)
        metadata: dict[object, object] = (
            raw_metadata if isinstance(raw_metadata, dict) else {}
        )
        return cls.model_validate(
            {
                "operation": metadata.get(fields.OPERATION),
                "execution_mode": metadata.get(fields.EXECUTION_MODE),
                "org_id": row.get(fields.ORG_ID),
                "user_id": row.get(fields.USER_ID),
                "conversation_id": metadata.get(fields.CONVERSATION_ID),
                "run_id": row.get(fields.RUN_ID),
                "trace_id": row.get(fields.TRACE_ID),
                "lane": metadata.get(fields.LANE),
                "artifact_id": row.get(fields.RESOURCE_ID),
                "revision": metadata.get(fields.REVISION),
                "author": row.get(fields.ACTOR_TYPE),
                "occurred_at": metadata.get(fields.OCCURRED_AT),
            }
        )


__all__ = (
    "ArtifactExecutionMode",
    "ArtifactExecutionModeResolver",
    "ArtifactOperation",
    "ArtifactOperationAudit",
)
