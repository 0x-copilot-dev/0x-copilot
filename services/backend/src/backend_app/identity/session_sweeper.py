"""Background sweeper for expired sessions (A2).

Runs in the FastAPI lifespan task: every ``SESSION_SWEEPER_INTERVAL_SECONDS``
(default 600s) it deletes session rows where
``expires_at < now() - retention_after_expiry_seconds``. The retention
window keeps recently-expired rows around so an audit query like "when did
this session end" still resolves; the policy default is 30 days.

The sweeper writes one ``identity_audit_events`` row per non-zero pass so
the audit history records that retention was applied.
"""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import suppress
from datetime import datetime, timezone

from backend_app.identity.sessions import SessionService
from backend_app.identity.store import IdentityStore


_LOGGER = logging.getLogger(__name__)

_DEFAULT_INTERVAL_SECONDS = 600
_MIN_INTERVAL_SECONDS = 5  # guard against catastrophic 0-interval misconfig


class SessionSweeper:
    """Periodic background loop. One per process; uses asyncio."""

    def __init__(
        self,
        *,
        sessions: SessionService,
        identity_store: IdentityStore | None = None,
        interval_seconds: int | None = None,
        env: dict[str, str] | None = None,
    ) -> None:
        env_map = env if env is not None else dict(os.environ)
        resolved_interval = (
            interval_seconds
            if interval_seconds is not None
            else (
                _read_int(
                    env_map,
                    "SESSION_SWEEPER_INTERVAL_SECONDS",
                    _DEFAULT_INTERVAL_SECONDS,
                )
            )
        )
        self._sessions = sessions
        self._identity_store = identity_store
        self._interval_seconds = max(_MIN_INTERVAL_SECONDS, resolved_interval)
        self._task: asyncio.Task[None] | None = None
        self._stopped = asyncio.Event()

    async def start(self) -> None:
        if self._task is not None:
            return
        self._stopped.clear()
        self._task = asyncio.create_task(self._loop(), name="session-sweeper")

    async def stop(self) -> None:
        if self._task is None:
            return
        self._stopped.set()
        self._task.cancel()
        with suppress(asyncio.CancelledError):
            await self._task
        self._task = None

    async def sweep_once(self) -> int:
        """Run a single sweep and return the row count purged."""

        return await asyncio.to_thread(self._sessions.sweep_expired)

    async def _loop(self) -> None:
        _LOGGER.info("session_sweeper_started interval_s=%d", self._interval_seconds)
        try:
            while not self._stopped.is_set():
                try:
                    purged = await self.sweep_once()
                except Exception as exc:  # pragma: no cover - defensive
                    _LOGGER.exception("session_sweeper_failed: %s", exc)
                    purged = 0
                if purged and self._identity_store is not None:
                    self._record_sweep_audit(purged)
                try:
                    await asyncio.wait_for(
                        self._stopped.wait(), timeout=self._interval_seconds
                    )
                except asyncio.TimeoutError:
                    continue
        finally:
            _LOGGER.info("session_sweeper_stopped")

    def _record_sweep_audit(self, count: int) -> None:
        if self._identity_store is None:
            return
        # Sweeper rows are not tied to one org. We record under the empty
        # string so listing per-org audit excludes them; an operator query
        # against the audit table directly will still surface them. A future
        # PR may introduce a dedicated system-audit table.
        from backend_app.contracts import IdentityAuditEventRecord

        record = IdentityAuditEventRecord(
            org_id="org_system",
            actor_user_id=None,
            subject_user_id=None,
            action="session.expired_swept",
            metadata={"count": count, "swept_at": _now().isoformat()},
        )
        try:
            self._identity_store.append_identity_audit(record)
        except Exception as exc:  # pragma: no cover - defensive
            _LOGGER.exception("session_sweeper_audit_failed: %s", exc)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _read_int(env: dict[str, str], key: str, default: int) -> int:
    raw = env.get(key, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


__all__ = ["SessionSweeper"]
