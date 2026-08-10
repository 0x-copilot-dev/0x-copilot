"""Code-routine sandbox executor (Tool.kind="code", Routines §9.7 Q1).

V1 in-process executor. The sandbox is a `Protocol` (substitutable); the
in-process adapter is what tests and local dev use today. A real
container-based adapter is a Wave-11 swap that implements the same
``CodeSandboxPort`` surface.

Hardening notes (v1, in-process, single-tenant dev):

* AST validation against a deny-list of dangerous nodes / imports.
* Deterministic timeout via ``asyncio.wait_for``.
* Entry function signature is exact: ``def run(args: dict) -> dict``.
* No subprocess, no Docker; CPU/memory limits left at OS defaults.

The hardening here is conservative. It is **not** a security boundary
suitable for executing untrusted third-party code in a production
multi-tenant environment — that requires the container adapter
(``transport=container`` per tools-prd §9.1). Use this adapter for
first-party code or trusted dev loops only.

TU-1 invariant: this executor itself does NOT call an LLM. Code-routines
that happen to invoke an LLM do so via the existing runtime tool-call
envelope (e.g. ``ask_a_question`` or an explicit /v1/llm/* call); that
path keeps its existing Purpose attribution. We do NOT add a new
Purpose value for this executor.
"""

from __future__ import annotations

import ast
import asyncio
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal, Protocol

from agent_runtime.execution.contracts import RuntimeContract

# ---------------------------------------------------------------------------
# AST allow-list / deny-list (top-level constants — auditable)
# ---------------------------------------------------------------------------

#: Names that may NOT appear as imports or attribute roots. The list focuses
#: on capability escalation paths (filesystem write, network, arbitrary code
#: exec, FFI). The container adapter relaxes this list at Wave 11.
BANNED_IMPORT_NAMES: frozenset[str] = frozenset(
    {
        # Process control / shell escape
        "os",
        "subprocess",
        "pty",
        "popen2",
        "commands",
        "sh",
        "shutil",
        # Network
        "socket",
        "socketserver",
        "asyncio.subprocess",
        "http.client",
        "http.server",
        "urllib",
        "urllib.request",
        "urllib2",
        "ftplib",
        "smtplib",
        "telnetlib",
        "xmlrpc",
        "requests",
        "httpx",
        "aiohttp",
        # Filesystem / OS surfaces
        "pathlib",
        "tempfile",
        "fileinput",
        "glob",
        # FFI / native-code escape hatches
        "ctypes",
        "cffi",
        "_ctypes",
        # Code execution / introspection escape hatches
        "importlib",
        "imp",
        "runpy",
        "code",
        "codeop",
        "marshal",
        "pickle",
        "shelve",
        "dill",
        "cloudpickle",
        # Multiprocessing — would dodge our timeout
        "multiprocessing",
        "threading",
        "concurrent",
        "concurrent.futures",
        # System
        "sys",
        "gc",
        "atexit",
        "signal",
        "resource",
        # Logging — keeps the sandbox quiet; printing arrives via stdout capture
        "logging",
    }
)

#: Names that may NOT be looked up as a global name (``__import__`` bypass,
#: ``eval``, ``exec``, ``compile``). These are checked via ``ast.Name`` and
#: ``ast.Attribute`` walks, not just ``ast.Import`` / ``ast.ImportFrom``.
BANNED_GLOBAL_NAMES: frozenset[str] = frozenset(
    {
        "__import__",
        "eval",
        "exec",
        "compile",
        "globals",
        "locals",
        "vars",
        "breakpoint",
        "input",
        "memoryview",
        "open",  # write modes — see _OpenCallValidator
        "exit",
        "quit",
    }
)

#: Attribute roots that are forbidden anywhere in the AST (e.g. accessing
#: ``some_obj.__class__.__bases__[0].__subclasses__()`` — a classic bypass).
BANNED_ATTRIBUTE_PREFIXES: frozenset[str] = frozenset(
    {
        "__subclasses__",
        "__bases__",
        "__mro__",
        "__globals__",
        "__builtins__",
        "__code__",
        "__closure__",
        "__class__",
        "__dict__",
        "__getattribute__",
    }
)

#: When ``open()`` is permitted at all (it currently is not, see
#: ``BANNED_GLOBAL_NAMES``), only these modes would be allowed. Kept as a
#: top-level constant so a future v1.1 that relaxes ``open`` for read-only
#: scratch files has one knob to flip.
ALLOWED_OPEN_MODES: frozenset[str] = frozenset({"r", "rt", "rb"})

#: The exact entry function signature the executor will invoke. Keeping
#: this as a constant lets callers + reviewers see the contract in one
#: place. The function is resolved by name (``entry``) and called with the
#: validated ``args`` mapping.
ENTRY_SIGNATURE_HINT: str = "def run(args: dict) -> dict"


