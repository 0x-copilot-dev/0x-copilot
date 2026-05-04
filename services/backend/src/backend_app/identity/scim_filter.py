"""Minimal SCIM 2.0 filter parser (A7).

Supports the 99% subset IdPs actually use:

- ``attr eq "value"``  (equality)
- ``attr eq true``     (boolean)
- ``attr pr``          (presence)
- ``a eq "x" and b eq "y"`` (conjunction)

Anything else raises :class:`ScimFilterError` so the route layer can map
it to a SCIM 400 with ``scimType=invalidFilter``. Adding ``or`` / ``co`` /
``sw`` / ``not`` is small and additive — left for a follow-up rather than
shipping a full RFC 7644 §3.4.2.2 parser before any IdP needs it.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass


class ScimFilterError(ValueError):
    """Filter expression couldn't be parsed (or used unsupported syntax)."""


@dataclass(frozen=True)
class _Eq:
    attr: str
    value: object


@dataclass(frozen=True)
class _Pr:
    attr: str


@dataclass(frozen=True)
class _And:
    left: object
    right: object


_ScimNode = _Eq | _Pr | _And


def parse_filter(expression: str | None) -> _ScimNode | None:
    """Parse a SCIM filter string into a tree of ``_Eq`` / ``_Pr`` / ``_And``.

    Returns ``None`` for empty / missing expressions (caller should treat
    that as "no filter — return everything").
    """

    if expression is None:
        return None
    text = expression.strip()
    if not text:
        return None
    tokens = _tokenize(text)
    parser = _Parser(tokens)
    node = parser.parse_expression()
    if parser.pos != len(tokens):
        raise ScimFilterError(
            f"unexpected trailing input at token {parser.pos}: {tokens[parser.pos]!r}"
        )
    return node


def filter_matches(node: _ScimNode | None, attributes: Mapping[str, object]) -> bool:
    """Apply a parsed filter to an attribute dict.

    String comparisons are case-insensitive (per RFC 7644 §3.4.2.2 default
    of ``caseExact=false``). Boolean values match Python truthy after
    coercion. Missing attributes never match an ``eq``; ``pr`` matches
    anything non-null and non-empty.
    """

    if node is None:
        return True
    if isinstance(node, _And):
        return filter_matches(node.left, attributes) and filter_matches(
            node.right, attributes
        )
    if isinstance(node, _Pr):
        value = attributes.get(node.attr)
        if value is None:
            return False
        if isinstance(value, str):
            return bool(value)
        if isinstance(value, (list, tuple, dict)):
            return bool(value)
        return True
    if isinstance(node, _Eq):
        actual = attributes.get(node.attr)
        if isinstance(node.value, bool) and isinstance(actual, bool):
            return actual is node.value
        if isinstance(node.value, str) and isinstance(actual, str):
            return actual.lower() == node.value.lower()
        return actual == node.value
    raise ScimFilterError(
        f"unknown filter node {type(node).__name__}"
    )  # pragma: no cover


# ---------------------------------------------------------------------------
# Tokenizer + parser
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Token:
    kind: str  # "ident", "string", "bool", "op", "lparen", "rparen"
    value: object


_OPERATORS = ("eq", "pr", "and")


def _tokenize(text: str) -> Sequence[_Token]:
    tokens: list[_Token] = []
    i = 0
    while i < len(text):
        ch = text[i]
        if ch.isspace():
            i += 1
            continue
        if ch == "(":
            tokens.append(_Token("lparen", "("))
            i += 1
            continue
        if ch == ")":
            tokens.append(_Token("rparen", ")"))
            i += 1
            continue
        if ch == '"':
            end = i + 1
            chars: list[str] = []
            while end < len(text) and text[end] != '"':
                if text[end] == "\\" and end + 1 < len(text):
                    chars.append(text[end + 1])
                    end += 2
                else:
                    chars.append(text[end])
                    end += 1
            if end >= len(text):
                raise ScimFilterError("unterminated string literal")
            tokens.append(_Token("string", "".join(chars)))
            i = end + 1
            continue
        if ch.isalpha() or ch == "_":
            end = i
            while end < len(text) and (text[end].isalnum() or text[end] in "._-"):
                end += 1
            word = text[i:end]
            lowered = word.lower()
            if lowered in _OPERATORS:
                tokens.append(_Token("op", lowered))
            elif lowered in ("true", "false"):
                tokens.append(_Token("bool", lowered == "true"))
            else:
                tokens.append(_Token("ident", word))
            i = end
            continue
        raise ScimFilterError(f"unexpected character {ch!r} at offset {i}")
    return tokens


class _Parser:
    def __init__(self, tokens: Sequence[_Token]) -> None:
        self._tokens = tokens
        self.pos = 0

    def _peek(self) -> _Token | None:
        if self.pos >= len(self._tokens):
            return None
        return self._tokens[self.pos]

    def _consume(self) -> _Token:
        token = self._tokens[self.pos]
        self.pos += 1
        return token

    def parse_expression(self) -> _ScimNode:
        # Only `and` is supported as a connective; left-associative.
        left = self._parse_atom()
        while True:
            peek = self._peek()
            if peek is None:
                break
            if peek.kind == "op" and peek.value == "and":
                self._consume()
                right = self._parse_atom()
                left = _And(left=left, right=right)
                continue
            break
        return left

    def _parse_atom(self) -> _ScimNode:
        token = self._peek()
        if token is None:
            raise ScimFilterError("unexpected end of filter")
        if token.kind == "lparen":
            self._consume()
            inner = self.parse_expression()
            close = self._peek()
            if close is None or close.kind != "rparen":
                raise ScimFilterError("missing closing paren")
            self._consume()
            return inner
        if token.kind != "ident":
            raise ScimFilterError(
                f"expected attribute name, got {token.kind} {token.value!r}"
            )
        attr = self._consume().value
        op = self._peek()
        if op is None or op.kind != "op":
            raise ScimFilterError(
                f"expected operator after attribute {attr!r}, got {op}"
            )
        if op.value == "pr":
            self._consume()
            return _Pr(attr=str(attr))
        if op.value == "eq":
            self._consume()
            value_token = self._peek()
            if value_token is None:
                raise ScimFilterError("expected value after 'eq'")
            self._consume()
            if value_token.kind == "string":
                return _Eq(attr=str(attr), value=str(value_token.value))
            if value_token.kind == "bool":
                return _Eq(attr=str(attr), value=bool(value_token.value))
            raise ScimFilterError(f"unsupported value type for eq: {value_token.kind}")
        raise ScimFilterError(f"unsupported operator {op.value!r}")


__all__ = ["filter_matches", "parse_filter", "ScimFilterError"]
