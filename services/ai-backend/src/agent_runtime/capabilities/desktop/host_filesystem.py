"""Real host filesystem access, composed from Deep Agents' own primitives.

This replaces a hand-rolled stack — a brokered read backend, a `claims_path`
predicate, a `guarded_default` wrapper and a bespoke grant gate — with the
mechanisms deepagents already ships. The hand-rolled version failed in the
packaged app for a reason worth keeping in view: a host-absolute path is not a
prefix of anything, so `CompositeBackend` routing could never carry it, and it
fell through to the `StateBackend` default, which answers EVERY path with
success and nothing. `ls ~/Downloads` came back empty with a green tick.

Two deepagents facts make the honest version simple:

1. `FilesystemBackend(virtual_mode=False)` uses absolute paths AS-IS. Its own
   docstring flags that as "no security" — and that is precisely right: this
   object is the raw capability, not the boundary. It can genuinely read
   `/Users/<name>/Downloads`.
2. `FilesystemPermission` rules are enforced in the TOOL layer, before any
   backend runs, and a rule with `mode="interrupt"` auto-installs the same
   `HumanInTheLoopMiddleware` that already gates MCP tools. So "ask the user
   before touching this folder" is a rule, not a bespoke gate, and it surfaces
   through the approval path that already works in the product.

The boundary is therefore the RULE SET, not the backend. That inversion is the
whole design, and it hangs on one detail of deepagents' matcher:

    `_check_fs_permission` returns "allow" when NO rule matches.

Unmatched-means-allow is a footgun for us, so the rule list always ends with a
catch-all `deny`. First match wins, so the ordering below is load-bearing:

    1. the agent's own virtual namespaces      -> allow      (memories, drafts, ...)
    2. the agent's own scratch on disk         -> allow      ($COPILOT_HOME/.tmp)
    3. a granted host root, READ               -> allow
    3. a granted host root, WRITE              -> allow      under Bypass
                                               -> interrupt  under Manual
    4. every other read                        -> interrupt  (ask the user)
    5. every other write                       -> deny       (the floor)

Rule 4 is what makes an ungranted path ASK instead of lying, and rule 5 is what
makes a future wiring mistake fail closed instead of silently succeeding. A run
with zero grants still gets rules 1, 4 and 5 — so "you have granted nothing" is
answered by a consent request, never by an empty listing.

Where the bypass pill lands
---------------------------
Rule 3's write half is the ONLY thing the filesystem-bypass decision moves, and
that is deliberate: bypass removes the PAUSE, never widens the SET. Rules 4 and
5 are identical in both modes, so an ungranted write is refused rather than
asked in every posture — one click on a generic card can therefore never become
write access to the machine. This is
:class:`~agent_runtime.execution.filesystem_bypass.FilesystemBypassBound`'s
invariant (granted ∧ writable), now enforced where host writes actually happen.

Before this, the decision was consumed only by the ``/workspace/`` staged lane
(``WorkspaceGatewayServices``), which needs a C2 attestation an unpackaged build
cannot produce — so on the lane that does land bytes, Manual and Bypass were
byte-identical and the composer pill decided nothing observable.

The one thing these patterns CANNOT say
---------------------------------------
deepagents matches with `wcmatch` under `BRACE | GLOBSTAR` and no `DOTGLOB`, so
`*`/`**` never match a segment beginning with a dot: `/**` does not match
`~/.ssh/id_rsa`, and unmatched means allow. No pattern fixes it (a literal dot
covers one level; nothing covers `/a/.b/.c`), so the catch-all rules are total
over VISIBLE absolute paths only. The residue is closed one layer down, by
`host_floor.HostFilesystemFloor`, which is composed around the real filesystem
backend by `factory._host_default_backend`.

Scratch memory
--------------
Because rule 4 exists, the agent needs a real place to keep working files.
That place is `$COPILOT_HOME/.tmp`, supplied by
`agent_scratch.AgentScratchRoot` — NOT `.copilot` inside a granted root, which
this module used to site (PRD-FS-12 D7 dropped it). Writing into the folder the
user attached was wrong twice over: it put agent bookkeeping inside the user's
own content, and it made a read-only grant a special case that had to be
re-implemented in `host_floor` because `.copilot` is a hidden segment the
matcher cannot see. The scratch is now somewhere that is ours, so no grant's
writability affects it and nothing is ever written into a shared folder.

The `.copilot` lane got one thing right on its way out, and it is worth keeping
in view because the same trap will reappear at the new location: a rule that
ALLOWS a directory does not CREATE it. deepagents' `FilesystemBackend.write`
runs `parent.mkdir(parents=True)`, so a write conjures the directory as a side
effect and a read does not — `ls` of a permitted-but-absent scratch answers
`path_not_found`, which is this subsystem's own defect turned inward: a real
place, reported absent. It went unnoticed because every scratch test ran
`mkdir(parents=True)` in its fixture first, proving the PERMISSION and never the
DIRECTORY. `$COPILOT_HOME/.tmp` does not repeat it: `AgentScratchRoot.ensure`
and `runtime_worker.agent_scratch_wiring` materialise the tree before the graph
runs, and `test_agent_scratch` drives creation rather than assuming it.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Final

from agent_runtime.capabilities.desktop.host_path import HostPathClassifier
from agent_runtime.execution.filesystem_bypass import (
    MANUAL_FILESYSTEM_BYPASS,
    FilesystemBypassDecision,
)

if TYPE_CHECKING:
    from agent_runtime.capabilities.desktop.agent_scratch import AgentScratchRoot

#: Prefixes the agent owns inside its own virtual filesystem. These are routed
#: by `CompositeBackend` to real backends (memory, drafts, subagent artifacts),
#: so they must be allowed BEFORE the catch-all interrupt rule or every ordinary
#: memory read would prompt the user.
#:
#: Membership is not decorative — every entry here is an unconditional read AND
#: write allow, so a prefix that is NOT actually routed away is a hole straight
#: through to the host. `/tmp/` was exactly that: it is not a `CompositeBackend`
#: route and `HostPathClassifier` classifies it as a HOST path, so rule 1 was
#: granting blanket read+write over the machine's real `/tmp` — the only
#: unqualified host-write allow in a rule set whose whole point is that host
#: writes go through the staged, ledgered lane. Keep this list to prefixes the
#: composite genuinely owns.
VIRTUAL_NAMESPACES: Final[tuple[str, ...]] = (
    "/memories/",
    "/policies/",
    "/skills/",
    "/drafts/",
    "/subagents/",
    "/large_tool_results/",
    "/workspace/",
)

#: Deep Agents filesystem operations we write rules for. Kept explicit rather
#: than imported so a deepagents change surfaces as a failing test here rather
#: than as a silently narrower rule set.
_READ: Final = "read"
_WRITE: Final = "write"
_ALL_OPERATIONS: Final[tuple[str, ...]] = (_READ, _WRITE)


class _Mode:
    ALLOW: Final = "allow"
    DENY: Final = "deny"
    INTERRUPT: Final = "interrupt"


@dataclass(frozen=True)
class GrantedRoot:
    """One host folder the user has actually granted.

    ``path`` is a real host-absolute path. It is used to build allow rules; it
    is never handed to the model as a value, and — since PRD-FS-12 D7 — nothing
    is ever written inside it by the agent.
    """

    path: str
    writable: bool = True

    def __post_init__(self) -> None:
        if not self.path.startswith("/"):
            # Windows roots arrive already normalised to POSIX form by the
            # caller; a bare drive letter here means the caller skipped that
            # step, and a silent pass would produce a rule that matches nothing.
            raise ValueError(f"granted root must be POSIX-absolute: {self.path!r}")
        if ".." in PurePosixPath(self.path).parts:
            raise ValueError(f"granted root must not contain '..': {self.path!r}")

    @classmethod
    def from_host_path(cls, path: str, *, writable: bool = True) -> GrantedRoot:
        r"""Build a root from a host path in EITHER platform's grammar.

        `__post_init__` demands the POSIX-shaped canonical spelling because that
        is what a tool call is rewritten to before the rules are matched — a
        rule written as `C:\Users\p\Downloads` would match nothing the tool layer
        ever produces. This is the one seam that performs that conversion, so a
        grant lane never has to know the encoding.

        A path the classifier will not resolve to a concrete folder — a
        traversal, a device namespace, a drive-relative remainder — raises
        rather than becoming an `allow` rule. A grant is the widest thing this
        rule set can express, so an unresolvable one degrades to "that folder
        still asks", never to a rule matching more than the user agreed to.
        """

        classified = HostPathClassifier.classify(path)
        if not classified.is_host:
            raise ValueError(f"granted root is not a host folder: {path!r}")
        return cls(path=classified.canonical, writable=writable)

    def glob(self) -> str:
        """Match the root itself and everything beneath it."""

        return str(PurePosixPath(self.path) / "**")


class HostFilesystemRules:
    """Builds the ordered `FilesystemPermission` list for one run.

    Emitted as plain dicts so this module stays importable without deepagents
    (the runtime factory converts them at build time, where the dependency
    already exists). The ORDER of the returned tuple is the security property —
    deepagents returns the first matching rule's mode.
    """

    @staticmethod
    def build(
        roots: tuple[GrantedRoot, ...],
        *,
        scratch: AgentScratchRoot | None = None,
        bypass: FilesystemBypassDecision = MANUAL_FILESYSTEM_BYPASS,
    ) -> tuple[dict[str, object], ...]:
        """The ordered rules for one run, under that run's sealed bypass mode.

        ``bypass`` defaults to Manual so a caller that forgets to thread the
        run's decision gets the ASKING posture, never the silent one. Every
        wrong answer here should cost a prompt, not an unannounced write.
        """

        rules: list[dict[str, object]] = []

        # 1. The agent's own namespaces. Without these, an ordinary
        #    `read_file("/memories/...")` would hit the catch-all interrupt and
        #    prompt the user about the agent's own bookkeeping.
        #
        #    BOTH forms, and the bare one is not decoration. `/workspace/**`
        #    matches what is INSIDE the namespace and not the namespace itself,
        #    so `ls("/workspace")` — the obvious first move to find out which
        #    folders are attached — fell through to rule 4 and asked the user to
        #    approve reading `/workspace`. Measured: all seven roots interrupted
        #    while every path under them allowed. That one gap produced both of
        #    the live symptoms — an attached folder that "still asks", and a
        #    consent card naming a virtual mount the user has never heard of.
        rules.append(
            {
                "operations": list(_ALL_OPERATIONS),
                "paths": [
                    path
                    for prefix in VIRTUAL_NAMESPACES
                    for path in (prefix.rstrip("/"), f"{prefix}**")
                ],
                "mode": _Mode.ALLOW,
            }
        )

        # 2. The agent's own scratch on disk — ``$COPILOT_HOME/.tmp`` and
        #    nothing above it. This is the ONE host location the agent may write
        #    directly, because it holds the agent's own working files rather
        #    than the user's content, and it lives somewhere that is OURS: no
        #    grant is required for it, and a read-only grant is no longer a
        #    special case (PRD-FS-12 D7).
        #
        #    The rule comes from ``AgentScratchRoot`` rather than being spelled
        #    here, because ``.tmp`` is a dotted segment and a glob cannot see it
        #    — see that module's header for the trap and the pinning test. It
        #    must precede rule 5, which denies every other host write.
        if scratch is not None:
            rules.extend(scratch.allow_rules())

        # 3. Granted roots. A grant carries its own MODE, and this rule is
        #    where that mode finally means something again.
        #
        #    It used to be READ-only regardless: `writable` was carried on every
        #    grant and decided nothing, because D7 routed all host writes through
        #    the staged overlay. That lane has never once run on a desktop
        #    install — it needs a C2 attestation only a signed, packaged build
        #    can produce, and this app ships as an unpackaged CLI payload. So
        #    the practical effect of "writes are audited" was "writes never
        #    happen", while `writable` sat in the contract looking meaningful.
        #
        #    The consent argument that motivated D7 is kept, and SPLIT across
        #    the two questions it was always conflating:
        #
        #      * WHICH folders may be written — answered once, at attach time,
        #        by `writable`. A read-only grant emits no write rule at all and
        #        falls through to rule 5's `deny`, so a write there is refused
        #        and is never even a question. Approving something the user
        #        already said no to is not a decision worth offering.
        #      * WHETHER EACH WRITE PAUSES — answered per run by the composer's
        #        bypass pill. Manual asks; Bypass proceeds.
        #
        #    Manual is the default, and the asking half is what makes the
        #    attach-time answer safe to give: saying "yes, writable" no longer
        #    means "and never tell me again", so the wide grant stops being the
        #    only way to get work done. Bypass then exists for the case that
        #    would otherwise produce thirty cards in one task, which is how
        #    consent prompts get clicked through unread.
        #
        #    READ and WRITE are separate rules because they now carry separate
        #    modes. Interleaving them per root is safe — deepagents matches on
        #    (operation, path), so a read rule can never answer a write.
        write_mode = _Mode.ALLOW if bypass.skips_approval_pause else _Mode.INTERRUPT
        for root in roots:
            rules.append(
                {
                    "operations": [_READ],
                    "paths": [root.path, root.glob()],
                    "mode": _Mode.ALLOW,
                }
            )
            if not root.writable:
                continue
            rules.append(
                {
                    "operations": [_WRITE],
                    "paths": [root.path, root.glob()],
                    "mode": write_mode,
                }
            )

        # 4. Every other READ asks. This is the rule that turns the original
        #    defect — a confident empty listing over a folder full of files —
        #    into a consent request, and it applies even when `roots` is empty,
        #    which is exactly the first-run case that was broken. Approving it
        #    yields a REAL listing, because the backend beneath is a real
        #    filesystem rather than agent memory.
        rules.append(
            {
                "operations": [_READ],
                "paths": ["/**"],
                "mode": _Mode.INTERRUPT,
            }
        )

        # 5. Every other WRITE is denied outright — NOT interrupted. This is the
        #    rule that keeps bypass a statement about PAUSING rather than about
        #    REACH, and it is identical in both modes.
        #
        #    The original argument for `deny` was that reads and writes share a
        #    consent card, so an `interrupt` could let "Allow reading /path?" be
        #    the click that overwrites that path. Rule 3 now does raise write
        #    interrupts, so that half is answered elsewhere and must stay
        #    answered: `_FilesystemApproval` renders a write as "Allow writing
        #    to <file>?" with `operation: "write"` and high risk, so the two
        #    cards are not interchangeable. A regression there would make Manual
        #    strictly MORE dangerous than Bypass, which is why that projection
        #    has its own tests.
        #
        #    What keeps THIS rule at `deny` is the other half: an ungranted path
        #    has no attach-time decision behind it, so a single click would be
        #    the entire authorization for a write anywhere on the machine. A
        #    write inside a granted writable root is the user's second answer
        #    about a folder they already chose; a write outside one would be
        #    their first and only. Those do not deserve the same control.
        #
        #    Together, rules 4 and 5 are total over every absolute path the
        #    MATCHER CAN SEE, so nothing visible is left to deepagents'
        #    unmatched-means-allow default. Hidden paths are not visible to it
        #    (no `DOTGLOB`), and no pattern makes them so; `HostFilesystemFloor`
        #    supplies those two verdicts instead. No separate terminal rule is
        #    possible here anyway: deepagents rejects a rule path that does not
        #    start with "/", so a bare `**` raises.
        rules.append(
            {
                "operations": [_WRITE],
                "paths": ["/**"],
                "mode": _Mode.DENY,
            }
        )
        return tuple(rules)


@dataclass(frozen=True)
class HostBulkReadScope:
    """Ground a bulk read may sweep without asking: `ls`, `glob`, `grep`.

    WHY A SECOND ANSWER EXISTS. The rules above are matched per PATH, but
    deepagents splits filesystem tools into two scopes, and only one of them
    asks that question. `read_file` is ``exact`` — it interrupts iff the named
    path matches an interrupt rule. `ls`/`glob`/`grep` are ``bulk``: the path is
    a search ROOT, so `_make_bulk_when_predicate` instead asks whether the
    subtree OVERLAPS any interrupt-mode rule, looking only at interrupt rules
    and ignoring allow rules and rule order entirely.

    Rule 4's anchor is ``/``. Everything overlaps ``/``. So every `ls` fired,
    on every path, including inside a folder the user had just attached — which
    is the whole of the "an attached folder still asks" report, and the reason a
    consent card could name ``/workspace``, a mount nobody has heard of.

    Fixing the rules could not have helped: no glob expresses "everything except
    these roots", and the predicate would not have consulted an allow rule
    anyway. deepagents does, however, let a host-supplied ``interrupt_on`` entry
    take precedence over its generated one — so this type supplies the missing
    half of the question.

    CONTAINMENT, not overlap. A bulk call goes silent only when EVERY descendant
    it could surface is already granted, so `ls("/Users")` still asks with a
    grant on `/Users/ada/Projects` beneath it: that listing would show the
    user's ungranted siblings.
    """

    #: Subtrees whose entire contents the user has already agreed to.
    prefixes: tuple[str, ...]

    @classmethod
    def build(
        cls,
        roots: Sequence[GrantedRoot],
        *,
        scratch: AgentScratchRoot | None = None,
    ) -> HostBulkReadScope:
        """The confined subtrees for one run: namespaces, grants, scratch."""

        prefixes = [prefix.rstrip("/") for prefix in VIRTUAL_NAMESPACES]
        prefixes.extend(root.path.rstrip("/") for root in roots)
        if scratch is not None:
            prefixes.append(str(scratch.posix).rstrip("/"))
        return cls(tuple(prefixes))

    def confines(self, path: str) -> bool:
        """Is every descendant of ``path`` inside ground already granted?

        Fails CLOSED for anything it cannot reason about — a relative path, a
        traversal, an empty scope. The caller only ever uses a ``True`` here to
        SUPPRESS a prompt, so an unsure answer must be ``False``.
        """

        if not path.startswith("/") or not self.prefixes:
            return False
        candidate = PurePosixPath(path)
        if ".." in candidate.parts:
            return False
        parents = set(candidate.parents)
        return any(
            candidate == PurePosixPath(prefix) or PurePosixPath(prefix) in parents
            for prefix in self.prefixes
        )

    @staticmethod
    def pattern_stays_inside(pattern: object) -> bool:
        """Can ``glob``'s pattern NOT redirect the search out of the root?

        `glob(pattern="/secrets/**", path="/granted")` sweeps `/secrets`, not
        the granted folder — deepagents guards this and a replacement predicate
        that forgot to would be a silent hole. Absolute patterns and traversals
        are treated as an escape; anything that is not a string is unknown, and
        unknown is an escape too.
        """

        if not isinstance(pattern, str):
            return False
        return not pattern.startswith("/") and ".." not in PurePosixPath(pattern).parts


__all__ = (
    "VIRTUAL_NAMESPACES",
    "GrantedRoot",
    "HostBulkReadScope",
    "HostFilesystemRules",
)
