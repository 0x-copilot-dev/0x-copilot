"""CI guard: the SUB-module forms of "landed but not wired".

This repo's defining pathology is a correct mechanism shipped with its last seam
missing: unreachable at runtime, green in every unit test. Two gates already
catch it at *module* scale — the orphan ratchet
(``services/ai-backend/tests/unit/orphan_ratchet_baseline.txt``) for a module
nothing imports, and :mod:`tools.check_dark_capabilities` for an env flag no test
turns on. Both are blind below the module line, and that is where the population
actually lives. An audit across nine dimensions found this defect in six of them,
every instance *inside* a module that is imported, exercised and green:

* FTS5 conversation search — an index maintained on every write whose only
  callers are its own adapter and a test.
* the export/import archive — same shape, same file.
* ``grant_options`` — emitted on every filesystem approval; the sole app-side
  mention is a *strip list*, i.e. the client's instruction to throw it away.
* ``approvals.expires_at`` — read by a sweeper, populated by nothing.
* skills ``allowed_tools`` — parsed, typed and validated, then spent on one
  f-string in a prompt. Advice to the model, not a rule the runtime enforces.

Three detectors, one per shape
==============================

**1. ``test-only``** — a symbol whose only exercising reference is a test. What
counts as "exercising" depends on what the symbol *is*, and collapsing that
distinction is what makes a naive version useless:

* a **callable** is exercised by a caller, so a mention from any other src module
  counts — and so does a call from elsewhere in its own module, since a reachable
  module makes the call reachable too;
* a **contract field** is exercised by a *producer*, and only the ``x: T | None =
  None`` shape is in scope. The field must be threaded through constructors
  somewhere as a copy of itself and never once originated: alive-looking at every
  hop, produced at none. That is exactly ``expires_at``, which has a reader, a
  sweeper and a query, and no writer. A field merely sitting on a working default
  is a different and uninteresting fact, and a contract whose fields are *mostly*
  copies is a projection doing its job;
* a **wire key** (``FIELD = "some_key"``) is exercised by whoever reads the key
  off the payload, and for this service that consumer is TypeScript. So the app
  tree is scanned for a genuine property read (``payload.some_key`` /
  ``payload["some_key"]``). A bare string in a strip list is not a read, and a
  ``some_key?: string[]`` member in ``packages/api-types`` is a declaration, not
  a consumer — which is precisely why ``grant_options`` looks wired and is not.
  Only constants Python uses in *key position* qualify, which is what keeps enum
  values (compared, never property-read) out of a detector that cannot see them.

**2. ``backend-only``** — a public store capability defined in exactly one
``runtime_adapters/<backend>/`` package, absent from its sibling backends and
from every ``Protocol`` port in the service, **and named by no module outside
that package**. Nothing typed against the port can call it; it is reachable only
by first proving which backend is live, so the other backends silently degrade.
This is the ``runtime_worker/file_store_wiring`` shape (``hasattr(store, ...)``
→ capability or ``None``) stated as a static fact about the class surface
instead of a runtime probe. Both halves are required: a file-native store
legitimately owns dozens of methods its in-memory sibling has no reason to have,
and all of those are called.

**3. ``prompt-only``** — a validated contract field whose every read outside its
declaring package is interpolated into an f-string. The value was *validated*, so
it reads as enforced; it is advisory text handed to a model free to ignore it.

Attribution, and why it follows the import graph
================================================

Detector 3 is keyed on ``(package, field name)``, not on the field name alone,
and that is load-bearing rather than tidy. ``allowed_tools`` is declared by six
different classes across three packages, so a name-only index cannot tell which
one a read refers to — and gets it exactly backwards: the *subagent* handoff
genuinely narrows tools, which makes the name look enforced and hides the
*skills* manifest field whose only consumer is a prompt. The gate then flags the
one field that is enforced and stays silent on the one that is not.

The import graph settles it without type inference. A read in file F is
attributed to a declaration in package P only when F is in P or imports from it:
``handoff.py`` never imports ``capabilities.skills``, so its real read cannot be
of the skills field; ``factory.py`` imports both, so its prompt read counts
against both. A file can only read a field of a contract it can name.

A real read *anywhere that can see the field* counts as enforcement, including
inside the declaring package — local enforcement is enforcement, and excluding
own-package reads discards precisely the evidence that ``SubagentTask``'s field
is enforced by its package-mate.

A read that only moves the value on — ``allowed_tools=tuple(x.allowed_tools)``,
the same name in and out — is a **pass-through**, not a consumer. Counting those
as enforcement is what let a five-hop copy chain ending in an f-string read as
wired at every hop.

Direction of error
==================

Every detector is deliberately biased toward **silence**. All three ask "is there
any evidence of a real consumer", and ambiguous evidence counts as one: an
unresolvable receiver makes a symbol look live, not dark. That direction can only
ever miss a dark symbol; it cannot redden a build over a false positive, which is
the property that decides whether a gate survives its first month.

So a verdict here means "no consumer was found", never "no consumer exists".

The ratchet
===========

Today's population is recorded in :data:`BASELINE_PATH`, one line per entry with
a hand-written reason. Only a *new* dark symbol fails. There is deliberately no
``--update-baseline`` writer, for the reason ``route_reachability_baseline.txt``
has none: the only sanctioned way to add a line is to write the reason by hand,
where a reviewer reads it in the diff. A baselined symbol that later gains a real
consumer makes its line **stale**, and a stale line also fails — so the file can
only ever shrink.

Waiver: put ``# dark-wiring-waiver: <reason>`` on the declaration line.

Usage::

    python tools/check_dark_wiring.py           # ratchet against the baseline
    python tools/check_dark_wiring.py --list    # every finding, exit 0

Exits non-zero listing every new dark symbol and every stale baseline line.
"""

