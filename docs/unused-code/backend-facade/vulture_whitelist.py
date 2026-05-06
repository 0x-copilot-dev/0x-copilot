"""Anchor module for optional Vulture passes over ``services/backend-facade``.

Vulture does **not** treat assignments here as “used” for **function parameters**
with the same name elsewhere (e.g. ``parent_context`` on ``SpanProcessor.on_start``).
For FastAPI routes, prefer ``--ignore-decorators`` as documented in
[07-vulture-fastapi-inventory.md](./07-vulture-fastapi-inventory.md).

This file exists so future audits can grow a small list of **module-level**
false positives (same pattern as ``docs/unused-code/ai-backend/vulture_whitelist.py``).
"""

from __future__ import annotations

_VULTURE_ANCHOR = True
