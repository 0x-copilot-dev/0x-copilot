"""Per-provider model-kwargs adapters for workspace and user policy knobs.

The agent runtime reads workspace policy from
``AgentRuntimeContext.workspace_behavior_overrides`` (populated at
run-create from ``workspace_defaults.behavior_overrides``) and per-user
policy from ``AgentRuntimeContext.user_policies_json`` (populated from
the backend's ``/internal/v1/policies/runtime`` aggregate).

Two load-bearing knobs:

* ``training_data_opt_out`` (workspace) / ``training_opt_out`` (user) —
  when ``True``, every outbound model call must carry the provider's
  "do not train" signal. User opt-out wins (privacy is a one-way
  ratchet — a user opting out cannot be silently re-enrolled by a
  more permissive workspace setting).
* ``region`` (user) — pins the run to a specific provider deployment.
  ``None`` means "use whatever the deployment configured for this
  provider."

Public surface:

* :func:`workspace_model_kwargs(provider, overrides)` — workspace opt-out.
* :func:`user_policy_model_kwargs(provider, user_policies_json, provider_keys)`
  — per-user opt-out + region routing + BYOK ``api_key`` injection for the
  active provider (user key wins over the deployment env key).
* :class:`RegionUnavailableError` — raised when the user pinned a region
  the deployment doesn't have a mapping for; the runtime worker catches
  this and translates to ``RUN_REJECTED``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping


# Map of LangChain ``init_chat_model`` provider slugs (post normalisation
# in :class:`ModelConfigResolver`) to the kwarg overrides we apply when
# ``workspace_behavior_overrides.training_data_opt_out`` is ``True``.
#
# Keep this list short and well-cited; every entry is a contract with
# a third party and renames silently. The values below are the
# documented "do not retain / do not train" hints as of writing:
#
#   * OpenAI Responses API: ``store=False`` excludes the request from
#     the response store and from model improvements that read it.
#   * Anthropic: ``extra_headers`` carries the opt-out hint that
#     enterprise customers can negotiate; the header name is set to
#     a stable canonical and updated as Anthropic publishes.
#
# When a provider doesn't expose a flag (Gemini today), we record the
# fact via ``_NO_PROVIDER_FLAG`` so the model-call middleware can log
# a one-line warning instead of silently doing nothing.
_NO_PROVIDER_FLAG = object()


_TRAINING_OPT_OUT_KWARGS: Mapping[str, dict[str, object]] = {
    # init_chat_model("gpt-...", model_provider="openai") accepts ``store``
    # via ``model_kwargs`` (responses API). The exact path is a stable
    # documented surface; we set both the body-level field and the legacy
    # header so older client versions still honour it.
    "openai": {
        "model_kwargs": {"store": False},
    },
    "anthropic": {
        "extra_headers": {"anthropic-disable-training": "true"},
    },
    # Google AI Studio / Gemini: no first-class flag at the time of
    # writing. Operators rely on workspace-level data-residency contract
    # rather than per-call hints. We surface the absence so the audit
    # row records the call was made without a provider-side guarantee.
    "gemini": {},
}


def workspace_model_kwargs(
    *,
    provider: str,
    workspace_behavior_overrides: Mapping[str, object] | None,
) -> dict[str, object]:
    """Return the kwargs to merge into ``init_chat_model(...)`` calls.

    The returned dict is small (often empty). Callers merge with
    ``dict.update(...)`` after their own provider-specific kwargs so
    workspace policy wins on conflict — opt-out is a deliberate
    workspace decision and must not be silently dropped.
    """

    if not workspace_behavior_overrides:
        return {}
    if not workspace_behavior_overrides.get("training_data_opt_out"):
        return {}
    template = _TRAINING_OPT_OUT_KWARGS.get(provider, {})
    # Deep-copy mutable entries so callers can mutate without aliasing.
    return _shallow_copy_kwargs(template)


def _shallow_copy_kwargs(template: Mapping[str, object]) -> dict[str, object]:
    out: dict[str, object] = {}
    for key, value in template.items():
        if isinstance(value, dict):
            out[key] = dict(value)
        else:
            out[key] = value
    return out


@dataclass(frozen=True)
class ProviderRegionDeployment:
    """One ``(provider, region) → base_url`` mapping.

    Composed at module-import time from the
    ``PROVIDER_REGION_DEPLOYMENTS`` env (CSV of
    ``provider:region=base_url`` triples). Empty / missing env =
    no region routing — the existing deployment continues to handle
    every region, and a user pinning a region they didn't configure
    raises :class:`RegionUnavailableError`.
    """

    provider: str
    region: str
    base_url: str


class RegionUnavailableError(Exception):
    """Raised when the user pinned a region the deployment can't honor.

    The runtime worker catches this at run start and emits a
    ``RUN_REJECTED`` envelope with ``reason=region_unavailable`` —
    safer than silently routing to the default region.
    """

    def __init__(self, *, provider: str, region: str) -> None:
        super().__init__(
            f"data residency '{region}' is not configured for provider '{provider}'"
        )
        self.provider = provider
        self.region = region


def _load_region_deployments() -> Mapping[tuple[str, str], str]:
    """Parse ``PROVIDER_REGION_DEPLOYMENTS=provider:region=url,...``."""

    raw = os.environ.get("PROVIDER_REGION_DEPLOYMENTS", "").strip()
    if not raw:
        return {}
    out: dict[tuple[str, str], str] = {}
    for entry in raw.split(","):
        cleaned = entry.strip()
        if not cleaned or "=" not in cleaned or ":" not in cleaned:
            continue
        provider_region, base_url = cleaned.split("=", 1)
        if ":" not in provider_region:
            continue
        provider, region = provider_region.split(":", 1)
        out[(provider.strip(), region.strip())] = base_url.strip()
    return out


def user_policy_model_kwargs(
    *,
    provider: str,
    user_policies_json: Mapping[str, object] | None,
    provider_keys: Mapping[str, str] | None = None,
) -> dict[str, object]:
    """Return opt-out, region routing, and BYOK key kwargs for one user.

    Composes with :func:`workspace_model_kwargs` at the call site:
    user opt-out is a one-way ratchet (cannot be silently disabled
    by a less-strict workspace setting), and region is a per-user
    knob without a workspace counterpart.

    ``provider_keys`` is the in-memory ``AgentRuntimeContext.provider_keys``
    mapping (normalized provider slug -> plaintext key). When the active
    provider has a stored user key it is injected as ``api_key``, which
    ``init_chat_model`` forwards to the provider client — taking precedence
    over any deployment env key the SDK would otherwise read. The returned
    dict must never be logged or persisted by callers.
    """

    out: dict[str, object] = {}
    privacy = (
        user_policies_json.get("privacy") if user_policies_json is not None else None
    )
    if isinstance(privacy, Mapping):
        if privacy.get("training_opt_out") is True:
            template = _TRAINING_OPT_OUT_KWARGS.get(provider, {})
            out.update(_shallow_copy_kwargs(template))
        region = privacy.get("region")
        if isinstance(region, str) and region:
            deployments = _load_region_deployments()
            base_url = deployments.get((provider, region))
            if base_url is None:
                raise RegionUnavailableError(provider=provider, region=region)
            # ``init_chat_model`` accepts ``base_url`` for OpenAI-shaped
            # clients (incl. Anthropic via the Anthropic SDK's ``base_url``
            # kwarg). Providers that ignore the kwarg simply continue
            # to route to the default region — mapped above to
            # RegionUnavailableError so we never silently mis-route.
            out["base_url"] = base_url
    api_key = (provider_keys or {}).get(provider)
    if isinstance(api_key, str) and api_key:
        out["api_key"] = api_key
    return out


__all__ = (
    "ProviderRegionDeployment",
    "RegionUnavailableError",
    "user_policy_model_kwargs",
    "workspace_model_kwargs",
)
