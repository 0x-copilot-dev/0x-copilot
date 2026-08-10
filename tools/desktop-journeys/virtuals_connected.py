#!/usr/bin/env python3
"""virtuals-connected — the experience AFTER a real Virtuals key is added.

Sibling of ``virtuals_provider.py``, which deliberately needs no key. This one
needs a REAL one, because everything it asserts is downstream of a working
credential: which model first-run lands on, what the composer's pill says, and
how a Virtuals-backed run renders in both cockpit modes.

    COPILOT_JOURNEY_DOTENV=/path/to/main-checkout/services/ai-backend/.env \\
      python3 tools/desktop-journeys/virtuals_connected.py

It SKIPS (exit 3) without ``VIRTUALS_ACP_KEY`` — a skip is not a pass.

The claim worth stating: after adding a Virtuals key, first-run must open on
**Claude Sonnet 5**. That is not a preference encoded here — it falls out of
the tier ladder (`ModelSizeTierResolver`) picking the `medium` rung ahead of
`small` and `big`, so a Virtuals user starts on the everyday frontier model
rather than the cheapest or the dearest. Asserting it here is what stops a
catalog change from silently moving everyone onto a $37.50/Mtok flagship.
"""

from __future__ import annotations

import time

from _lib import (
    DriverSession,
    JourneyPlan,
    PhaseSkipped,
    load_env_key,
)

PROVIDER = "virtuals"
LABEL = "Virtuals"

SETTINGS_BUTTON = '[aria-label="Settings"]'
NAV_PROVIDER_KEYS = '[data-slug="provider-keys"]'
MODEL_PILL = ".atlas-model-pill"

# The rung the ladder resolves for a Virtuals key. Derived, not chosen: medium
# beats small and big in `DEFAULT_TIER_ORDER`, and Claude Sonnet 5 is the medium
# rung of the Virtuals catalog.
EXPECTED_DEFAULT_MODEL = "Claude Sonnet 5"

JS_PILL_ROWS = """
(() => {
  const rows = Array.from(document.querySelectorAll('[role=menuitemradio]'));
  return rows.map(r => ({
    text: (r.innerText || '').replace(/\\s+/g, ' ').trim().slice(0, 80),
    checked: r.getAttribute('aria-checked') === 'true',
    disabled: r.getAttribute('aria-disabled') === 'true',
  }));
})()
"""

JS_CONNECTED_ROW = """
(() => {
  const n = document.querySelector('[data-testid=provider-row-virtuals]');
  if (!n) return null;
  return (n.innerText || '').replace(/\\s+/g, ' ').trim().slice(0, 160);
})()
"""


def _key() -> str:
    try:
        return load_env_key(PROVIDER)
    except SystemExit as exc:  # no .env / no VIRTUALS_ACP_KEY
        raise PhaseSkipped(str(exc)) from exc


# ── setup ────────────────────────────────────────────────────────────────────
def sign_in_and_connect(s: DriverSession) -> None:
    """Sign in, then add the REAL Virtuals key through the first-run card."""

    s.sign_in_local()
    assert s.wait_for("[data-testid=first-run-add-key]", 60), (
        "FTUE key card never appeared"
    )

    # The catalog-snapshot race this journey found now lives in `_lib`, and
    # `ftue_add_key` waits it out for every Virtuals journey — see
    # `wait_for_virtuals_catalog` for why the wait must precede the save.
    #
    # Infers the provider from the key and asserts it landed on `virtuals`.
    s.ftue_add_key(PROVIDER, _key())  # value never printed


