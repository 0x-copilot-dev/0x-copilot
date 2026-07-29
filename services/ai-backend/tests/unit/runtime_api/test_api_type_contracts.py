from __future__ import annotations

from pathlib import Path
import re

from copilot_service_contracts.work_ledger import LEDGER_EVENT_TYPES

from agent_runtime.control_plane import AgentQualityFeature, FeatureMode
from agent_runtime.capabilities.task_policy_journal import (
    TaskPolicyJournalRecordKind,
    TaskPolicyReasonCode,
)
from agent_runtime.execution.contracts import StreamEventSource
from agent_runtime.api.pending_work_v2_service import (
    PendingWorkV2Response,
    PendingWorkV2RunWarning,
)
from agent_runtime.surfaces_v2.pending_work_v2 import PendingWorkItemV2
from runtime_api.schemas import (
    AgentRunStatus,
    QualityControlBoundPayload,
    QualityDecisionPayload,
    RunHistoryEntry,
    RuntimeActivityKind,
    RuntimeApiEventType,
    TaskPolicyJournalPayload,
)


def test_run_history_entry_fields_match_api_types() -> None:
    """PRD-05 + PRD-08 — the ai-backend ``RunHistoryEntry`` field set is exactly
    the ``RunHistoryEntry`` interface mirrored in ``packages/api-types``. Any
    drift (a field added on one side only) breaks the wire contract silently;
    this pins both to the same twelve fields — the nine PRD-05 fields plus the
    three PRD-08 Activity meta counters."""
    assert set(RunHistoryEntry.model_fields) == {
        "run_id",
        "conversation_id",
        "conversation_title",
        "status",
        "model_name",
        "created_at",
        "started_at",
        "completed_at",
        "cancelled_at",
        # PRD-08 D1 — Activity meta counters.
        "connector_count",
        "step_count",
        "pending_approval_count",
    }


def test_pending_work_v2_fields_match_api_types() -> None:
    """E1 D6 — the intentionally tiny public queue cannot drift or grow leaks."""

    repo_root = Path(__file__).resolve().parents[5]
    ledger_types = (repo_root / "packages/api-types/src/ledger.ts").read_text()

    assert set(PendingWorkV2Response.model_fields) == {
        "v",
        "items",
        "warnings",
        "next_cursor",
        "has_more",
    }
    assert set(PendingWorkV2RunWarning.model_fields) == {"run_id", "status"}
    assert set(PendingWorkItemV2.model_fields) == {
        "run_id",
        "subject_kind",
        "subject_id",
        "status",
        "opened_sequence_no",
        "latest_sequence_no",
    }
    assert _typescript_interface_fields(ledger_types, "PendingWorkV2Response") == set(
        PendingWorkV2Response.model_fields
    )
    assert _typescript_interface_fields(ledger_types, "PendingWorkV2RunWarning") == set(
        PendingWorkV2RunWarning.model_fields
    )
    assert _typescript_interface_fields(ledger_types, "PendingWorkItemV2") == set(
        PendingWorkItemV2.model_fields
    )


def _typescript_interface_fields(source: str, name: str) -> set[str]:
    match = re.search(
        rf"export interface {name}\s*\{{(?P<body>.*?)^\}}",
        source,
        re.MULTILINE | re.DOTALL,
    )
    assert match is not None, f"missing TypeScript interface {name}"
    return set(
        re.findall(
            r"^\s*(?:readonly\s+)?([a-z_][a-z0-9_]*)\??\s*:",
            match.group("body"),
            re.MULTILINE,
        )
    )


def _typescript_string_union(source: str, name: str) -> set[str]:
    match = re.search(
        rf"export type {name}\s*=\s*(?P<body>.*?);",
        source,
        re.DOTALL,
    )
    assert match is not None, f"missing TypeScript union {name}"
    return set(re.findall(r'"([^"]+)"', match.group("body")))


