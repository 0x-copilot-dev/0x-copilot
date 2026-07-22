# Login parity — findings & fixes

Interpretation of [`report.md`](./report.md) (the full machine table: **36 HIGH /
31 MEDIUM / 17 LOW** across `pick`/`connecting`/`sign`/`done`, plus the design-only
`werr` recovery screen). Design baseline = vendored `copilot-login.jsx`; live =
`apps/frontend` `LoginScreen` `SignInCard`, rendered by `lib/render-live-login.test.tsx`.

## 1. Structural gaps — the root of the "wallet/Google waiting & error" feedback

These are missing *screens*, not style drift, and they're the highest-priority fixes:

- **No wallet-error recovery screen.** The design has a dedicated `werr` view (icon +
  "No response from MetaMask" + **Try again** / **Choose another wallet** / **Back to
  sign-in**). The live `WalletView` union has no error variant — errors are an inline
  `.login-card__error` line, and worse, on wallet failures `_handleWalletError` calls
  `setError(...)` then `reset()` (which clears the error in the same tick), so a wallet
  error is **often never visible at all**. (`LoginScreen.tsx` view union + `_handleWalletError`.)
- **No Google waiting/error views.** Google is a fire-and-forget `window.location.assign`
  redirect — the design's "Authorizing with Google… / Google didn't finish" recovery
  has **no live analog**. This is why a raw Google error leaked in the desktop build.
- **Oversized waiting/done headings.** The design's `connecting`/`done` titles are a
  small **13.5px `h3`**; the live reuses the same **22.4px `.loginx-title` `h1`** as the
  main screens → **+8.9px**. The waiting and "Signed in" screens read as giant.

## 2. Systematic style drift (same cause as the FTUE gate)

The live resolves to **design-system rem font-sizes** + **design-system `ui-card`/
`ui-button`**, drifting from the design's hand-tuned **px** + bespoke `.login-*`/`.cbtn`:

| Where | Design → Live |
|---|---|
| Title (all states) | 18px → **22.4px** |
| Sub / body copy | 12px → 13.6px; `.empty`/card body 13px → 16px |
| Primary-option **sub** | **mono 10px → sans 12.48px** |
| "or" **divider** | **mono 9px UPPERCASE (1.08px tracking) → sans 12.48px lowercase** |
| Address **meta** (`sign`) | **mono 10px → sans 12.48px** |
| Version line | mono 9px → 11.2px |
| Option padding / radius | 10×12 / 9px → 12 / 8px |
| Google/local border | `--line2` → `--line` (fainter) |
| SIWE message color | `--tx2` → `--mut` (dimmer); bg `--ink2` → `--panel2` |
| Sign buttons | `.cbtn` 12px/500–600 · r6 · 9×12 → `ui-button--md` **14px/650** · r8 · 8.8×14.4 |
| Card | borderless floating → **design-system `Card`**: bg `--panel`, 1px `--line`, **16px radius, 32px padding** |

Note (INFO, not a defect): the live `sign` screen shows the **real** frozen EIP-4361
SIWE message; the mock showed a simplified preview. Live is more accurate there.

## 3. Fix order

1. **Add the `werr` recovery screen** (and stop `reset()` from wiping the error), and **add
   a Google waiting/error view** instead of a bare redirect. This is the #2 feedback fix.
2. **Stop reusing the 22.4px `.loginx-title` for `h3`-scale headings** (`connecting`/`done`)
   — give the waiting/done titles their own smaller size (~13.5px).
3. Align `.loginx-*` font-sizes/spacing to the design px (title 18, sub 12, body 13, meta 10).
4. Use **mono** for the option-sub, the "or" divider (uppercase + tracking), and the address meta.
5. Decide the card treatment: keep the design-system `Card` panel or match the design's
   borderless card (a design-system-stewardship call, same as the FTUE buttons).

Regenerate after changes: see `../../../SKILL.md` (render `lib/render-live-login.test.tsx`,
extract both sides, `lib/compare.mjs`).
