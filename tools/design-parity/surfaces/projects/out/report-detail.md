# Design-parity report — `detail`

Design baseline (source of truth) vs live app, by computed style.

- Design: `surfaces/projects/out/design-detail.json`
- Live: `surfaces/projects/out/live-detail.json`

**Summary:** 🔴 HIGH 13 · 🟠 MEDIUM 28 · 🟡 LOW 46 · ⚪ INFO 14

## 🔴 HIGH (13)

| Element                | Group         | Property        | Design → Live                                                                                                  |
| ---------------------- | ------------- | --------------- | -------------------------------------------------------------------------------------------------------------- |
| `detail.desc`          | Detail header | fontSize        | 11px → 13px (+2.0px)                                                                                           |
| `detail.desc`          | Detail header | color           | rgb(100, 100, 109) (--mut2) → rgb(152, 152, 159) (--mut)                                                       |
| `detail.chatrow`       | Chat row      | borderColor     | rgb(236, 236, 241) rgb(236, 236, 241) rgba(255, 255, 255, 0.06) rgb(236, 236, 241) → rgb(236, 236, 241) (--tx) |
| `detail.chatrow.icon`  | Chat row      | color           | rgb(87, 199, 133) (--jade) → rgb(152, 152, 159) (--mut)                                                        |
| `detail.chatrow.chip`  | Chat row      | fontFamily      | typeface class changed (mono → sans)                                                                           |
| `detail.chatrow.chip`  | Chat row      | fontSize        | 10.5px → 13px (+2.5px)                                                                                         |
| `detail.chatrow.chip`  | Chat row      | color           | rgb(87, 199, 133) (--jade) → rgb(236, 236, 241) (--tx)                                                         |
| `detail.chatrow.chip`  | Chat row      | borderColor     | rgba(87, 199, 133, 0.25) → rgb(236, 236, 241) (--tx)                                                           |
| `detail.rowlist.files` | Sections      | backgroundColor | rgb(17, 17, 20) (--panel) → rgba(0, 0, 0, 0) (transparent)                                                     |
| `detail.rowlist.files` | Sections      | borderColor     | rgba(255, 255, 255, 0.06) (--line) → rgb(236, 236, 241) (--tx)                                                 |
| `detail.filerow`       | File row      | missing-in-live | present in design, ABSENT in live                                                                              |
| `detail.filerow.name`  | File row      | missing-in-live | present in design, ABSENT in live                                                                              |
| `detail.filerow.sub`   | File row      | missing-in-live | present in design, ABSENT in live                                                                              |

## 🟠 MEDIUM (28)

| Element                  | Group         | Property      | Design → Live                                              |
| ------------------------ | ------------- | ------------- | ---------------------------------------------------------- |
| `default.page.container` | Layout        | display       | block → flex                                               |
| `default.page.container` | Layout        | flexDirection | row → column                                               |
| `default.page.container` | Layout        | flexGrow      | flex-grow 1 → 0 (affects vertical fill / button placement) |
| `default.page.container` | Layout        | gap           | normal → 16px                                              |
| `detail.backlink`        | Detail header | display       | inline-flex → flex                                         |
| `detail.icon`            | Detail header | borderWidth   | 0px → 1px                                                  |
| `detail.desc`            | Detail header | display       | block → flow-root                                          |
| `detail.desc`            | Detail header | margin        | 1px 0px 0px 0px → 0px                                      |
| `detail.secth.chats`     | Sections      | margin        | 22px 0px 10px 0px → 0px                                    |
| `detail.rowlist.chats`   | Sections      | display       | flex → block                                               |
| `detail.rowlist.chats`   | Sections      | flexDirection | column → row                                               |
| `detail.chatrow`         | Chat row      | borderWidth   | 0px 0px 1px 0px → 0px                                      |
| `detail.chatrow.name`    | Chat row      | display       | flex → block                                               |
| `detail.chatrow.name`    | Chat row      | alignItems    | center → normal                                            |
| `detail.chatrow.name`    | Chat row      | gap           | 8px → normal                                               |
| `detail.chatrow.chip`    | Chat row      | fontWeight    | 500 → 400                                                  |
| `detail.chatrow.chip`    | Chat row      | alignItems    | center → normal                                            |
| `detail.chatrow.chip`    | Chat row      | padding       | 1px 8px → 0px                                              |
| `detail.chatrow.chip`    | Chat row      | borderWidth   | 1px → 0px                                                  |
| `detail.chatrow.chip`    | Chat row      | borderRadius  | 999px → 0px                                                |
| `detail.chatrow.chip`    | Chat row      | gap           | 5px → normal                                               |
| `detail.chatrow.sub`     | Chat row      | display       | inline → block                                             |
| `detail.chatrow.sub`     | Chat row      | margin        | 1px 0px 0px 0px → 0px                                      |
| `detail.chatrow.time`    | Chat row      | fontSize      | 10.5px → 11.2px (+0.7px)                                   |
| `detail.secth.files`     | Sections      | margin        | 22px 0px 10px 0px → 0px                                    |
| `detail.rowlist.files`   | Sections      | borderWidth   | 1px → 0px                                                  |
| `detail.rowlist.files`   | Sections      | borderRadius  | 8px → 0px                                                  |
| `detail.rowlist.files`   | Sections      | gap           | normal → 8px                                               |

