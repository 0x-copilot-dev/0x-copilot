# Design-parity report — surface-language · `no-spec`

Design baseline (source of truth) vs live app, by computed style.

- Design: `surfaces/surface-language/out/design-no-spec.json`
- Live: `surfaces/surface-language/out/live-no-spec.json`

**Summary:** 🔴 HIGH 19 · 🟠 MEDIUM 28 · 🟡 LOW 45 · ⚪ INFO 12

## 🔴 HIGH (19)

| Element              | Group            | Property        | Design → Live                                                                                                                                                     |
| -------------------- | ---------------- | --------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `card`               | Card             | backgroundColor | oklch(0.212 0.01 276) → rgb(17, 17, 20) (--panel)                                                                                                                 |
| `card`               | Card             | borderColor     | oklch(1 0 0 / 0.115) → rgba(255, 255, 255, 0.1) (--line2)                                                                                                         |
| `card.header`        | Card             | backgroundColor | oklch(0.243 0.011 276) → rgba(0, 0, 0, 0) (transparent)                                                                                                           |
| `card.header`        | Card             | borderColor     | rgb(236, 236, 241) rgb(236, 236, 241) oklch(1 0 0 / 0.115) rgb(236, 236, 241) → rgb(236, 236, 241) rgb(236, 236, 241) rgba(255, 255, 255, 0.1) rgb(236, 236, 241) |
| `card.kicker`        | Card             | fontFamily      | typeface class changed (mono → sans)                                                                                                                              |
| `card.kicker`        | Card             | color           | rgb(100, 100, 109) (--mut2) → rgb(152, 152, 159) (--mut)                                                                                                          |
| `card.kicker-dot`    | Card             | fontFamily      | typeface class changed (mono → sans)                                                                                                                              |
| `card.kicker-dot`    | Card             | color           | rgb(100, 100, 109) (--mut2) → rgb(152, 152, 159) (--mut)                                                                                                          |
| `card.title`         | Card             | fontSize        | 15px → 18px (+3.0px)                                                                                                                                              |
| `card.subtitle`      | Card             | missing-in-live | present in design, ABSENT in live                                                                                                                                 |
| `card.chip`          | Card             | missing-in-live | present in design, ABSENT in live                                                                                                                                 |
| `nospec.note`        | The honest note  | backgroundColor | oklch(0.188 0.009 276) → rgb(13, 13, 16)                                                                                                                          |
| `nospec.note`        | The honest note  | borderColor     | rgb(212, 212, 219) rgb(212, 212, 219) oklch(1 0 0 / 0.07) rgb(212, 212, 219) → rgba(255, 255, 255, 0.1) (--line2)                                                 |
| `row.first`          | Field rows       | borderColor     | rgb(236, 236, 241) rgb(236, 236, 241) oklch(1 0 0 / 0.07) rgb(236, 236, 241) → rgb(236, 236, 241) rgb(236, 236, 241) rgba(255, 255, 255, 0.1) rgb(236, 236, 241)  |
| `row.first.value`    | Field rows       | fontFamily      | typeface class changed (sans → mono)                                                                                                                              |
| `row.numeric.value`  | Field rows       | fontFamily      | typeface class changed (mono → sans)                                                                                                                              |
| `nospec.footer`      | Read-only footer | backgroundColor | oklch(0.188 0.009 276) → rgb(13, 13, 16)                                                                                                                          |
| `nospec.footer`      | Read-only footer | borderColor     | oklch(1 0 0 / 0.115) rgb(236, 236, 241) rgb(236, 236, 241) rgb(236, 236, 241) → rgba(255, 255, 255, 0.1) (--line2)                                                |
| `nospec.footer.copy` | Read-only footer | missing-in-live | present in design, ABSENT in live                                                                                                                                 |

## 🟠 MEDIUM (28)

