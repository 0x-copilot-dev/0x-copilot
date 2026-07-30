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

Scratch memory
--------------
Because rule 4 exists, the agent needs a real place to keep working files.
`.copilot` inside a granted root is that place (write access assumed, per
product decision), which is what lets the composite's default stop being a
promiscuous catch-all.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Final

#: Prefixes the agent owns inside its own virtual filesystem. These are routed
#: by `CompositeBackend` to real backends (memory, drafts, subagent artifacts),
#: so they must be allowed BEFORE the catch-all interrupt rule or every ordinary
#: memory read would prompt the user.
VIRTUAL_NAMESPACES: Final[tuple[str, ...]] = (
    "/memories/",
    "/policies/",
    "/skills/",
    "/drafts/",
    "/subagents/",
    "/large_tool_results/",
    "/workspace/",
    "/tmp/",
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

    @property
    def scratch_path(self) -> str:
        """Where this root's agent scratch directory lives."""

        return str(PurePosixPath(self.path) / SCRATCH_DIR_NAME)

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

        # 2. Granted roots. Reads always; writes only where the grant allows it,
        #    so a read-only grant cannot be widened by this rule set. A
        #    read-only root's writes fall through to rule 3 and ASK, which is
        #    the honest outcome — the user may well say yes.
        for root in roots:
            operations = list(_ALL_OPERATIONS) if root.writable else [_READ]
            rules.append(
                {
                    "operations": operations,
                    "paths": [root.path, root.glob()],
                    "mode": _Mode.ALLOW,
                }
            )

        # 3. Everything else asks, and this is also the FLOOR. It turns the
        #    original defect — a confident empty listing — into a consent
        #    request, and it is present even when `roots` is empty.
        #
        #    There is deliberately no terminal `deny` rule after it. Two facts
        #    make one both impossible and unnecessary: deepagents validates that
        #    every rule path starts with "/" (a bare `**` raises), and `/**`
        #    already matches every absolute path, so nothing an fs tool can
        #    receive is left to the matcher's unmatched-means-allow default.
        #    `interrupt` is itself fail-closed — the call does not proceed
        #    unless a human approves — so using it as the floor asks where a
        #    `deny` floor would simply refuse, which is the better product
        #    behaviour and the same safety.
        rules.append(
            {
                "operations": list(_ALL_OPERATIONS),
                "paths": ["/**"],
                "mode": _Mode.INTERRUPT,
            }
        )
        return tuple(rules)


__all__ = (
    "SCRATCH_DIR_NAME",
    "VIRTUAL_NAMESPACES",
    "GrantedRoot",
    "HostFilesystemRules",
)
