"""E2 D9 final-conformance report assembled from existing architecture gates.

This is a release evaluator, not a rollout controller.  It reads checked-in
contracts and source trees only; it never selects a mode, starts an executor,
or changes a capability.  A blocked capability is explicit in the report and
cannot be mistaken for a passed release requirement.
"""

from __future__ import annotations

from collections.abc import Callable
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
from agent_runtime.surfaces_v2.legacy_v2_replay import project_legacy_v2_replay
from agent_runtime.surfaces_v2.lifecycle_reference_snapshots import (
    LifecycleReferenceConformanceGate,
)
from agent_runtime.surfaces_v2.lifecycle_refs import LifecycleReferenceEnumerator


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
            self._legacy_replay,
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
        if _legacy_mcp_direct_dispatch_present(self._paths.source_root):
            return _blocked(
                3,
                "no direct effect client upstream",
                "D7 prerequisite: CallMcpTool legacy direct-read compatibility path remains; default-off writes and unknown operations are fail-closed",
            )
        return _pass(
            3,
            "no direct effect client upstream",
            "sole-producer and sole-workspace-constructor AST guards; D7 legacy path retired",
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

    def _legacy_replay(self) -> ConformanceCondition:
        try:
            raw = load_legacy_v2_replay_corpus()
            cases = raw.get("cases")
            if not isinstance(cases, list) or not cases:
                return _fail(
                    7,
                    "all old fixtures/exports replay and verify",
                    "legacy replay corpus missing cases",
                )
            for case in cases:
                if not isinstance(case, dict):
                    return _fail(
                        7,
                        "all old fixtures/exports replay and verify",
                        "legacy replay corpus contains malformed case",
                    )
                events = case.get("events")
                expected = case.get("expected")
                if not isinstance(events, list) or not isinstance(expected, dict):
                    return _fail(
                        7,
                        "all old fixtures/exports replay and verify",
                        "legacy replay case lacks events/expected",
                    )
                if (
                    project_legacy_v2_replay(deepcopy(events)).model_dump(mode="json")
                    != expected
                ):
                    return _fail(
                        7,
                        "all old fixtures/exports replay and verify",
                        f"legacy replay mismatch: {case.get('id', 'unknown')}",
                    )
            golden = load_ledger_golden_events().get("events")
            if not isinstance(golden, list):
                return _fail(
                    7,
                    "all old fixtures/exports replay and verify",
                    "legacy golden event export missing",
                )
            project_legacy_v2_replay(deepcopy(golden))
        except Exception as exc:
            return _fail(
                7, "all old fixtures/exports replay and verify", type(exc).__name__
            )
        return _pass(
            7,
            "all old fixtures/exports replay and verify",
            f"{len(cases)} replay corpus case(s) + golden export",
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


def _legacy_mcp_direct_dispatch_present(source_root: Path) -> bool:
    """Detect the D7-owned direct model-tool client construction structurally."""

    import ast

    path = source_root / "agent_runtime/capabilities/mcp/middleware/call_tool.py"
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError):
        # Missing/unparseable safety evidence must never be a release pass.
        return True
    for node in ast.walk(tree):
        if not isinstance(node, ast.AsyncFunctionDef) or node.name != "ainvoke":
            continue
        if any(
            isinstance(call.func, ast.Attribute) and call.func.attr == "create_client"
            for call in ast.walk(node)
            if isinstance(call, ast.Call)
        ):
            return True
    return False


__all__ = (
    "ConformanceCondition",
    "ConformanceStatus",
    "E2FinalConformancePaths",
    "E2FinalConformanceReport",
    "E2FinalConformanceRunner",
)
