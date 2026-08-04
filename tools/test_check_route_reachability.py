"""Unit tests for the route-reachability gate.

Two halves. :class:`TestExtractionRules` pins the four extraction rules that
were each got wrong before this landed — decorator-only scanning, per-file
prefixes, prefixes owned by the caller, and substring matching — because every
one of them produced a *confident wrong answer* rather than an obvious failure.
:class:`TestAgainstTheRealTree` pins the same rules against the actual
repository, so re-narrowing the scan fails here rather than in review.

The real-tree assertions are deliberately about shape ("this route resolves to
exactly one ``/v1/agent``"), not about counts, so ordinary route churn does not
red the suite.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent

sys.path.insert(0, str(HERE))

from check_route_reachability import (  # noqa: E402
    BASELINE_PATH,
    DEFAULT_ROUTE_ROOTS,
    Baseline,
    CallerIndex,
    Route,
    RouteExtractor,
    _ModuleScanner,
    main,
    unreachable_routes,
)


def _write(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


def _paths(root: Path) -> set[str]:
    return {route.path for route in RouteExtractor(roots=(root,)).extract()}


def _index(*candidates: str) -> CallerIndex:
    return CallerIndex(
        candidates=frozenset(CallerIndex.normalise(c) for c in candidates)
    )


class RouteSourceMixin:
    """Source fragments for each registration shape the service really uses."""

    #: The dominant shape: a classmethod builds a prefixed router and registers
    #: paths on it by call, not by decorator.
    CLASS_ROUTER = """
class WidgetApiRouter:
    @classmethod
    def create_router(cls) -> APIRouter:
        router = APIRouter(prefix="/v1/widgets", tags=["widgets"])
        router.add_api_route(
            "/things",
            cls.list_things,
            methods=["GET"],
        )
        router.add_api_route(
            "/things/{thing_id}/parts",
            cls.get_parts,
            methods=["GET", "POST"],
        )
        return router
"""

    #: Two routers in ONE file with different prefixes. Resolving "the file's
    #: prefix" mis-attributes one of them; routes.py does this five times over.
    TWO_ROUTERS_ONE_FILE = """
class AlphaRouter:
    @classmethod
    def create_router(cls) -> APIRouter:
        router = APIRouter(prefix="/v1/alpha")
        router.add_api_route("/items", cls.items, methods=["GET"])
        return router


class BetaRouter:
    @classmethod
    def create_router(cls) -> APIRouter:
        router = APIRouter(prefix="/internal/v1/beta")
        router.add_api_route("/items", cls.items, methods=["GET"])
        return router
"""

    #: ``prefix=cls._PREFIX`` — the prefix lives in a class constant.
    CONSTANT_PREFIX = """
class DiagnosticsRouter:
    _PREFIX = "/internal/dev/diagnostics"

    @classmethod
    def create_router(cls) -> APIRouter:
        router = APIRouter(prefix=cls._PREFIX)
        router.add_api_route("/snapshot", cls.snapshot, methods=["GET"])
        return router
"""

    #: NO prefix, full path inline. This is desktop_workspace_attestation.py,
    #: and inventing a prefix for it is the doubled-prefix artefact.
    NO_PREFIX = """
class AttestationRouter:
    @classmethod
    def create_router(cls) -> APIRouter:
        router = APIRouter(tags=["desktop-capability"])
        router.add_api_route(
            "/v1/agent/desktop-workspace-attestation",
            cls.submit,
            methods=["POST"],
        )
        return router
