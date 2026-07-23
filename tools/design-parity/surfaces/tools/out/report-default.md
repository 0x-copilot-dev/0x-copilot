# Design-parity report — `default`

Design baseline (source of truth) vs live app, by computed style.

- Design: `surfaces/tools/out/design-default.json`
- Live: `surfaces/tools/out/live-default.json`

**Summary:** 🔴 HIGH 1 · 🟠 MEDIUM 30 · 🟡 LOW 46 · ⚪ INFO 4

## 🔴 HIGH (1)

| Element            | Group | Property        | Design → Live                     |
| ------------------ | ----- | --------------- | --------------------------------- |
| `default.row.logo` | Row   | missing-in-live | present in design, ABSENT in live |

## 🟠 MEDIUM (30)

| Element                     | Group              | Property       | Design → Live               |
| --------------------------- | ------------------ | -------------- | --------------------------- |
| `default.page.lead`         | Page               | fontSize       | 12px → 12.48px (+0.5px)     |
| `default.page.lead`         | Page               | margin         | -2px 0px 18px 0px → 0px     |
| `default.page.lead.link`    | Page               | fontSize       | 12px → 12.48px (+0.5px)     |
| `default.page.lead.link`    | Page               | display        | inline → inline-block       |
| `default.connect.cta`       | Section header     | justifyContent | normal → center             |
| `default.connect.cta`       | Section header     | padding        | 4px 9px → 4px 8.8px         |
| `default.connect.cta`       | Section header     | margin         | 0px 0px 0px 701.469px → 0px |
| `default.connect.cta`       | Section header     | gap            | 6px → 8px                   |
| `default.rowlist`           | List               | display        | flex → block                |
| `default.rowlist`           | List               | flexDirection  | column → row                |
| `default.row.first`         | Row                | borderWidth    | 0px 0px 1px 0px → 0px       |
| `default.row.name`          | Row                | display        | flex → block                |
| `default.row.name`          | Row                | alignItems     | center → normal             |
| `default.row.name`          | Row                | gap            | 8px → normal                |
| `default.row.sub`           | Row                | display        | inline → block              |
| `default.row.sub`           | Row                | margin         | 1px 0px 0px 0px → 0px       |
| `default.row.act`           | Row                | fontSize       | 13px → 11.2px (-1.8px)      |
| `default.row.act`           | Row                | display        | flex → inline-flex          |
| `default.seg`               | Permission control | fontSize       | 13px → 11.2px (-1.8px)      |
| `default.seg`               | Permission control | alignItems     | normal → center             |
| `default.seg`               | Permission control | borderRadius   | 7px → 8px                   |
| `default.seg.selected`      | Permission control | fontSize       | 12px → 12.48px (+0.5px)     |
| `default.seg.selected`      | Permission control | padding        | 5px 12px → 4px 10px         |
| `default.seg.selected`      | Permission control | borderRadius   | 5px → 6px                   |
| `default.seg.unselected`    | Permission control | fontSize       | 12px → 12.48px (+0.5px)     |
| `default.seg.unselected`    | Permission control | padding        | 5px 12px → 4px 10px         |
| `default.seg.unselected`    | Permission control | borderRadius   | 5px → 6px                   |
| `default.seg.read.selected` | Permission control | fontSize       | 12px → 12.48px (+0.5px)     |
| `default.seg.read.selected` | Permission control | padding        | 5px 12px → 4px 10px         |
| `default.seg.read.selected` | Permission control | borderRadius   | 5px → 6px                   |

## 🟡 LOW (46)

