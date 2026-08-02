"""W3 — the worker's F6 presence gate, and the dark path's untouched import graph.

Feature-off parity is asserted twice here, on purpose, because the two claims are
different. The in-process tests assert that an unconfigured deployment *composes
nothing*. The subprocess tests assert that it *loads nothing* — a feature can be
behaviourally dark and still have paid for itself in imports, module-level
construction, and startup cost, and W2 set the standard that the import graph is
part of parity.

One honesty note about the scope of that second claim. The concurrency *package*
is already on every deployment's import graph and has been since F6.2:
``runtime_api.schemas.events`` imports ``...concurrency.batch_journal`` for the
batch event payload, and importing a submodule runs its package ``__init__``.
That edge predates W3, is outside this lane, and no assertion here can honestly
pretend otherwise. What W3 can and does hold to the standard is the module it
added — the graph seam itself, the only F6 module on the path between a model
turn and the batch machinery — which stays unloaded unless F6 is configured.
"""

from __future__ import annotations

from pathlib import Path
import subprocess
import sys
from typing import Any

from agent_runtime.capabilities.concurrency import (
    ConcurrencyMode,
    ConcurrencyPolicyField,
    PolicySource,
    SideEffectKind,
)
from agent_runtime.capabilities.mcp.annotations import (
    McpToolAnnotations,
    McpToolAnnotationsRegistry,
)
from runtime_worker.batch_concurrency_composition import (
    BatchConcurrencyComposer,
    BatchConcurrencyEnvironment,
    EnvironmentConcurrencyKillSwitchSource,
    build_batch_concurrency_composer,
)

from tests.unit.agent_runtime.capabilities.middleware.test_runtime_tool_control_batch import (  # noqa: E501
    _ORG,
    _TRACE,
    _InMemoryBatchJournal,
    _binding,
)

_SOURCE_ROOT = Path(__file__).resolve().parents[3] / "src"


class _Events:
    """A stand-in for the event store; never called on any tested path."""


class _Snapshots:
    """A stand-in for the snapshot store; never called on any tested path."""


def _import_path() -> str:
    """Return the interpreter path the in-process test run already resolves with."""

    import os

    return os.pathsep.join(path for path in sys.path if path)


def _scrubbed_environ() -> dict[str, str]:
    import os

    return {
        key: value for key, value in os.environ.items() if not key.startswith("F6_")
    }


class TestThePresenceGate:
    """Presence, never meaning — and read before anything F6-owned is imported."""

    def test_an_unconfigured_deployment_builds_no_composer(self) -> None:
        assert (
            build_batch_concurrency_composer(
                events=_Events(),  # type: ignore[arg-type]
                snapshots=_Snapshots(),  # type: ignore[arg-type]
                environ={},
            )
            is None
        )

    def test_a_configured_deployment_builds_one(self) -> None:
        composer = build_batch_concurrency_composer(
            events=_Events(),  # type: ignore[arg-type]
            snapshots=_Snapshots(),  # type: ignore[arg-type]
            environ={BatchConcurrencyEnvironment.MAX_PARALLELISM: "4"},
        )

        assert isinstance(composer, BatchConcurrencyComposer)

    def test_a_worker_without_the_required_stores_builds_no_composer(self) -> None:
        """F6 journals to the canonical run event log or it does not run."""

        assert (
            build_batch_concurrency_composer(
                events=None,
                snapshots=_Snapshots(),  # type: ignore[arg-type]
                environ={BatchConcurrencyEnvironment.MAX_PARALLELISM: "4"},
            )
            is None
        )
        assert (
            build_batch_concurrency_composer(
                events=_Events(),  # type: ignore[arg-type]
                snapshots=None,
                environ={BatchConcurrencyEnvironment.MAX_PARALLELISM: "4"},
            )
            is None
        )

    def test_every_malformed_ceiling_resolves_to_serial(self) -> None:
        for raw in ("", "   ", "nope", "0", "-4", "3.5"):
            assert (
                BatchConcurrencyEnvironment.max_parallelism(
                    {BatchConcurrencyEnvironment.MAX_PARALLELISM: raw}
                )
                == 1
            )

    def test_the_ceiling_is_clamped_to_the_hard_bound(self) -> None:
        assert (
            BatchConcurrencyEnvironment.max_parallelism(
                {BatchConcurrencyEnvironment.MAX_PARALLELISM: "100000"}
            )
            == BatchConcurrencyEnvironment.MAX_ALLOWED_PARALLELISM
        )

    def test_a_misspelled_but_present_value_is_still_configured(self) -> None:
        """Presence is the gate; meaning is decided by the conservative parse."""

        assert BatchConcurrencyEnvironment.is_configured(
            {BatchConcurrencyEnvironment.MAX_PARALLELISM: "banana"}
        )
        assert (
            BatchConcurrencyEnvironment.max_parallelism(
                {BatchConcurrencyEnvironment.MAX_PARALLELISM: "banana"}
            )
            == 1
        )

    def test_an_unreadable_catalog_declares_nothing(self) -> None:
        assert (
            BatchConcurrencyEnvironment.raw_catalog(
                {BatchConcurrencyEnvironment.CATALOG: "{not json"}
            )
            is None
        )

    def test_the_kill_switch_source_reads_its_value_on_every_call(self) -> None:
        environ = {BatchConcurrencyEnvironment.KILL_SWITCH: "global"}
        source = EnvironmentConcurrencyKillSwitchSource(environ)

        assert source.current_kill_switch_directives() == "global"
        environ.pop(BatchConcurrencyEnvironment.KILL_SWITCH)
        assert source.current_kill_switch_directives() is None


