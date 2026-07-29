"""PRD-03 D2 — the execution-mode contract, independent of any operation.

The service tests in ``test_artifact_service`` prove each operation records a
mode. These prove the recorded thing is a typed, durable, server-owned fact:
that ``staged`` is genuinely all anything can produce today, that the row an
auditor reads parses back into the same typed value, and that nothing outside
the service can nominate it.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from agent_runtime.artifacts.contracts import (
    ArtifactAppendCommand,
    ArtifactCreateCommand,
    ArtifactSoftDeleteCommand,
)
from agent_runtime.artifacts.execution_mode import (
    ArtifactExecutionMode,
    ArtifactExecutionModeResolver,
    ArtifactOperation,
    ArtifactOperationAudit,
)
from agent_runtime.artifacts.ports import ArtifactOperationAuditPort
from agent_runtime.surfaces_v2.ledger_models import ArtifactAuthor, ArtifactCausalLane

OCCURRED_AT = datetime(2026, 7, 24, 6, 30, tzinfo=timezone.utc)


class ExecutionModeFixtures:
    ARTIFACT_ID = "art_00000000-0000-4000-8000-000000000001"
    #: Every command carrying the mode, so a new one cannot be added without
    #: deciding whether it has to state the mode it ran under.
    COMMANDS = (ArtifactCreateCommand, ArtifactAppendCommand, ArtifactSoftDeleteCommand)

    @classmethod
    def audit(cls, **overrides: object) -> ArtifactOperationAudit:
        return ArtifactOperationAudit.model_validate(
            {
                "operation": ArtifactOperation.CREATE,
                "execution_mode": ArtifactExecutionMode.STAGED,
                "org_id": "org_1",
                "user_id": "user_1",
                "conversation_id": "conv_1",
                "run_id": "run_1",
                "trace_id": "trace_1",
                "lane": ArtifactCausalLane.RUN,
                "artifact_id": cls.ARTIFACT_ID,
                "revision": 1,
                "author": ArtifactAuthor.MODEL,
                "occurred_at": OCCURRED_AT,
                **overrides,
            }
        )


class TestExecutionModeIsServerDerived(ExecutionModeFixtures):
    @pytest.mark.parametrize("operation", tuple(ArtifactOperation))
    @pytest.mark.parametrize("author", tuple(ArtifactAuthor))
    @pytest.mark.parametrize("lane", tuple(ArtifactCausalLane))
    def test_staged_is_all_anything_can_produce_today(
        self,
        operation: ArtifactOperation,
        author: ArtifactAuthor,
        lane: ArtifactCausalLane,
    ) -> None:
        """Nothing can turn the effect gate off, so nothing ran past it.

        Exhaustive over the resolver's whole input space rather than a sample:
        the claim being pinned is that no combination of operation, author, or
        lane yields anything else — which is what makes ``staged`` an honest
        constant rather than a default nobody checked.
        """

        assert (
            ArtifactExecutionModeResolver.resolve(
                operation=operation, author=author, lane=lane
            )
            is ArtifactExecutionMode.STAGED
        )

    def test_the_enum_already_has_room_for_auto(self) -> None:
        """The mode auto-execute will record under exists before auto-execute does.

        These strings land in durable audit rows, so they are a wire contract an
        exported SIEM row is read against — not free-form labels.
        """

        assert ArtifactExecutionMode.STAGED.value == "staged"
        assert ArtifactExecutionMode.AUTO.value == "auto"
        assert set(ArtifactExecutionMode) == {
            ArtifactExecutionMode.STAGED,
            ArtifactExecutionMode.AUTO,
        }

    @pytest.mark.parametrize("command", ExecutionModeFixtures.COMMANDS)
    def test_a_command_cannot_be_built_without_stating_its_mode(
        self, command: type
    ) -> None:
        """No default, so auto-execute cannot land as a silent ``staged``.

        A defaulted field would let a future ungated write be constructed
        without anyone deciding, and be audited as the gated thing it was not.
        """

        field = command.model_fields["execution_mode"]
        assert field.is_required()
        assert field.annotation is ArtifactExecutionMode

    def test_the_mode_cannot_be_reassigned_once_recorded(self) -> None:
        entry = self.audit()

        with pytest.raises(ValidationError):
            entry.execution_mode = ArtifactExecutionMode.AUTO


class TestExecutionModeSurvivesThePersistedRow(ExecutionModeFixtures):
    def test_an_audit_row_parses_back_into_the_same_typed_operation(self) -> None:
        """An auditor reads rows, not service calls.

        The answer to "was this gated?" has to come back off the row as a typed
        value, not as a string whose meaning someone has to remember.
        """

        entry = self.audit()

        recovered = ArtifactOperationAudit.parse_audit_record(entry.to_audit_record())

        assert recovered == entry
        assert recovered.execution_mode is ArtifactExecutionMode.STAGED
        assert recovered.operation is ArtifactOperation.CREATE
        assert recovered.lane is ArtifactCausalLane.RUN
        assert recovered.author is ArtifactAuthor.MODEL

    def test_the_mode_travels_in_the_metadata_the_adapters_export(self) -> None:
        """Asserted as a dict because this is the shape that reaches storage.

        Tenant identity stays top-level where the adapters keep columns;
        everything domain-specific goes in ``metadata``, which is the object
        they redact, encrypt, and hand to a customer SIEM. A mode filed anywhere
        else would not be exported with the operation it describes.
        """

        row = self.audit().to_audit_record()

        assert row["org_id"] == "org_1"
        assert row["resource_type"] == "artifact"
        assert row["resource_id"] == self.ARTIFACT_ID
        assert row["metadata"]["execution_mode"] == "staged"
        assert row["metadata"]["operation"] == "create"

    @pytest.mark.parametrize("operation", tuple(ArtifactOperation))
    def test_each_operation_is_logged_under_its_own_action(
        self, operation: ArtifactOperation
    ) -> None:
        entry = self.audit(operation=operation, revision=None)

        assert entry.event_type == f"artifact.{operation.value}"
        assert entry.event_type == operation.audit_event_type

    @pytest.mark.parametrize(
        "record",
        (
            None,
            "not-a-row",
            {"org_id": "org_1"},
            {"org_id": "org_1", "metadata": "not-an-object"},
            {"org_id": "org_1", "metadata": {"operation": "create"}},
        ),
    )
    def test_a_row_this_domain_did_not_write_fails_validation(
        self, record: object
    ) -> None:
        """One failure mode for every malformed shape, named by Pydantic."""

        with pytest.raises(ValidationError):
            ArtifactOperationAudit.parse_audit_record(record)

    def test_a_row_claiming_an_unknown_mode_is_refused(self) -> None:
        """A mode nobody defined cannot be smuggled back in through a row.

        Parsing is how an exported row re-enters the domain, so it is an
        untrusted boundary even though this service wrote the row.
        """

        row = self.audit().to_audit_record()
        row["metadata"]["execution_mode"] = "ungated"

        with pytest.raises(ValidationError):
            ArtifactOperationAudit.parse_audit_record(row)


class TestAuditPortMatchesTheRuntimeLog(ExecutionModeFixtures):
    def test_the_runtime_audit_log_satisfies_the_port_unchanged(self) -> None:
        """The seam reuses the signed log rather than opening a second one.

        A private artifact-only audit lane would be one nobody exports, so the
        port is deliberately the signature the runtime stores already expose.
        """

        class RuntimeStoreShape:
            async def write_audit_log(
                self, *, event_type: str, record: object
            ) -> None: ...

        assert isinstance(RuntimeStoreShape(), ArtifactOperationAuditPort)

    def test_a_store_without_an_audit_log_does_not_satisfy_the_port(self) -> None:
        class NoAuditLog:
            async def create_artifact(self, command: object) -> None: ...

        assert not isinstance(NoAuditLog(), ArtifactOperationAuditPort)
