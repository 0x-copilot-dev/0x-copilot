"""Captured connector write ops — descriptors the READ already had in hand.

:class:`~.write_back.ConnectorWriteOpsPort` asks *"which write ops may the model
choose from for this connector?"*, and until this module there was no answer: the
curated ``ActionCatalog`` carries ``READ|WRITE`` and no schema, op descriptors
were persisted nowhere, and ``input_schema`` existed only on a **live**
``LoadedMcpServer``. Reaching one from the save path would have meant building an
MCP client on an HTTP request — a network round trip on a user gesture, and a
module that holds the thing that lists ops one short step from holding the thing
that calls them.

**So the ops are captured at READ time instead.** A connector surface exists only
because a read produced it, and that read ran inside a session that had already
loaded the server — the descriptors were in memory at exactly the moment the
surface was minted. This module is the record of that moment: the sibling WRITE
ops of the read's own connector, bounded and normalised, written onto the same
``surface.created`` row that declares the surface. The port then becomes a
LOOKUP, with no MCP loading on the write path and no new network hop.

**Three properties this design has to keep, and how it keeps them.**

* ``SurfaceSpec`` stays side-effect-free. The captured ops ride the ledger event
  ALONGSIDE ``state``; nothing here is reachable from a spec, and no spec member
  is read to produce it. The MODEL authors specs — it does not author this.
* Nothing here becomes a channel for model-supplied values.
  :class:`WriteOpSchemaDigest` keeps arg NAMES, arg TYPES and the ``required``
  list, and drops every other member of an untrusted connector schema (defaults,
  ``enum``, ``examples``, nested sub-schemas, per-property prose). The write
  lane's rule is *the model maps field names, never values* — a captured schema
  that carried candidate VALUES would hand it exactly the thing it is forbidden.
* Absence fails CLOSED. A surface with no captured ops yields an EMPTY candidate
  list, which :class:`~.write_mapping.SurfaceWriteMapper` refuses with
  ``NO_CANDIDATE_OPS``. A missing capture must never degrade into an unbounded
  write, because ``input_schema.required`` is the only non-model source the
  scope guard has — no schema means no bound at all.

**Why the ledger and not a store.** The read runs in ``runtime_worker``; the save
arrives in ``runtime_api``, minutes or hours later, in a different process. The
run's event log is the durable cross-process channel both already share, and
:meth:`~.write_back.SurfaceWriteBackCoordinator._origin` folds it anyway to
decide which connector a surface belongs to — so the capture costs the save path
no additional read. It is also the honest compliance record: *these are the ops
the connector offered at the instant of the read*, sitting next to the write that
was composed from them, rather than whatever the connector advertises today.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
import logging

from pydantic import Field

from agent_runtime.execution.contracts import JsonObject, RuntimeContract
from agent_runtime.surfaces_v2.constants import Keys
from agent_runtime.surfaces_v2.ledger_models import LedgerEventType

_LOGGER = logging.getLogger(__name__)

_PREFIX = "[surfaces.writeops]"


class _Limits:
    """Bounds on an untrusted descriptor and on the record it becomes.

    Every one is a REFUSAL threshold rather than a truncation point wherever
    truncating could change meaning. Dropping a property from a schema can turn
    an unusable declaration into an apparently-bounding one — see
    :meth:`WriteOpSchemaDigest.of` — so an over-cap schema is discarded whole.
    """

    #: An op name is a tool name; the MCP descriptor caps its own at 200.
    NAME_MAX = 200
    #: Connector-authored prose that reaches the mapping model's user prompt.
    #: Capped hard: it is the one member of a descriptor a hostile or
    #: compromised server writes freely, and the prompt interpolates it.
    DESCRIPTION_MAX = 400
    #: Ops captured for one connector. Matches ``write_mapping``'s own candidate
    #: ceiling — a connector with more write ops than this is one whose
    #: catalogue needs narrowing before a model sees it.
    MAX_OPS = 40
    #: Declared properties one op may have. An op wider than this is not
    #: described precisely enough for a scope rule to bound it.
    MAX_PROPERTIES = 60


class _SchemaKeys:
    """The JSON-Schema keywords a captured declaration is read through."""

    TYPE = "type"
    PROPERTIES = "properties"
    REQUIRED = "required"
    OBJECT = "object"


class _PayloadKeys:
    """Keys of the ledger record one captured op is written as."""

    NAME = "name"
    DESCRIPTION = "description"
    INPUT_SCHEMA = "input_schema"


class WriteOpCandidate(RuntimeContract):
    """One write op the model may choose, as the connector describes it.

    Structurally a subset of ``McpToolDescriptor`` (same attribute names) so a
    real descriptor drops in unchanged. Only ops a caller has already decided
    are *writes* belong here — neither this module nor the mapper classifies.

    Defined here rather than in :mod:`.write_mapping` so the capture side can
    speak the contract without importing the mapper (and the shaping model
    ladder behind it). :mod:`.write_mapping` re-exports it, so there is one
    definition and no twin to drift.
    """

    name: str = Field(min_length=1, max_length=_Limits.NAME_MAX)
    description: str = ""
    input_schema: JsonObject = Field(default_factory=dict)


class WriteOpSchemaDigest:
    """Reduce an untrusted MCP input schema to the two facts a write needs.

    Those two facts are: **which args exist** (with their scalar type, which is
    all a name-mapping decision needs) and **which args the connector says it
    cannot be called without**. The second is the only non-model source
    :class:`~.write_mapping.WriteArgScope` has for *"which args address a
    record"*, so it must survive verbatim.

    Everything else is dropped, and the dropping is a security property rather
    than a size optimisation. ``default``, ``enum``, ``examples``, ``const`` and
    per-property prose are all connector-authored CONTENT; carrying them into
    the mapping prompt would put candidate VALUES in front of a model whose one
    rule is that it never chooses a value. Nested sub-schemas go for the same
    reason plus a second: an arg that genuinely nests cannot be written through
    this lane today (``ArgBinding`` names one flat arg), so describing the
    nesting would only invite an answer the composer refuses.

    **Over-cap and malformed schemas are discarded whole, never trimmed.** A
    trimmed ``properties`` can promote an unusable declaration into a
    bounding-looking one: ``WriteArgScope`` reads ``required`` as an allow-list
    only when it is a strict subset of ``properties``, so dropping properties
    can flip that test. Returning ``{}`` instead makes the op refuse loudly with
    ``UNBOUNDED_OP``, which is the correct answer for a declaration we could not
    read.
    """

    #: JSON-Schema primitive type names kept verbatim; anything else is dropped
    #: (an untyped property is still a nameable arg).
    TYPE_NAMES = frozenset(
        {"string", "number", "integer", "boolean", "object", "array", "null"}
    )

    @classmethod
    def of(cls, schema: object) -> JsonObject:
        """Return the bounded digest, or ``{}`` when nothing usable is declared."""

        if not isinstance(schema, Mapping):
            return {}
        properties = cls._properties(schema.get(_SchemaKeys.PROPERTIES))
        if properties is None:
            return {}
        required = cls._required(schema.get(_SchemaKeys.REQUIRED))
        if required is None or not required <= set(properties):
            # A required name the schema never declared is a contradiction, and
            # silently dropping it would shrink the set the scope rule trusts.
            return {}
        return {
            _SchemaKeys.TYPE: _SchemaKeys.OBJECT,
            _SchemaKeys.PROPERTIES: dict(properties),
            _SchemaKeys.REQUIRED: sorted(required),
        }

    @classmethod
    def _properties(cls, value: object) -> dict[str, JsonObject] | None:
        """Name → ``{type?}``, or ``None`` when unreadable / over cap."""

        if not isinstance(value, Mapping) or not value:
            return None
        if len(value) > _Limits.MAX_PROPERTIES:
            return None
        properties: dict[str, JsonObject] = {}
        for name, declared in value.items():
            if not isinstance(name, str) or not name:
                return None
            if len(name) > _Limits.NAME_MAX:
                return None
            properties[name] = cls._property(declared)
        return properties

    @classmethod
    def _property(cls, declared: object) -> JsonObject:
        """One property's kept members: its type name and nothing else."""

        if not isinstance(declared, Mapping):
            return {}
        type_name = declared.get(_SchemaKeys.TYPE)
        if isinstance(type_name, str) and type_name in cls.TYPE_NAMES:
            return {_SchemaKeys.TYPE: type_name}
        return {}

    @staticmethod
    def _required(value: object) -> set[str] | None:
        """The declared required names, or ``None`` when the member is malformed.

        An ABSENT ``required`` is not malformed — it is a schema that declares
        no addressing key, which the scope rule already refuses on its own
        terms. A ``required`` that is present but not a list of plain names is
        malformed, and the whole digest is discarded.
        """

        if value is None:
            return set()
        if not isinstance(value, (list, tuple)):
            return None
        names: set[str] = set()
        for item in value:
            if not isinstance(item, str) or not item:
                return None
            names.add(item)
        return names


