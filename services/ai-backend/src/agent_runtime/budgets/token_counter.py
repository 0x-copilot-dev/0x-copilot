"""Input-token counting for the pre-run budget preflight.

The preflight needs an input-token estimate for the run's FIRST model call. This
replaces the historical char/4 heuristic with ``litellm.token_counter``, which
uses the provider's real tokenizer where litellm bundles one (openai, anthropic,
gemini) and an offline tiktoken approximation elsewhere — always token-based,
always strictly better than char/4, and always offline once
:func:`~agent_runtime.pricing.litellm_runtime.apply_offline_litellm_config` has
run.

The :class:`TokenCounterPort` protocol keeps the worker decoupled from litellm and
lets unit tests inject a deterministic fake. Both concrete counters **never
raise**: a miss returns ``None`` so the caller falls through its own defence-in-
depth fallback chain (char-heuristic → context-window proxy → fail-open Allow).

Post-run charging is unaffected — it stays on the authoritative provider-reported
usage (``observability/token_usage.py`` + ``run_metrics.py``). This counter feeds
only the pre-run *estimate*.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from typing import Final, Protocol, runtime_checkable

_LOGGER = logging.getLogger("agent_runtime.budgets.token_counter")

# Message dicts are ``{role, content}`` — the same shape the worker feeds the
# runtime. Only ``content`` contributes to the char-heuristic length.
_CONTENT_KEY: Final[str] = "content"
_CHARS_PER_TOKEN: Final[int] = 4


@runtime_checkable
class TokenCounterPort(Protocol):
    """Count input tokens for a message list under a given model."""

    def count(self, *, model: str, messages: Sequence[Mapping[str, str]]) -> int | None:
        """Return the input-token count, or ``None`` when counting is unavailable."""
        ...


class LitellmTokenCounter:
    """Count tokens via ``litellm.token_counter`` under the offline guardrail.

    Applies :func:`apply_offline_litellm_config` before the (lazy) litellm import
    so the count is deterministic and network-free for every provider — the
    HuggingFace tokenizer download that llama / cohere slugs would otherwise
    trigger is disabled, and those models are counted with the bundled tiktoken
    encoders. Never raises: a malformed model / message list returns ``None``.
    """

    def count(self, *, model: str, messages: Sequence[Mapping[str, str]]) -> int | None:
        try:
            from agent_runtime.pricing.litellm_runtime import (  # noqa: PLC0415
                apply_offline_litellm_config,
            )

            apply_offline_litellm_config()
            import litellm  # noqa: PLC0415 — lazy: heavy import kept off the request path

            count = litellm.token_counter(model=model, messages=list(messages))
        except Exception:  # never fatal — the caller has a fallback chain
            _LOGGER.debug(
                "litellm_token_counter_failed",
                extra={"metadata": {"model": model}},
                exc_info=True,
            )
            return None
        if not isinstance(count, int) or count < 0:
            return None
        return count


class CharHeuristicTokenCounter:
    """Fallback counter: ``len // 4`` over the concatenated message text.

    The tokenizer-free approximation that preceded litellm. Retained as the
    second tier of the worker's fallback chain so a litellm miss still yields a
    token-shaped estimate rather than collapsing straight to the context-window
    proxy. Pure and total — it does not raise.
    """

    def count(self, *, model: str, messages: Sequence[Mapping[str, str]]) -> int | None:
        total_chars = sum(
            len(content)
            for message in messages
            for content in (message.get(_CONTENT_KEY),)
            if isinstance(content, str)
        )
        return total_chars // _CHARS_PER_TOKEN
