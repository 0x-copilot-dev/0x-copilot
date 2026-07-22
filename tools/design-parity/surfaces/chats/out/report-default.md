# Design-parity report — chats · `default`

Design baseline (source of truth) vs live app, by computed style.

- Design: `surfaces/chats/out/design-default.json`
- Live: `surfaces/chats/out/live-default.json`

**Summary:** 🔴 HIGH 17 · 🟠 MEDIUM 59 · 🟡 LOW 64 · ⚪ INFO 10

## 🔴 HIGH (17)

| Element                | Group         | Property        | Design → Live                                                           |
| ---------------------- | ------------- | --------------- | ----------------------------------------------------------------------- |
| `topbar.title`         | Shell         | missing-in-live | present in design, ABSENT in live                                       |
| `row.running.ic`       | Row (running) | color           | rgb(87, 199, 133) (--jade) → rgb(152, 152, 159) (--mut)                 |
| `row.running.ic`       | Row (running) | backgroundColor | rgb(29, 29, 35) (--panel3) → rgba(0, 0, 0, 0) (transparent)             |
| `row.running.ic`       | Row (running) | borderColor     | rgb(87, 199, 133) (--jade) → rgb(152, 152, 159) (--mut)                 |
| `chip.running`         | Status pills  | fontFamily      | typeface class changed (mono → sans)                                    |
| `chip.running`         | Status pills  | backgroundColor | rgba(0, 0, 0, 0) (transparent) → rgb(26, 47, 35)                        |
| `chip.running`         | Status pills  | borderColor     | rgba(87, 199, 133, 0.25) → rgb(87, 199, 133) (--jade)                   |
| `chip.running.dot`     | Status pills  | fontFamily      | typeface class changed (mono → sans)                                    |
| `row.running.sub.mono` | Row (running) | color           | rgb(100, 100, 109) (--mut2) → rgb(152, 152, 159) (--mut)                |
| `row.running.sub.mono` | Row (running) | borderColor     | rgb(100, 100, 109) (--mut2) → rgb(152, 152, 159) (--mut)                |
| `row.done.ic`          | Row (done)    | backgroundColor | rgb(29, 29, 35) (--panel3) → rgba(0, 0, 0, 0) (transparent)             |
| `chip.paused`          | Status pills  | fontFamily      | typeface class changed (mono → sans)                                    |
| `chip.paused`          | Status pills  | backgroundColor | rgba(0, 0, 0, 0) (transparent) → rgb(50, 38, 21)                        |
| `chip.paused`          | Status pills  | borderColor     | rgba(232, 180, 94, 0.25) → rgb(232, 180, 94)                            |
| `chip.archived`        | Status pills  | fontFamily      | typeface class changed (mono → sans)                                    |
| `chip.archived`        | Status pills  | backgroundColor | rgba(0, 0, 0, 0) (transparent) → rgb(22, 22, 26) (--panel2)             |
| `chip.archived`        | Status pills  | borderColor     | rgba(255, 255, 255, 0.1) (--line2) → rgba(255, 255, 255, 0.06) (--line) |

## 🟠 MEDIUM (59)

