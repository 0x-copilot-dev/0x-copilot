#!/usr/bin/env python3
"""Journey C — fresh app + ONLY an Anthropic key ⇒ preselect Claude, not GPT-5.4.

Bug (before PR #260): defaultSelectedModelId's fallback returned a naive
models[0]. The catalog leads with the deployment default gpt-5.4-mini, so an
Anthropic-only user was preselected onto an UNUSABLE (keyless) OpenAI model.
Fix (#260, 548d064f): the fallback walks an explicit provider priority among
USABLE (configured & not disabled) models only, returning '' when none qualify:

    OpenAI > Anthropic > OpenRouter > Gemini(google)

among CONFIGURED providers (then the first usable model of any other provider —
covers local/Ollama). The keyless default is never auto-picked.
See apps/desktop/renderer/composer/desktopModelCatalog.ts (PROVIDER_PRIORITY).

    python3 tools/desktop-journeys/chat-nav-model/model_preselect.py

The Anthropic key is read ONLY from services/ai-backend/.env via load_env_key
and is never printed or logged. Exits non-zero on any failed assertion.

Fixed in PR #260.
"""

from __future__ import annotations

import time

import os as _os
import sys as _sys

_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
from _lib import DriverSession, load_env_key  # noqa: E402


def _message_count(s: DriverSession) -> int:
    return int(
        s.evaluate(
            'document.querySelectorAll("[data-testid^=tc-chat-message-]").length'
        )
        or 0
    )


def main() -> int:
    key = load_env_key("anthropic")  # value never printed
    print(f"[model-preselect] anthropic key_len={len(key)} (value withheld)")

    with DriverSession(name="chat-nav-model-preselect") as s:
        # 1. Anthropic-only FTUE, then send to bind + reach the cockpit ----------
        s.sign_in_local()
        s.ftue_add_key("anthropic", key)  # only Anthropic is keyed
        s.send_first_run_message("hi")
        s.shot("preselect-sent")

        deadline = time.time() + 120
        while time.time() < deadline:
            if s.on_run() and _message_count(s) >= 2:
                break
            time.sleep(1)
        assert s.on_run(), "expected to reach a bound run cockpit"
        s.shot("preselect-cockpit")

        # 2. The composer model pill must be a Claude/Anthropic model -----------
        pill = (s.model_pill() or "").strip()
        print(f"[model-preselect] composer model pill = {pill!r}")
        assert pill, "composer model pill has no text"
        assert "claude" in pill.lower(), (
            f"Anthropic-only key must preselect a Claude model, got {pill!r}"
        )
        assert "gpt-5.4" not in pill.lower(), (
            f"the keyless GPT-5.4 Mini default must never be preselected, got {pill!r} (BUG C)"
        )
        print(
            f"PASS: composer preselected a Claude model ({pill!r}), not keyless GPT-5.4"
        )

        # 3. Catalog truth THROUGH the app: openai keyless, anthropic configured -
        cat = s.transport("GET", "/v1/agent/models")
        models = cat.get("models", [])
        assert models, "empty model catalog"

        openai_models = [m for m in models if m.get("provider") == "openai"]
        anthropic_models = [m for m in models if m.get("provider") == "anthropic"]
        assert openai_models, (
            "no openai models in the catalog to prove they are keyless"
        )
        assert anthropic_models, "no anthropic models in the catalog"

        assert all(m.get("configured") is False for m in openai_models), (
            "with no OpenAI key, every openai model must be configured=false; "
            f"got configured flags {[m.get('configured') for m in openai_models]}"
        )
        anthropic_configured = [m for m in anthropic_models if m.get("configured")]
        assert anthropic_configured, (
            f"expected ≥1 anthropic model configured=true after adding the key; "
            f"none of {len(anthropic_models)} are configured"
        )
        print(
            f"PASS: catalog — openai configured=false ({len(openai_models)} models), "
            f"anthropic configured=true ({len(anthropic_configured)}/{len(anthropic_models)})"
        )

        # Document the priority the fix applies among CONFIGURED providers.
        print(
            "NOTE: preselect priority among CONFIGURED providers = "
            "OpenAI > Anthropic > OpenRouter > Gemini "
            "(desktopModelCatalog.ts PROVIDER_PRIORITY); "
            "keyless gpt-5.4-mini is never auto-selected."
        )

    print("\nALL PASS — Journey C (Anthropic-only preselects Claude) — PR #260")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