class TestApiTypeContracts:
    def test_quality_event_payload_fields_match_closed_typescript_contracts(
        self,
    ) -> None:
        repo_root = Path(__file__).resolve().parents[5]
        api_types = (repo_root / "packages/api-types/src/index.ts").read_text()

        assert _typescript_interface_fields(
            api_types,
            "QualityControlBoundPayload",
        ) == set(QualityControlBoundPayload.model_fields)
        assert _typescript_interface_fields(
            api_types,
            "QualityDecisionPayload",
        ) == set(QualityDecisionPayload.model_fields)
        assert _typescript_interface_fields(
            api_types,
            "TaskPolicyJournalPayload",
        ) == set(TaskPolicyJournalPayload.model_fields)
        assert _typescript_string_union(api_types, "AgentQualityFeature") == {
            feature.value for feature in AgentQualityFeature
        }
        assert _typescript_string_union(api_types, "QualityFeatureMode") == {
            mode.value for mode in FeatureMode
        }
        assert _typescript_string_union(
            api_types,
            "TaskPolicyJournalRecordKind",
        ) == {kind.value for kind in TaskPolicyJournalRecordKind}
        assert _typescript_string_union(api_types, "TaskPolicyReasonCode") == {
            code.value for code in TaskPolicyReasonCode
        }

    def test_typescript_runtime_event_constants_match_backend_enums(self) -> None:
        repo_root = Path(__file__).resolve().parents[5]
        api_types = (repo_root / "packages/api-types/src/index.ts").read_text()
        ledger_types = (repo_root / "packages/api-types/src/ledger.ts").read_text()

        runtime_types = self._string_array(
            api_types, "RUNTIME_API_EVENT_TYPES", extra_sources=(ledger_types,)
        )
        runtime_types.update(
            self._string_array(ledger_types, "ARTIFACT_RUNTIME_EVENT_TYPES")
        )
        assert runtime_types == {event_type.value for event_type in RuntimeApiEventType}
        assert self._string_array(api_types, "RUNTIME_EVENT_SOURCES") == {
            source.value for source in StreamEventSource
        }
        assert self._string_array(api_types, "RUNTIME_ACTIVITY_KINDS") == {
            kind.value for kind in RuntimeActivityKind
        }

    def test_typescript_runtime_status_constants_match_backend_enums(self) -> None:
        repo_root = Path(__file__).resolve().parents[5]
        api_types = (repo_root / "packages/api-types/src/index.ts").read_text()

        assert self._string_array(api_types, "AGENT_RUN_STATUSES") == {
            status.value for status in AgentRunStatus
        }

    @classmethod
    def _string_array(
        cls,
        source: str,
        name: str,
        *,
        extra_sources: tuple[str, ...] = (),
    ) -> set[str]:
        """Resolve a TS ``as const`` tuple to its set of string values.

        ``extra_sources`` lets a spread resolve against a sibling module. The
        transport tuple composes the ledger's named event families (which live
        in ``ledger.ts``) rather than re-typing their values, because
        ``test_event_literal_gate_v2_1`` forbids inline duplicates of ledger
        event values — so following a spread across the file boundary is the
        only way to read it.
        """

        for candidate in (source, *extra_sources):
            match = re.search(
                rf"(?:export )?const {name} = \[(.*?)\] as const",
                candidate,
                re.S,
            )
            if match is not None:
                break
        assert match is not None, f"missing TypeScript tuple {name}"
        body = match.group(1)
        values = set(re.findall(r'"([^"]+)"', body))
        values.update(
            LEDGER_EVENT_TYPES[int(index)]
            for index in re.findall(
                r"WORK_LEDGER_EVENT_TYPES\[(\d+)\]",
                body,
            )
        )
        for spread in re.findall(r"\.\.\.([A-Z][A-Z0-9_]*)", body):
            # Import aliases (``X as WORK_LEDGER_X``) resolve under their local
            # name here; fall back to the original export name in the sibling.
            local = spread.removeprefix("WORK_LEDGER_")
            for alias in (spread, local):
                try:
                    values.update(
                        cls._string_array(source, alias, extra_sources=extra_sources)
                    )
                except AssertionError:
                    continue
                break
            else:  # pragma: no cover - a genuinely unresolvable spread
                raise AssertionError(f"unresolved TypeScript spread {spread}")
        return values
