"""Backend out-of-band jobs.

Each job is a self-contained module that exposes:

* a :class:`...Loop` async class with ``start`` / ``stop`` / ``tick_once``,
* an env-var helper class for tunables, and
* a small set of dataclasses for the claim shape.

Loops never reach across services — they talk to ai-backend over HTTP
(``/internal/v1/...``) when they need LLM inference, never via Python
import. The guard ``tools/check_llm_provider_imports.py`` enforces this
for the whole ``services/backend`` tree.
"""

from __future__ import annotations

__all__: list[str] = []
