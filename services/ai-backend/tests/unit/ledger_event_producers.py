"""Static scan for the producer of every ``LedgerEventType`` member.

A read model whose event nothing writes is the sharpest failure mode this
subsystem has: ``gate.resolved.v2`` had **five** readers — ``PendingWorkV2``,
``CanvasLifecycle``, the run receipt, the v2 receipt export, the effect-ledger
projection — and no producer anywhere in ``src``. Every one of those folds was
tested, every test passed, and a workspace gate could be recorded opening and
never recorded closing.

The reason this is invisible to ordinary review is that **references are not
evidence**. All 34 members are referenced: folds compare against them, dispatch
tables key on them, receipt drafts carry them, ``frozenset({...})`` membership
sets enumerate them. An earlier pass of this analysis counted references and
concluded, wrongly, that every member was live. ``gate.resolved.v2`` appeared in
``PendingWorkV2``'s ``frozenset`` of interesting types — a *reader* — and that
alone would have cleared it.

So this module looks for the two things only a writer does:

**1. Payload-model construction.** ``OperationRequestedPayload(v=1, ...)``.
A reader never builds the payload model field-by-field; it validates an
untrusted dict through ``WorkLedgerVocabulary.validate_payload``, which reaches
the model as ``model_validate`` — a different callee, deliberately not counted.

**2. Emission** — a call that carries *both* an event-type token and a payload
body. The body is what separates the writer from the reader, and it is a
structural signal rather than a list of blessed function names: a fold that
mentions ``LedgerEventType.EFFECT_STAGED`` inside ``frozenset({...})`` passes no
body, and ``_RowDraft(LedgerEventType.READ_EXECUTED, ...)`` — a frozen receipt
row — has no payload field to pass. Both are correctly silent here. Every real
append seam in the tree (``append_api_event``, ``ledger.emit``,
``append_stage_event``, ``WorkLedgerEmitter._sign``, ``EffectResultRecorder._append``)
hands over a dict literal, a ``model_dump()``, or a local bound to one — so the
gate needs no allow-list of seam names to maintain, and a new seam is covered
the day it is written.

Scope is ``src`` only, never ``tests``. A payload constructed exclusively by a
test is the defect, not the refutation: 9,094 passing tests were green over this
subsystem's dead half.

A member that genuinely has no producer and is meant to stay that way is
declared in :data:`READER_ONLY_EVENTS` with a reason — the same shape
``APPROVED_EXECUTION_PATHS`` uses in
``tests/unit/architecture/effect_execution_reachability.py``. It is a tuple of
records rather than a line in ``orphan_ratchet_baseline.txt`` for two reasons.
That file declares one format — "one dotted module path per line, sorted" — and
its own header tells the next maintainer to regenerate the set from
``tools/ai-backend-smells/orphans.py``, whose output contains module paths and
nothing else; a hand-curated event line has no regenerated counterpart and would
be dropped by the first person who follows those instructions. And a ``reason``
field cannot be left off the way a trailing ``#`` comment can. Entries are
checked for staleness in both directions: an entry naming a member that is no
longer real, or one that has since acquired a producer, fails.
"""

from __future__ import annotations

import ast
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from agent_runtime.surfaces_v2.ledger_models import (
    LedgerEventType,
    WorkLedgerVocabulary,
)


# The enum names a producer may spell an event type with. ``RuntimeApiEventType``
# is the transport mirror whose members are assigned straight off
# ``LedgerEventType`` values (``runtime_api/schemas/common.py``); the stage-commit
# handler emits ``write.applied`` through it, so a scan that recognised only the
# domain enum would report the sole producer of that event as missing.
EVENT_TYPE_ENUMS = frozenset({"LedgerEventType", "RuntimeApiEventType"})

# Callees that hand back a payload body. ``model_dump`` is how a constructed
# payload model reaches the wire; ``model_validate`` is deliberately absent —
# validating a dict someone else wrote is the reader's move.
PAYLOAD_PRODUCING_CALLS = frozenset({"model_dump", "model_dump_json", "dict"})

