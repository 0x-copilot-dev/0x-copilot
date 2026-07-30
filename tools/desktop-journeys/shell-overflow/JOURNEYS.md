# shell-overflow — the desktop document must never scroll

**User story.** I make the 0xCopilot window short — half my screen height, or a
small laptop display. Every surface stays where it belongs: the window frame
never moves, and anything taller than the window scrolls _inside_ its own panel.
Nothing is cut off, and I can never drag the whole app out of its own window.

## Root cause (why this journey exists)

The desktop window is a **fixed application frame**. `.desktop-window-frame` is
`height: 100%; overflow: hidden`, and every scroll region lives inside it. Two
invariants have to hold together, and each is only safe because of the other.

### A. The document must never be scrollable

`apps/desktop/renderer/desktop.css` reserves a 32px draggable titlebar strip
(`#root { padding-top: var(--titlebar-inset) }`) because macOS
`titleBarStyle: "hiddenInset"` paints the traffic lights over the content. If the
**document** can scroll, that strip and the frame's border scroll away with it —
the whole shell can be dragged out of its own window.

That is exactly what happened. `.ui-switch` in
`packages/design-system/src/styles.css` hides its checkbox with
`position: absolute`, but `.ui-switch` itself was not a positioned element, so
the checkbox's containing block resolved past every wrapper up to the **initial
containing block**. Overflow clipping only applies to descendants whose
containing block is inside the clipper, so the frame's `overflow: hidden` did
**not** clip it: each escaped checkbox grew the document's scrollable overflow
region, and the window became scrollable.

The fix is at the source — `.ui-switch` is now `position: relative`, so the
checkbox's containing block is its own component. Measured live in the desktop
app before that line: Settings → **Model & behavior** reported a **1239px**
document against a 600px viewport (`input[data-testid=pause-at-cap-toggle]`,
639px past the client box), with **Appearance** (`appearance-reduce-motion`) and
**Privacy** (`privacy-memory-toggle`) escaping too. Destinations without
switches were all clean, which is what pinned the cause to the toggle.

The second layer is the invariant this journey is named for. The web host had
declared it all along — `apps/frontend/src/styles.css` has
`body { margin: 0; overflow: hidden; }`. The desktop host only had
`body { margin: 0 }`. **That asymmetry is why the same escaped element produced
visible damage on desktop and none on web.** `desktop.css` now declares
`overflow: hidden` too, so the _next_ escaped absolutely-positioned element
degrades to an invisible layout artefact instead of a scrollable, broken window.

The two layers are not redundant, and neither is sufficient alone:
`overflow: hidden` clips and removes the scrollbar, but the box **remains a
scroll container** — `scrollHeight` still reports the full scrollable overflow
region and a programmatic `scrollTop` write is still honoured. So the
defense-in-depth layer stops the user-visible damage while the source fix is what
actually keeps `scrollHeight === clientHeight`.

### B. Therefore every full-window surface must own its own scroll

Enforcing A is only safe if nothing leans on the document to scroll. Anything
that sizes itself past the frame and relied on the document becomes
**permanently unreachable** the moment A lands — a defense-in-depth fix that
silently creates an unreachable-content bug.

Two surfaces render before the shell and are full-window:

| Surface      | Root            | Stylesheet     | Before                                                         | Now                                                 |
| ------------ | --------------- | -------------- | -------------------------------------------------------------- | --------------------------------------------------- |
| Sign-in gate | `.loginx-shell` | `signin.css`   | `min-height: 100vh`, **no internal overflow**                  | `height: 100%` + `overflow: auto`, column direction |
| FTUE         | `.fr`           | `firstrun.css` | `height: 100%; overflow: auto` + inherited `min-height: 100vh` | adds `min-height: 0` to drop the inherited `100vh`  |

Two details matter beyond "add `overflow: auto`":

- **`height: 100%`, not `min-height: 100vh`.** The frame's content box is one
  titlebar inset _shorter_ than the viewport, so a `100vh` surface hangs 32px
  past the frame and that tail is **clipped** by the frame — no amount of
  internal scrolling reveals it. The FTUE `.fr` hit this via the shared
  `packages/chat-surface/src/onboarding/onboarding.css`, which sets
  `min-height: 100vh` for web hosts where the document _does_ scroll;
  `firstrun.css` neutralises it for the desktop frame with `min-height: 0`.
- **Column flex direction on `.loginx-shell`.** Scrollable overflow never
  extends above a scroll container's start edge. `.loginx-pane` centres the card
  with `align-items: center`; as a **row** flex item it is stretched to the
  container's height and a tall card overflows it symmetrically, so the card's
  top would be cut off and unreachable even with `overflow: auto`. As a **column**
  flex item its automatic minimum size keeps it at content height, so overflow
  grows downward into the scrollable region.

