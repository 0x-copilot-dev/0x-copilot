# Design-parity report — `default`

Design baseline (source of truth) vs live app, by computed style.

- Design: `surfaces/tools/out/design-default.json`
- Live: `surfaces/tools/out/live-default.json`

**Summary:** 🔴 HIGH 14 · 🟠 MEDIUM 48 · 🟡 LOW 44 · ⚪ INFO 10

## 🔴 HIGH (14)

| Element                     | Group              | Property        | Design → Live                                                                                                           |
| --------------------------- | ------------------ | --------------- | ----------------------------------------------------------------------------------------------------------------------- |
| `default.section.head`      | Section header     | missing-in-live | present in design, ABSENT in live                                                                                       |
| `default.connect.cta`       | Section header     | fontSize        | 11.5px → 13.6px (+2.1px)                                                                                                |
| `default.connect.cta`       | Section header     | borderColor     | rgba(0, 0, 0, 0) (transparent) → rgb(95, 178, 236) (--accent/--sky)                                                     |
| `default.rowlist`           | List               | backgroundColor | rgb(17, 17, 20) (--panel) → rgba(0, 0, 0, 0) (transparent)                                                              |
| `default.rowlist`           | List               | borderColor     | rgba(255, 255, 255, 0.06) (--line) → rgb(236, 236, 241) (--tx)                                                          |
| `default.row.first`         | Row                | backgroundColor | rgba(0, 0, 0, 0) (transparent) → rgb(13, 13, 16)                                                                        |
| `default.row.first`         | Row                | borderColor     | rgb(236, 236, 241) rgb(236, 236, 241) rgba(255, 255, 255, 0.06) rgb(236, 236, 241) → rgba(255, 255, 255, 0.06) (--line) |
| `default.row.logo`          | Row                | missing-in-live | present in design, ABSENT in live                                                                                       |
| `default.row.sub`           | Row                | fontFamily      | typeface class changed (mono → sans)                                                                                    |
| `default.row.sub`           | Row                | fontSize        | 11px → 13.6px (+2.6px)                                                                                                  |
| `default.row.sub`           | Row                | color           | rgb(100, 100, 109) (--mut2) → rgb(152, 152, 159) (--mut)                                                                |
| `default.seg`               | Permission control | backgroundColor | rgb(17, 17, 20) (--panel) → rgb(9, 9, 11)                                                                               |
| `default.seg.selected`      | Permission control | backgroundColor | rgb(29, 29, 35) (--panel3) → rgb(13, 13, 16)                                                                            |
| `default.seg.read.selected` | Permission control | backgroundColor | rgb(29, 29, 35) (--panel3) → rgb(13, 13, 16)                                                                            |

## 🟠 MEDIUM (48)

