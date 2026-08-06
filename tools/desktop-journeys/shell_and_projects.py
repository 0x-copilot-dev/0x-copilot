#!/usr/bin/env python3
"""shell-and-projects — the fixed window frame, and Projects as a real container.

Two claims that share a boot because both are cheap and neither needs a rich
run: the shell must never let the document scroll, and a chat must be able to
enter a project and stay there.

The phases are ordered by the state they consume. SP-1/SP-2 need the VIRGIN
gates (sign-in, then FTUE) and must run before the key is added; SP-6 needs a
profile with ZERO projects; SP-7 onwards need the two projects it seeds. They
cannot be reordered, only removed.

    python3 tools/desktop-journeys/shell_and_projects.py

Folds in: shell-overflow/{shell_document_overflow, short_window_surfaces},
projects-filing/{file_a_chat, scope_and_refile}.

Requires an Anthropic key in services/ai-backend/.env (never printed) — the app
will not leave the first-run gate without a model configured.
"""

from __future__ import annotations

import json
import os
import time

from _lib import DriverSession, JourneyPlan, load_env_key, require

PROVIDER = os.environ.get("SHELL_OVERFLOW_PROVIDER", "anthropic")

# The two short window heights the walk is repeated at. 600 is what a user gets
# by halving the default window; the second is THE SHORTEST WINDOW THE APP
# ALLOWS, measured at runtime rather than written down.
#
# It used to be a literal 420, and that number could never be reached: the
# BrowserWindow sets `minHeight` (`apps/desktop/main/window.ts`, pinned by
# `window.test.ts`), the window manager clamps to it, and `apply_size`'s vacuity
# guard then failed every phase that walked these sizes — permanently, on a
# state no user can produce. Measuring means this tracks the app's own minimum
# for free and can never again test a window that cannot exist.
#
# Shortness is not what carries these phases anyway: `assert_surface_self_scrolls`
# checks the surface OWNS an internal scroll, is not clipped by the frame, and is
# reachable at both ends — structural properties that hold whether or not the
# content happens to overflow at a given height (it reports "fits, no scroll
# needed" as a pass). The original note conceded as much: the FTUE's regression
# was the sized-past-the-frame one, "which is height independent".
TALL_SHORT_HEIGHT = 600
SIZE_WIDTH = 1200

_shortest_height: int | None = None


def short_sizes(s: DriverSession) -> list[tuple[int, int]]:
    """`[(1200, 600), (1200, <app minimum>)]`, measuring the minimum once.

    Asking for an absurd height and reading back what the window actually became
    IS the measurement — the clamp is the app's own `minHeight`.
    """

    global _shortest_height
    if _shortest_height is None:
        reported = s.resize(SIZE_WIDTH, 1)
        viewport = reported.get("viewport") or {}
        got = viewport.get("innerHeight")
        assert got is not None, f"could not measure the window floor: {reported}"
        _shortest_height = int(got)
        print(f"  [size] app's shortest window measured at {_shortest_height}px")
        # A floor at or above the default would mean the walk never shortens the
        # window at all, and every short-window assertion below would be
        # vacuous — the exact failure this whole mechanism exists to prevent.
        assert _shortest_height < TALL_SHORT_HEIGHT, (
            f"the window floor ({_shortest_height}px) is not shorter than "
            f"{TALL_SHORT_HEIGHT}px — there is no short window left to test"
        )
    return [(SIZE_WIDTH, TALL_SHORT_HEIGHT), (SIZE_WIDTH, _shortest_height)]


FRAME = "[data-testid=desktop-window-frame]"

PROJECT_A = "Acme renewal"
PROJECT_B = "Kleos research"

# Rail destinations to sweep (solo profile labels, per destinationsForProfile).
DESTINATIONS = ["Run", "Chats", "Projects", "Activity", "Tools", "Skills"]

# Root-element overflow, in px. 0 is the only healthy value.
JS_DOC_OVERFLOW = (
    "(()=>{const d=document.documentElement;return d.scrollHeight-d.clientHeight})()"
)

# Every laid-out abs-positioned element whose containing block escaped to the
# initial containing block. `offsetParent === body` is exactly that condition in
# Blink. Elements in a `display:none` subtree report `offsetParent === null` and
# are skipped, as are deliberate `position: fixed` overlays (also null).
JS_ESCAPED = """
(()=>{const out=[];
document.querySelectorAll("*").forEach(e=>{
  if(getComputedStyle(e).position!=="absolute")return;
  if(e.offsetParent!==document.body)return;
  const r=e.getBoundingClientRect();
  if(r.width===0&&r.height===0)return;
  const cls=(e.className||"").toString().split(" ").filter(Boolean).join(".");
  const pcls=(e.parentElement&&e.parentElement.className||"").toString()
    .split(" ").filter(Boolean).join(".");
  out.push({
    el:e.tagName.toLowerCase()+(cls?"."+cls:""),
    testid:e.getAttribute("data-testid")||null,
    parent:(e.parentElement?e.parentElement.tagName.toLowerCase():"?")
      +(pcls?"."+pcls:""),
    docTop:Math.round(r.top+document.documentElement.scrollTop),
  })});
return out})()
"""

JS_SETTINGS_SLUGS = (
    "Array.from(document.querySelectorAll('[role=tab][data-slug]'))"
    ".map(t=>t.getAttribute('data-slug'))"
)

