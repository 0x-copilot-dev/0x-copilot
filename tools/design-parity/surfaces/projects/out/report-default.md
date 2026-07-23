# Design-parity report — `default`

Design baseline (source of truth) vs live app, by computed style.

- Design: `surfaces/projects/out/design-default.json`
- Live: `surfaces/projects/out/live-default.json`

**Summary:** 🔴 HIGH 1 · 🟠 MEDIUM 15 · 🟡 LOW 23 · ⚪ INFO 9

## 🔴 HIGH (1)

| Element             | Group        | Property | Design → Live                                            |
| ------------------- | ------------ | -------- | -------------------------------------------------------- |
| `default.card.desc` | Project card | color    | rgb(100, 100, 109) (--mut2) → rgb(152, 152, 159) (--mut) |

## 🟠 MEDIUM (15)

| Element                  | Group        | Property      | Design → Live                                              |
| ------------------------ | ------------ | ------------- | ---------------------------------------------------------- |
| `default.page.container` | Layout       | display       | block → flex                                               |
| `default.page.container` | Layout       | flexDirection | row → column                                               |
| `default.page.container` | Layout       | flexGrow      | flex-grow 1 → 0 (affects vertical fill / button placement) |
| `default.page.container` | Layout       | gap           | normal → 16px                                              |
| `default.page.lead`      | Layout       | fontSize      | 12px → 12.48px (+0.5px)                                    |
| `default.page.lead`      | Layout       | margin        | -2px 0px 18px 0px → 0px                                    |
| `default.card`           | Project card | display       | block → flex                                               |
| `default.card`           | Project card | flexDirection | row → column                                               |
| `default.card`           | Project card | padding       | 13px → 12px                                                |
| `default.card`           | Project card | gap           | normal → 10px                                              |
| `default.card.icon`      | Project card | borderWidth   | 0px → 1px                                                  |
| `default.card.name`      | Project card | fontSize      | 14px → 13px (-1.0px)                                       |
| `default.card.name`      | Project card | flexGrow      | flex-grow 0 → 1 (affects vertical fill / button placement) |
| `default.card.desc`      | Project card | margin        | 10px 0px 0px 0px → 0px                                     |
| `default.card.meta`      | Project card | margin        | 10px 0px 0px 0px → 0px                                     |

## 🟡 LOW (23)

| Element                  | Group        | Property    | Design → Live                                                                                                                                                                            |
| ------------------------ | ------------ | ----------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `default.page.container` | Layout       | lineHeight  | 19.5px → normal                                                                                                                                                                          |
| `default.page.container` | Layout       | height      | 754px → 254.219px                                                                                                                                                                        |
| `default.page.lead`      | Layout       | lineHeight  | 19.2px → 21.216px                                                                                                                                                                        |
| `default.page.lead`      | Layout       | width       | 544.219px → 565.984px                                                                                                                                                                    |
| `default.page.lead`      | Layout       | height      | 38.375px → 21.2188px                                                                                                                                                                     |
| `default.grid`           | Layout       | lineHeight  | 19.5px → normal                                                                                                                                                                          |
| `default.grid`           | Layout       | height      | 113px → 105px                                                                                                                                                                            |
| `default.card`           | Project card | lineHeight  | 19.5px → normal                                                                                                                                                                          |
| `default.card`           | Project card | transition  | all → background-color 0.12s cubic-bezier(0.2, 0, 0, 1), border-color 0.12s cubic-bezier(0.2, 0, 0, 1), color 0.12s cubic-bezier(0.2, 0, 0, 1), opacity 0.12s cubic-bezier(0.2, 0, 0, 1) |
| `default.card`           | Project card | height      | 113px → 105px                                                                                                                                                                            |
| `default.card.icon`      | Project card | lineHeight  | 19.5px → normal                                                                                                                                                                          |
| `default.card.icon`      | Project card | borderStyle | none → solid                                                                                                                                                                             |
| `default.card.name`      | Project card | lineHeight  | 21px → normal                                                                                                                                                                            |
| `default.card.name`      | Project card | width       | 90.1094px → 159.516px                                                                                                                                                                    |
| `default.card.name`      | Project card | height      | 21px → 16px                                                                                                                                                                              |
| `default.card.name`      | Project card | tag         | <div> → <span> (semantic/default-style change)                                                                                                                                           |
| `default.card.desc`      | Project card | lineHeight  | 16.5px → normal                                                                                                                                                                          |
| `default.card.desc`      | Project card | width       | 269.328px → 271.328px                                                                                                                                                                    |
| `default.card.desc`      | Project card | height      | 16.5px → 13px                                                                                                                                                                            |
| `default.card.meta`      | Project card | lineHeight  | 16.5px → normal                                                                                                                                                                          |
| `default.card.meta`      | Project card | width       | 269.328px → 120.969px                                                                                                                                                                    |
| `default.card.meta`      | Project card | height      | 16.5px → 14px                                                                                                                                                                            |
| `default.card.meta`      | Project card | tag         | <div> → <span> (semantic/default-style change)                                                                                                                                           |

## ⚪ INFO (9)

| Element                  | Group            | Property        | Design → Live                                                                                                                                                                                                                                  |
| ------------------------ | ---------------- | --------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `default.grid`           | Layout           | text            | “LLaunch WeekGTM for the v2 launch3 chats · 12 filesTTreasury…” → “LLaunch WeekactiveGTM for the v2 launch3 chats · 12 files☆Ar…”                                                                                                              |
| `default.card`           | Project card     | text            | “LLaunch WeekGTM for the v2 launch3 chats · 12 files” → “LLaunch WeekactiveGTM for the v2 launch3 chats · 12 files”                                                                                                                            |
| `default.card.icon`      | Project card     | color           | expected: Per-project hue is intentional (D3): live persists color_hue + ships a hue picker; the mock's !important tile neutralisation is a leftover, not intent. Recorded divergence. — rgb(212, 212, 219) (--tx2) → rgb(177, 215, 241)       |
| `default.card.icon`      | Project card     | backgroundColor | expected: Per-project hue is intentional (D3): live persists color_hue + ships a hue picker; the mock's !important tile neutralisation is a leftover, not intent. Recorded divergence. — rgb(29, 29, 35) (--panel3) → rgba(29, 79, 114, 0.45)  |
| `default.card.icon`      | Project card     | borderColor     | expected: Per-project hue is intentional (D3): live persists color_hue + ships a hue picker; the mock's !important tile neutralisation is a leftover, not intent. Recorded divergence. — rgb(212, 212, 219) (--tx2) → rgba(51, 140, 204, 0.55) |
| `default.x.card.actions` | Live-only chrome | extra-in-live   | present in live, not in design map                                                                                                                                                                                                             |
| `detail.x.filtertabs`    | Live-only chrome | extra-in-live   | present in live, not in design map                                                                                                                                                                                                             |
| `default.x.filtertabs`   | Live-only chrome | extra-in-live   | expected: Filter tabs are deliberate live chrome — the mock has no filter tabs (D4).                                                                                                                                                           |
| `default.x.create`       | Live-only chrome | extra-in-live   | expected: New-project control is deliberate live chrome — the mock ships no create affordance (D4).                                                                                                                                            |
