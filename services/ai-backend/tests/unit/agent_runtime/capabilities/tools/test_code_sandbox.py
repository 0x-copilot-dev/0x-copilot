"""Unit tests for the in-process code-routine sandbox."""

from __future__ import annotations

import asyncio
import textwrap

import pytest

from agent_runtime.capabilities.tools.code_sandbox import (
    BANNED_ATTRIBUTE_PREFIXES,
    BANNED_GLOBAL_NAMES,
    BANNED_IMPORT_NAMES,
    CodeAstValidator,
    InProcessCodeSandbox,
    SandboxResult,
)


def _make_sandbox(max_timeout_s: float = 2.0) -> InProcessCodeSandbox:
    """Return an in-process sandbox with a low ceiling for fast tests."""
    return InProcessCodeSandbox(max_timeout_s=max_timeout_s)


def _run(coro: object) -> SandboxResult:
    """Run an awaitable to completion in tests."""
    return asyncio.get_event_loop().run_until_complete(coro)  # type: ignore[arg-type]


class TestHappyPath:
    """Code that conforms to the contract returns the dict it produced."""

    def test_echo_args_roundtrip(self) -> None:
        sandbox = _make_sandbox()
        code = textwrap.dedent(
            """
            def run(args: dict) -> dict:
                return {"echoed": args, "ok": True}
            """
        )
        result = asyncio.run(
            sandbox.execute(
                code=code,
                entry="run",
                args={"hello": "world", "n": 7},
                timeout_s=1.0,
            )
        )
        assert result.status == "ok"
        assert result.error_kind is None
        assert result.result is not None
        assert result.result["echoed"] == {"hello": "world", "n": 7}
        assert result.result["ok"] is True
        assert result.latency_ms >= 0

    def test_async_entry_is_awaited(self) -> None:
        sandbox = _make_sandbox()
        code = textwrap.dedent(
            """
            async def run(args: dict) -> dict:
                return {"sum": sum(args.get('xs', []))}
            """
        )
        result = asyncio.run(
            sandbox.execute(
                code=code, entry="run", args={"xs": [1, 2, 3]}, timeout_s=1.0
            )
        )
        assert result.status == "ok"
        assert result.result == {"sum": 6}


class TestTimeout:
    """``asyncio.wait_for`` enforces deterministic per-call timeout."""

    def test_async_sleep_times_out(self) -> None:
        sandbox = _make_sandbox(max_timeout_s=0.1)
        # We need an awaitable that yields without importing anything
        # banned. ``await args["sleeper"]()`` lets us inject an asyncio
        # sleep from the test harness — the sandbox code itself is clean.
        code = textwrap.dedent(
            """
            async def run(args: dict) -> dict:
                await args["sleeper"]()
                return {"never": True}
            """
        )

        async def slow_sleeper() -> None:
            await asyncio.sleep(1.0)

        result = asyncio.run(
            sandbox.execute(
                code=code,
                entry="run",
                args={"sleeper": slow_sleeper},
                timeout_s=0.05,
            )
        )
        assert result.status == "error"
        assert result.error_kind == "timeout"
        assert result.result is None
        assert "exceeded" in (result.error_message or "")


