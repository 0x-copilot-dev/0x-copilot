# Design-parity report — first-run · `gate`

Design baseline (source of truth) vs live app, by computed style.

- Design: `surfaces/first-run/out/design-gate.json`
- Live: `surfaces/first-run/out/live-gate.json`

**Summary:** 🔴 HIGH 12 · 🟠 MEDIUM 30 · 🟡 LOW 6 · ⚪ INFO 5

## 🔴 HIGH (12)

| Element           | Group     | Property        | Design → Live                                                                  |
| ----------------- | --------- | --------------- | ------------------------------------------------------------------------------ |
| `topbar.brand`    | Top bar   | fontSize        | 12.5px → 16px (+3.5px)                                                         |
| `topbar.skip`     | Top bar   | fontFamily      | typeface class changed (mono → sans)                                           |
| `topbar.skip`     | Top bar   | fontSize        | 10px → 13.6px (+3.6px)                                                         |
| `hero.h1`         | Hero      | color           | rgb(236, 236, 241) (--tx) → rgb(212, 212, 219) (--tx2)                         |
| `card.local.meta` | Gate card | color           | rgb(100, 100, 109) (--mut2) → rgb(152, 152, 159) (--mut)                       |
| `card.local.body` | Gate card | fontSize        | 11.5px → 13.6px (+2.1px)                                                       |
| `btn.primary`     | Gate card | color           | rgb(11, 10, 14) (#0b0a0e (literal near-black)) → rgb(8, 19, 29) (--accent-ink) |
| `btn.primary`     | Gate card | borderColor     | rgb(95, 178, 236) (--accent/--sky) → rgba(0, 0, 0, 0) (transparent)            |
| `card.key.meta`   | Gate card | color           | rgb(100, 100, 109) (--mut2) → rgb(152, 152, 159) (--mut)                       |
| `card.key.body`   | Gate card | fontSize        | 11.5px → 13.6px (+2.1px)                                                       |
| `btn.secondary`   | Gate card | backgroundColor | rgb(29, 29, 35) (--panel3) → rgb(22, 22, 26) (--panel2)                        |
| `btn.secondary`   | Gate card | borderColor     | rgba(255, 255, 255, 0.18) (--line3) → rgba(255, 255, 255, 0.06) (--line)       |

## 🟠 MEDIUM (30)

| Element            | Group     | Property     | Design → Live                                              |
| ------------------ | --------- | ------------ | ---------------------------------------------------------- |
| `topbar.brand`     | Top bar   | fontWeight   | 600 → 400                                                  |
| `topbar.brand`     | Top bar   | gap          | 7px → 4px                                                  |
| `topbar.brand.zx`  | Top bar   | fontSize     | 12.5px → 14px (+1.5px)                                     |
| `topbar.skip`      | Top bar   | padding      | 0px → 2px 4px                                              |
| `topbar.skip`      | Top bar   | borderRadius | 0px → 6px                                                  |
| `hero.h1`          | Hero      | fontSize     | 23px → 22.4px (-0.6px)                                     |
| `hero.h1`          | Hero      | margin       | 0px 0px 7px 0px → 0px                                      |
| `hero.sub`         | Hero      | fontSize     | 12.5px → 13.6px (+1.1px)                                   |
| `gate.grid`        | Gate      | gap          | 10px → 16px                                                |
| `gate.grid`        | Gate      | alignItems   | stretch → normal                                           |
| `card.local`       | Gate card | padding      | 15px 16px → 24px                                           |
| `card.local`       | Gate card | gap          | 7px → 8px                                                  |
| `card.local.title` | Gate card | fontSize     | 13px → 14px (+1.0px)                                       |
| `card.local.meta`  | Gate card | fontSize     | 9.5px → 9px (-0.5px)                                       |
| `card.local.body`  | Gate card | flexGrow     | flex-grow 1 → 0 (affects vertical fill / button placement) |
| `btn.primary`      | Gate card | fontWeight   | 600 → 500                                                  |
| `btn.primary`      | Gate card | padding      | 7px 13px → 4px 8.8px                                       |
| `btn.primary`      | Gate card | borderRadius | 8px → 6px                                                  |
| `btn.primary`      | Gate card | gap          | 6px → 8px                                                  |
| `card.key.title`   | Gate card | fontSize     | 13px → 14px (+1.0px)                                       |
| `card.key.meta`    | Gate card | fontSize     | 9.5px → 9px (-0.5px)                                       |
| `btn.secondary`    | Gate card | fontWeight   | 600 → 500                                                  |
| `btn.secondary`    | Gate card | padding      | 7px 13px → 4px 8.8px                                       |
| `btn.secondary`    | Gate card | borderRadius | 8px → 6px                                                  |
| `btn.secondary`    | Gate card | gap          | 6px → 8px                                                  |
| `footer`           | Footer    | fontSize     | 9.5px → 9px (-0.5px)                                       |
| `footer`           | Footer    | padding      | 0px 18px 12px 18px → 12px 16px                             |
| `footer`           | Footer    | borderWidth  | 0px → 1px 0px 0px 0px                                      |
| `footer.left`      | Footer    | fontSize     | 9.5px → 9px (-0.5px)                                       |
| `footer.right`     | Footer    | fontSize     | 9.5px → 9px (-0.5px)                                       |

## 🟡 LOW (6)

| Element            | Group     | Property   | Design → Live                              |
| ------------------ | --------- | ---------- | ------------------------------------------ |
| `hero.h1`          | Hero      | lineHeight | 27.6px → 26.88px                           |
| `hero.sub`         | Hero      | lineHeight | 19.375px → normal                          |
| `card.local.title` | Gate card | tag        | <b> → <h2> (semantic/default-style change) |
| `card.local.body`  | Gate card | lineHeight | 17.25px → normal                           |
| `card.key.title`   | Gate card | tag        | <b> → <h2> (semantic/default-style change) |
| `card.key.body`    | Gate card | lineHeight | 17.25px → normal                           |

## ⚪ INFO (5)

| Element             | Group     | Property        | Design → Live                                                                                                                             |
| ------------------- | --------- | --------------- | ----------------------------------------------------------------------------------------------------------------------------------------- |
| `topbar.brand`      | Top bar   | text            | “” → “0xCopilot”                                                                                                                          |
| `topbar.walletChip` | Top bar   | missing-in-live | expected: harness limitation — the live gate renders the chip only when a profilePort supplies a wallet address; not wired in this render |
| `card.local.meta`   | Gate card | text            | “Qwen 3 4B · 5.6 GB · free forever” → “Qwen 3 4B · 4.3 GB · free forever”                                                                 |
| `trial.link`        | Gate      | missing-in-live | expected: hosted-trial lane deliberately SHELVED in v1 (README §7.1) — correct that the live app omits it                                 |
| `footer.right`      | Footer    | text            | “nothing leaves this machine” → “keys in OS keychain · runs via your provider”                                                            |