# ── phases ───────────────────────────────────────────────────────────────────
def vc1_first_run_opens_on_the_everyday_rung(s: DriverSession) -> None:
    """The composer lands on Claude Sonnet 5 — the medium rung, via Virtuals."""

    assert s.wait_for("[data-testid=first-run-composer]", 60), (
        "the composer never revealed after connecting the key"
    )

    # How long until the CATALOG actually carries Virtuals rows.
    # VirtualsModelSource never fetches on the request path, so on a fresh
    # profile the first read returns nothing and schedules a background
    # refresh. Measure the gap rather than assume it: if it outlives first-run,
    # a user who has just added a Virtuals key sees no Virtuals models.
    waited = 0.0
    rows: list = []
    while waited < 90:
        models = s.transport("GET", "/v1/agent/models").get("models", [])
        rows = [m for m in models if m.get("provider") == "virtuals"]
        if rows:
            break
        time.sleep(2)
        waited += 2
    print(f"  catalog carried Virtuals rows after ~{waited:.0f}s ({len(rows)} rows)")
    assert rows, (
        "the catalog never carried a Virtuals row within 90s of connecting the key"
    )
    configured = [m for m in rows if m.get("configured")]
    print(f"  {len(configured)}/{len(rows)} report configured=true")
    enabled = [m for m in rows if m.get("enabled") is not False]
    print(f"  {len(enabled)}/{len(rows)} are enabled (what the picker offers)")
    for m in enabled[:6]:
        print(f"     {m['id']:<34} cfg={m.get('configured')} tier={m.get('tier')}")

    # Was the key actually stored? Read it back through the app's own API.
    stored = s.transport("GET", "/v1/settings/provider-keys").get("keys", [])
    print(f"  stored keys: {[k.get('provider') for k in stored]}")

    # Evidence first, assertion second — a failing assert must not cost the
    # screenshot that explains it.
    s.wait_model_pill_resolved()
    s.shot("ftue-connected-composer")
    pill = s.model_pill()
    print(f"  model pill = {pill and pill.strip()!r}")

    assert any(k.get("provider") == PROVIDER for k in stored), (
        f"the Virtuals key was not stored; keys={[k.get('provider') for k in stored]}"
    )
    assert configured, (
        f"a Virtuals key is stored but all {len(rows)} Virtuals rows report "
        "configured=false — the catalog did not see the key"
    )
    assert pill and EXPECTED_DEFAULT_MODEL.lower() in pill.lower(), (
        f"first-run should open on {EXPECTED_DEFAULT_MODEL!r}; pill reads {pill!r}"
    )


def vc2_the_pill_offers_the_curated_virtuals_set(s: DriverSession) -> None:
    """Three rows, not fifty-seven — the two-tier picker, on real data."""

    s.click(MODEL_PILL)
    assert s.wait_for("[role=menuitemradio]", 15), "the model picker never opened"
    time.sleep(0.3)
    s.shot("ftue-model-picker")

    rows = s.evaluate(JS_PILL_ROWS) or []
    assert rows, "the picker rendered no rows"
    selected = [r for r in rows if r["checked"]]
    assert selected and EXPECTED_DEFAULT_MODEL.lower() in selected[0]["text"].lower(), (
        f"the checked row should be {EXPECTED_DEFAULT_MODEL!r}; rows={rows}"
    )
    # The whole "is 57 models too many" question, answered against live data.
    assert len(rows) <= 12, (
        f"the composer picker should stay short; it offered {len(rows)} rows"
    )
    print(f"  picker rows ({len(rows)}):")
    for r in rows:
        print(f"     {'●' if r['checked'] else ' '} {r['text']}")
    s.press("body", "Escape")


def vc3_a_virtuals_run_renders_in_studio(s: DriverSession) -> None:
    """Send a real message through Virtuals and land on the Run cockpit."""

    s.press("body", "Escape")
    s.send_first_run_message("In one short sentence, what is a vector database?")
    assert s.wait_for("[data-testid=tc-chat]", 120), "never landed on the run cockpit"
    # Wait for the ASSISTANT's reply, not merely the user's echo. `on_run`
    # is satisfied by one message, and the first message is the user's — so
    # polling it alone screenshots a transcript that is still thinking.
    for _ in range(240):
        count = s.evaluate(
            "document.querySelectorAll('[data-testid^=tc-chat-message-]').length"
        )
        thinking = s.evaluate(
            "!!Array.from(document.querySelectorAll('*'))"
            ".find(n => n.children.length === 0 &&"
            " (n.textContent||'').trim() === 'Thinking')"
        )
        if (count or 0) >= 2 and not thinking:
            break
        time.sleep(1)
    assert s.on_run(), "the run produced no messages"
    time.sleep(1.5)  # let the last tokens paint

    if not s.present("[data-testid=run-mode-studio]"):
        raise PhaseSkipped("no mode switcher on this surface")
    s.click("[data-testid=run-mode-studio]")
    time.sleep(1)
    s.shot("run-studio-mode")
    assert s.run_mode() in ("studio", None), f"expected Studio, got {s.run_mode()!r}"
    print(f"  studio: mode={s.run_mode()!r} pill={s.model_pill()!r}")


