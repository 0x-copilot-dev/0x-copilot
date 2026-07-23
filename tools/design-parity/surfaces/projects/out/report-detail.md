# Design-parity report — `detail`

Design baseline (source of truth) vs live app, by computed style.

- Design: `surfaces/projects/out/design-detail.json`
- Live: `surfaces/projects/out/live-detail.json`

**Summary:** 🔴 HIGH 23 · 🟠 MEDIUM 34 · 🟡 LOW 50 · ⚪ INFO 12

## 🔴 HIGH (23)

| Element                   | Group         | Property        | Design → Live                                                                                                  |
| ------------------------- | ------------- | --------------- | -------------------------------------------------------------------------------------------------------------- |
| `detail.backlink`         | Detail header | fontFamily      | typeface class changed (mono → sans)                                                                           |
| `detail.backlink`         | Detail header | fontSize        | 11px → 13px (+2.0px)                                                                                           |
| `detail.backlink`         | Detail header | color           | rgb(152, 152, 159) (--mut) → rgb(95, 178, 236) (--accent/--sky)                                                |
| `detail.icon`             | Detail header | fontSize        | 13px → 18px (+5.0px)                                                                                           |
| `detail.icon`             | Detail header | color           | rgb(212, 212, 219) (--tx2) → rgb(177, 215, 241)                                                                |
| `detail.icon`             | Detail header | backgroundColor | rgb(29, 29, 35) (--panel3) → rgba(29, 79, 114, 0.45)                                                           |
| `detail.icon`             | Detail header | borderColor     | rgb(212, 212, 219) (--tx2) → rgba(51, 140, 204, 0.55)                                                          |
| `detail.desc`             | Detail header | fontSize        | 11px → 13px (+2.0px)                                                                                           |
| `detail.desc`             | Detail header | color           | rgb(100, 100, 109) (--mut2) → rgb(152, 152, 159) (--mut)                                                       |
| `detail.rowlist.chats`    | Sections      | backgroundColor | rgb(17, 17, 20) (--panel) → rgba(0, 0, 0, 0) (transparent)                                                     |
| `detail.rowlist.chats`    | Sections      | borderColor     | rgba(255, 255, 255, 0.06) (--line) → rgb(236, 236, 241) (--tx)                                                 |
| `detail.chatrow`          | Chat row      | borderColor     | rgb(236, 236, 241) rgb(236, 236, 241) rgba(255, 255, 255, 0.06) rgb(236, 236, 241) → rgb(236, 236, 241) (--tx) |
| `detail.chatrow.icon`     | Chat row      | missing-in-live | present in design, ABSENT in live                                                                              |
| `detail.chatrow.name`     | Chat row      | color           | rgb(236, 236, 241) (--tx) → rgb(95, 178, 236) (--accent/--sky)                                                 |
| `detail.chatrow.chip`     | Chat row      | missing-in-live | present in design, ABSENT in live                                                                              |
| `detail.chatrow.sub`      | Chat row      | missing-in-live | present in design, ABSENT in live                                                                              |
| `detail.chatrow.sub.mono` | Chat row      | missing-in-live | present in design, ABSENT in live                                                                              |
| `detail.chatrow.time`     | Chat row      | missing-in-live | present in design, ABSENT in live                                                                              |
| `detail.rowlist.files`    | Sections      | backgroundColor | rgb(17, 17, 20) (--panel) → rgba(0, 0, 0, 0) (transparent)                                                     |
| `detail.rowlist.files`    | Sections      | borderColor     | rgba(255, 255, 255, 0.06) (--line) → rgb(236, 236, 241) (--tx)                                                 |
| `detail.filerow`          | File row      | missing-in-live | present in design, ABSENT in live                                                                              |
| `detail.filerow.name`     | File row      | missing-in-live | present in design, ABSENT in live                                                                              |
| `detail.filerow.sub`      | File row      | missing-in-live | present in design, ABSENT in live                                                                              |

## 🟠 MEDIUM (34)