# The module that DEFINES the vocabulary. Naming a payload model there is a
# declaration, not a production.
VOCABULARY_MODULE = Path("agent_runtime/surfaces_v2/ledger_models.py")

PAYLOAD_MODEL_SIGNAL = "payload-model"
EMISSION_SIGNAL = "emission"


@dataclass(frozen=True)
class ReaderOnlyEvent:
    """One member deliberately carrying readers and no writer, with its reason."""

    member: str
    reason: str


# Empty is the correct state. An entry here is debt with a name on it, not
# approval: it says the vocabulary can record something no code in this service
# can ever write, and every fold reading it is folding over nothing.
#
# Members whose emission is gated behind a default-off flag do NOT belong here.
# ``OPERATION_REQUESTED`` / ``OPERATION_CLASSIFIED`` / ``OPERATION_COMPLETED`` /
# ``OPERATION_FAILED`` need ``OPERATION_GATEWAY_MODE=enforce`` and
# ``GATE_OPENED_V2`` / ``GATE_RESOLVED_V2`` need ``WORKSPACE_EFFECT_MODE=enforce``
# — but the producer is checked in, reachable, and reviewable. A flag decides
# whether it runs today; this gate asks whether it exists at all.
READER_ONLY_EVENTS: tuple[ReaderOnlyEvent, ...] = ()


@dataclass(frozen=True)
class ProducerSite:
    """One place in ``src`` that writes an event of this type."""

    member: str
    path: str
    line: int
    signal: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line} ({self.signal})"


