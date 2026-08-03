"""The audit sink ``ArtifactServiceComposition`` casts the runtime store into.

The composition hands ``ports.persistence`` to :class:`ArtifactService` as an
``ArtifactOperationAuditPort`` through ``cast``, and a cast type-checks whatever
it is handed. Nothing in the composition fails if that object stops answering to
``write_audit_log``, and the loss would be silent in the worst possible way: a
committed artifact operation with no durable record of the mode it ran under —
exactly what the seam exists to prevent.

These tests are the check the cast cannot perform. They pin the call shape the
port declares against the declared type the cast reads from, prove the object the
composition really passes answers to it at runtime, follow one operation through
the composed service to the signed row an auditor exports, and hold every store
the factory can wire as ``persistence`` to the same shape so a later adapter
cannot drop it quietly.
"""

from __future__ import annotations

import inspect
from typing import get_type_hints

import pytest

from agent_runtime.api.artifact_repository import ArtifactServiceComposition
from agent_runtime.api.ports import PersistencePort
from agent_runtime.artifacts import (
    ArtifactNotFoundError,
    ArtifactProvenance,
    ArtifactService,
)
from agent_runtime.artifacts.contracts import (
    ArtifactCreateRequest,
    ArtifactMutationResult,
)
from agent_runtime.artifacts.errors import ArtifactErrorCode
from agent_runtime.artifacts.execution_mode import (
    ArtifactExecutionMode,
    ArtifactOperation,
    ArtifactOperationAudit,
)
from agent_runtime.artifacts.ports import ArtifactOperationAuditPort
from agent_runtime.execution.contracts import AgentRuntimeContext, ModelConfig
from agent_runtime.surfaces_v2.ledger_models import (
    ArtifactAuthor,
    ArtifactCausalLane,
    ArtifactKind,
)
from runtime_adapters.factory import RuntimeAdapterFactory, RuntimePorts
from runtime_adapters.file import FileRuntimeApiStore
from runtime_adapters.in_memory import InMemoryRuntimeApiStore
from runtime_api.schemas import AgentRunStatus, RunRecord


