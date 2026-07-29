# Design-parity report — surface-language · `board-changed`

Design baseline (source of truth) vs live app, by computed style.

- Design: `surfaces/surface-language/out/design-board-changed.json`
- Live: `surfaces/surface-language/out/live-board-changed.json`

**Summary:** 🔴 HIGH 12 · 🟠 MEDIUM 8 · 🟡 LOW 17 · ⚪ INFO 11

## 🔴 HIGH (12)

| Element                   | Group              | Property        | Design → Live                                            |
| ------------------------- | ------------------ | --------------- | -------------------------------------------------------- |
| `card.kicker-dot`         | Identity (control) | fontFamily      | typeface class changed (mono → sans)                     |
| `card.kicker-dot`         | Identity (control) | color           | rgb(100, 100, 109) (--mut2) → rgb(152, 152, 159) (--mut) |
| `card.kicker-dot`         | Identity (control) | backgroundColor | rgb(169, 139, 224) → oklch(0.76 0.1 288)                 |
| `lanes`                   | Lanes              | backgroundColor | oklch(1 0 0 / 0.07) → rgba(255, 255, 255, 0.1) (--line2) |
| `changed.lane`            | Changed lane       | backgroundColor | oklch(0.212 0.01 276) → rgb(17, 17, 20) (--panel)        |
| `changed.lane.header`     | Changed lane       | color           | rgb(100, 100, 109) (--mut2) → rgb(152, 152, 159) (--mut) |
| `changed.lane.header`     | Changed lane       | backgroundColor | oklch(0.212 0.01 276) → rgb(17, 17, 20) (--panel)        |
| `changed.card`            | Changed card       | backgroundColor | oklch(0.243 0.011 276) → rgb(29, 29, 35) (--panel3)      |
| `changed.card.meta`       | Changed card       | color           | rgb(100, 100, 109) (--mut2) → rgb(152, 152, 159) (--mut) |
| `changed.transition-chip` | Changed card       | missing-in-live | present in design, ABSENT in live                        |
| `unchanged.sibling`       | Changed card       | backgroundColor | oklch(0.243 0.011 276) → rgb(29, 29, 35) (--panel3)      |
| `unchanged.sibling`       | Changed card       | borderColor     | oklch(1 0 0 / 0.07) → rgba(255, 255, 255, 0.1) (--line2) |

## 🟠 MEDIUM (8)

| Element             | Group              | Property      | Design → Live                                                                                      |
| ------------------- | ------------------ | ------------- | -------------------------------------------------------------------------------------------------- |
| `card.kicker-dot`   | Identity (control) | fontSize      | 9.5px → 11px (+1.5px)                                                                              |
| `card.kicker-dot`   | Identity (control) | display       | flex → block                                                                                       |
| `card.kicker-dot`   | Identity (control) | flexDirection | column → row                                                                                       |
| `card.kicker-dot`   | Identity (control) | boxShadow     | oklch(0.69738 0.12513 299.414 / 0.16) 0px 0px 0px 3px → oklch(0.76 0.1 288 / 0.16) 0px 0px 0px 3px |
| `card.kicker-dot`   | Identity (control) | minHeight     | 0px → auto                                                                                         |
| `card.kicker-dot`   | Identity (control) | overflowX     | hidden → visible                                                                                   |
| `card.kicker-dot`   | Identity (control) | overflowY     | hidden → visible                                                                                   |
| `changed.card.meta` | Changed card       | flexWrap      | nowrap → wrap                                                                                      |

## 🟡 LOW (17)

| Element               | Group              | Property      | Design → Live                                  |
| --------------------- | ------------------ | ------------- | ---------------------------------------------- |
| `card.kicker-dot`     | Identity (control) | lineHeight    | 14.25px → normal                               |
| `card.kicker-dot`     | Identity (control) | letterSpacing | 1.14px → 0.6px                                 |
| `card.kicker-dot`     | Identity (control) | textWrap      | nowrap → wrap                                  |
| `lanes`               | Lanes              | lineHeight    | 19.5px → normal                                |
| `changed.lane`        | Changed lane       | lineHeight    | 19.5px → normal                                |
| `changed.lane.header` | Changed lane       | lineHeight    | 14.25px → normal                               |
| `changed.lane.header` | Changed lane       | width         | 249.25px → 196px                               |
| `changed.lane.header` | Changed lane       | height        | 31.25px → 30px                                 |
| `changed.card`        | Changed card       | lineHeight    | 19.5px → normal                                |
| `changed.card`        | Changed card       | height        | 73.8438px → 70.5938px                          |
| `changed.card.title`  | Changed card       | width         | 209.25px → 156px                               |
| `changed.card.title`  | Changed card       | tag           | <span> → <div> (semantic/default-style change) |
| `changed.card.meta`   | Changed card       | lineHeight    | 14.25px → normal                               |
| `changed.card.meta`   | Changed card       | height        | 16.25px → 13px                                 |
| `changed.card.meta`   | Changed card       | tag           | <span> → <div> (semantic/default-style change) |
| `unchanged.sibling`   | Changed card       | lineHeight    | 19.5px → normal                                |
| `unchanged.sibling`   | Changed card       | height        | 55.0469px → 53.7969px                          |

## ⚪ INFO (11)

| Element             | Group        | Property      | Design → Live                                                                                                                     |
| ------------------- | ------------ | ------------- | --------------------------------------------------------------------------------------------------------------------------------- |
| `lanes`             | Lanes        | text          | “Triage2Payout CSV drops the memo columnLW-208 · nova.ethSafe…” → “Triage2Payout CSV drops the memo columnMetaLW-208 · nova.eth…” |
| `lanes`             | Lanes        | width         | expected: intrinsic width follows dynamic runtime copy — 1000px → 774px                                                           |
| `changed.lane`      | Changed lane | text          | “In progress2Stage transfers from the contributor sheetLW-142…” → “In progress2ChangedStage transfers from the contributor shee…” |
| `changed.lane`      | Changed lane | width         | expected: intrinsic width follows dynamic runtime copy — 249.25px → 196px                                                         |
| `changed.card`      | Changed card | text          | “Stage transfers from the contributor sheetLW-142 · dev.tomo→…” → “ChangedStage transfers from the contributor sheetMetaLW-142 …” |
| `changed.card`      | Changed card | width         | expected: intrinsic width follows dynamic runtime copy — 229.25px → 176px                                                         |
| `changed.card.meta` | Changed card | text          | “LW-142 · dev.tomo” → “MetaLW-142 · dev.tomo”                                                                                     |
| `changed.card.meta` | Changed card | width         | expected: intrinsic width follows dynamic runtime copy — 209.25px → 156px                                                         |
| `unchanged.sibling` | Changed card | text          | “Recap thread draftLW-190 · 0xlune” → “Recap thread draftMetaLW-190 · 0xlune”                                                     |
| `unchanged.sibling` | Changed card | width         | expected: intrinsic width follows dynamic runtime copy — 229.25px → 176px                                                         |
| `changed.sr-marker` | Changed card | extra-in-live | present in live, not in design map                                                                                                |
