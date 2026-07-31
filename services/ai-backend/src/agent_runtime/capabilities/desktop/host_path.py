r"""Path classification for the workspace route: virtual vs host-absolute.

The live defect this exists to make impossible
---------------------------------------------
A user asked whether the agent could read their downloads folder. The agent
called ``ls`` with ``/Users/<name>/Downloads``; that call landed on the agent
MEMORY virtual filesystem (Deep Agents binds its built-in ``ls`` / ``read_file``
to whatever backend it is handed), memory holds nothing at that path, and the
tool returned an EMPTY LISTING as a SUCCESS. The agent reported an empty folder
that holds 1009 files, under a green tool card and a 175 ms duration. Nothing in
the trace looked wrong.

An empty listing of a path the process cannot see is not a result, it is a lie.
So a host-looking path is a *typed* thing this package recognises before any
backend answers it, and :class:`HostPathKind` is what a router consults instead
of falling through to a backend that will answer with silence.

The reconciliation with the broker's security property
------------------------------------------------------
:mod:`agent_runtime.capabilities.desktop.workspace_backend` guarantees that only
mount names and root-relative virtual paths ever cross to the broker — a
host-absolute path is never constructed or sent. That property is kept intact
here: a host-absolute path is an input to the GRANT flow (agent → user, "grant
access to this folder?"), never to the READ flow. Once a grant covers it the read
is served as mount + root-relative path exactly as before, and
:class:`HostRootIndex` is what performs that translation locally.

Both platforms, by shape and not by host
----------------------------------------
Classification is driven by the SHAPE of the string, never by the platform the
ai-backend happens to run on, so a Windows path is recognised identically in a
macOS test run. A path is Windows-shaped when it starts with a drive letter
(``C:``) or with a backslash (``\\server\share``, ``\\?\C:\...``, ``\Users``);
otherwise it is POSIX-shaped, where a backslash is an ordinary filename
character. A classifier that knew only POSIX would silently mis-route every
Windows path — the same failure mode, one platform over.

Everything that is not a resolvable folder fails CLOSED (:attr:`HostPathKind.UNSAFE`
/ :attr:`HostPathKind.HOST_AMBIGUOUS`) rather than becoming a grant request: a
grant request is for a folder the user could legitimately grant, never a way to
launder a traversal, a device path, or a path whose meaning depends on host state
this process cannot see.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Final


class HostPathKind(StrEnum):
    """How a path the agent supplied must be routed."""

    #: Addresses this process's virtual filesystem (``/workspace/<mount>/...``,
    #: ``/memories/...``, or a mount-relative remainder). Served as today.
    VIRTUAL = "virtual"
    #: Names a concrete host folder or file. Readable only through a covering
    #: grant; otherwise it becomes a grant request.
    HOST_ABSOLUTE = "host_absolute"
    #: Host-shaped but not fully qualified — its meaning depends on host state
    #: this process cannot see (a per-drive cwd, the current drive, ``$HOME``).
    #: Refused: there is no single folder we could truthfully ask to grant.
    HOST_AMBIGUOUS = "host_ambiguous"
    #: Refused outright. A traversal, a device namespace, a reserved name — a
    #: shape that must never be read and must never become a grant request.
    UNSAFE = "unsafe"


class HostPathFlavour(StrEnum):
    """Which platform's path grammar the string was written in."""

    POSIX = "posix"
    WINDOWS = "windows"


class HostPathRefusal(StrEnum):
    """Machine reason a path was refused (never surfaced verbatim to the model)."""

    TRAVERSAL = "traversal"
    CONTROL_CHARACTER = "control_character"
    DEVICE_NAMESPACE = "device_namespace"
    RESERVED_NAME = "reserved_name"
    TRAILING_DOT_OR_SPACE = "trailing_dot_or_space"
    DRIVE_RELATIVE = "drive_relative"
    ROOT_RELATIVE = "root_relative"
    HOME_RELATIVE = "home_relative"
    INCOMPLETE_UNC = "incomplete_unc"
    VOLUME_ROOT = "volume_root"


