# Design-parity report — surface-language · `board`

Design baseline (source of truth) vs live app, by computed style.

- Design: `surfaces/surface-language/out/design-board.json`
- Live: `surfaces/surface-language/out/live-board.json`

**Summary:** 🔴 HIGH 19 · 🟠 MEDIUM 30 · 🟡 LOW 46 · ⚪ INFO 20

## 🔴 HIGH (19)

| Element                | Group       | Property        | Design → Live                                                                                                                                                     |
| ---------------------- | ----------- | --------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `card`                 | Card        | backgroundColor | oklch(0.212 0.01 276) → rgb(17, 17, 20) (--panel)                                                                                                                 |
| `card`                 | Card        | borderColor     | oklch(1 0 0 / 0.115) → rgba(255, 255, 255, 0.1) (--line2)                                                                                                         |
| `card.header`          | Card        | backgroundColor | oklch(0.243 0.011 276) → rgba(0, 0, 0, 0) (transparent)                                                                                                           |
| `card.header`          | Card        | borderColor     | rgb(236, 236, 241) rgb(236, 236, 241) oklch(1 0 0 / 0.115) rgb(236, 236, 241) → rgb(236, 236, 241) rgb(236, 236, 241) rgba(255, 255, 255, 0.1) rgb(236, 236, 241) |
| `card.kicker-dot`      | Card        | backgroundColor | rgb(169, 139, 224) → oklch(0.76 0.1 288)                                                                                                                          |
| `card.subtitle`        | Card        | missing-in-live | present in design, ABSENT in live                                                                                                                                 |
| `card.badge`           | Card        | borderColor     | oklch(1 0 0 / 0.115) → rgba(255, 255, 255, 0.1) (--line2)                                                                                                         |
| `card.link`            | Card        | color           | rgb(152, 152, 159) (--mut) → rgb(95, 178, 236) (--accent/--sky)                                                                                                   |
| `lanes`                | Lanes       | backgroundColor | oklch(1 0 0 / 0.07) → rgba(255, 255, 255, 0.06) (--line)                                                                                                          |
| `lane.first`           | Lane        | backgroundColor | oklch(0.212 0.01 276) → rgb(17, 17, 20) (--panel)                                                                                                                 |
| `lane.first.header`    | Lane        | color           | rgb(100, 100, 109) (--mut2) → rgb(152, 152, 159) (--mut)                                                                                                          |
| `lane.first.header`    | Lane        | backgroundColor | oklch(0.212 0.01 276) → rgb(17, 17, 20) (--panel)                                                                                                                 |
| `lane.first.name`      | Lane        | color           | rgb(100, 100, 109) (--mut2) → rgb(152, 152, 159) (--mut)                                                                                                          |
| `lane.first.count`     | Lane        | color           | rgb(100, 100, 109) (--mut2) → rgb(152, 152, 159) (--mut)                                                                                                          |
| `lane.first.card`      | Card chrome | backgroundColor | oklch(0.243 0.011 276) → rgb(22, 22, 26) (--panel2)                                                                                                               |
| `lane.first.card`      | Card chrome | borderColor     | oklch(1 0 0 / 0.07) → rgba(255, 255, 255, 0.06) (--line)                                                                                                          |
| `lane.first.card.meta` | Card chrome | color           | rgb(100, 100, 109) (--mut2) → rgb(152, 152, 159) (--mut)                                                                                                          |
| `lane.single-card`     | Lane        | backgroundColor | oklch(0.212 0.01 276) → rgb(17, 17, 20) (--panel)                                                                                                                 |
| `lane.last`            | Lane        | backgroundColor | oklch(0.212 0.01 276) → rgb(17, 17, 20) (--panel)                                                                                                                 |

## 🟠 MEDIUM (30)

