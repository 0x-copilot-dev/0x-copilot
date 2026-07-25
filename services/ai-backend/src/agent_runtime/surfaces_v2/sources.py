"""Safe, pure provenance projection for Generative Surfaces v2 Sources.

``SourcesProjectionV2`` is deliberately a read model only: it folds persisted
work-ledger rows and never dereferences a ref, reaches a provider, or treats an
opaque id as authorization.  The allowlist below is intentionally narrow.  In
particular, it never copies tool arguments, result bodies, browser state,
cookies, sandbox commands, or physical paths into a source fact.

The projector accepts canonical ledger rows and a small set of compatible rows
whose field names carry the same safe semantics.  Unknown/malformed rows are
ignored, so replay remains total as the ledger grows.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from enum import StrEnum
from typing import Literal, Protocol
from urllib.parse import urlsplit

from agent_runtime.execution.contracts import RuntimeContract
from agent_runtime.surfaces_v2.ledger_ids import (
    LedgerIdCodec,
    LedgerIdFormatError,
    WorkspaceTargetRefCodec,
)
from agent_runtime.surfaces_v2.ledger_models import LedgerEventType


class SourceFactKindV2(StrEnum):
    """The closed, presentation-safe provenance fact vocabulary."""

    CONNECTOR = "connector"
    ARTIFACT = "artifact"
    WORKSPACE = "workspace"
    BROWSER = "browser"
    SANDBOX = "sandbox"
    SUBAGENT = "subagent"
    EXTERNAL_RECEIPT = "external_receipt"


class SourceFactV2(RuntimeContract):
    """One safe provenance edge derived from exactly one ledger row.

    ``workspace_virtual_path_key`` and the ``*_ref`` fields are identifiers
    only.  They are intentionally not links or authorization material; any
    future opener must re-check identity and resource scope.
    """

    source_id: str
    kind: SourceFactKindV2
    sequence_no: int
    ledger_id: str | None = None
    connector: str | None = None
    tool: str | None = None
    origin: str | None = None
    artifact_id: str | None = None
    artifact_revision: int | None = None
    artifact_source_ref: str | None = None
    workspace_grant_label: str | None = None
    workspace_virtual_path_key: str | None = None
    browser_origin: str | None = None
    sandbox_operation: str | None = None
    subagent_task: str | None = None
    external_receipt_ref: str | None = None


class SourcesProjectionStateV2(RuntimeContract):
    """The deterministic, replayable Sources v2 projection for one run."""

    v: Literal[2] = 2
    run_id: str
    latest_sequence_no: int
    facts: tuple[SourceFactV2, ...]


class _LedgerEventLike(Protocol):
    event_type: object
    sequence_no: object
    payload: object


_SOURCE_ID_PREFIX = "source:v2:"
_PHYSICAL_PATH = re.compile(
    r"(?:^|[\s(=:'\"])(?:~[\\/]|[A-Za-z]:[\\/]|\\\\[^\\/\s]+[\\/]|/(?:[^\s/]+(?:/|$)))",
    re.IGNORECASE,
)
_SENSITIVE_TEXT = re.compile(
    r"(?:authorization|proxy-authorization|cookie|set-cookie|api[_-]?key|"
    r"access[_-]?token|refresh[_-]?token|id[_-]?token|token|secret|password|"
    r"credential|private[_-]?key|client[_-]?secret|session)\s*[:=]|\bbearer\s+|"
    r"(?:^|[^A-Za-z0-9_-])(?:sk-[A-Za-z0-9_-]+|xox[baprs]-[A-Za-z0-9-]+|"
    r"gh[pousr]_[A-Za-z0-9]+|github_pat_[A-Za-z0-9_]+|AIza[A-Za-z0-9_-]{20,})"
    r"(?![A-Za-z0-9_-])",
    re.IGNORECASE,
)
_OPAQUE_TOKEN = re.compile(r"^[A-Za-z0-9._~-]+$")
_OPAQUE_REF = re.compile(
    r"^[A-Za-z][A-Za-z0-9+.-]*://[A-Za-z0-9._~-]+(?:/[A-Za-z0-9._~-]+)*$"
)
_SANDBOX_OPERATION = re.compile(r"^[A-Za-z][A-Za-z0-9._:-]{0,127}$")
_MAX_SAFE_INTEGER = 9_007_199_254_740_991
_ARTIFACT_SOURCE_EVENT_TYPES = frozenset(
    {
        LedgerEventType.ARTIFACT_CREATED.value,
        LedgerEventType.ARTIFACT_REVISED.value,
        LedgerEventType.ARTIFACT_PROMOTED.value,
    }
)
_RECEIPT_SOURCE_EVENT_TYPES = frozenset(
    {
        LedgerEventType.WRITE_APPLIED.value,
        LedgerEventType.EFFECT_APPLIED.value,
        LedgerEventType.EFFECT_RECONCILED.value,
    }
)
_READ_CONNECTOR_EVENT_TYPES = frozenset(
    {
        LedgerEventType.ACTION_CLASSIFIED.value,
        LedgerEventType.READ_EXECUTED.value,
    }
)
_CAPABILITY_CONNECTOR_EVENT_TYPES = frozenset(
    {
        LedgerEventType.OPERATION_REQUESTED.value,
        LedgerEventType.EFFECT_STAGED.value,
    }
)
_CONNECTOR_SOURCE_EVENT_TYPES = frozenset(
    {
        *_READ_CONNECTOR_EVENT_TYPES,
        LedgerEventType.SURFACE_CREATED.value,
        *_CAPABILITY_CONNECTOR_EVENT_TYPES,
    }
)


class SourcesProjectionV2:
    """Pure fold implementation kept separate from future transport adapters."""

    @classmethod
    def fold(
        cls,
        run_id: str,
        events: Iterable[_LedgerEventLike],
    ) -> SourcesProjectionStateV2:
        """Fold object-shaped ledger events without importing ``runtime_api``."""

        raw = (
            {
                "event_type": getattr(event, "event_type", None),
                "sequence_no": getattr(event, "sequence_no", None),
                "payload": getattr(event, "payload", None),
            }
            for event in events
        )
        return cls.fold_raw(run_id, raw)

    @classmethod
    def fold_raw(
        cls,
        run_id: str,
        events: Iterable[Mapping[str, object]],
    ) -> SourcesProjectionStateV2:
        """Fold mapping-shaped canonical or compatible ledger events.

        Events are sorted by ``sequence_no`` with input order as a deterministic
        tie-breaker.  A duplicate sequence/kind yields one fact, which makes
        replay retries harmless while preserving the first persisted row.
        """

        ordered: list[tuple[int, int, str, Mapping[str, object]]] = []
        latest_sequence_no = 0
        for index, event in enumerate(events):
            sequence_no = _positive_int(event.get("sequence_no"))
            if sequence_no is not None:
                latest_sequence_no = max(latest_sequence_no, sequence_no)
            event_type = event.get("event_type")
            payload = _mapping(event.get("payload"))
            if (
                sequence_no is None
                or not isinstance(event_type, str)
                or payload is None
            ):
                continue
            ordered.append((sequence_no, index, event_type, payload))

        facts: list[SourceFactV2] = []
        emitted_ids: set[str] = set()
        for sequence_no, _index, event_type, payload in sorted(
            ordered, key=lambda item: (item[0], item[1])
        ):
            for fact in cls._facts_for_event(run_id, sequence_no, event_type, payload):
                if fact.source_id in emitted_ids:
                    continue
                emitted_ids.add(fact.source_id)
                facts.append(fact)

        return SourcesProjectionStateV2(
            run_id=run_id,
            latest_sequence_no=latest_sequence_no,
            facts=tuple(facts),
        )

    @classmethod
    def _facts_for_event(
        cls,
        run_id: str,
        sequence_no: int,
        event_type: str,
        payload: Mapping[str, object],
    ) -> tuple[SourceFactV2, ...]:
        facts: list[SourceFactV2] = []

        connector, tool = _connector_and_tool(event_type, payload)
        origin = (
            _origin_from(payload, ("origin", "source_origin"))
            if _is_connector_event(event_type)
            else None
        )
        if connector is not None or tool is not None or origin is not None:
            fact = _fact(
                run_id,
                sequence_no,
                SourceFactKindV2.CONNECTOR,
                connector=connector,
                tool=tool,
                origin=origin,
            )
            if fact is not None:
                facts.append(fact)

        if event_type in _ARTIFACT_SOURCE_EVENT_TYPES:
            artifact_id = _safe_text(payload.get("artifact_id"))
            revision = _positive_int(payload.get("revision"))
            source_ref = _first_safe_opaque_ref(payload, ("source_ref", "content_ref"))
            if (
                artifact_id is not None
                or revision is not None
                or source_ref is not None
            ):
                fact = _fact(
                    run_id,
                    sequence_no,
                    SourceFactKindV2.ARTIFACT,
                    artifact_id=artifact_id,
                    artifact_revision=revision,
                    artifact_source_ref=source_ref,
                )
                if fact is not None:
                    facts.append(fact)

        executor = _safe_text(payload.get("executor"))
        lowered_executor = executor.lower() if executor is not None else ""
        is_workspace = lowered_executor == "workspace" or event_type.startswith(
            "workspace."
        )
        if is_workspace:
            workspace = _workspace_fields(payload)
            if workspace is not None:
                grant_label, virtual_path_key = workspace
                fact = _fact(
                    run_id,
                    sequence_no,
                    SourceFactKindV2.WORKSPACE,
                    workspace_grant_label=grant_label,
                    workspace_virtual_path_key=virtual_path_key,
                )
                if fact is not None:
                    facts.append(fact)

        is_browser = lowered_executor == "browser" or event_type.startswith("browser.")
        if is_browser:
            browser_origin = _origin_from(payload, ("browser_origin", "origin"))
            if browser_origin is not None:
                fact = _fact(
                    run_id,
                    sequence_no,
                    SourceFactKindV2.BROWSER,
                    browser_origin=browser_origin,
                )
                if fact is not None:
                    facts.append(fact)

        capability = _safe_text(payload.get("capability"))
        is_sandbox = (
            lowered_executor == "sandbox"
            or capability == "sandbox"
            or event_type.startswith("sandbox.")
        )
        if is_sandbox:
            sandbox_operation = _first_safe_sandbox_operation(
                payload, ("sandbox_operation", "operation", "op")
            )
            if sandbox_operation is not None:
                fact = _fact(
                    run_id,
                    sequence_no,
                    SourceFactKindV2.SANDBOX,
                    sandbox_operation=sandbox_operation,
                )
                if fact is not None:
                    facts.append(fact)

        producer = _safe_text(payload.get("producer"))
        is_subagent = (
            producer == "subagent"
            or event_type.startswith("subagent.")
            or event_type.startswith("subagent_")
        )
        if is_subagent:
            # Do not fall back to a tool name: a tool is not a subagent task.
            subagent_task = _first_safe_text(
                payload, ("subagent_task", "task_summary", "objective_summary", "task")
            )
            if subagent_task is not None:
                fact = _fact(
                    run_id,
                    sequence_no,
                    SourceFactKindV2.SUBAGENT,
                    subagent_task=subagent_task,
                )
                if fact is not None:
                    facts.append(fact)

        if event_type in _RECEIPT_SOURCE_EVENT_TYPES:
            receipt_ref = _first_safe_opaque_ref(
                payload, ("connector_receipt_ref", "receipt_ref")
            )
            if receipt_ref is not None:
                fact = _fact(
                    run_id,
                    sequence_no,
                    SourceFactKindV2.EXTERNAL_RECEIPT,
                    external_receipt_ref=receipt_ref,
                )
                if fact is not None:
                    facts.append(fact)

        return tuple(facts)


def _connector_and_tool(
    event_type: str, payload: Mapping[str, object]
) -> tuple[str | None, str | None]:
    if event_type in _READ_CONNECTOR_EVENT_TYPES:
        return _safe_text(payload.get("connector")), _safe_text(payload.get("op"))
    if event_type == LedgerEventType.SURFACE_CREATED.value:
        source = _mapping(payload.get("source"))
        if source is None:
            return None, None
        return _safe_text(source.get("connector")), _safe_text(source.get("op"))
    if event_type in _CAPABILITY_CONNECTOR_EVENT_TYPES:
        return _safe_text(payload.get("capability")), _safe_text(payload.get("op"))
    # Compatible connector rows are accepted only when their event name names
    # the compatible capability; arbitrary payloads cannot opt into Sources.
    if event_type.startswith("connector.") or event_type.startswith("tool."):
        return _safe_text(payload.get("connector")), _safe_text(payload.get("op"))
    return None, None


def _is_connector_event(event_type: str) -> bool:
    return event_type in _CONNECTOR_SOURCE_EVENT_TYPES or event_type.startswith(
        ("connector.", "tool.")
    )


def _workspace_fields(payload: Mapping[str, object]) -> tuple[str | None, str] | None:
    label = _first_safe_text(
        payload,
        ("workspace_grant_label", "grant_label", "display_target"),
    )
    target_ref = payload.get("target_ref")
    if isinstance(target_ref, str):
        try:
            target = WorkspaceTargetRefCodec.parse(target_ref)
        except (LedgerIdFormatError, ValueError):
            target = None
        if target is not None:
            grant_id = _safe_opaque_token(target.grant_id)
            path_token = _safe_opaque_token(target.path_token)
            if grant_id is not None and path_token is not None:
                return label, _workspace_key(grant_id, path_token)

    grant_id = _first_opaque_token(payload, ("workspace_grant_id", "grant_id"))
    path_token = _first_opaque_token(
        payload, ("workspace_virtual_path_token", "virtual_path_token", "path_token")
    )
    if grant_id is None or path_token is None:
        return None
    return label, _workspace_key(grant_id, path_token)


def _workspace_key(grant_id: str, path_token: str) -> str:
    """Return an identifier-only key; it is never a dereference capability."""

    return f"workspace:v2:{grant_id}:{path_token}"


def _fact(
    run_id: str,
    sequence_no: int,
    kind: SourceFactKindV2,
    **fields: str | int | None,
) -> SourceFactV2 | None:
    if not any(value is not None for value in fields.values()):
        return None
    return SourceFactV2(
        source_id=f"{_SOURCE_ID_PREFIX}{sequence_no:03d}:{kind.value}",
        kind=kind,
        sequence_no=sequence_no,
        ledger_id=_safe_ledger_id(run_id, sequence_no),
        **fields,
    )


def _safe_ledger_id(run_id: str, sequence_no: int) -> str | None:
    try:
        return LedgerIdCodec.format(run_id, sequence_no)
    except (LedgerIdFormatError, TypeError):
        return None


def _mapping(value: object) -> Mapping[str, object] | None:
    return value if isinstance(value, Mapping) else None


def _positive_int(value: object) -> int | None:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < 1
        or value > _MAX_SAFE_INTEGER
    ):
        return None
    return value


def _first_safe_text(
    payload: Mapping[str, object], keys: tuple[str, ...]
) -> str | None:
    for key in keys:
        value = _safe_text(payload.get(key))
        if value is not None:
            return value
    return None


def _first_safe_opaque_ref(
    payload: Mapping[str, object], keys: tuple[str, ...]
) -> str | None:
    for key in keys:
        value = _safe_opaque_ref(payload.get(key))
        if value is not None:
            return value
    return None


def _first_safe_sandbox_operation(
    payload: Mapping[str, object], keys: tuple[str, ...]
) -> str | None:
    for key in keys:
        value = _safe_text(payload.get(key))
        if value is not None and _SANDBOX_OPERATION.fullmatch(value) is not None:
            return value
    return None


def _first_opaque_token(
    payload: Mapping[str, object], keys: tuple[str, ...]
) -> str | None:
    for key in keys:
        value = _safe_opaque_token(payload.get(key))
        if value is not None:
            return value
    return None


def _safe_text(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip() or len(value) > 512:
        return None
    lowered = value.lower()
    if (
        _PHYSICAL_PATH.search(value) is not None
        or "file://" in lowered
        or "filesystem://" in lowered
        or _SENSITIVE_TEXT.search(value) is not None
    ):
        return None
    # Keep untrusted labels exactly as text.  Rendering policy belongs at the
    # display boundary, not in this provenance fold.
    return value


def _safe_opaque_ref(value: object) -> str | None:
    if not isinstance(value, str) or len(value) > 2048 or _safe_text(value) is None:
        return None
    if (
        _OPAQUE_REF.fullmatch(value) is not None
        or _OPAQUE_TOKEN.fullmatch(value) is not None
    ):
        return value
    return None


def _safe_opaque_token(value: object) -> str | None:
    if not isinstance(value, str) or _safe_text(value) is None:
        return None
    return value if _OPAQUE_TOKEN.fullmatch(value) is not None else None


def _origin_from(payload: Mapping[str, object], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        origin = _safe_origin(payload.get(key))
        if origin is not None:
            return origin
    return None


def _safe_origin(value: object) -> str | None:
    if not isinstance(value, str) or len(value) > 2048:
        return None
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except (TypeError, ValueError):
        return None
    scheme = parsed.scheme.lower()
    host = parsed.hostname
    if (
        scheme not in {"http", "https"}
        or host is None
        or parsed.username is not None
        or parsed.password is not None
        or _SENSITIVE_TEXT.search(value) is not None
        and parsed.query == ""
    ):
        return None
    host_text = host.lower()
    if ":" in host_text and not host_text.startswith("["):
        host_text = f"[{host_text}]"
    if port is not None and port != (80 if scheme == "http" else 443):
        host_text = f"{host_text}:{port}"
    return f"{scheme}://{host_text}"


__all__ = [
    "SourceFactKindV2",
    "SourceFactV2",
    "SourcesProjectionV2",
    "SourcesProjectionStateV2",
]