class HostPathMessages:
    """Safe, model-facing copy for a refused or ungranted host path.

    Every string is generic guidance: it repeats nothing about the host beyond
    what the caller already supplied, and it always tells the model what to do
    next. Silence — an empty listing, an empty match set — is never one of the
    options.
    """

    TRAVERSAL: Final = (
        "That path was refused: it contains a '..' segment that could leave the "
        "granted folder. Supply the full path of the file or folder instead."
    )
    CONTROL_CHARACTER: Final = (
        "That path was refused: it contains control characters. Supply a plain "
        "filesystem path."
    )
    DEVICE_NAMESPACE: Final = (
        "That path was refused: it addresses a device rather than a folder."
    )
    RESERVED_NAME: Final = (
        "That path was refused: it uses a name the operating system reserves for "
        "a device."
    )
    TRAILING_DOT_OR_SPACE: Final = (
        "That path was refused: a path segment ends in a dot or a space, which "
        "does not name the file it appears to."
    )
    DRIVE_RELATIVE: Final = (
        "That path is relative to a drive's current directory, which this "
        "process cannot resolve. Supply the full path, for example "
        "C:\\Users\\name\\Downloads."
    )
    ROOT_RELATIVE: Final = (
        "That path has no drive letter, so it cannot be resolved. Supply the "
        "full path, for example C:\\Users\\name\\Downloads."
    )
    HOME_RELATIVE: Final = (
        "This process cannot expand '~'. Ask the user for the full path, for "
        "example /Users/name/Downloads."
    )
    INCOMPLETE_UNC: Final = (
        "That network path names no share. Supply the full path, for example "
        "\\\\server\\share\\folder."
    )
    VOLUME_ROOT: Final = (
        "A whole drive cannot be granted. Name the folder you need, for example "
        "/Users/name/Downloads."
    )

    _BY_REFUSAL: Final[Mapping[HostPathRefusal, str]] = {
        HostPathRefusal.TRAVERSAL: TRAVERSAL,
        HostPathRefusal.CONTROL_CHARACTER: CONTROL_CHARACTER,
        HostPathRefusal.DEVICE_NAMESPACE: DEVICE_NAMESPACE,
        HostPathRefusal.RESERVED_NAME: RESERVED_NAME,
        HostPathRefusal.TRAILING_DOT_OR_SPACE: TRAILING_DOT_OR_SPACE,
        HostPathRefusal.DRIVE_RELATIVE: DRIVE_RELATIVE,
        HostPathRefusal.ROOT_RELATIVE: ROOT_RELATIVE,
        HostPathRefusal.HOME_RELATIVE: HOME_RELATIVE,
        HostPathRefusal.INCOMPLETE_UNC: INCOMPLETE_UNC,
        HostPathRefusal.VOLUME_ROOT: VOLUME_ROOT,
    }

    #: Fallback for a refusal with no dedicated copy (a future enum member).
    GENERIC: Final = (
        "That path was refused. Supply the full path of the folder you need."
    )

    @classmethod
    def for_refusal(cls, refusal: HostPathRefusal | None) -> str:
        """Return the safe public message for a machine refusal reason."""

        if refusal is None:
            return cls.GENERIC
        return cls._BY_REFUSAL.get(refusal, cls.GENERIC)


class _Sep:
    """Path separators, spelled once so escaping stays readable."""

    POSIX: Final = "/"
    WINDOWS: Final = "\\"
    BOTH: Final = "/\\"


class _WinPrefix:
    r"""Windows namespace prefixes, composed rather than escaped inline."""

    #: ``\\`` — UNC and both extended namespaces start here.
    UNC: Final = _Sep.WINDOWS * 2
    #: ``\\?\`` — extended-length ("long path") namespace.
    EXTENDED: Final = f"{UNC}?{_Sep.WINDOWS}"
    #: ``\\.\`` — device namespace. Never a folder; always refused.
    DEVICE: Final = f"{UNC}.{_Sep.WINDOWS}"
    #: ``UNC\`` — the extended spelling of a UNC share (``\\?\UNC\srv\share``).
    EXTENDED_UNC: Final = f"UNC{_Sep.WINDOWS}"


