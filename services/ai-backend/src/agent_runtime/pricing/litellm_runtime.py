"""Canonical offline posture for every litellm entry point.

:func:`apply_offline_litellm_config` is the single keystone guardrail that keeps
pricing lookups, catalog metadata, and pre-run token counting fully offline and
deterministic — on networked CI and on the fully-local desktop alike. Route every
litellm access through it before first use (pricing source, catalog source, token
counter) so the three seams share one posture.

Two litellm network hazards are neutralised:

1. The first ``litellm.model_cost`` / ``litellm.token_counter`` access attempts a
   remote fetch of ``model_prices_and_context_window.json`` (fail-soft, ~1.3s, a
   ``WARNING`` line, non-deterministic on networked CI). ``litellm`` reads the
   ``LITELLM_LOCAL_MODEL_COST_MAP`` env var **at import time** to decide whether
   to fetch; setting it to ``"True"`` before the first ``import litellm`` pins the
   bundled table and skips the fetch entirely.
2. ``litellm.token_counter`` for llama / cohere / openrouter-llama slugs triggers a
   HuggingFace tokenizer download (``Tokenizer.from_pretrained("Xenova/…")``) that
   retries several times before falling back to tiktoken — a multi-second stall and
   a hard network dependency that hangs the local desktop. Setting
   ``litellm.disable_hf_tokenizer_download = True`` routes those models through the
   bundled tiktoken encoders instead (an offline approximation, strictly better
   than char/4 and instant).

Both operations are idempotent, so every entry point may call this cheaply. The
env var is applied via :func:`os.environ.setdefault` **before** the (lazy) import
so the ordering contract holds as long as no un-guarded ``import litellm`` runs
first — which is why all litellm access in this service is funnelled here.
"""

from __future__ import annotations

from typing import Final

import os

_LOCAL_MODEL_COST_MAP_ENV: Final[str] = "LITELLM_LOCAL_MODEL_COST_MAP"


def apply_offline_litellm_config() -> None:
    """Pin litellm to its bundled offline data. Idempotent; call before any litellm use."""

    # Must precede the first ``import litellm`` in the process: litellm reads this
    # env var at import time to decide whether to fetch the remote cost map.
    os.environ.setdefault(_LOCAL_MODEL_COST_MAP_ENV, "True")
    import litellm  # noqa: PLC0415 — lazy: litellm is heavy; keep it off the module graph

    # Process-global but idempotent: kills the HuggingFace tokenizer download so
    # llama / cohere / openrouter counting stays offline and instant.
    litellm.disable_hf_tokenizer_download = True
