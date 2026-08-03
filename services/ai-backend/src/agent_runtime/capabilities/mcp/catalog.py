"""MCP filesystem catalog — a connector's descriptors as browsable files (P0).

A real Linear descriptor arrives as **70,465 bytes, 52 tools, and zero
newlines**. Handing that to the model is not a transport problem; it is a
context-engineering failure. The blob exceeds the admission budget, gets
offloaded to the content-addressed store, and the preview the model receives is
``"\\n".join(content.splitlines()[:10])[:2000]`` — which, with zero newlines,
is the first 2000 characters of tool #1. Re-reading at an offset returns the
same 2000 characters, and ``grep`` over the reference returns nothing. The
model cannot reach ``list_issues``, so it correctly refuses to invent an answer
and the run completes with **EMPTY SUCCESS**.

The fix is not to make a 70 KB single-line document navigable. It is to stop
producing it. This module materializes each loaded server as small, ordinary,
newline-delimited files that the model reaches with the primitives it already
has — ``ls``, ``grep``, ``read_file``::

    /mcp/<server>/SERVER.md              always-loaded tier: what the server is,
                                         auth state, and a bounded INDEX of every
                                         tool (name + action class + one line)
    /mcp/<server>/tools/<tool>.json      one tool: full input schema, output
                                         shape, action class, how to invoke it
    /mcp/<server>/resources/<name>.json  one MCP resource each

Finding ``list_issues`` becomes ``grep "issue" /mcp/linear/`` followed by
``read_file /mcp/linear/tools/list_issues.json`` — two small calls instead of
one unreachable 70 KB reference. ``SERVER.md`` is the Skills ``SKILL.md``
analogue: the only tier loaded up front, with everything else pulled on demand.

Three invariants this module holds, each with a test:

1. **Provider-agnostic.** Nothing here branches on the model provider. The
   catalog is built from a filesystem, ``ls``, ``grep`` and ``read_file`` —
   primitives every provider already exposes.
2. **The action class is the PDP's, not a second opinion.** Every index row and
   every tool file reads its READ / WRITE / DESTRUCTIVE class from
   :meth:`McpCapabilityDescriptorSource.action_for` — the same derivation the
   policy decision point polices at dispatch. A catalog that advertised its own
   classification could tell the model a write is a read.
3. **No credentials, ever.** The catalog is projected from
   :class:`McpToolDescriptor` / :class:`McpResourceDescriptor` / the compact
   :class:`McpServerCard`, none of which carry an endpoint, a header, or a
   token — so connection material cannot be represented here by construction.
   Every rendered byte is additionally scanned by
   :class:`SecretShapeScanner` for ciphertext shapes a hostile server could try
   to smuggle through a tool description.

Discovery is a READ of an ai-backend-authored artifact and grants nothing:
invocation still goes through the existing ``call_mcp_tool`` gateway, which
carries the PDP decision and the approval interrupt.

Two tiers, and the seed tier is the one that fixes the live bug
--------------------------------------------------------------
The first version of this module wrote nothing until the model called
``load_mcp_server``. But the tool's own description advertises
``/mcp/<server>/`` *before* any load, so a model that reads it and probes ``ls
/mcp`` first got success-with-zero-entries — which reads as "this connector has
no browsable filesystem", and the model stopped. So the catalog now has two
tiers:

``seed``
    Written at RUN START from the authorized server cards the registry already
    holds. No network call, no tool discovery — just ``SERVER.md`` naming the
    server and saying how to get the tool detail. ``ls /mcp`` is therefore
    non-empty from the first model turn of a fresh chat.
``build``
    Written when ``load_mcp_server`` returns descriptors. Replaces that one
    server's directory with the full ``SERVER.md`` index + ``tools/`` +
    ``resources/``.

:meth:`McpCatalogSeedPlanner.plan` is what keeps the two from fighting: a seed
pass never overwrites a server that has already been loaded (which would
silently un-load turn 1's tool list on turn 2), and it prunes the directories of
servers the user is no longer authorized for.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Mapping, Sequence
from enum import StrEnum
from typing import Any, Final, Protocol, runtime_checkable

from pydantic import Field, field_validator

from agent_runtime.hyperparameters.contracts import McpCatalogHyperparameters

from agent_runtime.capabilities.mcp.cards import (
    LoadedMcpServer,
    McpResourceDescriptor,
    McpServerCard,
    McpToolDescriptor,
)
from agent_runtime.capabilities.mcp.descriptor_source import (
    McpCapabilityDescriptorSource,
)
from agent_runtime.capabilities.mcp.files import (
    McpConfigSecretLeak,
    SecretShapeScanner,
)
from agent_runtime.capabilities.policy.contracts import Action
from agent_runtime.execution.contracts import RuntimeContract

_LOGGER = logging.getLogger(__name__)


class Log:
    """Structured events emitted by the catalog, in one place.

    Named once here so a live failure is greppable from the log alone and the
    in-memory and file-backed stores cannot drift into different spellings — a
    log line that means two things depending on which image wrote it is not
    evidence. Only server slugs, tiers, counts and byte sizes are ever logged;
    never a description, a schema, or anything else a connector authored.
    """

    PUBLISHED = (
        "mcp_catalog.published server=%s tier=%s tools=%d resources=%d "
        "bytes=%d files=%d"
    )
    SEEDED = "mcp_catalog.seeded seeded=%d retained=%d pruned=%d servers=%s"
    SEED_REFUSED = "mcp_catalog.seed_refused server=%s"
    #: Placeholder for an empty list of slugs, so the field is never blank.
    NONE = "-"

    @classmethod
    def published(cls, catalog: "McpServerCatalog") -> None:
        """Emit the one line that says a server's directory was (re)written."""

        _LOGGER.info(
            cls.PUBLISHED,
            catalog.server_name,
            catalog.tier.value,
            catalog.pointer.tool_count,
            catalog.pointer.resource_count,
            catalog.byte_size,
            len(catalog.files),
        )