| Element                  | Group         | Property       | Design → Live                                              |
| ------------------------ | ------------- | -------------- | ---------------------------------------------------------- |
| `default.page.container` | Layout        | flexGrow       | flex-grow 1 → 0 (affects vertical fill / button placement) |
| `default.page.container` | Layout        | padding        | 20px 24px 40px 24px → 24px                                 |
| `detail.backlink`        | Detail header | fontWeight     | 400 → 600                                                  |
| `detail.backlink`        | Detail header | display        | inline-flex → inline-block                                 |
| `detail.backlink`        | Detail header | alignItems     | center → normal                                            |
| `detail.backlink`        | Detail header | padding        | 0px → 0px 0px 12px 0px                                     |
| `detail.backlink`        | Detail header | margin         | 0px 0px 14px 0px → 0px                                     |
| `detail.backlink`        | Detail header | gap            | 6px → normal                                               |
| `detail.icon`            | Detail header | fontWeight     | 600 → 700                                                  |
| `detail.icon`            | Detail header | display        | grid → flex                                                |
| `detail.icon`            | Detail header | justifyContent | normal → center                                            |
| `detail.icon`            | Detail header | borderWidth    | 0px → 1px                                                  |
| `detail.icon`            | Detail header | borderRadius   | 8px → 10px                                                 |
| `detail.desc`            | Detail header | display        | block → flow-root                                          |
| `detail.desc`            | Detail header | margin         | 1px 0px 0px 0px → 0px                                      |
| `detail.secth.chats`     | Sections      | margin         | 22px 0px 10px 0px → 0px                                    |
| `detail.rowlist.chats`   | Sections      | display        | flex → block                                               |
| `detail.rowlist.chats`   | Sections      | flexDirection  | column → row                                               |
| `detail.rowlist.chats`   | Sections      | borderWidth    | 1px → 0px                                                  |
| `detail.rowlist.chats`   | Sections      | borderRadius   | 8px → 0px                                                  |
| `detail.chatrow`         | Chat row      | display        | flex → list-item                                           |
| `detail.chatrow`         | Chat row      | alignItems     | center → normal                                            |
| `detail.chatrow`         | Chat row      | padding        | 11px 14px → 8px 0px                                        |
| `detail.chatrow`         | Chat row      | borderWidth    | 0px 0px 1px 0px → 0px                                      |
| `detail.chatrow`         | Chat row      | gap            | 12px → normal                                              |
| `detail.chatrow.name`    | Chat row      | fontSize       | 12.5px → 13px (+0.5px)                                     |
| `detail.chatrow.name`    | Chat row      | fontWeight     | 500 → 400                                                  |
| `detail.chatrow.name`    | Chat row      | display        | flex → inline-block                                        |
| `detail.chatrow.name`    | Chat row      | alignItems     | center → normal                                            |
| `detail.chatrow.name`    | Chat row      | gap            | 8px → normal                                               |
| `detail.secth.files`     | Sections      | margin         | 22px 0px 10px 0px → 0px                                    |
| `detail.rowlist.files`   | Sections      | borderWidth    | 1px → 0px                                                  |
| `detail.rowlist.files`   | Sections      | borderRadius   | 8px → 0px                                                  |
| `detail.rowlist.files`   | Sections      | gap            | normal → 8px                                               |

## 🟡 LOW (50)

