# shell-overflow — the app shell must never scroll the document

Live-smoke guard for one structural invariant of the desktop window: **the root
element never gains scrollable overflow.**

`.desktop-window-frame` is `height: 100%` with `overflow: hidden`, and every
scroll region lives _inside_ it (the settings content pane, the settings nav, the
run transcript). If `<html>` itself becomes scrollable, the entire shell — rail,
nav, content — can be lifted out of the window by a wheel event, trackpad
overscroll, or a focus move onto an off-screen element, leaving dead background
below and the rail/nav tops clipped off. The window looks broken; nothing is
recoverable by scrolling back, because the user has no idea the _document_
scrolled.

Runnable: [`shell_document_overflow.py`](./shell_document_overflow.py). It signs
in and completes the FTUE if the userData dir is fresh (key read from
`services/ai-backend/.env` via `load_env_key`, never printed), then sweeps every
settings section and every rail destination. Default provider `anthropic`;
override with `SHELL_OVERFLOW_PROVIDER=openai`.

Result vocabulary: **PASS** (invariant held) · **SKIP** (destination absent from
this profile's rail) · **FAIL** (exit 1).

---

## The regression this exists for

Settings → Model & behavior shipped with the shell liftable out of the window.

The visually-hidden `input` inside `.ui-switch` was `position: absolute` while
`.ui-switch` itself was `position: static`, so the input's containing block
resolved to the **initial containing block**. An abs-positioned box whose
containing block sits _outside_ a clipper is not clipped by that clipper's
`overflow: hidden` — so the input escaped `.desktop-window-frame` entirely and
reported its static position in **document** coordinates as root overflow.

Measured live in the packaged app at a 1200×800 window:

| Probe                            | Broken | Fixed        |
| -------------------------------- | ------ | ------------ |
| `documentElement.scrollHeight`   | 1239   | 800          |
| `documentElement.clientHeight`   | 800    | 800          |
| `.desktop-window-frame` rect top | −407   | +32          |
| escaped input `offsetParent`     | `body` | `.ui-switch` |

Fix: [`packages/design-system/src/styles.css`](../../../packages/design-system/src/styles.css)
gives `.ui-switch` `position: relative`, so the input's containing block is the
switch and the frame clips it like every other descendant. The comment there is
load-bearing — read it before touching that rule.

Why unit tests could not catch it: **jsdom does not lay out.** There is no
containing-block resolution, no `offsetParent`, no `scrollHeight`. The escape
only exists in a real engine, which is exactly the class of bug this harness
exists for.

---

## J1 — No settings section scrolls the document

**User story:** I open Settings and click through every section. The window
chrome stays put — the rail and the nav never slide out of the frame, no matter
how tall the section is.

| Step                                                      | Coverage                                                                          |
| --------------------------------------------------------- | --------------------------------------------------------------------------------- |
| Reach the shell (sign-in / FTUE tolerated, both optional) | ASSERTED (`sign-in-button` → `first-run-add-key` → `button[aria-label=Settings]`) |
| Open Settings, expand the collapsed **Advanced** group    | ASSERTED (`settings-surface`, `settings-group-toggle-advanced`)                   |
| For every `[role=tab][data-slug]`: root overflow is 0px   | ASSERTED (`documentElement.scrollHeight - clientHeight === 0`)                    |
| For every section: nothing escaped to the ICB             | ASSERTED (no laid-out `position:absolute` element has `offsetParent === body`)    |

`model-behavior` is the section that regressed — it is the tallest page and its
Spend guardrail toggle is the last row, so the escaped input landed furthest
below the fold. It is swept like every other slug rather than special-cased.

## J2 — No rail destination scrolls the document

**User story:** I click through Run, Chats, Projects, Activity, Tools and Skills.
Same invariant — the shell is a fixed frame everywhere, not just in Settings.

| Step                                                | Coverage                                                      |
| --------------------------------------------------- | ------------------------------------------------------------- |
| Each solo-profile destination in the rail is opened | ASSERTED (`[aria-label=…][data-destination]`), SKIP if absent |
| Root overflow is 0px on each                        | ASSERTED                                                      |
| Nothing escaped to the ICB on each                  | ASSERTED                                                      |

---

## Why the ICB check, not just the overflow check

The overflow check is the **symptom** and is window-height dependent: an escaped
element whose static position happens to fall _above_ the fold inflates nothing
today and becomes a live bug the moment the window shrinks or the page grows.
When this regression was found, three of the four escaped switches
(`appearance-reduce-motion`, `privacy-memory-toggle`, `web-access-toggle`) were
reporting 0px of overflow purely because they sat inside the viewport — latent,
not fixed.

The ICB check is the **cause** and is height independent, so it fails the moment
a new primitive absolutely-positions a child without anchoring its wrapper.
Deliberate `position: fixed` overlays (the toast stack) report
`offsetParent === null` and are correctly ignored, as are elements inside a
`display: none` subtree (the hidden destination outlet).

---

## J3 — A short window makes nothing unreachable

`short_window_surfaces.py`. J1/J2 sweep at the default 1200x800 window, where
every surface fits. This journey resizes the **real** window short and checks the
half that only breaks there.

**User story:** I make the window short — half my screen, or a small laptop
display. Nothing is cut off: anything taller than the window scrolls _inside_ its
own panel, and the window frame still never moves.

### Why enforcing the document invariant needed a second fix

`desktop.css` only set `body { margin: 0 }`; it never declared that the document
must not scroll. The web host has declared it all along
(`apps/frontend/src/styles.css`: `body { margin: 0; overflow: hidden }`), and that
asymmetry is why the escaped switch above produced visible damage on desktop and
none on web. `desktop.css` now declares `overflow: hidden` too, so the _next_
escape degrades to an invisible layout artefact instead of a scrollable window.

That is only safe if nothing leans on the document to scroll — otherwise the
defense-in-depth fix silently creates an **unreachable-content** bug. Both
pre-shell full-window surfaces did lean on it:

| Surface      | Root            | Stylesheet     | Was                                                            | Now                                                 |
| ------------ | --------------- | -------------- | -------------------------------------------------------------- | --------------------------------------------------- |
| Sign-in gate | `.loginx-shell` | `signin.css`   | `min-height: 100vh`, **no internal overflow**                  | `height: 100%` + `overflow: auto`, column direction |
| FTUE         | `.fr`           | `firstrun.css` | `height: 100%; overflow: auto` + inherited `min-height: 100vh` | adds `min-height: 0` to drop the inherited `100vh`  |

Two details matter beyond "add `overflow: auto`":

- **`height: 100%`, not `min-height: 100vh`.** The frame's content box is one
  titlebar inset _shorter_ than the viewport (33px with its hairline borders), so
  a `100vh` surface hangs past the frame and that tail is **clipped** by the
  frame — and since min-height beats height, `height: 100%` alone does not save
  it. `.fr` hit this via the shared
  `packages/chat-surface/src/onboarding/onboarding.css`, which sets
  `min-height: 100vh` for web hosts where the document _does_ scroll. Measured:
  33px of the FTUE surface, footer included, was unreachable at every height.
- **Column flex direction on `.loginx-shell`.** Scrollable overflow never extends
  above a scroll container's start edge. `.loginx-pane` centres the card with
  `align-items: center`; as a **row** flex item it is stretched to container
  height, so a tall card overflows symmetrically and its top lands above the
  scrollable region — unreachable even _with_ `overflow: auto`. As a **column**
  flex item its automatic minimum size holds it at content height, so overflow
  grows downward into the scrollable region.

The desktop host needs **no** document-scroll escape hatch. The web host has one
(`html.login-html, body.login-body { overflow: auto !important }`) for a login
screen that genuinely scrolls the page. Any new full-window desktop surface must
scroll internally instead — do not re-open the document scroll.

### Coverage

| Step                                                              | Coverage                                                                                  |
| ----------------------------------------------------------------- | ----------------------------------------------------------------------------------------- |
| Resize the real window to 1200x600, then 1200x420                 | ASSERTED (`resizeWindow` RPC; FAILS if the size was refused)                              |
| Sign-in gate + FTUE: surface owns `overflow-y: auto`              | ASSERTED (`.loginx-shell`, `.fr`)                                                         |
| Neither surface is clipped past the frame's top or bottom         | ASSERTED (this is the `100vh`-past-the-frame check)                                       |
| Content reachable at BOTH ends of each surface's own scroll range | ASSERTED (nothing above its visible top at `scrollTop=0`, none below when scrolled fully) |
| Every destination + every settings section, at BOTH short sizes   | ASSERTED (root overflow 0px; enumerated live, so new ones are covered)                    |

Resizing goes through the driver's `resizeWindow` RPC, which calls
`setContentSize` on the real `BrowserWindow` — not a Playwright viewport
override — so `vh` units, the titlebar inset, and internal scroll regions behave
as they do for a user dragging the window smaller.

### Why zero overflow, not "the window refuses to scroll"

`overflow: hidden` clips and drops the scrollbar, but the box **remains a scroll
container**: `scrollHeight` still reports the full scrollable overflow region and
a programmatic `scrollTop` write is still honoured. So asserting that a wheel or a
forced `scrollTop` does nothing would pass over a fully escaped element and prove
only that the defense-in-depth layer is present. Zero scrollable overflow is what
catches the escape — the same reason J1/J2 assert the ICB cause directly.
