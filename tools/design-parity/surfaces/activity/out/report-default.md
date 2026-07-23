# Design-parity report — `default`

Design baseline (source of truth) vs live app, by computed style.

- Design: `surfaces/activity/out/design-default.json`
- Live: `surfaces/activity/out/live-default.json`

**Summary:** 🔴 HIGH 1 · 🟠 MEDIUM 26 · 🟡 LOW 45 · ⚪ INFO 5

## 🔴 HIGH (1)

| Element    | Group    | Property    | Design → Live                                                                                                  |
| ---------- | -------- | ----------- | -------------------------------------------------------------------------------------------------------------- |
| `row.live` | Row/live | borderColor | rgb(236, 236, 241) rgb(236, 236, 241) rgba(255, 255, 255, 0.06) rgb(236, 236, 241) → rgb(236, 236, 241) (--tx) |

## 🟠 MEDIUM (26)

| Element           | Group    | Property           | Design → Live                                              |
| ----------------- | -------- | ------------------ | ---------------------------------------------------------- |
| `page.container`  | Page     | display            | block → flex                                               |
| `page.container`  | Page     | flexDirection      | row → column                                               |
| `page.container`  | Page     | flexGrow           | flex-grow 1 → 0 (affects vertical fill / button placement) |
| `page.container`  | Page     | gap                | normal → 12px                                              |
| `page.lead`       | Page     | fontSize           | 12px → 12.48px (+0.5px)                                    |
| `page.lead`       | Page     | margin             | -2px 0px 18px 0px → 0px                                    |
| `page.lead.link`  | Page     | fontSize           | 12px → 12.48px (+0.5px)                                    |
| `page.lead.link`  | Page     | display            | inline → inline-block                                      |
| `page.lead.link`  | Page     | textDecorationLine | none → underline                                           |
| `topbar.title`    | Topbar   | fontSize           | 13.5px → 13px (-0.5px)                                     |
| `day.head`        | Grouping | margin             | 18px 0px 8px 0px → 0px                                     |
| `rowlist`         | List     | display            | flex → block                                               |
| `rowlist`         | List     | flexDirection      | column → row                                               |
| `row.live`        | Row/live | borderWidth        | 0px 0px 1px 0px → 0px                                      |
| `row.live.name`   | Row/live | display            | flex → block                                               |
| `row.live.name`   | Row/live | alignItems         | center → normal                                            |
| `row.live.name`   | Row/live | gap                | 8px → normal                                               |
| `row.live.sub`    | Row/live | display            | inline → block                                             |
| `row.live.sub`    | Row/live | margin             | 1px 0px 0px 0px → 0px                                      |
| `row.live.time`   | Row/live | fontSize           | 10.5px → 11.2px (+0.7px)                                   |
| `row.done.name`   | Row/rest | display            | flex → block                                               |
| `row.done.name`   | Row/rest | alignItems         | center → normal                                            |
| `row.done.name`   | Row/rest | gap                | 8px → normal                                               |
| `row.done.spacer` | Row/rest | display            | block → flex                                               |
| `row.done.spacer` | Row/rest | justifyContent     | normal → flex-end                                          |
| `row.done.spacer` | Row/rest | alignItems         | normal → center                                            |

## 🟡 LOW (45)