| Element              | Group         | Property       | Design → Live                                              |
| -------------------- | ------------- | -------------- | ---------------------------------------------------------- |
| `page.container`     | Page          | fontSize       | 13px → 13.6px (+0.6px)                                     |
| `page.container`     | Page          | display        | block → flex                                               |
| `page.container`     | Page          | flexDirection  | row → column                                               |
| `page.container`     | Page          | flexGrow       | flex-grow 1 → 0 (affects vertical fill / button placement) |
| `page.container`     | Page          | padding        | 20px 24px 40px 24px → 24px 28px 96px 28px                  |
| `page.container`     | Page          | margin         | 0px → 0px 110px                                            |
| `page.container`     | Page          | gap            | normal → 20px                                              |
| `page.lead`          | Page          | fontSize       | 12px → 12.48px (+0.5px)                                    |
| `page.lead`          | Page          | margin         | -2px 0px 18px 0px → 0px                                    |
| `header.row`         | Header        | fontSize       | 13px → 13.6px (+0.6px)                                     |
| `header.row`         | Header        | margin         | 0px 0px 14px 0px → 0px                                     |
| `header.row`         | Header        | gap            | normal → 8px                                               |
| `sect.pinned`        | Sections      | fontSize       | 9.5px → 11.2px (+1.7px)                                    |
| `sect.pinned`        | Sections      | fontWeight     | 400 → 600                                                  |
| `btn.newChat`        | Header        | fontWeight     | 600 → 500                                                  |
| `btn.newChat`        | Header        | justifyContent | normal → center                                            |
| `btn.newChat`        | Header        | padding        | 4px 9px → 4px 8.8px                                        |
| `btn.newChat`        | Header        | margin         | 0px 0px 0px 778.766px → 0px                                |
| `list.pinned`        | Lists         | fontSize       | 13px → 13.6px (+0.6px)                                     |
| `list.pinned`        | Lists         | display        | flex → block                                               |
| `list.pinned`        | Lists         | flexDirection  | column → row                                               |
| `row.running`        | Row (running) | fontSize       | 13px → 13.6px (+0.6px)                                     |
| `row.running`        | Row (running) | padding        | 11px 14px → 10px 12px                                      |
| `row.running.ic`     | Row (running) | fontSize       | 13px → 13.6px (+0.6px)                                     |
| `row.running.ic`     | Row (running) | display        | grid → flex                                                |
| `row.running.ic`     | Row (running) | justifyContent | normal → center                                            |
| `row.running.ic`     | Row (running) | borderRadius   | 7px → 8px                                                  |
| `row.running.ic.svg` | Row (running) | fontSize       | 13px → 13.6px (+0.6px)                                     |
| `row.running.name`   | Row (running) | fontWeight     | 500 → 600                                                  |
| `row.running.name`   | Row (running) | display        | flex → block                                               |
| `row.running.name`   | Row (running) | alignItems     | center → normal                                            |
| `row.running.name`   | Row (running) | gap            | 8px → normal                                               |
| `chip.running`       | Status pills  | fontSize       | 10.5px → 11.2px (+0.7px)                                   |
| `chip.running`       | Status pills  | fontWeight     | 500 → 600                                                  |
| `chip.running`       | Status pills  | padding        | 1px 8px → 0px 8px                                          |
| `chip.running`       | Status pills  | gap            | 5px → 6px                                                  |
| `chip.running.dot`   | Status pills  | fontSize       | 10.5px → 11.2px (+0.7px)                                   |
| `chip.running.dot`   | Status pills  | fontWeight     | 500 → 600                                                  |
| `row.running.sub`    | Row (running) | display        | inline → block                                             |
| `row.running.sub`    | Row (running) | margin         | 1px 0px 0px 0px → 0px                                      |
| `row.running.time`   | Row (running) | fontSize       | 10.5px → 11.2px (+0.7px)                                   |
| `sect.recent`        | Sections      | fontSize       | 9.5px → 11.2px (+1.7px)                                    |
| `sect.recent`        | Sections      | fontWeight     | 400 → 600                                                  |
| `sect.recent`        | Sections      | margin         | 22px 0px 10px 0px → 0px                                    |
| `row.done.ic`        | Row (done)    | fontSize       | 13px → 13.6px (+0.6px)                                     |
| `row.done.ic`        | Row (done)    | display        | grid → flex                                                |
| `row.done.ic`        | Row (done)    | justifyContent | normal → center                                            |
| `row.done.ic`        | Row (done)    | borderRadius   | 7px → 8px                                                  |
| `chip.paused`        | Status pills  | fontSize       | 10.5px → 11.2px (+0.7px)                                   |
| `chip.paused`        | Status pills  | fontWeight     | 500 → 600                                                  |
| `chip.paused`        | Status pills  | padding        | 1px 8px → 0px 8px                                          |
| `chip.paused`        | Status pills  | gap            | 5px → 6px                                                  |
| `sect.archived`      | Sections      | fontSize       | 9.5px → 11.2px (+1.7px)                                    |
| `sect.archived`      | Sections      | fontWeight     | 400 → 600                                                  |
| `sect.archived`      | Sections      | margin         | 22px 0px 10px 0px → 0px                                    |
| `chip.archived`      | Status pills  | fontSize       | 10.5px → 11.2px (+0.7px)                                   |
| `chip.archived`      | Status pills  | fontWeight     | 500 → 600                                                  |
| `chip.archived`      | Status pills  | padding        | 1px 8px → 0px 8px                                          |
| `chip.archived`      | Status pills  | gap            | 5px → 6px                                                  |