class TestTheCatalogIsOperatorDeclaredAndConservative:
    """Only a catalog may establish, and one bad entry declares nothing."""

    @staticmethod
    def composer(catalog: str) -> BatchConcurrencyComposer:
        return BatchConcurrencyComposer(
            events=_Events(),  # type: ignore[arg-type]
            snapshots=_Snapshots(),  # type: ignore[arg-type]
            environ={
                BatchConcurrencyEnvironment.MAX_PARALLELISM: "4",
                BatchConcurrencyEnvironment.CATALOG: catalog,
            },
        )

    def test_a_declared_capability_is_attributed_to_the_product_catalog(self) -> None:
        from agent_runtime.capabilities.concurrency import (
            ConcurrencyMode,
            PolicySource,
            SideEffectKind,
        )

        composer = self.composer(
            '{"github:search": {"mode": "parallel_safe", "side_effect": "read"}}'
        )

        declarations = composer._declarations()  # noqa: SLF001 - parsing under test.

        assert set(declarations) == {"github:search"}
        declaration = declarations["github:search"][0]
        assert declaration.source is PolicySource.PRODUCT_CATALOG
        assert declaration.mode is ConcurrencyMode.PARALLEL_SAFE
        assert declaration.side_effect is SideEffectKind.READ

    def test_an_unparseable_field_narrows_rather_than_being_ignored(self) -> None:
        from agent_runtime.capabilities.concurrency import (
            ConcurrencyMode,
            SideEffectKind,
        )

        composer = self.composer(
            '{"github:search": {"mode": "go_wild", "side_effect": "read"}}'
        )

        declaration = composer._declarations()["github:search"][0]  # noqa: SLF001

        assert declaration.mode is ConcurrencyMode.SERIAL
        assert declaration.side_effect is SideEffectKind.READ
        assert declaration.rejections

    def test_a_non_object_catalog_declares_nothing(self) -> None:
        assert self.composer('["github:search"]')._declarations() == {}  # noqa: SLF001


#: The one F6 module W3 added to the path between the graph and the batch
#: machinery. The *package* around it is already on every deployment's import
#: graph and has been since F6.2: ``runtime_api.schemas.events`` imports
#: ``...concurrency.batch_journal`` for the batch event payload, which runs the
#: package ``__init__``. That edge predates W3 and is not W3's to remove — so
#: the parity claim these tests can honestly make is about the seam W3 built,
#: and it is enforced by keeping ``graph_admission`` out of that ``__init__``.
_GRAPH_SEAM_MODULE = "agent_runtime.capabilities.concurrency.graph_admission"


def _loaded_seam_modules(entry_point: str) -> str:
    """Return the graph-seam modules a fresh interpreter loads for ``entry_point``.

    A subprocess is essential. In-process, some earlier test has already imported
    everything, so the question "what does this path load" can only be asked of
    an interpreter that has loaded nothing.
    """

    script = (
        "import sys\n"
        f"{entry_point}\n"
        "leaked = [\n"
        "    name for name in sys.modules\n"
        f"    if name.startswith({_GRAPH_SEAM_MODULE!r})\n"
        "]\n"
        "print(','.join(sorted(leaked)))\n"
    )
    completed = subprocess.run(  # noqa: S603
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        check=False,
        env={**_scrubbed_environ(), "PYTHONPATH": _import_path()},
    )
    assert completed.returncode == 0, completed.stderr
    return completed.stdout.strip()


