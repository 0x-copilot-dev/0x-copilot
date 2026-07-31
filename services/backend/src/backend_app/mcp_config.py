"""The MCP configuration DOCUMENT — one editable JSON view of the registry.

Why a document at all, when `/v1/mcp/servers` already exists: the registry is
a set of rows reached one verb at a time, and the thing users actually want to
manage is the *config* — see every server at once, add one by pasting the
block a README gave them, delete one by removing it. Modelling that as a
document makes the whole state visible and diffable, and makes "what I see is
what is installed" true by construction.

The shape deliberately matches the `mcp.json` the MCP client ecosystem
already uses (`servers` keyed by name; `type`/`url`/`headers` for remote,
`command`/`args`/`env`/`cwd` for local; `${input:<id>}` placeholders resolved
from an `inputs` list), because the overwhelmingly common way a user obtains
one of these is copy-and-paste from a project's install instructions. A shape
of our own would have made every one of those a manual translation. Note that
this format is a client convention, NOT part of the MCP specification — the
spec defines the two transports and says nothing about configuration files.

Secrets never appear in the document. A credential-valued header or env var
reads back as the redaction marker `••••••••`, and writing that marker back
means "keep what is stored" — which is what lets the editor reformat, add a
server, or delete one without either leaking a token or wiping it.

Everything is expressed IN the document. There is no side-channel map of
secrets and no `${input:...}` indirection to maintain: you type the real value,
the backend seals it, and the next read shows the marker instead. `${input:x}`
is still RECOGNISED on write, because real README configs contain it and
sending that literal string to a server as a bearer token would be a bug — but
it is never emitted.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict, Field

from backend_app.contracts import (
    CreateMcpServerRequest,
    McpAuthMode,
    McpConfiguredValue,
    McpConfiguredValueRequest,
    McpServerRecord,
    McpStdioRequest,
    McpTransport,
)

# What a stored credential reads back as. Writing it back unchanged means
# "keep what is stored"; it is not a value anyone can send to a server.
REDACTED = "\u2022" * 8

# `${input:name}` as a WHOLE value. Not emitted — only recognised, because a
# config pasted from a project's install instructions carries it, and storing
# that literal string as a credential would mean sending
# `Authorization: ${input:github_mcp_pat}` to the server and getting an
# unexplainable 401. Treated as "no value supplied yet".
_INPUT_REFERENCE = re.compile(r"^\$\{input:[A-Za-z0-9][A-Za-z0-9._-]*\}$")


def is_unresolved_placeholder(value: str) -> bool:
    """Is this a `${input:...}` reference with no value behind it?"""

    return _INPUT_REFERENCE.fullmatch(value.strip()) is not None


class McpConfigServer(BaseModel):
    """One server entry. Remote and local fields are mutually exclusive.

    Every field is `None`-by-default rather than empty-by-default, and the read
    route serialises with `exclude_none`, so a server only ever states what
    actually applies to it. The alternative — emitting `"command": null,
    "args": [], "env": {}, "cwd": null` on an HTTP server — is not merely
    untidy: this document is the thing a user reads and edits, and four
    inapplicable keys per entry bury the two that matter, in a format whose
    whole appeal is that it looks like the config they were handed.
    """

    model_config = ConfigDict(extra="forbid")

    type: McpTransport = McpTransport.HTTP
    url: str | None = None
    headers: dict[str, str] | None = None
    command: str | None = None
    args: tuple[str, ...] | None = None
    env: dict[str, str] | None = None
    cwd: str | None = None


class McpConfigDocument(BaseModel):
    """The whole config: every server the user has, keyed by name."""

    model_config = ConfigDict(extra="forbid")

    servers: dict[str, McpConfigServer] = Field(default_factory=dict)


class McpConfigWriteRequest(BaseModel):
    """A PUT: just the document.

    There is no companion `secrets` map. A new credential is typed into the
    document like any other value and sealed on arrival; an existing one is
    left as the redaction marker and preserved. One representation, one place
    to look — the alternative was an envelope whose two halves had to agree
    about names that appeared in neither.
    """

    model_config = ConfigDict(extra="forbid")

    document: McpConfigDocument


class McpConfigWriteResult(BaseModel):
    """What the save actually did, by server name."""

    model_config = ConfigDict(extra="forbid")

    created: tuple[str, ...] = ()
    updated: tuple[str, ...] = ()
    deleted: tuple[str, ...] = ()
    unchanged: tuple[str, ...] = ()


def _values_to_document(values: tuple[McpConfiguredValue, ...]) -> dict[str, str]:
    """Render stored header/env values back into document form.

    A credential becomes the redaction marker; anything else is shown as it
    was written.
    """

    return {
        item.name: (item.value if item.value is not None else REDACTED)
        for item in values
    }


def document_from_records(
    records: tuple[McpServerRecord, ...],
) -> McpConfigDocument:
    """Project the registry into the editable document.

    Every server appears, including catalog installs and OAuth connectors —
    "Manage MCP" that hid half the servers would be a worse lie than no
    document at all. OAuth servers simply have no credential to show: their
    tokens live in the vault and are not configuration.
    """

    servers: dict[str, McpConfigServer] = {}
    for record in sorted(records, key=lambda r: r.name):
        if record.stdio is not None:
            env = _values_to_document(record.stdio.env)
            servers[record.name] = McpConfigServer(
                type=record.transport,
                command=record.stdio.command,
                args=record.stdio.args or None,
                env=env or None,
                cwd=record.stdio.cwd,
            )
        else:
            headers = _values_to_document(record.headers)
            servers[record.name] = McpConfigServer(
                type=record.transport,
                url=record.url,
                headers=headers or None,
            )
    return McpConfigDocument(servers=servers)


def _document_to_values(
    raw: dict[str, str],
) -> tuple[McpConfiguredValueRequest, ...]:
    """Turn a document header/env mapping into inbound configured values.

    Three cases, and only three:

      the redaction marker   `value=None` — keep whatever is stored. This is
                             what a save of an unedited document does, and it
                             is why reformatting cannot destroy a credential.
      `${input:x}`           `value=None` — nothing was supplied. Same
                             treatment, because storing that literal string
                             would mean sending it to the server as a bearer
                             token.
      anything else          the user typed it; it is the new value.
    """

    values: list[McpConfiguredValueRequest] = []
    for name, raw_value in raw.items():
        unresolved = raw_value.strip() == REDACTED or is_unresolved_placeholder(
            raw_value
        )
        values.append(
            McpConfiguredValueRequest(
                name=name,
                value=None if unresolved else raw_value,
            )
        )
    return tuple(values)


def create_request_from_entry(
    name: str,
    entry: McpConfigServer,
    *,
    org_id: str,
    user_id: str,
) -> CreateMcpServerRequest:
    """Build the registry create-request for one document entry."""

    if entry.type is McpTransport.STDIO:
        if entry.command is None:
            raise ValueError(f'server "{name}" is type stdio but has no command')
        if entry.url is not None:
            raise ValueError(f'server "{name}" is type stdio and cannot have a url')
        return CreateMcpServerRequest(
            org_id=org_id,
            user_id=user_id,
            display_name=name,
            transport=McpTransport.STDIO,
            # A local subprocess is reached by launching it; there is no
            # remote identity to authenticate against.
            auth_mode=McpAuthMode.NONE,
            stdio=McpStdioRequest(
                command=entry.command,
                args=entry.args or (),
                env=_document_to_values(entry.env or {}),
                cwd=entry.cwd,
            ),
        )
    if entry.url is None:
        raise ValueError(f'server "{name}" is type {entry.type.value} but has no url')
    if entry.command is not None:
        raise ValueError(
            f'server "{name}" is type {entry.type.value} and cannot have a command'
        )
    headers = _document_to_values(entry.headers or {})
    return CreateMcpServerRequest(
        org_id=org_id,
        user_id=user_id,
        url=entry.url,
        display_name=name,
        transport=entry.type,
        # A configured credential IS the authentication; defaulting to OAuth
        # would put a server that needs no browser round-trip into
        # "Disconnected" forever, with a Connect button that cannot succeed.
        auth_mode=McpAuthMode.API_KEY if headers else McpAuthMode.OAUTH2,
        headers=headers,
    )


def entries_differ(entry: McpConfigServer, record: McpServerRecord) -> bool:
    """Has this entry changed relative to the stored row?

    Compared through the document projection so the two sides are the same
    kind of thing — comparing a document field against a record field
    directly is how a placeholder ends up "differing" from the secret it
    stands for on every single save.
    """

    projected = document_from_records((record,)).servers.get(record.name)
    if projected is None:  # pragma: no cover — the record is its own source
        return True
    return projected.model_dump() != entry.model_dump()


__all__ = [
    "REDACTED",
    "McpConfigDocument",
    "McpConfigServer",
    "McpConfigWriteRequest",
    "McpConfigWriteResult",
    "create_request_from_entry",
    "document_from_records",
    "is_unresolved_placeholder",
    "entries_differ",
]
