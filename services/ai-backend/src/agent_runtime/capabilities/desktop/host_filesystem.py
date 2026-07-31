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
`.copilot` inside a granted root is that place (write access assumed, per
product decision), which is what lets the composite's default stop being a
promiscuous catch-all.

Rule 2 grants it and `HostFilesystemFloor` admits it, but nothing ever CREATED
it, so until `HostScratchDirectory` below the agent's own working area did not
exist. The failure is not the obvious one: deepagents' `FilesystemBackend.write`
runs `parent.mkdir(parents=True)`, so a WRITE conjures the directory as a side
effect. Reading does not — `ls("<root>/.copilot")` answered `path_not_found`, so
the agent could not look at its own scratch until it had guessed a filename to
write into it. That is this subsystem's own defect turned inward: a real place,
reported absent.

It went unnoticed because every scratch test ran `mkdir(parents=True)` in its
fixture first, proving the PERMISSION and never the DIRECTORY.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Final

from agent_runtime.capabilities.desktop.host_path import HostPathClassifier

_LOGGER = logging.getLogger("agent_runtime.desktop.host_filesystem")

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

#: The agent's durable scratch directory inside a granted root.
SCRATCH_DIR_NAME: Final = ".copilot"

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

    ``path`` is a real host-absolute path. It is used to build allow rules and
    to site the scratch directory; it is never handed to the model as a value.
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

    @property
    def scratch_path(self) -> str:
        """Where this root's agent scratch directory lives."""

        return str(PurePosixPath(self.path) / SCRATCH_DIR_NAME)

    def glob(self) -> str:
        """Match the root itself and everything beneath it."""

        return str(PurePosixPath(self.path) / "**")


class HostScratchDirectory:
    """Creates `<granted root>/.copilot` for the grants that may hold one.

    Called once while a run's backend is composed (``factory._host_default_backend``),
    because a grant is bound exactly there and nowhere earlier — the folder set is
    per run, and a folder attached mid-run arrives on the next composition.

    Three rules, and each one is a refusal the caller must not talk it out of:

    * **read-only grants get nothing.** Creating a directory is a WRITE. A user
      who attached a folder read-only did not authorise one, and rule 3 +
      :meth:`HostFilesystemFloor.permits_write` both agree the agent could never
      use it anyway. Materialising it would be a mutation performed purely so a
      log line could say it happened.
    * **one level, never ``parents=True``.** The scratch dir goes INSIDE a granted
      root; its ancestors are not granted. If the root itself is gone (unmounted
      volume, folder deleted since it was attached) the ``mkdir`` must fail, not
      helpfully rebuild a path the user never handed over.
    * **failure is not fatal.** A read-only filesystem, a permissions refusal, a
      race with another process — none of that is a reason to kill a run whose
      actual work may not touch scratch at all. Every ``OSError`` degrades to a
      warning and the run proceeds; the agent's own write, if it makes one, gets
      the honest error from the backend.

    Nothing here is logged with a path in it — a granted root is user data, and
    this module's whole neighbourhood keeps host paths out of logs.

    ONE DECODE, IN THE LAST INCH. :attr:`GrantedRoot.scratch_path` is spelled in
    the canonical POSIX encoding the rules and the floor match against, and on
    Windows that spelling (``/C:/Users/p/.copilot``) is not a path the operating
    system can create. ``mkdir`` is the only thing here that touches a real
    filesystem, so it is the only thing that decodes — the same placement, and
    the same reason, as ``NativeHostPathBackend`` sitting directly on the real
    backend in ``factory._host_default_backend``.
    """

    #: Owner-only. Agent working files sit inside a user's own folder; there is no
    #: reason for them to be group- or world-readable. Subject to the process
    #: umask like any ``mkdir``, which can only make it narrower.
    _MODE: Final = 0o700

    @classmethod
    def native_scratch_path(cls, root: GrantedRoot) -> str:
        r"""``root``'s scratch directory in the HOST's own spelling.

        Identity on POSIX; ``/C:/Users/p/.copilot`` → ``C:\Users\p\.copilot`` on
        Windows. Split out from :meth:`ensure` so the decode is assertable
        without creating a directory, which is the only way a POSIX test run can
        prove the Windows behaviour at all.
        """

        return HostPathClassifier.native(root.scratch_path)

    @classmethod
    def ensure(cls, roots: tuple[GrantedRoot, ...]) -> tuple[str, ...]:
        """Create the scratch dir of every WRITABLE root; return the usable ones.

        The return value is the set that now exists (already-present counts), in
        the canonical spelling every other caller in this module speaks — the
        native form exists only for the duration of the ``mkdir``. It is
        deliberately not an error channel: a root missing from it simply has no
        scratch, which the floor already treats as "that write is refused".
        """

        usable: list[str] = []
        for root in roots:
            if not root.writable:
                continue
            try:
                Path(cls.native_scratch_path(root)).mkdir(mode=cls._MODE, exist_ok=True)
            except OSError as error:
                # Never the path: a granted root is user data. The error's TYPE
                # is enough to tell a read-only volume from a vanished folder.
                _LOGGER.warning(
                    "host_filesystem.scratch_unavailable error=%s "
                    "(the agent has no scratch directory in one attached folder)",
                    type(error).__name__,
                )
                continue
            usable.append(root.scratch_path)
        return tuple(usable)


class HostFilesystemRules:
    """Builds the ordered `FilesystemPermission` list for one run.

    Emitted as plain dicts so this module stays importable without deepagents
    (the runtime factory converts them at build time, where the dependency
    already exists). The ORDER of the returned tuple is the security property —
    deepagents returns the first matching rule's mode.
    """

    @staticmethod
    def build(roots: tuple[GrantedRoot, ...]) -> tuple[dict[str, object], ...]:
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

        # 2. The agent's scratch directory inside each granted root. This is the
        #    ONE host location the agent may write directly, because it holds
        #    the agent's own working files rather than the user's content. It
        #    must precede rule 3, which is read-only.
        writable_scratch = [
            f"{root.scratch_path}/**" for root in roots if root.writable
        ] + [root.scratch_path for root in roots if root.writable]
        if writable_scratch:
            rules.append(
                {
                    "operations": list(_ALL_OPERATIONS),
                    "paths": writable_scratch,
                    "mode": _Mode.ALLOW,
                }
            )

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
    "SCRATCH_DIR_NAME",
    "VIRTUAL_NAMESPACES",
    "GrantedRoot",
    "HostFilesystemRules",
    "HostScratchDirectory",
)
