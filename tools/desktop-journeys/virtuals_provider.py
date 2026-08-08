#!/usr/bin/env python3
"""virtuals — the Virtuals compute gateway as a native BYOK provider.

One boot, driven exactly as a new user meets Virtuals: the first-run key card,
Settings → Provider keys, and the model catalog read THROUGH the running app.

**This journey needs no key, and that is deliberate.** Every claim below is
falsifiable without one, and the most valuable claim is specifically about NOT
having a good key:

    VP-3 pastes a syntactically-plausible but WRONG Virtuals key and requires
    the app to reject it.

That is the assertion the whole native integration exists for. The generic
"custom OpenAI-compatible endpoint" flow probes ``{base_url}/models``, and
``compute.virtuals.io/v1/models`` answers **200 with no credential** — so a
custom-endpoint Virtuals would have reported "connected" for a typo. VP-3 fails
if that false-pass ever comes back, and it exercises the real path to do it:
DOM → facade → backend → ``POST compute.virtuals.io/v1/chat/completions``.

Because it needs no key, it is also the one BYOK-shaped journey that runs on a
machine with an empty ``.env`` — it never skips for a missing credential.

    python3 tools/desktop-journeys/virtuals_provider.py

Network: VP-3 and VP-5 make real calls to compute.virtuals.io. VP-5 asserts the
catalog refresh landed, so it needs the gateway reachable; it SKIPS rather than
fails when the machine is offline, because "no network" is not a product defect.
"""

from __future__ import annotations

import json
import time

from _lib import (
    DriverSession,
    JourneyPlan,
    PhaseSkipped,
)

PROVIDER = "virtuals"
LABEL = "Virtuals"

SETTINGS_BUTTON = '[aria-label="Settings"]'
NAV_PROVIDER_KEYS = '[data-slug="provider-keys"]'

# Long enough to clear the format gate (>= 20 chars) and carry no other
# vendor's prefix, so it reaches the LIVE probe rather than dying client-side.
# That is the point of VP-3: the format check must NOT be what stops it.
WRONG_KEY = "virtuals-deliberately-wrong-key-0000000000"

# The toggle's rows, in DOM order, with the swatch each one carries.
JS_KEY_PROVIDER_ROWS = """
(() => {
  const g = document.querySelector('[data-testid=segmented-control]');
  if (!g) return null;
  return Array.from(g.querySelectorAll('[role=radio]')).map(n => ({
    text: (n.innerText || '').trim(),
    checked: n.getAttribute('aria-checked') === 'true',
    swatch: (n.querySelector('.fr-kf__dot') || {}).getAttribute
      ? n.querySelector('.fr-kf__dot').getAttribute('data-swatch')
      : null,
  }));
})()
"""

# The toggle wraps rather than spilling once it carries a fourth provider.
# jsdom runs no layout, so this is the only place the rule is actually MEASURED.
JS_TOGGLE_LAYOUT = """
(() => {
  const g = document.querySelector('.fr-kf__prov');
  if (!g) return null;
  const cs = getComputedStyle(g);
  const r = g.getBoundingClientRect();
  return {
    flexWrap: cs.flexWrap,
    right: Math.round(r.right),
    clientWidth: document.documentElement.clientWidth,
  };
})()
"""

JS_ALERT_TEXT = (
    "((document.querySelector('[role=alert]')||{}).innerText||'').trim().slice(0,200)"
)


def _leave_first_run(s: DriverSession) -> None:
    """Dismiss the FTUE gate so the shell (and its Settings button) exists.

    VP-3 deliberately ends with the key REJECTED, which is precisely the state
    where the composer never reveals — so the gate is still up and there is no
    app rail to click. Skipping is the honest way out: it is the same door a
    user takes when they decline to add a key.
    """

    if s.present("[data-testid=first-run-skip]"):
        s.click("[data-testid=first-run-skip]")
    assert s.wait_for(SETTINGS_BUTTON, 30), (
        "the app shell never appeared after leaving first-run"
    )


def _settings_provider_keys(s: DriverSession) -> None:
    """Open Settings → Provider keys."""

    if not s.present("[data-testid=settings-surface]"):
        _leave_first_run(s)
        s.click(SETTINGS_BUTTON)
        assert s.wait_for("[data-testid=settings-surface]", 20), "Settings never opened"
    s.click(NAV_PROVIDER_KEYS)
    assert s.wait_for("[data-testid=provider-keys-page]", 20), (
        "Provider keys page never rendered"
    )


# ── setup ────────────────────────────────────────────────────────────────────
def sign_in(s: DriverSession) -> None:
    s.sign_in_local()
    s.shot("sign-in-gate")
    assert s.wait_for("[data-testid=first-run-add-key]", 60), (
        "FTUE key card never appeared"
    )


