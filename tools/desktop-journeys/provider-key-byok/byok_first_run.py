#!/usr/bin/env python3
"""J-BYOK — first-run "Bring your own key" → model catalog → real run.

Drives the REAL packaged 0xCopilot desktop app through a keyless first-run:
sign in locally → FTUE "Add a key" → paste a live provider key → Connect →
assert the State-B composer appears, the model catalog marks the provider's
models ``configured=true``, the composer's model pill reflects that provider,
then send a message and assert the run streams a real assistant reply.

Usage (provider defaults to "anthropic"):

    python3 tools/desktop-journeys/provider-key-byok/byok_first_run.py
    python3 tools/desktop-journeys/provider-key-byok/byok_first_run.py openai

The key is read ONLY from services/ai-backend/.env via load_env_key and is
never printed, logged, or committed — only lengths / status codes ever surface.
Exits non-zero on any failed assertion.
"""

from __future__ import annotations

import sys
import time

import os as _os
import sys as _sys

_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
from _lib import DriverSession, load_env_key  # noqa: E402

# Facade normalizes some provider slugs on the catalog (e.g. google → gemini).
# argv provider → (catalog provider slug, substring the model pill should contain).
PROVIDER_SPEC = {
    "openai": ("openai", "gpt"),
    "anthropic": ("anthropic", "claude"),
    "openrouter": ("openrouter", ""),
}

DEFAULT_MODEL_ID = (
    "gpt-5.4-mini"  # the deployment default the catalog always leads with
)


def _assistant_message_count(s: DriverSession) -> int:
    return int(
        s.evaluate(
            'document.querySelectorAll("[data-testid^=tc-chat-message-]").length'
        )
        or 0
    )


def _has_error(s: DriverSession) -> bool:
    return bool(s.evaluate('!!document.querySelector("[data-testid*=error]")'))


def main() -> int:
    provider = sys.argv[1] if len(sys.argv) > 1 else "anthropic"
    if provider not in PROVIDER_SPEC:
        raise SystemExit(
            f"unsupported provider {provider!r}; pick one of {list(PROVIDER_SPEC)}"
        )
    catalog_provider, pill_substr = PROVIDER_SPEC[provider]
    key = load_env_key(provider)  # value never printed
    print(f"[byok] provider={provider} key_len={len(key)} (value withheld)")

    with DriverSession(name=f"byok-{provider}") as s:
        # 1. Sign in locally (no signup) ---------------------------------------
        s.sign_in_local()
        s.shot("sign-in-gate")
        print("PASS: signed in locally (Use locally, no account)")

        # 2. FTUE → Add a key → paste → Connect → State-B composer -------------
        s.ftue_add_key(provider, key)  # asserts first-run-composer appears
        assert s.present("[data-testid=first-run-composer]"), (
            "State-B composer not present"
        )
        s.shot("byok-composer")
        print("PASS: key connected → State-B composer (first-run-composer) present")

        # 3. Catalog truth THROUGH the app -------------------------------------
        cat = s.transport("GET", "/v1/agent/models")
        assert cat.get("default_model_id") == DEFAULT_MODEL_ID, (
            f"default_model_id={cat.get('default_model_id')!r} != {DEFAULT_MODEL_ID!r}"
        )
        print(f"PASS: catalog default_model_id == {DEFAULT_MODEL_ID}")

        models = cat.get("models", [])
        provider_models = [m for m in models if m.get("provider") == catalog_provider]
        assert provider_models, f"no {catalog_provider} models in catalog"
        configured = [m for m in provider_models if m.get("configured")]
        assert configured, (
            f"expected {catalog_provider} models configured=true after adding a key; "
            f"none of {len(provider_models)} are configured"
        )
        print(
            f"PASS: {len(configured)}/{len(provider_models)} {catalog_provider} "
            f"models configured=true after BYOK add"
        )

        # openrouter is ALWAYS_SELECTABLE (configured even with no key); prove it.
        openrouter_models = [m for m in models if m.get("provider") == "openrouter"]
        if openrouter_models:
            assert all(m.get("configured") for m in openrouter_models), (
                "openrouter models must be configured=true (ALWAYS_SELECTABLE)"
            )
            print(
                "PASS: openrouter models configured=true (ALWAYS_SELECTABLE), independent of BYOK"
            )

        # 4. Composer model pill reflects the added provider -------------------
        pill = (s.model_pill() or "").strip()
        print(f"[byok] model pill = {pill!r}")
        assert pill, "model pill has no text"
        if pill_substr:
            assert pill_substr.lower() in pill.lower(), (
                f"pill {pill!r} does not reflect provider {provider!r} "
                f"(expected substring {pill_substr!r})"
            )
            if provider == "anthropic":
                # The FTUE preselect walks provider priority among CONFIGURED models and
                # must NOT fall back to the keyless deployment default gpt-5.4-mini.
                assert "gpt-5.4" not in pill.lower(), (
                    f"anthropic-only key must preselect a Claude model, got {pill!r}"
                )
            print(f"PASS: composer model pill reflects {provider} ({pill!r})")

        # 5. Send a message → assert a real streamed assistant reply -----------
        before = _assistant_message_count(s)
        s.send_first_run_message("hi")
        s.shot("byok-sent")

        streamed = False
        deadline = time.time() + 120
        while time.time() < deadline:
            assert not _has_error(s), (
                "an error surface ([data-testid*=error]) appeared during the run"
            )
            if (
                _assistant_message_count(s) >= 2
                and _assistant_message_count(s) > before
            ):
                streamed = True
                break
            time.sleep(1)
        s.shot("byok-reply")
        assert streamed, (
            f"run did not stream an assistant reply within 120s "
            f"(messages={_assistant_message_count(s)}, before={before})"
        )
        assert not _has_error(s), "error surface present after the run"
        print(
            f"PASS: run streamed a real assistant reply ({_assistant_message_count(s)} messages, no error)"
        )

    print(f"\nALL PASS — J-BYOK-{provider.upper()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
