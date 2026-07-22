# Design-parity report — `gate`

Design baseline (source of truth) vs live app, by computed style.

- Design: `surfaces/first-run/out/design-gate.json`
- Live: `surfaces/first-run/out/live-gate.json`

**Summary:** 🔴 HIGH 1 · 🟠 MEDIUM 0 · 🟡 LOW 2 · ⚪ INFO 3

## 🔴 HIGH (1)

| Element       | Group     | Property | Design → Live                                                                  |
| ------------- | --------- | -------- | ------------------------------------------------------------------------------ |
| `btn.primary` | Gate card | color    | rgb(11, 10, 14) (#0b0a0e (literal near-black)) → rgb(8, 19, 29) (--accent-ink) |

## 🟡 LOW (2)

| Element            | Group     | Property | Design → Live                              |
| ------------------ | --------- | -------- | ------------------------------------------ |
| `card.local.title` | Gate card | tag      | <b> → <h2> (semantic/default-style change) |
| `card.key.title`   | Gate card | tag      | <b> → <h2> (semantic/default-style change) |

## ⚪ INFO (3)

| Element             | Group   | Property        | Design → Live                                                                                                                             |
| ------------------- | ------- | --------------- | ----------------------------------------------------------------------------------------------------------------------------------------- |
| `topbar.walletChip` | Top bar | missing-in-live | expected: harness limitation — the live gate renders the chip only when a profilePort supplies a wallet address; not wired in this render |
| `trial.link`        | Gate    | missing-in-live | expected: hosted-trial lane deliberately SHELVED in v1 (README §7.1) — correct that the live app omits it                                 |
| `footer.right`      | Footer  | text            | “nothing leaves this machine” → “keys in OS keychain · runs via your provider”                                                            |