class TestFeatureOffParity:
    """Not merely the same behaviour — the same work, and the same modules."""

    def test_an_unconfigured_composition_root_never_loads_the_graph_seam(self) -> None:
        """The gate is read before the seam is imported, not after."""

        loaded = _loaded_seam_modules(
            "from runtime_worker.batch_concurrency_composition import (\n"
            "    build_batch_concurrency_composer,\n"
            ")\n"
            "assert build_batch_concurrency_composer(\n"
            "    events=object(), snapshots=object(), environ={}\n"
            ") is None"
        )

        assert loaded == ""

    def test_importing_the_graph_tool_seam_never_loads_the_batch_seam(self) -> None:
        """The middleware is on every run's path; F6's join must not be.

        This is the assertion that makes the ``__init__`` exclusion load-bearing
        rather than stylistic: re-export ``graph_admission`` from the package and
        this test fails, because the package is imported by every deployment.
        """

        loaded = _loaded_seam_modules(
            "import agent_runtime.capabilities.middleware.runtime_tool_control"
        )

        assert loaded == ""

    def test_the_configured_root_does_load_it(self) -> None:
        """The negative above is a real constraint, not a tautology."""

        loaded = _loaded_seam_modules(
            "from agent_runtime.capabilities.concurrency import graph_admission  # noqa"
        )

        assert loaded == _GRAPH_SEAM_MODULE

    def test_an_unconfigured_run_installs_no_batch_admission(self) -> None:
        """The context slot the middleware reads stays empty."""

        from agent_runtime.execution.contracts import RuntimeBatchAdmissionContext

        assert RuntimeBatchAdmissionContext.current() is None


class TestBothCompositionRootsAreWired:
    """A source canary: the run and approval roots must not drift apart.

    Cheap and deliberately literal. A behavioural test can only cover the root a
    given test happens to drive; this one fails the moment either root stops
    installing F6, which is exactly the regression W2 found for F3.
    """

    @staticmethod
    def _read(name: str) -> str:
        return (_SOURCE_ROOT / "runtime_worker" / "handlers" / name).read_text()

    def test_both_roots_compose_and_install_the_same_way(self) -> None:
        for name in ("run.py", "approval.py"):
            source = self._read(name)
            assert "self._compose_batch_admission(" in source, name
            assert "install_parallel_admission(batch_admission)" in source, name
            assert "RuntimeBatchAdmissionContext.install(" in source, name
            assert "RuntimeBatchAdmissionContext.reset(" in source, name

    def test_both_roots_name_the_runs_approval_authority(self) -> None:
        """BUG-19's regression, guarded where root drift is already guarded.

        Both roots did compose and install F6 all along. What neither did was
        name the run authority BUG-15's rule folds with the catalog's claim, so
        ``GraphApprovalRequirementResolver`` answered *not declared* on every
        call and the rule could not fire. That is root drift of a different
        shape rather than a different problem, and it belongs in the same
        canary — a root that stops passing the context is a root that has
        silently re-opened the bug.
        """

        for name in ("run.py", "approval.py"):
            assert "runtime_context=run.runtime_context" in self._read(name), name

    def test_the_worker_loop_builds_one_composer_for_both_roots(self) -> None:
        source = (_SOURCE_ROOT / "runtime_worker" / "loop.py").read_text()

        assert source.count("build_batch_concurrency_composer(") == 1
        assert (
            source.count("batch_concurrency_composer=batch_concurrency_composer") == 2
        )


#: The MCP umbrella the model calls, and the connector call an operator files a
#: policy for. ``call_mcp_tool`` names its server and tool inside its arguments,
#: so the catalog is keyed ``server:tool`` — the same key
#: ``graph_capability_key`` derives — while the graph tool is the umbrella.
_MCP_TOOL = "call_mcp_tool"
_SERVER = "linear"
_INNER_TOOL = "create_issue"
_MCP_ARGUMENTS: dict[str, str] = {"server_name": _SERVER, "tool_name": _INNER_TOOL}


class _ProviderHintComposer(BatchConcurrencyComposer):
    """The real composer, with only the durable journal's storage substituted.

    Every seam it assembles is production — the presence gate, the operator
    catalog parse, and the one this class exists to pin: the ``policies=`` source
    the run installs. Only ``_journal`` is swapped, for the same reason
    ``_RecordingComposer`` swaps it: the durable adapter has its own tests and
    ``compose`` never writes through it here.
    """

    def __init__(self, *, catalog: str) -> None:
        super().__init__(
            events=_Events(),  # type: ignore[arg-type]
            snapshots=_Snapshots(),  # type: ignore[arg-type]
            environ={
                BatchConcurrencyEnvironment.MAX_PARALLELISM: "4",
                BatchConcurrencyEnvironment.CATALOG: catalog,
            },
        )

    def _journal(self) -> Any:
        return _InMemoryBatchJournal()