| Element            | Group    | Property    | Design → Live                                                                                                                                                                            |
| ------------------ | -------- | ----------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `page.container`   | Page     | lineHeight  | 19.5px → normal                                                                                                                                                                          |
| `page.container`   | Page     | height      | 754px → 698.438px                                                                                                                                                                        |
| `page.lead`        | Page     | lineHeight  | 19.2px → 21.216px                                                                                                                                                                        |
| `page.lead`        | Page     | width       | 544.219px → 565.984px                                                                                                                                                                    |
| `page.lead`        | Page     | height      | 38.375px → 42.4375px                                                                                                                                                                     |
| `page.lead.link`   | Page     | lineHeight  | 19.2px → 21.216px                                                                                                                                                                        |
| `page.lead.link`   | Page     | textAlign   | start → center                                                                                                                                                                           |
| `page.lead.link`   | Page     | transition  | all → background-color 0.12s cubic-bezier(0.2, 0, 0, 1), border-color 0.12s cubic-bezier(0.2, 0, 0, 1), color 0.12s cubic-bezier(0.2, 0, 0, 1), opacity 0.12s cubic-bezier(0.2, 0, 0, 1) |
| `page.lead.link`   | Page     | width       | auto → 109.281px                                                                                                                                                                         |
| `page.lead.link`   | Page     | height      | auto → 21.2188px                                                                                                                                                                         |
| `page.lead.link`   | Page     | tag         | <a> → <button> (semantic/default-style change)                                                                                                                                           |
| `topbar.title`     | Topbar   | lineHeight  | 16.2px → 15.6px                                                                                                                                                                          |
| `topbar.title`     | Topbar   | width       | 48.75px → 47.1875px                                                                                                                                                                      |
| `topbar.title`     | Topbar   | height      | 16.1875px → 15.5938px                                                                                                                                                                    |
| `topbar.sub`       | Topbar   | lineHeight  | 17.25px → 13.44px                                                                                                                                                                        |
| `topbar.sub`       | Topbar   | width       | 177.609px → 173.609px                                                                                                                                                                    |
| `topbar.sub`       | Topbar   | height      | 17.25px → 13.4375px                                                                                                                                                                      |
| `day.head`         | Grouping | lineHeight  | 15px → normal                                                                                                                                                                            |
| `day.head`         | Grouping | height      | 15px → 13px                                                                                                                                                                              |
| `day.head`         | Grouping | tag         | <div> → <h2> (semantic/default-style change)                                                                                                                                             |
| `rowlist`          | List     | lineHeight  | 19.5px → normal                                                                                                                                                                          |
| `rowlist`          | List     | height      | 187.75px → 174.25px                                                                                                                                                                      |
| `rowlist`          | List     | tag         | <div> → <ul> (semantic/default-style change)                                                                                                                                             |
| `row.live`         | Row/live | lineHeight  | 19.5px → normal                                                                                                                                                                          |
| `row.live`         | Row/live | height      | 62.25px → 56.75px                                                                                                                                                                        |
| `row.live`         | Row/live | borderStyle | none none solid none → none                                                                                                                                                              |
| `row.live`         | Row/live | tag         | <button> → <div> (semantic/default-style change)                                                                                                                                         |
| `row.live.ic`      | Row/live | lineHeight  | 19.5px → normal                                                                                                                                                                          |
| `row.live.ic.svg`  | Row/live | lineHeight  | 19.5px → normal                                                                                                                                                                          |
| `row.live.name`    | Row/live | lineHeight  | 18.75px → normal                                                                                                                                                                         |
| `row.live.name`    | Row/live | width       | 771.5px → 105.203px                                                                                                                                                                      |
| `row.live.name`    | Row/live | height      | 19.75px → 15px                                                                                                                                                                           |
| `row.live.sub`     | Row/live | lineHeight  | 16.5px → normal                                                                                                                                                                          |
| `row.live.sub`     | Row/live | width       | auto → 748.234px                                                                                                                                                                         |
| `row.live.sub`     | Row/live | height      | auto → 13px                                                                                                                                                                              |
| `row.live.time`    | Row/live | lineHeight  | 15.75px → normal                                                                                                                                                                         |
| `row.live.time`    | Row/live | width       | 31.5px → 53.7656px                                                                                                                                                                       |
| `row.live.time`    | Row/live | height      | 15.75px → 14px                                                                                                                                                                           |
| `row.live.chevron` | Row/live | lineHeight  | 19.5px → normal                                                                                                                                                                          |
| `row.done.ic.svg`  | Row/rest | lineHeight  | 19.5px → normal                                                                                                                                                                          |
| `row.done.name`    | Row/rest | lineHeight  | 18.75px → normal                                                                                                                                                                         |
| `row.done.name`    | Row/rest | width       | 770.5px → 180.359px                                                                                                                                                                      |
| `row.done.name`    | Row/rest | height      | 19.75px → 15px                                                                                                                                                                           |
| `row.done.spacer`  | Row/rest | lineHeight  | 19.5px → normal                                                                                                                                                                          |
| `chip.paused`      | Status   | width       | 55.8125px → 74.7031px                                                                                                                                                                    |

## ⚪ INFO (5)

| Element         | Group    | Property        | Design → Live                                                                                                                                                                                                                                                                                                                                 |
| --------------- | -------- | --------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `rail.badge`    | Rail     | missing-in-live | expected: OUT OF SCOPE for this surface's harness, not a claim about the app: the badge belongs to AppRail (shell chrome), which the Activity render deliberately does not mount — the sibling `surfaces/rail-badge/` audit owns it. Reported as INFO here so the anchor stays traceable; do NOT read it as 'the live app lacks a run badge'. |
| `rowlist`       | List     | text            | “Launch Week ops running4 apps · 7 steps · awaiting 1 approva…” → “Launch Week opsrunning4 apps · 7 steps · awaiting 1 approval…”                                                                                                                                                                                                             |
| `row.live`      | Row/live | text            | “Launch Week ops running4 apps · 7 steps · awaiting 1 approva…” → “Launch Week opsrunning4 apps · 7 steps · awaiting 1 approval…”                                                                                                                                                                                                             |
| `row.live.time` | Row/live | text            | “11:44” → “11:44 AM”                                                                                                                                                                                                                                                                                                                          |
| `chip.paused`   | Status   | text            | “paused” → “needs you”                                                                                                                                                                                                                                                                                                                        |