| Element                | Group       | Property       | Design → Live                                                                                                                   |
| ---------------------- | ----------- | -------------- | ------------------------------------------------------------------------------------------------------------------------------- |
| `card`                 | Card        | display        | block → flex                                                                                                                    |
| `card`                 | Card        | flexDirection  | row → column                                                                                                                    |
| `card`                 | Card        | boxShadow      | rgba(255, 255, 255, 0.035) 0px 1px 0px 0px inset, rgba(0, 0, 0, 0.85) 0px 20px 44px -30px → rgba(0, 0, 0, 0.4) 0px 8px 28px 0px |
| `card`                 | Card        | overflowX      | hidden → visible                                                                                                                |
| `card`                 | Card        | overflowY      | hidden → visible                                                                                                                |
| `card`                 | Card        | padding        | 0px → 24px                                                                                                                      |
| `card`                 | Card        | gap            | normal → 16px                                                                                                                   |
| `card.header`          | Card        | justifyContent | normal → space-between                                                                                                          |
| `card.header`          | Card        | flexWrap       | wrap → nowrap                                                                                                                   |
| `card.header`          | Card        | minHeight      | 0px → auto                                                                                                                      |
| `card.header`          | Card        | padding        | 12px 14px → 0px 0px 12px 0px                                                                                                    |
| `card.header`          | Card        | gap            | 10px / 12px → 12px                                                                                                              |
| `card.kicker`          | Card        | overflowX      | hidden → visible                                                                                                                |
| `card.kicker`          | Card        | overflowY      | hidden → visible                                                                                                                |
| `card.kicker-dot`      | Card        | display        | flex → block                                                                                                                    |
| `card.kicker-dot`      | Card        | flexDirection  | column → row                                                                                                                    |
| `card.kicker-dot`      | Card        | boxShadow      | oklch(0.69738 0.12513 299.414 / 0.16) 0px 0px 0px 3px → oklch(0.76 0.1 288 / 0.16) 0px 0px 0px 3px                              |
| `card.kicker-dot`      | Card        | minHeight      | 0px → auto                                                                                                                      |
| `card.kicker-dot`      | Card        | overflowX      | hidden → visible                                                                                                                |
| `card.kicker-dot`      | Card        | overflowY      | hidden → visible                                                                                                                |
| `card.title`           | Card        | fontSize       | 15px → 14px (-1.0px)                                                                                                            |
| `card.title`           | Card        | overflowX      | hidden → visible                                                                                                                |
| `card.title`           | Card        | overflowY      | hidden → visible                                                                                                                |
| `card.link`            | Card        | display        | flex → inline                                                                                                                   |
| `card.link`            | Card        | alignItems     | center → normal                                                                                                                 |
| `card.link`            | Card        | minHeight      | auto → 0px                                                                                                                      |
| `card.link`            | Card        | gap            | 5px → normal                                                                                                                    |
| `lane.first.name`      | Lane        | overflowX      | visible → hidden                                                                                                                |
| `lane.first.name`      | Lane        | overflowY      | visible → hidden                                                                                                                |
| `lane.first.card.meta` | Card chrome | flexWrap       | nowrap → wrap                                                                                                                   |

## 🟡 LOW (46)

| Element                 | Group       | Property   | Design → Live                                     |
| ----------------------- | ----------- | ---------- | ------------------------------------------------- |
| `card`                  | Card        | lineHeight | 19.5px → normal                                   |
| `card`                  | Card        | height     | 317.75px → 712px                                  |
| `card`                  | Card        | tag        | <div> → <section> (semantic/default-style change) |
| `card.header`           | Card        | lineHeight | 19.5px → normal                                   |
| `card.header`           | Card        | height     | 85.75px → 47px                                    |
| `card.header`           | Card        | tag        | <div> → <header> (semantic/default-style change)  |
| `card.kicker`           | Card        | lineHeight | 14.25px → normal                                  |
| `card.kicker`           | Card        | height     | 14.25px → 13px                                    |
| `card.kicker`           | Card        | textWrap   | nowrap → wrap                                     |
| `card.kicker`           | Card        | tag        | <div> → <span> (semantic/default-style change)    |
| `card.kicker-dot`       | Card        | lineHeight | 14.25px → normal                                  |
| `card.kicker-dot`       | Card        | textWrap   | nowrap → wrap                                     |
| `card.title`            | Card        | lineHeight | 22.5px → normal                                   |
| `card.title`            | Card        | width      | 743.547px → 163.781px                             |
| `card.title`            | Card        | height     | 22.5px → 17px                                     |
| `card.title`            | Card        | textWrap   | nowrap → wrap                                     |
| `card.title`            | Card        | tag        | <div> → <span> (semantic/default-style change)    |
| `card.badge`            | Card        | lineHeight | 15.75px → normal                                  |
| `card.badge`            | Card        | height     | 21.75px → 20px                                    |
| `card.link`             | Card        | lineHeight | 15.75px → normal                                  |
| `card.link`             | Card        | width      | 142.031px → auto                                  |
| `card.link`             | Card        | height     | 15.75px → auto                                    |
| `card.link`             | Card        | textWrap   | nowrap → wrap                                     |
| `card.link`             | Card        | tag        | <span> → <a> (semantic/default-style change)      |
| `lanes`                 | Lanes       | lineHeight | 19.5px → normal                                   |
| `lane.first`            | Lane        | lineHeight | 19.5px → normal                                   |
| `lane.first.header`     | Lane        | lineHeight | 14.25px → normal                                  |
| `lane.first.header`     | Lane        | width      | 249.25px → 196px                                  |
| `lane.first.header`     | Lane        | height     | 31.25px → 30px                                    |
| `lane.first.name`       | Lane        | lineHeight | 14.25px → normal                                  |
| `lane.first.name`       | Lane        | width      | 40.4844px → 39.9062px                             |
| `lane.first.name`       | Lane        | height     | 14.25px → 13px                                    |
| `lane.first.name`       | Lane        | textWrap   | wrap → nowrap                                     |
| `lane.first.count`      | Lane        | lineHeight | 14.25px → normal                                  |
| `lane.first.count`      | Lane        | width      | 6.75px → 6.65625px                                |
| `lane.first.count`      | Lane        | height     | 14.25px → 13px                                    |
| `lane.first.card`       | Card chrome | lineHeight | 19.5px → normal                                   |
| `lane.first.card`       | Card chrome | height     | 55.0469px → 70.5938px                             |
| `lane.first.card.title` | Card chrome | width      | 209.25px → 156px                                  |
| `lane.first.card.title` | Card chrome | height     | 16.7969px → 33.5938px                             |
| `lane.first.card.title` | Card chrome | tag        | <span> → <div> (semantic/default-style change)    |
| `lane.first.card.meta`  | Card chrome | lineHeight | 14.25px → normal                                  |
| `lane.first.card.meta`  | Card chrome | height     | 14.25px → 13px                                    |
| `lane.first.card.meta`  | Card chrome | tag        | <span> → <div> (semantic/default-style change)    |
| `lane.single-card`      | Lane        | lineHeight | 19.5px → normal                                   |
| `lane.last`             | Lane        | lineHeight | 19.5px → normal                                   |