| Element                  | Group         | Property      | Design → Live                                                                                                                                                                            |
| ------------------------ | ------------- | ------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `default.page.container` | Layout        | lineHeight    | 19.5px → normal                                                                                                                                                                          |
| `default.page.container` | Layout        | width         | 960px → 1040px                                                                                                                                                                           |
| `default.page.container` | Layout        | height        | 754px → 760px                                                                                                                                                                            |
| `default.page.container` | Layout        | tag           | <div> → <section> (semantic/default-style change)                                                                                                                                        |
| `detail.backlink`        | Detail header | transition    | all → background-color 0.12s cubic-bezier(0.2, 0, 0, 1), border-color 0.12s cubic-bezier(0.2, 0, 0, 1), color 0.12s cubic-bezier(0.2, 0, 0, 1), opacity 0.12s cubic-bezier(0.2, 0, 0, 1) |
| `detail.backlink`        | Detail header | width         | 98.2031px → 87.0312px                                                                                                                                                                    |
| `detail.backlink`        | Detail header | height        | 14px → 28px                                                                                                                                                                              |
| `detail.icon`            | Detail header | lineHeight    | 19.5px → normal                                                                                                                                                                          |
| `detail.icon`            | Detail header | width         | 32px → 44px                                                                                                                                                                              |
| `detail.icon`            | Detail header | height        | 32px → 44px                                                                                                                                                                              |
| `detail.icon`            | Detail header | borderStyle   | none → solid                                                                                                                                                                             |
| `detail.icon`            | Detail header | tag           | <span> → <div> (semantic/default-style change)                                                                                                                                           |
| `detail.title`           | Detail header | lineHeight    | 21.6px → normal                                                                                                                                                                          |
| `detail.title`           | Detail header | letterSpacing | -0.18px → normal                                                                                                                                                                         |
| `detail.title`           | Detail header | width         | 116.469px → 112.672px                                                                                                                                                                    |
| `detail.title`           | Detail header | height        | 21.5938px → 21px                                                                                                                                                                         |
| `detail.title`           | Detail header | tag           | <h2> → <span> (semantic/default-style change)                                                                                                                                            |
| `detail.desc`            | Detail header | lineHeight    | 16.5px → 20.15px                                                                                                                                                                         |
| `detail.desc`            | Detail header | width         | 116.469px → 822px                                                                                                                                                                        |
| `detail.desc`            | Detail header | height        | 16.5px → 20.1406px                                                                                                                                                                       |
| `detail.desc`            | Detail header | tag           | <div> → <p> (semantic/default-style change)                                                                                                                                              |
| `detail.secth.chats`     | Sections      | lineHeight    | 14.25px → normal                                                                                                                                                                         |
| `detail.secth.chats`     | Sections      | width         | 912px → 34.2031px                                                                                                                                                                        |
| `detail.secth.chats`     | Sections      | height        | 14.25px → 13px                                                                                                                                                                           |
| `detail.secth.chats`     | Sections      | tag           | <div> → <h2> (semantic/default-style change)                                                                                                                                             |
| `detail.rowlist.chats`   | Sections      | lineHeight    | 19.5px → normal                                                                                                                                                                          |
| `detail.rowlist.chats`   | Sections      | width         | 912px → 880px                                                                                                                                                                            |
| `detail.rowlist.chats`   | Sections      | height        | 187.75px → 96px                                                                                                                                                                          |
| `detail.rowlist.chats`   | Sections      | borderStyle   | solid → none                                                                                                                                                                             |
| `detail.rowlist.chats`   | Sections      | tag           | <div> → <ul> (semantic/default-style change)                                                                                                                                             |
| `detail.chatrow`         | Chat row      | lineHeight    | 19.5px → normal                                                                                                                                                                          |
| `detail.chatrow`         | Chat row      | textAlign     | left → start                                                                                                                                                                             |
| `detail.chatrow`         | Chat row      | width         | 910px → 880px                                                                                                                                                                            |
| `detail.chatrow`         | Chat row      | height        | 62.25px → 32px                                                                                                                                                                           |
| `detail.chatrow`         | Chat row      | borderStyle   | none none solid none → none                                                                                                                                                              |
| `detail.chatrow`         | Chat row      | tag           | <button> → <li> (semantic/default-style change)                                                                                                                                          |
| `detail.chatrow.name`    | Chat row      | lineHeight    | 18.75px → normal                                                                                                                                                                         |
| `detail.chatrow.name`    | Chat row      | transition    | all → background-color 0.12s cubic-bezier(0.2, 0, 0, 1), border-color 0.12s cubic-bezier(0.2, 0, 0, 1), color 0.12s cubic-bezier(0.2, 0, 0, 1), opacity 0.12s cubic-bezier(0.2, 0, 0, 1) |
| `detail.chatrow.name`    | Chat row      | width         | 811.094px → 296.938px                                                                                                                                                                    |
| `detail.chatrow.name`    | Chat row      | height        | 19.75px → 16px                                                                                                                                                                           |
| `detail.chatrow.name`    | Chat row      | tag           | <span> → <button> (semantic/default-style change)                                                                                                                                        |
| `detail.secth.files`     | Sections      | lineHeight    | 14.25px → normal                                                                                                                                                                         |
| `detail.secth.files`     | Sections      | width         | 912px → 34.2031px                                                                                                                                                                        |
| `detail.secth.files`     | Sections      | height        | 14.25px → 13px                                                                                                                                                                           |
| `detail.secth.files`     | Sections      | tag           | <div> → <h2> (semantic/default-style change)                                                                                                                                             |
| `detail.rowlist.files`   | Sections      | lineHeight    | 19.5px → normal                                                                                                                                                                          |
| `detail.rowlist.files`   | Sections      | width         | 912px → 880px                                                                                                                                                                            |
| `detail.rowlist.files`   | Sections      | height        | 246px → 156px                                                                                                                                                                            |
| `detail.rowlist.files`   | Sections      | borderStyle   | solid → none                                                                                                                                                                             |
| `detail.rowlist.files`   | Sections      | tag           | <div> → <section> (semantic/default-style change)                                                                                                                                        |

## ⚪ INFO (12)

| Element                  | Group            | Property      | Design → Live                                                                                                                     |
| ------------------------ | ---------------- | ------------- | --------------------------------------------------------------------------------------------------------------------------------- |
| `default.page.container` | Layout           | text          | “All projectsLLaunch WeekGTM for the v2 launchChats · 3Launch…” → “Projects3 activeAllActiveArchivedStarred← All projectsLLaunc…” |
| `detail.backlink`        | Detail header    | text          | “All projects” → “← All projects”                                                                                                 |
| `detail.secth.chats`     | Sections         | text          | “Chats · 3” → “Chats”                                                                                                             |
| `detail.rowlist.chats`   | Sections         | text          | “Launch Week ops runningStreaming the launch thread · Claude …” → “Launch Week ops — Streaming the launch threadInvestor update…” |
| `detail.chatrow`         | Chat row         | text          | “Launch Week ops runningStreaming the launch thread · Claude …” → “Launch Week ops — Streaming the launch thread”                 |
| `detail.chatrow.name`    | Chat row         | text          | “Launch Week ops” → “Launch Week ops — Streaming the launch thread”                                                               |
| `detail.secth.files`     | Sections         | text          | “Files · 12” → “Files”                                                                                                            |
| `detail.rowlist.files`   | Sections         | text          | “tokenomics.xlsxSheets · edited 2d agolaunch-brief.mdDoc · ed…” → “Project files coming soonThis workspace doesn't expose a pro…” |
| `detail.x.pageheader`    | Live-only chrome | extra-in-live | present in live, not in design map                                                                                                |
| `detail.x.filtertabs`    | Live-only chrome | extra-in-live | present in live, not in design map                                                                                                |
| `detail.x.status`        | Live-only chrome | extra-in-live | present in live, not in design map                                                                                                |
| `detail.x.owner`         | Live-only chrome | extra-in-live | present in live, not in design map                                                                                                |