| Element             | Group            | Property            | Design → Live                                                                                                                   |
| ------------------- | ---------------- | ------------------- | ------------------------------------------------------------------------------------------------------------------------------- |
| `card`              | Card             | display             | block → flex                                                                                                                    |
| `card`              | Card             | flexDirection       | row → column                                                                                                                    |
| `card`              | Card             | boxShadow           | rgba(255, 255, 255, 0.035) 0px 1px 0px 0px inset, rgba(0, 0, 0, 0.85) 0px 20px 44px -30px → rgba(0, 0, 0, 0.4) 0px 8px 28px 0px |
| `card`              | Card             | overflowY           | hidden → visible                                                                                                                |
| `card`              | Card             | padding             | 0px → 22px                                                                                                                      |
| `card`              | Card             | borderRadius        | 12px → 14px                                                                                                                     |
| `card`              | Card             | gap                 | normal → 16px                                                                                                                   |
| `card.header`       | Card             | justifyContent      | normal → space-between                                                                                                          |
| `card.header`       | Card             | flexWrap            | wrap → nowrap                                                                                                                   |
| `card.header`       | Card             | padding             | 12px 14px → 0px 0px 12px 0px                                                                                                    |
| `card.header`       | Card             | gap                 | 10px / 12px → 12px                                                                                                              |
| `card.kicker`       | Card             | fontSize            | 9.5px → 11px (+1.5px)                                                                                                           |
| `card.kicker`       | Card             | overflowY           | hidden → visible                                                                                                                |
| `card.kicker-dot`   | Card             | fontSize            | 9.5px → 11px (+1.5px)                                                                                                           |
| `card.kicker-dot`   | Card             | display             | flex → block                                                                                                                    |
| `card.kicker-dot`   | Card             | flexDirection       | column → row                                                                                                                    |
| `card.kicker-dot`   | Card             | overflowY           | hidden → visible                                                                                                                |
| `card.title`        | Card             | overflowY           | hidden → visible                                                                                                                |
| `nospec.note`       | The honest note  | borderWidth         | 0px 0px 1px 0px → 1px                                                                                                           |
| `nospec.note`       | The honest note  | borderRadius        | 0px → 10px                                                                                                                      |
| `row.first`         | Field rows       | gridTemplateColumns | 172px 790px → 172px 564px                                                                                                       |
| `row.first.value`   | Field rows       | fontSize            | 13px → 12px (-1.0px)                                                                                                            |
| `row.first.value`   | Field rows       | fontVariantNumeric  | normal → tabular-nums                                                                                                           |
| `row.numeric.value` | Field rows       | fontSize            | 12px → 13px (+1.0px)                                                                                                            |
| `row.numeric.value` | Field rows       | fontVariantNumeric  | tabular-nums → normal                                                                                                           |
| `row.last`          | Field rows       | gridTemplateColumns | 172px 790px → 172px 564px                                                                                                       |
| `nospec.footer`     | Read-only footer | borderWidth         | 1px 0px 0px 0px → 1px                                                                                                           |
| `nospec.footer`     | Read-only footer | borderRadius        | 0px → 10px                                                                                                                      |

## 🟡 LOW (45)

