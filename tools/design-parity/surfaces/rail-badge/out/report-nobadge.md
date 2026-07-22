# Design-parity report — `nobadge`

Design baseline (source of truth) vs live app, by computed style.

- Design: `surfaces/rail-badge/out/design-nobadge.json`
- Live: `surfaces/rail-badge/out/live-nobadge.json`

**Summary:** 🔴 HIGH 5 · 🟠 MEDIUM 41 · 🟡 LOW 7 · ⚪ INFO 13

## 🔴 HIGH (5)

| Element           | Group       | Property        | Design → Live                                                                                                  |
| ----------------- | ----------- | --------------- | -------------------------------------------------------------------------------------------------------------- |
| `shell.body.grid` | Shell frame | backgroundColor | rgba(0, 0, 0, 0) (transparent) → rgb(9, 9, 11)                                                                 |
| `rail.brand`      | Rail        | color           | rgb(236, 236, 241) (--tx) → rgb(95, 178, 236) (--accent/--sky)                                                 |
| `rail.brand.mark` | Rail        | color           | rgb(236, 236, 241) (--tx) → rgb(95, 178, 236) (--accent/--sky)                                                 |
| `rail.foot`       | Rail foot   | borderColor     | rgb(236, 236, 241) (--tx) → rgba(255, 255, 255, 0.06) rgb(236, 236, 241) rgb(236, 236, 241) rgb(236, 236, 241) |
| `rail.me`         | Rail foot   | borderColor     | rgba(255, 255, 255, 0.1) (--line2) → rgb(212, 212, 219) (--tx2)                                                |

## 🟠 MEDIUM (41)

| Element              | Group             | Property       | Design → Live           |
| -------------------- | ----------------- | -------------- | ----------------------- |
| `shell.body.grid`    | Shell frame       | fontSize       | 13px → 13.6px (+0.6px)  |
| `rail.container`     | Rail              | fontSize       | 13px → 13.6px (+0.6px)  |
| `rail.container`     | Rail              | gap            | 2px → normal            |
| `rail.brand`         | Rail              | display        | grid → flex             |
| `rail.brand`         | Rail              | justifyContent | normal → center         |
| `rail.brand`         | Rail              | padding        | 1px 6px → 0px           |
| `rail.brand`         | Rail              | margin         | 0px 0px 10px 0px → 0px  |
| `rail.item.run`      | Destination items | display        | grid → flex             |
| `rail.item.run`      | Destination items | justifyContent | normal → center         |
| `rail.item.run`      | Destination items | padding        | 1px 6px → 0px           |
| `rail.item.chats`    | Destination items | display        | grid → flex             |
| `rail.item.chats`    | Destination items | justifyContent | normal → center         |
| `rail.item.chats`    | Destination items | padding        | 1px 6px → 0px           |
| `rail.item.projects` | Destination items | display        | grid → flex             |
| `rail.item.projects` | Destination items | justifyContent | normal → center         |
| `rail.item.projects` | Destination items | padding        | 1px 6px → 0px           |
| `rail.item.activity` | Destination items | display        | grid → flex             |
| `rail.item.activity` | Destination items | justifyContent | normal → center         |
| `rail.item.activity` | Destination items | padding        | 1px 6px → 0px           |
| `rail.item.tools`    | Destination items | display        | grid → flex             |
| `rail.item.tools`    | Destination items | justifyContent | normal → center         |
| `rail.item.tools`    | Destination items | padding        | 1px 6px → 0px           |
| `rail.item.skills`   | Destination items | display        | grid → flex             |
| `rail.item.skills`   | Destination items | justifyContent | normal → center         |
| `rail.item.skills`   | Destination items | padding        | 1px 6px → 0px           |
| `rail.item.active`   | Active state      | display        | grid → flex             |
| `rail.item.active`   | Active state      | justifyContent | normal → center         |
| `rail.item.active`   | Active state      | padding        | 1px 6px → 0px           |
| `rail.foot`          | Rail foot         | fontSize       | 13px → 13.6px (+0.6px)  |
| `rail.foot`          | Rail foot         | padding        | 0px → 8px 0px 0px 0px   |
| `rail.foot`          | Rail foot         | margin         | 455px 0px 0px 0px → 0px |
| `rail.foot`          | Rail foot         | borderWidth    | 0px → 1px 0px 0px 0px   |
| `rail.foot`          | Rail foot         | gap            | 5px → 6px               |
| `rail.foot.settings` | Rail foot         | display        | grid → flex             |
| `rail.foot.settings` | Rail foot         | justifyContent | normal → center         |
| `rail.foot.settings` | Rail foot         | padding        | 1px 6px → 0px           |
| `rail.me`            | Rail foot         | display        | grid → flex             |
| `rail.me`            | Rail foot         | justifyContent | normal → center         |
| `rail.me`            | Rail foot         | padding        | 1px 6px → 0px           |
| `rail.me`            | Rail foot         | borderWidth    | 1px → 0px               |
| `rail.me`            | Rail foot         | borderRadius   | 50% → 999px             |

