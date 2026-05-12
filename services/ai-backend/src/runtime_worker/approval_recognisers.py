"""Vendor-specific projection from raw MCP tool-call arguments to ``ApprovalParam`` consent-card rows."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from typing import ClassVar

from runtime_api.schemas.approvals import APPROVAL_MAX_PARAMS, ApprovalParam
from runtime_api.schemas.common import ApprovalReversible

_VALUE_MAX = 128


class ApprovalParamRecogniser(ABC):
    """Abstract base for one vendor's approval-param projection.

    Concrete subclasses declare ``vendor_tokens`` — substrings compared against the
    lowercased, decoration-stripped ``server_name`` to claim a call. The ``mcp_`` /
    ``_mcp`` / ``_com`` / ``-com`` affixes are stripped before matching.
    """

    vendor_tokens: ClassVar[tuple[str, ...]] = ()

    @classmethod
    def matches_server_name(cls, server_name: str) -> bool:
        """Return ``True`` if any vendor token is found in the normalised server name."""
        normalized = cls._normalize_server_name(server_name)
        return any(token in normalized for token in cls.vendor_tokens)

    @abstractmethod
    def recognise(self, arguments: Mapping[str, object]) -> tuple[ApprovalParam, ...]:
        """Return up to ``APPROVAL_MAX_PARAMS`` rows for this vendor."""

    @classmethod
    def reversibility(
        cls, tool_name: str, read_only: bool
    ) -> ApprovalReversible | None:
        """Return this vendor's reversibility opinion for ``tool_name``, or ``None`` to defer to the caller."""
        return None

    @staticmethod
    def _normalize_server_name(value: str) -> str:
        """Strip transport decoration affixes and lowercase the server name for token matching."""
        normalized = value.strip().lower()
        if normalized.startswith("mcp_"):
            normalized = normalized[len("mcp_") :]
        if normalized.endswith("_mcp"):
            normalized = normalized[: -len("_mcp")]
        return normalized.removesuffix("_com").removesuffix("-com")

    @staticmethod
    def _stringify(raw: object) -> str | None:
        """Normalise a single argument value to a non-empty string.

        Booleans render as Yes/No; ints / floats as their str(); strings
        are stripped and capped. Containers and None return None — the
        recogniser then omits the row, leaving slots for other keys.
        """

        if raw is None:
            return None
        if isinstance(raw, bool):
            return "Yes" if raw else "No"
        if isinstance(raw, (int, float)):
            return str(raw)
        if isinstance(raw, str):
            stripped = raw.strip()
            if not stripped:
                return None
            return stripped[:_VALUE_MAX]
        return None


class SlackApprovalRecogniser(ApprovalParamRecogniser):
    """Approval-param recogniser for Slack MCP servers."""

    vendor_tokens: ClassVar[tuple[str, ...]] = ("slack",)

    # Only ``post_message`` opts into the undo window; other Slack writes
    # (channel admin, DM management) lack compensating actions.
    _REVERSIBLE_TOOLS: ClassVar[frozenset[str]] = frozenset(
        {"post_message", "chat.postMessage", "chat_postMessage"}
    )

    @classmethod
    def reversibility(
        cls, tool_name: str, read_only: bool
    ) -> ApprovalReversible | None:
        if read_only:
            return None
        if tool_name in cls._REVERSIBLE_TOOLS:
            return ApprovalReversible.YES
        return None

    def recognise(self, arguments: Mapping[str, object]) -> tuple[ApprovalParam, ...]:
        params: list[ApprovalParam] = []
        channel = self._stringify(arguments.get("channel"))
        if channel:
            params.append(ApprovalParam(label="Channel", value=channel))
        if "thread_ts" in arguments:
            in_thread = "Yes" if arguments.get("thread_ts") else "No"
            params.append(ApprovalParam(label="In thread", value=in_thread))
        recipient = self._stringify(arguments.get("user") or arguments.get("to"))
        if recipient:
            params.append(ApprovalParam(label="Recipient", value=recipient))
        return tuple(params)


class GitHubApprovalRecogniser(ApprovalParamRecogniser):
    """Approval-param recogniser for GitHub MCP servers."""

    vendor_tokens: ClassVar[tuple[str, ...]] = ("github",)

    def recognise(self, arguments: Mapping[str, object]) -> tuple[ApprovalParam, ...]:
        params: list[ApprovalParam] = []
        owner = self._stringify(arguments.get("owner") or arguments.get("org"))
        repo = self._stringify(arguments.get("repo"))
        pr_number = arguments.get("pull_number") or arguments.get("number")
        if owner and repo:
            value = f"{owner}/{repo}"
            if isinstance(pr_number, int) or self._stringify(pr_number):
                value = f"{value} · #{pr_number}"
            params.append(ApprovalParam(label="Repo", value=value[:_VALUE_MAX]))
        elif repo:
            params.append(ApprovalParam(label="Repo", value=repo))
        head = self._stringify(arguments.get("head"))
        base = self._stringify(arguments.get("base"))
        if head and base:
            params.append(
                ApprovalParam(label="Branch", value=f"{head} → {base}"[:_VALUE_MAX])
            )
        elif head or base:
            params.append(ApprovalParam(label="Branch", value=(head or base) or ""))
        title = self._stringify(arguments.get("title"))
        if title:
            params.append(ApprovalParam(label="Title", value=title))
        return tuple(params)


