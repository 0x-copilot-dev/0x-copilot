"""CI guard: no NEW ai-backend HTTP route ships with no caller anywhere.

27% of the ai-backend HTTP surface has no caller. Not "no test" — no *caller*:
neither the desktop/web client nor the facade that is the only thing allowed to
reach this service ever names the path. Those routes are authored, reviewed,
typed, unit-tested and mounted, and no product code can reach a single one of
them. They are the HTTP-layer form of the failure this repo keeps re-learning:
a thing built correctly, wired to nothing, green everywhere.

This gate does not delete them. It **baselines** them
(:data:`BASELINE_PATH`) and fails on the *next* one, which is the only
burn-down shape that survives contact with a large existing surface.

Reachability, precisely
=======================

A route is reachable when some known consumer names a path that the route's
pattern matches. There are three consumer families, and all three are needed —
a two-family version of this check reported live routes as dead:

* **The apps** (``packages/**``, ``apps/**`` TS/TSX) — the product clients.
* **The facade** (``services/backend-facade/src``) — the only service apps are
  allowed to call. It is a *per-route* proxy, not a catch-all: every upstream
  path it forwards to appears as a literal in its source, which is what makes
  its absence meaningful.
* **The core backend** (``services/backend/src``) — the consumer of
  ai-backend's service-token-gated ``/internal/v1/*`` routes, which are
  deliberately absent from the facade ("intentionally absent from the
  product-facing facade route table", ``runtime_api/app.py``). Omitting this
  root reports ``/internal/v1/audit/cursor`` — read every cycle by the SIEM
  export pump in ``backend_app/siem_export/pump.py`` — as unreachable.

Extraction rules that were not obvious
======================================

Four of these were each independently got wrong before landing:

1. **Routes are registered by call, not by decorator.** ``add_api_route`` is
   used 117 times; ``@router.get`` twice. A decorator scan finds 1 of 96 routes
   and silently calls the other 95 nonexistent. Both forms are handled.

2. **A prefix is per *router variable*, not per file.** ``routes.py`` builds
   five routers in five methods with five different prefixes (``/v1/agent``,
   ``/v1/usage``, ``/v1``, ``/v1/budgets``, ``/internal/v1``). Resolving "the
   file's prefix" mis-attributes four of them. Prefixes resolve per function
   scope, per variable, including the ``APIRouter(prefix=cls._PREFIX)`` form.

3. **The prefix often comes from the caller.** Seventeen
   ``register_*_routes(router)`` helpers take the router as a *parameter*, so
   nothing in the callee names its prefix — ``audit_list_routes.py`` registers a
   bare ``/audit/list`` that resolves to ``/internal/v1/audit/list`` only
   because of who calls it. Delegation is followed to a fixed point, so those
   routes get the caller's prefix instead of an empty one.

   This is also why the doubled-prefix reading of
   ``desktop_workspace_attestation.py`` is an artefact and not a bug: that
   router is built with **no** prefix and registers the fully-qualified
   ``/v1/agent/desktop-workspace-attestation``. Any extractor that applies a
   prefix borrowed from elsewhere renders it as
   ``/v1/agent/v1/agent/desktop-workspace-attestation``. The live app has no
   such path.

4. **Matching is a regex, anchored, with ``{param}`` as exactly one segment.**
   Substring or last-segment matching produces false orphans on any route with
   a parameter in the middle: ``/v1/agent/runs/{run_id}/surfaces`` is called
   constantly, always as ``/v1/agent/runs/${runId}/surfaces``, and a naive
   matcher sees no literal for it. ``{param}`` becomes ``[^/]+`` and the whole
   path must match end to end, so ``/v1/agent/conversations`` never satisfies
   ``/v1/agent/conversations/{id}`` and vice versa.

Callers are found as path-shaped *substrings* of string literals, not as whole
literals, because the real spellings are assembled:
``f"{self._ai_base}/internal/v1/audit/cursor"`` and
``` `${base}/v1/agent/runs/${id}/stream` ``` both yield a usable candidate.
``${...}`` collapses to one wildcard segment, matching the route side.

Known limits — read before trusting a verdict either way
========================================================

This is a **floor**, not a proof, and it is wrong in both directions in ways
worth naming:

*False orphan (why a baseline, not a hard fail).* A caller that assembles the
path from a variable holding more than the host — ``` `${conversationBase}/fork`
``` — contains no path-shaped substring and is invisible here. A gate that fails
the build on one of those is a gate someone deletes. So today's population is
recorded in :data:`BASELINE_PATH` with a reason per entry, and only routes
appearing *after* that fail. A baselined route that later gains a caller makes
its line stale, and a stale line also fails — so the file can only shrink.

*False reachable.* Any path-shaped literal counts, including one in a comment,
a docstring, a test fixture, an MSW mock, or a route *declaration* that a
consumer happens to serve at the same path as ai-backend's (``backend`` really
does declare its own ``/internal/v1/audit/list``). Distinguishing "I call this"
from "I mention this" needs dataflow the scan does not do. This direction only
ever *misses* an orphan, so it cannot redden a build — but it means "REACHABLE"
means "some consumer names it", not "some user can get to it".

*Granularity.* The unit is the **path**, not ``(path, method)``. The evidence on
the caller side is a path literal; the method sits in a separate argument that no
literal scan can reliably associate with it. A route whose GET is wired and whose
DELETE is not reads as reachable. Reporting per-method precision we do not have
would manufacture false failures instead.

*Depth.* Reachable-from-the-facade is not reachable-from-a-user: a facade route
with no TS caller is still orphaned one layer up, which this does not measure.
Nor does a live route say anything about how much of the module behind it is
live.

Usage::

    python tools/check_route_reachability.py             # default: ai-backend
    python tools/check_route_reachability.py <root>...   # explicit route root(s)
    python tools/check_route_reachability.py --list      # print every route + verdict

Exits non-zero listing every unreachable route that is not baselined, and every
baseline line that has gone stale.
"""