from __future__ import annotations

import argparse
import ast
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

SRC_ROOT: Path = REPO_ROOT / "services" / "ai-backend" / "src"
TEST_ROOT: Path = REPO_ROOT / "services" / "ai-backend" / "tests"
APP_ROOTS: tuple[Path, ...] = (REPO_ROOT / "apps", REPO_ROOT / "packages")

#: Declarations of the wire contract, not consumers of it. A key appearing only
#: here is still dark — that is the ``grant_options`` reading exactly.
TS_DECLARATION_ROOT: Path = REPO_ROOT / "packages" / "api-types"

#: The store backend packages. A capability on one and not the others is
#: unreachable through the port they share.
ADAPTER_ROOT: Path = SRC_ROOT / "runtime_adapters"

BASELINE_PATH: Path = REPO_ROOT / "tools" / "dark_wiring_baseline.txt"
BASELINE_SEPARATOR = " :: "
WAIVER_MARKER = "# dark-wiring-waiver:"

_SKIP_DIRS = frozenset({".venv", "venv", "__pycache__", "node_modules", "dist", ".git"})
_TS_SUFFIXES = frozenset({".ts", ".tsx"})

_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
#: A wire-key value: lower snake_case, which is how every payload key is spelled.
_WIRE_KEY_VALUE = re.compile(r"[a-z][a-z0-9]*(?:_[a-z0-9]+)+$")
#: TS property reads. Deliberately two narrow forms, because the whole point is
#: to NOT count a bare string sitting in a strip list or an ``x?: T`` member.
_TS_DOT_READ = re.compile(r"\.([A-Za-z_$][A-Za-z0-9_$]*)")
_TS_INDEX_READ = re.compile(r"""\[\s*["'`]([A-Za-z_][A-Za-z0-9_]*)["'`]\s*\]""")

#: Methods every framework calls for us. Absent a caller they are still live.
_FRAMEWORK_HOOKS = frozenset(
    {
        "setup",
        "teardown",
        "close",
        "start",
        "stop",
        "run",
        "main",
        "dispatch",
        "handle",
        "aclose",
        "model_post_init",
    }
)


def _is_public(name: str) -> bool:
    return not name.startswith("_") and len(name) >= 4


class Finding:
    """One dark symbol: its ratchet key, where it is declared, and why."""

    __slots__ = ("key", "detector", "file", "lineno", "reason")

    def __init__(
        self, *, key: str, detector: str, file: Path, lineno: int, reason: str
    ) -> None:
        self.key = key
        self.detector = detector
        self.file = file
        self.lineno = lineno
        self.reason = reason

    @property
    def location(self) -> str:
        try:
            rel = self.file.relative_to(REPO_ROOT)
        except ValueError:
            rel = self.file
        return f"{rel}:{self.lineno}"

    def render(self) -> str:
        return f"{self.location}: [{self.detector}] {self.key} — {self.reason}"


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _iter_files(roots: tuple[Path, ...], *, suffixes: frozenset[str]) -> list[Path]:
    results: list[Path] = []
    for root in roots:
        if root.is_file():
            if root.suffix in suffixes:
                results.append(root)
            continue
        if not root.is_dir():
            continue
        for path in root.rglob("*"):
            if path.suffix not in suffixes or not path.is_file():
                continue
            if any(part in _SKIP_DIRS for part in path.parts):
                continue
            results.append(path)
    return sorted(results)


class PyFile:
    """One parsed Python file plus the token set used for cheap name lookups."""

    __slots__ = ("path", "text", "tree", "tokens", "lines")

    def __init__(self, *, path: Path, text: str, tree: ast.Module) -> None:
        self.path = path
        self.text = text
        self.tree = tree
        self.lines = text.splitlines()
        self.tokens = frozenset(_IDENTIFIER.findall(text))

    def waived(self, lineno: int) -> bool:
        index = lineno - 1
        if 0 <= index < len(self.lines):
            return WAIVER_MARKER in self.lines[index]
        return False


def load_python(roots: tuple[Path, ...]) -> list[PyFile]:
    """Parse every Python file under ``roots``, skipping any that will not parse.

    A file this gate cannot parse is a problem for the type checker and the test
    suite, not for a static scan whose job is to stay total.
    """

    loaded: list[PyFile] = []
    for path in _iter_files(roots, suffixes=frozenset({".py"})):
        text = _read(path)
        if text is None:
            continue
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        loaded.append(PyFile(path=path, text=text, tree=tree))
    return loaded


def _package_of(path: Path) -> str:
    """The dotted package a src file lives in — the attribution unit for reads."""

    try:
        rel = path.relative_to(SRC_ROOT)
    except ValueError:
        return str(path.parent)
    return ".".join(rel.parts[:-1])


def _module_of(path: Path) -> str:
    try:
        rel = path.relative_to(SRC_ROOT)
    except ValueError:
        return path.stem
    return ".".join((*rel.parts[:-1], rel.stem))


# --------------------------------------------------------------------------
# Declaration scanning
# --------------------------------------------------------------------------


class Declaration:
    """A public symbol declared in src, with everything a detector needs."""

    __slots__ = (
        "name",
        "kind",
        "owner",
        "file",
        "lineno",
        "validated",
        "has_default",
        "attr",
        "default_is_none",
    )

    def __init__(
        self,
        *,
        name: str,
        kind: str,
        owner: str,
        file: Path,
        lineno: int,
        validated: bool = False,
        has_default: bool = False,
        attr: str = "",
        default_is_none: bool = False,
    ) -> None:
        self.name = name
        self.kind = kind
        self.owner = owner
        self.file = file
        self.lineno = lineno
        self.validated = validated
        self.has_default = has_default
        #: For a wire key, the constant's own spelling (``GRANT_OPTIONS``) as
        #: opposed to the value it carries (``grant_options``). Needed to ask
        #: whether the service uses it in key position.
        self.attr = attr
        #: ``x: T | None = None`` exactly. See :func:`_field_finding`.
        self.default_is_none = default_is_none


