# Design-parity report — `default`

Design baseline (source of truth) vs live app, by computed style.

- Design: `surfaces/activity/out/design-default.json`
- Live: `surfaces/activity/out/live-default.json`

**Summary:** 🔴 HIGH 6 · 🟠 MEDIUM 34 · 🟡 LOW 51 · ⚪ INFO 7

## 🔴 HIGH (6)

| Element            | Group    | Property        | Design → Live                                                                                                  |
| ------------------ | -------- | --------------- | -------------------------------------------------------------------------------------------------------------- |
| `topbar.sub`       | Topbar   | missing-in-live | present in design, ABSENT in live                                                                              |
| `row.live`         | Row/live | borderColor     | rgb(236, 236, 241) rgb(236, 236, 241) rgba(255, 255, 255, 0.06) rgb(236, 236, 241) → rgb(236, 236, 241) (--tx) |
| `row.live.ic`      | Row/live | color           | rgb(87, 199, 133) (--jade) → rgb(152, 152, 159) (--mut)                                                        |
| `row.live.ic`      | Row/live | backgroundColor | rgb(29, 29, 35) (--panel3) → rgba(0, 0, 0, 0) (transparent)                                                    |
| `row.live.chevron` | Row/live | missing-in-live | present in design, ABSENT in live                                                                              |
| `row.done.spacer`  | Row/rest | missing-in-live | present in design, ABSENT in live                                                                              |

## 🟠 MEDIUM (34)

| Element           | Group    | Property           | Design → Live                                              |
| ----------------- | -------- | ------------------ | ---------------------------------------------------------- |
| `page.container`  | Page     | display            | block → flex                                               |
| `page.container`  | Page     | flexDirection      | row → column                                               |
| `page.container`  | Page     | flexGrow           | flex-grow 1 → 0 (affects vertical fill / button placement) |
| `page.container`  | Page     | padding            | 20px 24px 40px 24px → 16px 20px 32px 20px                  |
| `page.container`  | Page     | margin             | 0px → 0px 110px                                            |
| `page.container`  | Page     | gap                | normal → 12px                                              |
| `page.lead`       | Page     | fontSize           | 12px → 12.48px (+0.5px)                                    |
| `page.lead`       | Page     | margin             | -2px 0px 18px 0px → 0px                                    |
| `page.lead.link`  | Page     | fontSize           | 12px → 12.48px (+0.5px)                                    |
| `page.lead.link`  | Page     | display            | inline → inline-block                                      |
| `page.lead.link`  | Page     | textDecorationLine | none → underline                                           |
| `topbar.title`    | Topbar   | fontSize           | 13.5px → 13px (-0.5px)                                     |
| `day.head`        | Grouping | fontSize           | 10px → 11.2px (+1.2px)                                     |
| `day.head`        | Grouping | fontWeight         | 400 → 600                                                  |
| `day.head`        | Grouping | margin             | 18px 0px 8px 0px → 0px                                     |
| `rowlist`         | List     | display            | flex → block                                               |
| `rowlist`         | List     | flexDirection      | column → row                                               |
| `row.live`        | Row/live | padding            | 11px 14px → 10px 12px                                      |
| `row.live`        | Row/live | borderWidth        | 0px 0px 1px 0px → 0px                                      |
| `row.live.ic`     | Row/live | display            | grid → flex                                                |
| `row.live.ic`     | Row/live | justifyContent     | normal → center                                            |
| `row.live.ic`     | Row/live | borderRadius       | 7px → 8px                                                  |
| `row.live.name`   | Row/live | fontWeight         | 500 → 600                                                  |
| `row.live.name`   | Row/live | display            | flex → block                                               |
| `row.live.name`   | Row/live | alignItems         | center → normal                                            |
| `row.live.name`   | Row/live | gap                | 8px → normal                                               |
| `row.live.sub`    | Row/live | display            | inline → block                                             |
| `row.live.sub`    | Row/live | margin             | 1px 0px 0px 0px → 0px                                      |
| `row.live.time`   | Row/live | fontSize           | 10.5px → 11.2px (+0.7px)                                   |
| `row.done.ic.svg` | Row/rest | display            | block → inline                                             |
| `row.done.name`   | Row/rest | fontWeight         | 500 → 600                                                  |
| `row.done.name`   | Row/rest | display            | flex → block                                               |
| `row.done.name`   | Row/rest | alignItems         | center → normal                                            |
| `row.done.name`   | Row/rest | gap                | 8px → normal                                               |

