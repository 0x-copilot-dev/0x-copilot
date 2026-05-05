"""Per-provider model-kwargs adapters for workspace-policy knobs (PR 4.3).

The agent runtime reads workspace policy from
``AgentRuntimeContext.workspace_behavior_overrides`` (a JSON-shape blob
populated at run-create from ``workspace_defaults.behavior_overrides``).

The single load-bearing knob today is ``training_data_opt_out`` —
when ``True``, every outbound model call must carry the provider's
"do not train on this conversation" signal. The mapping is small and
provider-specific; we keep it in one file so renames and policy
changes never sprawl.

Public surface:

* :class:`TrainingOptOutHeaders` — frozen dataclass with the kwargs
  we want to merge into ``init_chat_model(...)`` per provider.
* :func:`workspace_model_kwargs(model_config, overrides)` — returns
  the dict the chat-model factory should merge.
"""

from __future__ import annotations

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


__all__ = ("workspace_model_kwargs",)
