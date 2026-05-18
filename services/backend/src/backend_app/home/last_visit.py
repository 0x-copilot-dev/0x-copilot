"""Visit-cutoff store for the WhatsNewSection ``since_iso`` field.

Phase 9 home-prd §5.2 calls for a ``users.home_last_visit_at`` column.
Until the migration lands, we persist the cutoff inside the existing
``user_preferences.preferences`` JSONB blob under
``home.last_visit_iso`` — same blob the Phase 2 activity-window pref
lives in. Substitution: the column-backed implementation reuses the
same call signatures; only ``read_and_advance`` swaps the storage call.

Semantics:

* First visit (no prior cutoff stored) → return ``now - 24h`` so the
  WhatsNewDigest renders the last 24h for a brand-new account
  (home-prd §5.2 first-time fallback).
* Subsequent visits → return the *previous* stored value, then UPSERT
  the new cutoff atomically so the next visit sees this one's clock.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from backend_app.identity.me_store import MeStore, UserPreferencesRecord


# ---------------------------------------------------------------------------
# Constants — JSONB layout key under ``user_preferences.preferences``.
# ---------------------------------------------------------------------------


_HOME_KEY = "home"
_VISIT_KEY = "last_visit_iso"
_FIRST_VISIT_LOOKBACK_HOURS = 24


def read_and_advance_last_visit(
    *,
    me_store: MeStore,
    org_id: str,
    user_id: str,
    now: datetime,
) -> str:
    """Return the previous visit cutoff (ISO-8601 UTC), then advance.

    Atomic UPSERT — the route layer calls this once per ``GET /v1/home``
    and the returned string is the ``since_iso`` for the WhatsNew
    digest. The new cutoff (``now``) is persisted before this function
    returns so a second call observes it.

    First-time visit: returns ``now - 24h``. Never raises — a store
    failure logs upstream and returns the safe 24h fallback so the
    morning briefing still renders.
    """

    now_utc = _as_utc(now)
    fallback = (now_utc - timedelta(hours=_FIRST_VISIT_LOOKBACK_HOURS)).isoformat()
    try:
        record = me_store.get_preferences(org_id=org_id, user_id=user_id)
    except Exception:  # noqa: BLE001 — advisory; never blanks the page
        return fallback

    previous_iso = _read_previous(record) or fallback
    try:
        me_store.upsert_preferences(
            _merge_visit_cutoff(
                record=record,
                org_id=org_id,
                user_id=user_id,
                now_iso=now_utc.isoformat(),
            )
        )
    except Exception:  # noqa: BLE001 — best-effort cutoff write
        # Returning the previous cutoff is still correct; the next visit
        # will retry the UPSERT and converge.
        pass
    return previous_iso


def _read_previous(record: UserPreferencesRecord | None) -> str | None:
    if record is None:
        return None
    blob: Any = record.preferences
    if not isinstance(blob, dict):
        return None
    home_kv = blob.get(_HOME_KEY)
    if not isinstance(home_kv, dict):
        return None
    raw = home_kv.get(_VISIT_KEY)
    return raw if isinstance(raw, str) and raw.strip() else None


def _merge_visit_cutoff(
    *,
    record: UserPreferencesRecord | None,
    org_id: str,
    user_id: str,
    now_iso: str,
) -> UserPreferencesRecord:
    """Return a new UserPreferencesRecord with the visit cutoff merged.

    Deep-merges into ``preferences.home.last_visit_iso`` so the upsert
    never clobbers other ``home.*`` keys (activity_window_hours, etc.)
    or other top-level pref groups.
    """

    base: dict[str, Any] = {}
    if record is not None and isinstance(record.preferences, dict):
        base = dict(record.preferences)
    home_kv = dict(base.get(_HOME_KEY) or {})
    home_kv[_VISIT_KEY] = now_iso
    base[_HOME_KEY] = home_kv
    return UserPreferencesRecord(
        org_id=org_id,
        user_id=user_id,
        preferences=base,
    )


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


__all__ = ["read_and_advance_last_visit"]
