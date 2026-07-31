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

    1. the agent's own virtual namespaces      -> allow  (memories, drafts, ...)
    1b. the agent's own scratch on disk        -> allow  ($COPILOT_HOME/.tmp)
    2. every granted host root                 -> allow  (read + write)
    3. everything else                         -> interrupt  (ask the user)
    4. nothing reaches here                    -> deny  (the floor)

Rule 3 is what makes an ungranted path ASK instead of lying, and rule 4 is what
makes a future wiring mistake fail closed instead of silently succeeding. A run
with zero grants still gets rules 1, 3 and 4 — so "you have granted nothing" is
answered by a consent request, never by an empty listing.

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
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Final

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
    ) -> tuple[dict[str, object], ...]:
        rules: list[dict[str, object]] = []

        # 1. The agent's own namespaces. Without these, an ordinary
        #    `read_file("/memories/...")` would hit the catch-all interrupt and
        #    prompt the user about the agent's own bookkeeping.
        rules.append(
            {
                "operations": list(_ALL_OPERATIONS),
                "paths": [f"{prefix}**" for prefix in VIRTUAL_NAMESPACES],
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

        # 3. Granted roots — READ only, deliberately. Granting a folder makes it
        #    readable without prompting; it does not make it directly writable.
        #    See rule 5 for why.
        for root in roots:
            rules.append(
                {
                    "operations": [_READ],
                    "paths": [root.path, root.glob()],
                    "mode": _Mode.ALLOW,
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

        # 5. Every other WRITE is denied outright — NOT interrupted.
        #
        #    This is D7, and it is the reason `permissions` was empty before:
        #    "generic filesystem interrupts never authorize a host mutation."
        #    If this rule were `interrupt`, approving a read-shaped prompt would
        #    quietly become a way to mutate the user's disk outside the staged
        #    C3 overlay and C2's commit authority — the one path that records
        #    what changed and can undo it. Host writes keep going through the
        #    typed workspace operation adapter; this rule set only ever widens
        #    READS.
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


__all__ = (
    "VIRTUAL_NAMESPACES",
    "GrantedRoot",
    "HostFilesystemRules",
)
