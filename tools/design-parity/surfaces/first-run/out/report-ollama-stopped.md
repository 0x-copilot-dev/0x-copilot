# Design-parity report — `stopped`

Design baseline (source of truth) vs live app, by computed style.

- Design: `surfaces/first-run/out/design-ollama-stopped.json`
- Live: `surfaces/first-run/out/live-ollama-stopped.json`

**Summary:** 🔴 HIGH 0 · 🟠 MEDIUM 0 · 🟡 LOW 18 · ⚪ INFO 6

## 🟡 LOW (18)

| Element             | Group             | Property   | Design → Live                                |
| ------------------- | ----------------- | ---------- | -------------------------------------------- |
| `stopped.card.meta` | ④ runtime stopped | lineHeight | 14.25px → normal                             |
| `stopped.card.meta` | ④ runtime stopped | width      | 270px → 281px                                |
| `stopped.card.meta` | ④ runtime stopped | height     | 14.25px → 13px                               |
| `stopped.card.meta` | ④ runtime stopped | tag        | <span> → <p> (semantic/default-style change) |
| `stopped.dep`       | ④ runtime stopped | lineHeight | 19.5px → normal                              |
| `stopped.dep`       | ④ runtime stopped | width      | 270px → 281px                                |
| `stopped.dep`       | ④ runtime stopped | height     | 77.0938px → 84.0938px                        |
| `stopped.dling`     | ④ runtime stopped | width      | 270px → 281px                                |
| `stopped.dling`     | ④ runtime stopped | tag        | <span> → <p> (semantic/default-style change) |
| `stopped.acts`      | ④ runtime stopped | lineHeight | 19.5px → normal                              |
| `stopped.acts`      | ④ runtime stopped | width      | 270px → 281px                                |
| `stopped.acts`      | ④ runtime stopped | height     | 42px → 29px                                  |
| `stopped.action`    | ④ runtime stopped | width      | 111.875px → 112.156px                        |
| `stopped.action`    | ④ runtime stopped | height     | 42px → 29px                                  |
| `stopped.watch`     | ④ runtime stopped | lineHeight | 14.7px → normal                              |
| `stopped.watch`     | ④ runtime stopped | width      | 148.125px → 281px                            |
| `stopped.watch`     | ④ runtime stopped | height     | 29.375px → 12px                              |
| `stopped.watch`     | ④ runtime stopped | tag        | <span> → <p> (semantic/default-style change) |

## ⚪ INFO (6)

| Element             | Group             | Property | Design → Live                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                |
| ------------------- | ----------------- | -------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `stopped.card.meta` | ④ runtime stopped | text     | expected: PRD-P8 D5 — see not-installed.card.meta. — “Qwen 3 4B · 5.6 GB · free forever” → “Qwen 3 4B · 4.3 GB · free forever”                                                                                                                                                                                                                                                                                                                                                                                               |
| `stopped.dep`       | ④ runtime stopped | fontSize | expected: HARNESS ARTIFACT, no visual effect: the mock's base font-size is 13px and the live fixture inherits 13.6px (0.85rem). Every text node in this card sets its own size (title 13, meta 9.5, body 11.5, note 9, dling/ok 11.5, watch 10.5), so this is the inherited base only and renders nothing. — 13px → 13.6px (+0.6px)                                                                                                                                                                                          |
| `stopped.acts`      | ④ runtime stopped | text     | “Restart Ollamadownload resumes on its own” → “Restart Ollama”                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| `stopped.acts`      | ④ runtime stopped | fontSize | expected: HARNESS ARTIFACT, no visual effect: the mock's base font-size is 13px and the live fixture inherits 13.6px (0.85rem). Every text node in this card sets its own size (title 13, meta 9.5, body 11.5, note 9, dling/ok 11.5, watch 10.5), so this is the inherited base only and renders nothing. — 13px → 13.6px (+0.6px)                                                                                                                                                                                          |
| `stopped.acts`      | ④ runtime stopped | flexWrap | expected: live-only, deliberate: the shipped card is narrower than the mock's catalog column, so the action row wraps instead of overflowing. — nowrap → wrap                                                                                                                                                                                                                                                                                                                                                                |
| `stopped.action`    | ④ runtime stopped | color    | expected: PRE-EXISTING, verified at 5c890515 (before this branch): `.gbtn--pri` uses --color-accent-contrast, commented in onboarding.css as "design literal #0b0a0e". The mock hard-codes the literal; the FTUE tokenized it repo-wide for every primary gate button, not just these states. rgb(11,10,14) vs rgb(8,19,29) — both near-black on the accent fill. Not PRD-P8 drift; changing it would be a separate, surface-wide decision. — rgb(11, 10, 14) (#0b0a0e (literal near-black)) → rgb(8, 19, 29) (--accent-ink) |