# Name the elements whose boxes extend past the document's client box.
JS_CULPRITS = """
(() => {
  const d = document.documentElement;
  const limitY = d.clientHeight, limitX = d.clientWidth;
  const out = [];
  for (const el of document.querySelectorAll("*")) {
    const r = el.getBoundingClientRect();
    if (r.width === 0 && r.height === 0) continue;
    const overY = Math.round(r.bottom - limitY);
    const overX = Math.round(r.right - limitX);
    if (overY <= 1 && overX <= 1) continue;
    // Only report the elements that CAUSE the overflow: an element inside a
    // clipping ancestor is already contained, so walk up looking for a clipper
    // whose containing-block chain actually holds it.
    const cs = getComputedStyle(el);
    let clipped = false;
    for (let p = el.parentElement; p; p = p.parentElement) {
      const ps = getComputedStyle(p);
      const clips = ps.overflowX !== "visible" || ps.overflowY !== "visible";
      const positioned = ps.position !== "static";
      if (clips && (cs.position !== "absolute" || positioned)) { clipped = true; break; }
      if (cs.position === "fixed") break;
    }
    if (clipped) continue;
    out.push({
      tag: el.tagName.toLowerCase(),
      cls: (el.className || "").toString().slice(0, 60),
      testid: el.getAttribute("data-testid"),
      position: cs.position,
      overY, overX,
    });
  }
  // Deepest/worst offenders first, capped so the log stays readable.
  return out.sort((a, b) => b.overY - a.overY).slice(0, 6);
})()
"""

JS_REACHABILITY = """
(() => {
  const surface = document.querySelector(%(sel)s);
  if (!surface) return { missing: true };
  const frame = document.querySelector(%(frame)s);
  if (!frame) return { noFrame: true };

  const cs = getComputedStyle(surface);
  const fr = frame.getBoundingClientRect();

  // 1. The surface box must sit INSIDE the frame's content box. A surface
  //    sized past the frame (e.g. `min-height: 100vh` against a frame that is
  //    a titlebar inset shorter) has its tail clipped by the frame's
  //    `overflow: hidden`, and no amount of internal scrolling reveals it.
  const sr = surface.getBoundingClientRect();
  const clippedBottom = Math.round(sr.bottom - fr.bottom);
  const clippedTop = Math.round(fr.top - sr.top);

  // 2. Scroll the surface to its extremes and measure whether its own content
  //    is reachable at each end. Scrollable overflow never extends above the
  //    start edge, so a centred child that overflows a row flex container is
  //    permanently cut off at the top — check both ends, not just the bottom.
  const children = Array.from(surface.children).filter(
    (c) => c.getBoundingClientRect().height > 0,
  );
  const visibleTop = Math.max(sr.top, fr.top);
  const visibleBottom = Math.min(sr.bottom, fr.bottom);

  surface.scrollTop = 0;
  const firstTop = children.length
    ? Math.round(children[0].getBoundingClientRect().top - visibleTop)
    : 0;

  surface.scrollTop = surface.scrollHeight;
  const lastBottom = children.length
    ? Math.round(
        children[children.length - 1].getBoundingClientRect().bottom -
          visibleBottom,
      )
    : 0;
  const scrolledTo = surface.scrollTop;
  surface.scrollTop = 0;

  return {
    overflowY: cs.overflowY,
    height: Math.round(sr.height),
    scrollHeight: surface.scrollHeight,
    clientHeight: surface.clientHeight,
    overflows: surface.scrollHeight > surface.clientHeight,
    scrolledTo,
    clippedTop,
    clippedBottom,
    firstTop,
    lastBottom,
  };
})()
"""


class Violations:
    """Collect every violation in a phase, then fail once with all of them.

    A layout sweep that stops at the first bad section makes you re-run the
    whole boot per defect. These sweeps are read as a punch-list.
    """

    def __init__(self) -> None:
        self.items: list[str] = []

    def fail(self, message: str) -> None:
        print(f"  ✗ {message}")
        self.items.append(message)

    @staticmethod
    def ok(message: str) -> None:
        print(f"  ✓ {message}")

    def raise_if_any(self) -> None:
        if self.items:
            raise AssertionError(
                f"{len(self.items)} violation(s): " + " | ".join(self.items)
            )


# ── invariant A — the document cannot scroll ─────────────────────────────────
def assert_document_frozen(s: DriverSession, v: Violations, where: str) -> None:
    """The document must have ZERO scrollable overflow, in both axes.

    Deliberately stricter than "the user cannot scroll the window".
    `overflow: hidden` clips and removes the scrollbar, but the box remains a
    scroll container: `scrollHeight` still reports the full scrollable overflow
    region, and a programmatic `scrollTop` write is still honoured. So the
    document not scrolling for a *user* does not prove nothing escaped — only
    `scrollHeight === clientHeight` does. Both axes are checked, because a
    vertical-only check passes while the document still scrolls horizontally.
    """

    m = s.document_scroll()
    if m is None:
        v.fail(f"{where}: could not measure document scroll")
        return
    bad = False
    if m["scrollHeight"] != m["clientHeight"]:
        bad = True
        v.fail(
            f"{where}: document scrolls vertically — scrollHeight={m['scrollHeight']} "
            f"clientHeight={m['clientHeight']} "
            f"(overflow {m['scrollHeight'] - m['clientHeight']}px)"
        )
    if m["scrollWidth"] != m["clientWidth"]:
        bad = True
        v.fail(
            f"{where}: document scrolls horizontally — scrollWidth={m['scrollWidth']} "
            f"clientWidth={m['clientWidth']} "
            f"(overflow {m['scrollWidth'] - m['clientWidth']}px)"
        )
    if bad:
        for row in s.evaluate(JS_CULPRITS) or []:
            print(
                f"      ↳ {row['tag']}.{row['cls'] or '(no class)'}"
                + (f" [{row['testid']}]" if row["testid"] else "")
                + f" position:{row['position']} overflowsY={row['overY']}px"
                f" overflowsX={row['overX']}px"
            )
    else:
        v.ok(f"{where}: document frozen in both axes")