# ── phases ───────────────────────────────────────────────────────────────────
def vp1_ftue_offers_virtuals_first(s: DriverSession) -> None:
    """The first-run toggle leads with Virtuals and carries four providers."""

    s.shot("ftue-gate")
    s.click("[data-testid=first-run-add-key]")
    assert s.wait_for("[data-testid=first-run-keyform]", 20), "KeyForm never opened"
    s.shot("ftue-keyform-virtuals-first")

    rows = s.evaluate(JS_KEY_PROVIDER_ROWS)
    assert rows, "provider toggle not found"
    labels = [r["text"] for r in rows]
    assert labels[0].startswith(LABEL), f"Virtuals must lead the toggle; got {labels}"
    assert len(rows) == 4, f"expected four providers, got {labels}"
    assert rows[0]["checked"], (
        f"the leading provider must be preselected; checked={[r['checked'] for r in rows]}"
    )
    assert rows[0]["swatch"] == "#5ad1e8", (
        f"Virtuals swatch is inline data, got {rows[0]['swatch']!r}"
    )
    print(f"  toggle: {labels} (preselected={labels[0]!r})")


def vp2_toggle_wraps_rather_than_spilling(s: DriverSession) -> None:
    """The fourth row must reflow, not overflow — measured on the real screen.

    `SegmentedControl` is an inline-flex row that does not wrap on its own, so
    adding a fourth provider could push the last option past the viewport. Unit
    tests cannot see this: jsdom performs no layout, so a green DOM assertion
    would say nothing about whether the row fits.
    """

    layout = s.evaluate(JS_TOGGLE_LAYOUT)
    assert layout, ".fr-kf__prov not found"
    assert layout["flexWrap"] == "wrap", (
        f"toggle must be allowed to wrap; flex-wrap={layout['flexWrap']!r}"
    )
    assert layout["right"] <= layout["clientWidth"], (
        f"toggle overflows the viewport: right={layout['right']} "
        f"> clientWidth={layout['clientWidth']}"
    )
    print(
        f"  flex-wrap={layout['flexWrap']}, right={layout['right']}px "
        f"within {layout['clientWidth']}px"
    )


def vp3_a_wrong_key_is_rejected_end_to_end(s: DriverSession) -> None:
    """THE assertion: a wrong Virtuals key must not connect.

    Proves the live probe is a COMPLETION and not a model listing. Virtuals'
    /v1/models is public, so a listing probe would 200 on this key and the app
    would report success. If this phase ever passes a bad key, the false-pass
    is back.
    """

    s.fill("[data-testid=first-run-key-input]", WRONG_KEY)
    s.click("[data-testid=first-run-key-connect]")

    # The reject travels DOM → facade → backend → compute.virtuals.io and back.
    for _ in range(60):
        if s.evaluate(JS_ALERT_TEXT):
            break
        if s.present("[data-testid=first-run-composer]"):
            raise AssertionError(
                "a WRONG Virtuals key was accepted — the live probe is not "
                "verdictive (is it hitting the public /models listing again?)"
            )
        time.sleep(0.5)

    alert = s.evaluate(JS_ALERT_TEXT)
    s.shot("wrong-key-rejected")
    assert alert, "no error surfaced for a rejected key"
    assert not s.present("[data-testid=first-run-composer]"), (
        "the composer revealed despite the key being rejected"
    )
    print(f"  rejected with: {alert!r}")

    # And nothing was persisted — a rejected key must not reach the vault.
    keys = s.transport("GET", "/v1/settings/provider-keys").get("keys", [])
    stored = [k for k in keys if k.get("provider") == PROVIDER]
    assert not stored, f"a rejected key was stored anyway: {stored}"
    print("  and nothing was persisted")


