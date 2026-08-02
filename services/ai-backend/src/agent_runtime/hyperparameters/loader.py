"""Load ``hyperparameters.json`` once, at a composition root, and fail loudly.

Everything that reads the environment or the filesystem for a tunable lives in
this module. That is the whole mechanism behind "zero hyperparameters remain
readable from the environment": consumers take an injected
:class:`~agent_runtime.hyperparameters.contracts.Hyperparameters` (or the single
section they need), and the one break-glass override path is the uniform
``COPILOT_HP__SECTION__FIELD`` prefix handled here.

An override is deep-merged over the parsed document and re-validated through the
same frozen model, so it is bound-checked identically and cannot smuggle an
unknown key past ``extra="forbid"``.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Final

from pydantic import ValidationError

from agent_runtime.hyperparameters.contracts import (
    Hyperparameters,
    HyperparameterOverride,
)


_LOGGER: Final = logging.getLogger("agent_runtime.hyperparameters")


class HyperparameterError(ValueError):
    """The document, or an override applied to it, was rejected at load.

    Raised at composition-root boot, never lazily at first use — a tuning
    document that is wrong should stop the process, not surface as a strange
    runtime value in one code path hours later.

    ``pointer`` is the JSON pointer of the offending field (``/`` for a failure
    that is about the document as a whole). It is what makes the message
    actionable without reading a Pydantic traceback. This error is boot-only: it
    never reaches model output or an HTTP body, which is why naming the source
    file path in the message is safe and useful here.
    """

    def __init__(self, message: str, *, pointer: str, source: str) -> None:
        super().__init__(message)
        self.pointer = pointer
        self.source = source


class HyperparameterLoader:
    """Read, validate, and environment-override the hyperparameter document.

    Classmethods only, and no module-level singleton: the composition roots load
    once and inject the result. A module-level ``HYPERPARAMETERS = load()`` would
    re-introduce exactly the import-time global this document replaces, and would
    make the load order (and therefore which overrides applied) unobservable.
    """

    class Document:
        """Where the checked-in document lives and how it is read."""

        FILE_NAME: Final[str] = "hyperparameters.json"
        ENCODING: Final[str] = "utf-8"

    class Env:
        """The one override surface. Nothing else may read a tunable from env."""

        PREFIX: Final[str] = "COPILOT_HP__"
        PATH_SEPARATOR: Final[str] = "__"

    class Sources:
        """Labels for the ``source`` carried on :class:`HyperparameterError`.

        Plain labels rather than interpolations of ``Env.PREFIX``: a nested class
        body cannot read its enclosing class's namespace, so referencing it here
        would be a ``NameError`` at import.
        """

        DEFAULT: Final[str] = "the packaged hyperparameters.json"
        ENVIRONMENT: Final[str] = "an environment override"

    class Messages:
        """Safe, boot-facing failure copy. One home so wording cannot fork."""

        NOT_FOUND = (
            "hyperparameters.json not found; searched: {searched}. The document "
            "is required at boot — a missing file is not a fallback to defaults."
        )
        UNREADABLE = "Could not read {path}: {reason}."
        MALFORMED_JSON = "{path} is not valid JSON: {reason}."
        NOT_AN_OBJECT = "{path} must contain a JSON object at the top level."
        INVALID = "Invalid hyperparameter at {pointer} (from {source}): {reason}."
        EMPTY_OVERRIDE_PATH = (
            "Environment override {name} names no field; the form is "
            "{prefix}SECTION{separator}FIELD."
        )

    class Logs:
        """Boot log lines. Every applied override is recorded, by construction.

        An incident knob set once and forgotten is the failure this prevents:
        six months later the run behaves unlike the checked-in document and
        nothing in the logs says why.
        """

        OVERRIDE_APPLIED = "hyperparameters.override_applied pointer=%s old=%s new=%s"

    @classmethod
    def from_path(cls, path: str | Path) -> Hyperparameters:
        """Read and validate the document at ``path``."""

        resolved = Path(path)
        try:
            text = resolved.read_text(encoding=cls.Document.ENCODING)
        except OSError as error:
            raise HyperparameterError(
                cls.Messages.UNREADABLE.format(path=resolved, reason=error.strerror),
                pointer="/",
                source=str(resolved),
            ) from error
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as error:
            raise HyperparameterError(
                cls.Messages.MALFORMED_JSON.format(path=resolved, reason=error.msg),
                pointer="/",
                source=str(resolved),
            ) from error
        if not isinstance(payload, dict):
            raise HyperparameterError(
                cls.Messages.NOT_AN_OBJECT.format(path=resolved),
                pointer="/",
                source=str(resolved),
            )
        return cls._validate(payload, source=str(resolved))

    @classmethod
    def candidate_paths(cls) -> tuple[Path, ...]:
        """Return every location :meth:`default_path` searches, in order.

        Exactly ONE: the document lives beside this package, and that is not a
        style choice. Every packaging path this service has copies *directories*
        out of ``services/ai-backend`` — the desktop stager takes ``src``,
        ``migrations``, ``scripts``, ``config``, ``skills``
        (``tools/desktop-runtime/stage.mjs``) and the Dockerfile copies the same
        shape — so a loose file at the SERVICE ROOT is carried by neither.

        It was written at the service root first, and because ``load`` now hard-
        fails when the document is missing, every packaged boot would have raised
        ``HyperparameterError`` while every dev checkout stayed green. Reproduced
        before fixing, by copying ``src`` exactly as the stager does.

        Searching the service root as a second candidate would restore the dev/
        packaged split that hid it: the file would resolve in a checkout and be
        absent in the shipped artifact. One location, carried by construction.
        """

        return (Path(__file__).resolve().parent / cls.Document.FILE_NAME,)

    @classmethod
    def default_path(cls) -> Path:
        """Return the first existing candidate, or fail naming every location."""

        candidates = cls.candidate_paths()
        for candidate in candidates:
            if candidate.is_file():
                return candidate
        searched = ", ".join(str(candidate) for candidate in candidates)
        raise HyperparameterError(
            cls.Messages.NOT_FOUND.format(searched=searched),
            pointer="/",
            source=cls.Sources.DEFAULT,
        )

    @classmethod
    def default(cls) -> Hyperparameters:
        """Load the checked-in document from wherever this build carries it."""

        return cls.from_path(cls.default_path())

    @classmethod
    def applied_overrides(
        cls,
        base: Hyperparameters,
        env: Mapping[str, str] | None = None,
    ) -> tuple[HyperparameterOverride, ...]:
        """Return the override records ``with_overrides`` would apply.

        Does not bound-check and does not log: a record is produced even for a
        value the model will reject, so the observability context can say what
        was attempted. A malformed *variable name* still raises here, because
        there is no pointer to record for it. The composition root uses this to
        carry the override list without re-parsing the environment a second time.
        """

        snapshot = base.model_dump(mode="json")
        return tuple(
            HyperparameterOverride(
                pointer=cls._pointer(path),
                previous=cls._render(cls._lookup(snapshot, path)),
                applied=cls._render(value),
            )
            for path, value in cls._collect(cls._environment(env))
        )

    @classmethod
    def with_overrides(
        cls,
        base: Hyperparameters,
        env: Mapping[str, str] | None = None,
    ) -> Hyperparameters:
        """Deep-merge ``COPILOT_HP__`` overrides over ``base`` and re-validate.

        Re-validating through the same frozen model is the point: an override is
        bound-checked exactly like a value written into the document, and a
        misspelled field is rejected by ``extra="forbid"`` instead of being
        silently ignored.
        """

        collected = cls._collect(cls._environment(env))
        if not collected:
            return base
        # Logged before validation on purpose: when an override is what breaks
        # the boot, the line naming it must already be in the log next to the
        # error that names the same pointer.
        for record in cls.applied_overrides(base, env):
            _LOGGER.info(
                cls.Logs.OVERRIDE_APPLIED,
                record.pointer,
                record.previous,
                record.applied,
            )
        merged = base.model_dump(mode="json")
        for path, value in collected:
            cls._merge(merged, path, value)
        return cls._validate(merged, source=cls.Sources.ENVIRONMENT)

    @classmethod
    def _environment(cls, env: Mapping[str, str] | None) -> Mapping[str, str]:
        """Resolve the environment mapping; the process env is the default."""

        return os.environ if env is None else env

    @classmethod
    def _collect(
        cls, env: Mapping[str, str]
    ) -> tuple[tuple[tuple[str, ...], Any], ...]:
        """Return ``(path, parsed value)`` per override, ordered by variable name.

        Ordering is by name so two overrides that touch the same pointer resolve
        deterministically rather than by dict iteration order.
        """

        collected: list[tuple[tuple[str, ...], Any]] = []
        for name in sorted(env):
            if not name.startswith(cls.Env.PREFIX):
                continue
            remainder = name[len(cls.Env.PREFIX) :]
            segments = tuple(
                segment.lower() for segment in remainder.split(cls.Env.PATH_SEPARATOR)
            )
            if not segments or any(not segment for segment in segments):
                raise HyperparameterError(
                    cls.Messages.EMPTY_OVERRIDE_PATH.format(
                        name=name,
                        prefix=cls.Env.PREFIX,
                        separator=cls.Env.PATH_SEPARATOR,
                    ),
                    pointer="/",
                    source=cls.Sources.ENVIRONMENT,
                )
            collected.append((segments, cls._parse(env[name])))
        return tuple(collected)

    @classmethod
    def _parse(cls, raw: str) -> Any:
        """Parse an override value as JSON, falling back to the literal string.

        ``1`` / ``0.5`` / ``true`` arrive typed; a bare enum member such as
        ``mcp_only`` is not valid JSON and stays a string, which is what the
        ``StrEnum`` field wants. Anything genuinely wrong is caught by
        re-validation, not by guessing here.
        """

        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return raw

    @classmethod
    def _merge(cls, target: dict[str, Any], path: Sequence[str], value: Any) -> None:
        """Set ``value`` at ``path`` in ``target``, creating nested maps as needed.

        A path that traverses a non-mapping is replaced with a mapping rather
        than rejected here, so the resulting shape fails at re-validation with a
        pointer instead of raising a bare ``TypeError`` from the merge.
        """

        cursor = target
        for segment in path[:-1]:
            existing = cursor.get(segment)
            if not isinstance(existing, dict):
                existing = {}
                cursor[segment] = existing
            cursor = existing
        cursor[path[-1]] = value

    @classmethod
    def _lookup(cls, snapshot: Mapping[str, Any], path: Sequence[str]) -> Any:
        """Return the current value at ``path``, or ``None`` when absent."""

        cursor: Any = snapshot
        for segment in path:
            if not isinstance(cursor, Mapping) or segment not in cursor:
                return None
            cursor = cursor[segment]
        return cursor

    @classmethod
    def _render(cls, value: Any) -> str | None:
        """Render a value for the override log line and observability context."""

        if value is None:
            return None
        return str(value)

    @classmethod
    def _pointer(cls, location: Sequence[Any]) -> str:
        """Return the JSON pointer for a Pydantic error location or env path."""

        if not location:
            return "/"
        return "/" + "/".join(str(segment) for segment in location)

    @classmethod
    def _validate(cls, payload: Mapping[str, Any], *, source: str) -> Hyperparameters:
        """Validate ``payload``, converting the first error into a typed failure."""

        try:
            return Hyperparameters.model_validate(payload)
        except ValidationError as error:
            first = error.errors()[0]
            pointer = cls._pointer(first["loc"])
            raise HyperparameterError(
                cls.Messages.INVALID.format(
                    pointer=pointer, source=source, reason=first["msg"]
                ),
                pointer=pointer,
                source=source,
            ) from error
