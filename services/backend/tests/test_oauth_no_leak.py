"""Static check: OAuth raise sites must not interpolate token-bearing values.

Today this is true by inspection -- ``service.py`` and ``mcp_oauth.py`` raise
``ValueError`` with hardcoded strings like ``"MCP server is not authenticated"``
and never embed token, refresh_token, code, or client_secret in the exception
message. This test pins that contract via AST inspection so a future edit that
changes a message to ``f"refresh failed: {access_token}"`` fails CI before it
reaches a customer's logs.

The check walks every ``Raise`` statement in both modules and rejects any
expression in the exception args that names a banned identifier or attribute
(e.g. ``token.access_token``, ``response.code``). f-string interpolations are
recursed into. Constant strings are always allowed.
"""

from __future__ import annotations

import ast
from pathlib import Path


_BACKEND_SRC = Path(__file__).resolve().parents[1] / "src" / "backend_app"
_OAUTH_PATH = _BACKEND_SRC / "mcp_oauth.py"
_SERVICE_PATH = _BACKEND_SRC / "service.py"

# Identifiers and attribute names whose values could be OAuth secrets and
# therefore must never be embedded in an exception message.
_BANNED_NAMES = frozenset(
    {
        "access_token",
        "refresh_token",
        "client_secret",
        "code",
        "code_verifier",
        "secret",
        "token",
        "raw_token",
        "token_response",
    }
)


class _LeakScanner:
    """AST visitor that records banned-name leaks under any ``Raise`` node."""

    def __init__(self, source_path: Path) -> None:
        self._source = source_path.read_text(encoding="utf-8")
        self._tree = ast.parse(self._source, filename=str(source_path))

    def find_violations(self) -> list[tuple[int, str]]:
        """Return ``(lineno, banned_name)`` for every leaky raise."""

        violations: list[tuple[int, str]] = []
        for node in ast.walk(self._tree):
            if not isinstance(node, ast.Raise) or node.exc is None:
                continue
            for banned in self._inspect(node.exc):
                violations.append((node.lineno, banned))
        return violations

    @classmethod
    def _inspect(cls, expression: ast.AST) -> list[str]:
        """Walk the exception-construction expression for banned references."""

        found: list[str] = []
        for sub in ast.walk(expression):
            if isinstance(sub, ast.Name) and sub.id in _BANNED_NAMES:
                found.append(sub.id)
            elif isinstance(sub, ast.Attribute) and sub.attr in _BANNED_NAMES:
                found.append(sub.attr)
            elif isinstance(sub, ast.FormattedValue):
                # f-string interpolation: recurse into the embedded value.
                found.extend(cls._inspect(sub.value))
        return found


class TestOAuthRaiseSitesDoNotLeak:
    def test_mcp_oauth_module(self) -> None:
        violations = _LeakScanner(_OAUTH_PATH).find_violations()
        assert not violations, (
            f"mcp_oauth.py raise sites interpolate banned identifiers: {violations}"
        )

    def test_service_module(self) -> None:
        violations = _LeakScanner(_SERVICE_PATH).find_violations()
        assert not violations, (
            f"service.py raise sites interpolate banned identifiers: {violations}"
        )

    def test_scanner_catches_a_planted_leak(self) -> None:
        """Sanity: the AST walker must flag a deliberately-leaky source."""

        leaky_src = (
            "def f(access_token):\n    raise ValueError(f'token was: {access_token}')\n"
        )
        tree = ast.parse(leaky_src)
        violations: list[tuple[int, str]] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Raise) and node.exc is not None:
                for banned in _LeakScanner._inspect(node.exc):  # type: ignore[arg-type]
                    violations.append((node.lineno, banned))
        assert any(b == "access_token" for _, b in violations)
        assert all(line == 2 for line, _ in violations)