## 🟡 LOW (46)

| Element                   | Group         | Property    | Design → Live                                                                                                                                                                            |
| ------------------------- | ------------- | ----------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `default.page.container`  | Layout        | lineHeight  | 19.5px → normal                                                                                                                                                                          |
| `default.page.container`  | Layout        | height      | 754px → 588.391px                                                                                                                                                                        |
| `detail.backlink`         | Detail header | transition  | all → background-color 0.12s cubic-bezier(0.2, 0, 0, 1), border-color 0.12s cubic-bezier(0.2, 0, 0, 1), color 0.12s cubic-bezier(0.2, 0, 0, 1), opacity 0.12s cubic-bezier(0.2, 0, 0, 1) |
| `detail.backlink`         | Detail header | width       | 98.2031px → 912px                                                                                                                                                                        |
| `detail.icon`             | Detail header | lineHeight  | 19.5px → normal                                                                                                                                                                          |
| `detail.icon`             | Detail header | borderStyle | none → solid                                                                                                                                                                             |
| `detail.title`            | Detail header | lineHeight  | 21.6px → normal                                                                                                                                                                          |
| `detail.title`            | Detail header | width       | 116.469px → 110.703px                                                                                                                                                                    |
| `detail.title`            | Detail header | height      | 21.5938px → 21px                                                                                                                                                                         |
| `detail.desc`             | Detail header | lineHeight  | 16.5px → 20.15px                                                                                                                                                                         |
| `detail.desc`             | Detail header | width       | 116.469px → 866px                                                                                                                                                                        |
| `detail.desc`             | Detail header | height      | 16.5px → 20.1406px                                                                                                                                                                       |
| `detail.desc`             | Detail header | tag         | <div> → <p> (semantic/default-style change)                                                                                                                                              |
| `detail.secth.chats`      | Sections      | lineHeight  | 14.25px → normal                                                                                                                                                                         |
| `detail.secth.chats`      | Sections      | width       | 912px → 34.2031px                                                                                                                                                                        |
| `detail.secth.chats`      | Sections      | height      | 14.25px → 13px                                                                                                                                                                           |
| `detail.secth.chats`      | Sections      | tag         | <div> → <h2> (semantic/default-style change)                                                                                                                                             |
| `detail.rowlist.chats`    | Sections      | lineHeight  | 19.5px → normal                                                                                                                                                                          |
| `detail.rowlist.chats`    | Sections      | height      | 187.75px → 177.25px                                                                                                                                                                      |
| `detail.rowlist.chats`    | Sections      | tag         | <div> → <ul> (semantic/default-style change)                                                                                                                                             |
| `detail.chatrow`          | Chat row      | lineHeight  | 19.5px → normal                                                                                                                                                                          |
| `detail.chatrow`          | Chat row      | height      | 62.25px → 57.75px                                                                                                                                                                        |
| `detail.chatrow`          | Chat row      | borderStyle | none none solid none → none                                                                                                                                                              |
| `detail.chatrow`          | Chat row      | tag         | <button> → <div> (semantic/default-style change)                                                                                                                                         |
| `detail.chatrow.icon`     | Chat row      | lineHeight  | 19.5px → normal                                                                                                                                                                          |
| `detail.chatrow.name`     | Chat row      | lineHeight  | 18.75px → normal                                                                                                                                                                         |
| `detail.chatrow.name`     | Chat row      | width       | 811.094px → 105.203px                                                                                                                                                                    |
| `detail.chatrow.name`     | Chat row      | height      | 19.75px → 15px                                                                                                                                                                           |
| `detail.chatrow.chip`     | Chat row      | lineHeight  | 15.75px → normal                                                                                                                                                                         |
| `detail.chatrow.chip`     | Chat row      | width       | 73.1094px → 43.2031px                                                                                                                                                                    |
| `detail.chatrow.chip`     | Chat row      | borderStyle | solid → none                                                                                                                                                                             |
| `detail.chatrow.sub`      | Chat row      | lineHeight  | 16.5px → normal                                                                                                                                                                          |
| `detail.chatrow.sub`      | Chat row      | width       | auto → 761.672px                                                                                                                                                                         |
| `detail.chatrow.sub`      | Chat row      | height      | auto → 14px                                                                                                                                                                              |
| `detail.chatrow.sub.mono` | Chat row      | lineHeight  | 16.5px → normal                                                                                                                                                                          |
| `detail.chatrow.time`     | Chat row      | lineHeight  | 15.75px → normal                                                                                                                                                                         |
| `detail.chatrow.time`     | Chat row      | width       | 18.9062px → 40.3281px                                                                                                                                                                    |
| `detail.chatrow.time`     | Chat row      | height      | 15.75px → 14px                                                                                                                                                                           |
| `detail.secth.files`      | Sections      | lineHeight  | 14.25px → normal                                                                                                                                                                         |
| `detail.secth.files`      | Sections      | width       | 912px → 34.2031px                                                                                                                                                                        |
| `detail.secth.files`      | Sections      | height      | 14.25px → 13px                                                                                                                                                                           |
| `detail.secth.files`      | Sections      | tag         | <div> → <h2> (semantic/default-style change)                                                                                                                                             |
| `detail.rowlist.files`    | Sections      | lineHeight  | 19.5px → normal                                                                                                                                                                          |
| `detail.rowlist.files`    | Sections      | height      | 246px → 118px                                                                                                                                                                            |
| `detail.rowlist.files`    | Sections      | borderStyle | solid → none                                                                                                                                                                             |
| `detail.rowlist.files`    | Sections      | tag         | <div> → <section> (semantic/default-style change)                                                                                                                                        |

