# Design-parity report — `detected`

Design baseline (source of truth) vs live app, by computed style.

- Design: `surfaces/first-run/out/design-ollama-detected.json`
- Live: `surfaces/first-run/out/live-ollama-detected.json`

**Summary:** 🔴 HIGH 0 · 🟠 MEDIUM 0 · 🟡 LOW 8 · ⚪ INFO 5

## 🟡 LOW (8)

| Element            | Group        | Property   | Design → Live                                |
| ------------------ | ------------ | ---------- | -------------------------------------------- |
| `detected.dep`     | ① → detected | lineHeight | 19.5px → normal                              |
| `detected.dep`     | ① → detected | width      | 270px → 281px                                |
| `detected.dep`     | ① → detected | height     | 28.25px → 24px                               |
| `detected.ok`      | ① → detected | lineHeight | 17.25px → normal                             |
| `detected.ok`      | ① → detected | width      | 270px → 281px                                |
| `detected.ok`      | ① → detected | height     | 17.25px → 13px                               |
| `detected.ok`      | ① → detected | tag        | <span> → <p> (semantic/default-style change) |
| `detected.ok.icon` | ① → detected | lineHeight | 17.25px → normal                             |

## ⚪ INFO (5)

| Element            | Group        | Property    | Design → Live                                                                                                                                                                                                                                                                                                                                                                                                      |
| ------------------ | ------------ | ----------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `detected.dep`     | ① → detected | fontSize    | expected: HARNESS ARTIFACT, no visual effect: the mock's base font-size is 13px and the live fixture inherits 13.6px (0.85rem). Every text node in this card sets its own size (title 13, meta 9.5, body 11.5, note 9, dling/ok 11.5, watch 10.5), so this is the inherited base only and renders nothing. — 13px → 13.6px (+0.6px)                                                                                |
| `detected.ok`      | ① → detected | color       | expected: the mock hard-codes the literal #6aa88f (jade); no design-system token equals it exactly, so the live rule uses --color-success — the same substitution `.ln__check` already makes. Recorded in onboarding.css's PRD-P8 header block. Every OTHER property on this line (size, weight, gap) is scored normally and is NOT covered by this declaration. — rgb(106, 168, 143) → rgb(87, 199, 133) (--jade) |
| `detected.ok`      | ① → detected | borderColor | expected: Same single decision as this element's `color` — the icon and border inherit `currentColor`. The mock hard-codes #6aa88f (a muted jade) and no design-system token equals it; the live rule uses --color-success, the substitution `.ln__check` already makes. — rgb(106, 168, 143) → rgb(87, 199, 133) (--jade)                                                                                         |
| `detected.ok.icon` | ① → detected | color       | expected: Same single decision as this element's `color` — the icon and border inherit `currentColor`. The mock hard-codes #6aa88f (a muted jade) and no design-system token equals it; the live rule uses --color-success, the substitution `.ln__check` already makes. — rgb(106, 168, 143) → rgb(87, 199, 133) (--jade)                                                                                         |
| `detected.ok.icon` | ① → detected | borderColor | expected: Same single decision as this element's `color` — the icon and border inherit `currentColor`. The mock hard-codes #6aa88f (a muted jade) and no design-system token equals it; the live rule uses --color-success, the substitution `.ln__check` already makes. — rgb(106, 168, 143) → rgb(87, 199, 133) (--jade)                                                                                         |
