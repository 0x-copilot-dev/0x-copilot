"""Tests for the ``Redactor`` Protocol surface and the registry singleton.

P11.1 contract: the existing ``ObservabilityRedactor`` classmethods
delegate to the active default in
``agent_runtime.observability.redactor.RedactorRegistry``. Swapping the
default at runtime must reroute every call site that goes through the
legacy facade — that is the substitution principle we set up here so
P11.2 / P11.3 can plug in detect-secrets / Presidio without touching
the 19 callers.
"""

from __future__ import annotations

from typing import ClassVar

import pytest

from agent_runtime.observability.redaction import ObservabilityRedactor
from agent_runtime.observability.redactor import (
    Redactor,
    RedactorRegistry,
    RegexRedactor,
)


class _RecordingRedactor:
    """Tiny fake that records every call so the test can assert delegation."""

    def __init__(self) -> None:
        self.object_calls: list[object] = []
        self.value_calls: list[object] = []

    def redact_json_object(
        self,
        value: object,
        *,
        max_string_length: int | None = None,
        user_content: bool = False,
    ) -> dict[str, object]:
        self.object_calls.append(value)
        return {"recorded": True}

    def redact_json_value(
        self,
        value: object,
        *,
        max_string_length: int | None = None,
        user_content: bool = False,
    ) -> object:
        self.value_calls.append(value)
        return "recorded"


class _RegistryFixture:
    """Reset the registry around each test so a leak in one doesn't bleed."""

    @staticmethod
    def reset() -> None:
        RedactorRegistry.reset_for_tests()


@pytest.fixture(autouse=True)
def _isolate_registry() -> None:
    _RegistryFixture.reset()
    yield
    _RegistryFixture.reset()


def test_default_is_regex_redactor() -> None:
    """Pre-swap, the default is a fresh ``RegexRedactor``."""

    assert isinstance(RedactorRegistry.default(), RegexRedactor)


def test_regex_redactor_satisfies_protocol() -> None:
    """``RegexRedactor`` instances pass the runtime-checkable Protocol."""

    assert isinstance(RegexRedactor(), Redactor)


def test_set_default_returns_previous() -> None:
    """``set_default`` returns the prior default for restoration."""

    fake = _RecordingRedactor()
    previous = RedactorRegistry.set_default(fake)

    assert isinstance(previous, RegexRedactor)
    assert RedactorRegistry.default() is fake


def test_observability_redactor_delegates_to_default() -> None:
    """Legacy classmethod surface routes through the swapped default."""

    fake = _RecordingRedactor()
    RedactorRegistry.set_default(fake)

    result = ObservabilityRedactor.redact_json_object({"k": "v"})

    assert result == {"recorded": True}
    assert fake.object_calls == [{"k": "v"}]


def test_swap_is_restorable() -> None:
    """A swap + restore round-trips back to the original default."""

    fake = _RecordingRedactor()
    original = RedactorRegistry.set_default(fake)
    assert RedactorRegistry.default() is fake

    RedactorRegistry.set_default(original)
    assert RedactorRegistry.default() is original
    assert isinstance(RedactorRegistry.default(), RegexRedactor)


def test_fake_redactor_satisfies_protocol() -> None:
    """A hand-rolled fake with both methods passes ``isinstance``."""

    class _Minimal:
        def redact_json_object(
            self,
            value: object,
            *,
            max_string_length: int | None = None,
            user_content: bool = False,
        ) -> dict[str, object]:
            return {}

        def redact_json_value(
            self,
            value: object,
            *,
            max_string_length: int | None = None,
            user_content: bool = False,
        ) -> object:
            return value

    assert isinstance(_Minimal(), Redactor)


class _ObjectMissingMethod:
    """Used to assert the Protocol rejects objects without both methods."""

    PUBLIC_CONST: ClassVar[str] = "kept simple to avoid passing the check"

    def redact_json_object(
        self,
        value: object,
        *,
        max_string_length: int | None = None,
        user_content: bool = False,
    ) -> dict[str, object]:
        return {}


def test_protocol_rejects_partial_implementation() -> None:
    """Missing ``redact_json_value`` means the Protocol check fails."""

    assert not isinstance(_ObjectMissingMethod(), Redactor)
