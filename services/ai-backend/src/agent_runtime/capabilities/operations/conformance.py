"""Architecture gate for model-facing operation descriptor coverage."""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from datetime import date
from importlib.resources import files
from importlib.resources.abc import Traversable

from pydantic import Field, TypeAdapter, field_validator

from agent_runtime.capabilities.actions.catalog import ACTION_CATALOG
from agent_runtime.capabilities.operations.builtin_catalog import (
    DEFAULT_BUILTIN_OPERATION_CATALOG,
)
from agent_runtime.capabilities.operations.catalog import (
    DEFAULT_OPERATION_DESCRIPTORS,
)
from agent_runtime.capabilities.operations.descriptors import (
    OperationDescriptorRegistry,
)
from agent_runtime.execution.contracts import RuntimeContract
from agent_runtime.surfaces_v2.ledger_models import EffectClass, EffectExecutorKind

_EXEMPTIONS_FILE = "operation_descriptor_exemptions.json"


class CapabilityRegistration(RuntimeContract):
    """One callable model/system operation exposed by repository wiring."""

    capability: str = Field(min_length=1, max_length=128)
    op: str = Field(min_length=1, max_length=128)
    source: str = Field(min_length=1, max_length=512)

    @property
    def key(self) -> tuple[str, str]:
        return (
            OperationDescriptorRegistry.normalize_capability(self.capability),
            OperationDescriptorRegistry.normalize(self.op),
        )


class OperationDescriptorExemption(RuntimeContract):
    """Temporary reviewed exception, stored only in the central data file."""

    capability: str = Field(min_length=1, max_length=128)
    op: str = Field(min_length=1, max_length=128)
    owner: str = Field(min_length=1, max_length=128)
    expires_on: date
    reason: str = Field(min_length=1, max_length=512)
    safe_default_classification: EffectClass

    @field_validator("safe_default_classification")
    @classmethod
    def _default_must_hold(cls, value: EffectClass) -> EffectClass:
        if value in {EffectClass.NONE, EffectClass.INTERNAL_REVERSIBLE}:
            raise ValueError("exemption safe default must remain held")
        return value

    @property
    def key(self) -> tuple[str, str]:
        return (
            OperationDescriptorRegistry.normalize_capability(self.capability),
            OperationDescriptorRegistry.normalize(self.op),
        )


class OperationConformanceError(RuntimeError):
    """Descriptor coverage or exemption policy is invalid."""


class OperationConformanceGate:
    """Validate exact descriptor/exemption coverage and closed executors."""

    @classmethod
    def validate(
        cls,
        *,
        registrations: Iterable[CapabilityRegistration],
        registry: OperationDescriptorRegistry = DEFAULT_OPERATION_DESCRIPTORS,
        exemptions: Sequence[OperationDescriptorExemption] = (),
        today: date | None = None,
    ) -> None:
        effective_today = today or date.today()
        exemption_by_key: dict[tuple[str, str], OperationDescriptorExemption] = {}
        for exemption in exemptions:
            if exemption.key in exemption_by_key:
                raise OperationConformanceError(
                    f"duplicate operation exemption for {exemption.key}"
                )
            if exemption.expires_on < effective_today:
                raise OperationConformanceError(
                    f"expired operation exemption for {exemption.key}"
                )
            exemption_by_key[exemption.key] = exemption

        seen: set[tuple[str, str]] = set()
        for registration in registrations:
            if registration.key in seen:
                raise OperationConformanceError(
                    f"duplicate capability registration for {registration.key}"
                )
            seen.add(registration.key)
            descriptor = registry.resolve(registration.capability, registration.op)
            exemption = exemption_by_key.get(registration.key)
            if descriptor is not None and exemption is not None:
                raise OperationConformanceError(
                    f"operation has both descriptor and exemption: {registration.key}"
                )
            if descriptor is None and exemption is None:
                raise OperationConformanceError(
                    f"unregistered model-facing operation: {registration.key}"
                )
            if descriptor is not None and descriptor.executor not in tuple(
                EffectExecutorKind
            ):
                raise OperationConformanceError(
                    f"operation executor is not registered: {registration.key}"
                )

    @classmethod
    def validate_current(cls) -> None:
        cls.validate(
            registrations=current_capability_registrations(),
            exemptions=load_operation_exemptions(),
        )

    @classmethod
    def validate_model_tool_surface(
        cls,
        tools: Sequence[object],
        *,
        registry: OperationDescriptorRegistry = DEFAULT_OPERATION_DESCRIPTORS,
        exemptions: Sequence[OperationDescriptorExemption] = (),
    ) -> None:
        """Compare an actually assembled model tool surface to the inventory."""

        actual = registrations_from_model_tools(tools)
        inventoried = {
            registration.key for registration in current_capability_registrations()
        }
        for registration in actual:
            if registration.key not in inventoried:
                raise OperationConformanceError(
                    f"model-facing operation missing from inventory: {registration.key}"
                )
        cls.validate(
            registrations=actual,
            registry=registry,
            exemptions=exemptions,
        )


def load_operation_exemptions(
    path: Traversable | None = None,
) -> tuple[OperationDescriptorExemption, ...]:
    target = path or files(__package__).joinpath(_EXEMPTIONS_FILE)
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
        return TypeAdapter(tuple[OperationDescriptorExemption, ...]).validate_python(
            raw
        )
    except Exception as exc:
        raise OperationConformanceError(
            f"{target.name}: invalid operation exemption file"
        ) from exc