class LedgerEventProducerScan:
    """AST-only inventory of who WRITES each ledger event type.

    The vocabulary comes from the imported ``LedgerEventType`` /
    ``WorkLedgerVocabulary`` so "every member" always means every member of the
    live enum and cannot drift from a copy. Only the *sites* come from the
    scanned tree, which is what lets the canaries point this at a planted
    ``tmp_path`` holding one deliberate defect.
    """

    def __init__(self, source_root: Path) -> None:
        self._source_root = source_root
        self._payload_models: Mapping[str, str] = {
            member.name: model.__name__
            for member, model in WorkLedgerVocabulary.PAYLOAD_MODELS.items()
        }
        self._values: Mapping[str, str] = {
            member.value: member.name for member in LedgerEventType
        }
        self._sites = tuple(
            sorted(self._scan(), key=lambda s: (s.member, s.path, s.line))
        )

    # -- results ------------------------------------------------------------

    @property
    def members(self) -> tuple[str, ...]:
        """Every member of the live enum, in contract order."""

        return tuple(member.name for member in LedgerEventType)

    @property
    def sites(self) -> tuple[ProducerSite, ...]:
        """Every producer site found, sorted."""

        return self._sites

    def sites_for(self, member: str) -> tuple[ProducerSite, ...]:
        return tuple(site for site in self._sites if site.member == member)

    def members_without_a_producer(self) -> tuple[str, ...]:
        """Members no site in the scanned tree writes."""

        produced = {site.member for site in self._sites}
        return tuple(member for member in self.members if member not in produced)

    def undeclared_producerless_members(self) -> tuple[str, ...]:
        """Producerless members that are not declared reader-only."""

        declared = {entry.member for entry in READER_ONLY_EVENTS}
        return tuple(
            member
            for member in self.members_without_a_producer()
            if member not in declared
        )

    def stale_reader_only_declarations(self) -> tuple[str, ...]:
        """Reader-only entries that no longer describe the tree.

        Fails in both directions, because a baseline that only ever grows is a
        place to hide things: an entry naming a member the enum no longer has is
        stale, an entry for a member that has since acquired a producer must be
        pruned in the same change that wired it, and an entry with no reason is
        not a declaration at all.
        """

        stale: list[str] = []
        real = set(self.members)
        producerless = set(self.members_without_a_producer())
        for entry in READER_ONLY_EVENTS:
            if entry.member not in real:
                stale.append(f"{entry.member}: not a LedgerEventType member")
            elif entry.member not in producerless:
                stale.append(
                    f"{entry.member}: now produced by "
                    f"{', '.join(str(s) for s in self.sites_for(entry.member))}"
                )
            if not entry.reason.strip():
                stale.append(f"{entry.member}: declared reader-only with no reason")
        return tuple(sorted(stale))

    # -- scan ---------------------------------------------------------------

    def _scan(self) -> Iterator[ProducerSite]:
        model_members = {
            model: member for member, model in self._payload_models.items()
        }
        for path in sorted(self._source_root.rglob("*.py")):
            relative = path.relative_to(self._source_root)
            # A parse failure is deliberately not swallowed. Skipping an
            # unparseable module would let the file that holds the only producer
            # of an event drop out of the scan and report the event as
            # producerless — or, worse, let a whole subtree vanish while the
            # gate still read green.
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            posix = relative.as_posix()
            aliases = self._module_aliases(tree)
            seen: set[tuple[str, int, str]] = set()
            for scope_body, bound in _payload_scopes(tree):
                for node in _calls(scope_body):
                    for member, signal in self._signals(
                        node,
                        aliases=aliases,
                        payload_locals=bound,
                        model_members=model_members,
                        relative=relative,
                    ):
                        key = (member, node.lineno, signal)
                        if key in seen:
                            continue
                        seen.add(key)
                        yield ProducerSite(
                            member=member,
                            path=posix,
                            line=node.lineno,
                            signal=signal,
                        )

    def _signals(
        self,
        node: ast.Call,
        *,
        aliases: Mapping[str, str],
        payload_locals: frozenset[str],
        model_members: Mapping[str, str],
        relative: Path,
    ) -> Iterator[tuple[str, str]]:
        callee = _callee_name(node.func)
        if (
            callee is not None
            and callee in model_members
            and relative != VOCABULARY_MODULE
        ):
            yield model_members[callee], PAYLOAD_MODEL_SIGNAL
        arguments = tuple(_argument_expressions(node))
        if not any(
            _is_payload_body(argument, payload_locals) for argument in arguments
        ):
            return
        for argument in arguments:
            member = self._event_token(argument, aliases)
            if member is not None:
                yield member, EMISSION_SIGNAL

    def _module_aliases(self, tree: ast.Module) -> Mapping[str, str]:
        """Module-level constants that stand in for an event type.

        ``_EVENT_STAGED = LedgerEventType.EFFECT_STAGED.value`` is how the effect
        modules spell it, so an emission there names a local, not the enum.
        Deliberately module level only: class bodies are where the transport
        MIRRORS the vocabulary (``RuntimeApiEventType.SURFACE_CREATED =
        LedgerEventType.SURFACE_CREATED.value``), and a mirror declaration is not
        a producer.
        """

        aliases: dict[str, str] = {}
        for statement in tree.body:
            targets: Sequence[ast.expr]
            if isinstance(statement, ast.Assign):
                targets = statement.targets
                value = statement.value
            elif isinstance(statement, ast.AnnAssign) and statement.value is not None:
                targets = (statement.target,)
                value = statement.value
            else:
                continue
            member = self._event_token(value, {})
            if member is None:
                continue
            for target in targets:
                if isinstance(target, ast.Name):
                    aliases[target.id] = member
        return aliases

    def _event_token(self, node: ast.expr, aliases: Mapping[str, str]) -> str | None:
        """Resolve an expression to the event-type member it names, if any."""

        current: ast.expr = node
        if isinstance(current, ast.Await):
            current = current.value
        # ``RuntimeApiEventType(LedgerEventType.WRITE_APPLIED.value)`` — the
        # stage-commit handler's conversion into the transport enum.
        if (
            isinstance(current, ast.Call)
            and len(current.args) == 1
            and not current.keywords
            and (name := _callee_name(current.func)) is not None
            and name.endswith("EventType")
        ):
            current = current.args[0]
        if isinstance(current, ast.Attribute) and current.attr == "value":
            current = current.value
        if (
            isinstance(current, ast.Attribute)
            and isinstance(current.value, ast.Name)
            and current.value.id in EVENT_TYPE_ENUMS
            and current.attr in self._payload_models
        ):
            return current.attr
        if isinstance(current, ast.Name) and current.id in aliases:
            return aliases[current.id]
        if isinstance(current, ast.Constant) and isinstance(current.value, str):
            return self._values.get(current.value)
        return None