| Element                     | Group              | Property    | Design → Live                                                                                                                                                                                                             |
| --------------------------- | ------------------ | ----------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `default.page.lead`         | Page               | lineHeight  | 19.2px → 21.216px                                                                                                                                                                                                         |
| `default.page.lead`         | Page               | width       | 544.219px → 565.984px                                                                                                                                                                                                     |
| `default.page.lead`         | Page               | height      | 38.375px → 42.4375px                                                                                                                                                                                                      |
| `default.page.lead.link`    | Page               | lineHeight  | 19.2px → 21.216px                                                                                                                                                                                                         |
| `default.page.lead.link`    | Page               | textAlign   | start → center                                                                                                                                                                                                            |
| `default.page.lead.link`    | Page               | transition  | all → background-color 0.12s cubic-bezier(0.2, 0, 0, 1), border-color 0.12s cubic-bezier(0.2, 0, 0, 1), color 0.12s cubic-bezier(0.2, 0, 0, 1), opacity 0.12s cubic-bezier(0.2, 0, 0, 1)                                  |
| `default.page.lead.link`    | Page               | width       | auto → 168.922px                                                                                                                                                                                                          |
| `default.page.lead.link`    | Page               | height      | auto → 21.2188px                                                                                                                                                                                                          |
| `default.page.lead.link`    | Page               | tag         | <a> → <button> (semantic/default-style change)                                                                                                                                                                            |
| `default.section.head`      | Section header     | lineHeight  | 14.25px → normal                                                                                                                                                                                                          |
| `default.section.head`      | Section header     | width       | 88.9375px → 88.9219px                                                                                                                                                                                                     |
| `default.section.head`      | Section header     | height      | 14.25px → 13px                                                                                                                                                                                                            |
| `default.section.head`      | Section header     | tag         | <div> → <h2> (semantic/default-style change)                                                                                                                                                                              |
| `default.connect.cta`       | Section header     | lineHeight  | normal → 13.44px                                                                                                                                                                                                          |
| `default.connect.cta`       | Section header     | transition  | background 0.12s, border-color 0.12s → background-color 0.12s cubic-bezier(0.2, 0, 0, 1), border-color 0.12s cubic-bezier(0.2, 0, 0, 1), color 0.12s cubic-bezier(0.2, 0, 0, 1), opacity 0.12s cubic-bezier(0.2, 0, 0, 1) |
| `default.connect.cta`       | Section header     | width       | 121.594px → 100.297px                                                                                                                                                                                                     |
| `default.connect.cta`       | Section header     | height      | 23px → 24px                                                                                                                                                                                                               |
| `default.rowlist`           | List               | lineHeight  | 19.5px → normal                                                                                                                                                                                                           |
| `default.rowlist`           | List               | width       | 912px → 1040px                                                                                                                                                                                                            |
| `default.rowlist`           | List               | height      | 368.5px → 325px                                                                                                                                                                                                           |
| `default.rowlist`           | List               | tag         | <div> → <ul> (semantic/default-style change)                                                                                                                                                                              |
| `default.row.first`         | Row                | lineHeight  | 19.5px → normal                                                                                                                                                                                                           |
| `default.row.first`         | Row                | width       | 910px → 1038px                                                                                                                                                                                                            |
| `default.row.first`         | Row                | height      | 61.25px → 53px                                                                                                                                                                                                            |
| `default.row.first`         | Row                | borderStyle | none none solid none → none                                                                                                                                                                                               |
| `default.row.name`          | Row                | lineHeight  | 18.75px → normal                                                                                                                                                                                                          |
| `default.row.name`          | Row                | width       | 635.922px → 73.625px                                                                                                                                                                                                      |
| `default.row.name`          | Row                | height      | 18.75px → 15px                                                                                                                                                                                                            |
| `default.row.sub`           | Row                | lineHeight  | 16.5px → normal                                                                                                                                                                                                           |
| `default.row.sub`           | Row                | width       | auto → 744.125px                                                                                                                                                                                                          |
| `default.row.sub`           | Row                | height      | auto → 14px                                                                                                                                                                                                               |
| `default.row.act`           | Row                | lineHeight  | 19.5px → normal                                                                                                                                                                                                           |
| `default.row.act`           | Row                | width       | 192.078px → 183.875px                                                                                                                                                                                                     |
| `default.row.act`           | Row                | height      | 31px → 29px                                                                                                                                                                                                               |
| `default.seg`               | Permission control | lineHeight  | 19.5px → normal                                                                                                                                                                                                           |
| `default.seg`               | Permission control | width       | 192.078px → 183.875px                                                                                                                                                                                                     |
| `default.seg`               | Permission control | height      | 31px → 29px                                                                                                                                                                                                               |
| `default.seg.selected`      | Permission control | transition  | color 0.12s → background-color 0.12s cubic-bezier(0.2, 0, 0, 1)                                                                                                                                                           |
| `default.seg.selected`      | Permission control | width       | 86.5781px → 84.7344px                                                                                                                                                                                                     |
| `default.seg.selected`      | Permission control | height      | 25px → 23px                                                                                                                                                                                                               |
| `default.seg.unselected`    | Permission control | transition  | color 0.12s → background-color 0.12s cubic-bezier(0.2, 0, 0, 1)                                                                                                                                                           |
| `default.seg.unselected`    | Permission control | width       | 53.0938px → 50.1094px                                                                                                                                                                                                     |
| `default.seg.unselected`    | Permission control | height      | 25px → 23px                                                                                                                                                                                                               |
| `default.seg.read.selected` | Permission control | transition  | color 0.12s → background-color 0.12s cubic-bezier(0.2, 0, 0, 1)                                                                                                                                                           |
| `default.seg.read.selected` | Permission control | width       | 53.0938px → 50.1094px                                                                                                                                                                                                     |
| `default.seg.read.selected` | Permission control | height      | 25px → 23px                                                                                                                                                                                                               |

## ⚪ INFO (4)

| Element              | Group | Property        | Design → Live                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     |
| -------------------- | ----- | --------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `default.rowlist`    | List  | text            | “◇Safe{Wallet}3-of-5 multisig · BaseReadRead & actOffSGoogle …” → “sSafe{Wallet}3-of-5 multisig · BaseReadRead & actOffgGoogle …”                                                                                                                                                                                                                                                                                                                                                                                                                                                                 |
| `default.row.first`  | Row   | text            | “◇Safe{Wallet}3-of-5 multisig · BaseReadRead & actOff” → “sSafe{Wallet}3-of-5 multisig · BaseReadRead & actOff”                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| `default.row.first`  | Row   | borderColor     | expected: Border-placement artifact of the shared <RowList> primitive (identical to the Activity surface's `row.live` anchor): the design draws the between-row hairline on `.lrow` itself (border-bottom --line), while RowList draws it on the wrapping `<li>` and leaves the row's own border unset (so its measured borderColor defaults to currentColor --tx on all four edges). The hairline is present and visually identical — only the element that carries it differs. — rgb(236, 236, 241) rgb(236, 236, 241) rgba(255, 255, 255, 0.06) rgb(236, 236, 241) → rgb(236, 236, 241) (--tx) |
| `default.rail.badge` | Shell | missing-in-live | expected: OUT OF FRAME, not missing: the rail is app-shell chrome (ChatShell), not part of ConnectorsDestination. The live fixture renders only the destination content area (1172x756 = the design window minus the 48px rail, 38px title bar and 46px topbar), so there is no rail to anchor. Rail-badge parity belongs to the shell audit, not this surface.                                                                                                                                                                                                                                   |