def assert_surface_self_scrolls(
    s: DriverSession, v: Violations, selector: str, where: str
) -> None:
    """A full-window surface must be fully reachable by scrolling INSIDE itself.

    Because the document cannot scroll, anything that sizes itself past the
    frame becomes permanently unreachable — a defense-in-depth fix that
    silently creates an unreachable-content bug. Both `.loginx-shell` and `.fr`
    regressed in exactly this way.
    """

    before = len(v.items)
    m = s.evaluate(JS_REACHABILITY % {"sel": f'"{selector}"', "frame": f'"{FRAME}"'})
    if m is None or m.get("missing") or m.get("noFrame"):
        v.fail(f"{where}: could not measure {selector} (result={m})")
        return

    if m["overflowY"] not in ("auto", "scroll"):
        v.fail(
            f"{where}: {selector} has overflow-y: {m['overflowY']} — it owns no "
            "internal scroll, so on a short window its content is unreachable"
        )
    # Sized past the frame → the tail is clipped away, unreachable by scrolling.
    if m["clippedBottom"] > 1:
        v.fail(
            f"{where}: {selector} extends {m['clippedBottom']}px past the frame's "
            f"bottom (height={m['height']}) — that tail is clipped, not scrollable"
        )
    if m["clippedTop"] > 1:
        v.fail(
            f"{where}: {selector} starts {m['clippedTop']}px above the frame's top "
            "— that head is clipped, not scrollable"
        )
    # Content must be reachable at BOTH ends of the surface's own scroll range.
    if m["firstTop"] < -1:
        v.fail(
            f"{where}: {selector} content starts {abs(m['firstTop'])}px above its "
            "own visible top at scrollTop=0 — permanently unreachable "
            "(centred overflow in a row flex container)"
        )
    if m["lastBottom"] > 1:
        v.fail(
            f"{where}: {selector} content still ends {m['lastBottom']}px below its "
            f"visible bottom when scrolled fully (scrolledTo={m['scrolledTo']}) — "
            "unreachable"
        )
    if len(v.items) == before:
        detail = (
            "overflows→scrolls internally"
            if m["overflows"]
            else "fits, no scroll needed"
        )
        v.ok(f"{where}: {selector} fully reachable ({detail}, height={m['height']})")


def apply_size(s: DriverSession, v: Violations, width: int, height: int) -> None:
    reported = s.resize(width, height)
    viewport = reported.get("viewport") or {}
    got = viewport.get("innerHeight")
    print(f"  [size] requested {width}x{height} → viewport {viewport}")
    # The invariants are only meaningfully tested if the short size really took.
    if got is None or abs(int(got) - height) > 24:
        v.fail(
            f"window did not accept height {height} (innerHeight={got}) — the "
            "short-window assertions would be vacuous"
        )


# ── shell phases ─────────────────────────────────────────────────────────────
def sp1_signin_gate_short(s: DriverSession) -> None:
    """The sign-in card stays reachable when the window is short."""

    v = Violations()
    assert s.wait_for("[data-testid=sign-in-gate]"), "sign-in gate never appeared"
    for width, height in short_sizes(s):
        apply_size(s, v, width, height)
        time.sleep(0.4)
        assert_document_frozen(s, v, f"sign-in @{height}")
        assert_surface_self_scrolls(s, v, ".loginx-shell", f"sign-in @{height}")
        s.shot(f"signin-{height}")
    v.raise_if_any()


def sp2_ftue_surface_short(s: DriverSession) -> None:
    """The FTUE surface stays reachable when the window is short.

    Driven at a normal size to click through the gate, then shrunk: a control
    that is off-screen cannot be clicked, and that would be a harness failure
    dressed as a product one.
    """

    v = Violations()
    apply_size(s, v, 1200, 800)
    time.sleep(0.3)
    s.sign_in_local()
    assert s.wait_for("[data-testid=first-run-surface],.fr", 90), (
        "FTUE surface never appeared after sign-in"
    )
    time.sleep(1.0)
    for width, height in short_sizes(s):
        apply_size(s, v, width, height)
        time.sleep(0.4)
        assert_document_frozen(s, v, f"ftue @{height}")
        assert_surface_self_scrolls(s, v, ".fr", f"ftue @{height}")
        s.shot(f"ftue-{height}")
    v.raise_if_any()


def sp3_enter_the_shell(s: DriverSession) -> None:
    """Add the key and leave the first-run hero for the workspace shell.

    A transition, asserted because the rest of the file depends on it: the
    first-run surface has no nav rail, so `open_destination` cannot work until
    the skip link hands over. (The rail-click failure this replaced looked like
    an app crash; it was a missing rail.)
    """

    v = Violations()
    apply_size(s, v, 1200, 800)
    time.sleep(0.3)
    key = load_env_key(PROVIDER)  # value never printed
    print(f"  provider={PROVIDER} key_len={len(key)} (value withheld)")
    s.ftue_add_key(PROVIDER, key)

    # The FTUE composer carries the filing zone too. It was missing at first,
    # which repeated PRD-FS-10 §7's folder-bar bug exactly: the affordance
    # existed on every composer EXCEPT first run — the one send that CREATES the
    # conversation, and so the only place filing can ride the create rather than
    # a follow-up PATCH. A wiring claim, which is why it is asserted live: the
    # host has to pass the prop, and `OnboardingComposer` derives it silently.
    assert s.present("[data-testid=composer-project-filing]"), (
        "no filing zone on the FTUE composer — first run is the one send that "
        "creates the conversation, so filing must be reachable there"
    )
    # Zero projects on a fresh install ⇒ the create-only variant.
    assert s.present("[data-testid=composer-project-filing-create]"), (
        "FTUE filing zone rendered with no way to act"
    )
    s.shot("ftue-filing-zone")

    assert s.wait_for("[data-testid=first-run-skip]", 30), "FTUE skip missing"
    s.click("[data-testid=first-run-skip]")
    assert s.wait_for("[data-component=chat-shell]", 60), (
        "shell never mounted after leaving first-run"
    )
    time.sleep(1.5)
    v.raise_if_any()


