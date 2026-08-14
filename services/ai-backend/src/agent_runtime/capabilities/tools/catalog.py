"""First-party tool guidance as browsable files at ``/tools/``.

The measurement this exists for (``tools/harness-bench/FINDINGS.md``): a cold
prompt is **22,304 input tokens, of which 9,159 are tool schemas and 15 are the
user's actual message**. 97% of warm input is cache reads, so a warm run is
nearly free and a cold start pays full price — and cold starts were 71% of
measured spend. Shrinking the *cold* prompt is the lever; the tool block is the
biggest single thing in it.

This module applies the shape
:mod:`agent_runtime.capabilities.mcp.catalog` already proved for MCP — a
descriptor blob replaced by small newline-delimited files the model reaches with
``ls`` / ``grep`` / ``read_file`` — to the tools this repository authors::

    /tools/TOOLS.md                  the index: one line per deferred tool,
                                     plus how to expand one
    /tools/<tool>.md                 that tool's full authored guidance

What is deferred, and what is emphatically not
----------------------------------------------
**Prose defers; the argument schema stays resident.** Measured on the real
descriptions, a deferred tool's footprint splits roughly evenly between the two:

===================  =====  ====  ====
tool                 total  desc  args
===================  =====  ====  ====
publish_artifact      1362   750   607
stage_rowset_write    1311   440   864
revise_artifact        687   522   160
===================  =====  ====  ====

That split is the design. The argument schema is the **machine contract** — field
names, types, enums, what is required. The description is **documentation**.
Keeping the schema resident is what answers the question every progressive
disclosure scheme has to answer: *what happens when the model calls a tool it
never expanded?* Here it simply works, because the call it composes is
well-formed by construction. There is no reject-and-retry protocol to get right,
no auto-expansion round trip to pay for, and no silent failure mode where a
model that never learned to expand looks like a model that is bad at the task.

The cost of a wrong answer here is asymmetric, which is why it is worth stating
plainly: a round trip costs a **full model call**, and deferring something the
model needs on every run trades ~700 resident tokens for ~2,200 cache-read
tokens plus latency. Deferral has to be earned per tool.

Which tools, and why these
--------------------------
Deferred: ``publish_artifact``, ``revise_artifact``, ``stage_rowset_write`` —
the three heaviest first-party descriptions, and all three *episodic*: they act
at the end of a piece of work, at most once or twice per run, and never on a run
that is only answering a question.

Resident, deliberately:

``ask_a_question`` (667 tokens)
    Reached constantly and reached *while a human is waiting*. Paying a round
    trip to learn how to ask a question is the worst possible place to spend
    one.
the filesystem primitives (``ls`` / ``read_file`` / ``grep`` / ``glob`` / …)
    Deferring these would be circular — they are what reads this catalog.
``write_todos`` (997 tokens)
    The heaviest single tool, and the most tempting. It is authored by the
    LangChain middleware, not by this repository, so deferring it means
    rewriting a third-party tool's description at composition time; and it is
    called on nearly every multi-step run, so it is the wrong side of the round
    trip arithmetic above. Left alone on both counts.

Three invariants, each with a test
----------------------------------
1. **One text, two renderings.** The published file is built from the tool's
   OWN ``description`` attribute at composition time, not from a second copy of
   it. A catalog that restated the description would drift from it, and the
   drift would be invisible — the model would be reading documentation for a
   tool that no longer behaves that way.
2. **The mount is the single source of truth.** A run whose ``/tools/`` route
   declined to mount keeps every full description resident. Stubbing a
   description while the file it points at does not exist is strictly worse
   than the blob it replaced — the same failure the MCP catalog's mount rule
   exists to prevent.
3. **Read-only.** The catalog is a projection of authored text. A model that
   could edit it could rewrite, mid-run, the rules a later turn is judged
   against. Writes are refused with a stable message rather than an exception.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

from agent_runtime.prompts.tools import (
    PUBLISH_ARTIFACT_RESIDENT_SUMMARY,
    REVISE_ARTIFACT_RESIDENT_SUMMARY,
    STAGE_ROWSET_WRITE_RESIDENT_SUMMARY,
)

_LOGGER = logging.getLogger(__name__)


class Keys:
    """Stable path segments for the first-party tool catalog."""

    class Dir:
        ROOT: Final[str] = "/tools"

    class File:
        INDEX: Final[str] = "TOOLS.md"

    class Ext:
        MARKDOWN: Final[str] = ".md"

    class Field:
        #: Key added to a FAILED tool result of a deferred tool. Deliberately not
        #: ``message``: the tool's own message is the diagnosis and must not be
        #: overwritten by navigation advice.
        GUIDANCE: Final[str] = "guidance"
        OK: Final[str] = "ok"
        STATUS: Final[str] = "status"

    class Value:
        FAILED: Final[str] = "failed"


class Messages:
    """Model-facing strings this catalog emits. Never interpolates model input."""

    READ_ONLY = (
        "/tools/ is read-only: it is a projection of the tool descriptions this "
        "runtime authored. Nothing was written."
    )
    NOT_FOUND = (
        "No such tool guidance file. Read /tools/TOOLS.md for the list of tools "
        "whose full rules are published here."
    )
    #: An empty listing is answered with a DIRECTIVE, never with success — the
    #: lesson the MCP catalog paid for live: `ls` -> `[]` reads to a model as
    #: "there is nothing here", and it stops.
    EMPTY = (
        "No tool guidance is published for this run; every tool's full "
        "description is already in your tool list."
    )

    @staticmethod
    def read_after_failure(tool_name: str) -> str:
        """Instruction attached to a deferred tool's failed result."""

        return (
            f"This call was refused. The full rules for {tool_name} are in "
            f'read_file("{Keys.Dir.ROOT}/{tool_name}{Keys.Ext.MARKDOWN}") — '
            "read them before retrying rather than retrying the same call."
        )