## ⚪ INFO (20)

| Element                | Group       | Property | Design → Live                                                                                                                     |
| ---------------------- | ----------- | -------- | --------------------------------------------------------------------------------------------------------------------------------- |
| `card`                 | Card        | text     | “Board/generated · cachedCycle 14 — Launch WeekPlatform · 7 i…” → “BoardCycle 14 — Launch Week7 cardsTriage2Payout CSV drops th…” |
| `card`                 | Card        | width    | expected: intrinsic width follows dynamic runtime copy — 1002px → 820px                                                           |
| `card.header`          | Card        | text     | “Board/generated · cachedCycle 14 — Launch WeekPlatform · 7 i…” → “BoardCycle 14 — Launch Week7 cards”                            |
| `card.header`          | Card        | width    | expected: intrinsic width follows dynamic runtime copy — 1000px → 770px                                                           |
| `card.kicker`          | Card        | text     | “Board/generated · cached” → “Board”                                                                                              |
| `card.kicker`          | Card        | width    | expected: intrinsic width follows dynamic runtime copy — 743.547px → 163.781px                                                    |
| `card.badge`           | Card        | text     | “7 issues” → “7 cards”                                                                                                            |
| `card.badge`           | Card        | width    | expected: intrinsic width follows dynamic runtime copy — 68.4219px → 62.1094px                                                    |
| `lanes`                | Lanes       | text     | “Triage2Payout CSV drops the memo columnLW-208 · nova.ethSafe…” → “Triage2Payout CSV drops the memo columnMetaLW-208 · nova.eth…” |
| `lanes`                | Lanes       | width    | expected: intrinsic width follows dynamic runtime copy — 1000px → 770px                                                           |
| `lane.first`           | Lane        | text     | “Triage2Payout CSV drops the memo columnLW-208 · nova.ethSafe…” → “Triage2Payout CSV drops the memo columnMetaLW-208 · nova.eth…” |
| `lane.first`           | Lane        | width    | expected: intrinsic width follows dynamic runtime copy — 249.25px → 196px                                                         |
| `lane.first.card`      | Card chrome | text     | “Payout CSV drops the memo columnLW-208 · nova.eth” → “Payout CSV drops the memo columnMetaLW-208 · nova.eth”                     |
| `lane.first.card`      | Card chrome | width    | expected: intrinsic width follows dynamic runtime copy — 229.25px → 176px                                                         |
| `lane.first.card.meta` | Card chrome | text     | “LW-208 · nova.eth” → “MetaLW-208 · nova.eth”                                                                                     |
| `lane.first.card.meta` | Card chrome | width    | expected: intrinsic width follows dynamic runtime copy — 209.25px → 156px                                                         |
| `lane.single-card`     | Lane        | text     | “In review1Approval gate copy passLW-177 · rin.eth” → “In review1Approval gate copy passMetaLW-177 · rin.eth”                     |
| `lane.single-card`     | Lane        | width    | expected: intrinsic width follows dynamic runtime copy — 249.25px → 196px                                                         |
| `lane.last`            | Lane        | text     | “Done2Event log exportLW-160 · kira.ethCycle 14 retro notesLW…” → “Done2Event log exportMetaLW-160 · kira.ethCycle 14 retro not…” |
| `lane.last`            | Lane        | width    | expected: intrinsic width follows dynamic runtime copy — 249.25px → 196px                                                         |
