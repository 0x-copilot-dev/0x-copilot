"""Cross-language canonical JSON + SHA-256 helpers for Work Ledger digests.

The accepted value domain is deliberately narrower than Python's ``json``
module: JSON scalars, lists, and string-keyed dictionaries only. Non-finite
numbers, integers outside JavaScript's exact range, cycles, bytes, tuples, and
arbitrary objects fail before a stage can be approved. This keeps Python and
TypeScript digest inputs byte-identical.
"""

from __future__ import annotations

from decimal import Decimal
import hashlib
import json
import math
from typing import ClassVar

from copilot_service_contracts.work_ledger import load_work_ledger_contract


class CanonicalJsonError(ValueError):
    """A structured value cannot be represented by the canonical contract."""


class _Spec:
    _CONTRACT: ClassVar[dict[str, object]] = load_work_ledger_contract()
    _DIGESTS: ClassVar[dict[str, object]] = dict(_CONTRACT.get("digests") or {})
    MAX_SAFE_INTEGER: ClassVar[int] = int(_DIGESTS["max_safe_integer"])  # type: ignore[arg-type]


def canonical_json(value: object) -> str:
    """Return deterministic JSON with sorted keys and no insignificant space."""

    return _render(value, active=set(), path="$")


def canonical_json_bytes(value: object) -> bytes:
    """Return UTF-8 bytes for :func:`canonical_json`."""

    try:
        return canonical_json(value).encode("utf-8")
    except UnicodeEncodeError as exc:
        raise CanonicalJsonError(
            "canonical JSON strings must not contain unpaired surrogates"
        ) from exc


def sha256_hex(data: bytes | bytearray | memoryview) -> str:
    """Hash bytes as bytes and return lowercase SHA-256 hexadecimal."""

    if not isinstance(data, (bytes, bytearray, memoryview)):
        raise TypeError("sha256_hex accepts bytes-like input only")
    return hashlib.sha256(bytes(data)).hexdigest()


def canonical_json_sha256(value: object) -> str:
    """Hash the canonical UTF-8 representation of one structured value."""

    return sha256_hex(canonical_json_bytes(value))


def _render(value: object, *, active: set[int], path: str) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, str):
        if any(0xD800 <= ord(char) <= 0xDFFF for char in value):
            raise CanonicalJsonError(f"{path} contains an unpaired Unicode surrogate")
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if isinstance(value, int):
        if abs(value) > _Spec.MAX_SAFE_INTEGER:
            raise CanonicalJsonError(
                f"{path} integer exceeds the cross-language safe range"
            )
        return str(value)
    if isinstance(value, float):
        return _render_float(value, path)
    if isinstance(value, list):
        return _render_list(value, active=active, path=path)
    if isinstance(value, dict):
        return _render_dict(value, active=active, path=path)
    raise CanonicalJsonError(
        f"{path} contains unsupported value type {type(value).__name__}"
    )


def _render_float(value: float, path: str) -> str:
    if not math.isfinite(value):
        raise CanonicalJsonError(f"{path} must be a finite JSON number")
    if value == 0:
        return "0"
    if value.is_integer():
        integer = int(value)
        if abs(integer) > _Spec.MAX_SAFE_INTEGER:
            raise CanonicalJsonError(
                f"{path} integer exceeds the cross-language safe range"
            )
        return str(integer)

    magnitude = abs(value)
    shortest = repr(value).lower()
    if magnitude < 1e-6 or magnitude >= 1e21:
        mantissa, exponent_text = shortest.split("e")
        if mantissa.endswith(".0"):
            mantissa = mantissa[:-2]
        exponent = int(exponent_text)
        sign = "+" if exponent >= 0 else ""
        return f"{mantissa}e{sign}{exponent}"

    # ``Decimal(repr(value))`` preserves Python's shortest round-trip digits
    # while forcing the same ordinary notation JSON.stringify uses in this
    # magnitude range.
    rendered = format(Decimal(shortest), "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return rendered


def _render_list(value: list[object], *, active: set[int], path: str) -> str:
    identity = id(value)
    if identity in active:
        raise CanonicalJsonError(f"{path} contains a cycle")
    active.add(identity)
    try:
        return (
            "["
            + ",".join(
                _render(item, active=active, path=f"{path}[{index}]")
                for index, item in enumerate(value)
            )
            + "]"
        )
    finally:
        active.remove(identity)


def _render_dict(value: dict[object, object], *, active: set[int], path: str) -> str:
    identity = id(value)
    if identity in active:
        raise CanonicalJsonError(f"{path} contains a cycle")
    active.add(identity)
    try:
        for key in value:
            if not isinstance(key, str):
                raise CanonicalJsonError(f"{path} object keys must be strings")
        parts = []
        for key in sorted(value):
            encoded_key = _render(key, active=active, path=f"{path}.<key>")
            encoded_value = _render(value[key], active=active, path=f"{path}.{key}")
            parts.append(f"{encoded_key}:{encoded_value}")
        return "{" + ",".join(parts) + "}"
    finally:
        active.remove(identity)


__all__ = [
    "CanonicalJsonError",
    "canonical_json",
    "canonical_json_bytes",
    "canonical_json_sha256",
    "sha256_hex",
]
