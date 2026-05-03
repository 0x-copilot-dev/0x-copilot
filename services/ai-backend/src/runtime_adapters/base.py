"""Shared business logic for runtime API adapter stores.

Extracted from the in-memory and Postgres stores to eliminate duplication
and ensure consistent behaviour across backends.
"""

from __future__ import annotations

from collections.abc import Callable

from runtime_api.schemas import (
    AgentRunStatus,
    ConversationRecord,
    CreateRunRequest,
    MessageRecord,
    MessageRole,
)

SYNTHETIC_ASSISTANT_MESSAGE_PREFIX = "assistant-"

RuntimeApiServiceTerminalStatuses = frozenset(
    {
        AgentRunStatus.CANCELLED,
        AgentRunStatus.COMPLETED,
        AgentRunStatus.FAILED,
        AgentRunStatus.TIMED_OUT,
    }
)

MessageLookupFn = Callable[[str], MessageRecord | None]
LatestMessageIdFn = Callable[[str, str], str | None]
LatestAssistantForRunFn = Callable[[str, str, str], str | None]


def message_metadata(request: CreateRunRequest) -> dict[str, object]:
    """Build the metadata dict for a user message created from a run request."""

    metadata: dict[str, object] = {}
    quote = request.quote_payload()
    if quote is not None:
        metadata["quote"] = quote
    if request.source_message_id is not None:
        metadata["source_message_id"] = request.source_message_id
    if request.branch_id is not None:
        metadata["branch_id"] = request.branch_id
    branch = request.branch_payload()
    if branch is not None:
        metadata["branch"] = branch
    if request.regenerate_from_message_id is not None:
        metadata["regenerate_from_message_id"] = request.regenerate_from_message_id
    return metadata


def regenerated_user_message(
    message_id: str,
    *,
    get_message: MessageLookupFn,
) -> MessageRecord | None:
    """Walk from *message_id* up to its user-role origin for regeneration."""

    message = get_message(message_id)
    if message is None:
        return None
    if message.role == MessageRole.USER:
        return message
    if message.parent_message_id is None:
        return None
    parent = get_message(message.parent_message_id)
    if parent is None or parent.role != MessageRole.USER:
        return None
    return parent


def real_assistant_message_id_for_synthetic_parent(
    *,
    parent_message_id: str,
    org_id: str,
    conversation_id: str,
    find_latest_assistant_for_run: LatestAssistantForRunFn,
) -> str | None:
    """Resolve a synthetic ``assistant-<run_id>`` parent to a real message ID."""

    if not parent_message_id.startswith(SYNTHETIC_ASSISTANT_MESSAGE_PREFIX):
        return None
    run_id = parent_message_id.removeprefix(SYNTHETIC_ASSISTANT_MESSAGE_PREFIX)
    return find_latest_assistant_for_run(org_id, conversation_id, run_id)


def parent_message_id_for_run_request(
    *,
    request: CreateRunRequest,
    org_id: str,
    conversation_id: str,
    get_latest_message_id: LatestMessageIdFn,
    find_latest_assistant_for_run: LatestAssistantForRunFn,
) -> str | None:
    """Determine the parent message ID for a new run request."""

    parent_message_id = request.parent_message_id
    if parent_message_id is None:
        return get_latest_message_id(org_id, conversation_id)
    return (
        real_assistant_message_id_for_synthetic_parent(
            parent_message_id=parent_message_id,
            org_id=org_id,
            conversation_id=conversation_id,
            find_latest_assistant_for_run=find_latest_assistant_for_run,
        )
        or parent_message_id
    )


def message_for_run_request(
    *,
    request: CreateRunRequest,
    conversation: ConversationRecord,
    get_message: MessageLookupFn,
    get_latest_message_id: LatestMessageIdFn,
    find_latest_assistant_for_run: LatestAssistantForRunFn,
    run_id_for_message: str | None = None,
) -> MessageRecord:
    """Build or retrieve the user message for a run request.

    Storage-specific lookups are injected via callbacks so both in-memory and
    Postgres stores can share this orchestration logic.
    """

    context = request.runtime_context
    if request.regenerate_from_message_id is not None:
        regen = regenerated_user_message(
            request.regenerate_from_message_id,
            get_message=get_message,
        )
        if regen is not None:
            return regen
    parent_id = parent_message_id_for_run_request(
        request=request,
        org_id=conversation.org_id,
        conversation_id=conversation.conversation_id,
        get_latest_message_id=get_latest_message_id,
        find_latest_assistant_for_run=find_latest_assistant_for_run,
    )
    return MessageRecord(
        conversation_id=conversation.conversation_id,
        org_id=conversation.org_id,
        run_id=run_id_for_message,
        role=MessageRole.USER,
        content_text=request.user_input,
        content_format=request.content_format,
        content=tuple(
            part.model_dump(
                mode="json",
                exclude_none=True,
                exclude_defaults=True,
            )
            for part in request.content
        ),
        attachments=tuple(
            attachment.model_dump(
                mode="json",
                exclude_none=True,
                exclude_defaults=True,
            )
            for attachment in request.attachments
        ),
        quote=request.quote_payload(),
        metadata=message_metadata(request),
        parent_message_id=parent_id,
        source_message_id=request.source_message_id,
        branch_id=request.branch_id,
        trace_id=context.trace_id,
    )


def normalize_risk_class(metadata: dict[str, object]) -> str:
    """Normalize ``risk_level`` metadata to a safe risk class value."""

    risk_class = str(metadata.get("risk_level") or "low").lower()
    if risk_class == "critical":
        risk_class = "high"
    if risk_class not in {"low", "medium", "high"}:
        risk_class = "low"
    return risk_class


def derive_action_summary(metadata: dict[str, object]) -> str:
    """Derive a human-readable action summary from approval metadata."""

    return str(
        metadata.get("message")
        or metadata.get("reason")
        or "Approve this runtime action."
    )
