"""The agent's own working area on disk — ``$COPILOT_HOME/.tmp`` (PRD-FS-12).

This replaces ``.copilot`` inside a granted folder. That location was wrong in
two ways at once: it wrote into the user's own content, and it made a read-only
grant a special case (the scratch dir had to be suppressed, and because
``.copilot`` is a *hidden* segment the glob matcher could not see it, so the
suppression had to be re-implemented in :mod:`.host_floor`). Moving the scratch
somewhere that is ours removes both problems: nothing is ever written into a
folder the user attached, and a grant's writability stops affecting where the
agent keeps its bookkeeping.

Where it lives (D3)
-------------------
``$COPILOT_HOME/.tmp``, where ``COPILOT_HOME`` defaults to ``~/.0xcopilot`` —
the same resolution ``tools/cli/lib/paths.mjs`` uses for ``STATE_DIR``, so the
CLI's ``uninstall`` already knows about this tree. Deliberately NOT the install
directory: an upgrade replaces it, and a global npm prefix may be read-only.

What it is named (D4/D5)
------------------------
``.tmp/<conversation_id>/`` — the opaque id, NEVER the chat title. A title is
user content: it carries client names, people's names, anything typed into a
chat box, and a title-derived path leaks that into the filesystem, into every
log line and into every error message that prints a path. Titles also change,
and a directory that renames itself is not a directory you can rely on.

That is enforced STRUCTURALLY here rather than by convention:
:func:`safe_segment` accepts only an opaque identifier and RAISES on anything
else, so passing a title cannot silently produce a path. It refuses rather than
sanitises, because sanitising is worse: two different ids that normalise to one
segment would silently share one scratch directory.

The human-readable name lives in ``meta.json`` INSIDE the directory. The opaque
id names the *path*; the metadata carries the *meaning*.

Layout (§3 of the PRD)
----------------------
Not everything re-roots at the run — the namespaces do not share a lifetime::

    .tmp/<conversation_id>/
        meta.json                       the human name + orientation (D5)
        drafts/                         conversation-scoped: a draft outlives
                                        the run that started it
        mcp/<server>/                   conversation-scoped: the browsable
                                        connector catalog the model reads at
                                        /mcp/, seeded at run start and enriched
                                        by load_mcp_server
        <run_id>/
            tool-results/               this run's offloaded tool output
            subagents/                  this run's subagent transcripts

``/memories/``, ``/policies/`` and ``/skills/`` are deliberately absent: they
are USER-scoped and already file-backed by ``FileMemoryBackendFactory``.
Memory that dies with a run is not memory.

Retention (D8)
--------------
Nothing here ages out and nothing is size-capped. A scratch directory is
deleted only when the chat that owns it is deleted (D6, :meth:`ConversationScratch.delete`).
There is deliberately no sweeper, no TTL and no timer — see PRD §4, which
records that as an accepted risk rather than an oversight.

Permissions (§5) — and the trap
-------------------------------
``.tmp`` is a DOTTED segment, and deepagents matches rule paths with ``wcmatch``
under ``BRACE | GLOBSTAR`` and **no** ``DOTGLOB``. Under those flags neither
``*`` nor ``**`` matches a segment beginning with a dot, and
``_check_fs_permission`` returns ``"allow"`` when no rule matches. That exact
combination is what let ``~/.ssh/id_rsa`` through a rule set whose author
believed ``/**`` was total.

So the allow rule here is written with a LITERAL path — every segment of
``$COPILOT_HOME/.tmp`` spelled out, including both dotted ones (``.0xcopilot``
and ``.tmp``) — never as a glob that has to *find* the directory. A literal dot
in the pattern does match a leading dot in the path; a ``*`` does not.

Who actually decides — stated exactly, because it is easy to overclaim
------------------------------------------------------------------------
Under the CURRENT dotted name and the default ``COPILOT_HOME``, every path
beneath the scratch carries at least one hidden segment, so
``_check_fs_permission`` answers ``"allow"`` for it **whether or not this rule
exists** — via the unmatched default. On that configuration
:class:`~.host_floor.HostFilesystemFloor` is what genuinely permits the scratch
and refuses everything beside it, and it is keyed on the same root object so the
two cannot disagree.

The rule is nonetheless load-bearing, in the two configurations where the
matcher CAN see the path — and both are one edit away:

* **a visible scratch.** Drop the dot from ``SCRATCH_DIR_NAME``, or set
  ``COPILOT_HOME`` somewhere visible with a visible scratch name, and rule 5
  (``write`` → ``deny`` on ``/**``) starts matching. This rule, ordered BEFORE
  it, is then the only thing keeping the agent's own working area writable;
* **a future ``DOTGLOB``.** ``host_floor`` anticipates upstream fixing the flag.
  The day it lands, rules 4 and 5 become total over hidden paths too and would
  deny every scratch write. Ordering this allow first is what survives that.

So: the floor covers what the matcher cannot see, the rule covers what it can,
and neither is total alone. ``test_agent_scratch`` asserts each against the half
it actually owns — via ``_matching_rule`` where the question is "did a rule
DECIDE", never via a return value the unmatched default can forge.

Two further details:

* a path with a *further* dotted segment (``.tmp/<conv>/.hidden/x``) is
  invisible to the pattern and is therefore decided by the floor, which admits
  it, segment-wise, because it is genuinely inside the scratch root;
* the pattern is escaped with :func:`wcmatch.glob.escape` before use, so a home
  directory containing ``[``, ``{`` or ``*`` stays a literal path rather than
  quietly becoming a wildcard that matches the wrong tree.

The allow is scoped to ``$COPILOT_HOME/.tmp`` and NOTHING above it — which
matters more than it looks, because ``COPILOT_HOME`` also holds the staged
Python runtime and Postgres the app boots from. Host writes outside the scratch
stay on the staged C3 → ledger → C2 lane; this module does not widen that and
must never be used to.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
from dataclasses import dataclass
from collections.abc import Mapping
from pathlib import Path, PurePosixPath
from typing import Any, Final

_LOGGER = logging.getLogger(__name__)

#: Environment variable naming the user-writable state root. Same name and same
#: default as ``tools/cli/lib/paths.mjs`` ``STATE_DIR`` — the CLI stages the
#: runtime there and ``copilot uninstall`` clears it, so a second spelling here
#: would strand the scratch tree behind an uninstall that believed it was done.
COPILOT_HOME_ENV: Final = "COPILOT_HOME"

#: ``$HOME``-relative default when ``COPILOT_HOME`` is unset or empty.
DEFAULT_COPILOT_HOME_DIRNAME: Final = ".0xcopilot"

#: The scratch directory inside ``COPILOT_HOME``. Dotted on purpose: it is
#: ours, not the user's, and it should stay out of the way in a file browser.
#: See the module header for why that dot is also the whole permission story.
SCRATCH_DIR_NAME: Final = ".tmp"

#: Conversation-scoped subdirectory. A draft outlives the run that started it,
#: so it is NOT under ``<run_id>/``.
DRAFTS_DIR_NAME: Final = "drafts"

#: Conversation-scoped subdirectory holding the browsable MCP catalog
#: (``mcp/<server>/SERVER.md`` + ``tools/`` + ``resources/``). Conversation
#: scope for the same reason ``drafts/`` has it, and the reason is a bug that
#: has already been paid for: the catalog used to live in the per-harness
#: process, and a harness is rebuilt for every run AND again on approval
#: resume — so a connector browsed in turn 1 was gone in turn 2, and one
#: browsed before a write approval was gone when the run resumed. Run scope
#: would reproduce exactly that.
MCP_DIR_NAME: Final = "mcp"

#: Run-scoped subdirectories.
TOOL_RESULTS_DIR_NAME: Final = "tool-results"
SUBAGENTS_DIR_NAME: Final = "subagents"

#: The human-readable name and orientation for one conversation's scratch (D5).
META_FILE_NAME: Final = "meta.json"

#: Directory / file modes, matching the file store's own scaffold: the scratch
#: holds tool output and subagent transcripts, which is the same class of
#: material as the conversation store.
_DIR_MODE: Final = 0o700
_FILE_MODE: Final = 0o600

#: What may name a directory here. An opaque identifier: starts alphanumeric,
#: then alphanumerics and the three separators ids actually use. A chat title
#: fails on the first space, which is the point — see the module header on D4.
#:
#: The leading-alphanumeric anchor is doing three jobs at once, so do not relax
#: it to ``\w`` or ``.``: it is what makes the segment itself never
#: matcher-blind (no leading dot), and it is what rejects ``.`` and ``..``
#: without a separate traversal check — a mutation run proved a hand-written
#: ``value == ".."`` guard beside it was unreachable.
_SAFE_SEGMENT: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class ScratchIdError(ValueError):
    """An identifier was not safe to use as a scratch directory name.

    Raised rather than sanitised. Sanitising would map two distinct ids onto one
    directory, which silently mixes two chats' working files — a worse failure
    than having no scratch at all.
    """


def safe_segment(value: str, *, kind: str = "id") -> str:
    """Return ``value`` unchanged if it may name a scratch directory, else raise.

    ``kind`` names the field in the error so a caller passing the wrong thing
    (a title, a path, an org slug) is told which one. The error message quotes
    the *kind*, never the value: an invalid value may be user content, and this
    module exists partly so user content never reaches a log line.
    """

    if not isinstance(value, str) or not _SAFE_SEGMENT.match(value):
        raise ScratchIdError(
            f"{kind} is not usable as a scratch directory name; "
            "scratch directories are named by opaque identifier only"
        )
    return value


def copilot_home(env: Mapping[str, str] | None = None) -> Path:
    """Resolve ``COPILOT_HOME``, defaulting to ``~/.0xcopilot``.

    Mirrors ``tools/cli/lib/paths.mjs``: an explicitly set, non-empty
    ``COPILOT_HOME`` wins; otherwise the home-relative default. An empty value
    is treated as unset, exactly as the CLI treats it, so a shell that exports
    the variable blank does not resolve the scratch to the filesystem root.
    """

    source = os.environ if env is None else env
    configured = (source.get(COPILOT_HOME_ENV) or "").strip()
    if configured:
        return Path(configured).expanduser()
    return Path.home() / DEFAULT_COPILOT_HOME_DIRNAME


def agent_scratch_root(env: Mapping[str, str] | None = None) -> AgentScratchRoot:
    """The ``$COPILOT_HOME/.tmp`` root for this process."""

    return AgentScratchRoot(copilot_home(env) / SCRATCH_DIR_NAME)


def delete_conversation_scratch(conversation_id: str) -> bool:
    """Remove one chat's scratch tree (D6). ``True`` if it existed.

    The whole of retention, as one total function a deletion path can call
    without knowing anything about this layout: an unusable id or an unreadable
    directory is ``False``, never an exception. Deleting something that should
    not exist is always safe, so unlike provisioning this is NOT gated on the
    desktop signal — gating it would strand a tree written by an earlier,
    differently-configured process.

    Lives here rather than in the worker so a store adapter can call it without
    depending on ``runtime_worker``.

    ``RuntimeError`` is caught alongside the rest because ``Path.home()`` raises
    it — not ``OSError`` — on a process with no resolvable home. A deletion path
    must not turn that into a failed conversation delete.
    """

    try:
        return agent_scratch_root().conversation(conversation_id).delete()
    except (ScratchIdError, OSError, RuntimeError):
        return False


@dataclass(frozen=True)
class RunScratch:
    """One run's working directories inside a conversation's scratch.

    Run-scoped because both hold material about THIS run's calls: offloaded tool
    output and subagent transcripts. Neither means anything once detached from
    the run that produced it.
    """

    path: Path

    @property
    def tool_results(self) -> Path:
        """``<run>/tool-results/`` — offloaded results of this run's calls."""

        return self.path / TOOL_RESULTS_DIR_NAME

    @property
    def subagents(self) -> Path:
        """``<run>/subagents/`` — this run's subagent transcripts + artifacts."""

        return self.path / SUBAGENTS_DIR_NAME

    def provision(self) -> RunScratch:
        """Create this run's directories (idempotent) and return self."""

        _ensure_dir(self.tool_results)
        _ensure_dir(self.subagents)
        return self


