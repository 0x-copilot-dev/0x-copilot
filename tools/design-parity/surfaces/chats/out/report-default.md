# Design-parity report — `default`

Design baseline (source of truth) vs live app, by computed style.

- Design: `surfaces/chats/out/design-default.json`
- Live: `surfaces/chats/out/live-default.json`

**Summary:** 🔴 HIGH 5 · 🟠 MEDIUM 31 · 🟡 LOW 46 · ⚪ INFO 7

## 🔴 HIGH (5)

| Element                | Group         | Property        | Design → Live                                               |
| ---------------------- | ------------- | --------------- | ----------------------------------------------------------- |
| `topbar.title`         | Shell         | missing-in-live | present in design, ABSENT in live                           |
| `row.running.ic`       | Row (running) | color           | rgb(87, 199, 133) (--jade) → rgb(152, 152, 159) (--mut)     |
| `row.running.ic`       | Row (running) | backgroundColor | rgb(29, 29, 35) (--panel3) → rgba(0, 0, 0, 0) (transparent) |
| `row.running.sub.mono` | Row (running) | color           | rgb(100, 100, 109) (--mut2) → rgb(152, 152, 159) (--mut)    |
| `row.done.ic`          | Row (done)    | backgroundColor | rgb(29, 29, 35) (--panel3) → rgba(0, 0, 0, 0) (transparent) |

## 🟠 MEDIUM (31)

| Element            | Group         | Property       | Design → Live                                              |
| ------------------ | ------------- | -------------- | ---------------------------------------------------------- |
| `page.container`   | Page          | display        | block → flex                                               |
| `page.container`   | Page          | flexDirection  | row → column                                               |
| `page.container`   | Page          | flexGrow       | flex-grow 1 → 0 (affects vertical fill / button placement) |
| `page.container`   | Page          | padding        | 20px 24px 40px 24px → 24px 28px 96px 28px                  |
| `page.container`   | Page          | margin         | 0px → 0px 110px                                            |
| `page.container`   | Page          | gap            | normal → 20px                                              |
| `page.lead`        | Page          | fontSize       | 12px → 12.48px (+0.5px)                                    |
| `page.lead`        | Page          | margin         | -2px 0px 18px 0px → 0px                                    |
| `header.row`       | Header        | margin         | 0px 0px 14px 0px → 0px 0px 10px 0px                        |
| `header.row`       | Header        | gap            | normal → 8px                                               |
| `btn.newChat`      | Header        | justifyContent | normal → center                                            |
| `btn.newChat`      | Header        | padding        | 4px 9px → 4px 8.8px                                        |
| `btn.newChat`      | Header        | margin         | 0px 0px 0px 778.766px → 0px                                |
| `list.pinned`      | Lists         | display        | flex → block                                               |
| `list.pinned`      | Lists         | flexDirection  | column → row                                               |
| `row.running`      | Row (running) | padding        | 11px 14px → 10px 12px                                      |
| `row.running.ic`   | Row (running) | display        | grid → flex                                                |
| `row.running.ic`   | Row (running) | justifyContent | normal → center                                            |
| `row.running.ic`   | Row (running) | borderRadius   | 7px → 8px                                                  |
| `row.running.name` | Row (running) | fontWeight     | 500 → 600                                                  |
| `row.running.name` | Row (running) | display        | flex → block                                               |
| `row.running.name` | Row (running) | alignItems     | center → normal                                            |
| `row.running.name` | Row (running) | gap            | 8px → normal                                               |
| `row.running.sub`  | Row (running) | display        | inline → block                                             |
| `row.running.sub`  | Row (running) | margin         | 1px 0px 0px 0px → 0px                                      |
| `row.running.time` | Row (running) | fontSize       | 10.5px → 11.2px (+0.7px)                                   |
| `sect.recent`      | Sections      | margin         | 22px 0px 10px 0px → 0px                                    |
| `row.done.ic`      | Row (done)    | display        | grid → flex                                                |
| `row.done.ic`      | Row (done)    | justifyContent | normal → center                                            |
| `row.done.ic`      | Row (done)    | borderRadius   | 7px → 8px                                                  |
| `sect.archived`    | Sections      | margin         | 22px 0px 10px 0px → 0px                                    |

## 🟡 LOW (46)

