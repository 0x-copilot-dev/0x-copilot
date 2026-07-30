# Design-parity report — surface-language · `board-capped`

Design baseline (source of truth) vs live app, by computed style.

- Design: `surfaces/surface-language/out/design-board-capped.json`
- Live: `surfaces/surface-language/out/live-board-capped.json`

**Summary:** 🔴 HIGH 8 · 🟠 MEDIUM 13 · 🟡 LOW 9 · ⚪ INFO 6

## 🔴 HIGH (8)

| Element     | Group | Property        | Design → Live                                                                                             |
| ----------- | ----- | --------------- | --------------------------------------------------------------------------------------------------------- |
| `cap.line`  | Cap   | fontFamily      | typeface class changed (mono → sans)                                                                      |
| `cap.line`  | Cap   | fontSize        | 10px → 12px (+2.0px)                                                                                      |
| `cap.line`  | Cap   | color           | rgb(100, 100, 109) (--mut2) → rgb(152, 152, 159) (--mut)                                                  |
| `cap.line`  | Cap   | backgroundColor | oklch(0.188 0.009 276) → rgba(0, 0, 0, 0) (transparent)                                                   |
| `cap.line`  | Cap   | borderColor     | oklch(1 0 0 / 0.07) rgb(100, 100, 109) rgb(100, 100, 109) rgb(100, 100, 109) → rgb(152, 152, 159) (--mut) |
| `cap.badge` | Cap   | borderColor     | oklch(1 0 0 / 0.115) → rgba(255, 255, 255, 0.1) (--line2)                                                 |
| `cap.card`  | Cap   | backgroundColor | oklch(0.212 0.01 276) → rgb(17, 17, 20) (--panel)                                                         |
| `cap.card`  | Cap   | borderColor     | oklch(1 0 0 / 0.115) → rgba(255, 255, 255, 0.1) (--line2)                                                 |

## 🟠 MEDIUM (13)

| Element    | Group | Property      | Design → Live                                                                                                                   |
| ---------- | ----- | ------------- | ------------------------------------------------------------------------------------------------------------------------------- |
| `cap.line` | Cap   | display       | flex → block                                                                                                                    |
| `cap.line` | Cap   | alignItems    | center → normal                                                                                                                 |
| `cap.line` | Cap   | minHeight     | 0px → auto                                                                                                                      |
| `cap.line` | Cap   | padding       | 7px 12px → 0px                                                                                                                  |
| `cap.line` | Cap   | borderWidth   | 1px 0px 0px 0px → 0px                                                                                                           |
| `cap.line` | Cap   | gap           | 13px → normal                                                                                                                   |
| `cap.card` | Cap   | display       | block → flex                                                                                                                    |
| `cap.card` | Cap   | flexDirection | row → column                                                                                                                    |
| `cap.card` | Cap   | boxShadow     | rgba(255, 255, 255, 0.035) 0px 1px 0px 0px inset, rgba(0, 0, 0, 0.85) 0px 20px 44px -30px → rgba(0, 0, 0, 0.4) 0px 8px 28px 0px |
| `cap.card` | Cap   | overflowX     | hidden → visible                                                                                                                |
| `cap.card` | Cap   | overflowY     | hidden → visible                                                                                                                |
| `cap.card` | Cap   | padding       | 0px → 24px                                                                                                                      |
| `cap.card` | Cap   | gap           | normal → 16px                                                                                                                   |

## 🟡 LOW (9)

| Element     | Group | Property      | Design → Live                                     |
| ----------- | ----- | ------------- | ------------------------------------------------- |
| `cap.line`  | Cap   | lineHeight    | 15px → normal                                     |
| `cap.line`  | Cap   | letterSpacing | normal → 0.3px                                    |
| `cap.line`  | Cap   | height        | 30px → 15px                                       |
| `cap.line`  | Cap   | borderStyle   | solid none none none → none                       |
| `cap.badge` | Cap   | lineHeight    | 15.75px → normal                                  |
| `cap.badge` | Cap   | height        | 21.75px → 20px                                    |
| `cap.card`  | Cap   | lineHeight    | 19.5px → normal                                   |
| `cap.card`  | Cap   | height        | 500.75px → 712px                                  |
| `cap.card`  | Cap   | tag           | <div> → <section> (semantic/default-style change) |

## ⚪ INFO (6)

| Element     | Group | Property | Design → Live                                                                                                                     |
| ----------- | ----- | -------- | --------------------------------------------------------------------------------------------------------------------------------- |
| `cap.line`  | Cap   | text     | “Showing 8 of 1284 rows6 of 6 columnsrender cap 200” → “Showing 200 of 260 cards.”                                                |
| `cap.line`  | Cap   | width    | expected: intrinsic width follows dynamic runtime copy — 1000px → 770px                                                           |
| `cap.badge` | Cap   | text     | “8 of 1284 rows” → “260 cards”                                                                                                    |
| `cap.badge` | Cap   | width    | expected: intrinsic width follows dynamic runtime copy — 106.219px → 74.7031px                                                    |
| `cap.card`  | Cap   | text     | “Table/curated specLaunch Week payout batch8 transfers · ops …” → “BoardCycle 14 — Launch Week260 cardsTriage58Payout CSV drops…” |
| `cap.card`  | Cap   | width    | expected: intrinsic width follows dynamic runtime copy — 1002px → 820px                                                           |
