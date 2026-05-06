"""Operator CLI: backfill the typed notification_preferences tables
from the legacy ``user_preferences.preferences.notifications`` JSONB blob.

Reads every ``user_preferences`` row, projects the legacy v1 shape::

    {
      "matrix": {
        "mention":         {"email": True,  "slack": False, "desktop": True},
        "approval_needed": {"email": True,  "slack": False, "desktop": True},
        "run_finished":    {"email": False, "slack": False, "desktop": True},
        "weekly_digest":   {"email": True,  "slack": False, "desktop": False},
      }
    }

onto the v2 typed schema:

* ``notification_preferences (user_id, event_kind, channel, enabled)``
* ``notification_quiet_hours (user_id, enabled, from_local, to_local, tz)``

The mapping is deliberately narrow:

* v1 ``mention``         → v2 ``mention``
* v1 ``approval_needed`` → v2 ``approval_requested``
* v1 ``run_finished``    → v2 ``long_task_finished``
* v1 ``weekly_digest``   → v2 ``weekly_digest``
* v1 ``email`` channel   → v2 ``email``
* v1 ``desktop`` channel → v2 ``in_app``    (closest semantic match)
* v1 ``slack`` channel   → DROPPED          (no v2 equivalent; the
                                              dispatcher reads the v2
                                              channel set, so a copied
                                              ``slack`` row would never
                                              fire anyway.)

Idempotent — re-running upserts the same cells. Safe to schedule
behind a feature flag; until the flag flips, the v1 dispatcher keeps
reading the JSONB blob and the v2 dispatcher only reads from the
typed tables (so dual-read works without coordination).

Examples:
    BACKEND_DATABASE_URL=... \\
        .venv/bin/python scripts/backfill_notification_preferences.py
    BACKEND_DATABASE_URL=... \\
        .venv/bin/python scripts/backfill_notification_preferences.py --dry-run
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from collections.abc import Iterable, Mapping
from typing import Any

import psycopg
from psycopg.rows import dict_row


_LOGGER = logging.getLogger("backend.backfill_notification_preferences")


# v1 → v2 event_kind translation. Unknown v1 events drop silently
# (forward-compatible: a v1 row with a stray event we never shipped
# isn't a backfill failure).
_V1_TO_V2_EVENT: Mapping[str, str] = {
    "mention": "mention",
    "approval_needed": "approval_requested",
    "run_finished": "long_task_finished",
    "weekly_digest": "weekly_digest",
}

# v1 → v2 channel translation. ``slack`` has no v2 equivalent; the
# dispatcher targets in_app / email / push instead. The mapping is
# explicit to avoid dropping an entry by mistake during migration.
_V1_TO_V2_CHANNEL: Mapping[str, str | None] = {
    "email": "email",
    "desktop": "in_app",
    "slack": None,
}


def main() -> int:
    logging.basicConfig(
        format="%(asctime)s %(levelname)-7s %(message)s",
        level=logging.INFO,
        stream=sys.stderr,
    )
    args = _parse_args()
    database_url = os.environ.get("BACKEND_DATABASE_URL", "").strip()
    if not database_url:
        _LOGGER.error("BACKEND_DATABASE_URL is required")
        return 1

    with psycopg.connect(database_url, row_factory=dict_row) as conn:
        rows = _read_user_preferences(conn)
        total = 0
        skipped = 0
        upserted = 0
        for row in rows:
            total += 1
            user_id = row["user_id"]
            preferences = row.get("preferences") or {}
            translated = list(_translate_v1_blob(preferences))
            if not translated:
                skipped += 1
                continue
            if args.dry_run:
                _LOGGER.info(
                    "would upsert %d cells for user_id=%s", len(translated), user_id
                )
                upserted += len(translated)
                continue
            _upsert_cells(conn, user_id=user_id, cells=translated)
            upserted += len(translated)
        if not args.dry_run:
            conn.commit()
        _LOGGER.info(
            "backfill complete: rows_seen=%d users_skipped=%d cells_upserted=%d "
            "(dry_run=%s)",
            total,
            skipped,
            upserted,
            args.dry_run,
        )
    return 0


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Read the source rows and log what WOULD be written without committing.",
    )
    return parser.parse_args()


def _read_user_preferences(conn: Any) -> Iterable[dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT user_id, preferences FROM user_preferences "
            "WHERE preferences IS NOT NULL"
        )
        return list(cur.fetchall())


def _translate_v1_blob(blob: Any) -> Iterable[tuple[str, str, bool]]:
    """Yield ``(event_kind, channel, enabled)`` triples from a v1 row."""

    if not isinstance(blob, Mapping):
        return
    notifications = blob.get("notifications")
    if not isinstance(notifications, Mapping):
        return
    matrix = notifications.get("matrix")
    if not isinstance(matrix, Mapping):
        return
    for raw_event, channels in matrix.items():
        v2_event = _V1_TO_V2_EVENT.get(str(raw_event))
        if v2_event is None or not isinstance(channels, Mapping):
            continue
        for raw_channel, enabled in channels.items():
            v2_channel = _V1_TO_V2_CHANNEL.get(str(raw_channel))
            if v2_channel is None or not isinstance(enabled, bool):
                continue
            yield v2_event, v2_channel, enabled


def _upsert_cells(
    conn: Any,
    *,
    user_id: str,
    cells: Iterable[tuple[str, str, bool]],
) -> None:
    """Idempotent upsert into ``notification_preferences``.

    ``ON CONFLICT (user_id, event_kind, channel) DO UPDATE`` matches
    the table's primary key so re-running the script is a no-op for
    cells that already match the source.
    """

    with conn.cursor() as cur:
        for event_kind, channel, enabled in cells:
            cur.execute(
                """
                INSERT INTO notification_preferences
                    (user_id, event_kind, channel, enabled)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (user_id, event_kind, channel)
                DO UPDATE SET enabled = EXCLUDED.enabled, updated_at = NOW()
                """,
                (user_id, event_kind, channel, enabled),
            )


# Standalone-friendly: the script also exposes ``translate_v1_blob`` so a
# unit test can exercise the mapping without spinning up Postgres.
translate_v1_blob = _translate_v1_blob


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