@dataclass(frozen=True)
class ConversationScratch:
    """One chat's scratch directory: ``.tmp/<conversation_id>/``.

    Constructed through :meth:`AgentScratchRoot.conversation`, which validates
    the id, so an instance always names a path that cannot have come from a
    title.
    """

    path: Path
    conversation_id: str

    @property
    def meta_path(self) -> Path:
        """``meta.json`` — the human name lives here, not in the path (D5)."""

        return self.path / META_FILE_NAME

    @property
    def drafts(self) -> Path:
        """``drafts/`` — conversation-scoped: a draft outlives its run."""

        return self.path / DRAFTS_DIR_NAME

    @property
    def mcp(self) -> Path:
        """``mcp/`` — the browsable connector catalog for this chat."""

        return self.path / MCP_DIR_NAME

    def run(self, run_id: str) -> RunScratch:
        """The run-scoped subtree for ``run_id`` (validated, never a title)."""

        return RunScratch(self.path / safe_segment(run_id, kind="run_id"))

    def provision(
        self, *, title: str | None = None, **extra: Any
    ) -> ConversationScratch:
        """Create the directories and write ``meta.json``; return self.

        Idempotent, and safe to call on every run: ``meta.json`` is rewritten so
        a renamed chat's scratch reports the current name — the *path* is what
        must not move, not the metadata.

        ``title`` is the one place a chat title is allowed to appear, and it
        appears as a VALUE inside a file rather than as a path segment. It is
        bounded before it is written so an enormous title cannot be used to
        inflate the scratch.
        """

        _ensure_dir(self.path)
        _ensure_dir(self.drafts)
        _ensure_dir(self.mcp)
        self._write_meta(title=title, extra=extra)
        return self

    def delete(self) -> bool:
        """Remove this conversation's scratch tree (D6). ``True`` if it existed.

        The whole of retention: nothing else deletes from here, on any schedule
        (D8). Best-effort — a scratch that cannot be removed must not fail the
        conversation deletion that triggered it, because the conversation is the
        record and this is the ephemera.
        """

        try:
            if not self.path.is_dir():
                return False
            shutil.rmtree(self.path)
            return True
        except OSError:
            _LOGGER.warning(
                "agent_scratch.delete_failed conversation_scratch_remained=true"
            )
            return False

    def _write_meta(self, *, title: str | None, extra: Mapping[str, Any]) -> None:
        """Write ``meta.json`` atomically-enough for a scratch directory."""

        document: dict[str, Any] = {
            "conversation_id": self.conversation_id,
            "title": (title or "")[:512] or None,
            **{key: value for key, value in extra.items() if key != "conversation_id"},
        }
        try:
            self.meta_path.write_text(
                json.dumps(document, indent=2, sort_keys=True, default=str),
                encoding="utf-8",
            )
            _restrict_file(self.meta_path)
        except OSError:
            # The directories are what the agent needs; the metadata is an aid.
            _LOGGER.warning("agent_scratch.meta_write_failed")


