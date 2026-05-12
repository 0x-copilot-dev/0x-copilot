"""Tests for the PR 4.1 follow-up: notification dispatcher consults the
recipient's per-user notification matrix before fanning out.

Until this PR landed, ``InboxAndEmailNotificationDispatcher`` always
fired the inbox + email channels (gated only by env var). The matrix
the user persisted in Settings → Notifications had no reader. This
behavior is the regression the tests below pin in place — the matrix is
honored on every ``notify_*`` path, channels disabled by the user are
skipped, and a fetcher that misbehaves never breaks dispatch.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from agent_runtime.api.notifications import (
    InboxAndEmailNotificationDispatcher,
    InMemoryUserPreferenceFetcher,
    LoggingSlackDispatcher,
    NotificationMatrix,
    SlackDispatcherPort,
    UserPreferenceFetcher,
    _DefaultsOnlyUserPreferenceFetcher,
    _channel_allowed,
)
from runtime_api.schemas import (
    ApprovalDecision,
    ApprovalRequestRecord,
    ShareSnapshot,
)
from runtime_api.schemas.common import ApprovalStatus

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _approval(*, user_id: str, org_id: str = "org_acme") -> ApprovalRequestRecord:
    """A minimally-valid approval record. Fields not exercised by the
    dispatcher path are filled with deterministic placeholders."""

    return ApprovalRequestRecord(
        approval_id="ap_01",
        run_id="run_01",
        conversation_id="conv_01",
        org_id=org_id,
        user_id=user_id,
        status=ApprovalStatus.PENDING,
        created_at=datetime.now(timezone.utc),
        metadata={"tool": "post_to_slack"},
    )


def _share(
    *, created_by_user_id: str = "user_sarah", org_id: str = "org_acme"
) -> ShareSnapshot:
    return ShareSnapshot(
        share_id="share_01",
        org_id=org_id,
        conversation_id="conv_01",
        snapshot_at=datetime.now(timezone.utc),
        view_access="workspace",
        recipient_user_ids=(),
        sources_visible_to_viewer=False,
        created_by_user_id=created_by_user_id,
    )


class _RecordingInbox:
    """Inbox publish stub that captures every call."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []

    async def __call__(
        self, approval: ApprovalRequestRecord, event_type: str, actor_user_id: str
    ) -> None:
        self.calls.append((approval.approval_id, event_type, actor_user_id))


class _RecordingPoster:
    """HTTP poster stub that captures payloads + always returns 200."""

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def __call__(
        self,
        url: str,
        headers: dict[str, str],
        body: dict[str, object],
    ) -> tuple[int, dict[str, object]]:
        self.calls.append({"url": url, "headers": headers, "body": dict(body)})
        return 200, {}


class _RecordingSlack:
    """Slack dispatcher stub that captures send calls."""

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def send_notification(
        self,
        *,
        recipient_user_id: str,
        org_id: str,
        template: str,
        text: str,
        link_url: str | None = None,
        metadata: dict[str, object] | None = None,
    ) -> None:
        self.calls.append(
            {
                "recipient_user_id": recipient_user_id,
                "org_id": org_id,
                "template": template,
                "text": text,
                "metadata": metadata or {},
            }
        )


class _RaisingSlack:
    """Slack adapter that raises — confirms the dispatcher's safe-wrap."""

    async def send_notification(
        self,
        *,
        recipient_user_id: str,
        org_id: str,
        template: str,
        text: str,
        link_url: str | None = None,
        metadata: dict[str, object] | None = None,
    ) -> None:
        raise RuntimeError("simulated Slack outage")


class _RaisingFetcher:
    """Pretends to be a real fetcher that misbehaves at the boundary —
    used to confirm the dispatcher swallows failures and falls back to
    deployment defaults (the safe choice for "should I notify?")."""

    async def fetch_notification_matrix(
        self,
        *,
        user_id: str,
        org_id: str,
    ) -> NotificationMatrix | None:
        raise RuntimeError("simulated backend outage")


# ---------------------------------------------------------------------------
# _channel_allowed — the narrow predicate the dispatcher uses
# ---------------------------------------------------------------------------


