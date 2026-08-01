r"""The tool surface a host path has to survive before any rule can judge it.

The gap this closes
-------------------
``agent_runtime.capabilities.desktop.host_path`` classifies all 26 Windows path
shapes correctly, and ``host_filesystem`` turns grants into deepagents
``FilesystemPermission`` rules. Neither was ever reached on Windows. Deep Agents'
filesystem tools validate the model's path argument with
``deepagents.backends.utils.validate_path`` FIRST — before the permission check,
before the backend, before the interrupt predicate — and that validator:

* raises ``ValueError`` on any drive-absolute path (``C:\Users\p\Downloads``),
  so the tool returns "Windows absolute paths are not supported" and the agent
  cannot address a host folder AT ALL on the platform half the product targets;
* rewrites UNC and extended paths to a ``//``-rooted POSIX form
  (``\\server\share`` → ``//server/share``).

That second behaviour is worse than the first, and it is not hypothetical — it
was measured against the real rule set and the real tools. deepagents decides
whether a bulk filesystem call (``ls`` / ``glob`` / ``grep``) needs consent with
``_paths_overlap(call_path, rule_anchor)``, which compares
:class:`pathlib.PurePosixPath` components. ``PurePosixPath("//x")`` has root
``"//"``, is NOT ``is_relative_to("/")``, and therefore overlaps nothing — so the
catch-all ``interrupt`` rule anchored at ``/`` never fires. The tool then runs,
``_check_fs_permission`` returns ``interrupt`` (which is not ``deny``), and the
real filesystem backend answers. Doubling one slash reads an ungranted folder
with no consent card::

    ls("/Users/p/Downloads")   -> interrupt fires -> user is asked
    ls("//Users/p/Downloads")  -> interrupt SKIPPED -> real listing returned

The fix, and the ONE hook that is early enough
----------------------------------------------
Both problems are the same problem: the tool surface understands one path
grammar and it is not the host's. So a host path is translated into the
canonical spelling (:attr:`ClassifiedPath.canonical`) that ``validate_path``
returns unchanged and the permission globs can match, and
:class:`NativeHostPathBackend` undoes that encoding at the backend so the host
is still opened in its own grammar. Encode-at-the-tool and decode-at-the-backend
are one contract, which is why they live in one module.

WHERE the translation happens is not a matter of taste. ``HumanInTheLoopMiddleware``
— the consent gate a ``mode="interrupt"`` rule installs — does not wrap the tool
call at all: it implements ``after_model`` and decides on the tool-call arguments
the MODEL emitted, before the tool node and therefore before every
``wrap_tool_call`` in the stack. This was measured, not assumed; a first attempt
that translated in ``wrap_tool_call`` left the ``//`` bypass wide open while
looking correct from inside the tool. So the translation is applied in
``wrap_model_call``, which runs around the model node and can rewrite the
response's tool calls before ``after_model`` ever sees them.

``wrap_tool_call`` is still implemented, and not as belt-and-braces: the consent
card offers an ``edit`` decision, and an edited tool call goes straight to the
tool node without passing the model hook again. It is also where a refused shape
is turned into a typed refusal, since only a tool wrapper can answer with a
``ToolMessage``.

Refused shapes are deliberately NOT translated. Every category this exists to
fail closed on — a traversal, a device namespace, a reserved device name, a
drive-relative remainder, ``~`` — is a shape deepagents' own gate already
declines to interrupt on (``validate_path`` raises, or the ``//`` root overlaps
no anchor), so leaving it as the model wrote it means no consent card is raised
before the refusal, and the transcript still records what was actually asked
for. Two shapes the classifier refuses do still draw a generic consent card
first, because upstream accepts them and the catch-all rule is anchored at
``/``: a path carrying control characters, and a current-drive-rooted
``\Users\p``. Both are refused when the tool runs whatever the answer is, so
they fail closed either way — the cost is a spurious card, not access.

Desktop only. This middleware is installed on exactly the signal that installs
the host filesystem rules; a run without those rules composes byte-identically
to before it existed.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import replace
from typing import TYPE_CHECKING, Any, Final

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import (
    ModelRequest,
    ModelResponse,
    ToolCallRequest,
)
from langchain_core.messages import BaseMessage, ToolCall, ToolMessage
from langgraph.types import Command

from agent_runtime.capabilities.desktop.host_path import (
    ClassifiedPath,
    HostPathClassifier,
    HostPathKind,
    HostPathMessages,
    HostPathRefusal,
)

if TYPE_CHECKING:
    from deepagents.backends.protocol import (
        EditResult,
        FileInfo,
        GlobResult,
        GrepMatch,
        GrepResult,
        LsResult,
        ReadResult,
        WriteResult,
    )

ToolHandlerItem = ToolMessage | Command[Any]
ToolHandlerResult = ToolHandlerItem | list[ToolHandlerItem]
ToolHandler = Callable[[ToolCallRequest], ToolHandlerResult]
AsyncToolHandler = Callable[[ToolCallRequest], Awaitable[ToolHandlerResult]]


class HostFsToolArgs:
    """Which arguments of each Deep Agents filesystem tool name a PATH.

    Mirrors ``deepagents.middleware._fs_interrupt._FS_TOOL_PATH_ARGS`` — the map
    deepagents itself uses to find the path argument of a filesystem call — and
    is spelled out here rather than imported so a version skew shows up as a
    failing contract test instead of as a silently narrower rewrite.

    ``glob`` carries two: an ABSOLUTE ``pattern`` redirects the search away from
    ``path`` entirely (deepagents gates on it for exactly that reason), so
    leaving it in host grammar would send an untranslated path to the backend.
    ``grep``'s own ``glob`` argument is a filename filter, not a path, and is
    deliberately absent.
    """

    _BY_TOOL: Final[Mapping[str, tuple[str, ...]]] = {
        "ls": ("path",),
        "read_file": ("file_path",),
        "write_file": ("file_path",),
        "edit_file": ("file_path",),
        "glob": ("path", "pattern"),
        "grep": ("path",),
    }

    @classmethod
    def for_tool(cls, name: str) -> tuple[str, ...]:
        """Path-bearing argument names for ``name``; empty for any other tool."""

        return cls._BY_TOOL.get(name, ())

    @classmethod
    def tool_names(cls) -> frozenset[str]:
        """Every filesystem tool this middleware translates for."""

        return frozenset(cls._BY_TOOL)


class HostPathToolMiddleware(AgentMiddleware):
    r"""Translates and screens filesystem tool paths before deepagents runs.

    Installed through ``create_deep_agent(middleware=...)`` and through the
    harness profile's universal factories, so subagents — which inherit the
    parent's filesystem permissions — are covered by the same translation rather
    than keeping a second, untranslated door to the same tools.

    Only arguments that are host-shaped or POSIX-absolute are touched. A
    relative path, a bare glob (``*.md``), a virtual namespace (``/memories/…``)
    and a ``None`` search root all pass through byte-for-byte, so the non-host
    surface behaves exactly as it did before this existed.
    """

    # --- the model response: the only layer the consent gate has not read yet --

    def wrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], ModelResponse[Any]],
    ) -> ModelResponse[Any]:
        """Canonicalise the model's filesystem calls before consent is decided."""

        return self._translated_response(handler(request))

    async def awrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], Awaitable[ModelResponse[Any]]],
    ) -> ModelResponse[Any]:
        """Canonicalise the model's filesystem calls before consent is decided."""

        return self._translated_response(await handler(request))

    def _translated_response(self, response: ModelResponse[Any]) -> ModelResponse[Any]:
        """``response`` with every filesystem tool call's paths canonicalised.

        This is the layer the consent gate reads. ``HumanInTheLoopMiddleware``
        implements ``after_model``, so it judges the tool calls sitting on this
        response — translating any later, in a tool wrapper, is translating
        after the decision has already been made.
        """

        messages = getattr(response, "result", None)
        if not isinstance(messages, list):
            return response
        translated = [self._translated_message(message) for message in messages]
        if all(new is old for new, old in zip(translated, messages, strict=True)):
            return response
        return replace(response, result=translated)

    def _translated_message(self, message: BaseMessage) -> BaseMessage:
        """One message with canonical filesystem tool-call arguments."""

        calls = getattr(message, "tool_calls", None)
        if not calls:
            return message
        rewritten = [self._translated_call(call) for call in calls]
        if all(new is old for new, old in zip(rewritten, calls, strict=True)):
            return message
        return message.model_copy(update={"tool_calls": rewritten})

    def _translated_call(self, call: ToolCall) -> ToolCall:
        """One tool call with canonical path arguments, or itself unchanged."""

        args = self._translated_args(call.get("name"), call.get("args"))
        return call if args is None else {**call, "args": args}

    @classmethod
    def _translated_args(cls, name: object, args: object) -> dict[str, Any] | None:
        """Canonical arguments for a filesystem call; ``None`` when unchanged.

        A refused shape is left exactly as the model wrote it ON PURPOSE — see
        the module header. Translating it would hand the consent gate a path it
        CAN match, turning a shape that must never resolve into a consent card;
        left alone, deepagents' own gate declines it and the tool wrapper
        refuses it.
        """

        screened = cls._screened(name, args)
        return None if isinstance(screened, ClassifiedPath) else screened

    # --- the tool call: where a refusal can be answered, and where an edited
    #     (human-supplied) call enters without passing the model hook ----------

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: ToolHandler,
    ) -> ToolHandlerResult:
        """Screen and translate one synchronous filesystem call."""

        prepared = self._prepared(request)
        if isinstance(prepared, ToolMessage):
            return prepared
        return handler(prepared)

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: AsyncToolHandler,
    ) -> ToolHandlerResult:
        """Screen and translate one asynchronous filesystem call."""

        prepared = self._prepared(request)
        if isinstance(prepared, ToolMessage):
            return prepared
        return await handler(prepared)

    def _prepared(self, request: ToolCallRequest) -> ToolCallRequest | ToolMessage:
        """The call as deepagents must see it, or the refusal that replaces it.

        The translation is repeated here rather than assumed from
        ``wrap_model_call`` because a call can reach the tool node without
        passing that hook: approving a consent card with an ``edit`` decision
        replaces the arguments and dispatches them directly. Both entry points
        therefore screen, and the operation is idempotent — a canonical path
        classifies back to itself.
        """

        call = request.tool_call
        screened = self._screened(call.get("name"), call.get("args"))
        if isinstance(screened, ClassifiedPath):
            return self._refusal(request, screened.refusal)
        if screened is None:
            return request
        return request.override(tool_call={**call, "args": screened})

    @classmethod
    def _screened(
        cls, name: object, args: object
    ) -> dict[str, Any] | ClassifiedPath | None:
        """Screen one call's path arguments.

        Returns the refusing classification for a shape that must never
        resolve, the rewritten argument mapping when translation changed
        something, or ``None`` when the call is already in the form deepagents
        needs.
        """

        arg_names = HostFsToolArgs.for_tool(str(name or ""))
        if not arg_names or not isinstance(args, Mapping):
            return None
        translated = dict(args)
        changed = False
        for arg_name in arg_names:
            raw = args.get(arg_name)
            if not isinstance(raw, str):
                # A missing search root is a legitimate call shape (``grep``
                # without ``path``); anything else non-string is malformed and
                # is left for the tool's own schema handling to reject.
                continue
            outcome = cls._translated(raw)
            if isinstance(outcome, ClassifiedPath):
                return outcome
            if outcome != raw:
                translated[arg_name] = outcome
                changed = True
        return translated if changed else None

    @staticmethod
    def _translated(raw: str) -> str | ClassifiedPath:
        """One argument's canonical form, or the classification that refuses it."""

        if not (HostPathClassifier.is_host_shaped(raw) or raw.startswith("/")):
            # Relative remainders and bare glob patterns are addressed against
            # whichever backend route answers them. Rewriting one would re-point
            # it at the host, which is the opposite of this middleware's job.
            return raw
        classified = HostPathClassifier.classify(raw)
        if classified.kind in {HostPathKind.UNSAFE, HostPathKind.HOST_AMBIGUOUS}:
            return classified
        return classified.canonical

    @staticmethod
    def _refusal(
        request: ToolCallRequest, refusal: HostPathRefusal | None
    ) -> ToolMessage:
        """The tool result a refused shape produces instead of an interrupt.

        Shaped like deepagents' own path-validation failure (``Error: …`` with
        ``status="error"``) so the model reads one consistent failure form, and
        carrying only :class:`HostPathMessages` copy, which repeats nothing about
        the host beyond what the caller already supplied.
        """

        call = request.tool_call
        return ToolMessage(
            content=f"Error: {HostPathMessages.for_refusal(refusal)}",
            name=str(call.get("name") or ""),
            tool_call_id=str(call.get("id") or ""),
            status="error",
        )