# ---------------------------------------------------------------------------
# AST helpers
# ---------------------------------------------------------------------------


def _callee_name(func: ast.expr) -> str | None:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _argument_expressions(node: ast.Call) -> Iterator[ast.expr]:
    """Every expression the call is handed, one container literal deep.

    Membership sets are written as ``frozenset({A, B})`` and dispatch tables as
    ``(A, B)``, so unwrapping one level is what lets the body requirement do its
    job: those calls hand over event types and nothing else.
    """

    for argument in node.args:
        yield argument
        if isinstance(argument, (ast.List, ast.Tuple, ast.Set)):
            yield from argument.elts
    for keyword in node.keywords:
        yield keyword.value
        if isinstance(keyword.value, (ast.List, ast.Tuple, ast.Set)):
            yield from keyword.value.elts


def _is_payload_body(node: ast.expr, payload_locals: frozenset[str]) -> bool:
    """Whether this argument is the event's body rather than a mention of it."""

    current = node.value if isinstance(node, ast.Await) else node
    if isinstance(current, (ast.Dict, ast.DictComp)):
        return True
    if isinstance(current, ast.Call):
        callee = _callee_name(current.func)
        return callee is not None and _names_a_payload(callee)
    return isinstance(current, ast.Name) and current.id in payload_locals


def _names_a_payload(callee: str) -> bool:
    return (
        callee in PAYLOAD_PRODUCING_CALLS
        or callee.endswith("_payload")
        or callee.endswith("Payload")
    )


def _payload_scopes(
    tree: ast.Module,
) -> Iterator[tuple[list[ast.stmt], frozenset[str]]]:
    """Yield each scope's body with the local names bound to a payload body.

    A scope is the module body or any function, at any depth. Bindings are
    gathered over the whole function including its nested definitions, which
    over-approximates for a closure — harmless, because a name only matters when
    it rides the same call as an event token.
    """

    module_bound = _payload_bindings(tree.body, recurse=False)
    yield list(tree.body), module_bound
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            yield (
                list(node.body),
                module_bound | _payload_bindings(node.body, recurse=True),
            )


def _payload_bindings(body: Iterable[ast.stmt], *, recurse: bool) -> frozenset[str]:
    bound: set[str] = set()
    for statement in body:
        nodes = ast.walk(statement) if recurse else (statement,)
        for node in nodes:
            targets: Sequence[ast.expr]
            if isinstance(node, ast.Assign):
                targets = node.targets
                value: ast.expr | None = node.value
            elif isinstance(node, ast.AnnAssign):
                targets = (node.target,)
                value = node.value
            elif isinstance(node, ast.NamedExpr):
                targets = (node.target,)
                value = node.value
            else:
                continue
            if value is None or not _is_payload_body(value, frozenset()):
                continue
            for target in targets:
                if isinstance(target, ast.Name):
                    bound.add(target.id)
    return frozenset(bound)


def _calls(body: Iterable[ast.stmt]) -> Iterator[ast.Call]:
    for statement in body:
        for node in ast.walk(statement):
            if isinstance(node, ast.Call):
                yield node


__all__ = (
    "EMISSION_SIGNAL",
    "EVENT_TYPE_ENUMS",
    "LedgerEventProducerScan",
    "PAYLOAD_MODEL_SIGNAL",
    "ProducerSite",
    "READER_ONLY_EVENTS",
    "ReaderOnlyEvent",
    "VOCABULARY_MODULE",
)