@dataclass(frozen=True)
class ClassifiedPath:
    """A path the agent supplied, classified and normalised.

    ``root`` is the volume/namespace root in its native spelling (``/``,
    ``C:\\``, ``\\\\server\\share``) and ``segments`` are the cleaned path
    segments below it — no separators, no ``.``, and never ``..`` (a traversal is
    refused instead of normalised, so nothing here can have been laundered).

    The comparison keys (:attr:`root_key`, :attr:`segment_keys`) fold case for
    Windows-shaped paths and preserve it for POSIX-shaped ones, so coverage
    checks match each platform's own semantics regardless of where this code
    runs.
    """

    kind: HostPathKind
    flavour: HostPathFlavour
    root: str = ""
    segments: tuple[str, ...] = ()
    refusal: HostPathRefusal | None = None

    @property
    def is_host(self) -> bool:
        """True when this names a concrete host folder or file."""

        return self.kind is HostPathKind.HOST_ABSOLUTE

    @property
    def separator(self) -> str:
        """The native separator for this path's flavour."""

        return _Sep.WINDOWS if self.flavour is HostPathFlavour.WINDOWS else _Sep.POSIX

    @property
    def display(self) -> str:
        """The path re-rendered in its native spelling (for a consent surface)."""

        if not self.segments:
            return self.root
        body = self.separator.join(self.segments)
        if self.root.endswith(self.separator):
            return f"{self.root}{body}"
        return f"{self.root}{self.separator}{body}"

    @property
    def folder_name(self) -> str:
        """The last segment — what a consent card names — or the root itself."""

        return self.segments[-1] if self.segments else self.root

    @property
    def root_key(self) -> str:
        """Case-normalised volume root, for coverage comparison."""

        return self._fold(self.root)

    @property
    def segment_keys(self) -> tuple[str, ...]:
        """Case-normalised segments, for coverage comparison."""

        return tuple(self._fold(segment) for segment in self.segments)

    def parent(self) -> ClassifiedPath:
        """The containing folder — the grantable unit for a file read.

        A path already at its volume root has no grantable parent and is
        returned as a :attr:`HostPathKind.UNSAFE` volume-root refusal, so a
        request to read ``/x`` can never turn into "grant the whole drive".
        """

        if self.kind is not HostPathKind.HOST_ABSOLUTE or len(self.segments) < 2:
            # One segment deep, so the container IS the volume root.
            return HostPathClassifier.refused(
                HostPathRefusal.VOLUME_ROOT, flavour=self.flavour
            )
        return ClassifiedPath(
            kind=self.kind,
            flavour=self.flavour,
            root=self.root,
            segments=self.segments[:-1],
        )

    def contains(self, other: ClassifiedPath) -> bool:
        """True when ``other`` is this folder or lies beneath it.

        Segment-wise, so ``/Users/p/Downloads`` does not cover
        ``/Users/p/Downloads2``, and flavour-and-root-exact, so a POSIX root
        never covers a Windows path.
        """

        if not self.is_host or not other.is_host:
            return False
        if self.flavour is not other.flavour or self.root_key != other.root_key:
            return False
        mine = self.segment_keys
        return other.segment_keys[: len(mine)] == mine

    def relative_to(self, root: ClassifiedPath) -> str:
        """Return the POSIX, root-relative path the broker accepts.

        ``""`` denotes the root itself. This is the ONLY form that crosses to the
        broker: the host-absolute string stays in this process.
        """

        if not root.contains(self):
            msg = "path is not covered by the supplied host root"
            raise ValueError(msg)
        return _Sep.POSIX.join(self.segments[len(root.segments) :])

    def _fold(self, value: str) -> str:
        """Case-fold for Windows-shaped paths; preserve case for POSIX ones."""

        return value.casefold() if self.flavour is HostPathFlavour.WINDOWS else value