from __future__ import annotations

import argparse
import ast
import re
import sys
from collections import deque
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

#: Where ai-backend registers its HTTP surface.
DEFAULT_ROUTE_ROOTS: tuple[Path, ...] = (
    REPO_ROOT / "services" / "ai-backend" / "src" / "runtime_api",
)

#: Product clients. Both hosts mount the same shared surface, so a path named
#: by either one counts.
CLIENT_ROOTS: tuple[Path, ...] = (REPO_ROOT / "packages", REPO_ROOT / "apps")

#: Server-side consumers. The facade proxies the product surface; the core
#: backend is the only caller of ai-backend's ``/internal/v1/*`` routes.
SERVICE_CALLER_ROOTS: tuple[Path, ...] = (
    REPO_ROOT / "services" / "backend-facade" / "src",
    REPO_ROOT / "services" / "backend" / "src",
)

BASELINE_PATH: Path = REPO_ROOT / "tools" / "route_reachability_baseline.txt"

#: A candidate must start at one of these to be a plausible API path. Derived
#: from the route table's own roots; anything else is an asset URL or a local
#: filesystem path.
API_ROOTS: tuple[str, ...] = ("/v1/", "/internal/")

_SKIP_DIRS = frozenset(
    {
        ".venv",
        "venv",
        "__pycache__",
        "node_modules",
        "dist",
        "dist-electron",
        "build",
        ".vite",
        ".git",
        ".turbo",
        "coverage",
    }
)

_CLIENT_SUFFIXES = frozenset({".ts", ".tsx"})
_PYTHON_SUFFIXES = frozenset({".py"})

#: ``{conversation_id}`` on the route side, capturing the declaration so the
#: ``{surface_id:path}`` converter can be told apart — it is the one parameter
#: form that matches *across* ``/``.
_ROUTE_PARAM = re.compile(r"\{([^{}]*)\}")
#: Starlette's only multi-segment converter.
_PATH_CONVERTER = ":path"
#: An interpolation hole on the caller side. Both spellings occur and both mean
#: "one segment supplied at runtime": ``${runId}`` in a TS template literal and
#: ``{run_id}`` in a Python f-string, which is how the facade writes every
#: parameterised forward target (``f"/v1/usage/runs/{run_id}"``).
_TEMPLATE_HOLE = re.compile(r"\$?\{[^{}]*\}")
#: A path-shaped substring inside any literal: starts at an API root and runs to
#: the quote, whitespace, or a character no URL path carries. Interpolation
#: holes are admitted whole so they survive into the candidate; a stray brace is
#: not, so a candidate cannot run past the end of the string it lives in.
_PATH_CANDIDATE = re.compile(r"/(?:v1|internal)/(?:\$?\{[^{}]*\}|[^\s'\"`\\){}])*")