| Element                | Group         | Property   | Design → Live                                                                                                                                                                                                             |
| ---------------------- | ------------- | ---------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `page.container`       | Page          | lineHeight | 19.5px → normal                                                                                                                                                                                                           |
| `page.container`       | Page          | height     | 754px → 789.719px                                                                                                                                                                                                         |
| `page.lead`            | Page          | lineHeight | 19.2px → 21.216px                                                                                                                                                                                                         |
| `page.lead`            | Page          | width      | 544.219px → 565.984px                                                                                                                                                                                                     |
| `page.lead`            | Page          | height     | 19.1875px → 21.2188px                                                                                                                                                                                                     |
| `header.row`           | Header        | lineHeight | 19.5px → normal                                                                                                                                                                                                           |
| `header.row`           | Header        | width      | 912px → 904px                                                                                                                                                                                                             |
| `header.row`           | Header        | height     | 23px → 24px                                                                                                                                                                                                               |
| `sect.pinned`          | Sections      | lineHeight | 14.25px → normal                                                                                                                                                                                                          |
| `sect.pinned`          | Sections      | height     | 14.25px → 13px                                                                                                                                                                                                            |
| `sect.pinned`          | Sections      | tag        | <div> → <h2> (semantic/default-style change)                                                                                                                                                                              |
| `btn.newChat`          | Header        | lineHeight | normal → 13.44px                                                                                                                                                                                                          |
| `btn.newChat`          | Header        | transition | background 0.12s, border-color 0.12s → background-color 0.12s cubic-bezier(0.2, 0, 0, 1), border-color 0.12s cubic-bezier(0.2, 0, 0, 1), color 0.12s cubic-bezier(0.2, 0, 0, 1), opacity 0.12s cubic-bezier(0.2, 0, 0, 1) |
| `btn.newChat`          | Header        | width      | 92.1875px → 91.5469px                                                                                                                                                                                                     |
| `btn.newChat`          | Header        | height     | 23px → 24px                                                                                                                                                                                                               |
| `list.pinned`          | Lists         | lineHeight | 19.5px → normal                                                                                                                                                                                                           |
| `list.pinned`          | Lists         | width      | 912px → 904px                                                                                                                                                                                                             |
| `list.pinned`          | Lists         | height     | 63.25px → 57.75px                                                                                                                                                                                                         |
| `list.pinned`          | Lists         | tag        | <div> → <ul> (semantic/default-style change)                                                                                                                                                                              |
| `row.running`          | Row (running) | lineHeight | 19.5px → normal                                                                                                                                                                                                           |
| `row.running`          | Row (running) | width      | 910px → 902px                                                                                                                                                                                                             |
| `row.running`          | Row (running) | height     | 61.25px → 55.75px                                                                                                                                                                                                         |
| `row.running`          | Row (running) | tag        | <button> → <div> (semantic/default-style change)                                                                                                                                                                          |
| `row.running.ic`       | Row (running) | lineHeight | 19.5px → normal                                                                                                                                                                                                           |
| `row.running.ic.svg`   | Row (running) | lineHeight | 19.5px → normal                                                                                                                                                                                                           |
| `row.running.ic.svg`   | Row (running) | width      | 15px → 18px                                                                                                                                                                                                               |
| `row.running.ic.svg`   | Row (running) | height     | 15px → 18px                                                                                                                                                                                                               |
| `row.running.name`     | Row (running) | lineHeight | 18.75px → normal                                                                                                                                                                                                          |
| `row.running.name`     | Row (running) | width      | 811.094px → 107.016px                                                                                                                                                                                                     |
| `row.running.name`     | Row (running) | height     | 19.75px → 15px                                                                                                                                                                                                            |
| `row.running.sub`      | Row (running) | lineHeight | 16.5px → normal                                                                                                                                                                                                           |
| `row.running.sub`      | Row (running) | width      | auto → 772.234px                                                                                                                                                                                                          |
| `row.running.sub`      | Row (running) | height     | auto → 14px                                                                                                                                                                                                               |
| `row.running.sub.mono` | Row (running) | lineHeight | 16.5px → normal                                                                                                                                                                                                           |
| `row.running.time`     | Row (running) | lineHeight | 15.75px → normal                                                                                                                                                                                                          |
| `row.running.time`     | Row (running) | width      | 18.9062px → 53.7656px                                                                                                                                                                                                     |
| `row.running.time`     | Row (running) | height     | 15.75px → 14px                                                                                                                                                                                                            |
| `sect.recent`          | Sections      | lineHeight | 14.25px → normal                                                                                                                                                                                                          |
| `sect.recent`          | Sections      | width      | 912px → 41.0469px                                                                                                                                                                                                         |
| `sect.recent`          | Sections      | height     | 14.25px → 13px                                                                                                                                                                                                            |
| `sect.recent`          | Sections      | tag        | <div> → <h2> (semantic/default-style change)                                                                                                                                                                              |
| `row.done.ic`          | Row (done)    | lineHeight | 19.5px → normal                                                                                                                                                                                                           |
| `sect.archived`        | Sections      | lineHeight | 14.25px → normal                                                                                                                                                                                                          |
| `sect.archived`        | Sections      | width      | 912px → 123.125px                                                                                                                                                                                                         |
| `sect.archived`        | Sections      | height     | 14.25px → 13px                                                                                                                                                                                                            |
| `sect.archived`        | Sections      | tag        | <div> → <h2> (semantic/default-style change)                                                                                                                                                                              |

## ⚪ INFO (7)

| Element            | Group         | Property        | Design → Live                                                                                                                                                                                                                                                                                 |
| ------------------ | ------------- | --------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `header.row`       | Header        | text            | “Pinned New chat” → “Pinned1New chat”                                                                                                                                                                                                                                                         |
| `list.pinned`      | Lists         | text            | “Launch Week ops runningStreaming the launch thread · Claude …” → “Launch Week opsrunningStreaming the launch thread · Claude S…”                                                                                                                                                             |
| `row.running`      | Row (running) | text            | “Launch Week ops runningStreaming the launch thread · Claude …” → “Launch Week opsrunningStreaming the launch thread · Claude S…”                                                                                                                                                             |
| `row.running.sub`  | Row (running) | text            | “Streaming the launch thread ·” → “·”                                                                                                                                                                                                                                                         |
| `row.running.time` | Row (running) | text            | “now” → “just now”                                                                                                                                                                                                                                                                            |
| `sect.count.pill`  | Sections      | extra-in-live   | present in live, not in design map                                                                                                                                                                                                                                                            |
| `rail.badge`       | Shell         | missing-in-live | expected: OUT OF HARNESS SCOPE, not drift: the rail is ChatShell chrome, not part of the Chats destination component this harness renders (render-live-chats.test.tsx mounts ChatsArchive alone). The live rail badge is measured by the sibling harness lib/render-live-rail-badge.test.tsx. |
