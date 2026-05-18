"""Connectors webhook manager (Phase 11 P11-A3).

Owns the OUTBOUND webhook surface — the management UI for receivers
the user registers (Routine `triggers[].webhook` → this destination's
rows). The Phase 5 ``routines/webhook.py`` module owns the INBOUND
ingest path; the two are kept separate because their security models
differ (see ``webhooks/signer.py`` module docstring).

Public surface:

* ``GET /v1/connectors/webhooks`` — list (owner OR admin scope).
* ``GET /v1/connectors/webhooks/{id}`` — detail.
* ``POST /v1/connectors/webhooks`` — create (returns copy-once secret).
* ``PATCH /v1/connectors/webhooks/{id}`` — edit (url / ip_allowlist /
  status).
* ``POST /v1/connectors/webhooks/{id}/rotate`` — rotate secret (returns
  copy-once secret + the 14-day grace secret).
* ``DELETE /v1/connectors/webhooks/{id}`` — soft-delete.
* ``POST /v1/connectors/webhooks/{id}/test-fire`` — owner/admin only;
  posts the canonical sample payload with the canonical signed
  headers and returns the upstream status code.

HMAC constants (algo / header names / skew window) are the single
source of truth in :mod:`backend_app.webhooks.signer`. Every signer
and verifier in the codebase reads from there — do not re-declare in
this package.

Secret rotation:

* ``rotating`` strategy (default) — Atlas mints a fresh secret on
  create; the rotation worker advances ``rotates_at`` by 90 days on
  every successful rotation and preserves the previous secret for 14
  days so receivers can roll their copy without a hard cutover.
* ``static`` strategy — user-supplied secret; Atlas never rotates it.
  rotate endpoint rejects with 400.

Sub-PRDs:

* connectors-prd §4.10 (endpoints) + §5.2 (schema) + §9 (HMAC + grace).
* cross-audit §9.7 Q6 — HMAC-of-payload signature UX lands here.
"""

from __future__ import annotations

from backend_app.webhooks.rotation_worker import WebhookRotationWorker
from backend_app.webhooks.routes import register_webhook_routes
from backend_app.webhooks.service import (
    ROTATION_GRACE,
    ROTATION_INTERVAL,
    WebhookCreated,
    WebhookForbidden,
    WebhookInvalidRequest,
    WebhookNotFound,
    WebhookRotated,
    WebhooksService,
)
from backend_app.webhooks.signer import (
    HMAC_ALGO,
    SIGNATURE_HEADER,
    TIMESTAMP_HEADER,
    TIMESTAMP_MAX_SKEW_S,
    sign,
    verify,
)
from backend_app.webhooks.store import (
    InMemoryWebhooksStore,
    WebhookAuditRecord,
    WebhookRecord,
    WebhooksStore,
)

__all__ = [
    "HMAC_ALGO",
    "InMemoryWebhooksStore",
    "ROTATION_GRACE",
    "ROTATION_INTERVAL",
    "SIGNATURE_HEADER",
    "TIMESTAMP_HEADER",
    "TIMESTAMP_MAX_SKEW_S",
    "WebhookAuditRecord",
    "WebhookCreated",
    "WebhookForbidden",
    "WebhookInvalidRequest",
    "WebhookNotFound",
    "WebhookRecord",
    "WebhookRotated",
    "WebhookRotationWorker",
    "WebhooksService",
    "WebhooksStore",
    "register_webhook_routes",
    "sign",
    "verify",
]