class HostPathClassifier:
    """Classifies an agent-supplied path as virtual, host, ambiguous, or unsafe.

    Pure and side-effect free: it never touches the filesystem, never expands
    ``~``, and never consults the running platform. Nothing here can become a
    host-path oracle because nothing here reads host state.
    """

    #: POSIX-absolute first segments that belong to THIS process's virtual
    #: filesystem rather than the host. Mirrors the composite route table in
    #: ``agent_runtime.execution.factory._composed_deep_backend`` (plus
    #: ``Keys.VirtualPath``); a new virtual route must be added here too or its
    #: paths will be read as host folders.
    VIRTUAL_ROOTS: Final[frozenset[str]] = frozenset(
        {
            "workspace",
            "memories",
            "policies",
            "skills",
            "drafts",
            "subagents",
            "large_tool_results",
        }
    )

    #: Names Windows resolves to a device no matter which folder they appear in.
    _RESERVED_WINDOWS_NAMES: Final[frozenset[str]] = frozenset(
        {"con", "prn", "aux", "nul", "conin$", "conout$"}
        | {f"com{index}" for index in range(1, 10)}
        | {f"lpt{index}" for index in range(1, 10)}
    )

    _DRIVE: Final = re.compile(r"^([A-Za-z]):")
    _CURRENT: Final = "."
    _PARENT: Final = ".."
    _HOME: Final = "~"
    _POSIX_ROOT: Final = _Sep.POSIX

    @classmethod
    def classify(cls, path: str | None) -> ClassifiedPath:
        """Classify ``path`` by shape. Never raises; refusals are typed."""

        text = path or ""
        flavour = cls.flavour(text)
        if cls._has_control_characters(text):
            return cls.refused(HostPathRefusal.CONTROL_CHARACTER, flavour=flavour)
        if flavour is HostPathFlavour.WINDOWS:
            return cls._classify_windows(text)
        return cls._classify_posix(text)

    @classmethod
    def flavour(cls, path: str | None) -> HostPathFlavour:
        """Return the path grammar ``path`` is written in, by shape alone.

        Windows-shaped: a leading drive letter (``C:``) or a leading backslash
        (UNC, extended, or current-drive-rooted). Everything else is POSIX,
        where an interior backslash is a legal filename character and must NOT
        be read as a separator.
        """

        text = path or ""
        if cls._DRIVE.match(text) or text.startswith(_Sep.WINDOWS):
            return HostPathFlavour.WINDOWS
        return HostPathFlavour.POSIX

    @classmethod
    def is_host_shaped(cls, path: str | None) -> bool:
        """True when ``path`` addresses the host rather than this process.

        Deliberately independent of whether the path is *safe*: a router must
        claim ``/Users/x/../../etc`` so it can be refused here, instead of
        letting it fall through to a backend that would answer with silence.
        Mount membership is NOT considered — the workspace backend applies that
        narrowing itself, since only it knows its own mount table.
        """

        text = path or ""
        if cls.flavour(text) is HostPathFlavour.WINDOWS:
            return True
        if text.startswith(cls._HOME):
            return True
        if not text.startswith(cls._POSIX_ROOT):
            return False
        first = cls._first_segment(text)
        # ``/`` alone is the addressed backend's own root, not a host folder —
        # and a volume root is never grantable, so it is nobody's host path.
        return bool(first) and first not in cls.VIRTUAL_ROOTS

    @classmethod
    def refused(
        cls,
        refusal: HostPathRefusal,
        *,
        flavour: HostPathFlavour = HostPathFlavour.POSIX,
    ) -> ClassifiedPath:
        """Build the typed refusal for a path that must never be resolved."""

        kind = (
            HostPathKind.HOST_AMBIGUOUS
            if refusal in cls._AMBIGUOUS_REFUSALS
            else HostPathKind.UNSAFE
        )
        return ClassifiedPath(kind=kind, flavour=flavour, refusal=refusal)

    #: Refusals that mean "host-shaped but under-specified" rather than "unsafe".
    _AMBIGUOUS_REFUSALS: Final[frozenset[HostPathRefusal]] = frozenset(
        {
            HostPathRefusal.DRIVE_RELATIVE,
            HostPathRefusal.ROOT_RELATIVE,
            HostPathRefusal.HOME_RELATIVE,
            HostPathRefusal.INCOMPLETE_UNC,
        }
    )

    # --- POSIX --------------------------------------------------------------

    @classmethod
    def _classify_posix(cls, text: str) -> ClassifiedPath:
        """Classify a POSIX-shaped path (backslashes are filename characters)."""

        if text.startswith(cls._HOME):
            return cls.refused(HostPathRefusal.HOME_RELATIVE)
        segments = cls._segments(text, separators=_Sep.POSIX)
        if segments is None:
            return cls.refused(HostPathRefusal.TRAVERSAL)
        # A rootless remainder is addressed relative to the current backend
        # route, so it stays virtual (the workspace backend reads it as
        # ``<mount>/<relative>`` exactly as before).
        if not text.startswith(cls._POSIX_ROOT):
            return ClassifiedPath(
                kind=HostPathKind.VIRTUAL,
                flavour=HostPathFlavour.POSIX,
                root="",
                segments=segments,
            )
        if segments and segments[0] in cls.VIRTUAL_ROOTS:
            return ClassifiedPath(
                kind=HostPathKind.VIRTUAL,
                flavour=HostPathFlavour.POSIX,
                root=cls._POSIX_ROOT,
                segments=segments,
            )
        if not segments:
            # ``/`` alone: the virtual root of whichever backend was addressed.
            return ClassifiedPath(
                kind=HostPathKind.VIRTUAL,
                flavour=HostPathFlavour.POSIX,
                root=cls._POSIX_ROOT,
            )
        return ClassifiedPath(
            kind=HostPathKind.HOST_ABSOLUTE,
            flavour=HostPathFlavour.POSIX,
            root=cls._POSIX_ROOT,
            segments=segments,
        )

    # --- Windows ------------------------------------------------------------

    @classmethod
    def _classify_windows(cls, text: str) -> ClassifiedPath:
        r"""Classify a Windows-shaped path across every root spelling.

        Handled: ``C:\dir`` and ``C:/dir`` (drive-absolute), ``C:dir``
        (drive-relative → ambiguous), ``\dir`` (current-drive-rooted →
        ambiguous), ``\\server\share\dir`` (UNC), ``\\?\C:\dir`` and
        ``\\?\UNC\server\share\dir`` (extended-length), ``\\.\device`` and any
        other ``\\?\`` root such as ``\\?\GLOBALROOT`` or a volume GUID
        (device namespace → refused).
        """

        if text.startswith(_WinPrefix.DEVICE):
            return cls.refused(
                HostPathRefusal.DEVICE_NAMESPACE, flavour=HostPathFlavour.WINDOWS
            )
        if text.startswith(_WinPrefix.EXTENDED):
            return cls._classify_extended(text[len(_WinPrefix.EXTENDED) :])
        if text.startswith(_WinPrefix.UNC):
            return cls._classify_unc(text[len(_WinPrefix.UNC) :])
        drive = cls._DRIVE.match(text)
        if drive is not None:
            return cls._classify_drive(drive.group(1), text[drive.end() :])
        # A single leading backslash is rooted on the *current* drive, which
        # this process cannot know.
        return cls.refused(
            HostPathRefusal.ROOT_RELATIVE, flavour=HostPathFlavour.WINDOWS
        )

    @classmethod
    def _classify_extended(cls, rest: str) -> ClassifiedPath:
        r"""Classify the remainder of a ``\\?\`` extended-length path."""

        if rest[: len(_WinPrefix.EXTENDED_UNC)].upper() == _WinPrefix.EXTENDED_UNC:
            return cls._classify_unc(rest[len(_WinPrefix.EXTENDED_UNC) :])
        drive = cls._DRIVE.match(rest)
        if drive is not None:
            return cls._classify_drive(drive.group(1), rest[drive.end() :])
        # ``\\?\GLOBALROOT\...``, ``\\?\Volume{GUID}\...``: a namespace, not a
        # folder a user could grant.
        return cls.refused(
            HostPathRefusal.DEVICE_NAMESPACE, flavour=HostPathFlavour.WINDOWS
        )

    @classmethod
    def _classify_drive(cls, letter: str, tail: str) -> ClassifiedPath:
        r"""Classify ``<letter>:<tail>``, separating absolute from drive-relative."""

        if not tail or tail[0] not in _Sep.BOTH:
            # ``C:dir`` and a bare ``C:`` resolve against the per-drive current
            # directory, which this process cannot know. Only ``C:\`` is a root.
            return cls.refused(
                HostPathRefusal.DRIVE_RELATIVE, flavour=HostPathFlavour.WINDOWS
            )
        segments = cls._windows_segments(tail)
        if isinstance(segments, HostPathRefusal):
            return cls.refused(segments, flavour=HostPathFlavour.WINDOWS)
        return ClassifiedPath(
            kind=HostPathKind.HOST_ABSOLUTE,
            flavour=HostPathFlavour.WINDOWS,
            root=f"{letter.upper()}:{_Sep.WINDOWS}",
            segments=segments,
        )

    @classmethod
    def _classify_unc(cls, rest: str) -> ClassifiedPath:
        r"""Classify ``server\share\tail`` from a UNC or extended-UNC path."""

        segments = cls._windows_segments(rest)
        if isinstance(segments, HostPathRefusal):
            return cls.refused(segments, flavour=HostPathFlavour.WINDOWS)
        if len(segments) < 2:
            # A server with no share names no filesystem.
            return cls.refused(
                HostPathRefusal.INCOMPLETE_UNC, flavour=HostPathFlavour.WINDOWS
            )
        server, share = segments[0], segments[1]
        return ClassifiedPath(
            kind=HostPathKind.HOST_ABSOLUTE,
            flavour=HostPathFlavour.WINDOWS,
            root=f"{_WinPrefix.UNC}{server}{_Sep.WINDOWS}{share}",
            segments=segments[2:],
        )

    @classmethod
    def _windows_segments(cls, tail: str) -> tuple[str, ...] | HostPathRefusal:
        """Split a Windows tail, refusing traversal and device-equivalent names."""

        segments = cls._segments(tail, separators=_Sep.BOTH)
        if segments is None:
            return HostPathRefusal.TRAVERSAL
        for segment in segments:
            if segment[-1] in {cls._CURRENT, " "}:
                # ``foo.`` and ``foo `` silently resolve to ``foo``.
                return HostPathRefusal.TRAILING_DOT_OR_SPACE
            stem = segment.split(cls._CURRENT, 1)[0].casefold()
            if stem in cls._RESERVED_WINDOWS_NAMES:
                return HostPathRefusal.RESERVED_NAME
        return segments

    # --- shared -------------------------------------------------------------

    @classmethod
    def _segments(cls, text: str, *, separators: str) -> tuple[str, ...] | None:
        """Split and clean ``text``; ``None`` when it contains a traversal.

        Empty segments and ``.`` are dropped (they address the same node);
        ``..`` is never normalised away — it fails the whole path closed.
        """

        cleaned: list[str] = []
        for raw in re.split(f"[{re.escape(separators)}]", text):
            if not raw or raw == cls._CURRENT:
                continue
            if raw == cls._PARENT:
                return None
            cleaned.append(raw)
        return tuple(cleaned)

    @classmethod
    def _first_segment(cls, text: str) -> str:
        """The first non-empty POSIX segment of ``text`` (``""`` when rootish)."""

        for raw in text.split(_Sep.POSIX):
            if raw:
                return raw
        return ""

    @staticmethod
    def _has_control_characters(text: str) -> bool:
        """True when ``text`` carries a NUL or any other C0 control character."""

        return any(character < " " or character == "\x7f" for character in text)