def sp4_shell_frozen_at_short_heights(s: DriverSession) -> None:
    """Every destination and settings section, at both short heights."""

    v = Violations()
    for width, height in short_sizes(s):
        apply_size(s, v, width, height)
        time.sleep(0.5)
        assert_document_frozen(s, v, f"shell @{height}")

        slugs = (
            s.evaluate(
                'Array.from(document.querySelectorAll("button[data-destination]"))'
                '.map(b=>b.getAttribute("data-destination"))'
            )
            or []
        )
        if not slugs:
            v.fail(f"shell @{height}: no rail destinations — shell did not mount")
        for slug in slugs:
            s.click(f'button[data-destination="{slug}"]')
            time.sleep(1.2)
            assert_document_frozen(s, v, f"shell @{height} destination:{slug}")

        s.click('[aria-label="Settings"]')
        if not s.wait_for("[data-testid=settings-surface]", 30):
            v.fail(f"shell @{height}: settings surface never opened")
            continue
        settings_slugs = s.evaluate(JS_SETTINGS_SLUGS) or []
        if not settings_slugs:
            v.fail(f"shell @{height}: no settings sections found")
        for slug in settings_slugs:
            s.click(f'[role=tab][data-slug="{slug}"]')
            time.sleep(0.9)
            assert_document_frozen(s, v, f"shell @{height} settings:{slug}")
        s.shot(f"settings-{height}")
    v.raise_if_any()


def sp5_nothing_escapes_to_the_icb(s: DriverSession) -> None:
    """The ROOT-CAUSE half: no element resolves its containing block to the ICB.

    Stronger than the overflow check and window-height independent. An escaped
    element whose static position happens to fall ABOVE the fold inflates
    nothing today, but becomes real overflow the moment the window shrinks or
    the page grows.

    This shipped once (Settings → Model & behavior): the visually-hidden `input`
    inside `.ui-switch` was `position: absolute` while `.ui-switch` was
    `static`, so its containing block resolved to the INITIAL containing block.
    An abs-positioned box whose containing block sits outside a clipper is not
    clipped by that clipper's `overflow: hidden`, so the input escaped the
    window frame and reported its static position in DOCUMENT coordinates as
    root overflow — measured live at scrollHeight 1239 against clientHeight 800.
    jsdom does not lay out, so the escape only exists in a real engine.
    """

    v = Violations()
    apply_size(s, v, 1200, 800)
    time.sleep(0.5)

    def check(where: str) -> None:
        overflow = s.evaluate(JS_DOC_OVERFLOW)
        if overflow and int(overflow) > 0:
            v.fail(
                f"{where}: document root scrolls by {overflow}px "
                "(the shell can be lifted out of the window)"
            )
        for e in s.evaluate(JS_ESCAPED) or []:
            tid = f" data-testid={e['testid']}" if e.get("testid") else ""
            v.fail(
                f"{where}: {e['el']}{tid} (in {e['parent']}) escaped its clipper — "
                f"containing block is the ICB, laid out at document y={e['docTop']}. "
                "Give its positioned wrapper `position: relative`."
            )

    s.click('button[aria-label="Settings"]')
    assert s.wait_for("[data-testid=settings-surface]"), "Settings never mounted"
    # Advanced is collapsed by default; expand it so its sections are reachable.
    if s.present("[data-testid=settings-group-toggle-advanced]"):
        s.click("[data-testid=settings-group-toggle-advanced]")
        time.sleep(0.4)
    slugs = s.evaluate(JS_SETTINGS_SLUGS) or []
    if not slugs:
        v.fail("no settings tabs found")
    for slug in slugs:
        s.click(f"[role=tab][data-slug={slug}]")
        time.sleep(0.5)
        check(f"settings/{slug}")

    for label in DESTINATIONS:
        if not s.present(f'[aria-label="{label}"][data-destination]'):
            print(f"  — destination/{label}: not in this profile's rail")
            continue
        s.open_destination(label)
        check(f"destination/{label}")
    s.shot("icb-sweep")
    v.raise_if_any()


# ── projects helpers ─────────────────────────────────────────────────────────
def _post(s: DriverSession, path: str, body: dict) -> dict:
    """POST through the app's bridge (the `transport` helper is GET-only)."""

    js = (
        '(async()=>{try{const r=await window.bridge.ipc.invoke("transport.request",'
        f'{{method:"POST",path:{json.dumps(path)},body:{json.dumps(body)}}});'
        'if(r&&r.kind==="transport-result"){'
        'if(!r.ok)return "ERR:HTTP "+String(r.error?.status??"unknown")+'
        '" "+String(r.error?.message??"request failed");'
        "return JSON.stringify(r.value);}"
        "return JSON.stringify(r);}"
        'catch(e){return "ERR:"+e.message}})()'
    )
    raw = s.evaluate(js)
    if isinstance(raw, str) and raw.startswith("ERR:"):
        raise RuntimeError(f"POST {path} failed: {raw}")
    return json.loads(raw)


def _projects(s: DriverSession) -> list[dict]:
    return list((s.transport("GET", "/v1/projects?limit=50")).get("items") or [])