## 🟡 LOW (64)

| Element                | Group         | Property      | Design → Live                                    |
| ---------------------- | ------------- | ------------- | ------------------------------------------------ |
| `page.container`       | Page          | lineHeight    | 19.5px → normal                                  |
| `page.container`       | Page          | height        | 754px → 762.219px                                |
| `page.lead`            | Page          | lineHeight    | 19.2px → 21.216px                                |
| `page.lead`            | Page          | width         | 544.219px → 565.984px                            |
| `page.lead`            | Page          | height        | 19.1875px → 21.2188px                            |
| `header.row`           | Header        | lineHeight    | 19.5px → normal                                  |
| `header.row`           | Header        | width         | 912px → 904px                                    |
| `header.row`           | Header        | height        | 23px → 24px                                      |
| `sect.pinned`          | Sections      | lineHeight    | 14.25px → normal                                 |
| `sect.pinned`          | Sections      | width         | 41.0469px → 48.3906px                            |
| `sect.pinned`          | Sections      | height        | 14.25px → 14px                                   |
| `sect.pinned`          | Sections      | tag           | <div> → <h2> (semantic/default-style change)     |
| `btn.newChat`          | Header        | lineHeight    | normal → 13.44px                                 |
| `btn.newChat`          | Header        | width         | 92.1875px → 90.6094px                            |
| `btn.newChat`          | Header        | height        | 23px → 24px                                      |
| `list.pinned`          | Lists         | lineHeight    | 19.5px → normal                                  |
| `list.pinned`          | Lists         | width         | 912px → 904px                                    |
| `list.pinned`          | Lists         | height        | 63.25px → 58px                                   |
| `list.pinned`          | Lists         | tag           | <div> → <ul> (semantic/default-style change)     |
| `row.running`          | Row (running) | lineHeight    | 19.5px → normal                                  |
| `row.running`          | Row (running) | width         | 910px → 902px                                    |
| `row.running`          | Row (running) | height        | 61.25px → 56px                                   |
| `row.running`          | Row (running) | tag           | <button> → <div> (semantic/default-style change) |
| `row.running.ic`       | Row (running) | lineHeight    | 19.5px → normal                                  |
| `row.running.ic.svg`   | Row (running) | lineHeight    | 19.5px → normal                                  |
| `row.running.ic.svg`   | Row (running) | width         | 15px → 18px                                      |
| `row.running.ic.svg`   | Row (running) | height        | 15px → 18px                                      |
| `row.running.name`     | Row (running) | lineHeight    | 18.75px → normal                                 |
| `row.running.name`     | Row (running) | width         | 811.094px → 107.016px                            |
| `row.running.name`     | Row (running) | height        | 19.75px → 15px                                   |
| `chip.running`         | Status pills  | lineHeight    | 15.75px → normal                                 |
| `chip.running`         | Status pills  | letterSpacing | normal → 0.3px                                   |
| `chip.running`         | Status pills  | textTransform | none → uppercase                                 |
| `chip.running`         | Status pills  | width         | 73.1094px → 85.875px                             |
| `chip.running`         | Status pills  | height        | 19.75px → 20px                                   |
| `chip.running.dot`     | Status pills  | lineHeight    | 15.75px → normal                                 |
| `chip.running.dot`     | Status pills  | letterSpacing | normal → 0.3px                                   |
| `chip.running.dot`     | Status pills  | textTransform | none → uppercase                                 |
| `row.running.sub`      | Row (running) | lineHeight    | 16.5px → normal                                  |
| `row.running.sub`      | Row (running) | width         | auto → 772.234px                                 |
| `row.running.sub`      | Row (running) | height        | auto → 14px                                      |
| `row.running.sub.mono` | Row (running) | lineHeight    | 16.5px → normal                                  |
| `row.running.time`     | Row (running) | lineHeight    | 15.75px → normal                                 |
| `row.running.time`     | Row (running) | width         | 18.9062px → 53.7656px                            |
| `row.running.time`     | Row (running) | height        | 15.75px → 14px                                   |
| `sect.recent`          | Sections      | lineHeight    | 14.25px → normal                                 |
| `sect.recent`          | Sections      | width         | 912px → 48.3906px                                |
| `sect.recent`          | Sections      | height        | 14.25px → 14px                                   |
| `sect.recent`          | Sections      | tag           | <div> → <h2> (semantic/default-style change)     |
| `row.done.ic`          | Row (done)    | lineHeight    | 19.5px → normal                                  |
| `chip.paused`          | Status pills  | lineHeight    | 15.75px → normal                                 |
| `chip.paused`          | Status pills  | letterSpacing | normal → 0.3px                                   |
| `chip.paused`          | Status pills  | textTransform | none → uppercase                                 |
| `chip.paused`          | Status pills  | width         | 55.8125px → 65.7969px                            |
| `chip.paused`          | Status pills  | height        | 19.75px → 20px                                   |
| `sect.archived`        | Sections      | lineHeight    | 14.25px → normal                                 |
| `sect.archived`        | Sections      | width         | 912px → 145.156px                                |
| `sect.archived`        | Sections      | height        | 14.25px → 14px                                   |
| `sect.archived`        | Sections      | tag           | <div> → <h2> (semantic/default-style change)     |
| `chip.archived`        | Status pills  | lineHeight    | 15.75px → normal                                 |
| `chip.archived`        | Status pills  | letterSpacing | normal → 0.3px                                   |
| `chip.archived`        | Status pills  | textTransform | none → uppercase                                 |
| `chip.archived`        | Status pills  | width         | 68.4062px → 79.7031px                            |
| `chip.archived`        | Status pills  | height        | 19.75px → 20px                                   |