class ComposedArtifactAuditMixin:
    ORG = "org_audit_wiring"
    USER = "user_audit_wiring"
    CONVERSATION = "conv_audit_wiring"
    RUN = "run_audit_wiring"
    TRACE = "trace_audit_wiring"
    USER_MESSAGE = "msg_audit_wiring"
    CONTENT = b"# composed audit row\n"
    MODEL = "gpt-5.4-mini"
    PROVIDER = "openai"

    #: The concrete stores :class:`RuntimeAdapterFactory` wires as
    #: ``RuntimePorts.persistence`` — one per runtime backend, and therefore the
    #: exact objects the composition's cast is applied to in production.
    RUNTIME_STORES = (
        InMemoryRuntimeApiStore,
        FileRuntimeApiStore,
    )

    AUDIT_PARAMETERS = ("event_type", "record")
    #: Hash-chain columns the runtime log adds while appending. Everything else
    #: in a persisted row has to be exactly what the domain handed it.
    CHAIN_FIELDS = frozenset({"seq", "prev_hash", "signature", "key_version"})

    @staticmethod
    def audit_call_shape(target: object) -> inspect.Signature:
        """Return ``write_audit_log``'s declared shape, ``self`` off for classes.

        Dropping ``self`` lets a protocol, an adapter class, and a live store all
        be compared under one shape instead of three special cases.
        """

        signature = inspect.signature(target.write_audit_log)
        if isinstance(target, type):
            return signature.replace(
                parameters=tuple(signature.parameters.values())[1:]
            )
        return signature

    @classmethod
    def audit_call_contract(
        cls, target: object
    ) -> tuple[tuple[str, object, object], ...]:
        """The name, kind, and resolved type of each ``write_audit_log`` parameter.

        Types come from ``get_type_hints`` rather than from the raw signature:
        whether a module postpones annotation evaluation is a style choice, and a
        comparison that flipped when one module changed it would fail for a reason
        that has nothing to do with this wiring.
        """

        hints = get_type_hints(target.write_audit_log)
        return tuple(
            (name, parameter.kind, hints.get(name))
            for name, parameter in cls.audit_call_shape(target).parameters.items()
        )

    @classmethod
    def assert_accepts_the_audit_call(cls, target: object) -> None:
        """Assert the service's exact keyword-only call is the one this accepts.

        Attribute presence is all ``isinstance`` against a runtime-checkable
        protocol proves, and all the ``cast`` demands. A store that renamed a
        parameter, took it positionally, or stopped being awaitable would satisfy
        both and fail on the first real artifact operation instead.
        """

        contract = cls.audit_call_contract(target)
        assert tuple(name for name, _kind, _type in contract) == cls.AUDIT_PARAMETERS
        assert all(
            kind is inspect.Parameter.KEYWORD_ONLY for _name, kind, _type in contract
        )
        assert inspect.iscoroutinefunction(target.write_audit_log)
        shape = cls.audit_call_shape(target)
        shape.bind(event_type=ArtifactOperation.CREATE.audit_event_type, record={})
        with pytest.raises(TypeError):
            shape.bind(ArtifactOperation.CREATE.audit_event_type, {})

    @classmethod
    def run_record(cls) -> RunRecord:
        """One live run, so the scope resolver the composition builds authorizes."""

        return RunRecord(
            run_id=cls.RUN,
            conversation_id=cls.CONVERSATION,
            org_id=cls.ORG,
            user_id=cls.USER,
            user_message_id=cls.USER_MESSAGE,
            trace_id=cls.TRACE,
            model_provider=cls.PROVIDER,
            model_name=cls.MODEL,
            status=AgentRunStatus.RUNNING,
            runtime_context=AgentRuntimeContext(
                user_id=cls.USER,
                org_id=cls.ORG,
                roles=["employee"],
                run_id=cls.RUN,
                trace_id=cls.TRACE,
                model_profile=ModelConfig(
                    provider=cls.PROVIDER,
                    model_name=cls.MODEL,
                    max_input_tokens=128_000,
                    timeout_seconds=30,
                    temperature=0,
                    supports_streaming=True,
                ),
            ),
        )

    @classmethod
    def composed_ports(cls) -> tuple[InMemoryRuntimeApiStore, RuntimePorts]:
        """Build the real port bundle the API and worker composition roots read.

        Assembled by the factory rather than hand-rolled: the field names the
        composition reaches for through ``getattr`` are part of what is under
        test, and a namespace built here could agree with the composition while
        the factory no longer does.
        """

        store = InMemoryRuntimeApiStore()
        return store, RuntimeAdapterFactory.from_store(store, artifact_effects_v2=True)

    @classmethod
    def composed_service(
        cls,
    ) -> tuple[InMemoryRuntimeApiStore, RuntimePorts, ArtifactService]:
        store, ports = cls.composed_ports()
        store.runs[cls.RUN] = cls.run_record()
        service = ArtifactServiceComposition.build(ports)
        assert service is not None
        return store, ports, service

    @classmethod
    async def create(
        cls,
        service: ArtifactService,
        *,
        idempotency_key: str = "composed-audit-1",
        user_id: str | None = None,
    ) -> ArtifactMutationResult:
        return await service.create_from_bytes(
            org_id=cls.ORG,
            user_id=user_id or cls.USER,
            request=ArtifactCreateRequest(
                run_id=cls.RUN,
                kind=ArtifactKind.DOCUMENT,
                title="Composed audit note",
                media_type="text/markdown",
                idempotency_key=idempotency_key,
            ),
            provenance=ArtifactProvenance(author=ArtifactAuthor.MODEL),
            content=cls.CONTENT,
        )


class TestTheCastTargetSatisfiesTheAuditPort(ComposedArtifactAuditMixin):
    @pytest.mark.asyncio
    async def test_the_object_the_composition_passes_is_a_working_audit_sink(
        self,
    ) -> None:
        """The cast target, checked the two ways the cast itself cannot.

        Structurally, because the port is runtime-checkable; then by actually
        making the call the service makes, because a signature that binds still
        proves nothing about a body that has to accept it.
        """

        store, ports = self.composed_ports()

        assert ports.persistence is store
        assert isinstance(ports.persistence, ArtifactOperationAuditPort)
        self.assert_accepts_the_audit_call(ports.persistence)
        await ports.persistence.write_audit_log(
            event_type=ArtifactOperation.CREATE.audit_event_type,
            record={"org_id": self.ORG},
        )

        assert [event_type for event_type, _ in store.audit_log] == ["artifact.create"]

    def test_the_declared_type_the_cast_reads_from_declares_the_same_call(
        self,
    ) -> None:
        """What makes the cast honest rather than merely convenient.

        The composition casts a ``PersistencePort``, so the two ports agreeing on
        the call is the whole justification: widening one alone would leave the
        cast asserting a shape its source no longer promises, with no compiler
        complaint because a cast silences exactly that.
        """

        assert self.audit_call_contract(PersistencePort) == self.audit_call_contract(
            ArtifactOperationAuditPort
        )

    def test_the_shape_check_refuses_a_sink_the_protocol_alone_accepts(self) -> None:
        """A negative control for the helper the tests above lean on.

        ``isinstance`` sees an attribute named ``write_audit_log``; the service
        calls it with keywords only. This store satisfies the protocol and would
        raise ``TypeError`` on the first committed artifact operation, so a check
        that could not tell the two apart would be worth nothing.
        """

        class PositionalAuditLog:
            async def write_audit_log(self, event_type: str, record: object) -> None:
                """Structurally a sink, but not one the service can call."""

        assert isinstance(PositionalAuditLog(), ArtifactOperationAuditPort)
        with pytest.raises(AssertionError):
            self.assert_accepts_the_audit_call(PositionalAuditLog())