## ⚪ INFO (14)

| Element                   | Group            | Property        | Design → Live                                                                                                                                                                                                                                  |
| ------------------------- | ---------------- | --------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `default.page.container`  | Layout           | text            | “All projectsLLaunch WeekGTM for the v2 launchChats · 3Launch…” → “All projectsLLaunch WeekActiveGTM for the v2 launchOwner: us…”                                                                                                              |
| `detail.icon`             | Detail header    | color           | expected: Per-project hue is intentional (D3): live persists color_hue + ships a hue picker; the mock's !important tile neutralisation is a leftover, not intent. Recorded divergence. — rgb(212, 212, 219) (--tx2) → rgb(177, 215, 241)       |
| `detail.icon`             | Detail header    | backgroundColor | expected: Per-project hue is intentional (D3): live persists color_hue + ships a hue picker; the mock's !important tile neutralisation is a leftover, not intent. Recorded divergence. — rgb(29, 29, 35) (--panel3) → rgba(29, 79, 114, 0.45)  |
| `detail.icon`             | Detail header    | borderColor     | expected: Per-project hue is intentional (D3): live persists color_hue + ships a hue picker; the mock's !important tile neutralisation is a leftover, not intent. Recorded divergence. — rgb(212, 212, 219) (--tx2) → rgba(51, 140, 204, 0.55) |
| `detail.secth.chats`      | Sections         | text            | “Chats · 3” → “Chats”                                                                                                                                                                                                                          |
| `detail.rowlist.chats`    | Sections         | text            | “Launch Week ops runningStreaming the launch thread · Claude …” → “Launch Week opsdoneStreaming the launch thread · gpt-4o2d ag…”                                                                                                              |
| `detail.chatrow`          | Chat row         | text            | “Launch Week ops runningStreaming the launch thread · Claude …” → “Launch Week opsdoneStreaming the launch thread · gpt-4o2d ag…”                                                                                                              |
| `detail.chatrow.chip`     | Chat row         | text            | “running” → “done”                                                                                                                                                                                                                             |
| `detail.chatrow.sub.mono` | Chat row         | text            | “Claude Sonnet 4.5” → “gpt-4o”                                                                                                                                                                                                                 |
| `detail.chatrow.time`     | Chat row         | text            | “now” → “2d ago”                                                                                                                                                                                                                               |
| `detail.secth.files`      | Sections         | text            | “Files · 12” → “Files”                                                                                                                                                                                                                         |
| `detail.rowlist.files`    | Sections         | text            | “tokenomics.xlsxSheets · edited 2d agolaunch-brief.mdDoc · ed…” → “Launch deck.pdfPDF2d agoLaunch deck.pdfGTM plan.mdDoc3d agoG…”                                                                                                              |
| `detail.x.status`         | Live-only chrome | extra-in-live   | present in live, not in design map                                                                                                                                                                                                             |
| `detail.x.owner`          | Live-only chrome | extra-in-live   | present in live, not in design map                                                                                                                                                                                                             |