class TestAstViolations:
    """Each banned import / name / attribute family must reject."""

    @pytest.mark.parametrize(
        "banned",
        ["os", "subprocess", "socket", "ctypes", "importlib", "requests"],
    )
    def test_banned_import_rejected(self, banned: str) -> None:
        sandbox = _make_sandbox()
        code = textwrap.dedent(
            f"""
            import {banned}
            def run(args: dict) -> dict:
                return {{}}
            """
        )
        result = asyncio.run(
            sandbox.execute(code=code, entry="run", args={}, timeout_s=0.5)
        )
        assert result.status == "error"
        assert result.error_kind == "schema_invalid"
        assert banned in (result.error_message or "")

    @pytest.mark.parametrize("banned", ["os", "subprocess", "socket", "ctypes"])
    def test_banned_from_import_rejected(self, banned: str) -> None:
        sandbox = _make_sandbox()
        code = textwrap.dedent(
            f"""
            from {banned} import something
            def run(args: dict) -> dict:
                return {{}}
            """
        )
        result = asyncio.run(
            sandbox.execute(code=code, entry="run", args={}, timeout_s=0.5)
        )
        assert result.status == "error"
        assert result.error_kind == "schema_invalid"

    @pytest.mark.parametrize("name", ["eval", "exec", "compile", "__import__"])
    def test_banned_global_name_rejected(self, name: str) -> None:
        sandbox = _make_sandbox()
        code = textwrap.dedent(
            f"""
            def run(args: dict) -> dict:
                return {{"x": {name}}}
            """
        )
        result = asyncio.run(
            sandbox.execute(code=code, entry="run", args={}, timeout_s=0.5)
        )
        assert result.status == "error"
        assert result.error_kind == "schema_invalid"
        assert name in (result.error_message or "")

    def test_banned_attribute_subclasses_rejected(self) -> None:
        sandbox = _make_sandbox()
        code = textwrap.dedent(
            """
            def run(args: dict) -> dict:
                return {"x": [].__class__}
            """
        )
        result = asyncio.run(
            sandbox.execute(code=code, entry="run", args={}, timeout_s=0.5)
        )
        assert result.status == "error"
        assert result.error_kind == "schema_invalid"

    def test_open_write_mode_rejected_via_validator(self) -> None:
        # ``open`` itself is banned as a name, but the open-mode validator
        # is a belt-and-braces check that survives a future relaxation.
        # Exercise the validator directly to keep that guard covered.
        rejection = CodeAstValidator.validate(
            'def run(args):\n    f = open("/tmp/x", "w")\n    return {}\n'
        )
        assert rejection is not None

    def test_star_import_rejected(self) -> None:
        sandbox = _make_sandbox()
        code = "from math import *\ndef run(args): return {}\n"
        result = asyncio.run(
            sandbox.execute(code=code, entry="run", args={}, timeout_s=0.5)
        )
        assert result.status == "error"
        assert result.error_kind == "schema_invalid"

    def test_syntax_error_rejected(self) -> None:
        sandbox = _make_sandbox()
        result = asyncio.run(
            sandbox.execute(
                code="def run(args:\n    return {}\n",
                entry="run",
                args={},
                timeout_s=0.5,
            )
        )
        assert result.status == "error"
        assert result.error_kind == "schema_invalid"


class TestRuntimeException:
    """Exceptions inside the entry function settle as ``sandbox_crash``."""

    def test_runtime_exception_classified(self) -> None:
        sandbox = _make_sandbox()
        code = textwrap.dedent(
            """
            def run(args: dict) -> dict:
                raise ValueError("boom")
            """
        )
        result = asyncio.run(
            sandbox.execute(code=code, entry="run", args={}, timeout_s=0.5)
        )
        assert result.status == "error"
        assert result.error_kind == "sandbox_crash"
        # Public message must NOT leak the original "boom" — TU-1
        # safe-public-message rule.
        assert "boom" not in (result.error_message or "")

    def test_entry_not_callable(self) -> None:
        sandbox = _make_sandbox()
        code = "x = 1\n"
        result = asyncio.run(
            sandbox.execute(code=code, entry="run", args={}, timeout_s=0.5)
        )
        assert result.status == "error"
        assert result.error_kind == "entry_missing"

    def test_result_not_dict_rejected(self) -> None:
        sandbox = _make_sandbox()
        code = textwrap.dedent(
            """
            def run(args: dict):
                return [1, 2, 3]
            """
        )
        result = asyncio.run(
            sandbox.execute(code=code, entry="run", args={}, timeout_s=0.5)
        )
        assert result.status == "error"
        assert result.error_kind == "result_invalid"


class TestAllowListAuditability:
    """The deny-list constants are top-level so reviewers can audit them."""

    def test_banned_imports_contains_critical_entries(self) -> None:
        for required in ("os", "subprocess", "socket", "ctypes", "importlib"):
            assert required in BANNED_IMPORT_NAMES

    def test_banned_globals_contains_eval_family(self) -> None:
        for required in ("eval", "exec", "compile", "__import__"):
            assert required in BANNED_GLOBAL_NAMES

    def test_banned_attributes_contains_introspection_escapes(self) -> None:
        for required in ("__subclasses__", "__globals__", "__builtins__"):
            assert required in BANNED_ATTRIBUTE_PREFIXES
