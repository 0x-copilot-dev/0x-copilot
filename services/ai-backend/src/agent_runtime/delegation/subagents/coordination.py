"""Pure-domain admission and dependency planning for bounded delegation batches.

This module deliberately stops before dispatch.  It turns compact, model-proposed
requests into deterministic packets and an admitted dependency plan; lifecycle,
persistence, events, and provider execution remain owned by their existing seams.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from enum import StrEnum
import hashlib
import json

from pydantic import Field, PositiveInt, ValidationInfo, field_validator

from agent_runtime.delegation.subagents.contracts import (
    SubagentDefinition,
    SubagentOutputContract,
    SubagentTask,
    SubagentValueNormalizer,
)
from agent_runtime.delegation.subagents.authority import (
    SubagentAuthorityPolicy,
    SubagentCapabilityGrant,
)
from agent_runtime.delegation.subagents.definitions import SubagentPermissionPolicy
from agent_runtime.delegation.subagents.handoff import SubagentHandoffBuilder
from agent_runtime.execution.contracts import AgentRuntimeContext, RuntimeContract

_MAX_DIRECT_CHILDREN = 8
_MAX_EVIDENCE_REFS = 64
_MAX_DEPENDENCIES = 32
_MAX_CONSTRAINTS = 32
_MAX_REFERENCE_LENGTH = 500
_MAX_CONSTRAINT_LENGTH = 1_000
_ADDITIVE_BUDGET_FIELDS = (
    "max_model_turns",
    "max_tool_calls",
    "max_input_tokens",
    "max_output_tokens",
    "max_cost_microusd",
)


class DelegationAdmissionCode(StrEnum):
    """Stable reasons why a delegation batch was not admitted."""

    EMPTY_BATCH = "empty_batch"
    DUPLICATE_DELEGATION_ID = "duplicate_delegation_id"
    DEPTH_LIMIT_EXCEEDED = "depth_limit_exceeded"
    CHILD_LIMIT_EXCEEDED = "child_limit_exceeded"
    TOTAL_BUDGET_EXCEEDED = "total_budget_exceeded"
    DEADLINE_EXCEEDED = "deadline_exceeded"
    UNKNOWN_DEPENDENCY = "unknown_dependency"
    SELF_DEPENDENCY = "self_dependency"
    DEPENDENCY_CYCLE = "dependency_cycle"
    PACKET_TOO_LARGE = "packet_too_large"
    DUPLICATE_SUBAGENT_DEFINITION = "duplicate_subagent_definition"
    SUBAGENT_UNAVAILABLE = "subagent_unavailable"
    AUTHORITY_DENIED = "authority_denied"


class DelegationDispatchMode(StrEnum):
    """Execution authority represented by the foundation plan."""

    SERIAL_DEFAULT = "serial_default"


_ADMISSION_MESSAGES: dict[DelegationAdmissionCode, str] = {
    DelegationAdmissionCode.EMPTY_BATCH: "Delegation batch must contain a child.",
    DelegationAdmissionCode.DUPLICATE_DELEGATION_ID: (
        "Delegation IDs must be unique within a batch."
    ),
    DelegationAdmissionCode.DEPTH_LIMIT_EXCEEDED: (
        "Delegation depth exceeds the configured limit."
    ),
    DelegationAdmissionCode.CHILD_LIMIT_EXCEEDED: (
        "Delegation child count exceeds the configured limit."
    ),
    DelegationAdmissionCode.TOTAL_BUDGET_EXCEEDED: (
        "Delegation batch exceeds the remaining aggregate budget."
    ),
    DelegationAdmissionCode.DEADLINE_EXCEEDED: (
        "Delegation deadline does not fit within the parent run."
    ),
    DelegationAdmissionCode.UNKNOWN_DEPENDENCY: (
        "Delegation dependency is not present in the batch."
    ),
    DelegationAdmissionCode.SELF_DEPENDENCY: ("Delegation cannot depend on itself."),
    DelegationAdmissionCode.DEPENDENCY_CYCLE: (
        "Delegation dependencies contain a cycle."
    ),
    DelegationAdmissionCode.PACKET_TOO_LARGE: (
        "Delegation context packet exceeds the configured size limit."
    ),
    DelegationAdmissionCode.DUPLICATE_SUBAGENT_DEFINITION: (
        "Trusted subagent definitions must have unique names."
    ),
    DelegationAdmissionCode.SUBAGENT_UNAVAILABLE: (
        "Requested subagent is unavailable to this run."
    ),
    DelegationAdmissionCode.AUTHORITY_DENIED: (
        "Parent authority does not permit subagent dispatch."
    ),
}


class DelegationAdmissionError(ValueError):
    """Typed, safe admission failure raised before any child is dispatched."""

    def __init__(
        self,
        code: DelegationAdmissionCode,
        *,
        delegation_id: str | None = None,
    ) -> None:
        self.code = code
        self.delegation_id = delegation_id
        super().__init__(_ADMISSION_MESSAGES[code])


class DelegationBudget(RuntimeContract):
    """A child reservation whose consumable and serial wall budgets are additive."""

    max_model_turns: PositiveInt
    max_tool_calls: int = Field(ge=0)
    max_input_tokens: PositiveInt
    max_output_tokens: PositiveInt
    max_cost_microusd: int = Field(ge=0)
    max_wall_ms: PositiveInt

    @classmethod
    def aggregate(cls, budgets: Sequence["DelegationBudget"]) -> "DelegationBudget":
        """Return the aggregate reservation for a batch.

        Every field adds across children. Model-declared dependencies prove
        ordering only; until F6 issues an independent concurrency admission,
        delegation is conservatively serial.
        """

        if not budgets:
            raise ValueError("at least one delegation budget is required")
        return cls(
            max_model_turns=sum(item.max_model_turns for item in budgets),
            max_tool_calls=sum(item.max_tool_calls for item in budgets),
            max_input_tokens=sum(item.max_input_tokens for item in budgets),
            max_output_tokens=sum(item.max_output_tokens for item in budgets),
            max_cost_microusd=sum(item.max_cost_microusd for item in budgets),
            max_wall_ms=sum(item.max_wall_ms for item in budgets),
        )

    def fits_within(self, limit: "DelegationBudget") -> bool:
        """Return whether this reservation is a component-wise subset of a limit."""

        return all(
            getattr(self, field_name) <= getattr(limit, field_name)
            for field_name in (*_ADDITIVE_BUDGET_FIELDS, "max_wall_ms")
        )


class DelegationRequest(RuntimeContract):
    """Compact child request that cannot contain parent conversation history."""

    delegation_id: str
    subagent_name: str
    objective: str = Field(min_length=1, max_length=4_000)
    relevant_summary: str = Field(min_length=1, max_length=4_000)
    evidence_refs: tuple[str, ...] = Field(
        default_factory=tuple,
        max_length=_MAX_EVIDENCE_REFS,
    )
    constraints: tuple[str, ...] = Field(
        default_factory=tuple,
        max_length=_MAX_CONSTRAINTS,
    )
    dependency_refs: tuple[str, ...] = Field(
        default_factory=tuple,
        max_length=_MAX_DEPENDENCIES,
    )
    requested_tools: tuple[str, ...] = Field(default_factory=tuple, max_length=64)
    requested_skills: tuple[str, ...] = Field(default_factory=tuple, max_length=64)
    output_contract: SubagentOutputContract = Field(
        default_factory=SubagentOutputContract
    )
    budget: DelegationBudget
    deadline_at: datetime

    @field_validator("delegation_id")
    @classmethod
    def _normalize_delegation_id(cls, value: object) -> str:
        return SubagentValueNormalizer.normalize_id(value, "delegation_id")

    @field_validator("subagent_name")
    @classmethod
    def _normalize_subagent_name(cls, value: object) -> str:
        return SubagentValueNormalizer.normalize_slug(value, "subagent_name")

    @field_validator("objective", "relevant_summary")
    @classmethod
    def _normalize_task_text(cls, value: object, info: ValidationInfo) -> str:
        return SubagentValueNormalizer.normalize_nonempty_string(
            value,
            info.field_name,
        )

    @field_validator("requested_tools", "requested_skills", mode="before")
    @classmethod
    def _normalize_requested_capabilities(
        cls,
        value: object,
        info: ValidationInfo,
    ) -> tuple[str, ...]:
        normalized = tuple(
            SubagentValueNormalizer.normalize_slug(item, info.field_name)
            for item in SubagentValueNormalizer.coerce_iterable(value, info.field_name)
        )
        if len(normalized) != len(set(normalized)):
            raise ValueError(f"{info.field_name} must not contain duplicates")
        return tuple(sorted(normalized))

    @field_validator("evidence_refs", "dependency_refs", mode="before")
    @classmethod
    def _normalize_references(
        cls,
        value: object,
        info: ValidationInfo,
    ) -> tuple[str, ...]:
        normalized = tuple(
            SubagentValueNormalizer.normalize_id(item, info.field_name)
            for item in SubagentValueNormalizer.coerce_iterable(value, info.field_name)
        )
        if len(normalized) != len(set(normalized)):
            raise ValueError(f"{info.field_name} must not contain duplicates")
        if any(len(item) > _MAX_REFERENCE_LENGTH for item in normalized):
            raise ValueError(f"{info.field_name} contains an oversized reference")
        return tuple(sorted(normalized))

    @field_validator("constraints", mode="before")
    @classmethod
    def _normalize_constraints(cls, value: object) -> tuple[str, ...]:
        normalized = tuple(
            SubagentValueNormalizer.normalize_nonempty_string(item, "constraints")
            for item in SubagentValueNormalizer.coerce_iterable(value, "constraints")
        )
        if len(normalized) != len(set(normalized)):
            raise ValueError("constraints must not contain duplicates")
        if any(len(item) > _MAX_CONSTRAINT_LENGTH for item in normalized):
            raise ValueError("constraints contains an oversized value")
        return tuple(sorted(normalized))

    @field_validator("deadline_at")
    @classmethod
    def _require_aware_deadline(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("deadline_at must be timezone-aware")
        return value


class DelegationContextPacket(RuntimeContract):
    """Bounded child context containing references instead of raw transcript."""

    objective: str
    evidence_refs: tuple[str, ...]
    constraints: tuple[str, ...]
    output_contract: SubagentOutputContract
    packet_digest: str = Field(pattern=r"^[a-f0-9]{64}$")


class DelegationParentState(RuntimeContract):
    """Server-derived parent limits used for deterministic admission."""

    current_depth: int = Field(default=0, ge=0)
    active_children: int = Field(default=0, ge=0)
    remaining_budget: DelegationBudget
    deadline_at: datetime

    @field_validator("deadline_at")
    @classmethod
    def _require_aware_deadline(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("deadline_at must be timezone-aware")
        return value


class DelegationAdmissionPolicy(RuntimeContract):
    """Local limits for a single parent operation."""

    max_depth: PositiveInt = Field(default=1, le=8)
    max_children: PositiveInt = Field(default=3, le=_MAX_DIRECT_CHILDREN)
    max_packet_bytes: PositiveInt = Field(default=32_768, le=65_536)


class DelegationPlanEntry(RuntimeContract):
    """One admitted child and its model-safe context packet."""

    order: int = Field(ge=0)
    child_depth: PositiveInt
    request: DelegationRequest
    context_packet: DelegationContextPacket
    handoff: SubagentTask


class DelegationPlan(RuntimeContract):
    """Stable serial child plan produced without execution side effects.

    ``dependency_stages`` records model-requested ordering only. It is not a
    parallelism grant; ``dispatch_mode`` has no parallel value. A later F6
    admission may derive safe cohorts without changing this source plan.
    """

    entries: tuple[DelegationPlanEntry, ...]
    dependency_stages: tuple[tuple[str, ...], ...]
    dispatch_order: tuple[str, ...]
    dispatch_mode: DelegationDispatchMode = DelegationDispatchMode.SERIAL_DEFAULT
    reserved_budget: DelegationBudget


class DelegationCoordinator:
    """Compose trusted authority and admit a serial-by-default child plan."""

    def __init__(
        self,
        policy: DelegationAdmissionPolicy | None = None,
        *,
        context: AgentRuntimeContext,
        definitions: Sequence[SubagentDefinition],
        parent_grant: SubagentCapabilityGrant,
        handoff_builder: SubagentHandoffBuilder | None = None,
    ) -> None:
        self._policy = policy or DelegationAdmissionPolicy()
        self._context = context
        self._parent_grant = parent_grant
        self._handoff_builder = handoff_builder or SubagentHandoffBuilder()
        self._definitions = {definition.name: definition for definition in definitions}
        if len(self._definitions) != len(definitions):
            raise DelegationAdmissionError(
                DelegationAdmissionCode.DUPLICATE_SUBAGENT_DEFINITION
            )

    def build_plan(
        self,
        *,
        requests: Sequence[DelegationRequest],
        parent_state: DelegationParentState,
        now: datetime,
    ) -> DelegationPlan:
        """Validate a batch and return a deterministic, execution-free DAG plan."""

        self._require_aware_now(now)
        request_tuple = tuple(requests)
        if not request_tuple:
            raise DelegationAdmissionError(DelegationAdmissionCode.EMPTY_BATCH)

        child_depth = parent_state.current_depth + 1
        if child_depth > self._policy.max_depth:
            raise DelegationAdmissionError(DelegationAdmissionCode.DEPTH_LIMIT_EXCEEDED)
        if (
            parent_state.active_children + len(request_tuple)
            > self._policy.max_children
        ):
            raise DelegationAdmissionError(DelegationAdmissionCode.CHILD_LIMIT_EXCEEDED)

        by_id = {request.delegation_id: request for request in request_tuple}
        if len(by_id) != len(request_tuple):
            raise DelegationAdmissionError(
                DelegationAdmissionCode.DUPLICATE_DELEGATION_ID
            )

        reserved_budget = DelegationBudget.aggregate(
            tuple(request.budget for request in request_tuple)
        )
        if not reserved_budget.fits_within(parent_state.remaining_budget):
            raise DelegationAdmissionError(
                DelegationAdmissionCode.TOTAL_BUDGET_EXCEEDED
            )

        packets: dict[str, DelegationContextPacket] = {}
        handoffs: dict[str, SubagentTask] = {}
        for request in request_tuple:
            self._validate_deadline(
                request=request,
                parent_state=parent_state,
                now=now,
            )
            self._validate_dependencies(request=request, known_ids=frozenset(by_id))
            packets[request.delegation_id] = self._build_packet(request)
            handoffs[request.delegation_id] = self._build_handoff(request)

        dependency_stages = self._topological_stages(by_id)
        stable_ids = tuple(
            delegation_id for stage in dependency_stages for delegation_id in stage
        )
        self._validate_serial_schedule(
            stable_ids=stable_ids,
            by_id=by_id,
            parent_state=parent_state,
            now=now,
        )
        return DelegationPlan(
            entries=tuple(
                DelegationPlanEntry(
                    order=order,
                    child_depth=child_depth,
                    request=by_id[delegation_id],
                    context_packet=packets[delegation_id],
                    handoff=handoffs[delegation_id],
                )
                for order, delegation_id in enumerate(stable_ids)
            ),
            dependency_stages=dependency_stages,
            dispatch_order=stable_ids,
            reserved_budget=reserved_budget,
        )

    def _build_handoff(self, request: DelegationRequest) -> SubagentTask:
        definition = self._definitions.get(request.subagent_name)
        if definition is None or not SubagentPermissionPolicy.is_definition_visible(
            self._context,
            definition,
        ):
            raise DelegationAdmissionError(
                DelegationAdmissionCode.SUBAGENT_UNAVAILABLE,
                delegation_id=request.delegation_id,
            )
        handoff = self._handoff_builder.build_task(
            context=self._context,
            definition=definition,
            objective=request.objective,
            relevant_summary=request.relevant_summary,
            constraints=request.constraints,
            requested_tools=request.requested_tools,
            requested_skills=request.requested_skills,
            output_contract=request.output_contract,
            parent_grant=self._parent_grant,
        )
        if (
            SubagentAuthorityPolicy.DISPATCH_CAPABILITY
            not in handoff.authority.capabilities
        ):
            raise DelegationAdmissionError(
                DelegationAdmissionCode.AUTHORITY_DENIED,
                delegation_id=request.delegation_id,
            )
        return handoff

    def _build_packet(
        self,
        request: DelegationRequest,
    ) -> DelegationContextPacket:
        payload = {
            "objective": request.objective,
            "evidence_refs": request.evidence_refs,
            "constraints": request.constraints,
            "output_contract": request.output_contract.model_dump(mode="json"),
        }
        canonical_payload = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        if len(canonical_payload) > self._policy.max_packet_bytes:
            raise DelegationAdmissionError(
                DelegationAdmissionCode.PACKET_TOO_LARGE,
                delegation_id=request.delegation_id,
            )
        return DelegationContextPacket(
            **payload,
            packet_digest=hashlib.sha256(canonical_payload).hexdigest(),
        )

    @staticmethod
    def _require_aware_now(now: datetime) -> None:
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("now must be timezone-aware")

    @staticmethod
    def _validate_deadline(
        *,
        request: DelegationRequest,
        parent_state: DelegationParentState,
        now: datetime,
    ) -> None:
        available_ms = int((request.deadline_at - now).total_seconds() * 1_000)
        if (
            parent_state.deadline_at <= now
            or request.deadline_at <= now
            or request.deadline_at > parent_state.deadline_at
            or request.budget.max_wall_ms > available_ms
        ):
            raise DelegationAdmissionError(
                DelegationAdmissionCode.DEADLINE_EXCEEDED,
                delegation_id=request.delegation_id,
            )

    @staticmethod
    def _validate_serial_schedule(
        *,
        stable_ids: tuple[str, ...],
        by_id: dict[str, DelegationRequest],
        parent_state: DelegationParentState,
        now: datetime,
    ) -> None:
        elapsed_ms = 0
        for delegation_id in stable_ids:
            request = by_id[delegation_id]
            elapsed_ms += request.budget.max_wall_ms
            request_available_ms = int(
                (request.deadline_at - now).total_seconds() * 1_000
            )
            parent_available_ms = int(
                (parent_state.deadline_at - now).total_seconds() * 1_000
            )
            if elapsed_ms > request_available_ms or elapsed_ms > parent_available_ms:
                raise DelegationAdmissionError(
                    DelegationAdmissionCode.DEADLINE_EXCEEDED,
                    delegation_id=delegation_id,
                )

    @staticmethod
    def _validate_dependencies(
        *,
        request: DelegationRequest,
        known_ids: frozenset[str],
    ) -> None:
        for dependency_id in request.dependency_refs:
            if dependency_id == request.delegation_id:
                raise DelegationAdmissionError(
                    DelegationAdmissionCode.SELF_DEPENDENCY,
                    delegation_id=request.delegation_id,
                )
            if dependency_id not in known_ids:
                raise DelegationAdmissionError(
                    DelegationAdmissionCode.UNKNOWN_DEPENDENCY,
                    delegation_id=request.delegation_id,
                )

    @staticmethod
    def _topological_stages(
        by_id: dict[str, DelegationRequest],
    ) -> tuple[tuple[str, ...], ...]:
        indegree = {
            delegation_id: len(request.dependency_refs)
            for delegation_id, request in by_id.items()
        }
        dependents: dict[str, list[str]] = {
            delegation_id: [] for delegation_id in by_id
        }
        for request in by_id.values():
            for dependency_id in request.dependency_refs:
                dependents[dependency_id].append(request.delegation_id)

        ready = sorted(
            delegation_id for delegation_id, degree in indegree.items() if degree == 0
        )
        stages: list[tuple[str, ...]] = []
        visited = 0
        while ready:
            stage = tuple(ready)
            stages.append(stage)
            visited += len(stage)
            next_ready: list[str] = []
            for delegation_id in stage:
                for dependent_id in dependents[delegation_id]:
                    indegree[dependent_id] -= 1
                    if indegree[dependent_id] == 0:
                        next_ready.append(dependent_id)
            ready = sorted(next_ready)

        if visited != len(by_id):
            raise DelegationAdmissionError(DelegationAdmissionCode.DEPENDENCY_CYCLE)
        return tuple(stages)
