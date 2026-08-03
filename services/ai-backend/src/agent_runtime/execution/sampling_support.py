"""Which models still accept the sampling parameters.

Anthropic removed `temperature` / `top_p` / `top_k` from the Claude 4.7+
generation: a non-default value is rejected with HTTP 400
``invalid_request_error: `temperature` is deprecated for this model``. Our
runtime default is ``0.0`` — a non-default value — so sending it to one of
those models fails **every run on that model**, which is exactly what a live
desktop journey caught on ``claude-sonnet-5``.

This is per-MODEL, not per-provider: Claude 4.6 and earlier still accept the
parameters, so a blanket "never send temperature to Anthropic" would silently
drop a knob that still works on those models.

Matching is on a normalized model name rather than an exact id because the same
model reaches us under several spellings: bare (``claude-sonnet-5``), gateway-
prefixed (``anthropic/claude-sonnet-5`` via OpenRouter), and cloud-prefixed
(``anthropic.claude-sonnet-5``). Separators are folded so one entry covers all
three.

Adding a model here is the whole fix for a future generation that drops the
parameters; nothing else in the build path branches on model identity.
"""

from __future__ import annotations

import re


class SamplingParameterSupport:
    """Decide whether a model still accepts `temperature` and friends."""

    #: Models that reject the sampling parameters outright. Anthropic's
    #: migration guide is the authority; every entry below is a model whose
    #: documented behaviour is a 400 on a non-default `temperature`.
    SAMPLING_FREE_MODELS: frozenset[str] = frozenset(
        {
            "claudefable5",
            "claudemythos5",
            "claudeopus5",
            "claudeopus48",
            "claudeopus47",
            "claudesonnet5",
        }
    )

    #: Everything that is not [a-z0-9] — separators differ per gateway
    #: (``claude-sonnet-5`` / ``anthropic/claude-sonnet-5`` /
    #: ``anthropic.claude-sonnet-5``) and carry no meaning for this decision.
    _NOISE = re.compile(r"[^a-z0-9]")

    @classmethod
    def normalize(cls, model_name: str) -> str:
        """Fold a model name to its separator-free, provider-free comparison key."""

        folded = cls._NOISE.sub("", model_name.strip().lower())
        # Gateways prefix the vendor onto the id; the model identity is the
        # suffix, so a trailing match is what we want.
        return folded

    @classmethod
    def accepts_temperature(cls, model_name: str) -> bool:
        """Return ``True`` when ``temperature`` may be sent to this model.

        Unknown models are treated as accepting it — the parameter has been
        valid across every provider we support for years, so defaulting to
        "send it" keeps a newly-added model behaving as it does everywhere
        else. A model that later drops the parameter is a one-line addition
        to :attr:`SAMPLING_FREE_MODELS`.
        """

        normalized = cls.normalize(model_name)
        if not normalized:
            return True
        return not any(
            normalized.endswith(sampling_free)
            for sampling_free in cls.SAMPLING_FREE_MODELS
        )


__all__ = ("SamplingParameterSupport",)