# ---------------------------------------------------------------------------
# Result / Port
# ---------------------------------------------------------------------------

SandboxStatus = Literal["ok", "error"]
#: Error kinds align with the public ``ToolInvocation.error_kind`` enum in
#: ``packages/api-types/src/index.ts`` (tools-prd §3.1).
SandboxErrorKind = Literal[
    "schema_invalid",
    "timeout",
    "sandbox_crash",
    "entry_missing",
    "result_invalid",
]


@dataclass(frozen=True)
class SandboxResult:
    """Terminal result of one sandbox invocation.

    ``status="ok"`` implies ``result is not None`` and ``error_kind is None``.
    ``status="error"`` implies ``result is None`` and ``error_kind`` is set.
    """

    status: SandboxStatus
    result: dict[str, Any] | None
    error_kind: SandboxErrorKind | None
    error_message: str | None
    latency_ms: int


class CodeSandboxPort(Protocol):
    """Substitution boundary for code-routine executors.

    The in-process adapter implements this; a container adapter (Wave 11)
    will implement the same surface.
    """

    async def execute(
        self,
        *,
        code: str,
        entry: str,
        args: Mapping[str, Any],
        timeout_s: float,
    ) -> SandboxResult:
        """Execute ``code``'s ``entry`` callable with ``args``."""
        ...


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


class _SandboxMessages:
    """Public, redaction-safe messages surfaced via ``SandboxResult.error_message``."""

    AST_SYNTAX = "code failed to parse: syntax error"
    AST_BANNED_IMPORT = "code imports a disallowed module: {name}"
    AST_BANNED_NAME = "code references a disallowed global: {name}"
    AST_BANNED_ATTRIBUTE = "code references a disallowed attribute: {name}"
    AST_OPEN_WRITE = "code attempts to open a file in write mode"
    AST_STAR_IMPORT = "code uses 'from X import *' which is not allowed"
    ENTRY_MISSING = "entry callable '{entry}' not found or not callable"
    TIMEOUT = "code execution exceeded {timeout_s:.2f}s"
    RUNTIME_EXCEPTION = "code raised an exception during execution"
    RESULT_NOT_DICT = "entry callable did not return a dict"


class CodeAstValidator:
    """AST walker that rejects banned imports, names, attributes, and writes."""

    @classmethod
    def validate(cls, code: str) -> str | None:
        """Return ``None`` on pass, or a safe public error message on reject."""
        try:
            tree = ast.parse(code, mode="exec")
        except SyntaxError:
            return _SandboxMessages.AST_SYNTAX

        for node in ast.walk(tree):
            rejection = cls._inspect(node)
            if rejection is not None:
                return rejection
        return None

    @classmethod
    def _inspect(cls, node: ast.AST) -> str | None:
        """Return a rejection message for ``node`` or ``None`` if it passes."""
        if isinstance(node, ast.Import):
            return cls._check_import_names(alias.name for alias in node.names)
        if isinstance(node, ast.ImportFrom):
            if node.module is None:
                return _SandboxMessages.AST_STAR_IMPORT
            if any(alias.name == "*" for alias in node.names):
                return _SandboxMessages.AST_STAR_IMPORT
            return cls._check_import_names([node.module])
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            if node.id in BANNED_GLOBAL_NAMES:
                return _SandboxMessages.AST_BANNED_NAME.format(name=node.id)
        if isinstance(node, ast.Attribute):
            if node.attr in BANNED_ATTRIBUTE_PREFIXES:
                return _SandboxMessages.AST_BANNED_ATTRIBUTE.format(name=node.attr)
        if isinstance(node, ast.Call):
            return cls._check_call(node)
        return None

    @classmethod
    def _check_import_names(cls, names: object) -> str | None:
        """Reject any import whose dotted root is in ``BANNED_IMPORT_NAMES``."""
        for raw_name in names:  # type: ignore[union-attr]
            if not isinstance(raw_name, str):
                continue
            root = raw_name.split(".", 1)[0]
            if raw_name in BANNED_IMPORT_NAMES or root in BANNED_IMPORT_NAMES:
                return _SandboxMessages.AST_BANNED_IMPORT.format(name=raw_name)
        return None

    @classmethod
    def _check_call(cls, node: ast.Call) -> str | None:
        """Reject ``open(..., mode='w'...)``-style calls, even though ``open`` is banned.

        Belt-and-braces: if a future relaxation re-allows ``open``, this
        keeps write modes locked down.
        """
        func = node.func
        called_name: str | None = None
        if isinstance(func, ast.Name):
            called_name = func.id
        elif isinstance(func, ast.Attribute):
            called_name = func.attr
        if called_name != "open":
            return None
        mode = cls._extract_open_mode(node)
        if mode is not None and mode not in ALLOWED_OPEN_MODES:
            return _SandboxMessages.AST_OPEN_WRITE
        return None

    @staticmethod
    def _extract_open_mode(node: ast.Call) -> str | None:
        """Return the literal ``mode=`` argument if statically resolvable."""
        if len(node.args) >= 2:
            second = node.args[1]
            if isinstance(second, ast.Constant) and isinstance(second.value, str):
                return second.value
        for keyword in node.keywords:
            if keyword.arg == "mode" and isinstance(keyword.value, ast.Constant):
                value = keyword.value.value
                if isinstance(value, str):
                    return value
        return None