| Element                     | Group              | Property       | Design → Live                                              |
| --------------------------- | ------------------ | -------------- | ---------------------------------------------------------- |
| `default.page.lead`         | Page               | fontSize       | 12px → 13.6px (+1.6px)                                     |
| `default.page.lead`         | Page               | margin         | -2px 0px 18px 0px → 0px                                    |
| `default.page.lead.link`    | Page               | fontSize       | 12px → 13.6px (+1.6px)                                     |
| `default.page.lead.link`    | Page               | display        | inline → inline-block                                      |
| `default.connect.cta`       | Section header     | display        | flex → block                                               |
| `default.connect.cta`       | Section header     | alignItems     | center → normal                                            |
| `default.connect.cta`       | Section header     | padding        | 4px 9px → 0px 14px                                         |
| `default.connect.cta`       | Section header     | margin         | 0px 0px 0px 701.469px → 0px                                |
| `default.connect.cta`       | Section header     | gap            | 6px → normal                                               |
| `default.rowlist`           | List               | fontSize       | 13px → 13.6px (+0.6px)                                     |
| `default.rowlist`           | List               | display        | flex → grid                                                |
| `default.rowlist`           | List               | flexDirection  | column → row                                               |
| `default.rowlist`           | List               | borderWidth    | 1px → 0px                                                  |
| `default.rowlist`           | List               | borderRadius   | 8px → 0px                                                  |
| `default.rowlist`           | List               | gap            | normal → 12px                                              |
| `default.row.first`         | Row                | fontSize       | 13px → 13.6px (+0.6px)                                     |
| `default.row.first`         | Row                | flexDirection  | row → column                                               |
| `default.row.first`         | Row                | alignItems     | center → normal                                            |
| `default.row.first`         | Row                | padding        | 11px 14px → 14px                                           |
| `default.row.first`         | Row                | borderWidth    | 0px 0px 1px 0px → 1px                                      |
| `default.row.first`         | Row                | borderRadius   | 0px → 8px                                                  |
| `default.row.first`         | Row                | gap            | 12px → 8px                                                 |
| `default.row.name`          | Row                | fontSize       | 12.5px → 14px (+1.5px)                                     |
| `default.row.name`          | Row                | fontWeight     | 500 → 600                                                  |
| `default.row.name`          | Row                | display        | flex → block                                               |
| `default.row.name`          | Row                | alignItems     | center → normal                                            |
| `default.row.name`          | Row                | flexGrow       | flex-grow 0 → 1 (affects vertical fill / button placement) |
| `default.row.name`          | Row                | gap            | 8px → normal                                               |
| `default.row.sub`           | Row                | display        | inline → flow-root                                         |
| `default.row.sub`           | Row                | margin         | 1px 0px 0px 0px → 0px                                      |
| `default.row.act`           | Row                | fontSize       | 13px → 13.6px (+0.6px)                                     |
| `default.row.act`           | Row                | justifyContent | normal → space-between                                     |
| `default.row.act`           | Row                | flexWrap       | nowrap → wrap                                              |
| `default.row.act`           | Row                | gap            | 9px → 10px                                                 |
| `default.seg`               | Permission control | fontSize       | 13px → 13.6px (+0.6px)                                     |
| `default.seg`               | Permission control | alignItems     | normal → center                                            |
| `default.seg`               | Permission control | borderRadius   | 7px → 8px                                                  |
| `default.seg.selected`      | Permission control | fontSize       | 12px → 12.48px (+0.5px)                                    |
| `default.seg.selected`      | Permission control | fontWeight     | 500 → 600                                                  |
| `default.seg.selected`      | Permission control | padding        | 5px 12px → 4px 10px                                        |
| `default.seg.selected`      | Permission control | borderRadius   | 5px → 6px                                                  |
| `default.seg.unselected`    | Permission control | fontSize       | 12px → 12.48px (+0.5px)                                    |
| `default.seg.unselected`    | Permission control | padding        | 5px 12px → 4px 10px                                        |
| `default.seg.unselected`    | Permission control | borderRadius   | 5px → 6px                                                  |
| `default.seg.read.selected` | Permission control | fontSize       | 12px → 12.48px (+0.5px)                                    |
| `default.seg.read.selected` | Permission control | fontWeight     | 500 → 600                                                  |
| `default.seg.read.selected` | Permission control | padding        | 5px 12px → 4px 10px                                        |
| `default.seg.read.selected` | Permission control | borderRadius   | 5px → 6px                                                  |

## 🟡 LOW (44)