def _is_contract_class(node: ast.ClassDef) -> bool:
    """A declarative data contract: has a base, ≥2 annotated fields, no __init__.

    A structural proxy for "pydantic model or dataclass" that needs no import
    resolution — the bases in this service are spelled a dozen ways
    (``RuntimeContract``, ``BaseModel``, ``_Record``, a generic alias), and a
    name allowlist over them went stale the week it was written.
    """

    if not node.bases:
        return False
    fields = 0
    for statement in node.body:
        if isinstance(statement, ast.FunctionDef) and statement.name == "__init__":
            return False
        if isinstance(statement, ast.AnnAssign) and isinstance(
            statement.target, ast.Name
        ):
            fields += 1
    return fields >= 2


def _has_field_call(node: ast.AnnAssign) -> bool:
    """``x: T = Field(...)`` — the marker of a *validated* contract field."""

    value = node.value
    if not isinstance(value, ast.Call):
        return False
    func = value.func
    name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
    return name == "Field"


def collect_declarations(src: list[PyFile]) -> list[Declaration]:
    """Every public callable, contract field and wire key declared in src."""

    found: list[Declaration] = []
    for file in src:
        for node in ast.walk(file.tree):
            if not isinstance(node, ast.ClassDef):
                continue
            contract = _is_contract_class(node)
            for statement in node.body:
                if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    if not _is_public(statement.name):
                        continue
                    if statement.name in _FRAMEWORK_HOOKS:
                        continue
                    if file.waived(statement.lineno):
                        continue
                    found.append(
                        Declaration(
                            name=statement.name,
                            kind="callable",
                            owner=node.name,
                            file=file.path,
                            lineno=statement.lineno,
                        )
                    )
                elif isinstance(statement, ast.AnnAssign):
                    target = statement.target
                    if not isinstance(target, ast.Name) or not _is_public(target.id):
                        continue
                    if not contract or file.waived(statement.lineno):
                        continue
                    found.append(
                        Declaration(
                            name=target.id,
                            kind="field",
                            owner=node.name,
                            file=file.path,
                            lineno=statement.lineno,
                            validated=_has_field_call(statement),
                            has_default=statement.value is not None,
                            default_is_none=isinstance(statement.value, ast.Constant)
                            and statement.value.value is None,
                        )
                    )
                elif isinstance(statement, ast.Assign):
                    value = statement.value
                    if not isinstance(value, ast.Constant) or not isinstance(
                        value.value, str
                    ):
                        continue
                    if not _WIRE_KEY_VALUE.fullmatch(value.value):
                        continue
                    if file.waived(statement.lineno):
                        continue
                    for target in statement.targets:
                        if isinstance(target, ast.Name) and target.id.isupper():
                            found.append(
                                Declaration(
                                    name=value.value,
                                    kind="wire_key",
                                    owner=node.name,
                                    file=file.path,
                                    lineno=statement.lineno,
                                    attr=target.id,
                                )
                            )
    return found


# --------------------------------------------------------------------------
# Detector 1: test-only
# --------------------------------------------------------------------------


class ProducerIndex:
    """Where each contract-field name is *written*, as opposed to read.

    Three write forms, all of them real in this service: a constructor keyword
    (``Record(expires_at=...)``), an attribute assignment (``row.expires_at =``),
    and a dict-literal key in a file that also names the owning class (how a
    file-native adapter builds a row before validating it).
    """

    def __init__(self) -> None:
        self.by_class: dict[tuple[str, str], set[Path]] = {}
        self.loose: dict[str, set[Path]] = {}
        #: Writes that only copy the field off another record
        #: (``expires_at=approval.expires_at``). Tracked rather than discarded
        #: because their *presence* is the evidence that separates the audit's
        #: finding from ordinary quiet defaulting: a field nobody ever passes is
        #: simply unset, while a field passed only ever by copy is a value the
        #: whole service moves around and no code originates.
        self.copied: dict[tuple[str, str], set[Path]] = {}
        #: Classes seen built by keyword — the only ones whose field writes this
        #: index can claim to have observed at all.
        self.keyword_built: set[str] = set()
        #: Classes seen built from a ``**splat``. Their field writes are hidden
        #: inside a dict this scan cannot open, so every field counts as written.
        self.splat_built: set[str] = set()

    @classmethod
    def build(cls, files: list[PyFile]) -> ProducerIndex:
        index = cls()
        for file in files:
            for node in ast.walk(file.tree):
                if isinstance(node, ast.Call):
                    owner = _callee_name(node.func)
                    for keyword in node.keywords:
                        if keyword.arg is None:
                            if owner:
                                index.splat_built.add(owner)
                            continue
                        if _reads_same_name(keyword.value, keyword.arg):
                            # ``expires_at=approval.expires_at`` — a pass-through
                            # forwards a value, it does not originate one. Every
                            # write of ``ApprovalRequestRecord.expires_at`` in
                            # this service is one of these, which is exactly how
                            # a field with a reader, a query and a sweeper ends
                            # up populated by nothing at all.
                            if owner:
                                index.copied.setdefault(
                                    (owner, keyword.arg), set()
                                ).add(file.path)
                            continue
                        if owner:
                            index.keyword_built.add(owner)
                            index.by_class.setdefault((owner, keyword.arg), set()).add(
                                file.path
                            )
                        else:
                            index.loose.setdefault(keyword.arg, set()).add(file.path)
                elif isinstance(node, ast.Assign):
                    for target in node.targets:
                        if isinstance(target, ast.Attribute):
                            index.loose.setdefault(target.attr, set()).add(file.path)
                    for key in _dict_keys(node.value):
                        index.loose.setdefault(key, set()).add(file.path)
        return index

    def writers(self, *, owner: str, name: str, tokens_of: dict[Path, frozenset[str]]):
        """Files that write ``owner.name``.

        A loose write (unattributable receiver) counts only when the same file
        also names the owning class — the cheapest available stand-in for type
        resolution, and one that errs toward calling a field live.
        """

        found = set(self.by_class.get((owner, name), ()))
        for path in self.loose.get(name, ()):
            if owner in tokens_of.get(path, frozenset()):
                found.add(path)
        return found


