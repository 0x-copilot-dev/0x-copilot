"""Pin LiteLLM's offline cost map before any test module imports ``litellm``.

``apply_offline_litellm_config`` sets ``LITELLM_LOCAL_MODEL_COST_MAP`` so the
catalog and the token counter read LiteLLM's **bundled** cost table rather than
fetching the remote one. Product code already calls it — but lazily, at first
use, which is too late: pytest imports every test module during collection, and
``tests/unit/agent_runtime/budgets/test_token_counter.py`` imports ``litellm``
at module scope. Whichever happens first decides which table the process holds
for the rest of the run.

Lose that race and ``litellm.model_cost`` is the 3,212-entry remote map instead
of the 2,953-entry bundled one, ``claude-opus-4-8`` stops satisfying
``LitellmModelSource``'s eligibility filter, and
``TestModelCatalogRealLitellm::test_native_product_models_present_with_metadata``
fails — but only when the whole suite runs, never for the file alone. That is
why it survived: every way a person checks it by hand passes.

A ``conftest.py`` at the tests root is imported before any test module in any
subdirectory, which is the only hook early enough. It must stay module-level:
as a fixture it would run after collection and lose the same race.
"""

from agent_runtime.pricing.litellm_runtime import apply_offline_litellm_config

apply_offline_litellm_config()