The desktop host needs **no** document-scroll escape hatch. The web host does
have one (`html.login-html, body.login-body { overflow: auto !important }`) for a
login screen that genuinely scrolls the page. Any new full-window desktop
surface must scroll internally instead — do not re-open the document scroll.

## The journey

```bash
python3 tools/desktop-journeys/shell-overflow/shell_document_overflow.py
```

Needs no provider key and starts no run — it is pure layout truth, so it is
cheap enough to run on any renderer change.

**Runs the whole walk at two short window heights.** At the 1200x800 default
every surface fits and all checks pass trivially; invariant B only bites when the
window is short. The journey uses `1200x600` (a user halving the window) and
`1200x420`, and fails if the window manager refused the size, so a vacuous pass
is not reported as green.

What each size actually exercises, as measured: at 420 the sign-in card genuinely
overflows and is reachable only by scrolling `.loginx-shell` internally
(`overflows→scrolls internally, height=386`). The FTUE body still fits at 420 —
its failure mode was the frame-clipping one, caught at **both** sizes (33px of
`.fr`, its footer included, hung past the frame). Both surfaces are asserted to
carry `overflow-y: auto` whether or not the current content needs it, so the
contract is checked even when the content happens to fit.

Window resizing goes through the driver's `resizeWindow` RPC
(`tools/cli-testing/harness/driver.mjs`), which calls `setContentSize` on the
**real** `BrowserWindow` — not a Playwright viewport override — so `vh` units,
the titlebar inset, and internal scroll regions all behave as they do for a user
dragging the window smaller.

### What it asserts

**Invariant A**, at every step — sign-in, FTUE, every rail destination, every
Settings section:

- `document.documentElement.scrollHeight === clientHeight` **and**
  `scrollWidth === clientWidth` (a single-axis check can pass while the document
  still scrolls in the other axis).
- This is deliberately **stricter** than "the user cannot scroll the window".
  Because `overflow: hidden` leaves the box a scroll container, asserting that a
  wheel or a forced `scrollTop` does nothing would pass over a fully escaped
  element and prove only that the defense-in-depth layer is present. Zero
  scrollable overflow is the assertion that catches the escape itself.
- On failure the journey prints the **culprits**: every element whose box extends
  past the document's client box and which no ancestor actually clips, with its
  tag, class, `data-testid`, `position`, and overflow in px. The bug class here is
  always "which element escaped its clipping ancestor", so the report names it
  rather than leaving a bare pixel delta. This is what identified
  `input[data-testid=pause-at-cap-toggle] position:absolute` as the `.ui-switch`
  checkbox.

**Invariant B**, for `.loginx-shell` and `.fr`:

- `overflow-y` is `auto`/`scroll` — the surface owns a scroll.
- Its box sits inside the frame's content box: no part is clipped past the
  frame's top or bottom (this is the `100vh`-past-the-frame check).
- Content is reachable at **both** ends of its own scroll range: nothing above
  its visible top at `scrollTop = 0` (the centred-overflow trap), and nothing
  below its visible bottom when scrolled fully.

### Coverage

| Step | Surface                | Selector                                      |
| ---- | ---------------------- | --------------------------------------------- |
| 1    | Sign-in gate           | `[data-testid=sign-in-gate]`, `.loginx-shell` |
| 2    | FTUE                   | `[data-testid=first-run-surface]`, `.fr`      |
| 3    | Every rail destination | `button[data-destination]` (enumerated live)  |
| 4    | Every Settings section | `[role=tab][data-slug]` (enumerated live)     |

Destinations and Settings sections are **enumerated from the live DOM**, not
hardcoded, so a newly added destination or settings section is covered
automatically. Settings is the important half — its `.ui-switch` toggles are
where the escaping absolutely-positioned element was found.

A regression in `.ui-switch` — or in any other component that absolutely
positions a child without establishing a containing block — **does** fail this
journey, because invariant A is asserted as zero scrollable overflow rather than
as "the window doesn't move". The defense-in-depth layer keeps such a regression
from being user-visible; the journey keeps it from being silent.

### Not covered

- The boot screen (`.boot`, `height: 100vh`) is not asserted: it renders before
  the frame mounts, is `overflow: hidden` with centred content, and has no
  scrollable content of its own.
- Destinations are visited but their _internal_ scroll regions are not walked —
  only that reaching each one leaves the document unscrollable. Per-destination
  scroll behaviour belongs with that destination's own journey.
- The team-profile Settings sections (`workspace`, `members`, `billing`, `audit`)
  never render under the desktop's `single_user_desktop` profile, so the 9
  sections enumerated here are all of them for this host.
