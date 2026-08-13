"""Compile a model-authored plan into an executable, bounded, acyclic schedule.

Everything that can be decided *before* any tool runs is decided here: step
count, duplicate ids, tool authorization, reference targets, cycles, and the
concurrency layering. A plan that reaches :class:`ToolProgramExecutor` has
already been proven schedulable, so the executor only has to deal with runtime
outcomes.

Authorization is not a check this module performs by inspecting names — it is
delegated to the injected ``ExternalFunctionResolver``, which in production
resolves only against the run's already scope-filtered model-visible toolset. An
unknown or unauthorized name simply fails to resolve.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from agent_runtime.capabilities.interpreter.contracts import ExternalFunctionSpec
from agent_runtime.capabilities.interpreter.service import ExternalFunctionResolver
from agent_runtime.capabilities.tool_program.contracts import (
    RunToolProgramInput,
    StepRef,
    ToolProgramError,
    ToolProgramErrorCode,
    ToolProgramLimits,
    ToolProgramStep,
)
from agent_runtime.execution.contracts import JsonValue


class ReferenceWalker:
    """Reads and rewrites the :class:`StepRef` markers inside a JSON structure.

    One class, two directions: :meth:`targets` collects the step ids a value
    depends on (used to build the dependency graph before anything runs), and
    :meth:`resolve` substitutes recorded outputs into the same positions at
    execution time. Keeping both here is what stops the graph and the
    substitution from disagreeing about what counts as a reference.
    """

    @classmethod
    def targets(cls, value: object, *, into: set[str]) -> None:
        """Collect every referenced step id in ``value`` into ``into``."""

        if StepRef.marker(value):
            into.add(StepRef.parse(value).step)
            return
        if isinstance(value, Mapping):
            for item in value.values():
                cls.targets(item, into=into)
            return
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
            for item in value:
                cls.targets(item, into=into)

    @classmethod
    def resolve(
        cls,
        value: object,
        *,
        outputs: Mapping[str, JsonValue],
        owner: str | None,
    ) -> JsonValue:
        """Return ``value`` with every reference replaced by a recorded output.

        ``owner`` is the step id blamed when a path does not resolve (``None``
        for the final projection), so the model is told which step to fix.
        """

        if StepRef.marker(value):
            return cls._dereference(StepRef.parse(value), outputs=outputs, owner=owner)
        if isinstance(value, Mapping):
            return {
                str(key): cls.resolve(item, outputs=outputs, owner=owner)
                for key, item in value.items()
            }
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
            return [cls.resolve(item, outputs=outputs, owner=owner) for item in value]
        return value  # type: ignore[return-value]

    @classmethod
    def _dereference(
        cls,
        ref: StepRef,
        *,
        outputs: Mapping[str, JsonValue],
        owner: str | None,
    ) -> JsonValue:
        if ref.step not in outputs:
            raise ToolProgramError(
                ToolProgramErrorCode.REFERENCE_UNRESOLVED,
                f"step '{ref.step}' produced no output to reference",
                step_id=owner or ref.step,
            )
        current: object = outputs[ref.step]
        for index, segment in enumerate(ref.path):
            current = cls._descend(
                current, segment=segment, ref=ref, depth=index, owner=owner
            )
        return current  # type: ignore[return-value]

    @classmethod
    def _descend(
        cls,
        current: object,
        *,
        segment: str | int,
        ref: StepRef,
        depth: int,
        owner: str | None,
    ) -> object:
        if isinstance(segment, str) and isinstance(current, Mapping):
            if segment in current:
                return current[segment]
        elif isinstance(current, Sequence) and not isinstance(current, (str, bytes)):
            if isinstance(segment, int) and -len(current) <= segment < len(current):
                return current[segment]
        raise ToolProgramError(
            ToolProgramErrorCode.REFERENCE_UNRESOLVED,
            f"reference to step '{ref.step}' does not resolve at path "
            f"position {depth} ({segment!r})",
            step_id=owner or ref.step,
        )


class ToolProgramPlan:
    """A validated plan: authorized steps, resolved specs, and execution layers.

    ``layers`` is the schedule. Every step inside one layer is independent of
    every other step in it, so a layer is exactly the unit that may run
    concurrently; layer *N+1* never starts before layer *N* has fully settled.
    """

    def __init__(
        self,
        *,
        steps: Mapping[str, ToolProgramStep],
        specs: Mapping[str, ExternalFunctionSpec],
        layers: tuple[tuple[str, ...], ...],
        projection: JsonValue,
    ) -> None:
        self.steps = dict(steps)
        self.specs = dict(specs)
        self.layers = layers
        self.projection = projection

    @classmethod
    def compile(
        cls,
        program: RunToolProgramInput,
        *,
        resolver: ExternalFunctionResolver,
        limits: ToolProgramLimits,
    ) -> "ToolProgramPlan":
        """Validate and schedule ``program``, or raise :class:`ToolProgramError`."""

        steps = cls._indexed(program.steps, limits=limits)
        specs = cls._authorize(steps, resolver=resolver)
        edges = cls._edges(steps, projection=program.result)
        return cls(
            steps=steps,
            specs=specs,
            layers=cls._layered(steps, edges=edges),
            projection=program.result,
        )

    @staticmethod
    def _indexed(
        steps: Sequence[ToolProgramStep], *, limits: ToolProgramLimits
    ) -> dict[str, ToolProgramStep]:
        if not steps:
            raise ToolProgramError(
                ToolProgramErrorCode.INVALID_PLAN, "a program needs at least one step"
            )
        if len(steps) > limits.max_steps:
            raise ToolProgramError(
                ToolProgramErrorCode.STEP_LIMIT_EXCEEDED,
                f"a program may contain at most {limits.max_steps} steps; "
                f"got {len(steps)}",
            )
        indexed: dict[str, ToolProgramStep] = {}
        for step in steps:
            if step.id in indexed:
                raise ToolProgramError(
                    ToolProgramErrorCode.INVALID_PLAN,
                    f"duplicate step id '{step.id}'",
                    step_id=step.id,
                )
            indexed[step.id] = step
        return indexed

    @staticmethod
    def _authorize(
        steps: Mapping[str, ToolProgramStep], *, resolver: ExternalFunctionResolver
    ) -> dict[str, ExternalFunctionSpec]:
        specs: dict[str, ExternalFunctionSpec] = {}
        for step_id, step in steps.items():
            spec = resolver.resolve(step.tool)
            if spec is None:
                raise ToolProgramError(
                    ToolProgramErrorCode.UNKNOWN_TOOL,
                    f"step '{step_id}' names a tool this run cannot call",
                    step_id=step_id,
                )
            specs[step_id] = spec
        return specs

    @staticmethod
    def _edges(
        steps: Mapping[str, ToolProgramStep], *, projection: JsonValue
    ) -> dict[str, frozenset[str]]:
        """Build ``step -> steps it waits on`` from references plus ``depends_on``."""

        edges: dict[str, frozenset[str]] = {}
        for step_id, step in steps.items():
            targets: set[str] = set(step.depends_on)
            ReferenceWalker.targets(step.arguments, into=targets)
            for target in targets:
                if target not in steps:
                    raise ToolProgramError(
                        ToolProgramErrorCode.UNKNOWN_STEP_REFERENCE,
                        f"step '{step_id}' references unknown step '{target}'",
                        step_id=step_id,
                    )
                if target == step_id:
                    raise ToolProgramError(
                        ToolProgramErrorCode.CYCLIC_DEPENDENCY,
                        f"step '{step_id}' references itself",
                        step_id=step_id,
                    )
            edges[step_id] = frozenset(targets)
        projected: set[str] = set()
        ReferenceWalker.targets(projection, into=projected)
        for target in sorted(projected - set(steps)):
            raise ToolProgramError(
                ToolProgramErrorCode.UNKNOWN_STEP_REFERENCE,
                f"the result projection references unknown step '{target}'",
            )
        return edges

    @staticmethod
    def _layered(
        steps: Mapping[str, ToolProgramStep], *, edges: Mapping[str, frozenset[str]]
    ) -> tuple[tuple[str, ...], ...]:
        """Kahn layering. Anything left unscheduled is, by definition, a cycle."""

        pending = set(steps)
        layers: list[tuple[str, ...]] = []
        settled: set[str] = set()
        while pending:
            ready = tuple(sorted(step for step in pending if edges[step] <= settled))
            if not ready:
                raise ToolProgramError(
                    ToolProgramErrorCode.CYCLIC_DEPENDENCY,
                    f"the program's steps form a dependency cycle: {sorted(pending)}",
                    step_id=sorted(pending)[0],
                )
            layers.append(ready)
            settled.update(ready)
            pending.difference_update(ready)
        return tuple(layers)


__all__ = ("ReferenceWalker", "ToolProgramPlan")