class TestChannelAllowed:
    def test_user_opt_out_overrides_default(self) -> None:
        # Sarah turned off email for approval_needed; the predicate
        # honors her choice even though the default is True.
        matrix: NotificationMatrix = {
            "approval_needed": {"email": False, "slack": False, "desktop": True}
        }

        assert (
            _channel_allowed(matrix, event="approval_needed", channel="email") is False
        )
        assert (
            _channel_allowed(matrix, event="approval_needed", channel="desktop") is True
        )

    def test_no_matrix_uses_deployment_defaults(self) -> None:
        # User without a preferences row → opt-out semantics: email +
        # desktop fire by default for actionable events; weekly_digest
        # for slack stays off (matches the FE's documented defaults).
        assert _channel_allowed(None, event="approval_needed", channel="email")
        assert _channel_allowed(None, event="approval_needed", channel="desktop")
        assert _channel_allowed(None, event="approval_needed", channel="slack") is False
        assert _channel_allowed(None, event="weekly_digest", channel="desktop") is False

    def test_partial_matrix_uses_default_for_missing_cell(self) -> None:
        # User row exists but is missing the cell we care about — fall
        # through to the default. Forward-compat with deployments that
        # ship a matrix written before a new event was introduced.
        matrix: NotificationMatrix = {
            "mention": {"email": False, "slack": False, "desktop": False}
            # approval_needed row absent entirely
        }

        assert _channel_allowed(matrix, event="approval_needed", channel="email")


# ---------------------------------------------------------------------------
# notify_approval_assigned
# ---------------------------------------------------------------------------


class TestNotifyApprovalAssigned:
    async def test_default_fetcher_fires_inbox_and_email(self) -> None:
        # No fetcher wired (= deployment defaults) + email channel
        # enabled. Both channels should fire — the same behavior as
        # before this PR.
        inbox = _RecordingInbox()
        poster = _RecordingPoster()
        dispatcher = InboxAndEmailNotificationDispatcher(
            publish_inbox=inbox,
            post=poster,
            service_token="tkn",
        )

        await dispatcher.notify_approval_assigned(
            approval=_approval(user_id="user_marcus"),
            forwarded_by_user_id="user_sarah",
        )

        assert len(inbox.calls) == 1
        assert inbox.calls[0][1] == "approval_assigned"
        assert len(poster.calls) == 1

    async def test_user_opted_out_of_email_skips_email_only(self) -> None:
        # Marcus opts out of email for approval_needed; the inbox push
        # still fires (he kept desktop on).
        inbox = _RecordingInbox()
        poster = _RecordingPoster()
        fetcher = InMemoryUserPreferenceFetcher(
            {
                "user_marcus": {
                    "approval_needed": {
                        "email": False,
                        "slack": False,
                        "desktop": True,
                    }
                }
            }
        )
        dispatcher = InboxAndEmailNotificationDispatcher(
            publish_inbox=inbox,
            post=poster,
            service_token="tkn",
            preference_fetcher=fetcher,
        )

        await dispatcher.notify_approval_assigned(
            approval=_approval(user_id="user_marcus"),
            forwarded_by_user_id="user_sarah",
        )

        assert len(inbox.calls) == 1
        assert poster.calls == []

    async def test_user_opted_out_of_desktop_skips_inbox(self) -> None:
        # The recipient turned off the in-product (desktop) push but
        # left email on.
        inbox = _RecordingInbox()
        poster = _RecordingPoster()
        fetcher = InMemoryUserPreferenceFetcher(
            {
                "user_marcus": {
                    "approval_needed": {
                        "email": True,
                        "slack": False,
                        "desktop": False,
                    }
                }
            }
        )
        dispatcher = InboxAndEmailNotificationDispatcher(
            publish_inbox=inbox,
            post=poster,
            service_token="tkn",
            preference_fetcher=fetcher,
        )

        await dispatcher.notify_approval_assigned(
            approval=_approval(user_id="user_marcus"),
            forwarded_by_user_id="user_sarah",
        )

        assert inbox.calls == []
        assert len(poster.calls) == 1

    async def test_user_opted_out_of_all_channels_no_fan_out(self) -> None:
        inbox = _RecordingInbox()
        poster = _RecordingPoster()
        fetcher = InMemoryUserPreferenceFetcher(
            {
                "user_marcus": {
                    "approval_needed": {
                        "email": False,
                        "slack": False,
                        "desktop": False,
                    }
                }
            }
        )
        dispatcher = InboxAndEmailNotificationDispatcher(
            publish_inbox=inbox,
            post=poster,
            service_token="tkn",
            preference_fetcher=fetcher,
        )

        await dispatcher.notify_approval_assigned(
            approval=_approval(user_id="user_marcus"),
            forwarded_by_user_id="user_sarah",
        )

        assert inbox.calls == []
        assert poster.calls == []

    async def test_email_unconfigured_skips_email(self) -> None:
        # Even if the matrix says "email me", a deployment without an
        # HttpPoster wired skips email — config presence is the gate.
        inbox = _RecordingInbox()
        fetcher = InMemoryUserPreferenceFetcher(
            {
                "user_marcus": {
                    "approval_needed": {
                        "email": True,
                        "slack": True,
                        "desktop": True,
                    }
                }
            }
        )
        dispatcher = InboxAndEmailNotificationDispatcher(
            publish_inbox=inbox,
            service_token="tkn",
            preference_fetcher=fetcher,
        )

        await dispatcher.notify_approval_assigned(
            approval=_approval(user_id="user_marcus"),
            forwarded_by_user_id="user_sarah",
        )

        assert len(inbox.calls) == 1