def _ensure_project(s: DriverSession, name: str, hue: int) -> str:
    """Create a project unless one of that name already exists.

    Idempotent because SP-7 runs after SP-6 has already created PROJECT_A from
    the chip. `icon_emoji` and `color_hue` are REQUIRED by the backend's create
    model — omitting them is a 422, not a defaulted row. (The app's own create
    sheet always sends both, which is why the UI path never hits this.)
    """

    for project in _projects(s):
        if project.get("name") == name:
            return str(project["id"])
    created = _post(
        s,
        "/v1/projects",
        {"name": name, "description": "", "icon_emoji": "📁", "color_hue": hue},
    )
    project_id = created.get("id")
    assert isinstance(project_id, str) and project_id, (
        f"POST /v1/projects returned no id for {name!r}: {created}"
    )
    return project_id


def _create_chat(s: DriverSession, title: str, project_id: str | None) -> str:
    body: dict = {"title": title}
    if project_id is not None:
        body["project_id"] = project_id
    created = _post(s, "/v1/agent/conversations", body)
    cid = created.get("conversation_id")
    assert isinstance(cid, str) and cid, f"no conversation_id in {created}"
    return cid


def _conversations(s: DriverSession, project_id: str | None = None) -> list[dict]:
    path = "/v1/agent/conversations?limit=50"
    if project_id is not None:
        path += f"&filter[project_id]={project_id}"
    return list((s.transport("GET", path)).get("conversations") or [])


def _project_of(s: DriverSession, conversation_id: str) -> str | None:
    row = s.transport("GET", f"/v1/agent/conversations/{conversation_id}")
    conv = row.get("conversation") if isinstance(row.get("conversation"), dict) else row
    return conv.get("project_id")


def _chip_label(s: DriverSession) -> str:
    return (
        s.evaluate(
            '(document.querySelector("[data-testid=composer-project-filing-trigger]")'
            '||{}).innerText||""'
        )
        or ""
    )


def _thread_titles(s: DriverSession) -> list[str]:
    return list(
        s.evaluate(
            "Array.from(document.querySelectorAll("
            '"[data-testid^=thread-switcher-row-]")).map(e=>e.innerText.trim())'
        )
        or []
    )


def _move_from_chats_row(
    s: DriverSession, conversation_id: str, project_id: str | None
) -> None:
    """Re-file a chat from the Chats list: row ⋯ → Move to project → pick.

    The composer's filing zone is pre-first-message only, so this is the surface
    that owns re-filing a chat that already has a transcript. The sheet mounts
    the SAME `ProjectFilingChip`, hence the identical option testids.
    """

    s.open_destination("Chats")
    row = f'[data-testid=chat-archive-row][data-conversation-id="{conversation_id}"]'
    assert s.wait_for(row, 30), f"chat row {conversation_id} not in the Chats list"
    s.click(f"{row} [data-testid=chat-archive-row-overflow-trigger]")
    assert s.wait_for("[data-testid=chat-archive-row-overflow-menu]", 15), (
        "the row's ⋯ menu never opened"
    )
    s.click("[data-testid=chat-archive-row-move-to-project]")
    assert s.wait_for("[data-testid=desktop-project-filing-sheet]", 15), (
        "'Move to project…' opened no sheet"
    )
    s.click("[data-testid=composer-project-filing-trigger]")
    assert s.wait_for("[data-testid=composer-project-filing-menu]", 15)
    if project_id is None:
        s.click("[data-testid=composer-project-filing-none]")
    else:
        s.click(
            "[data-testid=composer-project-filing-option]"
            f'[data-project-id="{project_id}"]'
        )
    time.sleep(1.5)


def _open_filing_menu(s: DriverSession, timeout_s: int = 30) -> None:
    """Open the composer's filing menu, waiting out any in-flight write.

    The trigger carries `disabled` while a filing PATCH is in flight, and the
    click is a silent no-op against a disabled button — so a bare click
    followed by `wait_for(menu)` fails with no indication of why.
    """

    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if s.evaluate(
            "(()=>{const b=document.querySelector("
            '"[data-testid=composer-project-filing-trigger]");'
            "return !!b && !b.disabled;})()"
        ):
            break
        time.sleep(1)
    assert s.present("[data-testid=composer-project-filing-trigger]"), (
        "no filing trigger on the composer"
    )
    s.click("[data-testid=composer-project-filing-trigger]")
    assert s.wait_for("[data-testid=composer-project-filing-menu]", 15), (
        "filing menu did not open"
    )


def _wait_run_settled(
    s: DriverSession, conversation_id: str, timeout_s: int = 120
) -> str:
    """Block until the chat's latest run stops moving.

    Not politeness — necessity. While a run streams the composer re-renders on
    every delta and Playwright loses the menu row mid-click ("element was
    detached from the DOM"). Re-filing mid-stream is also not a flow a person
    performs.

    The signal is the COMPOSER, not `latest_run_status`. Two attempts at the
    server-side field failed: "not running" returned instantly because the row
    carries `latest_run_status: null` for a moment after the send, and waiting
    for a terminal value timed out at 120s with the field STILL null long after
    the answer had streamed. What the user sees is authoritative for "can I
    click now": the send button is `aria-label="Stop response"` while a run is
    in flight and `"Send message"` when it is not.
    """

    deadline = time.time() + timeout_s
    while time.time() < deadline:
        idle = s.evaluate(
            "!!document.querySelector('button[aria-label=\"Send message\"]') && "
            "!document.querySelector('button[aria-label=\"Stop response\"]')"
        )
        if idle:
            time.sleep(2)  # the final render lands just after the swap
            row = next(
                (
                    c
                    for c in _conversations(s)
                    if c["conversation_id"] == conversation_id
                ),
                {},
            )
            return str(row.get("latest_run_status"))
        time.sleep(2)
    raise AssertionError(f"composer never went idle for {conversation_id}")


