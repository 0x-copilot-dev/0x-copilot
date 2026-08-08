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

# Carries Virtuals' `acp-` prefix so the form INFERS Virtuals, and is long
# enough to clear the format gate — so it reaches the LIVE probe rather than
# dying client-side. That is the point of VP-3: neither inference nor the format
# check may be what stops it.
WRONG_KEY = "acp-deliberately-wrong-key-00000000000000"
# No known prefix — the one input that must make the form ASK instead of guess.
UNKNOWN_KEY = "zz-deliberately-unrecognised-key-000000"

# What the settled row concluded, read off the DOM the user is looking at.
JS_RESOLVED = """
(() => {
  const n = document.querySelector('[data-testid=first-run-key-resolved]');
  if (!n) return null;
  return {
    provider: n.getAttribute('data-provider') || '',
    text: (n.innerText || '').trim(),
    connect: ((document.querySelector('[data-testid=first-run-key-connect]')
      || {}).innerText || '').trim(),
  };
})()
"""

# The settled row's shrink contract, MEASURED against the real stylesheet.
# Same class of bug as TcWriteGateRow: the row holds one unbounded string (the
# masked key) beside a control (Change). If the string does not give way first,
# the control is what gets clipped — and jsdom, running no layout, would report
# a perfectly healthy row either way.
JS_ROW_SHRINK = """
(() => {
  const row = document.querySelector('[data-testid=first-run-key-resolved]');
  if (!row) return null;
  const masked = row.querySelector('[data-testid=first-run-key-edit]');
  const change = row.querySelector('[data-testid=first-run-key-change]');
  if (!masked || !change) return null;
  const m = getComputedStyle(masked), c = getComputedStyle(change);
  const rowBox = row.getBoundingClientRect();
  const changeBox = change.getBoundingClientRect();
  return {
    maskedShrink: m.flexShrink,
    maskedMinWidth: m.minWidth,
    maskedOverflow: m.overflow,
    changeShrink: c.flexShrink,
    changeVisible: changeBox.width > 0
      && Math.round(changeBox.right) <= Math.round(rowBox.right) + 1,
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
def vp1_key_field_asks_nothing_and_infers(s: DriverSession) -> None:
    """One field, no provider choice — and an `acp-` key resolves to Virtuals."""

    s.shot("ftue-gate")
    s.click("[data-testid=first-run-add-key]")
    assert s.wait_for("[data-testid=first-run-keyform]", 20), "KeyForm never opened"
    s.shot("ftue-keyform-one-field")

    # The toggle is gone: the card asks for a key and nothing else.
    assert not s.present("[data-testid=segmented-control]"), (
        "a provider toggle is still rendered — the form should ask nothing"
    )
    assert not s.present("[data-testid=first-run-key-picker]"), (
        "the fallback picker is open before a key was even entered"
    )

    s.fill("[data-testid=first-run-key-input]", WRONG_KEY)
    s.press("[data-testid=first-run-key-input]", "Tab")  # blur takes the verdict
    assert s.wait_for("[data-testid=first-run-key-resolved]", 10), (
        "the key never resolved to a provider"
    )
    s.shot("ftue-key-inferred-virtuals")

    row = s.evaluate(JS_RESOLVED)
    assert row and row["provider"] == PROVIDER, (
        f"acp- must infer Virtuals; resolved to {row and row['provider']!r}"
    )
    # The destination is named on the control the user is about to press.
    assert LABEL in row["connect"], (
        f"Connect must name the provider; button reads {row['connect']!r}"
    )
    print(f"  resolved={row['provider']!r}, button={row['connect']!r}")


def vp2_settled_row_never_clips_its_own_control(s: DriverSession) -> None:
    """The masked key gives way before Change does — measured, not asserted.

    jsdom performs no layout, so a unit test cannot tell a healthy row from one
    whose only escape hatch has been pushed off the edge by a long key.
    """

    shrink = s.evaluate(JS_ROW_SHRINK)
    assert shrink, "settled row or its controls not found"
    assert shrink["maskedShrink"] != "0", (
        f"the masked key must shrink; flex-shrink={shrink['maskedShrink']!r}"
    )
    assert shrink["maskedMinWidth"] in ("0px", "0"), (
        f"a flex item needs min-width:0 to shrink; got {shrink['maskedMinWidth']!r}"
    )
    assert shrink["maskedOverflow"] == "hidden", (
        f"the masked key must clip itself; overflow={shrink['maskedOverflow']!r}"
    )
    assert shrink["changeShrink"] == "0", (
        f"Change must not shrink; flex-shrink={shrink['changeShrink']!r}"
    )
    assert shrink["changeVisible"], "Change is clipped out of the settled row"
    print(
        f"  masked shrink={shrink['maskedShrink']} min-width={shrink['maskedMinWidth']}"
        f", Change fixed and on screen"
    )


def vp2b_an_unrecognised_key_asks_instead_of_guessing(s: DriverSession) -> None:
    """No prefix match is the ONE case that asks the user anything."""

    s.click("[data-testid=first-run-key-edit]")
    assert s.wait_for("[data-testid=first-run-key-input]", 10), (
        "editing the key did not restore the input"
    )
    s.fill("[data-testid=first-run-key-input]", UNKNOWN_KEY)
    s.press("[data-testid=first-run-key-input]", "Tab")
    assert s.wait_for("[data-testid=first-run-key-picker]", 10), (
        "an unrecognised key did not open the provider picker"
    )
    s.shot("ftue-unknown-key-picker")

    row = s.evaluate(JS_RESOLVED)
    assert row is not None and row["provider"] == "", (
        f"a provider was invented for an unrecognised key: {row and row['provider']!r}"
    )
    # Every provider is offered, each with the prefix that explains the miss.
    for pid in ("virtuals", "anthropic", "openai", "openrouter"):
        assert s.present(f"[data-testid=first-run-key-pick-{pid}]"), (
            f"{pid} missing from the fallback picker"
        )
    print("  no guess made; picker offers all four providers")

    # Put the recognised key back for VP-3.
    s.click("[data-testid=first-run-key-edit]")
    s.fill("[data-testid=first-run-key-input]", WRONG_KEY)
    s.press("[data-testid=first-run-key-input]", "Tab")
    assert s.wait_for("[data-testid=first-run-key-resolved]", 10)


def vp3_a_wrong_key_is_rejected_end_to_end(s: DriverSession) -> None:
    """THE assertion: a wrong Virtuals key must not connect.

    Proves the live probe is a COMPLETION and not a model listing. Virtuals'
    /v1/models is public, so a listing probe would 200 on this key and the app
    would report success. If this phase ever passes a bad key, the false-pass
    is back.
    """

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
            (
                "VP-1",
                "one field, and acp- infers Virtuals",
                vp1_key_field_asks_nothing_and_infers,
            ),
            (
                "VP-2",
                "the settled row never clips Change",
                vp2_settled_row_never_clips_its_own_control,
            ),
            (
                "VP-2b",
                "an unknown key asks, never guesses",
                vp2b_an_unrecognised_key_asks_instead_of_guessing,
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