def _called_names(tree: ast.Module) -> frozenset[str]:
    """Every name this module *calls* or references as an attribute load.

    Attribute loads, not just calls, because a method is equally wired when it is
    handed on as a bound reference (``callbacks.append(self.flush)``).
    """

    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Load):
            found.add(node.attr)
        elif isinstance(node, ast.Call):
            name = _callee_name(node.func)
            if name:
                found.add(name)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            # ``getattr(store, "search_conversations")`` and the hasattr probes
            # in the worker's wiring modules name the method as a string.
            found.add(node.value)
    return frozenset(found)


def _callee_name(func: ast.expr) -> str:
    """``Record(...)`` / ``mod.Record(...)`` / ``Record.model_construct(...)``."""

    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        if func.attr in {"model_construct", "model_validate", "construct"}:
            return _callee_name(func.value)
        return func.attr
    return ""


def _reads_same_name(value: ast.expr, name: str) -> bool:
    """``name=other.name`` — copied off another instance, so it originates nothing.

    **Only the attribute form.** ``expires_at=approval.expires_at`` reads the field
    off an existing record and moves it along; if every write in the service looks
    like that, the value entered the system nowhere, which is exactly the
    ``ApprovalRequestRecord.expires_at`` finding.

    ``name=name`` is deliberately NOT a pass-through, and the distinction is the
    single highest-leverage line in this module. A bare local is the *normal*
    spelling of a real write — it is a parameter the caller computed, so the
    origination happened one frame up. Counting it as a pass-through made
    ``run_id``, ``org_id`` and ``user_id`` report as "written by nothing", i.e. it
    turned the most-written fields in the codebase into the strongest findings.
    That is a false positive of the kind that gets a gate deleted in its first
    week, so the rule stops at the form that provably reads a field off a record.
    """

    return isinstance(value, ast.Attribute) and value.attr == name


def collect_key_position_constants(src: list[PyFile]) -> set[tuple[str, str]]:
    """``(Owner, ATTR)`` for every constant this service uses as a *payload key*.

    The discriminator the wire-key detector cannot work without. Both of these
    are spelled ``UPPER = "lower_snake"`` inside a bare constants class, so no
    amount of squinting at the declaration tells them apart::

        class _Fields:  GRANT_OPTIONS = "grant_options"   # a key in a payload
        class EventType: FINAL_RESPONSE = "final_response" # a value of a field

    They differ in how the *client* consumes them, and only one of the two is
    checkable from TypeScript. A key is read as a property (``payload.x``), which
    is exactly what this detector looks for; an enum value is compared against
    (``status === "final_response"``), which no property-read scan can see. So
    every enum value in the service looked unread, and 272 findings — the great
    majority of them — were the detector reporting its own blind spot.

    Python settles it. A payload key reaches the wire as a dict key, a subscript,
    or the argument of ``.get`` / ``.pop``; an enum value never does. Position of
    use is a fact about the code, so unlike a class-name allowlist (``_Fields``,
    ``Field``, ``Fields``, …) it cannot go stale as new constants classes appear.
    """

    found: set[tuple[str, str]] = set()

    def record(node: ast.expr) -> None:
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            found.add((node.value.id, node.attr))

    for file in src:
        for node in ast.walk(file.tree):
            if isinstance(node, ast.Dict):
                for key in node.keys:
                    if key is not None:
                        record(key)
            elif isinstance(node, ast.Subscript):
                record(node.slice)
            elif isinstance(node, ast.Call):
                func = node.func
                if (
                    isinstance(func, ast.Attribute)
                    and func.attr in {"get", "pop", "setdefault"}
                    and node.args
                ):
                    record(node.args[0])
    return found


def _dict_keys(value: ast.expr | None) -> list[str]:
    if not isinstance(value, ast.Dict):
        return []
    return [
        key.value
        for key in value.keys
        if isinstance(key, ast.Constant) and isinstance(key.value, str)
    ]


def find_test_only(
    *,
    declarations: list[Declaration],
    src: list[PyFile],
    tests: list[PyFile],
    ts_reads: set[str],
    ts_mentions: set[str],
) -> list[Finding]:
    """Detector 1, dispatched per symbol kind (see the module docstring)."""

    src_tokens = {file.path: file.tokens for file in src}
    test_tokens = {file.path: file.tokens for file in tests}
    intra = {file.path: _called_names(file.tree) for file in src}
    producers = ProducerIndex.build(src)
    test_producers = ProducerIndex.build(tests)
    key_positions = collect_key_position_constants(src)

    fields_per_owner: dict[str, int] = {}
    for declaration in declarations:
        if declaration.kind == "field":
            fields_per_owner[declaration.owner] = (
                fields_per_owner.get(declaration.owner, 0) + 1
            )

    findings: list[Finding] = []
    field_candidates: list[tuple[Declaration, Finding]] = []
    for declaration in declarations:
        if declaration.kind == "callable":
            finding = _callable_finding(declaration, src_tokens, test_tokens, intra)
        elif declaration.kind == "field":
            candidate = _field_finding(
                declaration, producers, test_producers, src_tokens, test_tokens
            )
            if candidate is not None:
                field_candidates.append((declaration, candidate))
            continue
        else:
            if (declaration.owner, declaration.attr) not in key_positions:
                # Not used as a payload key anywhere in the service, so the
                # "no TS property read" evidence means nothing. See
                # ``collect_key_position_constants``.
                continue
            finding = _wire_key_finding(declaration, ts_reads, ts_mentions)
        if finding is not None:
            findings.append(finding)
    findings.extend(_surviving_field_findings(field_candidates, fields_per_owner))
    return findings