def vp4_settings_lists_virtuals_as_a_native_row(s: DriverSession) -> None:
    """Settings → Provider keys carries Virtuals as a first-class row."""

    _settings_provider_keys(s)
    s.shot("settings-provider-keys")

    # No key is stored (VP-3 rejected the one we tried), so Virtuals belongs to
    # the "Add a provider" list. `provider-row-` is the CONNECTED testid and
    # would be the wrong assertion here.
    assert s.present(f"[data-testid=provider-available-{PROVIDER}]"), (
        "no Virtuals row in the Add-a-provider list"
    )
    assert s.present(f"[data-testid=provider-add-{PROVIDER}]"), (
        "Virtuals row does not offer an Add-key affordance"
    )
    # It is a NATIVE row, not the generic custom-endpoint affordance — the whole
    # point of the integration. The custom entry must still exist separately.
    assert not s.present("[data-testid=provider-row-openai_compatible]"), (
        "Virtuals should not be surfacing as a connected custom endpoint"
    )
    # The row carries a BRAND tile, not the neutral initials fallback. Caught by
    # eye on a screenshot: ProviderKeysPage keeps its own brand map, separate
    # from `PROVIDER_BRAND_COLOR` in providerMarks, so filling one leaves the
    # other showing a grey square next to four coloured ones.
    # The logo slot is `krow > span[aria-hidden] > span(tile)`; the OUTER span
    # carries the row's own surface colour, so read the inner one.
    tile_bg = s.evaluate(
        "(() => {"
        f"  const row = document.querySelector('[data-testid=provider-available-{PROVIDER}]');"
        "  if (!row) return null;"
        "  const tile = row.querySelector('span[aria-hidden=\"true\"] > span');"
        "  return tile ? getComputedStyle(tile).backgroundColor : null;"
        "})()"
    )
    assert tile_bg == "rgb(90, 209, 232)", (
        f"Virtuals should render its brand tile (#5ad1e8); got {tile_bg!r}"
    )

    order = (
        s.evaluate(
            "Array.from(document.querySelectorAll('[data-testid^=provider-available-]'))"
            ".map(n=>n.getAttribute('data-testid').replace('provider-available-',''))"
        )
        or []
    )
    assert order and order[0] == PROVIDER, (
        f"Virtuals must lead the Settings rows; got {order}"
    )
    # The Add button opens the native add-key modal, and that modal asks for a
    # key ONLY. No Base URL field is the observable difference between a native
    # provider and the generic custom endpoint: Virtuals' base_url is known to
    # the runtime, so the user is never asked to type compute.virtuals.io.
    s.click(f"[data-testid=provider-add-{PROVIDER}]")
    assert s.wait_for("[data-testid=add-key-input]", 10), (
        "the Virtuals Add-key modal never opened"
    )
    s.shot("settings-add-virtuals-modal")
    assert not s.present("[data-testid=add-key-base-url]"), (
        "the Virtuals add flow asks for a Base URL — that is the custom-endpoint "
        "shape, not a native provider"
    )
    print(f"  available rows: {order}")
    print("  add-key modal asks for a key only (no Base URL field)")


def vp5_catalog_carries_virtuals_models_unconfigured(s: DriverSession) -> None:
    """The catalog, read THROUGH the app, serves live Virtuals rows.

    The snapshot is empty on a cold boot by design (the source never fetches on
    the request path), so this polls for the background refresh to land. That
    wait IS the assertion: it proves the refresh really reaches the gateway and
    is parsed, not merely that a code path exists.
    """

    rows: list[dict] = []
    for _ in range(60):
        models = s.transport("GET", "/v1/agent/models").get("models", [])
        rows = [m for m in models if m.get("provider") == PROVIDER]
        if rows:
            break
        time.sleep(1)

    if not rows:
        raise PhaseSkipped(
            "no Virtuals rows after 60s — the catalog refresh never landed "
            "(offline? compute.virtuals.io unreachable?)"
        )

    # With no key stored, every row must read configured=false.
    configured = [m for m in rows if m.get("configured")]
    assert not configured, (
        f"{len(configured)} Virtuals models report configured=true with no key stored"
    )

    # The rows must carry the metadata the picker renders — this is what a
    # hardcoded list would have had to hand-maintain.
    priced = [m for m in rows if m.get("output_cost_per_mtok")]
    assert priced, "no Virtuals row carries pricing"
    tiered = [m for m in rows if m.get("tier")]
    assert tiered, "no Virtuals row landed on the size ladder"

    sample = sorted(
        ({"id": m["id"], "tier": m.get("tier")} for m in tiered),
        key=lambda m: str(m["tier"]),
    )
    print(f"  {len(rows)} Virtuals rows, all configured=false; {len(priced)} priced")
    print(f"  ladder rungs: {json.dumps(sample)}")


def main() -> int:
    plan = JourneyPlan("virtuals-provider")
    plan.boot(
        "keyless",
        lambda: DriverSession(name="virtuals-provider"),
        setup=sign_in,
        phases=[
            ("VP-1", "first-run offers Virtuals first", vp1_ftue_offers_virtuals_first),
            (
                "VP-2",
                "the four-row toggle wraps, not spills",
                vp2_toggle_wraps_rather_than_spilling,
            ),
            (
                "VP-3",
                "a WRONG key is rejected end to end",
                vp3_a_wrong_key_is_rejected_end_to_end,
            ),
            (
                "VP-4",
                "Settings lists Virtuals natively",
                vp4_settings_lists_virtuals_as_a_native_row,
            ),
            (
                "VP-5",
                "the catalog serves live Virtuals rows",
                vp5_catalog_carries_virtuals_models_unconfigured,
            ),
        ],
    )
    return plan.finish()


if __name__ == "__main__":
    raise SystemExit(main())
