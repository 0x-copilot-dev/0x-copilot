"""Shaping-model resolution — the desktop shaping-on default (PRD-B3, SDR §13 #1).

v1 gated surface-spec generation entirely on a non-empty ``SURFACE_SPEC_MODEL``
env var (``build_surface_generation_scheduler``): empty ⇒ no model, no
generation. That is the wrong posture for the desktop single-user app, where
shaping should be **on by default** once the user has configured a BYOK provider
key — but still honestly **off** when no key exists (nothing to shape with) and
byte-identical to today when ``SURFACES_V2`` is off (generation stays opt-in).

:class:`ShapingModelResolver` is the single seam every shaping caller consults
instead of the bare env read. It resolves, in order:

1. ``SURFACE_SPEC_MODEL`` set (non-empty) → that id verbatim (today's behaviour,
   an explicit operator override that wins over every default).
2. ``SURFACES_V2`` off → ``None`` (flag-off is byte-identical: generation stays
   opt-in on the env var alone).
3. ``SURFACES_V2`` on → the cheapest shaping model for the run's provider from
   :class:`_ShapingDefaults`; an unknown provider or ``run_provider is None``
   (no BYOK key configured) → ``None`` — shaping off, generic/raw only.

The model ids in :class:`_ShapingDefaults` are the cheapest **native** model of
each direct provider in the shipped catalog
(``agent_runtime.api.litellm_model_source._NativeModelCatalog.NATIVE``) — the
``-mini`` / ``haiku`` / ``flash`` tier — never invented ids. B4's higher-effort
budget hooks the same resolver.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import ClassVar

from agent_runtime.surfaces_v2.config import SurfacesV2Flag


class _EnvKeys:
    """Env vars this resolver reads."""

    SURFACE_SPEC_MODEL = "SURFACE_SPEC_MODEL"


class _ShapingDefaults:
    """Cheapest native shaping model per provider (catalog-verified, not invented).

    Keys are the ``RunRecord.model_provider`` values the runtime stamps. Each
    value is the cheapest native model of that provider in the shipped catalog
    (``litellm_model_source._NativeModelCatalog.NATIVE``): the small/fast tier a
    background shaping pass should use. Providers with no cheap native tier here
    (``openrouter`` / ``ollama`` / custom OpenAI-compatible) resolve to ``None``
    — an honest "no default shaping model", not a guess.
    """

    _BY_PROVIDER: ClassVar[Mapping[str, str]] = {
        "openai": "gpt-5.4-mini",
        "anthropic": "claude-haiku-4-5",
        # Gemini is exposed under both provider spellings across the stack.
        "gemini": "gemini-2.5-flash",
        "google": "gemini-2.5-flash",
    }

    @classmethod
    def cheapest_for(cls, provider: str) -> str | None:
        """Return the cheapest shaping model id for ``provider`` or ``None``."""

        return cls._BY_PROVIDER.get(provider.strip().lower())


class ShapingModelResolver:
    """Resolve the shaping model id for a run, or ``None`` when shaping is off."""

    @classmethod
    def resolve(
        cls,
        *,
        environ: Mapping[str, str],
        run_provider: str | None,
    ) -> str | None:
        """Resolve the shaping model id (see module docstring for the ladder).

        ``environ`` is injectable so tests assert every branch without touching
        process state. ``run_provider`` is the run's ``model_provider`` (the
        user's configured default provider proxy) — ``None`` when no provider /
        BYOK key is configured, which disables background shaping honestly.
        """

        explicit = environ.get(_EnvKeys.SURFACE_SPEC_MODEL, "").strip()
        if explicit:
            return explicit
        if not SurfacesV2Flag.enabled(environ):
            return None
        if run_provider is None:
            return None
        cleaned = run_provider.strip()
        if not cleaned:
            return None
        return _ShapingDefaults.cheapest_for(cleaned)


__all__ = ["ShapingModelResolver"]