#: Above this share of a contract's fields being copy-only, the contract is a
#: projection rather than a contract with one dead field. Half is not a tuned
#: number — it is the point at which "the exception" stops being the right word.
_PROJECTION_SHARE = 0.5


def _surviving_field_findings(
    candidates: list[tuple[Declaration, Finding]],
    fields_per_owner: dict[str, int],
) -> list[Finding]:
    """Drop copy-only fields belonging to *projection* contracts.

    The single hardest false positive in this gate, and worth stating plainly
    because the two cases are genuinely hard to tell apart::

        ApprovalRequestRecord(run_id=..., status=PENDING, expires_at=rec.expires_at)
        VerifiedTaskPolicySignals(run_id=run.run_id, org_id=run.org_id, ...)

    Both contain a field written only as a copy of itself. In the first the field
    is *the exception* — its siblings are computed, so the record is genuinely
    produced and ``expires_at`` alone arrives from nowhere. In the second every
    field is a copy, because the contract's whole job is to project an existing
    record; the values were originated on the source, one hop out of view.

    Per-field analysis cannot separate these — the two writes are the same shape.
    The *class* separates them, which is why the verdict is taken there: a lone
    copy-only field among populated siblings is an anomaly worth reporting, and a
    struct that is nothing but copy-only fields is a projection working
    correctly. Proving the second properly means resolving ``run`` to its type
    and asking whether ``RunRecord.run_id`` is originated — real type inference,
    and far more machinery than this gate should carry. Silence is the right
    error direction, so the cheap structural proxy stands in for it.
    """

    by_owner: dict[str, list[Finding]] = {}
    for declaration, finding in candidates:
        by_owner.setdefault(declaration.owner, []).append(finding)

    surviving: list[Finding] = []
    for owner, found in by_owner.items():
        total = fields_per_owner.get(owner, len(found))
        if total and len(found) / total > _PROJECTION_SHARE:
            continue
        surviving.extend(found)
    return surviving


def _callable_finding(
    declaration: Declaration,
    src_tokens: dict[Path, frozenset[str]],
    test_tokens: dict[Path, frozenset[str]],
    intra_module_calls: dict[Path, frozenset[str]],
) -> Finding | None:
    # A method called from elsewhere in its own module is wired — the module is
    # reachable, so the call is too. Only counting *cross-file* references
    # reported 3,800 ordinary private-by-convention helpers as dark, which is
    # the population size at which a gate stops being read.
    if declaration.name in intra_module_calls.get(declaration.file, frozenset()):
        return None
    callers = [
        path
        for path, tokens in src_tokens.items()
        if path != declaration.file and declaration.name in tokens
    ]
    if callers:
        return None
    exercising = [
        path for path, tokens in test_tokens.items() if declaration.name in tokens
    ]
    if not exercising:
        # No reference anywhere: plain dead code, which the orphan ratchet and
        # the vulture pass already own. Not this gate's population.
        return None
    return Finding(
        key=f"{_module_of(declaration.file)}:{declaration.owner}.{declaration.name}",
        detector="test-only",
        file=declaration.file,
        lineno=declaration.lineno,
        reason=(
            f"callable is referenced by {len(exercising)} test file(s) and by no "
            "other src module — nothing in the product calls it"
        ),
    )


def _field_finding(
    declaration: Declaration,
    producers: ProducerIndex,
    test_producers: ProducerIndex,
    src_tokens: dict[Path, frozenset[str]],
    test_tokens: dict[Path, frozenset[str]],
) -> Finding | None:
    # Three conditions decide whether a *verdict is even available*, and each of
    # them removed roughly an order of magnitude of noise:
    #
    # 1. A field with no default cannot be dark — omitting it raises at
    #    construction, so every instance that exists proves a producer.
    # 2. A class this scan never sees built by keyword tells us nothing: its
    #    producers are wherever we could not look.
    # 3. A class built from a ``**splat`` hides its field writes in a dict, so
    #    every field must count as written.
    if not declaration.has_default:
        return None
    # 0. Only ``x: T | None = None``. This is the narrowest of the conditions and
    #    the one that decides whether the detector is worth reading. A field with
    #    a *working* default that nobody overrides is a shrug —
    #    ``ModelCatalogItem.supports_tools`` defaults to something usable and the
    #    system behaves. A field defaulting to ``None`` that nobody populates is
    #    the audit's finding, because ``None`` is indistinguishable from "not due
    #    yet": the approval sweeper queries ``expires_at``, reads ``None``
    #    forever, expires nothing, and reports success while doing so. The
    #    silence is the defect, and only the ``None`` default produces it.
    if not declaration.default_is_none:
        return None
    if declaration.owner not in producers.keyword_built:
        return None
    if declaration.owner in producers.splat_built:
        return None
    # 4. The field must be *passed* somewhere, by copy, for a verdict to mean
    #    anything. Without this the detector reports every field that merely sits
    #    on its default — ``schema_version``, ``display_name``, a limits struct's
    #    ceilings — which is 900-odd findings of "nobody overrides this", a
    #    completely different and entirely uninteresting fact. The audit's shape
    #    is narrower and much more damning: the value *is* threaded through
    #    constructors, so it looks alive at every hop, and no hop ever produces
    #    it. That is what makes a sweeper read a field that is forever ``None``.
    if not producers.copied.get((declaration.owner, declaration.name)):
        return None
    written = producers.writers(
        owner=declaration.owner, name=declaration.name, tokens_of=src_tokens
    )
    # Any originating write counts, *including one in the declaring module*.
    # Discounting the declaring file looks like hygiene ("a class vouching for
    # its own field proves nothing") and is simply wrong: plenty of contracts in
    # this service are constructed exactly once, in the module that defines them
    # — ``VerifiedTaskPolicySignals`` is built only at run_control.py:478 — so
    # the exclusion reported every field of every such class as populated by
    # nothing. A write is a write wherever it is spelled.
    if written:
        return None
    readers = [
        path
        for path, tokens in src_tokens.items()
        if path != declaration.file and declaration.name in tokens
    ]
    if not readers:
        # Nothing writes it and nothing reads it: unused, not mis-wired.
        return None
    by_tests = test_producers.writers(
        owner=declaration.owner, name=declaration.name, tokens_of=test_tokens
    )
    return Finding(
        key=f"{_module_of(declaration.file)}:{declaration.owner}.{declaration.name}",
        detector="test-only",
        file=declaration.file,
        lineno=declaration.lineno,
        reason=(
            f"field is read by {len(readers)} src module(s) and threaded through "
            "constructors only as a copy of itself; the sole originating writes "
            f"are in {len(by_tests)} test file(s) — no product code populates it"
        ),
    )