#: Marks the reason column in the baseline, matching the grain of
#: ``css_shadowing_baseline.txt``.
BASELINE_SEPARATOR = " :: "


class Route:
    """One registered HTTP route: its resolved path and where it is declared.

    ``path`` is the *display* spelling, with any Starlette converter stripped
    (``{surface_id:path}`` -> ``{surface_id}``) so it reads the same as the
    OpenAPI table and the baseline file. ``raw_path`` keeps the source spelling,
    because only that says whether a parameter spans one segment or many.
    """

    __slots__ = ("path", "raw_path", "methods", "file", "lineno")

    def __init__(
        self,
        *,
        path: str,
        methods: tuple[str, ...],
        file: Path,
        lineno: int,
    ) -> None:
        self.raw_path = path
        self.path = _ROUTE_PARAM.sub(
            lambda match: "{" + match.group(1).split(":", 1)[0] + "}", path
        )
        self.methods = methods
        self.file = file
        self.lineno = lineno

    @property
    def location(self) -> str:
        return f"{_display(self.file)}:{self.lineno}"

    def matcher(self) -> re.Pattern[str]:
        """An anchored regex in which every ``{param}`` is one path segment.

        Rule 4 in the module docstring: the whole path must match, so a route
        and its parameterised child never satisfy each other's callers.

        The exception is ``{param:path}``, which Starlette matches across ``/``
        — ``/v1/agent/surfaces/{surface_id:path}/regenerate`` really does accept
        a multi-segment id, so pinning it to one segment would invent orphans.
        """

        parts: list[str] = []
        cursor = 0
        for match in _ROUTE_PARAM.finditer(self.raw_path):
            parts.append(re.escape(self.raw_path[cursor : match.start()]))
            parts.append(".+" if match.group(1).endswith(_PATH_CONVERTER) else "[^/]+")
            cursor = match.end()
        parts.append(re.escape(self.raw_path[cursor:]))
        return re.compile("".join(parts))

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"Route({self.path!r}, {self.methods!r})"


class _Scope:
    """One function body, viewed as "what does it register, and on what".

    Kept separate from :class:`Route` because a scope's routes are not yet
    addressable: a scope that receives its router as a parameter does not know
    its own prefix until :class:`RouteExtractor` resolves who calls it.
    """

    __slots__ = ("name", "prefixes", "param_routers", "registrations", "delegations")

    def __init__(self, *, name: str) -> None:
        self.name = name
        #: router variable -> prefix, for routers constructed in this scope.
        self.prefixes: dict[str, str] = {}
        #: router variables that arrive as parameters (prefix owned by callers).
        self.param_routers: set[str] = set()
        #: ``(router_var, path, methods, lineno)`` per registration.
        self.registrations: list[tuple[str, str, tuple[str, ...], int]] = []
        #: ``(callee_function_name, router_var)`` per delegated registration.
        self.delegations: list[tuple[str, str]] = []

    @property
    def is_interesting(self) -> bool:
        return bool(self.registrations or self.delegations)