class TestTheComposerWiresTheProviderNarrowingTier:
    """The composer installs the provider-narrowing tier, not the bare catalog.

    ``test_provider_hints`` proves the decorator narrows and rejects over-claims
    in isolation; these prove the object ``_compose`` actually hands the run *is*
    that decorator, reading this run's live MCP annotations. Together they close
    the gap F6.1 left: the ``TRUSTED_PROVIDER`` tier had no production source, so
    a connector's self-declared danger reached no real run.

    The annotations are registered before ``compose`` and read at
    ``resolution_for``. In production the per-run registry binds *after* compose,
    so the tier reads the ``ContextVar`` lazily at plan time — an eager snapshot
    taken during compose would be empty.
    """

    @staticmethod
    def _compose(catalog: str) -> Any:
        """Compose one run's admission exactly as both worker handlers do."""

        return _ProviderHintComposer(catalog=catalog).compose(
            org_id=_ORG,
            trace_id=_TRACE,
            control=_binding(),
            runtime_context=None,
        )

    @staticmethod
    def _resolution(admission: Any) -> Any:
        policies = admission._policies  # noqa: SLF001 - the wiring is under test.
        return policies.resolution_for(tool_name=_MCP_TOOL, arguments=_MCP_ARGUMENTS)

    def test_a_destructive_hint_narrows_the_operators_parallel_read(self) -> None:
        """A connector self-declaring danger flips a catalog read to serial.

        The operator declared ``linear:create_issue`` the widest thing honest of
        a read — parallel-safe, naturally idempotent. The server's own
        ``destructiveHint`` narrows it on every field it touches, and the
        effective trust source drops to the provider that caused it. On the bare
        ``DeclaredConcurrencyPolicySource`` this stays parallel-safe under
        ``PRODUCT_CATALOG`` and every assertion below fails — which is the point.
        """

        registry: dict[tuple[str, str], McpToolAnnotations] = {}
        token = McpToolAnnotationsRegistry.bind_for_run(registry)
        try:
            McpToolAnnotationsRegistry.register(
                _SERVER,
                _INNER_TOOL,
                McpToolAnnotations.from_wire({"destructiveHint": True}),
            )
            admission = self._compose(
                '{"linear:create_issue": {"mode": "parallel_safe",'
                ' "side_effect": "read", "idempotency": "natural"}}'
            )
            assert admission is not None, "the composition root refused to compose"
            resolution = self._resolution(admission)
        finally:
            McpToolAnnotationsRegistry.unbind(token)

        assert resolution is not None
        assert resolution.policy.mode is ConcurrencyMode.SERIAL
        assert resolution.policy.side_effect is SideEffectKind.IRREVERSIBLE_WRITE
        assert resolution.policy.policy_source is PolicySource.TRUSTED_PROVIDER
        assert PolicySource.TRUSTED_PROVIDER in resolution.contributing_sources

    def test_an_over_claiming_hint_is_rejected_never_applied(self) -> None:
        """A server declaring itself safer than the catalog cannot widen it.

        The operator declared an irreversible, serial, non-idempotent write. The
        server claims ``readOnlyHint`` and ``idempotentHint`` — both widenings.
        Not one field moves, the source stays ``PRODUCT_CATALOG``, and each
        over-claim is a typed widening rejection attributed to the provider tier.
        On the bare catalog source there is no tier to reject anything, so
        ``widening_rejections`` is empty and the set assertion fails.
        """

        registry: dict[tuple[str, str], McpToolAnnotations] = {}
        token = McpToolAnnotationsRegistry.bind_for_run(registry)
        try:
            McpToolAnnotationsRegistry.register(
                _SERVER,
                _INNER_TOOL,
                McpToolAnnotations.from_wire(
                    {"readOnlyHint": True, "idempotentHint": True}
                ),
            )
            admission = self._compose(
                '{"linear:create_issue": {"mode": "serial",'
                ' "side_effect": "irreversible_write", "idempotency": "none"}}'
            )
            assert admission is not None, "the composition root refused to compose"
            resolution = self._resolution(admission)
        finally:
            McpToolAnnotationsRegistry.unbind(token)

        assert resolution is not None
        assert resolution.policy.mode is ConcurrencyMode.SERIAL
        assert resolution.policy.side_effect is SideEffectKind.IRREVERSIBLE_WRITE
        assert resolution.policy.policy_source is PolicySource.PRODUCT_CATALOG
        assert {
            rejection.policy_field for rejection in resolution.widening_rejections
        } == {ConcurrencyPolicyField.SIDE_EFFECT, ConcurrencyPolicyField.IDEMPOTENCY}
        assert all(
            rejection.source is PolicySource.TRUSTED_PROVIDER
            for rejection in resolution.widening_rejections
        )
