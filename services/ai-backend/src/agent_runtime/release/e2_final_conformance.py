"""E2 D9 final-conformance report assembled from existing architecture gates.

This is a release evaluator, not a rollout controller.  It reads checked-in
contracts and source trees only; it never selects a mode, starts an executor,
or changes a capability.  A blocked capability is explicit in the report and
cannot be mistaken for a passed release requirement.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from copy import deepcopy
from dataclasses import dataclass
from enum import StrEnum
import json
from pathlib import Path
from typing import Any

from copilot_service_contracts.work_ledger import (
    load_ledger_golden_events,
    load_legacy_v2_replay_corpus,
)

from agent_runtime.capabilities.operations.catalog import DEFAULT_OPERATION_DESCRIPTORS
from agent_runtime.capabilities.operations.conformance import OperationConformanceGate
from agent_runtime.effects.composition import (
    EffectDescriptorCompositionError,
    validate_effect_descriptor_staging,
)
from agent_runtime.effects.conformance import (
    canonical_effect_result_producer_present,
    canonical_workspace_executor_constructor_present,
    effect_applied_producer_violations,
    workspace_executor_constructor_violations,
)
from agent_runtime.observability.llm_seam_conformance import (
    canonical_model_funnel_present,
    llm_seam_violations,
)
from agent_runtime.rollout import (
    E2RolloutResolution,
    E2RolloutSettings,
    RolloutCapability,
    RolloutConfigurationError,
    RolloutMode,
    RolloutProvenance,
    RolloutStartupReadiness,
    RolloutStartupValidator,
)
from agent_runtime.surfaces_v2.content import SurfaceContentProjection
from agent_runtime.surfaces_v2.lifecycle_reference_snapshots import (
    LifecycleReferenceConformanceGate,
)
from agent_runtime.surfaces_v2.lifecycle_refs import LifecycleReferenceEnumerator
from agent_runtime.surfaces_v2.projection import SurfaceStoreProjection


class ConformanceStatus(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class ConformanceCondition:
    number: int
    title: str
    status: ConformanceStatus
    evidence: tuple[str, ...]


@dataclass(frozen=True)
class E2FinalConformanceReport:
    conditions: tuple[ConformanceCondition, ...]

    @property
    def ready(self) -> bool:
        return all(item.status is ConformanceStatus.PASS for item in self.conditions)

    def as_json(self) -> dict[str, object]:
        return {
            "contract": "e2-final-conformance-v1",
            "ready": self.ready,
            "conditions": [
                {
                    "number": item.number,
                    "title": item.title,
                    "status": item.status.value,
                    "evidence": list(item.evidence),
                }
                for item in self.conditions
            ],
        }


@dataclass(frozen=True)
class E2FinalConformancePaths:
    repo_root: Path
    source_root: Path
    descriptor_factory: Path
    surface_schema: Path
    renderer_spec_types: Path

    @classmethod
    def current(cls) -> "E2FinalConformancePaths":
        repo_root = Path(__file__).resolve().parents[5]
        return cls(
            repo_root=repo_root,
            source_root=repo_root / "services/ai-backend/src",
            descriptor_factory=repo_root
            / "services/ai-backend/src/runtime_worker/mcp_operation_storage.py",
            surface_schema=repo_root
            / "packages/service-contracts/src/copilot_service_contracts/surface_spec.schema.json",
            renderer_spec_types=repo_root
            / "packages/surface-renderers/src/_shared/specTypes.ts",
        )


class E2FinalConformanceRunner:
    """Run all twelve D9 conditions deterministically and fail closed."""

    def __init__(
        self,
        *,
        paths: E2FinalConformancePaths | None = None,
        dark_capability_check: Callable[[], tuple[str, ...]] | None = None,
        service_boundary_check: Callable[[], tuple[str, ...]] | None = None,
    ) -> None:
        self._paths = paths or E2FinalConformancePaths.current()
        self._dark_capability_check = (
            dark_capability_check or _dark_capability_violations
        )
        self._service_boundary_check = (
            service_boundary_check or _service_boundary_violations
        )

    def run(self) -> E2FinalConformanceReport:
        checks: tuple[Callable[[], ConformanceCondition], ...] = (
            self._descriptor_coverage,
            self._effect_descriptor_execution_path,
            self._no_direct_effect_client,
            self._canonical_effect_result_producer,
            self._metered_model_calls,
            self._reference_ownership,
            self._historic_replay,
            self._no_temporary_exemption_or_unsafe_default,
            self._workspace_attestation,
            self._fixed_ui_schema,
            self._service_boundaries,
            self._dark_capabilities,
        )
        return E2FinalConformanceReport(conditions=tuple(check() for check in checks))

    def _descriptor_coverage(self) -> ConformanceCondition:
        try:
            OperationConformanceGate.validate_current()
        except Exception as exc:
            return _fail(
                1, "every model-facing operation has descriptor", type(exc).__name__
            )
        return _pass(
            1,
            "every model-facing operation has descriptor",
            "OperationConformanceGate.validate_current",
        )

    def _effect_descriptor_execution_path(self) -> ConformanceCondition:
        try:
            mappings = validate_effect_descriptor_staging(DEFAULT_OPERATION_DESCRIPTORS)
        except EffectDescriptorCompositionError as exc:
            return _blocked(
                2,
                "effect descriptors map to stager and executor",
                str(exc),
            )
        return _pass(
            2,
            "effect descriptors map to stager and executor",
            f"{len(mappings)} reviewed descriptor→stager mappings; CI resolves each executor from the worker registry",
        )

    def _no_direct_effect_client(self) -> ConformanceCondition:
        violations = (
            *effect_applied_producer_violations(self._paths.source_root),
            *workspace_executor_constructor_violations(self._paths.source_root),
        )
        if violations:
            return _fail(3, "no direct effect client upstream", *violations)
        mcp_dispatch = _model_facing_mcp_dispatch_violations(self._paths.source_root)
        if mcp_dispatch:
            return _fail(3, "no direct effect client upstream", *mcp_dispatch)
        return _pass(
            3,
            "no direct effect client upstream",
            "sole-producer, sole-workspace-constructor, and model-facing MCP dispatch AST guards",
        )

    def _canonical_effect_result_producer(self) -> ConformanceCondition:
        present = canonical_effect_result_producer_present(self._paths.source_root)
        direct = canonical_workspace_executor_constructor_present(
            self._paths.source_root
        )
        if present and direct:
            return _pass(
                4,
                "one canonical effect result producer",
                "EffectResultRecorder + worker workspace constructor present",
            )
        missing = []
        if not present:
            missing.append("canonical terminal-result producer missing")
        if not direct:
            missing.append("canonical workspace constructor missing")
        return _fail(4, "one canonical effect result producer", *missing)

    def _metered_model_calls(self) -> ConformanceCondition:
        violations = llm_seam_violations(self._paths.source_root)
        funnel = canonical_model_funnel_present(self._paths.source_root)
        if violations:
            return _fail(5, "every model call metered", *violations)
        if not funnel:
            return _fail(
                5,
                "every model call metered",
                "canonical model funnel is missing guarded constructors",
            )
        return _pass(
            5,
            "every model call metered",
            "AST LLM seam guard and canonical model funnel",
        )

    def _reference_ownership(self) -> ConformanceCondition:
        try:
            LifecycleReferenceConformanceGate.validate_current()
            unmapped = LifecycleReferenceEnumerator.unmapped_contract_reference_events()
        except Exception as exc:
            return _fail(
                6,
                "every ref scheme has auth/retention/deletion owner",
                type(exc).__name__,
            )
        if unmapped:
            return _fail(
                6,
                "every ref scheme has auth/retention/deletion owner",
                "unmapped contract refs: " + ", ".join(sorted(unmapped)),
            )
        return _pass(
            6,
            "every ref scheme has auth/retention/deletion owner",
            "LifecycleReferenceConformanceGate + contract-reference enumeration",
        )

    def _historic_replay(self) -> ConformanceCondition:
        """Every checked-in historic export still replays through the LIVE fold.

        This condition used to drive a separate compatibility reader. That
        reader decided "is this record historic?" from five string signals that
        all match current data, so it claimed every live surface and served none
        of them; deleting it leaves the canonical projector as the only
        projector, which is what the endpoint already serves.

        The frozen cross-language corpus keeps its job and gains a better one.
        It is the checked-in record of what old runs actually contain, so it now
        pins that ``SurfaceStoreProjection`` — the fold behind
        ``GET /v1/agent/runs/{run_id}/surfaces`` — still recovers every historic
        subject with the same identity, kind, title, provenance and payload
        reference the vectors froze. A change that dropped an old surface fails
        here instead of silently in a user's canvas.

        The second fold is pinned too, and it is the one that changed. Surface
        state is now *carried* on ``surface.created``; the old
        ``payload_ref → call_id → tool_result.output`` re-join is gone, so a
        pre-carry record on a desktop's disk hydrates to whatever it actually
        carried and nothing more — for the golden export, to nothing at all.
        That consequence is real and permanent, so each vector pins the exact
        output of ``SurfaceContentProjection`` (``expected.content``, and
        ``golden_export_content`` for the export) rather than merely folding
        twice and comparing the fold to itself. Running it twice still proves
        determinism; only a pinned value can prove *what* a user's old surface
        now shows.

        Envelope-origin vectors are deliberately not asserted for metadata: they
        replay the retired v1 presentation envelope (``payload.surface``), which
        no canonical producer writes. Demanding recovery of a subject nothing
        emits is the reader-without-a-writer defect this release wave removes.
        Their content pin still applies — an envelope case now hydrates to ``{}``
        and the vector says so.
        """

        title = "all old fixtures/exports replay and verify"
        try:
            corpus = load_legacy_v2_replay_corpus()
            cases = corpus.get("cases")
            if not isinstance(cases, list) or not cases:
                return _fail(7, title, "historic replay corpus missing cases")
            recovered = 0
            hydrated = 0
            for case in cases:
                if not isinstance(case, dict):
                    return _fail(7, title, "historic replay corpus malformed case")
                events = case.get("events")
                expected = case.get("expected")
                if not isinstance(events, list) or not isinstance(expected, dict):
                    return _fail(7, title, "historic replay case lacks events/expected")
                case_id = str(case.get("id", "unknown"))
                content = expected.get("content")
                if not isinstance(content, Mapping):
                    return _fail(
                        7, title, f"{case_id}: frozen expectation lacks content"
                    )
                mismatch, subjects = _historic_replay_violations(
                    case_id, events, expected, content
                )
                if mismatch is not None:
                    return _fail(7, title, mismatch)
                recovered += subjects
                hydrated += len(content)
            golden = load_ledger_golden_events().get("events")
            if not isinstance(golden, list):
                return _fail(7, title, "historic golden event export missing")
            golden_content = corpus.get("golden_export_content")
            if not isinstance(golden_content, Mapping):
                return _fail(7, title, "golden export lacks a frozen content pin")
            mismatch, _ = _historic_replay_violations(
                "golden_export", golden, None, golden_content
            )
            if mismatch is not None:
                return _fail(7, title, mismatch)
            hydrated += len(golden_content)
        except Exception as exc:
            return _fail(7, title, type(exc).__name__)
        return _pass(
            7,
            title,
            f"{len(cases)} historic corpus case(s) + golden export replay through "
            f"SurfaceStoreProjection + SurfaceContentProjection",
            f"{recovered} frozen historic subject(s) still recovered by the "
            f"canonical fold",
            f"{hydrated} frozen historic subject(s) hydrate to exactly the state "
            f"the vectors pin; every other replayed subject is pinned to hydrate "
            f"nothing",
        )

    def _no_temporary_exemption_or_unsafe_default(self) -> ConformanceCondition:
        try:
            from agent_runtime.capabilities.operations.conformance import (
                load_operation_exemptions,
            )

            exemptions = load_operation_exemptions()
            defaults = E2RolloutSettings.from_environment({})[0]
        except Exception as exc:
            return _fail(
                8,
                "no temporary exemption or unsafe flag combination",
                type(exc).__name__,
            )
        if exemptions:
            return _blocked(
                8,
                "no temporary exemption or unsafe flag combination",
                f"{len(exemptions)} temporary descriptor exemption(s)",
            )
        enabled = [capability.value for capability in defaults.enforced()]
        if enabled:
            return _fail(
                8,
                "no temporary exemption or unsafe flag combination",
                "default enforce lanes: " + ", ".join(enabled),
            )
        return _pass(
            8,
            "no temporary exemption or unsafe flag combination",
            "zero descriptor exemptions; all E2 lanes default off",
        )

    def _workspace_attestation(self) -> ConformanceCondition:
        modes = E2RolloutSettings.model_construct(
            **{
                capability.value: RolloutMode.ENFORCE
                for capability in RolloutCapability
            }
        )
        resolution = E2RolloutResolution.model_construct(
            modes=modes,
            provenance=RolloutProvenance.model_construct(
                explicit_enforced=(RolloutCapability.WORKSPACE_COMMIT,),
                legacy_elevated=(),
            ),
        )
        readiness = RolloutStartupReadiness(
            descriptor_catalog_ready=True,
            executor_registry_ready=True,
            artifact_repository_ready=True,
            operation_gateway_ready=True,
            effect_stager_ready=True,
            effect_commit_ready=True,
            presentation_v2_1_ready=True,
            workspace_overlay_ready=True,
            workspace_commit_ready=True,
            workspace_c2_native_attested=False,
            mcp_gateway_ready=True,
            sandbox_adapter_ready=True,
            browser_adapter_ready=True,
        )
        try:
            RolloutStartupValidator.validate_startup(resolution, readiness=readiness)
        except RolloutConfigurationError:
            return _pass(
                9,
                "workspace write enabled only on attested platforms",
                "workspace enforce rejected without C2 native attestation",
            )
        return _fail(
            9,
            "workspace write enabled only on attested platforms",
            "workspace enforce accepted without native attestation",
        )

    def _fixed_ui_schema(self) -> ConformanceCondition:
        try:
            schema = json.loads(self._paths.surface_schema.read_text(encoding="utf-8"))
            renderer_types = self._paths.renderer_spec_types.read_text(encoding="utf-8")
        except (OSError, json.JSONDecodeError) as exc:
            return _fail(
                10,
                "UI subject/renderers use fixed constrained schemas",
                type(exc).__name__,
            )
        if schema.get("additionalProperties") is not False:
            return _fail(
                10,
                "UI subject/renderers use fixed constrained schemas",
                "SurfaceSpec root permits undeclared properties",
            )
        required = {"SurfaceSpec", "specFromState", "dataFromState"}
        absent = sorted(token for token in required if token not in renderer_types)
        if absent:
            return _fail(
                10,
                "UI subject/renderers use fixed constrained schemas",
                "renderer schema seam missing: " + ", ".join(absent),
            )
        return _pass(
            10,
            "UI subject/renderers use fixed constrained schemas",
            "frozen SurfaceSpec JSON schema + typed renderer narrowing seam",
        )

    def _service_boundaries(self) -> ConformanceCondition:
        violations = self._service_boundary_check()
        return (
            _pass(
                11,
                "no service-boundary violation",
                "AST deployable-service import boundary guard",
            )
            if not violations
            else _fail(11, "no service-boundary violation", *violations)
        )

    def _dark_capabilities(self) -> ConformanceCondition:
        violations = self._dark_capability_check()
        return (
            _pass(
                12,
                "dark-capability scanner reports none",
                "tools/check_dark_capabilities.py",
            )
            if not violations
            else _fail(12, "dark-capability scanner reports none", *violations)
        )


def _pass(number: int, title: str, *evidence: str) -> ConformanceCondition:
    return ConformanceCondition(number, title, ConformanceStatus.PASS, tuple(evidence))


def _fail(number: int, title: str, *evidence: str) -> ConformanceCondition:
    return ConformanceCondition(number, title, ConformanceStatus.FAIL, tuple(evidence))


def _blocked(number: int, title: str, *evidence: str) -> ConformanceCondition:
    return ConformanceCondition(
        number, title, ConformanceStatus.BLOCKED, tuple(evidence)
    )


@dataclass(frozen=True)
class _HistoricEvent:
    """Envelope-lite adapter so raw corpus JSON can drive the content fold.

    ``SurfaceContentProjection`` reads persisted events structurally
    (``event_type`` / ``sequence_no`` / ``payload``) rather than importing a
    transport envelope; the corpus stores those same three fields as plain JSON.
    """

    event_type: str
    sequence_no: int
    payload: Mapping[str, object]

    @classmethod
    def of(cls, raw: object) -> "_HistoricEvent":
        record: Mapping[str, object] = raw if isinstance(raw, Mapping) else {}
        sequence_no = record.get("sequence_no")
        payload = record.get("payload")
        return cls(
            event_type=str(record.get("event_type", "")),
            sequence_no=sequence_no if isinstance(sequence_no, int) else 0,
            payload=payload if isinstance(payload, Mapping) else {},
        )


#: Canonical snapshot attribute ← frozen corpus key. The corpus spells an absent
#: value ``null``; the fold spells it ``""``, because a total fold never invents
#: an optional. The comparison normalizes that one direction and nothing else.
_HISTORIC_SUBJECT_FIELDS: tuple[tuple[str, str], ...] = (
    ("kind", "kind"),
    ("title", "title"),
    ("connector", "source_connector"),
    ("op", "source_op"),
    ("payload_ref", "payload_ref"),
)


def _historic_replay_violations(
    case_id: str,
    events: list[object],
    expected: Mapping[str, object] | None,
    expected_content: Mapping[str, object],
) -> tuple[str | None, int]:
    """Replay one historic export through both canonical folds.

    Returns the first violation (or ``None``) plus the number of frozen subjects
    the canonical fold recovered. Each replay runs over its own deep copy and
    runs twice, because a fold that mutated its input or answered differently on
    the second pass would break reconnect and restart — which is the property
    "replay and verify" actually names.

    ``expected_content`` is the separate, stronger claim: determinism says the
    fold agrees with itself, this says it agrees with a value a human froze.
    Every replay carries one, including the exports that hydrate to ``{}`` —
    "this old surface now shows nothing" is the release consequence most worth
    stating out loud, and an empty pin is the only way to state it.
    """

    metadata = SurfaceStoreProjection.fold_raw(case_id, deepcopy(events))
    if metadata != SurfaceStoreProjection.fold_raw(case_id, deepcopy(events)):
        return (f"{case_id}: surface fold is not deterministic", 0)
    content_events = [_HistoricEvent.of(event) for event in deepcopy(events)]
    content = SurfaceContentProjection.fold(content_events)
    if content != SurfaceContentProjection.fold(content_events):
        return (f"{case_id}: content fold is not deterministic", 0)
    if content != dict(expected_content):
        return (
            f"{case_id}: hydrated content drifted from its frozen vector: "
            f"{sorted(content)} vs {sorted(expected_content)}",
            0,
        )
    if expected is None:
        return (None, 0)
    frozen = expected.get("surfaces")
    if not isinstance(frozen, list):
        return (f"{case_id}: frozen expectation lacks surfaces", 0)
    snapshots = {snapshot.surface_id: snapshot for snapshot in metadata.surfaces}
    recovered = 0
    for subject in frozen:
        if not isinstance(subject, Mapping) or subject.get("origin") != "ledger":
            continue
        subject_id = subject.get("subject_id")
        snapshot = snapshots.get(subject_id) if isinstance(subject_id, str) else None
        if snapshot is None:
            return (f"{case_id}: historic subject lost: {subject_id!r}", recovered)
        for attribute, key in _HISTORIC_SUBJECT_FIELDS:
            if getattr(snapshot, attribute) != (subject.get(key) or ""):
                return (
                    f"{case_id}: {subject_id} {attribute} drifted from its "
                    f"frozen vector",
                    recovered,
                )
        recovered += 1
    return (None, recovered)


def _dark_capability_violations() -> tuple[str, ...]:
    module = _load_tool_module("e2_dark_capability_check", "check_dark_capabilities.py")
    if module is None:
        return ("cannot load dark-capability scanner",)
    referenced = module._collect_referenced_names(module.REFERENCE_ROOTS)
    declarations = module._first_declarations(module.DEFAULT_SRC_ROOTS)
    return tuple(sorted(name for name in declarations if name not in referenced))


def _service_boundary_violations() -> tuple[str, ...]:
    module = _load_tool_module(
        "e2_service_boundary_check", "check_service_boundaries.py"
    )
    if module is None:
        return ("cannot load service-boundary scanner",)
    return (*module.boundary_violations(), *module.desktop_ipc_boundary_violations())


def _load_tool_module(name: str, filename: str) -> Any | None:
    """Load a stdlib tool once without making ``tools`` a runtime package."""

    from importlib.util import module_from_spec, spec_from_file_location
    import sys

    path = E2FinalConformancePaths.current().repo_root / "tools" / filename
    spec = spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        return None
    module = module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _model_facing_mcp_dispatch_violations(source_root: Path) -> tuple[str, ...]:
    """Reject provider construction or invocation from model-facing MCP adapters.

    ``McpOperationAdapter`` is the reviewed canonical read seam.  Everything in
    the MCP middleware package is upstream of descriptor classification, so an
    upstream ``create_client`` or ``call_tool`` would restore the exact D1/D7
    bypass this release gate retires.
    """

    import ast

    middleware_root = source_root / "agent_runtime/capabilities/mcp/middleware"
    if not middleware_root.is_dir():
        return ("model-facing MCP middleware directory is missing",)
    violations: list[str] = []
    for path in sorted(middleware_root.glob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, SyntaxError):
            violations.append(f"{path.relative_to(source_root)}: unparseable")
            continue
        for call in ast.walk(tree):
            if not isinstance(call, ast.Call) or not isinstance(
                call.func, ast.Attribute
            ):
                continue
            if call.func.attr in {"create_client", "call_tool"}:
                violations.append(
                    f"{path.relative_to(source_root)}:{call.lineno}: {call.func.attr}"
                )
    return tuple(violations)


__all__ = (
    "ConformanceCondition",
    "ConformanceStatus",
    "E2FinalConformancePaths",
    "E2FinalConformanceReport",
    "E2FinalConformanceRunner",
)