@dataclass(frozen=True)
class AgentScratchRoot:
    """``$COPILOT_HOME/.tmp`` — the whole of the agent's own writable disk.

    Holds the rule-building half too, so the place that OWNS the path is the
    place that says which paths are allowed. Splitting those was how the
    ``.copilot`` version ended up with a floor and a rule set that disagreed.
    """

    path: Path

    def conversation(self, conversation_id: str) -> ConversationScratch:
        """The scratch for ``conversation_id`` (validated — never a title)."""

        segment = safe_segment(conversation_id, kind="conversation_id")
        return ConversationScratch(self.path / segment, conversation_id=segment)

    @property
    def posix(self) -> str:
        """The root as a POSIX-absolute string, for rules and floor comparison.

        Windows roots are normalised to POSIX form (``C:/Users/...``) the same
        way :class:`~.host_filesystem.GrantedRoot` expects them, so one spelling
        reaches both the matcher and the floor.
        """

        return _posix_absolute(self.path)

    def allow_rules(self) -> tuple[dict[str, object], ...]:
        """The direct-write allow for the scratch, as plain rule dicts.

        Emitted as dicts (not ``FilesystemPermission``) so this module stays
        importable without deepagents, matching
        :class:`~.host_filesystem.HostFilesystemRules`.

        TWO patterns, because ``<root>/**`` does not match ``<root>`` itself
        under GLOBSTAR — without the bare path, ``ls`` of the scratch root would
        fall through to the catch-all interrupt and prompt the user about the
        agent's own working directory.

        Both are LITERAL and escaped. See the module header: this is the rule
        the dotted-segment trap eats if it is ever rewritten as a glob.
        """

        literal = _escape_pattern(self.posix)
        return (
            {
                "operations": ["read", "write"],
                "paths": [literal, f"{literal}/**"],
                "mode": "allow",
            },
        )