def _wire_key_finding(
    declaration: Declaration, ts_reads: set[str], ts_mentions: set[str]
) -> Finding | None:
    if declaration.name in ts_reads:
        return None
    if declaration.name not in ts_mentions:
        # The client never mentions it at all — a server-internal key, not a
        # broken client contract. Biased toward silence, as everywhere here.
        return None
    return Finding(
        key=f"{_module_of(declaration.file)}:{declaration.owner}.{declaration.name}",
        detector="test-only",
        file=declaration.file,
        lineno=declaration.lineno,
        reason=(
            "wire key is emitted to the client, and the app tree mentions it "
            "only as a bare string (a strip list or a type member) — no code "
            "ever reads it off the payload"
        ),
    )


def collect_ts_usage(roots: tuple[Path, ...]) -> tuple[set[str], set[str]]:
    """``(genuine property reads, any mention)`` across the app tree.

    ``packages/api-types`` is excluded from *both*: it declares the payload
    shape and consumes nothing, so counting it either way would report every
    typed-but-unused key as wired.
    """

    reads: set[str] = set()
    mentions: set[str] = set()
    for path in _iter_files(roots, suffixes=_TS_SUFFIXES):
        if TS_DECLARATION_ROOT in path.parents:
            continue
        text = _read(path)
        if text is None:
            continue
        mentions.update(_IDENTIFIER.findall(text))
        if path.name.endswith((".test.ts", ".test.tsx", ".spec.ts", ".spec.tsx")):
            continue
        reads.update(_TS_DOT_READ.findall(text))
        reads.update(_TS_INDEX_READ.findall(text))
    return reads, mentions


# --------------------------------------------------------------------------
# Detector 2: backend-only
# --------------------------------------------------------------------------


def find_backend_only(src: list[PyFile]) -> list[Finding]:
    """A public store capability that exists on one backend and no port."""

    by_backend: dict[str, dict[str, tuple[Declaration, PyFile]]] = {}
    protocol_methods: set[str] = set()

    for file in src:
        backend = _backend_of(file.path)
        for node in ast.walk(file.tree):
            if not isinstance(node, ast.ClassDef):
                continue
            is_protocol = any(
                _callee_name(base) == "Protocol" or _callee_name(base) == "ABC"
                for base in node.bases
            )
            for statement in node.body:
                if not isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                name = statement.name
                if is_protocol:
                    protocol_methods.add(name)
                    continue
                if backend is None or not _is_public(name):
                    continue
                if name.endswith("_locked") or name in _FRAMEWORK_HOOKS:
                    # Lock-discipline internals, public only by spelling.
                    continue
                if file.waived(statement.lineno):
                    continue
                by_backend.setdefault(backend, {}).setdefault(
                    name,
                    (
                        Declaration(
                            name=name,
                            kind="callable",
                            owner=node.name,
                            file=file.path,
                            lineno=statement.lineno,
                        ),
                        file,
                    ),
                )

    findings: list[Finding] = []
    backends = sorted(by_backend)
    for backend in backends:
        siblings = [other for other in backends if other != backend]
        # Every src file that is not part of this backend's own package. A
        # capability named by one of these has a caller that reached it somehow,
        # so it is not dark however lopsided its implementation is.
        outsiders = [file for file in src if _backend_of(file.path) != backend]
        for name, (declaration, _file) in sorted(by_backend[backend].items()):
            if name in protocol_methods:
                continue
            if any(name in by_backend[other] for other in siblings):
                continue
            # The second, decisive condition — and the one that turns this from a
            # style complaint into the audit's actual finding. "Implemented on one
            # backend" is by itself unremarkable: a file-native store legitimately
            # owns dozens of methods its in-memory sibling has no reason to have,
            # and every one of them is *called*. What makes FTS5 search and the
            # export/import archive dark is that their only callers live inside
            # the adapter that defines them. Requiring both cuts this detector's
            # population by an order of magnitude and leaves the real shape.
            if any(name in file.tokens for file in outsiders):
                continue
            findings.append(
                Finding(
                    key=f"{_module_of(declaration.file)}:"
                    f"{declaration.owner}.{declaration.name}",
                    detector="backend-only",
                    file=declaration.file,
                    lineno=declaration.lineno,
                    reason=(
                        f"capability exists only on the {backend!r} store backend, "
                        f"on no sibling backend ({', '.join(siblings)}) and on no "
                        "Protocol port, and no src module outside "
                        f"runtime_adapters/{backend}/ names it — nothing typed "
                        "against the port can reach it, so every other backend "
                        "silently degrades"
                    ),
                )
            )
    return findings


