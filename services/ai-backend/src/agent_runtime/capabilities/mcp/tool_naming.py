"""The one place an MCP tool's model-visible name is composed and taken apart.

Every connector tool the model can call is registered as
``mcp__{server_slug}__{tool_name}``. The namespace is not decoration — it is
what makes two connectors that both expose ``search`` two callable tools
instead of one registered tool and one silently dropped connector. Before this
module the registered name was the connector's bare tool name, so the second
``search`` lost a typed ``DUPLICATE_DESCRIPTOR_NAME`` race for the whole run
(:mod:`agent_runtime.capabilities.mcp.tool_source`).

**Which register a name is in matters, and there are exactly two.**

* The **model surface** register — what the provider sees in the tool block,
  what the model emits in a ``tool_call``, what ``payload.tool_name`` carries on
  every stream event, and therefore what the ``tool_name → server_slug``
  provenance map is keyed on. This register is namespaced.
* The **connector** register — the name the MCP server itself answers to, and
  the key the action catalog, the untrusted-annotations registry and the
  synthesised display templates are all stored under. This register is bare, and
  it must stay bare: ``langchain-mcp-adapters`` captures the wire name in the
  dispatch closure (``convert_mcp_tool_to_langchain_tool``), so renaming the
  LangChain tool never renames the call.

:meth:`McpToolName.compose` moves a name from the second register into the
first; :meth:`McpToolName.parse` / :meth:`McpToolName.strip` move it back. A
seam that mixes the two is a bug — a namespaced name looked up in the action
catalog misses and the tool fail-closes to ``WRITE``, and a namespaced name
handed to a humaniser reads "Mcp Linear List Issues".

**Sanitization** matches what provider tool-name validation accepts —
``[A-Za-z0-9_-]`` (OpenAI ``^[a-zA-Z0-9_-]{1,64}$``, Anthropic the same charset
at 128, Gemini 64), so :data:`McpToolName.MAX_LENGTH` takes the tightest of the
three. Runs of ``_`` collapse to one and the ends are trimmed, which is what
makes ``__`` an unambiguous delimiter: no sanitized component can contain the
separator, so :meth:`McpToolName.parse` is the exact inverse of
:meth:`McpToolName.compose` for every name inside the length limit.

The convention (``mcp__`` + double-underscore separator) is deliberately the one
Claude Code, Codex, opencode and hermes already use, because it is the shape
models have seen in training — see ``opencode/packages/opencode/src/mcp/catalog.ts``
(``sanitize``/``toolName``) and ``hermes-agent/tools/mcp_tool.py``
(``MCP_TOOL_NAME_PREFIX``). Neither of those can express this module's
round-trip guarantee: opencode joins with a single ``_`` and hermes keeps ``__``
without collapsing runs inside a component, so both are ambiguous the moment a
server or tool name contains the separator.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import re
from typing import Final


@dataclass(frozen=True, slots=True)
class ParsedMcpToolName:
    """The two components a namespaced model-surface tool name carries.

    ``server`` is the sanitized connector slug and ``tool`` is the sanitized
    connector-register tool name — the value every catalog / annotation /
    display lookup wants.
    """

    server: str
    tool: str


class McpToolName:
    """Compose and decompose ``mcp__{server}__{tool}``. Pure, total, injective.

    Total because a naming helper sits on the registration path and must not be
    able to fail it; injective because two distinct sanitized ``(server, tool)``
    pairs must never compose to one registered name — that is the whole point of
    namespacing. Over-long names keep injectivity with a digest of the *full*
    pair rather than by hoping the truncated head is unique.
    """

    #: The namespace marker. Same convention as Claude Code / Codex / opencode /
    #: hermes, so the shape is one models have already seen.
    PREFIX: Final[str] = "mcp__"
    #: Double, so a single ``_`` inside either component is never a boundary.
    DELIMITER: Final[str] = "__"
    #: The tightest provider limit of the three we ship against (OpenAI, Gemini).
    MAX_LENGTH: Final[int] = 64
    #: Characters no provider's tool-name validator accepts.
    _ILLEGAL: Final[re.Pattern[str]] = re.compile(r"[^A-Za-z0-9_-]+")
    #: Collapsed so no sanitized component can contain :attr:`DELIMITER`.
    _UNDERSCORE_RUN: Final[re.Pattern[str]] = re.compile(r"_+")
    #: What a component that sanitizes away to nothing becomes. Reaching it means
    #: a caller passed a name its own upstream guard should have refused.
    _EMPTY_COMPONENT: Final[str] = "unknown"
    #: Hex characters of pair-digest appended when the composed name overflows.
    _DIGEST_LENGTH: Final[int] = 6
    #: The shortest tool head truncation may leave, so a long connector slug can
    #: never squeeze the tool out of its own name.
    _MIN_TOOL_HEAD: Final[int] = 8

    @classmethod
    def sanitize(cls, value: object) -> str:
        """Reduce one component to the provider-safe, separator-free charset.

        Three steps, each load-bearing: illegal characters become ``_`` (so the
        provider accepts the name), runs of ``_`` collapse to one (so ``__``
        stays an unambiguous boundary), and the ends are trimmed of ``_`` (so a
        component can never abut the delimiter and steal a character from it).
        """

        text = cls._ILLEGAL.sub("_", str(value or "").strip())
        text = cls._UNDERSCORE_RUN.sub("_", text).strip("_")
        return text or cls._EMPTY_COMPONENT

    @classmethod
    def compose(cls, *, server: str, tool: str) -> str:
        """Return the model-surface name for ``tool`` hosted by ``server``.

        **Idempotent** for every name inside :attr:`MAX_LENGTH`: re-composing a
        name this method already produced for the same connector returns it
        unchanged, so a caller cannot double-prefix by running the registration
        path twice.

        **Injective** in the sanitized ``(server, tool)`` pair, which is what
        keeps the collision this module exists to remove from coming back in
        another form. The prefix is only absorbed when it names *this* server;
        a prefix naming a different one is an ordinary part of the tool's name
        and is sanitized into the tail. That case is real, not hypothetical —
        an aggregating connector (mcp-proxy, metamcp) re-advertises its
        upstreams' tools already carrying their ``mcp__<upstream>__`` prefix, so
        absorbing any prefix would map ``mcp__github__search`` and
        ``mcp__gitlab__search`` onto one registered name and drop the second
        with the very ``DUPLICATE_DESCRIPTOR_NAME`` failure this module removes.

        The ``server`` argument always wins the attribution: the caller holds
        the true connector, while the tool name is untrusted input read off an
        MCP server and may not re-attribute itself to a connector it does not
        belong to.

        A pair long enough to need :meth:`_fitted` stays idempotent as long as
        the connector slug itself survives truncation intact — it does for any
        slug up to 42 characters, which is every slug ``server_slug`` can
        produce for a real connector.
        """

        server_part = cls.sanitize(server)
        parsed = cls.parse(tool)
        # Absorb the prefix only when it is THIS server's — see the injectivity
        # paragraph above.
        bare = (
            parsed.tool if parsed is not None and parsed.server == server_part else tool
        )
        tool_part = cls.sanitize(bare)
        name = f"{cls.PREFIX}{server_part}{cls.DELIMITER}{tool_part}"
        if len(name) <= cls.MAX_LENGTH:
            return name
        return cls._fitted(server_part, tool_part)

    @classmethod
    def parse(cls, name: object) -> ParsedMcpToolName | None:
        """Split a namespaced name; ``None`` when it is not one.

        ``None`` is the answer for every native tool name, which is why callers
        can run this over any tool name without first knowing its origin. A
        native tool literally called ``mcp__x__y`` is indistinguishable and would
        parse — the source's reserved-name guard is what keeps that name from
        being claimed twice.
        """

        if not isinstance(name, str):
            return None
        text = name.strip()
        if not text.startswith(cls.PREFIX):
            return None
        server, separator, tool = text[len(cls.PREFIX) :].partition(cls.DELIMITER)
        if not separator or not server or not tool:
            return None
        return ParsedMcpToolName(server=server, tool=tool)

    @classmethod
    def is_namespaced(cls, name: object) -> bool:
        """Whether ``name`` is a model-surface MCP name this module composed."""

        return cls.parse(name) is not None

    @classmethod
    def strip(cls, name: object) -> str:
        """Return the connector-register name — the presentation/lookup key.

        The exact inverse of :meth:`compose` for any name inside
        :attr:`MAX_LENGTH`, and the identity on a name that was never
        namespaced, so a seam serving both registers needs no branch.
        """

        parsed = cls.parse(name)
        if parsed is not None:
            return parsed.tool
        return str(name or "").strip()

    @classmethod
    def _fitted(cls, server: str, tool: str) -> str:
        """Compose an over-long pair down to :attr:`MAX_LENGTH`, injectively.

        The digest is taken over the **whole** sanitized pair, not the truncated
        head, so two long tools on one connector that share a prefix still
        register as two tools. The truncated halves are re-trimmed because a cut
        can land mid-run and leave a trailing ``_`` that would blur the boundary.
        """

        digest = sha256(f"{server}\x00{tool}".encode()).hexdigest()[
            : cls._DIGEST_LENGTH
        ]
        budget = (
            cls.MAX_LENGTH
            - len(cls.PREFIX)
            - len(cls.DELIMITER)
            - 1  # the ``_`` joining the tool head to its digest
            - cls._DIGEST_LENGTH
        )
        server_head = server[: budget - cls._MIN_TOOL_HEAD].strip("_") or server[0]
        tool_head = tool[: budget - len(server_head)].strip("_") or tool[0]
        return f"{cls.PREFIX}{server_head}{cls.DELIMITER}{tool_head}_{digest}"


__all__ = ["McpToolName", "ParsedMcpToolName"]