class Keys:
    """Stable path segments and payload keys for the MCP catalog."""

    class Dir:
        ROOT = "/mcp"
        TOOLS = "tools"
        RESOURCES = "resources"

    class File:
        SERVER_MARKDOWN = "SERVER.md"

    class Ext:
        JSON = ".json"

    class Field:
        ACTION = "action"
        DESCRIPTION = "description"
        INPUT_SCHEMA = "input_schema"
        INVOKE_WITH = "invoke_with"
        MIME_TYPE = "mime_type"
        NAME = "name"
        OUTPUT_SHAPE = "output_shape"
        READ_ONLY = "read_only"
        REQUIRED_SCOPES = "required_scopes"
        RISK_LEVEL = "risk_level"
        SERVER_NAME = "server_name"
        TOOL = "tool"
        TOOL_NAME = "tool_name"
        URI = "uri"

    class Encoding:
        UTF_8 = "utf-8"


class Values:
    """Stable string values emitted into catalog artifacts."""

    NEWLINE = "\n"
    ELLIPSIS = "…"
    #: Between a tool's name/class prefix and its one-line summary.
    INDEX_SEPARATOR = " — "
    #: How a catalog file tells the model to actually run something.
    #:
    #: There is no umbrella tool any more: per-tool registration puts each
    #: connector tool on the model surface under its OWN name, so a descriptor
    #: is invoked by calling that name directly. This string used to be
    #: `call_mcp_tool`, and a live run showed exactly what a stale value costs —
    #: the model read all 52 Linear descriptors, then called a tool that no
    #: longer exists and returned a successful-looking nothing. Discovery still
    #: never dispatches; the POLICY stage still keeps the decision.
    DISPATCH_GUIDANCE = "call the tool by its own name"
    #: The tool that turns a seeded stub into the full tool tree.
    LOAD_TOOL = "load_mcp_server"
    #: Joins server slugs in one log line.
    LOG_SEPARATOR = ","
    #: Characters that survive into a generated file name.
    SAFE_FILE_NAME = re.compile(r"[^a-z0-9._-]+")
    #: Collapsed to a single space when a description becomes a one-liner.
    WHITESPACE_RUN = re.compile(r"\s+")


class Limits:
    """Size bounds for the always-loaded tier.

    The four byte budgets are the ``mcp_catalog`` section of
    ``hyperparameters.json``: tuning how much of a connector's index fits in the
    always-loaded tier changes only how the agent reads, never a contract a peer
    relies on. ``MIN_NEWLINES_PER_FILE`` is deliberately NOT among them.
    """

    #: ``SERVER.md`` is loaded on every disclosure, so it is budgeted. The
    #: summary column flexes to fit; tool NAMES never do — a hidden tool is the
    #: exact failure this module exists to remove, so a server with enough
    #: tools to blow the budget on names alone produces a larger file rather
    #: than a shorter list.
    _SECTION: Final = McpCatalogHyperparameters()

    SERVER_MARKDOWN_MAX_BYTES = _SECTION.server_markdown_max_bytes
    #: Reserved for the ``SERVER.md`` preamble (title, status block, the
    #: three-step how-to). The preamble is fixed text plus a bounded
    #: description, so this is a ceiling rather than an estimate.
    HEADER_RESERVE_BYTES = _SECTION.header_reserve_bytes
    #: Widest one-line summary rendered in the index, before the budget shrinks
    #: it further.
    INDEX_SUMMARY_MAX_BYTES = _SECTION.index_summary_max_bytes
    #: Below this the summary column is dropped entirely rather than rendered
    #: as a useless stub.
    INDEX_SUMMARY_MIN_BYTES = _SECTION.index_summary_min_bytes
    #: Every emitted file must be line-oriented; deepagents' ``read_file``
    #: offset/limit are SOURCE LINES, so a single-line file is unreadable in
    #: exactly the way the 70 KB blob was. An invariant, not a budget: tuning it
    #: to 0 reintroduces the 70 KB single-line bug the catalog exists to remove,
    #: so it stays a code constant and never enters the document.
    MIN_NEWLINES_PER_FILE = 2


class Messages:
    """Safe, model-facing copy emitted by the catalog."""

    READ_ONLY = "The /mcp/ catalog is read-only; it is written when a server loads."
    NOT_FOUND = "No MCP catalog file at this path. Run `ls /mcp/` to see what loaded."
    SECRET_SHAPED = "The MCP server returned descriptors that could not be published."
    #: ``ls /mcp`` with nothing seeded. Deliberately an ERROR and not an empty
    #: success: success-with-zero-entries is what taught the model that the
    #: connector had no browsable filesystem, and it stopped instead of asking.
    NO_SERVERS = (
        "No MCP servers are connected for this user, so /mcp/ is empty. "
        "Nothing here can be called — tell the user they need to connect a "
        "connector rather than guessing a server or tool name."
    )
    #: ``ls /mcp/<unknown>``. Same reasoning: a directive, not silent success.
    UNKNOWN_SERVER = (
        "No MCP server by that name. Run `ls /mcp/` for the connected servers."
    )
    #: ``ls /mcp/<server>/tools`` on a server that IS connected but has not been
    #: loaded yet. Distinct from :data:`UNKNOWN_SERVER` on purpose: answering
    #: "no MCP server by that name" about a server the model just saw in
    #: ``ls /mcp/`` is false, and it sends the model back to ``ls /mcp/`` to see
    #: the same name again — the loop is worse than the original empty listing,
    #: because now the runtime is actively contradicting itself.
    SERVER_NOT_LOADED = (
        "This server is connected but its tools are not listed yet. Call "
        "`load_mcp_server` with this server's name first, then read "
        "/mcp/<server>/SERVER.md and list /mcp/<server>/tools/."
    )

    class Seed:
        """Copy for the always-present, not-yet-loaded tier."""

        TOOLS_UNKNOWN = "not listed yet — call `load_mcp_server` for this server"
        INDEX_PLACEHOLDER = (
            "Not listed yet. Nothing under this server can be called until "
            "`load_mcp_server` has run for it."
        )

    class Validation:
        ABSOLUTE_PATH_REQUIRED = "path must be an absolute catalog path"
        NEWLINE_TERMINATED = "content must be newline-terminated"
        LINE_ORIENTED = "content must span more than one line"