# ---------------------------------------------------------------------------
# notify_approval_resolved (maps to event="run_finished")
# ---------------------------------------------------------------------------


class TestNotifyApprovalResolved:
    async def test_resolution_uses_run_finished_defaults(self) -> None:
        # Defaults: run_finished → desktop only. No fetcher → defaults
        # apply → desktop fires, email does not.
        inbox = _RecordingInbox()
        poster = _RecordingPoster()
        dispatcher = InboxAndEmailNotificationDispatcher(
            publish_inbox=inbox,
            post=poster,
            service_token="tkn",
        )

        await dispatcher.notify_approval_resolved(
            approval=_approval(user_id="user_marcus"),
            decision=ApprovalDecision.APPROVED,
            decided_by_user_id="user_sarah",
        )

        assert len(inbox.calls) == 1
        assert inbox.calls[0][1] == "approval_resolved"
        # Email default for run_finished is False → no poster call.
        assert poster.calls == []

    async def test_resolution_email_when_user_opts_in(self) -> None:
        # Marcus turned email on for run_finished — opt-in beats
        # default. Both channels fire.
        inbox = _RecordingInbox()
        poster = _RecordingPoster()
        fetcher = InMemoryUserPreferenceFetcher(
            {
                "user_marcus": {
                    "run_finished": {
                        "email": True,
                        "slack": False,
                        "desktop": True,
                    }
                }
            }
        )
        dispatcher = InboxAndEmailNotificationDispatcher(
            publish_inbox=inbox,
            post=poster,
            service_token="tkn",
            preference_fetcher=fetcher,
        )

        await dispatcher.notify_approval_resolved(
            approval=_approval(user_id="user_marcus"),
            decision=ApprovalDecision.APPROVED,
            decided_by_user_id="user_sarah",
        )

        assert len(inbox.calls) == 1
        assert len(poster.calls) == 1


# ---------------------------------------------------------------------------
# notify_share_forked (maps to event="mention")
# ---------------------------------------------------------------------------