# ── projects phases ──────────────────────────────────────────────────────────
def sp6_create_from_chip_files_the_chat(s: DriverSession) -> None:
    """A fresh install meets filing, and creating from the chip FILES the chat.

    Projects used to be a container nothing could enter: every `project_id` in
    the renderer was a read filter, so a card read "0 chats · 0 files" forever.
    The counts were not a bug — they were the honest output of a surface with no
    write. Needs a profile with ZERO projects, so it must precede SP-7.
    """

    require(not _projects(s), "this phase needs a profile with zero projects")

    # The zone must render even with nothing to pick, because "New project…" is
    # a row that can act. Hiding it here was the original defect: a fresh
    # install never showed filing at all, at exactly the moment the user has no
    # projects and most needs the way in.
    s.open_destination("Run")
    assert s.wait_for("[data-testid=composer-project-filing]", 30), (
        "filing chip absent on Run with zero projects — the empty-state gate "
        "regressed, so a fresh install never meets filing"
    )
    # The design rule, asserted geometrically rather than by class name. Folders
    # (what the agent can REACH) sit above the frame; the project (where the
    # work BELONGS) sits below it. A refactor that moved the chip into the
    # control row would still render a chip, still pass every unit test, and be
    # the wrong product — so measure the pixels.
    assert s.evaluate(
        "(()=>{"
        'const chip=document.querySelector("[data-testid=composer-project-filing]");'
        'const frame=document.querySelector(".aui-composer-frame")'
        '||document.querySelector("[data-testid=composer-textarea]");'
        "if(!chip||!frame)return false;"
        "return chip.getBoundingClientRect().top >= "
        "frame.getBoundingClientRect().bottom - 2;})()"
    ), (
        "filing chip is not below the composer frame — the above/below split "
        "(folders up, project down) has been broken"
    )
    # With ZERO projects the zone is the CREATE-ONLY variant: a direct
    # "New project" button, not a pill whose menu's only real entry would be
    # "No project" — an absence reported as though it were a filing decision.
    # Assert the AFFORDANCE, not one shape of it; the pill and its menu are
    # asserted in SP-7 onwards, once a project exists.
    assert s.present("[data-testid=composer-project-filing-create]"), (
        "zero-project zone is not the create-only variant — a fresh install has "
        "nothing to pick, so the zone must offer the way to make one"
    )
    s.shot("chip-unfiled-zero-projects")

    s.click("[data-testid=composer-project-filing-create]")
    assert s.wait_for("[data-testid=desktop-project-create-sheet]", 20), (
        "the zone's 'New project' opened nothing"
    )
    # Create words, not edit words: the sheet reused the edit editor and said
    # "Edit project" / "Save" for a project that did not exist yet.
    editor_text = (
        s.evaluate(
            '(document.querySelector("[data-testid=project-editor]")||{}).innerText||""'
        )
        or ""
    )
    assert "New project" in editor_text, (
        f"create sheet is not titled 'New project': {editor_text[:120]!r}"
    )
    assert "Edit project" not in editor_text, "create sheet still says 'Edit project'"
    save_label = (
        s.evaluate(
            '(document.querySelector("[data-testid=project-editor-save]")'
            '||{}).innerText||""'
        )
        or ""
    )
    assert "Create" in save_label, (
        f"create sheet's primary action reads {save_label!r}, not 'Create'"
    )
    s.fill("[data-testid=project-editor-name-input]", PROJECT_A)
    time.sleep(0.3)
    # Members is a team surface; a solo desktop must not be offered a tab whose
    # only content was an internal "not wired" notice.
    assert not s.present("[data-testid=filter-tab-members]"), (
        "Members tab rendered under single_user_desktop"
    )
    s.shot("create-sheet-from-chip")
    s.click("[data-testid=project-editor-save]")

    deadline = time.time() + 30
    project_ids: list[str] = []
    while time.time() < deadline:
        project_ids = [p["id"] for p in _projects(s)]
        if project_ids:
            break
        time.sleep(1)
    assert project_ids, "POST /v1/projects never produced a project"

    # Creating from the chip must FILE the chat into it — the click meant "put
    # this chat somewhere new", so stopping at creation is a no-op.
    deadline = time.time() + 15
    while time.time() < deadline:
        if PROJECT_A in _chip_label(s):
            break
        time.sleep(0.5)
    assert PROJECT_A in _chip_label(s), (
        "created a project from the chip but the chat was left unfiled; chip "
        f"reads {_chip_label(s)!r}"
    )


def sp7_filing_menu_clears_the_composer(s: DriverSession) -> None:
    """The open filing menu must not cover the composer it belongs to.

    The first cut reused the `+` menu's renderer verbatim, which is hard-coded
    to open upward — correct for a trigger inside the frame, and it drew this
    panel straight over the Tools / Manual / model row. Unit tests could not see
    it: they assert the slot was called, never where the pixels landed. Rect
    intersection, not a class-name check.
    """

    project_id = next(
        (p["id"] for p in _projects(s) if p.get("name") == PROJECT_A), None
    )
    require(project_id, "needs the project SP-6 creates")

    _open_filing_menu(s)
    time.sleep(0.4)  # let the portal settle its fixed coords
    s.shot("chip-menu-open")
    assert not s.evaluate(
        "(()=>{"
        'const m=document.querySelector("[data-testid=composer-project-filing-menu]");'
        'const f=document.querySelector(".aui-composer-frame")'
        '||document.querySelector("[data-testid=composer-textarea]");'
        "if(!m||!f)return false;"
        "const a=m.getBoundingClientRect(),b=f.getBoundingClientRect();"
        "return !(a.bottom<=b.top||a.top>=b.bottom||"
        "a.right<=b.left||a.left>=b.right);})()"
    ), (
        "the filing menu overlaps the composer frame — anchored-popover "
        "placement regressed to opening upward"
    )
    # Re-pick it explicitly, so the ordinary option path is exercised too and
    # not only the create-then-file shortcut.
    s.click(
        f'[data-testid=composer-project-filing-option][data-project-id="{project_id}"]'
    )
    time.sleep(0.5)
    assert PROJECT_A in _chip_label(s), (
        f"chip did not adopt the picked project, reads {_chip_label(s)!r}"
    )
    s.shot("chip-filed")