@dataclass(frozen=True)
class HostRootMatch:
    """A host path covered by one indexed root."""

    #: The opaque key the caller indexed the root under (a mount name here).
    key: str
    #: POSIX, root-relative remainder — the only path form sent to the broker.
    relative: str


class HostRootIndex:
    """Longest-prefix index from granted host roots to opaque binding keys.

    The index exists so a host-absolute path can be answered as
    ``mount + root-relative`` *locally*. The host roots it holds were supplied by
    the user through the grant flow, never learned from the broker (the broker's
    grant projection is deliberately path-free), and they never travel back to
    it. Longest match wins, so a nested grant is preferred over its ancestor.
    """

    def __init__(
        self,
        roots: Mapping[str, ClassifiedPath] | Sequence[tuple[str, ClassifiedPath]],
    ) -> None:
        """Index the host-absolute roots in ``roots``, ignoring any other kind."""

        entries = roots.items() if isinstance(roots, Mapping) else roots
        self._entries: tuple[tuple[str, ClassifiedPath], ...] = tuple(
            (key, root) for key, root in entries if root.is_host
        )

    def cover(self, target: ClassifiedPath) -> HostRootMatch | None:
        """Return the deepest indexed root covering ``target``, or ``None``."""

        if not target.is_host:
            return None
        best: tuple[str, ClassifiedPath] | None = None
        for key, root in self._entries:
            if not root.contains(target):
                continue
            if best is None or len(root.segments) > len(best[1].segments):
                best = (key, root)
        if best is None:
            return None
        return HostRootMatch(key=best[0], relative=target.relative_to(best[1]))


__all__ = (
    "ClassifiedPath",
    "HostPathClassifier",
    "HostPathFlavour",
    "HostPathKind",
    "HostPathMessages",
    "HostPathRefusal",
    "HostRootIndex",
    "HostRootMatch",
)