class TestAnOperationThroughTheComposedServiceReachesTheLog(ComposedArtifactAuditMixin):
    @pytest.mark.asyncio
    async def test_creating_an_artifact_appends_the_row_an_auditor_exports(
        self,
    ) -> None:
        """The end-to-end claim: the composed service writes to the signed log.

        Asserted on the persisted row and on the export cursor, not on a call
        having happened — the reason for casting the runtime store instead of
        opening a private artifact lane is that this row leaves for a customer
        SIEM through the same path as approvals, which only the export read can
        show.
        """

        store, _ports, service = self.composed_service()

        created = await self.create(service)

        ((event_type, row),) = store.audit_log
        assert event_type == "artifact.create"
        entry = ArtifactOperationAudit.parse_audit_record(row)
        assert entry.operation is ArtifactOperation.CREATE
        assert entry.execution_mode is ArtifactExecutionMode.STAGED
        assert entry.artifact_id == created.record.artifact.artifact_id
        assert entry.org_id == self.ORG
        assert entry.user_id == self.USER
        assert entry.conversation_id == self.CONVERSATION
        assert entry.run_id == self.RUN
        assert entry.trace_id == self.TRACE
        assert entry.lane is ArtifactCausalLane.RUN
        assert entry.revision == 1
        assert entry.author is ArtifactAuthor.MODEL
        # Nothing between the service and the log rewrote or dropped a field:
        # the stored row is the domain row plus the chain columns, exactly.
        assert set(row) - self.CHAIN_FIELDS == set(entry.to_audit_record())
        assert {key: row[key] for key in entry.to_audit_record()} == (
            entry.to_audit_record()
        )
        assert row["signature"]
        exported = await store.list_audit_log_for_export(after_id=None, limit=10)
        assert [dict(candidate) for candidate in exported] == [row]

    @pytest.mark.asyncio
    async def test_an_idempotent_replay_adds_no_second_row(self) -> None:
        """One committed create, one row — proved against the real adapter.

        The service suppresses the row when the metadata store reports a replay,
        so this is the durable adapter's half of that agreement: an adapter that
        stopped reporting ``replayed`` would put a second create of a
        once-created artifact into a signed chain nobody can correct.
        """

        store, _ports, service = self.composed_service()

        first = await self.create(service, idempotency_key="composed-audit-replay")
        replay = await self.create(service, idempotency_key="composed-audit-replay")

        assert first.replayed is False
        assert replay.replayed is True
        assert replay.record.artifact.artifact_id == first.record.artifact.artifact_id
        assert len(store.audit_log) == 1

    @pytest.mark.asyncio
    async def test_a_refused_operation_records_nothing(self) -> None:
        """A log that only ever states what happened.

        The composed scope resolver reads the same store as the audit sink, so a
        run outside the caller's scope is the honest not-found, and an audit row
        for it would claim an operation that never committed.
        """

        store, _ports, service = self.composed_service()

        with pytest.raises(ArtifactNotFoundError) as captured:
            await self.create(service, user_id="user_foreign")

        assert captured.value.code is ArtifactErrorCode.NOT_FOUND
        assert captured.value.safe_message == "Artifact was not found for this scope."
        assert store.audit_log == []


class TestEveryRuntimeStoreSatisfiesTheAuditPort(ComposedArtifactAuditMixin):
    @pytest.mark.parametrize("store_class", ComposedArtifactAuditMixin.RUNTIME_STORES)
    def test_each_store_the_factory_wires_as_persistence_satisfies_the_port(
        self,
        store_class: type,
    ) -> None:
        """Every backend, so the cast cannot come true for only the tested one.

        Checked on the classes rather than on live stores: the in-memory and file
        adapters own a pool and a directory tree, and the claim here is about the
        shape they declare, which needs neither. ``issubclass`` is available
        because the port is methods-only.
        """

        assert issubclass(store_class, ArtifactOperationAuditPort)
        self.assert_accepts_the_audit_call(store_class)