def sp8_create_path_persists_project_id(s: DriverSession) -> None:
    """THE assertion: the first send carries project_id onto the create call.

    Picking a project before the first message must reach
    `POST /v1/agent/conversations`, because a chat started inside a project is
    the flow the whole design is built around. A PATCH-only implementation
    passes a unit test and fails here. The DOM showing a project name proves
    nothing about what was persisted; this reads the server's own copy.
    """

    project_id = next(
        (p["id"] for p in _projects(s) if p.get("name") == PROJECT_A), None
    )
    require(project_id, "needs the project SP-6 creates")
    assert PROJECT_A in _chip_label(s), "chat is not filed before the send"

    before = {c["conversation_id"] for c in _conversations(s)}
    s.fill("[data-testid=composer-textarea]", "Say only: filed.")
    time.sleep(0.3)
    s.click('button[aria-label="Send message"]')

    deadline = time.time() + 90
    created: dict | None = None
    while time.time() < deadline:
        fresh = [c for c in _conversations(s) if c["conversation_id"] not in before]
        if fresh:
            created = fresh[0]
            break
        time.sleep(1.5)
    assert created is not None, "no conversation was created by the first send"
    assert created.get("project_id") == project_id, (
        "the conversation was created WITHOUT the project — the create path "
        f"dropped project_id. server row: {json.dumps(created)[:400]}"
    )
    s.shot("run-filed")

    # The count that started all of this.
    s.open_destination("Projects")
    assert s.wait_for("[data-testid=project-card-counts]", 30)
    counts = s.evaluate(
        '(document.querySelector("[data-testid=project-card-counts]")||{}).innerText||null'
    )
    assert counts is not None and not counts.strip().startswith("0 chats"), (
        f"project card still reads {counts!r} — filing did not reach the count"
    )
    s.shot("projects-grid-after")


def sp9_server_filter_narrows(s: DriverSession) -> None:
    """`filter[project_id]` really narrows the rows — the BACKEND, not the binder.

    The facade reads the app-standard `filter[project_id]` alias and rewrites it
    to ai-backend's plain `project_id`. A silently-ignored filter would return
    everything and every UI assertion downstream would still pass, because the
    panel would be showing a correct render of wrong data.

    Conversations are seeded through the app's own authenticated transport,
    which is a real facade call, not a fixture: the seeds only exist to reach
    the multi-project state quickly, and every ASSERTION still reads the server.
    """

    a_id = _ensure_project(s, PROJECT_A, 210)
    b_id = _ensure_project(s, PROJECT_B, 150)
    a1 = _create_chat(s, "Renewal pricing model", a_id)
    a2 = _create_chat(s, "Redline MSA section 7", a_id)
    b1 = _create_chat(s, "Screening rubric v2", b_id)
    loose = _create_chat(s, "Hello world", None)
    print(f"  projects A={a_id} B={b_id}")

    in_a = {c["conversation_id"] for c in _conversations(s, a_id)}
    in_b = {c["conversation_id"] for c in _conversations(s, b_id)}
    all_ids = {c["conversation_id"] for c in _conversations(s)}

    assert {a1, a2} <= in_a, (
        f"filter[project_id]={a_id} returned {sorted(in_a)}, expected to contain "
        "the two chats filed there — the facade alias is not reaching ai-backend"
    )
    assert in_b == {b1}, f"project B filter returned {sorted(in_b)}"
    assert {a1, a2, b1, loose} <= all_ids, "the unscoped list is missing seeded chats"
    assert loose not in in_a and loose not in in_b, (
        "an unfiled chat leaked into a project-scoped list"
    )
    # Every row the server returns must carry the field the UI reads.
    for row in _conversations(s, a_id):
        assert row.get("project_id") == a_id, (
            f"row {row.get('conversation_id')} is missing project_id: {row}"
        )


def sp10_threads_panel_scopes(s: DriverSession) -> None:
    """The Threads panel scoped to a project lists its chats and NOT the others'."""

    a_id = _ensure_project(s, PROJECT_A, 210)

    # Bounce off Run and back so the cockpit REMOUNTS: the project list is
    # fetched on mount and memoised in a module-level cache, and these projects
    # were seeded through the transport after that first fetch, so without a
    # remount the picker would still hold the empty list it read at boot. A user
    # creating a project through the sheet does not need this — that path calls
    # the hook's own `reload`.
    s.open_destination("Chats")
    s.open_destination("Run")
    if not s.present("[data-testid=thread-switcher-title]"):
        assert s.wait_for("[data-testid=thread-switcher-toggle]", 20), (
            "no Threads toggle in the run header"
        )
        s.click("[data-testid=thread-switcher-toggle]")
        time.sleep(1.5)
    assert s.wait_for("[data-testid=thread-switcher-scope]", 30), (
        "no scope control in the Threads panel"
    )
    s.shot("threads-unscoped")

    unscoped = _thread_titles(s)
    assert any("Hello world" in t for t in unscoped), (
        f"unscoped panel is missing the unfiled chat: {unscoped}"
    )

    s.click("[data-testid=thread-switcher-scope-trigger]")
    assert s.wait_for("[data-testid=thread-switcher-scope-menu]")
    s.shot("threads-scope-menu")
    s.click(f'[data-testid=thread-switcher-scope-option][data-project-id="{a_id}"]')
    time.sleep(2)

    scoped = _thread_titles(s)
    assert any("Renewal pricing" in t for t in scoped), (
        f"scoped panel does not list project A's chats: {scoped}"
    )
    assert not any("Screening rubric" in t for t in scoped), (
        f"project B's chat leaked into a panel scoped to A: {scoped}"
    )
    assert not any("Hello world" in t for t in scoped), (
        f"an unfiled chat leaked into a scoped panel: {scoped}"
    )
    s.shot("threads-scoped-to-a")