def _backend_of(path: Path) -> str | None:
    """``runtime_adapters/<backend>/...`` → ``<backend>``, else ``None``."""

    try:
        rel = path.relative_to(ADAPTER_ROOT)
    except ValueError:
        return None
    if len(rel.parts) < 2:
        return None
    return rel.parts[0]


# --------------------------------------------------------------------------
# Detector 3: prompt-only
# --------------------------------------------------------------------------


class _ReadClassifier(ast.NodeVisitor):
    """Classify every read of a tracked name in one file as prompt or real.

    A read is a *prompt* read when it is lexically inside an f-string, or when it
    is bound to a local that an f-string in the same function interpolates —
    which is the ``allowed_tools = tuple(getattr(skill, "allowed_tools", ()))``
    then ``f"...{','.join(allowed_tools)}"`` shape at ``execution/factory.py``.

    A read whose value goes straight back out under the *same* name is a
    pass-through and is not counted at all; see the module docstring.
    """

    def __init__(self, *, names: frozenset[str]) -> None:
        self._names = names
        self.prompt: set[str] = set()
        self.real: set[str] = set()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._scan_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._scan_function(node)

    def _scan_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        interpolated = _names_in_fstrings(node)
        for read, name in _tracked_reads(node, self._names):
            if _is_pass_through(node, read, name):
                continue
            if _inside_fstring(node, read) or name in interpolated:
                self.prompt.add(name)
            else:
                self.real.add(name)
        self.generic_visit(node)


def _names_in_fstrings(scope: ast.AST) -> set[str]:
    """Every local name any f-string in this scope interpolates."""

    found: set[str] = set()
    for node in ast.walk(scope):
        if not isinstance(node, ast.JoinedStr):
            continue
        for inner in ast.walk(node):
            if isinstance(inner, ast.Name):
                found.add(inner.id)
            elif isinstance(inner, ast.Attribute):
                found.add(inner.attr)
    # A local bound from a tracked read keeps the read's name only when the
    # binding is spelled the same way, which is the case that matters here.
    return found


def _tracked_reads(scope: ast.AST, names: frozenset[str]) -> list[tuple[ast.AST, str]]:
    """``(node, name)`` for every attribute read or ``getattr`` of a tracked name."""

    found: list[tuple[ast.AST, str]] = []
    for node in ast.walk(scope):
        if isinstance(node, ast.Attribute) and node.attr in names:
            if isinstance(node.ctx, ast.Load):
                found.append((node, node.attr))
        elif isinstance(node, ast.Call) and _callee_name(node.func) == "getattr":
            if len(node.args) >= 2:
                target = node.args[1]
                if (
                    isinstance(target, ast.Constant)
                    and isinstance(target.value, str)
                    and target.value in names
                ):
                    found.append((node, target.value))
    return found


def _inside_fstring(scope: ast.AST, needle: ast.AST) -> bool:
    for node in ast.walk(scope):
        if isinstance(node, ast.JoinedStr) and any(
            inner is needle for inner in ast.walk(node)
        ):
            return True
    return False


def _is_pass_through(scope: ast.AST, needle: ast.AST, name: str) -> bool:
    """``name=<expr containing this read>`` — the value only moves on."""

    for node in ast.walk(scope):
        if not isinstance(node, ast.Call):
            continue
        for keyword in node.keywords:
            if keyword.arg != name:
                continue
            if any(inner is needle for inner in ast.walk(keyword.value)):
                return True
    return False


def _visible_packages(file: PyFile) -> frozenset[str]:
    """Every package this module imports from, plus its own.

    Each imported module contributes all of its dotted prefixes, so a file that
    does ``from agent_runtime.capabilities.skills.middleware import X`` is
    recorded as able to see ``agent_runtime.capabilities.skills`` — the package
    :func:`_package_of` reports for that contract's declaring file.
    """

    found: set[str] = {_package_of(file.path)}
    for node in ast.walk(file.tree):
        module = ""
        if isinstance(node, ast.ImportFrom) and node.module:
            module = node.module
        elif isinstance(node, ast.Import):
            for alias in node.names:
                parts = alias.name.split(".")
                found.update(
                    ".".join(parts[: index + 1]) for index in range(len(parts))
                )
            continue
        if not module:
            continue
        parts = module.split(".")
        found.update(".".join(parts[: index + 1]) for index in range(len(parts)))
    return frozenset(found)