def current_capability_registrations() -> tuple[CapabilityRegistration, ...]:
    """Inventory the fixed registration seams and the existing C1 catalog.

    Dynamic MCP tools are not guessed: every reviewed C1 catalog entry is
    included exactly, while an unrecognized provider operation classifies via
    the runtime safe default.  New fixed assembly wrappers must add a row here;
    the conformance test also compares this inventory to factory registrations.
    """

    fixed = (
        ("builtin", "web_search", "runtime_worker.dependencies.WebSearchToolRegistry"),
        ("builtin", "load_mcp_server", "execution.factory._model_visible_tools"),
        ("builtin", "auth_mcp", "execution.factory._model_visible_tools"),
        ("builtin", "load_skill", "execution.factory._model_visible_tools"),
        ("builtin", "load_prior_tool_result", "execution.factory._model_visible_tools"),
        ("builtin", "ask_a_question", "execution.factory._model_visible_tools"),
        (
            "builtin",
            "list_connected_servers",
            "execution.factory._model_visible_tools",
        ),
        ("builtin", "suggest_mcp_connector", "execution.factory._model_visible_tools"),
        # F3 capability-discovery bridge tools. They are registered by the same
        # factory function as their neighbours, through
        # ``CapabilityBridgeRegistrar``, and only in the ``deferred`` posture.
        # They are inventoried unconditionally because this list records fixed
        # registration *seams*, not whichever seams a given run's feature flags
        # happened to open — exactly like ``run_in_sandbox``, which is also
        # absent from a run that supplies no sandbox tool.
        (
            "builtin",
            "search_capabilities",
            "execution.factory._model_visible_tools",
        ),
        (
            "builtin",
            "describe_capability",
            "execution.factory._model_visible_tools",
        ),
        ("builtin", "invoke_capability", "execution.factory._model_visible_tools"),
        ("builtin", "run_code_mode", "execution.factory._model_visible_tools"),
        ("builtin", "run_in_sandbox", "execution.factory._model_visible_tools"),
        ("builtin", "stage_rowset_write", "execution.factory._model_visible_tools"),
        ("builtin", "write_todos", "langchain.agents.middleware.TodoListMiddleware"),
        ("builtin", "execute", "deepagents.middleware.filesystem.FilesystemMiddleware"),
        ("builtin", "task", "delegation.subagents.atlas_task_tool"),
        ("subagent", "dispatch", "delegation.subagents.atlas_task_tool"),
        ("workspace", "ls", "capabilities.desktop.workspace_backend"),
        ("workspace", "read", "capabilities.desktop.workspace_backend"),
        ("workspace", "glob", "capabilities.desktop.workspace_backend"),
        ("workspace", "grep", "capabilities.desktop.workspace_backend"),
        ("workspace", "write", "capabilities.desktop.workspace_backend"),
        ("workspace", "edit", "capabilities.desktop.workspace_backend"),
        ("model", "artifact_content_part", "runtime_worker.handlers.run"),
        ("artifact", "publish", "agent_runtime.artifacts.service"),
        ("artifact", "revise", "agent_runtime.artifacts.service"),
        ("draft", "publish", "capabilities.backends.draft_backend"),
        (
            "desktop_browser",
            "browser_navigate",
            "capabilities.browser.desktop_browser_provider",
        ),
        (
            "desktop_browser",
            "browser_snapshot",
            "capabilities.browser.desktop_browser_provider",
        ),
        (
            "desktop_browser",
            "browser_wait",
            "capabilities.browser.desktop_browser_provider",
        ),
        (
            "desktop_browser",
            "browser_screenshot",
            "capabilities.browser.desktop_browser_provider",
        ),
        (
            "desktop_browser",
            "browser_close",
            "capabilities.browser.desktop_browser_provider",
        ),
        (
            "desktop_browser",
            "browser_click",
            "capabilities.browser.desktop_browser_provider",
        ),
        (
            "desktop_browser",
            "browser_submit",
            "capabilities.browser.desktop_browser_provider",
        ),
    )
    catalog = tuple(
        (connector, op, "capabilities.actions.catalog_data")
        for connector, op in sorted(ACTION_CATALOG.all_entries())
    )
    return tuple(
        CapabilityRegistration(capability=capability, op=op, source=source)
        for capability, op, source in (*fixed, *catalog)
    )


def registrations_from_model_tools(
    tools: Sequence[object],
) -> tuple[CapabilityRegistration, ...]:
    """Derive registrations from the concrete tools handed to the model."""

    registrations: list[CapabilityRegistration] = []
    for tool in tools:
        name = (
            tool.strip()
            if isinstance(tool, str)
            else str(getattr(tool, "name", "")).strip()
        )
        if not name:
            raise OperationConformanceError(
                "model-facing callable has no operation name"
            )
        entry = DEFAULT_BUILTIN_OPERATION_CATALOG.resolve_model_tool_name(name)
        capability, op = (
            (entry.capability, entry.op) if entry is not None else ("builtin", name)
        )
        registrations.append(
            CapabilityRegistration(
                capability=capability,
                op=op,
                source=f"{type(tool).__module__}.{type(tool).__qualname__}",
            )
        )
    return tuple(registrations)


__all__ = (
    "CapabilityRegistration",
    "OperationConformanceError",
    "OperationConformanceGate",
    "OperationDescriptorExemption",
    "current_capability_registrations",
    "load_operation_exemptions",
    "registrations_from_model_tools",
)