def sp11_new_run_inherits_scope_then_refile_and_unfile(s: DriverSession) -> None:
    """New-run inheritance, the PATCH re-file path, and the explicit-null unfile.

    Three writes that only exist once there is more than one project. Unfiling
    is separate from re-filing because `null` and "absent" are different things
    to a PATCH, and only one of them clears.
    """

    a_id = _ensure_project(s, PROJECT_A, 210)
    b_id = _ensure_project(s, PROJECT_B, 150)

    # C. "New run" inherits the scope — the coupling that justifies putting the
    # scope under the button. The chat does not exist until the first send, so
    # the chip is what carries the inheritance until then.
    before = {c["conversation_id"] for c in _conversations(s)}
    s.click("[data-testid=thread-switcher-new]")
    time.sleep(2)
    assert PROJECT_A in _chip_label(s), (
        f"a new run under scope A opened unfiled; chip reads {_chip_label(s)!r}"
    )
    s.shot("new-run-inherits-scope")

    s.fill("[data-testid=composer-textarea]", "Say only: scoped.")
    time.sleep(0.3)
    s.click('button[aria-label="Send message"]')

    deadline = time.time() + 90
    fresh: dict | None = None
    while time.time() < deadline:
        new = [c for c in _conversations(s) if c["conversation_id"] not in before]
        if new:
            fresh = new[0]
            break
        time.sleep(1.5)
    assert fresh is not None, "New run never created a conversation"
    assert fresh.get("project_id") == a_id, (
        f"a run started under scope A was filed elsewhere: {json.dumps(fresh)[:300]}"
    )

    # D. Re-file an EXISTING chat (the PATCH path) — from the CHATS ROW.
    #
    # Not from the composer: the filing zone is pre-first-message only, so once
    # a chat has a transcript the surface that owns re-filing is the Chats row's
    # ⋯ → "Move to project". That is the deliberate division — filing is
    # orientation when you START, and an action ON a chat afterwards.
    moved_id = fresh["conversation_id"]
    settled = _wait_run_settled(s, moved_id)
    print(f"  run settled ({settled}); re-filing from the Chats row")

    _move_from_chats_row(s, moved_id, project_id=b_id)
    deadline = time.time() + 20
    while time.time() < deadline:
        if _project_of(s, moved_id) == b_id:
            break
        time.sleep(1)
    assert _project_of(s, moved_id) == b_id, (
        "the UI moved the chat to B but the server still has "
        f"{_project_of(s, moved_id)!r} — the PATCH did not persist"
    )
    s.shot("refiled-to-b")

    # E. Unfiling clears the field. `null` and "absent" differ to a PATCH.
    _move_from_chats_row(s, moved_id, project_id=None)
    deadline = time.time() + 20
    while time.time() < deadline:
        if _project_of(s, moved_id) is None:
            break
        time.sleep(1)
    assert _project_of(s, moved_id) is None, (
        "unfiling left the chat filed as "
        f"{_project_of(s, moved_id)!r} — the write sent no explicit null"
    )
    s.shot("unfiled")

    # Observation, deliberately NOT an assertion. Unrelated to filing, found
    # while building this journey: the conversation list's `latest_run_status`
    # still read "running" after the composer had gone idle and the answer had
    # fully streamed. The Chats list projects its status chip from that same
    # field (`chatArchiveStatus`), so a stale value there would show a finished
    # chat as still working. Printed rather than asserted because it is outside
    # this change's scope and needs its own investigation.
    final = next((c for c in _conversations(s) if c["conversation_id"] == moved_id), {})
    print(
        "  OBSERVATION latest_run_status="
        f"{final.get('latest_run_status')!r} while the composer is idle"
    )


def main() -> int:
    plan = JourneyPlan("shell-and-projects")
    plan.boot(
        "source · fresh",
        lambda: DriverSession(name="shell-and-projects"),
        phases=[
            ("SP-1", "sign-in gate reachable at short heights", sp1_signin_gate_short),
            ("SP-2", "FTUE surface reachable at short heights", sp2_ftue_surface_short),
            (
                "SP-3",
                "add a key, meet filing on the FTUE, enter the shell",
                sp3_enter_the_shell,
            ),
            (
                "SP-4",
                "document frozen across every destination + settings section",
                sp4_shell_frozen_at_short_heights,
            ),
            (
                "SP-5",
                "nothing escapes to the initial containing block",
                sp5_nothing_escapes_to_the_icb,
            ),
            (
                "SP-6",
                "a fresh install meets filing, and create-from-chip files the chat",
                sp6_create_from_chip_files_the_chat,
            ),
            (
                "SP-7",
                "the filing menu clears the composer it belongs to",
                sp7_filing_menu_clears_the_composer,
            ),
            (
                "SP-8",
                "the create path persists project_id server-side",
                sp8_create_path_persists_project_id,
            ),
            ("SP-9", "filter[project_id] narrows the rows", sp9_server_filter_narrows),
            (
                "SP-10",
                "the Threads panel scoped to a project excludes the others",
                sp10_threads_panel_scopes,
            ),
            (
                "SP-11",
                "new-run inherits scope; re-file and unfile persist",
                sp11_new_run_inherits_scope_then_refile_and_unfile,
            ),
        ],
    )
    return plan.finish()


if __name__ == "__main__":
    raise SystemExit(main())