class CapturedWriteOps:
    """Bound, encode and decode the write ops captured for one surface.

    The codec is total in both directions and trusts neither: what it writes is
    bounded because a ledger row is durable, and what it reads is re-validated
    because a stored payload has crossed a process boundary and, on the desktop,
    a file on the user's own disk.
    """

    @classmethod
    def bounded(cls, ops: Iterable[WriteOpCandidate]) -> tuple[WriteOpCandidate, ...]:
        """Normalise + cap the ops captured for one connector.

        An op whose schema does not digest is KEPT with an empty schema rather
        than dropped, because dropping it would silently narrow what the model
        may choose while an empty schema makes the op refuse loudly the moment
        it is chosen (``UNBOUNDED_OP``). Loud beats absent: *"this connector
        does not say which arguments identify a record"* is diagnosable, where a
        vanished op reads as *"this connector has no write op"*.
        """

        captured: list[WriteOpCandidate] = []
        seen: set[str] = set()
        for op in ops:
            name = op.name.strip()
            if not name or name in seen:
                continue
            seen.add(name)
            captured.append(
                WriteOpCandidate(
                    name=name[: _Limits.NAME_MAX],
                    description=op.description.strip()[: _Limits.DESCRIPTION_MAX],
                    input_schema=WriteOpSchemaDigest.of(op.input_schema),
                )
            )
            if len(captured) >= _Limits.MAX_OPS:
                break
        return tuple(captured)

    @classmethod
    def to_payload(cls, ops: Sequence[WriteOpCandidate]) -> list[JsonObject]:
        """Render the captured ops as the ledger's plain-JSON record."""

        return [
            {
                _PayloadKeys.NAME: op.name,
                _PayloadKeys.DESCRIPTION: op.description,
                _PayloadKeys.INPUT_SCHEMA: dict(op.input_schema),
            }
            for op in cls.bounded(ops)
        ]

    @classmethod
    def from_payload(cls, raw: object) -> tuple[WriteOpCandidate, ...]:
        """Read a stored record back, dropping anything that does not validate.

        Total: a malformed entry is skipped and the rest survive, because one
        unreadable descriptor is not a reason to refuse every write on a
        connector. An entirely unreadable record yields an empty tuple, which is
        the fail-closed answer the whole lane already handles.
        """

        if not isinstance(raw, (list, tuple)):
            return ()
        parsed: list[WriteOpCandidate] = []
        for entry in raw:
            if not isinstance(entry, Mapping):
                continue
            name = entry.get(_PayloadKeys.NAME)
            if not isinstance(name, str) or not name.strip():
                continue
            description = entry.get(_PayloadKeys.DESCRIPTION)
            schema = entry.get(_PayloadKeys.INPUT_SCHEMA)
            parsed.append(
                WriteOpCandidate(
                    name=name.strip()[: _Limits.NAME_MAX],
                    description=(
                        description.strip()[: _Limits.DESCRIPTION_MAX]
                        if isinstance(description, str)
                        else ""
                    ),
                    # Digested again on the way in: the stored value is only as
                    # trustworthy as the file it came off, and a digest of a
                    # digest is the same digest.
                    input_schema=WriteOpSchemaDigest.of(schema),
                )
            )
        return cls.bounded(parsed)