## 🟡 LOW (51)

| Element           | Group    | Property      | Design → Live                                                                                                                                                                            |
| ----------------- | -------- | ------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `page.container`  | Page     | lineHeight    | 19.5px → normal                                                                                                                                                                          |
| `page.container`  | Page     | height        | 754px → 652.219px                                                                                                                                                                        |
| `page.lead`       | Page     | lineHeight    | 19.2px → 21.216px                                                                                                                                                                        |
| `page.lead`       | Page     | width         | 544.219px → 565.984px                                                                                                                                                                    |
| `page.lead`       | Page     | height        | 38.375px → 21.2188px                                                                                                                                                                     |
| `page.lead.link`  | Page     | lineHeight    | 19.2px → 21.216px                                                                                                                                                                        |
| `page.lead.link`  | Page     | textAlign     | start → center                                                                                                                                                                           |
| `page.lead.link`  | Page     | transition    | all → background-color 0.12s cubic-bezier(0.2, 0, 0, 1), border-color 0.12s cubic-bezier(0.2, 0, 0, 1), color 0.12s cubic-bezier(0.2, 0, 0, 1), opacity 0.12s cubic-bezier(0.2, 0, 0, 1) |
| `page.lead.link`  | Page     | width         | auto → 321.969px                                                                                                                                                                         |
| `page.lead.link`  | Page     | height        | auto → 21.2188px                                                                                                                                                                         |
| `page.lead.link`  | Page     | tag           | <a> → <button> (semantic/default-style change)                                                                                                                                           |
| `topbar.title`    | Topbar   | lineHeight    | 16.2px → 15.6px                                                                                                                                                                          |
| `topbar.title`    | Topbar   | letterSpacing | -0.135px → normal                                                                                                                                                                        |
| `topbar.title`    | Topbar   | width         | 48.75px → 882px                                                                                                                                                                          |
| `topbar.title`    | Topbar   | height        | 16.1875px → 15.5938px                                                                                                                                                                    |
| `topbar.title`    | Topbar   | tag           | <h1> → <span> (semantic/default-style change)                                                                                                                                            |
| `day.head`        | Grouping | lineHeight    | 15px → normal                                                                                                                                                                            |
| `day.head`        | Grouping | letterSpacing | normal → 0.4px                                                                                                                                                                           |
| `day.head`        | Grouping | textTransform | none → uppercase                                                                                                                                                                         |
| `day.head`        | Grouping | width         | 912px → 920px                                                                                                                                                                            |
| `day.head`        | Grouping | height        | 15px → 14px                                                                                                                                                                              |
| `day.head`        | Grouping | tag           | <div> → <h2> (semantic/default-style change)                                                                                                                                             |
| `rowlist`         | List     | lineHeight    | 19.5px → normal                                                                                                                                                                          |
| `rowlist`         | List     | width         | 912px → 920px                                                                                                                                                                            |
| `rowlist`         | List     | height        | 187.75px → 168.25px                                                                                                                                                                      |
| `rowlist`         | List     | tag           | <div> → <ul> (semantic/default-style change)                                                                                                                                             |
| `row.live`        | Row/live | lineHeight    | 19.5px → normal                                                                                                                                                                          |
| `row.live`        | Row/live | width         | 910px → 918px                                                                                                                                                                            |
| `row.live`        | Row/live | height        | 62.25px → 54.75px                                                                                                                                                                        |
| `row.live`        | Row/live | borderStyle   | none none solid none → none                                                                                                                                                              |
| `row.live`        | Row/live | tag           | <button> → <div> (semantic/default-style change)                                                                                                                                         |
| `row.live.ic`     | Row/live | lineHeight    | 19.5px → normal                                                                                                                                                                          |
| `row.live.ic.svg` | Row/live | lineHeight    | 19.5px → normal                                                                                                                                                                          |
| `row.live.ic.svg` | Row/live | width         | 15px → 18px                                                                                                                                                                              |
| `row.live.ic.svg` | Row/live | height        | 15px → 18px                                                                                                                                                                              |
| `row.live.name`   | Row/live | lineHeight    | 18.75px → normal                                                                                                                                                                         |
| `row.live.name`   | Row/live | width         | 771.484px → 107.016px                                                                                                                                                                    |
| `row.live.name`   | Row/live | height        | 19.75px → 15px                                                                                                                                                                           |
| `row.live.sub`    | Row/live | lineHeight    | 16.5px → normal                                                                                                                                                                          |
| `row.live.sub`    | Row/live | width         | auto → 794.953px                                                                                                                                                                         |
| `row.live.sub`    | Row/live | height        | auto → 13px                                                                                                                                                                              |
| `row.live.time`   | Row/live | lineHeight    | 15.75px → normal                                                                                                                                                                         |
| `row.live.time`   | Row/live | width         | 31.5156px → 47.0469px                                                                                                                                                                    |
| `row.live.time`   | Row/live | height        | 15.75px → 14px                                                                                                                                                                           |
| `row.done.ic.svg` | Row/rest | lineHeight    | 19.5px → normal                                                                                                                                                                          |
| `row.done.ic.svg` | Row/rest | width         | 15px → 18px                                                                                                                                                                              |
| `row.done.ic.svg` | Row/rest | height        | 15px → 18px                                                                                                                                                                              |
| `row.done.name`   | Row/rest | lineHeight    | 18.75px → normal                                                                                                                                                                         |
| `row.done.name`   | Row/rest | width         | 770.484px → 184.953px                                                                                                                                                                    |
| `row.done.name`   | Row/rest | height        | 19.75px → 15px                                                                                                                                                                           |
| `row.done.chip`   | Row/rest | width         | 43.2188px → 43.2031px                                                                                                                                                                    |