| Element                     | Group              | Property    | Design → Live                                  |
| --------------------------- | ------------------ | ----------- | ---------------------------------------------- |
| `default.page.lead`         | Page               | lineHeight  | 19.2px → 20.4px                                |
| `default.page.lead`         | Page               | width       | 544.219px → 620px                              |
| `default.page.lead`         | Page               | height      | 38.375px → 20.3906px                           |
| `default.page.lead.link`    | Page               | lineHeight  | 19.2px → 20.4px                                |
| `default.page.lead.link`    | Page               | textAlign   | start → center                                 |
| `default.page.lead.link`    | Page               | width       | auto → 358.109px                               |
| `default.page.lead.link`    | Page               | height      | auto → 20.3906px                               |
| `default.page.lead.link`    | Page               | tag         | <a> → <button> (semantic/default-style change) |
| `default.connect.cta`       | Section header     | width       | 121.594px → 125.438px                          |
| `default.connect.cta`       | Section header     | height      | 23px → 32px                                    |
| `default.rowlist`           | List               | lineHeight  | 19.5px → normal                                |
| `default.rowlist`           | List               | width       | 912px → 892px                                  |
| `default.rowlist`           | List               | height      | 368.5px → 330px                                |
| `default.rowlist`           | List               | borderStyle | solid → none                                   |
| `default.row.first`         | Row                | lineHeight  | 19.5px → normal                                |
| `default.row.first`         | Row                | textAlign   | left → start                                   |
| `default.row.first`         | Row                | width       | 910px → 289.328px                              |
| `default.row.first`         | Row                | height      | 61.25px → 159px                                |
| `default.row.first`         | Row                | borderStyle | none none solid none → solid                   |
| `default.row.name`          | Row                | lineHeight  | 18.75px → normal                               |
| `default.row.name`          | Row                | textAlign   | left → start                                   |
| `default.row.name`          | Row                | width       | 635.922px → 144.844px                          |
| `default.row.name`          | Row                | height      | 18.75px → 17px                                 |
| `default.row.name`          | Row                | tag         | <span> → <h3> (semantic/default-style change)  |
| `default.row.sub`           | Row                | lineHeight  | 16.5px → normal                                |
| `default.row.sub`           | Row                | textAlign   | left → start                                   |
| `default.row.sub`           | Row                | width       | auto → 259.328px                               |
| `default.row.sub`           | Row                | height      | auto → 16px                                    |
| `default.row.sub`           | Row                | tag         | <span> → <p> (semantic/default-style change)   |
| `default.row.act`           | Row                | lineHeight  | 19.5px → normal                                |
| `default.row.act`           | Row                | textAlign   | left → start                                   |
| `default.row.act`           | Row                | width       | 192.078px → 259.328px                          |
| `default.row.act`           | Row                | height      | 31px → 54px                                    |
| `default.row.act`           | Row                | tag         | <span> → <div> (semantic/default-style change) |
| `default.seg`               | Permission control | lineHeight  | 19.5px → normal                                |
| `default.seg`               | Permission control | textAlign   | left → start                                   |
| `default.seg`               | Permission control | width       | 192.078px → 184.859px                          |
| `default.seg`               | Permission control | height      | 31px → 29px                                    |
| `default.seg.selected`      | Permission control | width       | 86.5781px → 85.7188px                          |
| `default.seg.selected`      | Permission control | height      | 25px → 23px                                    |
| `default.seg.unselected`    | Permission control | width       | 53.0938px → 50.1094px                          |
| `default.seg.unselected`    | Permission control | height      | 25px → 23px                                    |
| `default.seg.read.selected` | Permission control | width       | 53.0938px → 50.7188px                          |
| `default.seg.read.selected` | Permission control | height      | 25px → 23px                                    |

## ⚪ INFO (10)

| Element                  | Group            | Property        | Design → Live                                                                                                                                                                                                                                                                                                                                                   |
| ------------------------ | ---------------- | --------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `default.page.lead`      | Page             | text            | “The apps the agent can read from and act through — a destina…” → “The approval policy lives in Settings → Model & behavior.”                                                                                                                                                                                                                                   |
| `default.page.lead.link` | Page             | text            | “Settings → Model & behavior” → “The approval policy lives in Settings → Model & behavior.”                                                                                                                                                                                                                                                                     |
| `default.rowlist`        | List             | text            | “◇Safe{Wallet}3-of-5 multisig · BaseReadRead & actOffSGoogle …” → “Safe{Wallet}Connected3-of-5 multisig · BaseAgent accessReadR…”                                                                                                                                                                                                                               |
| `default.row.first`      | Row              | text            | “◇Safe{Wallet}3-of-5 multisig · BaseReadRead & actOff” → “Safe{Wallet}Connected3-of-5 multisig · BaseAgent accessReadR…”                                                                                                                                                                                                                                        |
| `default.row.act`        | Row              | text            | “ReadRead & actOff” → “Agent accessReadRead & actOff”                                                                                                                                                                                                                                                                                                           |
| `default.rail.badge`     | Shell            | missing-in-live | expected: OUT OF FRAME, not missing: the rail is app-shell chrome (ChatShell), not part of ConnectorsDestination. The live fixture renders only the destination content area (1172x756 = the design window minus the 48px rail, 38px title bar and 46px topbar), so there is no rail to anchor. Rail-badge parity belongs to the shell audit, not this surface. |
| `live.filter.tabs`       | Live-only chrome | extra-in-live   | present in live, not in design map                                                                                                                                                                                                                                                                                                                              |
| `live.page.header.title` | Live-only chrome | extra-in-live   | present in live, not in design map                                                                                                                                                                                                                                                                                                                              |
| `live.status.pill`       | Live-only chrome | extra-in-live   | present in live, not in design map                                                                                                                                                                                                                                                                                                                              |
| `live.connectors.panel`  | Live-only chrome | extra-in-live   | present in live, not in design map                                                                                                                                                                                                                                                                                                                              |