class CapturedSurfaceWriteOps(RuntimeContract):
    """What one surface's read captured: its connector, and that connector's ops."""

    surface_id: str
    connector: str
    ops: tuple[WriteOpCandidate, ...] = ()


class CapturedWriteOpsProjection:
    """Pure fold from a run's ledger events to its captured write ops.

    A sibling of :class:`~agent_runtime.surfaces_v2.projection.SurfaceStoreProjection`
    and deliberately NOT a member of it: the SurfaceStore fold is served as-is by
    ``GET /v1/agent/runs/{run_id}/surfaces``, and connector op declarations are
    server-side write-path material with no business on that response. Same
    events, same determinism, separate output.

    Reads events structurally (``event_type`` / ``payload``) for the same reason
    the SurfaceStore fold does — a ``RuntimeEventEnvelope`` satisfies it by
    shape and no ``runtime_api`` import is needed. Later reads of the same
    surface refresh the capture: repeat reads re-declare the connector's ops,
    and the freshest declaration is the one a save should be composed against.
    """

    @classmethod
    def fold(cls, events: Iterable[object]) -> dict[str, CapturedSurfaceWriteOps]:
        """Return ``surface_id -> CapturedSurfaceWriteOps`` for one run."""

        return cls.fold_raw(
            {
                _RawKey.EVENT_TYPE: cls._event_type_value(
                    getattr(event, "event_type", "")
                ),
                _RawKey.PAYLOAD: getattr(event, "payload", None),
            }
            for event in events
        )

    @classmethod
    def fold_raw(
        cls, events: Iterable[Mapping[str, object]]
    ) -> dict[str, CapturedSurfaceWriteOps]:
        """Fold plain ``{event_type, payload}`` dicts. Total; never raises."""

        captured: dict[str, CapturedSurfaceWriteOps] = {}
        for event in events:
            if str(event.get(_RawKey.EVENT_TYPE, "")) != (
                LedgerEventType.SURFACE_CREATED.value
            ):
                continue
            payload = event.get(_RawKey.PAYLOAD)
            if not isinstance(payload, Mapping):
                continue
            entry = cls._entry(payload)
            if entry is not None:
                captured[entry.surface_id] = entry
        return captured

    @classmethod
    def _entry(cls, payload: Mapping[str, object]) -> CapturedSurfaceWriteOps | None:
        surface_id = payload.get(Keys.Field.SURFACE_ID)
        if not isinstance(surface_id, str) or not surface_id:
            return None
        source = payload.get(Keys.Field.SOURCE)
        source = source if isinstance(source, Mapping) else {}
        connector = source.get(Keys.Field.CONNECTOR)
        ops = CapturedWriteOps.from_payload(payload.get(Keys.Field.WRITE_OPS))
        return CapturedSurfaceWriteOps(
            surface_id=surface_id,
            connector=connector if isinstance(connector, str) else "",
            ops=ops,
        )

    @staticmethod
    def _event_type_value(event_type: object) -> str:
        value = getattr(event_type, "value", None)
        return value if isinstance(value, str) else str(event_type)