class McpCatalogError(Exception):
    """Base error for MCP catalog construction."""


class McpCatalogSecretLeak(McpCatalogError):
    """Raised when a rendered catalog artifact looks secret-shaped.

    Structural safety is the primary control — the catalog is projected from
    contracts that have no credential fields. This is defense in depth for the
    free-form content an untrusted server controls (tool descriptions, JSON
    schemas), which is where a token would have to hide.
    """


class McpToolActionClass(StrEnum):
    """The action class shown in the catalog, mirroring the PDP vocabulary."""

    READ = "read"
    WRITE = "write"
    DESTRUCTIVE = "destructive"

    @classmethod
    def for_tool(cls, *, server: str, tool: str) -> "McpToolActionClass":
        """Return the class the PDP will police for ``(server, tool)``.

        Delegates to the single derivation (curated catalog → untrusted
        annotation hints → fail-closed WRITE) rather than re-reading the
        descriptor's own hints, so the index can never advertise READ for a
        call the gateway treats as a write.
        """

        return McpActionClassTable.classify(
            McpCapabilityDescriptorSource.action_for(server=server, tool=tool)
        )


class McpActionClassTable:
    """Map the PDP's ``Action`` onto the label shown in the catalog."""

    #: Explicit (not value-coerced) so a future divergence between the two
    #: vocabularies fails at review rather than silently mislabelling a write.
    _BY_ACTION: dict[Action, McpToolActionClass] = {
        Action.READ: McpToolActionClass.READ,
        Action.WRITE: McpToolActionClass.WRITE,
        Action.DESTRUCTIVE: McpToolActionClass.DESTRUCTIVE,
    }

    @classmethod
    def classify(cls, action: Action) -> McpToolActionClass:
        """Return the catalog label for ``action``."""

        return cls._BY_ACTION[action]


class McpCatalogPaths:
    """Resolve the model-visible paths for one server's catalog artifacts."""

    @classmethod
    def server_dir(cls, server_name: str) -> str:
        """Return ``/mcp/<server>``."""

        return f"{Keys.Dir.ROOT}/{server_name}"

    @classmethod
    def server_markdown(cls, server_name: str) -> str:
        """Return ``/mcp/<server>/SERVER.md`` — the always-loaded tier."""

        return f"{cls.server_dir(server_name)}/{Keys.File.SERVER_MARKDOWN}"

    @classmethod
    def tools_dir(cls, server_name: str) -> str:
        """Return ``/mcp/<server>/tools/``."""

        return f"{cls.server_dir(server_name)}/{Keys.Dir.TOOLS}/"

    @classmethod
    def resources_dir(cls, server_name: str) -> str:
        """Return ``/mcp/<server>/resources/``."""

        return f"{cls.server_dir(server_name)}/{Keys.Dir.RESOURCES}/"

    @classmethod
    def tool_file(cls, server_name: str, tool_name: str) -> str:
        """Return ``/mcp/<server>/tools/<tool>.json``."""

        return f"{cls.tools_dir(server_name)}{tool_name}{Keys.Ext.JSON}"

    @classmethod
    def resource_file(cls, server_name: str, resource_slug: str) -> str:
        """Return ``/mcp/<server>/resources/<slug>.json``."""

        return f"{cls.resources_dir(server_name)}{resource_slug}{Keys.Ext.JSON}"

    @classmethod
    def file_slug(cls, raw: str, *, fallback: str) -> str:
        """Reduce an arbitrary descriptor name to a safe file-name stem.

        Tool names are already slugs (``McpToolDescriptor`` normalizes them);
        resource names are free-form strings, so they pass through here.
        """

        collapsed = Values.SAFE_FILE_NAME.sub("-", raw.strip().lower()).strip("-.")
        return collapsed or fallback


class McpCatalogFile(RuntimeContract):
    """One rendered catalog artifact: an absolute path and its full content.

    ``content`` is validated to be line-oriented and newline-terminated.
    deepagents' ``read_file`` slices by SOURCE LINE (``DEFAULT_READ_LIMIT``
    counts lines, not bytes), so a single-line artifact would be unreadable in
    exactly the way the offloaded blob was.
    """

    path: str
    content: str

    @field_validator("path")
    @classmethod
    def _validate_path(cls, value: str) -> str:
        if not value.startswith(f"{Keys.Dir.ROOT}/"):
            raise ValueError(Messages.Validation.ABSOLUTE_PATH_REQUIRED)
        return value

    @field_validator("content")
    @classmethod
    def _validate_content(cls, value: str) -> str:
        if not value.endswith(Values.NEWLINE):
            raise ValueError(Messages.Validation.NEWLINE_TERMINATED)
        if value.count(Values.NEWLINE) < Limits.MIN_NEWLINES_PER_FILE:
            raise ValueError(Messages.Validation.LINE_ORIENTED)
        return value

    @property
    def byte_size(self) -> int:
        """Return the UTF-8 size of this artifact."""

        return len(self.content.encode(Keys.Encoding.UTF_8))