def find_prompt_only(
    *, declarations: list[Declaration], src: list[PyFile]
) -> list[Finding]:
    """Detector 3, keyed on ``(package, field)`` — see the docstring on why."""

    tracked: dict[str, list[Declaration]] = {}
    for declaration in declarations:
        if declaration.kind == "field" and declaration.validated:
            tracked.setdefault(declaration.name, []).append(declaration)
    if not tracked:
        return []

    names = frozenset(tracked)
    #: name -> [(reader package, packages that reader can see)]
    prompt_reads: dict[str, list[tuple[str, frozenset[str]]]] = {}
    real_reads: dict[str, list[tuple[str, frozenset[str]]]] = {}
    for file in src:
        if not (names & file.tokens):
            continue
        classifier = _ReadClassifier(names=names)
        classifier.visit(file.tree)
        package = _package_of(file.path)
        reach = _visible_packages(file)
        for name in classifier.prompt:
            prompt_reads.setdefault(name, []).append((package, reach))
        for name in classifier.real:
            real_reads.setdefault(name, []).append((package, reach))

    findings: list[Finding] = []
    seen: set[str] = set()
    for name, declared in sorted(tracked.items()):
        for declaration in declared:
            package = _package_of(declaration.file)

            def sees(
                reader: str, reach: frozenset[str], *, owner: str = package
            ) -> bool:
                """Could this reader be reading *this* declaration's field?

                Six classes across three packages declare a field called
                ``allowed_tools``, so a name-keyed index cannot tell which one a
                read refers to — and gets it exactly backwards here. The subagent
                handoff genuinely narrows tools, which made the *name* look
                enforced and hid the skills manifest field, whose only consumer
                is a prompt f-string. Reversed, the gate flagged the one field
                that is enforced and stayed silent on the one that is not.

                The import graph settles it without type inference: ``handoff.py``
                never imports ``capabilities.skills``, so its real read cannot be
                of the skills field; ``factory.py`` imports both, so its prompt
                read counts against both. A file can only read a field of a
                contract it can name.
                """

                return reader == owner or owner in reach

            outside_prompt = {
                reader
                for reader, reach in prompt_reads.get(name, [])
                if sees(reader, reach) and reader != package
            }
            # A real read *anywhere that can see this field* means enforced —
            # including inside the declaring package. Excluding own-package reads
            # (on the theory that a package consuming its own field proves
            # nothing downstream) discards precisely the evidence that
            # ``SubagentTask.allowed_tools`` is enforced, since the enforcing
            # code is its package-mate ``handoff.py``. Local enforcement is
            # enforcement.
            outside_real = any(
                sees(reader, reach) for reader, reach in real_reads.get(name, [])
            )
            if not outside_prompt or outside_real:
                continue
            key = f"{package}:{declaration.owner}.{name}"
            if key in seen:
                continue
            seen.add(key)
            findings.append(
                Finding(
                    key=key,
                    detector="prompt-only",
                    file=declaration.file,
                    lineno=declaration.lineno,
                    reason=(
                        "field is parsed, typed and validated, and every read "
                        f"outside {package!r} interpolates it into a prompt "
                        f"({', '.join(sorted(outside_prompt))}) — it is advice to "
                        "the model, enforced by nothing"
                    ),
                )
            )
    return findings


# --------------------------------------------------------------------------
# Ratchet
# --------------------------------------------------------------------------


class Baseline:
    """The recorded, reasoned population of already-dark symbols."""

    def __init__(self, *, entries: dict[str, str], path: Path) -> None:
        self.entries = entries
        self.path = path

    @classmethod
    def load(cls, path: Path = BASELINE_PATH) -> Baseline:
        entries: dict[str, str] = {}
        text = _read(path)
        if text is None:
            return cls(entries=entries, path=path)
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            key, separator, reason = stripped.partition(BASELINE_SEPARATOR.strip())
            entries[key.strip()] = reason.strip() if separator else ""
        return cls(entries=entries, path=path)

    def __contains__(self, key: str) -> bool:
        return key in self.entries


def _display(path: Path) -> str:
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def collect_findings(
    *,
    src_roots: tuple[Path, ...] = (SRC_ROOT,),
    test_roots: tuple[Path, ...] = (TEST_ROOT,),
    app_roots: tuple[Path, ...] = APP_ROOTS,
) -> list[Finding]:
    """Every dark symbol the three detectors can see, sorted by ratchet key."""

    src = load_python(src_roots)
    tests = load_python(test_roots)
    ts_reads, ts_mentions = collect_ts_usage(app_roots)

    declarations = collect_declarations(src)
    findings = find_test_only(
        declarations=declarations,
        src=src,
        tests=tests,
        ts_reads=ts_reads,
        ts_mentions=ts_mentions,
    )
    findings.extend(find_backend_only(src))
    findings.extend(find_prompt_only(declarations=declarations, src=src))

    deduped: dict[str, Finding] = {}
    for finding in findings:
        deduped.setdefault(finding.key, finding)
    return sorted(deduped.values(), key=lambda finding: finding.key)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="check_dark_wiring")
    parser.add_argument(
        "--baseline",
        type=Path,
        default=BASELINE_PATH,
        help="Baseline file of already-dark symbols.",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="Print every finding with its ratchet verdict, then exit 0.",
    )
    args = parser.parse_args(argv)

    findings = collect_findings()
    baseline = Baseline.load(args.baseline)
    live = {finding.key for finding in findings}

    if args.list:
        for finding in findings:
            verdict = "baselined" if finding.key in baseline else "NEW"
            sys.stdout.write(f"{verdict:10s} {finding.render()}\n")
        sys.stdout.write(f"\n{len(findings)} finding(s)\n")
        return 0

    new = [finding for finding in findings if finding.key not in baseline]
    stale = sorted(key for key in baseline.entries if key not in live)

    if not new and not stale:
        sys.stdout.write(
            "OK: dark wiring "
            f"({len(findings)} sub-module symbol(s) dark and baselined, 0 new)\n"
        )
        return 0

    if new:
        sys.stderr.write(
            "FAIL: new symbol(s) that are built but not wired to any product caller\n"
        )
        for finding in new:
            sys.stderr.write(
                f"  {finding.render()}\n"
                "      Wire it to a real consumer, delete it, or — if a consumer "
                "exists that this scan cannot see — add a reasoned line to "
                f"{_display(baseline.path)}:\n"
                f"      {finding.key}{BASELINE_SEPARATOR}<why this is wired in "
                "reality>\n"
            )
    if stale:
        sys.stderr.write(
            f"FAIL: stale line(s) in {_display(baseline.path)} — these symbols "
            "now have a consumer (or no longer exist). Delete the line(s):\n"
        )
        for key in stale:
            sys.stderr.write(f"  {key}\n")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