def _ensure_dir(path: Path) -> Path:
    """Create ``path`` (and parents) ``0o700`` if absent; return it."""

    path.mkdir(mode=_DIR_MODE, parents=True, exist_ok=True)
    return path


def _restrict_file(path: Path) -> None:
    """Best-effort ``chmod 0o600`` — some filesystems reject it."""

    try:
        path.chmod(_FILE_MODE)
    except OSError:
        pass


def _posix_absolute(path: Path) -> str:
    """Render ``path`` as the POSIX-absolute string the matcher/floor compare.

    ``as_posix`` on Windows yields ``C:/Users/...``, which is exactly the shape
    :class:`~.host_filesystem.GrantedRoot` requires and
    :class:`~.host_path.HostPathClassifier` classifies as host-absolute.
    """

    return PurePosixPath(path.as_posix()).as_posix()


def _escape_pattern(literal: str) -> str:
    """Escape ``literal`` so wcmatch treats every character as itself.

    A home directory containing ``[``, ``{``, ``*`` or ``?`` would otherwise
    turn the "literal" rule path into a wildcard — matching some other tree, or
    nothing at all. ``escape`` is a no-op on an ordinary path, so this costs
    nothing in the normal case and closes a silent one in the abnormal one.

    Falls back to the unescaped literal if wcmatch is unavailable (it is a
    deepagents dependency, and this module must stay importable without it); in
    that case there is no matcher to confuse either.
    """

    try:
        from wcmatch import glob as wcglob  # noqa: PLC0415
    except ImportError:  # pragma: no cover - wcmatch ships with deepagents
        return literal
    return wcglob.escape(literal)


__all__ = (
    "COPILOT_HOME_ENV",
    "DEFAULT_COPILOT_HOME_DIRNAME",
    "DRAFTS_DIR_NAME",
    "MCP_DIR_NAME",
    "META_FILE_NAME",
    "SCRATCH_DIR_NAME",
    "SUBAGENTS_DIR_NAME",
    "TOOL_RESULTS_DIR_NAME",
    "AgentScratchRoot",
    "ConversationScratch",
    "RunScratch",
    "ScratchIdError",
    "agent_scratch_root",
    "copilot_home",
    "delete_conversation_scratch",
    "safe_segment",
)