class NativeHostPathBackend:
    r"""Undoes the canonical encoding so the host is opened in its own grammar.

    Wraps the backend that answers every unrouted path — deepagents'
    ``FilesystemBackend(virtual_mode=False)`` on the desktop — and translates in
    both directions:

    * arguments in, ``/C:/Users/p`` → ``C:\Users\p``, because that is what the
      operating system can open;
    * result paths out, back to the canonical spelling, because those paths are
      re-checked against the permission rules (which are canonical) and are then
      handed to the model, which will address them again on its next call.

    On POSIX both directions are the identity — ``HostPathClassifier.native``
    changes nothing whose first segment is neither a drive nor the UNC marker —
    so this wrapper is provably inert on macOS and Linux.

    Delegating rather than subclassing ``BackendProtocol``, for the reason
    ``host_route.HostPathGuardBackend`` documents at length: the protocol is an
    ABC carrying concrete implementations, so subclassing would shadow the
    wrapped backend for every operation this class does not name and normal
    attribute lookup would never reach ``__getattr__``.
    """

    #: Deep Agents' own ``read`` defaults, mirrored so a delegated call that
    #: omits them behaves identically on both sides of the translation.
    _READ_OFFSET: Final = 0
    _READ_LIMIT: Final = 2000

    def __init__(self, inner: object) -> None:
        """Wrap ``inner``, translating host paths in and result paths out."""

        self._inner = inner

    @property
    def inner(self) -> object:
        """The wrapped host backend (a real filesystem on the desktop)."""

        return self._inner

    def __getattr__(self, name: str) -> Any:
        """Delegate every operation this wrapper does not translate."""

        return getattr(self._inner, name)

    # --- list ---------------------------------------------------------------

    def ls(self, path: str) -> LsResult:
        """List ``path`` on the host, returning canonical entry paths."""

        return self._with_entries(self._inner.ls(self._native(path)))  # type: ignore[attr-defined]

    async def als(self, path: str) -> LsResult:
        """List ``path`` on the host, returning canonical entry paths."""

        return self._with_entries(await self._inner.als(self._native(path)))  # type: ignore[attr-defined]

    # --- read ---------------------------------------------------------------

    def read(
        self,
        file_path: str,
        offset: int = _READ_OFFSET,
        limit: int = _READ_LIMIT,
    ) -> ReadResult:
        """Read ``file_path`` from the host."""

        return self._inner.read(self._native(file_path), offset, limit)  # type: ignore[attr-defined]

    async def aread(
        self,
        file_path: str,
        offset: int = _READ_OFFSET,
        limit: int = _READ_LIMIT,
    ) -> ReadResult:
        """Read ``file_path`` from the host."""

        return await self._inner.aread(self._native(file_path), offset, limit)  # type: ignore[attr-defined]

    # --- search -------------------------------------------------------------

    def glob(self, pattern: str, path: str | None = None) -> GlobResult:
        """Glob under ``path`` on the host, returning canonical match paths."""

        return self._with_matches(
            self._inner.glob(self._native(pattern), self._native_or_none(path))  # type: ignore[attr-defined]
        )

    async def aglob(self, pattern: str, path: str | None = None) -> GlobResult:
        """Glob under ``path`` on the host, returning canonical match paths."""

        return self._with_matches(
            await self._inner.aglob(  # type: ignore[attr-defined]
                self._native(pattern), self._native_or_none(path)
            )
        )

    def grep(
        self, pattern: str, path: str | None = None, glob: str | None = None
    ) -> GrepResult:
        """Grep under ``path`` on the host, returning canonical match paths."""

        return self._with_grep_matches(
            self._inner.grep(pattern, self._native_or_none(path), glob)  # type: ignore[attr-defined]
        )

    async def agrep(
        self, pattern: str, path: str | None = None, glob: str | None = None
    ) -> GrepResult:
        """Grep under ``path`` on the host, returning canonical match paths."""

        return self._with_grep_matches(
            await self._inner.agrep(pattern, self._native_or_none(path), glob)  # type: ignore[attr-defined]
        )

    # --- mutation -----------------------------------------------------------
    #
    # Every host write is denied by the rule set in the tool layer before it can
    # reach here; the one exception is the agent's own scratch directory inside
    # a writable grant. Translating anyway keeps the wrapper total, so nothing
    # depends on that rule set staying exactly as it is today.

    def write(self, file_path: str, content: str) -> WriteResult:
        """Write ``file_path`` on the host."""

        return self._with_path(self._inner.write(self._native(file_path), content))  # type: ignore[attr-defined]

    async def awrite(self, file_path: str, content: str) -> WriteResult:
        """Write ``file_path`` on the host."""

        return self._with_path(
            await self._inner.awrite(self._native(file_path), content)  # type: ignore[attr-defined]
        )

    def edit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,  # noqa: FBT001, FBT002
    ) -> EditResult:
        """Edit ``file_path`` on the host."""

        return self._with_path(
            self._inner.edit(  # type: ignore[attr-defined]
                self._native(file_path), old_string, new_string, replace_all
            )
        )

    async def aedit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,  # noqa: FBT001, FBT002
    ) -> EditResult:
        """Edit ``file_path`` on the host."""

        return self._with_path(
            await self._inner.aedit(  # type: ignore[attr-defined]
                self._native(file_path), old_string, new_string, replace_all
            )
        )

    # --- translation --------------------------------------------------------

    @staticmethod
    def _native(path: str) -> str:
        """A canonical tool path in the host's own grammar."""

        return HostPathClassifier.native(path)

    @classmethod
    def _native_or_none(cls, path: str | None) -> str | None:
        """``_native`` for an optional search root, preserving ``None``."""

        return None if path is None else cls._native(path)

    @staticmethod
    def _canonical(path: str) -> str:
        """A host path in the one spelling the rules and the model see."""

        return HostPathClassifier.classify(path).canonical or path

    @classmethod
    def _with_entries(cls, result: LsResult) -> LsResult:
        """``result`` with every listed path rendered canonically."""

        if result.entries is None:
            return result
        result.entries = [cls._file_info(entry) for entry in result.entries]
        return result

    @classmethod
    def _with_matches(cls, result: GlobResult) -> GlobResult:
        """``result`` with every matched path rendered canonically."""

        if result.matches is None:
            return result
        result.matches = [cls._file_info(entry) for entry in result.matches]
        return result

    @classmethod
    def _with_grep_matches(cls, result: GrepResult) -> GrepResult:
        """``result`` with every matching file's path rendered canonically."""

        if result.matches is None:
            return result
        result.matches = [cls._grep_match(match) for match in result.matches]
        return result

    @classmethod
    def _with_path(cls, result: EditResult | WriteResult) -> EditResult | WriteResult:
        """``result`` with the written/edited path rendered canonically."""

        if result.path is not None:
            result.path = cls._canonical(result.path)
        return result

    @classmethod
    def _file_info(cls, entry: FileInfo) -> FileInfo:
        """One listing entry with a canonical ``path``, other fields untouched."""

        path = entry.get("path")
        if not isinstance(path, str):
            return entry
        return {**entry, "path": cls._canonical(path)}

    @classmethod
    def _grep_match(cls, match: GrepMatch) -> GrepMatch:
        """One grep match with a canonical ``path``, other fields untouched."""

        path = match.get("path")
        if not isinstance(path, str):
            return match
        return {**match, "path": cls._canonical(path)}


__all__ = (
    "HostFsToolArgs",
    "HostPathToolMiddleware",
    "NativeHostPathBackend",
)