#: Tool name -> the resident summary that replaces its description.
#:
#: This table IS the deferral decision, and it is deliberately a closed literal
#: rather than a flag or a heuristic: a tool joins it only when someone has
#: written a summary for it, which is the same edit as accepting the round-trip
#: risk. The summaries live in ``prompts.tools`` next to the full text they
#: stand in for, so the two are edited together.
DEFERRED_TOOL_SUMMARIES: Final[Mapping[str, str]] = {
    "publish_artifact": PUBLISH_ARTIFACT_RESIDENT_SUMMARY,
    "revise_artifact": REVISE_ARTIFACT_RESIDENT_SUMMARY,
    "stage_rowset_write": STAGE_ROWSET_WRITE_RESIDENT_SUMMARY,
}


@dataclass(frozen=True)
class ToolGuidanceDocument:
    """One deferred tool's published file plus the stub that points at it."""

    tool_name: str
    #: The tool's own ``description``, verbatim. Never re-authored here.
    full_description: str
    #: The short text that occupies the model surface in its place.
    resident_summary: str

    @property
    def path(self) -> str:
        """Public path of this document, e.g. ``/tools/publish_artifact.md``."""

        return f"{Keys.Dir.ROOT}/{self.tool_name}{Keys.Ext.MARKDOWN}"

    @property
    def index_line(self) -> str:
        """The ``TOOLS.md`` row for this tool: name, purpose, path."""

        return f"- `{self.tool_name}` — {self._purpose()} Full rules: {self.path}"

    def render(self) -> str:
        """Render the published file: a title, the full text, and how to act."""

        return (
            f"# {self.tool_name}\n\n"
            f"{self.full_description}\n\n"
            "---\n\n"
            "The argument schema for this tool is already complete in your tool "
            f"list; this file is the usage guidance only. Call `{self.tool_name}` "
            "directly once you have read what you needed.\n"
        )

    def _purpose(self) -> str:
        """First sentence of the resident summary, as the index one-liner.

        Derived rather than authored a third time: an index that restated the
        purpose would be a third copy of the same claim, and the one that drifts
        is always the one nobody is looking at.
        """

        head = self.resident_summary.strip().split("\n", 1)[0]
        sentence, separator, _ = head.partition(". ")
        return f"{sentence}." if separator else head