class McpCatalogIndexRow(RuntimeContract):
    """One tool as it appears in the ``SERVER.md`` index."""

    tool_name: str
    action: McpToolActionClass
    summary: str


class McpCatalogPointer(RuntimeContract):
    """The compact result ``load_mcp_server`` returns instead of descriptors.

    This is the whole point of the P0: what reaches the model after a load is a
    handful of paths and counts, not 52 tool schemas. Everything else is on the
    filesystem, one small file at a time.
    """

    server_name: str
    display_name: str | None = None
    short_description: str
    auth_state: str
    health: str
    transport: str
    tool_count: int = Field(ge=0)
    resource_count: int = Field(ge=0)
    server_md: str
    tools_dir: str
    resources_dir: str
    next_steps: str


class McpCatalogTier(StrEnum):
    """Which of the catalog's two tiers produced a server's artifacts.

    The distinction is load-bearing, not descriptive: a seed pass at run start
    must not overwrite a server that ``load_mcp_server`` already enriched, or
    turn 2 of a chat would silently lose turn 1's tool list.
    """

    #: Written at run start from the authorized card. ``SERVER.md`` only.
    SEED = "seed"
    #: Written by ``load_mcp_server`` from live descriptors. The full tree.
    LOADED = "loaded"


class McpServerCatalog(RuntimeContract):
    """Every artifact for one server, plus the pointer handed to the model."""

    server_name: str
    pointer: McpCatalogPointer
    files: tuple[McpCatalogFile, ...] = Field(default_factory=tuple)
    #: Directories that exist even when empty, so the layout is stable whether
    #: or not a server publishes resources.
    #:
    #: They are also the on-disk tier marker: a LOADED catalog always declares
    #: ``tools/`` and ``resources/``, a SEED catalog declares neither, so a
    #: file-backed store can answer "has this server been loaded?" from the
    #: directory listing without a sidecar state file.
    directories: tuple[str, ...] = Field(default_factory=tuple)
    tier: McpCatalogTier = McpCatalogTier.LOADED

    @property
    def byte_size(self) -> int:
        """Return the total UTF-8 size of every artifact in this catalog."""

        return sum(file.byte_size for file in self.files)

    @property
    def server_markdown(self) -> McpCatalogFile:
        """Return the always-loaded ``SERVER.md`` artifact."""

        path = McpCatalogPaths.server_markdown(self.server_name)
        for file in self.files:
            if file.path == path:
                return file
        raise McpCatalogError("catalog is missing its SERVER.md artifact")

    def as_mapping(self) -> dict[str, str]:
        """Return ``{path: content}`` for every artifact in this catalog."""

        return {file.path: file.content for file in self.files}


class McpCatalogText:
    """Bounded, deterministic text shaping for catalog artifacts."""

    @classmethod
    def one_line(cls, raw: str) -> str:
        """Collapse a descriptor's description into a single line."""

        return Values.WHITESPACE_RUN.sub(" ", raw.strip())

    @classmethod
    def clip(cls, raw: str, max_bytes: int) -> str:
        """Clip ``raw`` to ``max_bytes`` UTF-8 bytes, ellipsis included.

        Measured in bytes rather than characters because the budget the
        always-loaded tier is held to is a byte budget, and a description can
        be any script.
        """

        encoded = raw.encode(Keys.Encoding.UTF_8)
        if len(encoded) <= max_bytes:
            return raw
        marker = Values.ELLIPSIS.encode(Keys.Encoding.UTF_8)
        if max_bytes <= len(marker):
            return ""
        head = encoded[: max_bytes - len(marker)].decode(
            Keys.Encoding.UTF_8, errors="ignore"
        )
        return f"{head.rstrip()}{Values.ELLIPSIS}"


