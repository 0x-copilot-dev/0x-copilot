"""Versioned, content-free baseline traces for harness conformance.

The catalog freezes observable runtime journeys without becoming a second event
store. Each trace is an F1 ``TrajectoryManifest``: ordered event metadata plus
payload digests, never prompts, arguments, results, credentials, or answer
text. Runtime changes may intentionally revise the catalog, but cannot silently
move the baseline.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Annotated, ClassVar, Literal

from pydantic import Field, field_validator, model_validator
from pydantic_core import to_jsonable_python

from agent_runtime.execution.contracts import RuntimeContract, StreamEventSource
from agent_runtime.harness_quality.evaluation_contracts import (
    OpaqueId,
    Revision,
    Sha256,
    TrajectoryManifest,
    TrajectoryStep,
)
from agent_runtime.surfaces_v2.canonical_json import canonical_json_sha256
from runtime_api.schemas import RuntimeApiEventType


BASELINE_JOURNEY_IDS = frozenset(
    {
        "approval_resume",
        "cancel",
        "large_tool_result",
        "local_subagent",
        "local_tool_use",
        "mcp_auth",
        "mcp_read",
        "mcp_write_staging",
        "ordinary_chat",
        "provider_error",
        "timeout",
        "workspace_draft",
    }
)


class GoldenTrace(RuntimeContract):
    """One named journey and its immutable redacted trajectory."""

    journey_id: OpaqueId
    task_family: Annotated[str, Field(min_length=1, max_length=80)]
    expected_last_event_type: RuntimeApiEventType
    ordered_steps: tuple[TrajectoryStep, ...]
    trace_digest: Sha256

    @field_validator("ordered_steps", mode="before")
    @classmethod
    def _expand_compact_steps(cls, value: object) -> object:
        """Accept the catalog's compact, reviewable wire representation.

        A compact row is ``[event_type, source, capability_id, payload_digest]``.
        Sequence numbers are positional, eliminating a second order field that
        could disagree with the JSON array.
        """

        if not isinstance(value, Sequence) or isinstance(
            value, (str, bytes, bytearray)
        ):
            return value
        expanded: list[object] = []
        for sequence_no, item in enumerate(value, start=1):
            if isinstance(item, TrajectoryStep):
                expanded.append(item)
                continue
            if isinstance(item, Mapping):
                expanded.append(item)
                continue
            if (
                not isinstance(item, Sequence)
                or isinstance(item, (str, bytes, bytearray))
                or len(item) != 4
            ):
                raise ValueError(
                    "compact golden step must contain event, source, capability, digest"
                )
            event_type, source, capability_id, payload_digest = item
            expanded.append(
                {
                    "sequence_no": sequence_no,
                    "event_type": event_type,
                    "source": source,
                    "capability_id": capability_id,
                    "payload_digest": payload_digest,
                }
            )
        return expanded

    @model_validator(mode="after")
    def _trajectory_matches_journey(self) -> "GoldenTrace":
        steps = self.ordered_steps
        if not steps:
            raise ValueError("golden trace must contain at least one event")
        for expected_sequence, step in enumerate(steps, start=1):
            if step.sequence_no != expected_sequence:
                raise ValueError("golden trace sequence must be contiguous from 1")
            try:
                RuntimeApiEventType(step.event_type)
            except ValueError as exc:
                raise ValueError(
                    f"unsupported runtime event type: {step.event_type}"
                ) from exc
            try:
                StreamEventSource(step.source)
            except ValueError as exc:
                raise ValueError(
                    f"unsupported runtime event source: {step.source}"
                ) from exc
        if steps[-1].event_type != self.expected_last_event_type.value:
            raise ValueError("expected_last_event_type does not match final step")
        expected_digest = self.digest_for(
            journey_id=self.journey_id,
            task_family=self.task_family,
            expected_last_event_type=self.expected_last_event_type,
            ordered_steps=self.ordered_steps,
        )
        if self.trace_digest != expected_digest:
            raise ValueError("trace_digest does not match canonical trace content")
        return self

    @staticmethod
    def digest_for(**values: object) -> str:
        return canonical_json_sha256(
            GoldenTrace._without_empty_task_policy_projection(
                to_jsonable_python(values)
            )
        )

    @staticmethod
    def _without_empty_task_policy_projection(value: object) -> object:
        """Keep pre-F4/F2 compact trace digests stable for absent fields."""

        if isinstance(value, list):
            return [
                GoldenTrace._without_empty_task_policy_projection(item)
                for item in value
            ]
        if not isinstance(value, dict):
            return value
        projection_keys = {
            "policy_record_kind",
            "policy_disposition",
            "policy_reason_codes",
            "policy_exhausted_dimensions",
            "prompt_record_kind",
            "prompt_cache_outcome",
            "prompt_cache_owner",
            "prompt_reason_code",
            "prompt_provider_reported",
            "prompt_input_tokens",
            "prompt_cached_input_tokens",
            "prompt_cache_creation_input_tokens",
        }
        return {
            key: GoldenTrace._without_empty_task_policy_projection(item)
            for key, item in value.items()
            if key not in projection_keys
            or (item is not None and item != [] and item != 0)
        }

    def as_manifest(
        self,
        *,
        variant_id: str,
        redaction_policy_revision: str,
        harness_revisions: Mapping[str, str],
    ) -> TrajectoryManifest:
        """Materialize the baseline as the F1 projection contract."""

        values: dict[str, object] = {
            "trajectory_id": f"golden_{self.journey_id}",
            "run_id": None,
            "case_id": self.journey_id,
            "variant_id": variant_id,
            "ordered_steps": self.ordered_steps,
            "evidence_refs": (),
            "usage_summary": {},
            "redaction_policy_revision": redaction_policy_revision,
            "harness_revisions": dict(harness_revisions),
        }
        return TrajectoryManifest(
            **values,
            manifest_digest=TrajectoryManifest.digest_for(**values),
        )


class GoldenTraceCatalog(RuntimeContract):
    """Complete Step 0 baseline with a canonical catalog digest."""

    SCHEMA_VERSION: ClassVar[str] = "agent-runtime-golden-traces.v1"

    schema_version: Literal["agent-runtime-golden-traces.v1"]
    catalog_id: OpaqueId
    revision: Revision
    variant_id: OpaqueId
    redaction_policy_revision: Revision
    harness_revisions: dict[str, str]
    traces: tuple[GoldenTrace, ...]
    catalog_digest: Sha256

    @model_validator(mode="after")
    def _catalog_is_complete_and_canonical(self) -> "GoldenTraceCatalog":
        journey_ids = tuple(trace.journey_id for trace in self.traces)
        if journey_ids != tuple(sorted(journey_ids)):
            raise ValueError("golden traces must be sorted by journey_id")
        if len(journey_ids) != len(set(journey_ids)):
            raise ValueError("golden trace journey_id values must be unique")
        missing = BASELINE_JOURNEY_IDS - set(journey_ids)
        unexpected = set(journey_ids) - BASELINE_JOURNEY_IDS
        if missing or unexpected:
            raise ValueError(
                "golden trace journey set mismatch: "
                f"missing={sorted(missing)}, unexpected={sorted(unexpected)}"
            )
        expected = self.digest_for(
            schema_version=self.schema_version,
            catalog_id=self.catalog_id,
            revision=self.revision,
            variant_id=self.variant_id,
            redaction_policy_revision=self.redaction_policy_revision,
            harness_revisions=self.harness_revisions,
            traces=self.traces,
        )
        if self.catalog_digest != expected:
            raise ValueError("catalog_digest does not match canonical catalog content")
        return self

    @staticmethod
    def digest_for(**values: object) -> str:
        """Return the stable digest for all catalog content."""

        return canonical_json_sha256(
            GoldenTrace._without_empty_task_policy_projection(
                to_jsonable_python(values)
            )
        )

    def manifests(self) -> tuple[TrajectoryManifest, ...]:
        """Return immutable F1 manifests in catalog order."""

        return tuple(
            trace.as_manifest(
                variant_id=self.variant_id,
                redaction_policy_revision=self.redaction_policy_revision,
                harness_revisions=self.harness_revisions,
            )
            for trace in self.traces
        )


__all__ = [
    "BASELINE_JOURNEY_IDS",
    "GoldenTrace",
    "GoldenTraceCatalog",
]