class _ModuleScanner:
    """Collect router construction, route registration and delegation, per scope.

    Everything here is deliberately syntactic. A route registered inside an
    ``if`` still exists, so no attempt is made to evaluate conditions — a
    conditionally-mounted route is a route.
    """

    #: ``APIRouter(...)`` — the only constructor that makes a prefix.
    ROUTER_FACTORY = "APIRouter"
    #: The registration call.
    ADD_ROUTE = "add_api_route"
    #: Decorator forms, mapped to the method they imply.
    _DECORATOR_METHODS = frozenset({"get", "post", "put", "patch", "delete", "head"})
    #: FastAPI's own default when ``methods=`` is omitted.
    _DEFAULT_METHODS = ("GET",)

    def __init__(self, *, file: Path, source: str) -> None:
        self._file = file
        self._source = source
        self._scopes: list[_Scope] = []

    def scan(self) -> list[_Scope]:
        try:
            tree = ast.parse(self._source)
        except SyntaxError:
            # A file that will not parse is the type checker's problem, not a
            # reason for a static gate to abort the whole scan.
            return []
        self._visit_body(tree.body, constants={}, scope=None)
        return [scope for scope in self._scopes if scope.is_interesting]

    def _visit_body(
        self,
        body: list[ast.stmt],
        *,
        constants: dict[str, str],
        scope: _Scope | None,
    ) -> None:
        for node in body:
            self._visit(node, constants=constants, scope=scope)

    def _visit(
        self,
        node: ast.stmt,
        *,
        constants: dict[str, str],
        scope: _Scope | None,
    ) -> None:
        if isinstance(node, ast.ClassDef):
            # A class body contributes constants (``_PREFIX = "/internal/dev"``)
            # that its methods dereference as ``cls._PREFIX``.
            merged = dict(constants)
            merged.update(self._string_constants(node))
            self._visit_body(node.body, constants=merged, scope=None)
            return

        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if scope is None:
                self._visit_function(node, constants=constants)
            else:
                # A function *inside* a scope is a handler, not a new scope. Its
                # decorators register on the enclosing scope's router — which is
                # the whole ``@router.get`` shape in ``audit_list_routes.py``.
                # Giving it its own scope orphans that route, and did.
                self._walk_scope(node, constants=constants, scope=scope)
            return

        if scope is not None:
            self._walk_scope(node, constants=constants, scope=scope)
            return

        # Module level: recurse into blocks so a conditionally defined
        # registrar is still seen.
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.stmt):
                self._visit(child, constants=constants, scope=scope)

    def _visit_function(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
        *,
        constants: dict[str, str],
    ) -> None:
        scope = _Scope(name=node.name)
        scope.param_routers.update(self._router_parameters(node))
        self._scopes.append(scope)
        for statement in node.body:
            self._visit(statement, constants=constants, scope=scope)

    def _walk_scope(
        self,
        node: ast.AST,
        *,
        constants: dict[str, str],
        scope: _Scope,
    ) -> None:
        """Attribute every construction, registration and delegation to ``scope``.

        One flat traversal rather than a statement recursion: the earlier
        two-path version collected calls twice for anything inside an ``if``.
        Nested classes are pruned — a class defined inside a registrar body
        registers nothing on that registrar's router.
        """

        stack: list[ast.AST] = [node]
        while stack:
            current = stack.pop()
            if isinstance(current, ast.ClassDef):
                continue
            if isinstance(current, ast.Assign):
                self._collect_router_assignment(
                    current, constants=constants, scope=scope
                )
            elif isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self._collect_decorators(current, scope=scope)
            elif isinstance(current, ast.Call):
                self._collect_call(current, scope=scope)
            stack.extend(ast.iter_child_nodes(current))

    @classmethod
    def _router_parameters(
        cls, node: ast.FunctionDef | ast.AsyncFunctionDef
    ) -> set[str]:
        """Parameters annotated ``APIRouter`` — a prefix owned by the caller.

        This is the ``register_*_routes(router: APIRouter)`` shape, which is how
        most of this service's surface is registered (rule 3).
        """

        found: set[str] = set()
        args = node.args
        for arg in (*args.posonlyargs, *args.args, *args.kwonlyargs):
            annotation = arg.annotation
            if annotation is None:
                continue
            name = (
                annotation.id
                if isinstance(annotation, ast.Name)
                else annotation.attr
                if isinstance(annotation, ast.Attribute)
                else None
            )
            if name == cls.ROUTER_FACTORY:
                found.add(arg.arg)
        return found

    def _collect_router_assignment(
        self,
        node: ast.Assign,
        *,
        constants: dict[str, str],
        scope: _Scope,
    ) -> None:
        value = node.value
        if not isinstance(value, ast.Call):
            return
        if self._called_name(value) != self.ROUTER_FACTORY:
            return
        prefix = self._resolve_prefix(value, constants=constants)
        for target in node.targets:
            if isinstance(target, ast.Name):
                scope.prefixes[target.id] = prefix
                # A locally built router owns its prefix; drop any same-named
                # parameter binding so the caller cannot also supply one.
                scope.param_routers.discard(target.id)

    @classmethod
    def _resolve_prefix(cls, call: ast.Call, *, constants: dict[str, str]) -> str:
        """The ``prefix=`` argument, literal or via a class constant.

        Absent means the empty prefix, which is correct and load-bearing: the
        desktop attestation router has no prefix and registers its full path
        inline. Inventing a prefix here is exactly the doubled-prefix artefact.
        """

        for keyword in call.keywords:
            if keyword.arg != "prefix":
                continue
            value = keyword.value
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                return value.value
            if isinstance(value, ast.Attribute) and value.attr in constants:
                return constants[value.attr]
        return ""

    def _collect_call(self, call: ast.Call, *, scope: _Scope) -> None:
        func = call.func
        if isinstance(func, ast.Attribute) and func.attr == self.ADD_ROUTE:
            if not isinstance(func.value, ast.Name):
                return
            path = self._first_string_argument(call)
            if path is None:
                return
            scope.registrations.append(
                (func.value.id, path, self._methods_of(call), call.lineno)
            )
            return

        # ``register_share_routes(router)`` — delegation. The callee is a bare
        # name; its definition is indexed globally because the caller and callee
        # are usually in different files.
        if isinstance(func, ast.Name):
            for arg in call.args:
                if isinstance(arg, ast.Name):
                    scope.delegations.append((func.id, arg.id))

    def _collect_decorators(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
        *,
        scope: _Scope,
    ) -> None:
        """``@router.get("/audit/list")`` on a handler nested in a registrar."""

        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call):
                continue
            func = decorator.func
            if not isinstance(func, ast.Attribute):
                continue
            if func.attr not in self._DECORATOR_METHODS:
                continue
            if not isinstance(func.value, ast.Name):
                continue
            path = self._first_string_argument(decorator)
            if path is None:
                continue
            scope.registrations.append(
                (func.value.id, path, (func.attr.upper(),), decorator.lineno)
            )

    @staticmethod
    def _first_string_argument(call: ast.Call) -> str | None:
        if not call.args:
            return None
        first = call.args[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            return first.value
        return None

    @classmethod
    def _methods_of(cls, call: ast.Call) -> tuple[str, ...]:
        for keyword in call.keywords:
            if keyword.arg != "methods":
                continue
            value = keyword.value
            if isinstance(value, (ast.List, ast.Tuple, ast.Set)):
                return tuple(
                    element.value.upper()
                    for element in value.elts
                    if isinstance(element, ast.Constant)
                    and isinstance(element.value, str)
                )
        return cls._DEFAULT_METHODS

    @staticmethod
    def _called_name(call: ast.Call) -> str | None:
        func = call.func
        if isinstance(func, ast.Name):
            return func.id
        if isinstance(func, ast.Attribute):
            return func.attr
        return None

    @staticmethod
    def _string_constants(node: ast.ClassDef) -> dict[str, str]:
        """Class-level ``ATTR = "<string>"`` bindings, annotated or not."""

        constants: dict[str, str] = {}
        for statement in node.body:
            if isinstance(statement, ast.AnnAssign):
                targets: list[ast.expr] = [statement.target]
                value = statement.value
            elif isinstance(statement, ast.Assign):
                targets = list(statement.targets)
                value = statement.value
            else:
                continue
            if not isinstance(value, ast.Constant) or not isinstance(value.value, str):
                continue
            for target in targets:
                if isinstance(target, ast.Name):
                    constants[target.id] = value.value
        return constants


class RouteExtractor:
    """Resolve every scope's registrations into fully-qualified paths.

    The interesting work is delegation: a scope that receives its router as a
    parameter has no prefix of its own, so its routes are only addressable once
    a caller is known. Resolution therefore runs as a worklist to a fixed point
    rather than a single pass — a registrar may delegate onward.
    """

    def __init__(self, *, roots: tuple[Path, ...]) -> None:
        self._roots = roots
        #: Scope identity -> declaring file, so a resolved route can report
        #: where it was written. Per instance: a class-level dict would leak
        #: between runs and, worse, between tests.
        self._files: dict[int, Path] = {}

    def extract(self) -> list[Route]:
        scopes: list[_Scope] = []
        for path in _iter_files(self._roots, suffixes=_PYTHON_SUFFIXES):
            source = _read(path)
            if source is None:
                continue
            for scope in _ModuleScanner(file=path, source=source).scan():
                scopes.append(scope)
                self._files[id(scope)] = path

        registrars: dict[str, list[_Scope]] = {}
        for scope in scopes:
            if scope.param_routers:
                registrars.setdefault(scope.name, []).append(scope)

        routes: dict[tuple[str, tuple[str, ...]], Route] = {}
        seen: set[tuple[int, str, str]] = set()
        work: deque[tuple[_Scope, str, str]] = deque()
        for scope in scopes:
            for var, prefix in scope.prefixes.items():
                work.append((scope, var, prefix))

        while work:
            scope, var, prefix = work.popleft()
            key = (id(scope), var, prefix)
            if key in seen:
                continue
            seen.add(key)

            for reg_var, path, methods, lineno in scope.registrations:
                if reg_var != var:
                    continue
                route = Route(
                    path=prefix + path,
                    methods=methods,
                    file=self._files[id(scope)],
                    lineno=lineno,
                )
                routes.setdefault((route.path, methods), route)

            for callee, call_var in scope.delegations:
                if call_var != var:
                    continue
                for target in registrars.get(callee, ()):
                    for param in target.param_routers:
                        work.append((target, param, prefix))

        return sorted(routes.values(), key=lambda route: (route.path, route.methods))


class CallerIndex:
    """Every path-shaped substring any known consumer names.

    Substrings, not whole literals, because the real spellings are assembled:
    ``f"{base}/internal/v1/audit/cursor"`` carries the path but does not start
    with it. Template holes collapse to a single wildcard segment so the caller
    side lines up with the route side's ``{param}``.
    """

    #: Stand-in for ``${...}``. Contains no ``/``, so it satisfies exactly one
    #: ``[^/]+`` segment — the same width as a route parameter.
    WILDCARD = "\x00"

    def __init__(self, *, candidates: frozenset[str]) -> None:
        self._candidates = candidates

    @classmethod
    def build(
        cls,
        *,
        client_roots: tuple[Path, ...] = CLIENT_ROOTS,
        service_roots: tuple[Path, ...] = SERVICE_CALLER_ROOTS,
    ) -> CallerIndex:
        candidates: set[str] = set()
        for path in _iter_files(client_roots, suffixes=_CLIENT_SUFFIXES):
            candidates.update(cls._candidates_in(_read(path)))
        for path in _iter_files(service_roots, suffixes=_PYTHON_SUFFIXES):
            candidates.update(cls._candidates_in(_read(path)))
        return cls(candidates=frozenset(candidates))

    @classmethod
    def _candidates_in(cls, text: str | None) -> set[str]:
        if text is None:
            return set()
        found: set[str] = set()
        for raw in _PATH_CANDIDATE.findall(text):
            candidate = cls.normalise(raw)
            if candidate:
                found.add(candidate)
        return found

    @classmethod
    def normalise(cls, raw: str) -> str:
        """Strip query/fragment, drop a trailing slash, widen template holes."""

        candidate = raw.split("?", 1)[0].split("#", 1)[0]
        candidate = _TEMPLATE_HOLE.sub(cls.WILDCARD, candidate)
        if len(candidate) > 1 and candidate.endswith("/"):
            candidate = candidate[:-1]
        if not candidate.startswith(API_ROOTS):
            return ""
        return candidate

    def reaches(self, route: Route) -> bool:
        matcher = route.matcher()
        return any(
            matcher.fullmatch(candidate) is not None for candidate in self._candidates
        )

    def __len__(self) -> int:
        return len(self._candidates)


class Baseline:
    """The recorded, reasoned population of already-unreachable routes.

    There is deliberately no ``--update-baseline`` writer, for the same reason
    ``css_shadowing_baseline.txt`` has none: the only sanctioned way to add a
    line is to write the reason by hand, where a reviewer reads it in the diff.
    A line whose route has since gained a caller is *stale* and fails, so the
    file can only ever shrink.
    """

    def __init__(self, *, entries: dict[str, str], path: Path) -> None:
        self.entries = entries
        self.path = path

    @classmethod
    def load(cls, path: Path = BASELINE_PATH) -> Baseline:
        entries: dict[str, str] = {}
        text = _read(path)
        if text is None:
            return cls(entries=entries, path=path)
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            route, separator, reason = stripped.partition(BASELINE_SEPARATOR.strip())
            entries[route.strip()] = reason.strip() if separator else ""
        return cls(entries=entries, path=path)

    def __contains__(self, path: str) -> bool:
        return path in self.entries


def _iter_files(roots: tuple[Path, ...], *, suffixes: frozenset[str]) -> list[Path]:
    results: list[Path] = []
    for root in roots:
        if root.is_file():
            if root.suffix in suffixes:
                results.append(root)
            continue
        if not root.is_dir():
            continue
        for path in root.rglob("*"):
            if path.suffix not in suffixes or not path.is_file():
                continue
            if any(part in _SKIP_DIRS for part in path.parts):
                continue
            results.append(path)
    return sorted(results)


def _display(path: Path) -> str:
    """Repo-relative when possible, absolute otherwise.

    Not cosmetic: a caller may point ``--baseline`` at a path outside the repo
    (every test that exercises the failure output does), and an unguarded
    ``relative_to`` raises ``ValueError`` from inside the error reporter — so
    the gate would crash precisely when it had something to say.
    """

    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def unreachable_routes(
    *,
    routes: list[Route],
    callers: CallerIndex,
) -> list[Route]:
    """One entry per unreachable *path* (see the docstring on granularity)."""

    by_path: dict[str, Route] = {}
    reachable: set[str] = set()
    for route in routes:
        by_path.setdefault(route.path, route)
        if callers.reaches(route):
            reachable.add(route.path)
    return [route for path, route in sorted(by_path.items()) if path not in reachable]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="check_route_reachability")
    parser.add_argument(
        "paths",
        nargs="*",
        type=Path,
        help="Route source root(s) to scan (default: ai-backend runtime_api).",
    )
    parser.add_argument(
        "--baseline",
        type=Path,
        default=BASELINE_PATH,
        help="Baseline file of already-unreachable routes.",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="Print every extracted route with its verdict, then exit 0.",
    )
    args = parser.parse_args(argv)

    roots: tuple[Path, ...] = tuple(args.paths) if args.paths else DEFAULT_ROUTE_ROOTS
    routes = RouteExtractor(roots=roots).extract()
    callers = CallerIndex.build()
    baseline = Baseline.load(args.baseline)

    if args.list:
        for route in routes:
            verdict = "REACHABLE" if callers.reaches(route) else "unreachable"
            methods = ",".join(route.methods)
            sys.stdout.write(f"{verdict:11s} {methods:12s} {route.path}\n")
        sys.stdout.write(
            f"\n{len(routes)} route(s), {len(callers)} caller path candidate(s)\n"
        )
        return 0

    unreachable = unreachable_routes(routes=routes, callers=callers)
    unreachable_paths = {route.path for route in unreachable}

    new = [route for route in unreachable if route.path not in baseline]
    stale = sorted(path for path in baseline.entries if path not in unreachable_paths)

    if not new and not stale:
        sys.stdout.write(
            "OK: route reachability "
            f"({len(routes)} route(s) scanned, {len(unreachable)} unreachable and "
            "baselined, 0 new)\n"
        )
        return 0

    if new:
        sys.stderr.write(
            "FAIL: new ai-backend route(s) with no caller in the apps, the "
            "facade, or the core backend\n"
        )
        for route in new:
            sys.stderr.write(
                f"  {route.location}: {','.join(route.methods)} {route.path} "
                "is registered but no consumer names it. Wire it to a caller, "
                "delete it, or — if a caller assembles the path from variables "
                "and this is a false orphan — add a reasoned line to "
                f"{_display(baseline.path)}:\n"
                f"      {route.path}{BASELINE_SEPARATOR}<why this is reachable "
                "in reality>\n"
            )
    if stale:
        sys.stderr.write(
            f"FAIL: stale line(s) in {_display(baseline.path)} — these "
            "routes now have a caller (or no longer exist). Delete the line(s):\n"
        )
        for path in stale:
            sys.stderr.write(f"  {path}\n")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