class _RawKey:
    """Keys of the plain-dict event shape :meth:`fold_raw` consumes."""

    EVENT_TYPE = "event_type"
    PAYLOAD = "payload"


@dataclass(frozen=True)
class CapturedConnectorWriteOps:
    """The production :class:`~.write_back.ConnectorWriteOpsPort`: a LOOKUP.

    It holds no client, no registry and no credential, and it performs no I/O —
    every op it can return was already read off the run's own ledger by the
    coordinator's origin fold. That is the whole point of capturing at read
    time: the save path asks a question that was already answered, so there is
    no per-call hop to put on it and nothing here that could execute an op.

    The connector is re-checked rather than assumed. A capture belongs to the
    surface it was written with, and the coordinator resolves the connector from
    that same surface, so a mismatch means the two disagree about what the user
    is looking at — which is the one condition under which staging a write would
    be composing against the wrong vendor. Refuse by returning nothing; the
    mapper turns that into ``NO_CANDIDATE_OPS`` and the save stages nothing.

    Substituting a different adapter remains a deployment's choice: this is also
    where narrowing to *"writes this user is permitted"* belongs, and a
    deployment with a real, permissioned catalogue can bind one on
    ``app.state.connector_write_ops`` without the coordinator learning about it.
    """

    async def write_ops(
        self,
        *,
        org_id: str,
        user_id: str,
        connector: str,
        captured: Sequence[WriteOpCandidate],
        captured_connector: str,
    ) -> Sequence[WriteOpCandidate]:
        """Return the captured ops for this surface's connector (may be empty)."""

        del org_id, user_id  # scope is decided before this port is reached
        if captured_connector and captured_connector != connector:
            _LOGGER.warning(
                "%s capture_connector_mismatch surface=%s save=%s: no candidates",
                _PREFIX,
                captured_connector,
                connector,
            )
            return ()
        return tuple(captured)


__all__ = [
    "CapturedConnectorWriteOps",
    "CapturedSurfaceWriteOps",
    "CapturedWriteOps",
    "CapturedWriteOpsProjection",
    "WriteOpCandidate",
    "WriteOpSchemaDigest",
]
