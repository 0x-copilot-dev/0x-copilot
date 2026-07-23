# Design-parity report — `installed`

Design baseline (source of truth) vs live app, by computed style.

- Design: `surfaces/first-run/out/design-ollama-installed.json`
- Live: `surfaces/first-run/out/live-ollama-installed.json`

**Summary:** 🔴 HIGH 0 · 🟠 MEDIUM 0 · 🟡 LOW 11 · ⚪ INFO 5

## 🟡 LOW (11)

| Element                 | Group       | Property   | Design → Live                                |
| ----------------------- | ----------- | ---------- | -------------------------------------------- |
| `installed.card.meta`   | ② installed | lineHeight | 14.25px → normal                             |
| `installed.card.meta`   | ② installed | width      | 270px → 281px                                |
| `installed.card.meta`   | ② installed | height     | 14.25px → 13px                               |
| `installed.card.meta`   | ② installed | tag        | <span> → <p> (semantic/default-style change) |
| `installed.action`      | ② installed | width      | 132.188px → 133.688px                        |
| `installed.action.icon` | ② installed | width      | 11.5px → 13px                                |
| `installed.action.icon` | ② installed | height     | 11.5px → 13px                                |
| `installed.note`        | ② installed | lineHeight | 13.5px → normal                              |
| `installed.note`        | ② installed | width      | 270px → 221.406px                            |
| `installed.note`        | ② installed | height     | 13.5px → 12px                                |
| `installed.note`        | ② installed | tag        | <span> → <p> (semantic/default-style change) |

## ⚪ INFO (5)

| Element                 | Group       | Property      | Design → Live                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                |
| ----------------------- | ----------- | ------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `installed.card.meta`   | ② installed | text          | expected: PRD-P8 D5 — see not-installed.card.meta. — “Qwen 3 4B · 5.6 GB · free forever” → “Qwen 3 4B · 4.3 GB · free forever”                                                                                                                                                                                                                                                                                                                                                                                               |
| `installed.foot`        | ② installed | extra-in-live | expected: live-only wrapper — see not-installed.foot.                                                                                                                                                                                                                                                                                                                                                                                                                                                                        |
| `installed.action`      | ② installed | color         | expected: PRE-EXISTING, verified at 5c890515 (before this branch): `.gbtn--pri` uses --color-accent-contrast, commented in onboarding.css as "design literal #0b0a0e". The mock hard-codes the literal; the FTUE tokenized it repo-wide for every primary gate button, not just these states. rgb(11,10,14) vs rgb(8,19,29) — both near-black on the accent fill. Not PRD-P8 drift; changing it would be a separate, surface-wide decision. — rgb(11, 10, 14) (#0b0a0e (literal near-black)) → rgb(8, 19, 29) (--accent-ink) |
| `installed.action.icon` | ② installed | color         | expected: PRE-EXISTING, verified at 5c890515 (before this branch): `.gbtn--pri` uses --color-accent-contrast, commented in onboarding.css as "design literal #0b0a0e". The mock hard-codes the literal; the FTUE tokenized it repo-wide for every primary gate button, not just these states. rgb(11,10,14) vs rgb(8,19,29) — both near-black on the accent fill. Not PRD-P8 drift; changing it would be a separate, surface-wide decision. — rgb(11, 10, 14) (#0b0a0e (literal near-black)) → rgb(8, 19, 29) (--accent-ink) |
| `installed.action.icon` | ② installed | borderColor   | expected: PRE-EXISTING, verified at 5c890515 (before this branch): `.gbtn--pri` uses --color-accent-contrast, commented in onboarding.css as "design literal #0b0a0e". The mock hard-codes the literal; the FTUE tokenized it repo-wide for every primary gate button, not just these states. rgb(11,10,14) vs rgb(8,19,29) — both near-black on the accent fill. Not PRD-P8 drift; changing it would be a separate, surface-wide decision. — rgb(11, 10, 14) (#0b0a0e (literal near-black)) → rgb(8, 19, 29) (--accent-ink) |