## ⚪ INFO (7)

| Element          | Group    | Property        | Design → Live                                                                                                                                                                                                                                                                                                                                 |
| ---------------- | -------- | --------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `page.container` | Page     | text            | “Everything the agent has done, most recent first. This is th…” → “Everything the agent has done. Retention, export, and delete…”                                                                                                                                                                                                             |
| `page.lead`      | Page     | text            | “Everything the agent has done, most recent first. This is th…” → “Everything the agent has done. Retention, export, and delete…”                                                                                                                                                                                                             |
| `page.lead.link` | Page     | text            | “Settings → Privacy” → “Retention, export, and delete live in Settings → Privacy.”                                                                                                                                                                                                                                                            |
| `rail.badge`     | Rail     | missing-in-live | expected: OUT OF SCOPE for this surface's harness, not a claim about the app: the badge belongs to AppRail (shell chrome), which the Activity render deliberately does not mount — the sibling `surfaces/rail-badge/` audit owns it. Reported as INFO here so the anchor stays traceable; do NOT read it as 'the live app lacks a run badge'. |
| `rowlist`        | List     | text            | “Launch Week ops running4 apps · 7 steps · awaiting 1 approva…” → “Launch Week opsrunning4 apps · 7 steps · awaiting 1 approval…”                                                                                                                                                                                                             |
| `row.live`       | Row/live | text            | “Launch Week ops running4 apps · 7 steps · awaiting 1 approva…” → “Launch Week opsrunning4 apps · 7 steps · awaiting 1 approval…”                                                                                                                                                                                                             |
| `row.live.time`  | Row/live | text            | “11:44” → “46m ago”                                                                                                                                                                                                                                                                                                                           |