class TestNotifyShareForked:
    async def test_share_fork_logs_when_default_allows(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        # Default for mention/desktop is True → log line emitted.
        import logging

        dispatcher = InboxAndEmailNotificationDispatcher(
            publish_inbox=_RecordingInbox(),
            post=None,
        )

        with caplog.at_level(logging.INFO, logger="agent_runtime.api.notifications"):
            await dispatcher.notify_share_forked(
                share=_share(),
                forked_by_user_id="user_priya",
                new_conversation_id="conv_forked",
            )

        records = [r for r in caplog.records if r.message == "share.notify.forked"]
        assert len(records) == 1

    async def test_share_fork_silenced_when_user_opted_out(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        import logging

        fetcher = InMemoryUserPreferenceFetcher(
            {
                "user_sarah": {
                    "mention": {
                        "email": False,
                        "slack": False,
                        "desktop": False,
                    }
                }
            }
        )
        dispatcher = InboxAndEmailNotificationDispatcher(
            publish_inbox=_RecordingInbox(),
            post=None,
            preference_fetcher=fetcher,
        )

        with caplog.at_level(logging.INFO, logger="agent_runtime.api.notifications"):
            await dispatcher.notify_share_forked(
                share=_share(created_by_user_id="user_sarah"),
                forked_by_user_id="user_priya",
                new_conversation_id="conv_forked",
            )

        records = [r for r in caplog.records if r.message == "share.notify.forked"]
        assert records == []


# ---------------------------------------------------------------------------
# Robustness: fetcher misbehavior never breaks dispatch
# ---------------------------------------------------------------------------


class TestFetcherFailureFallthrough:
    async def test_raising_fetcher_falls_back_to_defaults(self) -> None:
        # A fetcher that raises must not break the dispatch path; the
        # caller falls through to deployment defaults (silence is not a
        # safe regression — opt-out is).
        inbox = _RecordingInbox()
        poster = _RecordingPoster()
        dispatcher = InboxAndEmailNotificationDispatcher(
            publish_inbox=inbox,
            post=poster,
            service_token="tkn",
            preference_fetcher=_RaisingFetcher(),
        )

        # No exception — both channels still fire (defaults: True/True).
        await dispatcher.notify_approval_assigned(
            approval=_approval(user_id="user_marcus"),
            forwarded_by_user_id="user_sarah",
        )

        assert len(inbox.calls) == 1
        assert len(poster.calls) == 1


class TestSlackChannel:
    async def test_slack_unconfigured_no_calls(self) -> None:
        # No slack adapter wired → never dispatch slack even when the
        # matrix would allow it. Slack is gated on config presence, so a
        # deployment that hasn't configured Slack never sees stray
        # "would-have-sent" lines.
        fetcher = InMemoryUserPreferenceFetcher(
            {
                "user_marcus": {
                    "approval_needed": {
                        "email": False,
                        "slack": True,
                        "desktop": True,
                    }
                }
            }
        )
        dispatcher = InboxAndEmailNotificationDispatcher(
            publish_inbox=_RecordingInbox(),
            post=None,
            preference_fetcher=fetcher,
        )

        # No assertion needed beyond "this doesn't blow up" — there's no
        # slack adapter to record into.
        await dispatcher.notify_approval_assigned(
            approval=_approval(user_id="user_marcus"),
            forwarded_by_user_id="user_sarah",
        )

    async def test_slack_configured_and_user_opted_in(self) -> None:
        slack = _RecordingSlack()
        fetcher = InMemoryUserPreferenceFetcher(
            {
                "user_marcus": {
                    "approval_needed": {
                        "email": False,
                        "slack": True,
                        "desktop": False,
                    }
                }
            }
        )
        dispatcher = InboxAndEmailNotificationDispatcher(
            publish_inbox=_RecordingInbox(),
            post=None,
            preference_fetcher=fetcher,
            slack=slack,
        )

        await dispatcher.notify_approval_assigned(
            approval=_approval(user_id="user_marcus"),
            forwarded_by_user_id="user_sarah",
        )

        assert len(slack.calls) == 1
        call = slack.calls[0]
        assert call["recipient_user_id"] == "user_marcus"
        assert call["template"] == "approval_assigned"
        assert "user_sarah" in str(call["text"])
        # Metadata carries IDs the Slack adapter can use to render rich
        # blocks / link back to the chat.
        meta = call["metadata"]
        assert isinstance(meta, dict)
        assert meta["forwarded_by_user_id"] == "user_sarah"

    async def test_slack_configured_but_user_opted_out(self) -> None:
        # Even with slack configured, the user's matrix decides.
        slack = _RecordingSlack()
        fetcher = InMemoryUserPreferenceFetcher(
            {
                "user_marcus": {
                    "approval_needed": {
                        "email": False,
                        "slack": False,
                        "desktop": True,
                    }
                }
            }
        )
        dispatcher = InboxAndEmailNotificationDispatcher(
            publish_inbox=_RecordingInbox(),
            post=None,
            preference_fetcher=fetcher,
            slack=slack,
        )

        await dispatcher.notify_approval_assigned(
            approval=_approval(user_id="user_marcus"),
            forwarded_by_user_id="user_sarah",
        )

        assert slack.calls == []

    async def test_resolution_uses_run_finished_for_slack(self) -> None:
        # Default for run_finished is slack=False. With matrix opt-in,
        # the resolution event reaches Slack; without, no call.
        slack = _RecordingSlack()
        fetcher = InMemoryUserPreferenceFetcher(
            {
                "user_marcus": {
                    "run_finished": {
                        "email": False,
                        "slack": True,
                        "desktop": True,
                    }
                }
            }
        )
        dispatcher = InboxAndEmailNotificationDispatcher(
            publish_inbox=_RecordingInbox(),
            post=None,
            preference_fetcher=fetcher,
            slack=slack,
        )

        await dispatcher.notify_approval_resolved(
            approval=_approval(user_id="user_marcus"),
            decision=ApprovalDecision.APPROVED,
            decided_by_user_id="user_sarah",
        )

        assert len(slack.calls) == 1
        assert slack.calls[0]["template"] == "approval_resolved"

    async def test_share_fork_uses_mention_for_slack(self) -> None:
        slack = _RecordingSlack()
        fetcher = InMemoryUserPreferenceFetcher(
            {
                "user_sarah": {
                    "mention": {
                        "email": False,
                        "slack": True,
                        "desktop": False,
                    }
                }
            }
        )
        dispatcher = InboxAndEmailNotificationDispatcher(
            publish_inbox=_RecordingInbox(),
            post=None,
            preference_fetcher=fetcher,
            slack=slack,
        )

        await dispatcher.notify_share_forked(
            share=_share(created_by_user_id="user_sarah"),
            forked_by_user_id="user_priya",
            new_conversation_id="conv_forked",
        )

        assert len(slack.calls) == 1
        assert slack.calls[0]["template"] == "share_forked"
        # Recipient is the share creator, not the forker.
        assert slack.calls[0]["recipient_user_id"] == "user_sarah"

    async def test_slack_raise_does_not_break_dispatch(self) -> None:
        # Even if Slack adapter raises, the dispatcher swallows + logs.
        # The other channels still fire.
        inbox = _RecordingInbox()
        dispatcher = InboxAndEmailNotificationDispatcher(
            publish_inbox=inbox,
            post=None,
            slack=_RaisingSlack(),
        )

        # Default fetcher → defaults; approval_needed slack default is
        # False, so the path isn't even invoked. Add an opt-in matrix.
        fetcher = InMemoryUserPreferenceFetcher(
            {
                "user_marcus": {
                    "approval_needed": {
                        "email": False,
                        "slack": True,
                        "desktop": True,
                    }
                }
            }
        )
        dispatcher._preference_fetcher = fetcher  # noqa: SLF001 — test seam

        # No exception bubbles out.
        await dispatcher.notify_approval_assigned(
            approval=_approval(user_id="user_marcus"),
            forwarded_by_user_id="user_sarah",
        )

        # Inbox still fires (independent channel).
        assert len(inbox.calls) == 1


class TestLoggingSlackDispatcher:
    async def test_emits_structured_log(self, caplog: pytest.LogCaptureFixture) -> None:
        import logging

        adapter = LoggingSlackDispatcher()

        with caplog.at_level(logging.INFO, logger="agent_runtime.api.notifications"):
            await adapter.send_notification(
                recipient_user_id="user_marcus",
                org_id="org_acme",
                template="approval_assigned",
                text="A short message",
                metadata={"approval_id": "ap_01"},
            )

        records = [r for r in caplog.records if r.message == "slack.notify.dispatch"]
        assert len(records) == 1

    def test_satisfies_protocol(self) -> None:
        adapter: SlackDispatcherPort = LoggingSlackDispatcher()
        assert adapter is not None


class TestDefaultsOnlyFetcher:
    async def test_default_fetcher_returns_none(self) -> None:
        fetcher = _DefaultsOnlyUserPreferenceFetcher()

        result = await fetcher.fetch_notification_matrix(
            user_id="anyone", org_id="org_acme"
        )

        assert result is None

    def test_default_fetcher_satisfies_protocol(self) -> None:
        fetcher: UserPreferenceFetcher = _DefaultsOnlyUserPreferenceFetcher()
        assert fetcher is not None