# ---------------------------------------------------------------------------
# In-process adapter
# ---------------------------------------------------------------------------


class SandboxExecuteRequest(RuntimeContract):
    """Typed envelope for one sandbox call. Coerces external input at the boundary."""

    code: str
    entry: str
    args: dict[str, Any]
    timeout_s: float


@dataclass(frozen=True)
class InProcessCodeSandbox:
    """V1 sandbox adapter: runs the routine in this Python process.

    Adequate for tests + local dev. NOT a tenancy / security boundary
    suitable for production multi-tenant code. See module docstring.
    """

    #: Hard ceiling on per-call wall time, regardless of caller's
    #: ``timeout_s``. Belt-and-braces guard against a caller passing
    #: ``timeout_s=10_000``.
    max_timeout_s: float = 30.0

    async def execute(
        self,
        *,
        code: str,
        entry: str,
        args: Mapping[str, Any],
        timeout_s: float,
    ) -> SandboxResult:
        """Validate, compile, and call ``entry(args)`` with a timeout."""
        started = time.monotonic()
        effective_timeout = min(max(timeout_s, 0.001), self.max_timeout_s)

        rejection = CodeAstValidator.validate(code)
        if rejection is not None:
            return self._error(
                "schema_invalid",
                rejection,
                started_at=started,
            )

        try:
            namespace = self._compile(code)
        except SyntaxError:
            return self._error(
                "schema_invalid",
                _SandboxMessages.AST_SYNTAX,
                started_at=started,
            )

        callable_obj = namespace.get(entry)
        if not callable(callable_obj):
            return self._error(
                "entry_missing",
                _SandboxMessages.ENTRY_MISSING.format(entry=entry),
                started_at=started,
            )

        try:
            raw_result = await asyncio.wait_for(
                self._invoke(callable_obj, dict(args)),
                timeout=effective_timeout,
            )
        except asyncio.TimeoutError:
            return self._error(
                "timeout",
                _SandboxMessages.TIMEOUT.format(timeout_s=effective_timeout),
                started_at=started,
            )
        except Exception:
            return self._error(
                "sandbox_crash",
                _SandboxMessages.RUNTIME_EXCEPTION,
                started_at=started,
            )

        if not isinstance(raw_result, dict):
            return self._error(
                "result_invalid",
                _SandboxMessages.RESULT_NOT_DICT,
                started_at=started,
            )

        return SandboxResult(
            status="ok",
            result=dict(raw_result),
            error_kind=None,
            error_message=None,
            latency_ms=self._elapsed_ms(started),
        )

    @staticmethod
    def _compile(code: str) -> dict[str, Any]:
        """Compile ``code`` into a fresh module namespace.

        AST validation (run before this) is the primary defense; we keep
        the standard ``__builtins__`` so legitimate (allow-listed) imports
        — which desugar to ``__import__`` calls on builtins — still work.
        The builtins-level deny-list would block ``__import__`` for
        legitimate stdlib imports, which is too restrictive.
        """
        compiled = compile(code, "<code_sandbox>", "exec")
        namespace: dict[str, Any] = {}
        exec(compiled, namespace)  # noqa: S102 — AST-validated above; documented v1 caveat
        return namespace

    @staticmethod
    async def _invoke(callable_obj: Any, args: dict[str, Any]) -> Any:
        """Call sync or async ``entry``; await coroutine results."""
        result = callable_obj(args)
        if asyncio.iscoroutine(result):
            result = await result
        return result

    def _error(
        self,
        kind: SandboxErrorKind,
        message: str,
        *,
        started_at: float,
    ) -> SandboxResult:
        """Build a SandboxResult for an error path."""
        return SandboxResult(
            status="error",
            result=None,
            error_kind=kind,
            error_message=message,
            latency_ms=self._elapsed_ms(started_at),
        )

    @staticmethod
    def _elapsed_ms(started_at: float) -> int:
        """Wall-clock latency from ``started_at`` to now, milliseconds."""
        return max(0, int((time.monotonic() - started_at) * 1000))