"""

    #: The registrar shape: the callee never names its own prefix, so the route
    #: is only addressable once the caller is known. 17 modules do this.
    REGISTRAR = '''
def register_widget_routes(router: APIRouter) -> None:
    router.add_api_route("/widgets", _list_widgets, methods=["GET"])


def register_audit_routes(router: APIRouter) -> None:
    """The decorator variant, on a handler nested inside the registrar."""

    @router.get("/audit/list")
    async def list_audit():
        return {}


class HostRouter:
    @classmethod
    def create_router(cls) -> APIRouter:
        router = APIRouter(prefix="/internal/v1")
        register_widget_routes(router)
        register_audit_routes(router)
        return router
'''


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------


class TestExtractionRules(RouteSourceMixin):
    def test_add_api_route_is_found(self, tmp_path: Path) -> None:
        # Rule 1. A decorator-only scan finds 1 of 96 routes in this service.
        _write(tmp_path / "widgets.py", self.CLASS_ROUTER)
        assert _paths(tmp_path) == {
            "/v1/widgets/things",
            "/v1/widgets/things/{thing_id}/parts",
        }

    def test_methods_are_read_from_the_keyword(self, tmp_path: Path) -> None:
        _write(tmp_path / "widgets.py", self.CLASS_ROUTER)
        by_path = {
            r.path: r.methods for r in RouteExtractor(roots=(tmp_path,)).extract()
        }
        assert by_path["/v1/widgets/things"] == ("GET",)
        assert by_path["/v1/widgets/things/{thing_id}/parts"] == ("GET", "POST")

    def test_prefix_is_per_router_not_per_file(self, tmp_path: Path) -> None:
        # Rule 2. The bug this replaces: one prefix resolved for the whole file.
        _write(tmp_path / "routes.py", self.TWO_ROUTERS_ONE_FILE)
        assert _paths(tmp_path) == {"/v1/alpha/items", "/internal/v1/beta/items"}

    def test_prefix_from_a_class_constant_resolves(self, tmp_path: Path) -> None:
        _write(tmp_path / "diagnostics.py", self.CONSTANT_PREFIX)
        assert _paths(tmp_path) == {"/internal/dev/diagnostics/snapshot"}

    def test_a_router_without_a_prefix_gets_no_prefix(self, tmp_path: Path) -> None:
        # Rule 3's corollary and the doubled-prefix regression test: this router
        # declares no prefix, so its inline path is the whole path.
        _write(tmp_path / "attestation.py", self.NO_PREFIX)
        assert _paths(tmp_path) == {"/v1/agent/desktop-workspace-attestation"}

    def test_registrar_inherits_the_callers_prefix(self, tmp_path: Path) -> None:
        # Rule 3. Both the call form and the decorator-on-a-nested-handler form
        # must pick up ``/internal/v1`` from whoever called the registrar.
        _write(tmp_path / "host.py", self.REGISTRAR)
        assert _paths(tmp_path) == {
            "/internal/v1/widgets",
            "/internal/v1/audit/list",
        }

    def test_registrar_defined_in_another_file_still_resolves(
        self, tmp_path: Path
    ) -> None:
        # The real layout: the registrar and its caller are in different modules.
        _write(
            tmp_path / "widgets.py",
            "def register_widget_routes(router: APIRouter) -> None:\n"
            '    router.add_api_route("/widgets", _list, methods=["GET"])\n',
        )
        _write(
            tmp_path / "host.py",
            "class HostRouter:\n"
            "    @classmethod\n"
            "    def create_router(cls) -> APIRouter:\n"
            '        router = APIRouter(prefix="/v1/agent")\n'
            "        register_widget_routes(router)\n"
            "        return router\n",
        )
        assert _paths(tmp_path) == {"/v1/agent/widgets"}

    def test_conditionally_registered_routes_still_count(self, tmp_path: Path) -> None:
        # A flag-gated route is still a route that needs a caller; the real tree
        # registers artifacts, effect-stages and pending-work-v2 this way.
        _write(
            tmp_path / "host.py",
            "class HostRouter:\n"
            "    @classmethod\n"
            "    def create_router(cls, enabled) -> APIRouter:\n"
            '        router = APIRouter(prefix="/v1/agent")\n'
            "        if enabled:\n"
            '            router.add_api_route("/gated", cls.gated, methods=["GET"])\n'
            "        return router\n",
        )
        assert _paths(tmp_path) == {"/v1/agent/gated"}

    def test_a_route_is_not_collected_twice(self, tmp_path: Path) -> None:
        # Guards the traversal: an earlier version walked nested blocks twice.
        _write(
            tmp_path / "host.py",
            "class HostRouter:\n"
            "    @classmethod\n"
            "    def create_router(cls, enabled) -> APIRouter:\n"
            '        router = APIRouter(prefix="/v1/agent")\n'
            "        if enabled:\n"
            '            router.add_api_route("/gated", cls.gated, methods=["GET"])\n'
            "        return router\n",
        )
        routes = RouteExtractor(roots=(tmp_path,)).extract()
        assert len(routes) == 1

    def test_a_docstring_mentioning_a_decorator_is_not_a_route(
        self, tmp_path: Path
    ) -> None:
        # runtime_api/identity.py documents ``@router.get("/something")`` in its
        # module docstring. A grep counts it; the AST cannot.
        _write(
            tmp_path / "identity.py",
            '"""Docs.\n\n    @router.get("/something")\n"""\n',
        )
        assert _paths(tmp_path) == set()

    def test_unparseable_source_does_not_abort_the_scan(self, tmp_path: Path) -> None:
        _write(tmp_path / "broken.py", "def (:\n")
        _write(tmp_path / "widgets.py", self.CLASS_ROUTER)
        assert "/v1/widgets/things" in _paths(tmp_path)

    def test_scanner_reports_no_scope_for_a_routeless_module(
        self, tmp_path: Path
    ) -> None:
        scanner = _ModuleScanner(file=tmp_path / "x.py", source="X = 1\n")
        assert scanner.scan() == []


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------


def _route(path: str) -> Route:
    return Route(path=path, methods=("GET",), file=Path("x.py"), lineno=1)


class TestMatching:
    def test_a_mid_path_parameter_matches_a_concrete_caller(self) -> None:
        # Rule 4, the false-positive the brief called out by name: naive
        # substring or last-segment matching reports this route orphaned.
        route = _route("/v1/agent/runs/{run_id}/surfaces")
        assert _index("/v1/agent/runs/run-1/surfaces").reaches(route)

    def test_a_template_literal_matches_a_parameter(self) -> None:
        route = _route("/v1/agent/runs/{run_id}/surfaces")
        assert _index("/v1/agent/runs/${runId}/surfaces").reaches(route)

    def test_an_fstring_hole_matches_a_parameter(self) -> None:
        # How the facade writes every parameterised forward target.
        route = _route("/v1/usage/runs/{run_id}")
        assert _index("/v1/usage/runs/{run_id}").reaches(route)

    def test_a_parent_path_does_not_satisfy_its_parameterised_child(self) -> None:
        child = _route("/v1/agent/conversations/{conversation_id}")
        assert not _index("/v1/agent/conversations").reaches(child)

    def test_a_parameterised_caller_does_not_satisfy_the_parent(self) -> None:
        parent = _route("/v1/agent/conversations")
        assert not _index("/v1/agent/conversations/conv-1").reaches(parent)

    def test_a_parameter_spans_exactly_one_segment(self) -> None:
        route = _route("/v1/agent/conversations/{conversation_id}")
        assert not _index("/v1/agent/conversations/a/b").reaches(route)

    def test_the_path_converter_spans_many_segments(self) -> None:
        # Starlette's ``:path`` really does match across ``/``; pinning it to one
        # segment invents orphans for the surfaces routes.
        route = _route("/v1/agent/surfaces/{surface_id:path}/regenerate")
        assert _index("/v1/agent/surfaces/a/b/c/regenerate").reaches(route)

    def test_the_path_converter_is_stripped_from_the_display_path(self) -> None:
        assert (
            _route("/v1/agent/surfaces/{surface_id:path}/regenerate").path
            == "/v1/agent/surfaces/{surface_id}/regenerate"
        )

    def test_a_longer_caller_path_does_not_match(self) -> None:
        route = _route("/v1/agent/runs")
        assert not _index("/v1/agent/runs/run-1/cancel").reaches(route)


class TestCallerExtraction:
    def test_a_query_string_is_stripped(self) -> None:
        assert CallerIndex.normalise("/v1/agent/runs?after=3") == "/v1/agent/runs"

    def test_a_fragment_is_stripped(self) -> None:
        assert CallerIndex.normalise("/v1/agent/runs#top") == "/v1/agent/runs"

    def test_a_trailing_slash_is_dropped(self) -> None:
        assert CallerIndex.normalise("/v1/agent/runs/") == "/v1/agent/runs"

    def test_a_non_api_path_is_not_a_candidate(self) -> None:
        assert CallerIndex.normalise("/assets/logo.svg") == ""

    def test_a_path_assembled_onto_a_base_url_is_still_found(
        self, tmp_path: Path
    ) -> None:
        # ``f"{self._ai_base}/internal/v1/audit/cursor"`` — the real spelling in
        # backend's SIEM pump. Requiring the literal to *start* with the path
        # misses it, and reports a route read every cycle as unreachable.
        _write(
            tmp_path / "pump.py",
            "URL = f\"{base.rstrip('/')}/internal/v1/audit/cursor\"\n",
        )
        index = CallerIndex.build(client_roots=(), service_roots=(tmp_path,))
        assert index.reaches(_route("/internal/v1/audit/cursor"))

    def test_a_typescript_caller_is_found(self, tmp_path: Path) -> None:
        _write(
            tmp_path / "api.ts",
            "await get(`/v1/agent/conversations/${encodeURIComponent(id)}/fork`);\n",
        )
        index = CallerIndex.build(client_roots=(tmp_path,), service_roots=())
        assert index.reaches(_route("/v1/agent/conversations/{conversation_id}/fork"))

    def test_node_modules_are_not_scanned(self, tmp_path: Path) -> None:
        _write(tmp_path / "node_modules" / "dep" / "x.ts", '"/v1/agent/ghost";\n')
        index = CallerIndex.build(client_roots=(tmp_path,), service_roots=())
        assert not index.reaches(_route("/v1/agent/ghost"))

    def test_a_candidate_stops_at_the_end_of_its_literal(self) -> None:
        index = CallerIndex(
            candidates=frozenset(
                CallerIndex._candidates_in('get("/v1/agent/runs", other)')
            )
        )
        assert index.reaches(_route("/v1/agent/runs"))
        assert not index.reaches(_route("/v1/agent/runs/{run_id}"))


# ---------------------------------------------------------------------------
# The gate itself
# ---------------------------------------------------------------------------


class TestUnreachableRoutes:
    def test_one_entry_per_path_not_per_method(self) -> None:
        routes = [
            Route(path="/v1/x", methods=("GET",), file=Path("a.py"), lineno=1),
            Route(path="/v1/x", methods=("POST",), file=Path("a.py"), lineno=2),
        ]
        assert [
            r.path for r in unreachable_routes(routes=routes, callers=_index())
        ] == ["/v1/x"]

    def test_a_path_reached_by_any_method_row_is_reachable(self) -> None:
        routes = [
            Route(path="/v1/x", methods=("GET",), file=Path("a.py"), lineno=1),
            Route(path="/v1/x", methods=("POST",), file=Path("a.py"), lineno=2),
        ]
        assert unreachable_routes(routes=routes, callers=_index("/v1/x")) == []


class TestBaseline:
    BASELINE = (
        "# a comment\n\n/v1/todo-extractions :: no facade forward and no client\n"
    )

    def test_a_reason_is_parsed(self, tmp_path: Path) -> None:
        path = _write(tmp_path / "baseline.txt", self.BASELINE)
        baseline = Baseline.load(path)
        assert baseline.entries == {
            "/v1/todo-extractions": "no facade forward and no client"
        }

    def test_comments_and_blank_lines_are_ignored(self, tmp_path: Path) -> None:
        path = _write(tmp_path / "baseline.txt", self.BASELINE)
        assert "# a comment" not in Baseline.load(path).entries

    def test_a_missing_file_is_an_empty_baseline(self, tmp_path: Path) -> None:
        assert Baseline.load(tmp_path / "absent.txt").entries == {}


class TestGateOutcomes(RouteSourceMixin):
    """End-to-end: a new orphan fails, a stale line fails, an exact match passes."""

    @staticmethod
    def _no_callers(monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            CallerIndex,
            "build",
            classmethod(lambda cls, **_: cls(candidates=frozenset())),
        )

    def test_an_unbaselined_orphan_fails(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._no_callers(monkeypatch)
        src = tmp_path / "src"
        _write(src / "widgets.py", self.CLASS_ROUTER)
        baseline = _write(tmp_path / "baseline.txt", "")
        assert main([str(src), "--baseline", str(baseline)]) == 1

    def test_a_baselined_orphan_passes(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._no_callers(monkeypatch)
        src = tmp_path / "src"
        _write(src / "widgets.py", self.CLASS_ROUTER)
        baseline = _write(
            tmp_path / "baseline.txt",
            "/v1/widgets/things :: reason\n"
            "/v1/widgets/things/{thing_id}/parts :: reason\n",
        )
        assert main([str(src), "--baseline", str(baseline)]) == 0

    def test_a_stale_baseline_line_fails(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The property that makes the file shrink-only: once a route gains a
        # caller (or is deleted), its line must go.
        self._no_callers(monkeypatch)
        src = tmp_path / "src"
        _write(src / "widgets.py", self.CLASS_ROUTER)
        baseline = _write(
            tmp_path / "baseline.txt",
            "/v1/widgets/things :: reason\n"
            "/v1/widgets/things/{thing_id}/parts :: reason\n"
            "/v1/widgets/deleted :: this route no longer exists\n",
        )
        assert main([str(src), "--baseline", str(baseline)]) == 1

    def test_the_failure_names_the_route_and_its_source(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        self._no_callers(monkeypatch)
        src = tmp_path / "src"
        _write(src / "widgets.py", self.CLASS_ROUTER)
        baseline = _write(tmp_path / "baseline.txt", "")
        main([str(src), "--baseline", str(baseline)])
        err = capsys.readouterr().err
        assert "/v1/widgets/things" in err
        assert "widgets.py:" in err

    def test_list_mode_reports_every_route_and_exits_zero(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        self._no_callers(monkeypatch)
        src = tmp_path / "src"
        _write(src / "widgets.py", self.CLASS_ROUTER)
        assert main([str(src), "--list"]) == 0
        assert "/v1/widgets/things" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# The real tree — the half that stops the gate going blind
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def real_routes() -> list[Route]:
    return RouteExtractor(roots=DEFAULT_ROUTE_ROOTS).extract()


class TestAgainstTheRealTree:
    def test_the_scan_finds_the_whole_surface(self, real_routes: list[Route]) -> None:
        # A shape assertion, not a count: if a regression drops a prefix or a
        # registrar, this collapses far below 100.
        assert len(real_routes) > 100

    def test_no_route_carries_a_doubled_prefix(self, real_routes: list[Route]) -> None:
        # ORPHAN-AUDIT.md reports
        # ``/v1/agent/v1/agent/desktop-workspace-attestation``. It is an
        # extraction artefact — the live app has no such path — and this is the
        # assertion that stops it being reintroduced.
        doubled = [r.path for r in real_routes if "/v1/agent/v1/agent" in r.path]
        assert doubled == []

    def test_the_attestation_route_resolves_once(
        self, real_routes: list[Route]
    ) -> None:
        assert "/v1/agent/desktop-workspace-attestation" in {
            r.path for r in real_routes
        }

    def test_the_five_routers_in_routes_py_keep_their_own_prefixes(
        self, real_routes: list[Route]
    ) -> None:
        # Per-file prefix resolution collapses these onto one another.
        paths = {r.path for r in real_routes}
        assert "/v1/agent/conversations" in paths  # /v1/agent
        assert "/v1/usage/me" in paths  # /v1/usage
        assert "/v1/todo-extractions" in paths  # /v1  (NOT /v1/agent)
        assert "/v1/budgets" in paths  # /v1/budgets
        assert "/internal/v1/audit/cursor" in paths  # /internal/v1

    def test_a_registrar_route_inherits_its_callers_prefix(
        self, real_routes: list[Route]
    ) -> None:
        # audit_list_routes.py registers a bare ``/audit/list`` by decorator on
        # a nested handler; only the caller says it is ``/internal/v1``.
        assert "/internal/v1/audit/list" in {r.path for r in real_routes}

    def test_a_mid_path_parameter_route_is_reachable(
        self, real_routes: list[Route]
    ) -> None:
        # The brief's named false positive. Every client spells it
        # ``/v1/agent/runs/${runId}/surfaces``.
        callers = CallerIndex.build()
        surfaces = [
            r for r in real_routes if r.path == "/v1/agent/runs/{run_id}/surfaces"
        ]
        assert surfaces, "the surfaces route should exist"
        assert all(callers.reaches(r) for r in surfaces)

    def test_the_gate_passes_on_the_committed_baseline(self) -> None:
        # The definition of done: today's tree reports exactly the baselined
        # set, with nothing unexplained.
        assert main([]) == 0

    def test_every_baseline_line_carries_a_reason(self) -> None:
        baseline = Baseline.load(BASELINE_PATH)
        assert baseline.entries, "the baseline should not be empty"
        missing = [path for path, reason in baseline.entries.items() if not reason]
        assert missing == []