class LinearApprovalRecogniser(ApprovalParamRecogniser):
    """Approval-param recogniser for Linear MCP servers."""

    vendor_tokens: ClassVar[tuple[str, ...]] = ("linear",)

    _PRIORITY: ClassVar[dict[int, str]] = {
        0: "No priority",
        1: "P1 (Urgent)",
        2: "P2 (High)",
        3: "P3 (Medium)",
        4: "P4 (Low)",
    }

    def recognise(self, arguments: Mapping[str, object]) -> tuple[ApprovalParam, ...]:
        params: list[ApprovalParam] = []
        team = self._stringify(arguments.get("team") or arguments.get("team_id"))
        project = self._stringify(
            arguments.get("project") or arguments.get("project_id")
        )
        if team and project:
            params.append(
                ApprovalParam(label="Scope", value=f"{team} / {project}"[:_VALUE_MAX])
            )
        elif team:
            params.append(ApprovalParam(label="Team", value=team))
        elif project:
            params.append(ApprovalParam(label="Project", value=project))
        priority = arguments.get("priority")
        if isinstance(priority, int) and priority in self._PRIORITY:
            params.append(
                ApprovalParam(label="Priority", value=self._PRIORITY[priority])
            )
        title = self._stringify(arguments.get("title"))
        if title:
            params.append(ApprovalParam(label="Title", value=title))
        assignee = self._stringify(
            arguments.get("assignee") or arguments.get("assignee_id")
        )
        if assignee:
            params.append(ApprovalParam(label="Assignee", value=assignee))
        return tuple(params)


class NotionApprovalRecogniser(ApprovalParamRecogniser):
    """Approval-param recogniser for Notion MCP servers."""

    vendor_tokens: ClassVar[tuple[str, ...]] = ("notion",)

    def recognise(self, arguments: Mapping[str, object]) -> tuple[ApprovalParam, ...]:
        params: list[ApprovalParam] = []
        parent = arguments.get("parent")
        parent_added = False
        if isinstance(parent, Mapping):
            db_id = self._stringify(parent.get("database_id"))
            page_id = self._stringify(parent.get("page_id"))
            if db_id:
                params.append(ApprovalParam(label="Database", value=db_id))
                parent_added = True
            elif page_id:
                params.append(ApprovalParam(label="Parent page", value=page_id))
                parent_added = True
        if not parent_added:
            page_id = self._stringify(arguments.get("page_id"))
            if page_id:
                params.append(ApprovalParam(label="Page", value=page_id))
        title = self._extract_title(arguments)
        if title:
            params.append(ApprovalParam(label="Title", value=title))
        return tuple(params)

    @classmethod
    def _extract_title(cls, arguments: Mapping[str, object]) -> str | None:
        """Extract a page/database title from ``arguments``, trying top-level then nested ``properties``."""
        title = arguments.get("title")
        if isinstance(title, str):
            stripped = title.strip()
            if stripped:
                return stripped[:_VALUE_MAX]
        properties = arguments.get("properties")
        if isinstance(properties, Mapping):
            prop_title = properties.get("title")
            if isinstance(prop_title, str):
                stripped = prop_title.strip()
                if stripped:
                    return stripped[:_VALUE_MAX]
        return None


class AtlassianApprovalRecogniser(ApprovalParamRecogniser):
    """Approval-param recogniser for Atlassian (Jira / Confluence) MCP servers."""

    vendor_tokens: ClassVar[tuple[str, ...]] = (
        "atlassian",
        "jira",
        "confluence",
    )

    def recognise(self, arguments: Mapping[str, object]) -> tuple[ApprovalParam, ...]:
        params: list[ApprovalParam] = []
        project = self._stringify(
            arguments.get("project") or arguments.get("project_key")
        )
        issue_type = self._stringify(
            arguments.get("issue_type") or arguments.get("issuetype")
        )
        if project and issue_type:
            params.append(
                ApprovalParam(
                    label="Project",
                    value=f"{project} · {issue_type}"[:_VALUE_MAX],
                )
            )
        elif project:
            params.append(ApprovalParam(label="Project", value=project))
        issue = self._stringify(arguments.get("issue") or arguments.get("issue_key"))
        if issue:
            params.append(ApprovalParam(label="Issue", value=issue))
        summary = self._stringify(arguments.get("summary"))
        if summary:
            params.append(ApprovalParam(label="Summary", value=summary))
        return tuple(params)


class ApprovalParamRecogniserRegistry:
    """Central dispatch registry; first matching recogniser in ``_RECOGNISERS`` wins."""

    _RECOGNISERS: ClassVar[tuple[ApprovalParamRecogniser, ...]] = (
        SlackApprovalRecogniser(),
        GitHubApprovalRecogniser(),
        LinearApprovalRecogniser(),
        NotionApprovalRecogniser(),
        AtlassianApprovalRecogniser(),
    )

    @classmethod
    def recognise(
        cls,
        *,
        server_name: str,
        arguments: Mapping[str, object],
    ) -> tuple[ApprovalParam, ...] | None:
        """Return the first matching recogniser's projection.

        Returns ``None`` when no vendor token matches so the caller can
        fall through to the generic allow-list projector.
        """

        for recogniser in cls._RECOGNISERS:
            if recogniser.matches_server_name(server_name):
                return recogniser.recognise(arguments)[:APPROVAL_MAX_PARAMS]
        return None

    @classmethod
    def reversibility_for(
        cls, *, server_name: str, tool_name: str, read_only: bool
    ) -> ApprovalReversible | None:
        """Return the first matching recogniser's reversibility opinion, or ``None`` if none claimed it."""

        for recogniser in cls._RECOGNISERS:
            if recogniser.matches_server_name(server_name):
                return recogniser.reversibility(tool_name, read_only)
        return None