## 🟡 LOW (7)

| Element           | Group       | Property    | Design → Live               |
| ----------------- | ----------- | ----------- | --------------------------- |
| `shell.body.grid` | Shell frame | lineHeight  | 19.5px → normal             |
| `shell.body.grid` | Shell frame | width       | 1218px → 1220px             |
| `rail.container`  | Rail        | lineHeight  | 19.5px → normal             |
| `rail.foot`       | Rail foot   | lineHeight  | 19.5px → normal             |
| `rail.foot`       | Rail foot   | height      | 65px → 75px                 |
| `rail.foot`       | Rail foot   | borderStyle | none → solid none none none |
| `rail.me`         | Rail foot   | borderStyle | solid → none                |

## ⚪ INFO (13)

| Element                | Group             | Property        | Design → Live                                                                                                                                                                                                                                                                                                                                                                                              |
| ---------------------- | ----------------- | --------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `shell.body.grid`      | Shell frame       | text            | “RunChatsProjectsActivityToolsSkillsSettingsS” → “[data-component="app-rail"] .rail-btn { transition: backgrou…”                                                                                                                                                                                                                                                                                           |
| `rail.container`       | Rail              | text            | “RunChatsProjectsActivityToolsSkillsSettingsS” → “[data-component="app-rail"] .rail-btn { transition: backgrou…”                                                                                                                                                                                                                                                                                           |
| `rail.item.run`        | Destination items | text            | “Run” → “”                                                                                                                                                                                                                                                                                                                                                                                                 |
| `rail.item.run.label`  | Destination items | missing-in-live | expected: the design ships a `.rl` text span that is `display:none` (copilot.css:69) — zero visual footprint, present only as dead markup from an earlier labelled-rail iteration. The live rail carries the same affordance on the button itself via `title` + `aria-label` (AppRail.tsx:262-263), which is strictly better for assistive tech. Absence is intentional and unmeasurable; INFO, not drift. |
| `rail.item.chats`      | Destination items | text            | “Chats” → “”                                                                                                                                                                                                                                                                                                                                                                                               |
| `rail.item.projects`   | Destination items | text            | “Projects” → “”                                                                                                                                                                                                                                                                                                                                                                                            |
| `rail.item.activity`   | Destination items | text            | “Activity” → “”                                                                                                                                                                                                                                                                                                                                                                                            |
| `rail.item.tools`      | Destination items | text            | “Tools” → “”                                                                                                                                                                                                                                                                                                                                                                                               |
| `rail.item.skills`     | Destination items | text            | “Skills” → “”                                                                                                                                                                                                                                                                                                                                                                                              |
| `rail.item.active`     | Active state      | text            | “Run” → “”                                                                                                                                                                                                                                                                                                                                                                                                 |
| `rail.item.active.bar` | Active state      | extra-in-live   | present in live, not in design map                                                                                                                                                                                                                                                                                                                                                                         |
| `rail.foot`            | Rail foot         | text            | “SettingsS” → “S”                                                                                                                                                                                                                                                                                                                                                                                          |
| `rail.foot.settings`   | Rail foot         | text            | “Settings” → “”                                                                                                                                                                                                                                                                                                                                                                                            |
