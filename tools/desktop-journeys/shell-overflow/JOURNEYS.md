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