class ToolGuidanceCatalog:
    """A frozen ``/tools/`` filesystem: path -> content, decided at run start.

    Frozen is the whole difference from :class:`McpServerCatalog`, and it is why
    this is a separate small class rather than a second configuration of that
    one. The MCP catalog carries a two-tier seed/build model, per-server
    directories, and mutation on ``load_mcp_server`` because a connector's tools
    are discovered over the network mid-run. First-party descriptions are known
    at import time and cannot change during a run, so every one of those
    mechanisms would be a parameter that only ever takes one value.
    """

    def __init__(self, documents: Sequence[ToolGuidanceDocument]) -> None:
        self._documents = tuple(documents)
        self._files = self._render(self._documents)

    @classmethod
    def of_tools(cls, tools: Iterable[object]) -> ToolGuidanceCatalog | None:
        """Build the catalog from the composed tool adapters, or ``None``.

        ``None`` when no deferred tool is present this run — a gated capability
        that is off has no description to defer, and publishing an empty
        ``/tools/`` would advertise a directory with nothing in it.

        Reads each tool's own ``name`` and ``description``, which is invariant 1:
        the file and the surface are two renderings of one string. A tool whose
        description cannot be read is skipped rather than published empty.
        """

        documents: list[ToolGuidanceDocument] = []
        for tool in tools:
            document = cls._document(tool)
            if document is not None:
                documents.append(document)
        if not documents:
            return None
        return cls(tuple(sorted(documents, key=lambda item: item.tool_name)))

    @classmethod
    def _document(cls, tool: object) -> ToolGuidanceDocument | None:
        """Return the document for ``tool``, or ``None`` when it is not deferred."""

        if tool is None:
            return None
        try:
            name = str(getattr(tool, "name", "")).strip()
            summary = DEFERRED_TOOL_SUMMARIES.get(name)
            if summary is None:
                return None
            full = str(getattr(tool, "description", "")).strip()
            if not full:
                return None
            return ToolGuidanceDocument(
                tool_name=name,
                full_description=full,
                resident_summary=summary,
            )
        except Exception:  # noqa: BLE001 — an unreadable tool keeps its own text
            _LOGGER.debug(
                "Could not project a tool into the /tools/ catalog; it keeps its "
                "full resident description.",
                exc_info=True,
            )
            return None

    @property
    def documents(self) -> tuple[ToolGuidanceDocument, ...]:
        """Every published document, ordered by tool name."""

        return self._documents

    def resident_description(self, tool: object) -> str | None:
        """The stub that replaces ``tool``'s description, or ``None`` to leave it.

        ``None`` for any tool this catalog did not publish, which is what makes
        the call site safe to apply unconditionally: a tool whose file does not
        exist keeps the description it authored.
        """

        name = str(getattr(tool, "name", "")).strip()
        for document in self._documents:
            if document.tool_name == name:
                return document.resident_summary
        return None

    def snapshot(self) -> Mapping[str, str]:
        """Return ``{public_path: content}`` for the whole catalog."""

        return dict(self._files)

    def directories(self) -> tuple[str, ...]:
        """Directories that exist even when empty. ``/tools`` is flat."""

        return (Keys.Dir.ROOT,)

    def annotated_failure(self, tool_name: str, result: object) -> object:
        """Attach the read-the-file instruction to a FAILED deferred result.

        This is the second half of the "what if the model never expanded?"
        answer. The first half is structural (the argument schema is resident,
        so the call is well-formed); this half covers the rules a JSON schema
        cannot express — ``publish_artifact``'s exactly-one-of-content rule,
        ``revise_artifact``'s compare-and-append conflict, the row/diff accuracy
        check — where the tool refuses and the model would otherwise retry the
        identical call.

        Never raises and never rewrites an existing key: the tool's own
        ``message`` is the diagnosis, and this only adds navigation next to it.
        """

        try:
            if not isinstance(result, Mapping) or not self._is_failure(result):
                return result
            if Keys.Field.GUIDANCE in result:
                return result
            annotated: dict[str, Any] = dict(result)
            annotated[Keys.Field.GUIDANCE] = Messages.read_after_failure(tool_name)
        except Exception:  # noqa: BLE001 — a hint is never worth a failed call
            _LOGGER.debug(
                "Could not annotate a deferred tool failure with its guidance "
                "path; returning the result untouched.",
                exc_info=True,
            )
            return result
        return annotated

    @staticmethod
    def _is_failure(result: Mapping[str, Any]) -> bool:
        """Whether ``result`` is one of the two failure shapes builtins return.

        ``{"status": "failed"}`` (the artifact tools) and ``{"ok": False}``
        (the staging tool). Both are read, rather than one being normalized,
        because normalizing a shipped tool's result shape to serve an
        observability hint is the tail wagging the dog.
        """

        if result.get(Keys.Field.STATUS) == Keys.Value.FAILED:
            return True
        return result.get(Keys.Field.OK) is False

    @classmethod
    def _render(cls, documents: Sequence[ToolGuidanceDocument]) -> Mapping[str, str]:
        """Render every document plus the index into ``{path: content}``."""

        files = {document.path: document.render() for document in documents}
        files[f"{Keys.Dir.ROOT}/{Keys.File.INDEX}"] = cls._index(documents)
        return files

    @classmethod
    def _index(cls, documents: Sequence[ToolGuidanceDocument]) -> str:
        """Render ``TOOLS.md``.

        Exists so that a model probing ``ls /tools`` or grepping for a keyword
        lands somewhere that explains itself. The MCP catalog learned this the
        expensive way: a directory that answers a probe with nothing teaches the
        model the capability is absent.
        """

        rows = "\n".join(document.index_line for document in documents)
        return (
            "# Tool guidance\n\n"
            "The tools below carry a SHORT description in your tool list and "
            "their full usage rules here. Their argument schemas are already "
            "complete in the tool list, so you can call any of them directly — "
            "read a file when the call is non-obvious, or when one was "
            "refused.\n\n"
            f"{rows}\n"
        )


__all__ = [
    "DEFERRED_TOOL_SUMMARIES",
    "Keys",
    "Messages",
    "ToolGuidanceCatalog",
    "ToolGuidanceDocument",
]