| Element                 | Group            | Property      | Design → Live                                     |
| ----------------------- | ---------------- | ------------- | ------------------------------------------------- |
| `card`                  | Card             | lineHeight    | 19.5px → normal                                   |
| `card`                  | Card             | height        | 491.438px → 712px                                 |
| `card`                  | Card             | tag           | <div> → <section> (semantic/default-style change) |
| `card.header`           | Card             | lineHeight    | 19.5px → normal                                   |
| `card.header`           | Card             | height        | 85.75px → 51px                                    |
| `card.header`           | Card             | tag           | <div> → <header> (semantic/default-style change)  |
| `card.kicker`           | Card             | lineHeight    | 14.25px → normal                                  |
| `card.kicker`           | Card             | letterSpacing | 1.14px → 0.6px                                    |
| `card.kicker`           | Card             | height        | 14.25px → 13px                                    |
| `card.kicker`           | Card             | textWrap      | nowrap → wrap                                     |
| `card.kicker`           | Card             | tag           | <div> → <span> (semantic/default-style change)    |
| `card.kicker-dot`       | Card             | lineHeight    | 14.25px → normal                                  |
| `card.kicker-dot`       | Card             | letterSpacing | 1.14px → 0.6px                                    |
| `card.kicker-dot`       | Card             | textWrap      | nowrap → wrap                                     |
| `card.title`            | Card             | lineHeight    | 22.5px → normal                                   |
| `card.title`            | Card             | letterSpacing | -0.21px → normal                                  |
| `card.title`            | Card             | height        | 22.5px → 21px                                     |
| `card.title`            | Card             | overflowWrap  | normal → anywhere                                 |
| `card.title`            | Card             | textWrap      | nowrap → wrap                                     |
| `card.title`            | Card             | tag           | <div> → <span> (semantic/default-style change)    |
| `nospec.note`           | The honest note  | width         | 1000px → 774px                                    |
| `nospec.note`           | The honest note  | height        | 56.1875px → 57.1875px                             |
| `nospec.note`           | The honest note  | borderStyle   | none none solid none → solid                      |
| `nospec.note.tool-code` | The honest note  | overflowWrap  | normal → anywhere                                 |
| `row.first`             | Field rows       | lineHeight    | 19.5px → normal                                   |
| `row.first`             | Field rows       | width         | 1000px → 774px                                    |
| `row.first`             | Field rows       | height        | 38.5px → 35px                                     |
| `row.first.label`       | Field rows       | lineHeight    | 14.25px → normal                                  |
| `row.first.label`       | Field rows       | height        | 14.25px → 13px                                    |
| `row.first.label`       | Field rows       | overflowWrap  | normal → anywhere                                 |
| `row.first.value`       | Field rows       | lineHeight    | 19.5px → normal                                   |
| `row.first.value`       | Field rows       | width         | 790px → 564px                                     |
| `row.first.value`       | Field rows       | height        | 19.5px → 16px                                     |
| `row.numeric.value`     | Field rows       | lineHeight    | 18px → normal                                     |
| `row.numeric.value`     | Field rows       | width         | 790px → 564px                                     |
| `row.numeric.value`     | Field rows       | height        | 18px → 16px                                       |
| `row.summarised-object` | Field rows       | lineHeight    | 19.5px → normal                                   |
| `row.summarised-object` | Field rows       | width         | 790px → 564px                                     |
| `row.summarised-object` | Field rows       | height        | 19.5px → 16px                                     |
| `row.last`              | Field rows       | lineHeight    | 19.5px → normal                                   |
| `row.last`              | Field rows       | height        | 37.5px → 34px                                     |
| `nospec.footer`         | Read-only footer | lineHeight    | 19.5px → normal                                   |
| `nospec.footer`         | Read-only footer | height        | 42px → 35px                                       |
| `nospec.footer`         | Read-only footer | borderStyle   | solid none none none → solid                      |
| `nospec.footer`         | Read-only footer | tag           | <div> → <footer> (semantic/default-style change)  |

## ⚪ INFO (12)

| Element         | Group            | Property | Design → Live                                                                                                                     |
| --------------- | ---------------- | -------- | --------------------------------------------------------------------------------------------------------------------------------- |
| `card`          | Card             | text     | “Incident/no specElevated 5xx on payouts-apipagerduty · incid…” → “TableTableNo spec matched pagerduty.incident.read, so this i…” |
| `card`          | Card             | width    | expected: intrinsic width follows dynamic runtime copy — 1002px → 820px                                                           |
| `card.header`   | Card             | text     | “Incident/no specElevated 5xx on payouts-apipagerduty · incid…” → “TableTable”                                                    |
| `card.header`   | Card             | width    | expected: intrinsic width follows dynamic runtime copy — 1000px → 774px                                                           |
| `card.kicker`   | Card             | text     | “Incident/no spec” → “Table”                                                                                                      |
| `card.kicker`   | Card             | width    | expected: intrinsic width follows dynamic runtime copy — 866.375px → 49.8281px                                                    |
| `card.title`    | Card             | text     | “Elevated 5xx on payouts-api” → “Table”                                                                                           |
| `card.title`    | Card             | width    | expected: intrinsic width follows dynamic runtime copy — 866.375px → 49.8281px                                                    |
| `row.last`      | Field rows       | text     | “Html Urlhttps://…/incidents/4127” → “Html Urlhttps://example.pagerduty.com/incidents/4127”                                       |
| `row.last`      | Field rows       | width    | expected: intrinsic width follows dynamic runtime copy — 1000px → 774px                                                           |
| `nospec.footer` | Read-only footer | text     | “Read-only. Generic views never carry a write action. Open in…” → “Read-only. Generic views never carry a write action.”          |
| `nospec.footer` | Read-only footer | width    | expected: intrinsic width follows dynamic runtime copy — 1000px → 774px                                                           |