class McpCatalogRenderer:
    """Render one server's descriptors into line-oriented artifacts."""

    @classmethod
    def server_markdown(
        cls,
        *,
        card: McpServerCard,
        rows: Sequence[McpCatalogIndexRow],
        resource_count: int,
    ) -> str:
        """Render the always-loaded ``SERVER.md`` for one server."""

        header = cls._markdown_header(
            card=card, tool_count=len(rows), resource_count=resource_count
        )
        return f"{header}{cls._markdown_index(card.name, rows)}"

    @classmethod
    def server_markdown_seed(cls, *, card: McpServerCard) -> str:
        """Render the not-yet-loaded ``SERVER.md`` for one authorized server.

        Everything here comes from the compact card the registry already holds,
        so this costs no network call and no tool discovery — which is what
        makes it affordable on EVERY run, for every connected server, before the
        model has said anything.

        It is honest about what it does not know. Claiming "0 tools" for a
        server that simply has not been opened would be a lie the model would
        act on; ``not listed yet`` plus the exact call that fixes it is the
        whole content.
        """

        title = card.display_name or card.name
        return (
            f"# {title}\n\n"
            f"{McpCatalogText.one_line(card.short_description)}\n\n"
            f"- Server name: `{card.name}` (use this as `server_name`)\n"
            f"- Status: {card.health.value} · {card.auth_state.value} · "
            f"{card.transport.value}\n"
            f"- Tools: {Messages.Seed.TOOLS_UNKNOWN}\n"
            f"- Resources: {Messages.Seed.TOOLS_UNKNOWN}\n\n"
            "## How to use this server\n\n"
            f'1. Call `{Values.LOAD_TOOL}` with `server_name: "{card.name}"`. '
            f"That writes one file per tool under "
            f"`{McpCatalogPaths.tools_dir(card.name)}` and rewrites this file "
            "with the full tool index.\n"
            f'2. Then `grep "<term>" {McpCatalogPaths.server_dir(card.name)}/` '
            f"to find a tool, or `read_file "
            f"{McpCatalogPaths.tools_dir(card.name)}<tool>.json` for its full "
            "input schema.\n"
            f"3. {Values.DISPATCH_GUIDANCE} — each tool below is callable "
            "directly, with its own arguments. A write may pause for the "
            "user's approval; that is expected.\n\n"
            "## Tools\n\n"
            f"{Messages.Seed.INDEX_PLACEHOLDER}\n"
        )

    @classmethod
    def seed_next_steps(cls, *, server_name: str) -> str:
        """Return the pointer copy for a server that is listed but not loaded."""

        return (
            f"`{server_name}` is connected but not loaded in this chat. Call "
            f'`{Values.LOAD_TOOL}` with `server_name: "{server_name}"` to '
            f"populate `{McpCatalogPaths.tools_dir(server_name)}`, then read "
            f"`{McpCatalogPaths.server_markdown(server_name)}`."
        )

    @classmethod
    def tool_file(
        cls,
        *,
        server_name: str,
        descriptor: McpToolDescriptor,
        action: McpToolActionClass,
    ) -> str:
        """Render one tool's complete contract as pretty-printed JSON.

        Everything needed to make the call lives in this one file — schema,
        output shape, action class, and the dispatch envelope — so recovering a
        tool is exactly one ``read_file``.
        """

        payload: dict[str, Any] = {
            Keys.Field.ACTION: action.value,
            Keys.Field.DESCRIPTION: descriptor.description,
            Keys.Field.INPUT_SCHEMA: dict(descriptor.input_schema),
            # The machine-readable half of the same instruction, and the one
            # the model actually follows. Under per-tool registration the tool
            # IS the descriptor's name — there is no umbrella to address and no
            # `{server_name, tool_name}` envelope to fill in. `server_name` stays
            # for provenance: the reader still needs to know which connector a
            # descriptor came from.
            Keys.Field.INVOKE_WITH: {
                Keys.Field.TOOL: descriptor.name,
                Keys.Field.SERVER_NAME: server_name,
            },
            Keys.Field.NAME: descriptor.name,
            Keys.Field.OUTPUT_SHAPE: dict(descriptor.output_shape),
            Keys.Field.READ_ONLY: descriptor.read_only,
            Keys.Field.RISK_LEVEL: descriptor.risk_level.value,
            Keys.Field.SERVER_NAME: server_name,
        }
        return cls._json_document(payload)

    @classmethod
    def resource_file(
        cls,
        *,
        server_name: str,
        descriptor: McpResourceDescriptor,
    ) -> str:
        """Render one MCP resource as pretty-printed JSON."""

        payload: dict[str, Any] = {
            Keys.Field.DESCRIPTION: descriptor.description,
            Keys.Field.MIME_TYPE: descriptor.mime_type,
            Keys.Field.NAME: descriptor.name,
            Keys.Field.READ_ONLY: descriptor.access_policy.read_only,
            Keys.Field.REQUIRED_SCOPES: sorted(
                descriptor.access_policy.required_scopes
            ),
            Keys.Field.SERVER_NAME: server_name,
            Keys.Field.URI: descriptor.uri,
        }
        return cls._json_document(payload)

    @classmethod
    def next_steps(cls, *, server_name: str, tool_count: int) -> str:
        """Return the just-in-time instruction that travels with the pointer.

        The guidance rides on the result rather than the system prompt so it is
        present for whichever server actually loaded, on every provider, with
        no prompt surface to keep in sync.
        """

        return (
            f"{tool_count} tools are now browsable as files. Read "
            f"`{McpCatalogPaths.server_markdown(server_name)}` for the tool "
            f'index, or run `grep "<term>" {McpCatalogPaths.server_dir(server_name)}/` '
            f"to search descriptions. Then `read_file "
            f"{McpCatalogPaths.tools_dir(server_name)}<tool>.json` for one "
            f"tool's full schema, then {Values.DISPATCH_GUIDANCE}. "
            "Do not ask for the descriptors again — they are on the filesystem."
        )

    @classmethod
    def _json_document(cls, payload: Mapping[str, Any]) -> str:
        """Serialize a payload as a stable, newline-terminated JSON document."""

        body = json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False)
        return f"{body}{Values.NEWLINE}"

    @classmethod
    def _markdown_header(
        cls, *, card: McpServerCard, tool_count: int, resource_count: int
    ) -> str:
        title = card.display_name or card.name
        return (
            f"# {title}\n\n"
            f"{McpCatalogText.one_line(card.short_description)}\n\n"
            f"- Server name: `{card.name}` (use this as `server_name`)\n"
            f"- Status: {card.health.value} · {card.auth_state.value} · "
            f"{card.transport.value}\n"
            f"- Tools: {tool_count} — one file each under "
            f"`{McpCatalogPaths.tools_dir(card.name)}`\n"
            f"- Resources: {resource_count} — under "
            f"`{McpCatalogPaths.resources_dir(card.name)}`\n\n"
            "## How to use this server\n\n"
            "1. Find a tool in the index below, or `grep` this directory to "
            "search every description.\n"
            f"2. `read_file {McpCatalogPaths.tools_dir(card.name)}<tool>.json` "
            "for its full input schema.\n"
            f"3. {Values.DISPATCH_GUIDANCE} — each tool below is callable "
            "directly, with its own arguments. A write may pause for the "
            "user's approval; that is expected.\n\n"
            "## Tools\n\n"
        )

    @classmethod
    def _markdown_index(
        cls, server_name: str, rows: Sequence[McpCatalogIndexRow]
    ) -> str:
        """Render the tool index, flexing the summary column to fit the budget."""

        if not rows:
            return (
                "This server published no tools. Nothing here can be called; "
                "tell the user rather than guessing a tool name.\n"
            )
        del server_name
        prefixes = [cls._index_prefix(row) for row in rows]
        width = cls._summary_width(prefixes)
        lines = []
        for prefix, row in zip(prefixes, rows, strict=True):
            if width >= Limits.INDEX_SUMMARY_MIN_BYTES and row.summary:
                summary = McpCatalogText.clip(row.summary, width)
                lines.append(f"{prefix}{Values.INDEX_SEPARATOR}{summary}")
            else:
                lines.append(prefix)
        return Values.NEWLINE.join(lines) + Values.NEWLINE

    @classmethod
    def _index_prefix(cls, row: McpCatalogIndexRow) -> str:
        return f"- `{row.tool_name}` [{row.action.value}]"

    @classmethod
    def _summary_width(cls, prefixes: Sequence[str]) -> int:
        """Return the per-row summary budget that keeps ``SERVER.md`` bounded.

        Tool names and action classes are never sacrificed — only the summary
        column flexes, and it collapses to nothing before a name is dropped.
        """

        fixed = len(
            (Values.INDEX_SEPARATOR + Values.NEWLINE).encode(Keys.Encoding.UTF_8)
        )
        overhead = sum(
            len(prefix.encode(Keys.Encoding.UTF_8)) + fixed for prefix in prefixes
        )
        # The header is bounded and small; measuring it exactly would require
        # rendering twice, so reserve a fixed, generous slice for it.
        remaining = (
            Limits.SERVER_MARKDOWN_MAX_BYTES - Limits.HEADER_RESERVE_BYTES - overhead
        )
        if remaining <= 0:
            return 0
        return min(Limits.INDEX_SUMMARY_MAX_BYTES, remaining // len(prefixes))


class McpCatalogBuilder:
    """Project a :class:`LoadedMcpServer` into browsable catalog artifacts."""

    @classmethod
    def build(cls, loaded_server: LoadedMcpServer) -> McpServerCatalog:
        """Return the full catalog for one loaded server.

        Raises:
            McpCatalogSecretLeak: when a rendered artifact carries a
                ciphertext-shaped value — a hostile descriptor trying to smuggle
                a credential onto the model-visible filesystem.
        """

        card = loaded_server.server_card
        rows = cls._index_rows(card.name, loaded_server.tools)
        files = [
            cls._file(
                McpCatalogPaths.server_markdown(card.name),
                McpCatalogRenderer.server_markdown(
                    card=card,
                    rows=rows,
                    resource_count=len(loaded_server.resources),
                ),
            )
        ]
        files.extend(cls._tool_files(card.name, loaded_server.tools, rows))
        files.extend(cls._resource_files(card.name, loaded_server.resources))
        return McpServerCatalog(
            server_name=card.name,
            pointer=cls._pointer(loaded_server),
            files=tuple(files),
            directories=(
                McpCatalogPaths.tools_dir(card.name),
                McpCatalogPaths.resources_dir(card.name),
            ),
        )

    @classmethod
    def seed(cls, card: McpServerCard) -> McpServerCatalog:
        """Return the not-yet-loaded catalog for one authorized server.

        The whole of what a run start can know without talking to anybody: one
        ``SERVER.md`` naming the server and the call that fills in the rest. No
        ``tools/`` or ``resources/`` directory is declared, which is exactly
        what marks this catalog as the SEED tier on disk.

        Raises:
            McpCatalogSecretLeak: as :meth:`build` does. A card's description is
                still connector-authored text.
        """

        return McpServerCatalog(
            server_name=card.name,
            pointer=cls._seed_pointer(card),
            files=(
                cls._file(
                    McpCatalogPaths.server_markdown(card.name),
                    McpCatalogRenderer.server_markdown_seed(card=card),
                ),
            ),
            directories=(),
            tier=McpCatalogTier.SEED,
        )

    @classmethod
    def seed_all(cls, cards: Sequence[McpServerCard]) -> tuple[McpServerCatalog, ...]:
        """Return a seed catalog per card, dropping any the scanner refuses.

        One hostile card must not cost the user every OTHER connector's
        directory, so a refusal is logged and skipped rather than raised: the
        remaining servers stay listed and loadable.
        """

        catalogs: list[McpServerCatalog] = []
        for card in cards:
            try:
                catalogs.append(cls.seed(card))
            except McpCatalogError:
                _LOGGER.warning(Log.SEED_REFUSED, card.name, exc_info=True)
        return tuple(catalogs)

    @classmethod
    def _seed_pointer(cls, card: McpServerCard) -> McpCatalogPointer:
        return McpCatalogPointer(
            server_name=card.name,
            display_name=card.display_name,
            short_description=McpCatalogText.one_line(card.short_description),
            auth_state=card.auth_state.value,
            health=card.health.value,
            transport=card.transport.value,
            tool_count=0,
            resource_count=0,
            server_md=McpCatalogPaths.server_markdown(card.name),
            tools_dir=McpCatalogPaths.tools_dir(card.name),
            resources_dir=McpCatalogPaths.resources_dir(card.name),
            next_steps=McpCatalogRenderer.seed_next_steps(server_name=card.name),
        )

    @classmethod
    def _index_rows(
        cls, server_name: str, tools: Sequence[McpToolDescriptor]
    ) -> tuple[McpCatalogIndexRow, ...]:
        return tuple(
            McpCatalogIndexRow(
                tool_name=tool.name,
                action=McpToolActionClass.for_tool(server=server_name, tool=tool.name),
                summary=McpCatalogText.one_line(tool.description),
            )
            for tool in tools
        )

    @classmethod
    def _tool_files(
        cls,
        server_name: str,
        tools: Sequence[McpToolDescriptor],
        rows: Sequence[McpCatalogIndexRow],
    ) -> list[McpCatalogFile]:
        return [
            cls._file(
                McpCatalogPaths.tool_file(server_name, tool.name),
                McpCatalogRenderer.tool_file(
                    server_name=server_name,
                    descriptor=tool,
                    action=row.action,
                ),
            )
            for tool, row in zip(tools, rows, strict=True)
        ]

    @classmethod
    def _resource_files(
        cls, server_name: str, resources: Sequence[McpResourceDescriptor]
    ) -> list[McpCatalogFile]:
        files: list[McpCatalogFile] = []
        used: set[str] = set()
        for index, resource in enumerate(resources):
            slug = McpCatalogPaths.file_slug(
                resource.name, fallback=f"resource-{index}"
            )
            if slug in used:
                slug = f"{slug}-{index}"
            used.add(slug)
            files.append(
                cls._file(
                    McpCatalogPaths.resource_file(server_name, slug),
                    McpCatalogRenderer.resource_file(
                        server_name=server_name, descriptor=resource
                    ),
                )
            )
        return files

    @classmethod
    def _pointer(cls, loaded_server: LoadedMcpServer) -> McpCatalogPointer:
        card = loaded_server.server_card
        return McpCatalogPointer(
            server_name=card.name,
            display_name=card.display_name,
            short_description=McpCatalogText.one_line(card.short_description),
            auth_state=card.auth_state.value,
            health=card.health.value,
            transport=card.transport.value,
            tool_count=len(loaded_server.tools),
            resource_count=len(loaded_server.resources),
            server_md=McpCatalogPaths.server_markdown(card.name),
            tools_dir=McpCatalogPaths.tools_dir(card.name),
            resources_dir=McpCatalogPaths.resources_dir(card.name),
            next_steps=McpCatalogRenderer.next_steps(
                server_name=card.name, tool_count=len(loaded_server.tools)
            ),
        )

    @classmethod
    def _file(cls, path: str, content: str) -> McpCatalogFile:
        """Build one artifact after scanning its rendered bytes for secrets.

        The scanner is handed the rendered STRING, not the structured payload:
        a tool whose input schema legitimately declares an ``api_key`` property
        is describing a shape, not carrying a credential, and refusing to
        publish it would make the whole connector undiscoverable. What is
        refused is a ciphertext-shaped VALUE anywhere in the document.
        """

        try:
            SecretShapeScanner.assert_clean(content, where=path)
        except McpConfigSecretLeak as exc:
            raise McpCatalogSecretLeak(Messages.SECRET_SHAPED) from exc
        return McpCatalogFile(path=path, content=content)


class McpCatalogSeedOutcome(RuntimeContract):
    """What one run-start seed pass actually did — the log line, typed.

    Every field is a tuple of server slugs (configuration identifiers, never
    user content), so this is safe to log verbatim and is what makes a live
    "``ls /mcp`` was empty" report diagnosable without a reproduction.
    """

    seeded: tuple[str, ...] = Field(default_factory=tuple)
    retained: tuple[str, ...] = Field(default_factory=tuple)
    pruned: tuple[str, ...] = Field(default_factory=tuple)

    @property
    def server_names(self) -> tuple[str, ...]:
        """Every server the catalog holds after this pass, sorted."""

        return tuple(sorted((*self.seeded, *self.retained)))

    def log(self) -> None:
        """Emit the one structured line a live run is diagnosed from."""

        _LOGGER.info(
            Log.SEEDED,
            len(self.seeded),
            len(self.retained),
            len(self.pruned),
            Values.LOG_SEPARATOR.join(self.server_names) or Log.NONE,
        )


class McpCatalogSeedPlan(RuntimeContract):
    """The writes and deletes one seed pass implies, decided before any I/O."""

    publish: tuple[McpServerCatalog, ...] = Field(default_factory=tuple)
    prune: tuple[str, ...] = Field(default_factory=tuple)
    retained: tuple[str, ...] = Field(default_factory=tuple)

    def outcome(self) -> McpCatalogSeedOutcome:
        """Return the outcome this plan produces once applied."""

        return McpCatalogSeedOutcome(
            seeded=tuple(catalog.server_name for catalog in self.publish),
            retained=self.retained,
            pruned=self.prune,
        )


class McpCatalogSeedPlanner:
    """Decide what a run-start seed pass writes — shared by every store.

    Three rules, and each one is a bug that has already been paid for:

    1. **A loaded server is never re-seeded.** The store outlives the run (it is
       conversation-scoped on the desktop), so overwriting an enriched
       ``/mcp/<server>/`` with a stub would delete turn 1's tool list at the
       start of turn 2 — the same defect one level up.
    2. **A stub IS refreshed.** Auth state and health live on the card and
       change between runs; a stub is cheap and carries no descriptor, so
       rewriting it costs nothing and keeps the status line honest.
    3. **A server that is no longer authorized is pruned.** In-memory this never
       came up (the store died with the process). On disk the directory would
       persist and the model would happily read a schema for a connector the
       user removed.
    """

    @classmethod
    def plan(
        cls,
        *,
        incoming: Sequence[McpServerCatalog],
        known: Sequence[str],
        loaded: Sequence[str],
    ) -> McpCatalogSeedPlan:
        """Return the plan for seeding ``incoming`` over a store's current state.

        ``known`` is every server the store currently holds; ``loaded`` is the
        subset already enriched by ``load_mcp_server``.
        """

        by_name = {catalog.server_name: catalog for catalog in incoming}
        loaded_names = set(loaded)
        return McpCatalogSeedPlan(
            publish=tuple(
                by_name[name] for name in sorted(by_name) if name not in loaded_names
            ),
            prune=tuple(sorted(set(known) - set(by_name))),
            retained=tuple(sorted(loaded_names & set(by_name))),
        )


@runtime_checkable
class McpCatalogPublisher(Protocol):
    """Write half of the catalog seam, held by the model-facing load tool."""

    def publish(self, catalog: McpServerCatalog) -> None:
        """Make ``catalog`` visible on the agent filesystem."""

    def seed(self, catalogs: Sequence[McpServerCatalog]) -> McpCatalogSeedOutcome:
        """Populate ``/mcp/`` at run start without disturbing loaded servers."""


@runtime_checkable
class McpCatalogReader(Protocol):
    """Read half of the catalog seam, held by the filesystem backend."""

    def snapshot(self) -> Mapping[str, str]:
        """Return ``{path: content}`` for every published artifact."""

    def directories(self) -> tuple[str, ...]:
        """Return directories that exist even when they hold no files."""


class McpCatalogStore:
    """In-process catalog: written when a server loads, read by the backend.

    This is the runtime write surface into the agent filesystem, and it is
    deliberately the smallest one that works. The load tool and the filesystem
    route are composed together for one run, so a shared object needs no graph
    state write and no new persistence — exactly how ``/subagents/`` and
    ``/large_tool_results/`` are already fed.

    ``publish`` swaps in a fresh mapping rather than mutating in place, so a
    concurrent reader always sees a whole, consistent catalog. Re-publishing a
    server replaces its artifacts (rebuild-on-load: simple and always correct),
    and never disturbs another server's.
    """

    def __init__(self) -> None:
        self._files: Mapping[str, str] = {}
        self._directories: tuple[str, ...] = ()
        self._pointers: Mapping[str, McpCatalogPointer] = {}
        self._loaded: frozenset[str] = frozenset()

    def publish(self, catalog: McpServerCatalog) -> None:
        """Replace this server's artifacts with ``catalog``'s."""

        self._replace(catalog.server_name, catalog)
        Log.published(catalog)

    def seed(self, catalogs: Sequence[McpServerCatalog]) -> McpCatalogSeedOutcome:
        """Apply a run-start seed pass; never disturbs a loaded server."""

        plan = McpCatalogSeedPlanner.plan(
            incoming=catalogs, known=self.server_names(), loaded=sorted(self._loaded)
        )
        for server_name in plan.prune:
            self._replace(server_name, None)
        for catalog in plan.publish:
            self._replace(catalog.server_name, catalog)
        outcome = plan.outcome()
        outcome.log()
        return outcome

    def _replace(self, server_name: str, catalog: McpServerCatalog | None) -> None:
        """Swap one server's artifacts for ``catalog``'s, or drop them entirely.

        A whole fresh mapping rather than a mutation in place, so a concurrent
        reader always sees a consistent catalog.
        """

        prefix = f"{McpCatalogPaths.server_dir(server_name)}/"
        files = {
            path: content
            for path, content in self._files.items()
            if not path.startswith(prefix)
        }
        directories = {
            directory
            for directory in self._directories
            if not directory.startswith(prefix)
        }
        pointers = {
            name: pointer
            for name, pointer in self._pointers.items()
            if name != server_name
        }
        loaded = set(self._loaded) - {server_name}
        if catalog is not None:
            files.update(catalog.as_mapping())
            directories.update(catalog.directories)
            pointers[server_name] = catalog.pointer
            if catalog.tier is McpCatalogTier.LOADED:
                loaded.add(server_name)
        self._files = files
        self._directories = tuple(sorted(directories))
        self._pointers = pointers
        self._loaded = frozenset(loaded)

    def snapshot(self) -> Mapping[str, str]:
        """Return the current ``{path: content}`` view."""

        return self._files

    def directories(self) -> tuple[str, ...]:
        """Return the always-present directories of every published server."""

        return self._directories

    def pointer(self, server_name: str) -> McpCatalogPointer | None:
        """Return the pointer for ``server_name`` if it has been published."""

        return self._pointers.get(server_name)

    def server_names(self) -> tuple[str, ...]:
        """Return every published server name, sorted."""

        return tuple(sorted(self._pointers))


__all__ = [
    "McpCatalogBuilder",
    "McpCatalogError",
    "McpCatalogFile",
    "McpCatalogIndexRow",
    "McpCatalogPaths",
    "McpCatalogPointer",
    "McpCatalogPublisher",
    "McpCatalogReader",
    "McpCatalogRenderer",
    "McpCatalogSecretLeak",
    "McpCatalogSeedOutcome",
    "McpCatalogSeedPlan",
    "McpCatalogSeedPlanner",
    "McpCatalogStore",
    "McpCatalogText",
    "McpCatalogTier",
    "McpServerCatalog",
    "McpToolActionClass",
]