## ⚪ INFO (10)

| Element            | Group         | Property        | Design → Live                                                                                                                                                                                                                                                                                 |
| ------------------ | ------------- | --------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `header.row`       | Header        | text            | “Pinned New chat” → “Pinned1New chat”                                                                                                                                                                                                                                                         |
| `list.pinned`      | Lists         | text            | “Launch Week ops runningStreaming the launch thread · Claude …” → “Launch Week opsRunningStreaming the launch thread · Claude S…”                                                                                                                                                             |
| `row.running`      | Row (running) | text            | “Launch Week ops runningStreaming the launch thread · Claude …” → “Launch Week opsRunningStreaming the launch thread · Claude S…”                                                                                                                                                             |
| `chip.running`     | Status pills  | text            | “running” → “Running”                                                                                                                                                                                                                                                                         |
| `row.running.sub`  | Row (running) | text            | “Streaming the launch thread ·” → “·”                                                                                                                                                                                                                                                         |
| `row.running.time` | Row (running) | text            | “now” → “just now”                                                                                                                                                                                                                                                                            |
| `chip.paused`      | Status pills  | text            | “paused” → “Paused”                                                                                                                                                                                                                                                                           |
| `chip.archived`    | Status pills  | text            | “archived” → “Archived”                                                                                                                                                                                                                                                                       |
| `sect.count.pill`  | Sections      | extra-in-live   | present in live, not in design map                                                                                                                                                                                                                                                            |
| `rail.badge`       | Shell         | missing-in-live | expected: OUT OF HARNESS SCOPE, not drift: the rail is ChatShell chrome, not part of the Chats destination component this harness renders (render-live-chats.test.tsx mounts ChatsArchive alone). The live rail badge is measured by the sibling harness lib/render-live-rail-badge.test.tsx. |
