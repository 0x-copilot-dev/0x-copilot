"""Email dispatcher port (PR 5.1).

Magic-link auth needs to send a one-time URL to a user's mailbox. The
*shape* of the call is the same regardless of where the dispatch lands:
SES, SMTP, Postmark, a queue with a worker pulling from it. Adapter
choice is per-deploy and out of scope for this PR.

We ship a port (``EmailDispatcherPort``) and a single fallback adapter
(``LoggingEmailDispatcher``) that writes the dispatch as a structured
log line. Production deploys are expected to inject a real adapter at
``app.py`` construction time, the same shape we use for ``TokenVault``.

Implementations MUST NOT raise on transient failure: the magic-link
endpoint has already returned 202 to the caller before this method
runs. Buffer + retry + dead-letter live inside the adapter; the
service-layer caller treats dispatch as fire-and-forget.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Protocol


class EmailDispatcherPort(Protocol):
    def send_magic_link(
        self,
        *,
        to_email: str,
        org_display_name: str | None,
        login_url: str,
        expires_minutes: int,
        request_ip: str | None,
        request_user_agent: str | None,
    ) -> None: ...


@dataclass
class LoggingEmailDispatcher:
    """Dev / single-tenant fallback. Logs the dispatch; never sends mail.

    Production deploys MUST inject a real adapter at app construction.
    A red banner in ``backend_app/app.py`` (when this adapter is wired
    into a non-development profile) is left to the deploy hardening pass
    in a follow-up.
    """

    logger: logging.Logger

    def send_magic_link(
        self,
        *,
        to_email: str,
        org_display_name: str | None,
        login_url: str,
        expires_minutes: int,
        request_ip: str | None,
        request_user_agent: str | None,
    ) -> None:
        # The login_url contains the plaintext token. Logging it is fine
        # in dev (the same value is in the user's mailbox by definition
        # in production); operators must keep dev logs out of any
        # downstream system that retains them beyond the token's TTL.
        self.logger.info(
            "magic_link.dispatch",
            extra={
                "magic_link_to": to_email,
                "magic_link_org": org_display_name,
                "magic_link_url": login_url,
                "magic_link_expires_minutes": expires_minutes,
                "magic_link_request_ip": request_ip,
                "magic_link_request_ua": request_user_agent,
            },
        )


__all__ = ["EmailDispatcherPort", "LoggingEmailDispatcher"]