def vc4_the_same_run_renders_in_focus(s: DriverSession) -> None:
    """⌘M's destination — the same run, the quiet mode."""

    if not s.present("[data-testid=run-mode-focus]"):
        raise PhaseSkipped("no mode switcher on this surface")
    s.click("[data-testid=run-mode-focus]")
    time.sleep(1.5)
    s.shot("run-focus-mode")
    assert s.on_run(), "the transcript vanished when switching to Focus"
    print(f"  focus: mode={s.run_mode()!r}")


def vc5_settings_shows_virtuals_connected(s: DriverSession) -> None:
    """Settings → Provider keys, with a real key stored."""

    s.click(SETTINGS_BUTTON)
    assert s.wait_for("[data-testid=settings-surface]", 20), "Settings never opened"
    s.click(NAV_PROVIDER_KEYS)
    assert s.wait_for("[data-testid=provider-keys-page]", 20), (
        "Provider keys page never rendered"
    )
    time.sleep(0.5)
    s.shot("settings-virtuals-connected")

    row = s.evaluate(JS_CONNECTED_ROW)
    assert row, "Virtuals is not in the Connected list"
    # Rotate is the connected affordance; Add is the unconnected one.
    assert s.present(f"[data-testid=provider-rotate-{PROVIDER}]"), (
        "the connected Virtuals row offers no Rotate control"
    )
    # The masked hint is display-safe; the plaintext must never come back.
    assert "acp-" not in row.lower() or "…" in row or "•" in row, (
        f"the row may be showing key material: {row!r}"
    )
    print(f"  connected row: {row!r}")


def vc6_settings_models_lists_the_full_catalog(s: DriverSession) -> None:
    """The other tier: every Virtuals model, curatable."""

    if not s.present('[data-slug="models"]'):
        raise PhaseSkipped("no Models section in this profile")
    s.click('[data-slug="models"]')
    time.sleep(1.2)
    s.shot("settings-models-virtuals")

    count = s.evaluate(
        "document.querySelectorAll('[data-testid^=model-row-]').length"
        " || document.querySelectorAll('[role=switch]').length"
    )
    print(f"  Settings → Models rendered {count} rows")


def main() -> int:
    plan = JourneyPlan("virtuals-connected")
    plan.boot(
        "virtuals key",
        lambda: DriverSession(name="virtuals-connected"),
        setup=sign_in_and_connect,
        phases=[
            (
                "VC-1",
                "first-run opens on Claude Sonnet 5",
                vc1_first_run_opens_on_the_everyday_rung,
            ),
            (
                "VC-2",
                "the pill offers the curated set",
                vc2_the_pill_offers_the_curated_virtuals_set,
            ),
            (
                "VC-3",
                "a Virtuals run renders in Studio",
                vc3_a_virtuals_run_renders_in_studio,
            ),
            (
                "VC-4",
                "the same run renders in Focus",
                vc4_the_same_run_renders_in_focus,
            ),
            (
                "VC-5",
                "Settings shows Virtuals connected",
                vc5_settings_shows_virtuals_connected,
            ),
            (
                "VC-6",
                "Settings → Models lists the catalog",
                vc6_settings_models_lists_the_full_catalog,
            ),
        ],
    )
    return plan.finish()


if __name__ == "__main__":
    raise SystemExit(main())
