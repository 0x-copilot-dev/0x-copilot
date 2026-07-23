# Design-parity report — composer · `closed`

Design baseline (source of truth) vs live app, by computed style.

- Design: `surfaces/composer/out/design-closed.json`
- Live: `surfaces/composer/out/live-closed.json`

**Summary:** 🔴 HIGH 28 · 🟠 MEDIUM 49 · 🟡 LOW 35 · ⚪ INFO 29

## 🔴 HIGH (28)

| Element           | Group                    | Property        | Design → Live                                                                                                        |
| ----------------- | ------------------------ | --------------- | -------------------------------------------------------------------------------------------------------------------- |
| `cmp.frame`       | A · Composer frame       | borderColor     | rgba(255, 255, 255, 0.1) (--line2) → rgba(255, 255, 255, 0.06) (--line)                                              |
| `cmp.textarea`    | A · Composer frame       | backgroundColor | rgba(0, 0, 0, 0) (transparent) → rgb(13, 13, 16)                                                                     |
| `cmp.textarea`    | A · Composer frame       | borderColor     | rgb(236, 236, 241) (--tx) → rgba(255, 255, 255, 0.06) (--line)                                                       |
| `cmp.attach.icon` | A · Composer bottom row  | missing-in-live | present in design, ABSENT in live                                                                                    |
| `cmp.model.pill`  | A · Model pill (trigger) | color           | rgb(152, 152, 159) (--mut) → rgb(236, 236, 241) (--tx)                                                               |
| `cmp.model.pill`  | A · Model pill (trigger) | backgroundColor | rgba(0, 0, 0, 0) (transparent) → rgb(17, 17, 20) (--panel)                                                           |
| `cmp.model.pill`  | A · Model pill (trigger) | borderColor     | rgba(0, 0, 0, 0) (transparent) → rgba(255, 255, 255, 0.06) (--line)                                                  |
| `cmp.model.dot`   | A · Model pill (trigger) | color           | rgb(152, 152, 159) (--mut) → rgb(236, 236, 241) (--tx)                                                               |
| `cmp.model.dot`   | A · Model pill (trigger) | backgroundColor | rgb(217, 119, 87) → rgb(95, 178, 236) (--accent/--sky)                                                               |
| `cmp.model.dot`   | A · Model pill (trigger) | borderColor     | rgb(152, 152, 159) (--mut) → rgb(236, 236, 241) (--tx)                                                               |
| `cmp.model.label` | A · Model pill (trigger) | color           | rgb(152, 152, 159) (--mut) → rgb(236, 236, 241) (--tx)                                                               |
| `cmp.model.label` | A · Model pill (trigger) | borderColor     | rgb(152, 152, 159) (--mut) → rgb(236, 236, 241) (--tx)                                                               |
| `cmp.tools.pill`  | A · Tools pill           | fontFamily      | typeface class changed (mono → sans)                                                                                 |
| `cmp.tools.pill`  | A · Tools pill           | fontSize        | 10px → 12.48px (+2.5px)                                                                                              |
| `cmp.tools.pill`  | A · Tools pill           | borderColor     | rgba(0, 0, 0, 0) (transparent) → rgba(255, 255, 255, 0.06) (--line)                                                  |
| `cmp.tools.icon`  | A · Tools pill           | fontFamily      | typeface class changed (mono → sans)                                                                                 |
| `cmp.tools.icon`  | A · Tools pill           | fontSize        | 10px → 13.6px (+3.6px)                                                                                               |
| `cmp.tools.label` | A · Tools pill           | fontFamily      | typeface class changed (mono → sans)                                                                                 |
| `cmp.tools.label` | A · Tools pill           | fontSize        | 10px → 12.48px (+2.5px)                                                                                              |
| `cmp.tools.count` | A · Tools pill           | fontFamily      | typeface class changed (mono → sans)                                                                                 |
| `cmp.tools.count` | A · Tools pill           | color           | rgb(100, 100, 109) (--mut2) → rgb(95, 178, 236) (--accent/--sky)                                                     |
| `cmp.tools.count` | A · Tools pill           | backgroundColor | rgba(0, 0, 0, 0) (transparent) → color(srgb 0.372549 0.698039 0.92549 / 0.18)                                        |
| `cmp.tools.count` | A · Tools pill           | borderColor     | rgb(100, 100, 109) (--mut2) → rgb(95, 178, 236) (--accent/--sky)                                                     |
| `cmp.hint`        | A · Hint                 | fontFamily      | typeface class changed (mono → sans)                                                                                 |
| `cmp.hint`        | A · Hint                 | fontSize        | 9px → 11.2px (+2.2px)                                                                                                |
| `cmp.hint`        | A · Hint                 | borderColor     | rgb(100, 100, 109) (--mut2) → color(srgb 1 1 1 / 0.0235294) rgb(100, 100, 109) rgb(100, 100, 109) rgb(100, 100, 109) |
| `cmp.send.btn`    | A · Send                 | borderColor     | rgb(8, 19, 29) (--accent-ink) → rgb(95, 178, 236) (--accent/--sky)                                                   |
| `cmp.send.icon`   | A · Send                 | missing-in-live | present in design, ABSENT in live                                                                                    |

## 🟠 MEDIUM (49)

| Element           | Group                    | Property       | Design → Live                         |
| ----------------- | ------------------------ | -------------- | ------------------------------------- |
| `cmp.frame`       | A · Composer frame       | fontSize       | 13px → 13.6px (+0.6px)                |
| `cmp.frame`       | A · Composer frame       | display        | block → flex                          |
| `cmp.frame`       | A · Composer frame       | flexDirection  | row → column                          |
| `cmp.frame`       | A · Composer frame       | padding        | 0px → 10px                            |
| `cmp.frame`       | A · Composer frame       | margin         | 0px → 8px 0px 0px 0px                 |
| `cmp.frame`       | A · Composer frame       | borderRadius   | 11px → 12px                           |
| `cmp.frame`       | A · Composer frame       | gap            | normal → 6px                          |
| `cmp.textarea`    | A · Composer frame       | fontSize       | 12.5px → 13px (+0.5px)                |
| `cmp.textarea`    | A · Composer frame       | padding        | 10px 12px 4px 12px → 10px 12px        |
| `cmp.textarea`    | A · Composer frame       | borderWidth    | 0px → 1px                             |
| `cmp.textarea`    | A · Composer frame       | borderRadius   | 0px → 8px                             |
| `cmp.row`         | A · Composer bottom row  | fontSize       | 13px → 13.6px (+0.6px)                |
| `cmp.row`         | A · Composer bottom row  | justifyContent | normal → space-between                |
| `cmp.row`         | A · Composer bottom row  | padding        | 6px 8px 8px 8px → 0px                 |
| `cmp.row`         | A · Composer bottom row  | gap            | 5px → 8px                             |
| `cmp.attach.btn`  | A · Composer bottom row  | fontSize       | 13.3333px → 14px (+0.7px)             |
| `cmp.attach.btn`  | A · Composer bottom row  | display        | grid → flex                           |
| `cmp.attach.btn`  | A · Composer bottom row  | justifyContent | normal → center                       |
| `cmp.attach.btn`  | A · Composer bottom row  | padding        | 1px 6px → 4px                         |
| `cmp.attach.btn`  | A · Composer bottom row  | borderRadius   | 7px → 8px                             |
| `cmp.model.pill`  | A · Model pill (trigger) | fontWeight     | 400 → 500                             |
| `cmp.model.pill`  | A · Model pill (trigger) | display        | flex → inline-flex                    |
| `cmp.model.pill`  | A · Model pill (trigger) | borderRadius   | 7px → 8px                             |
| `cmp.model.pill`  | A · Model pill (trigger) | gap            | 6px → 4px                             |
| `cmp.model.dot`   | A · Model pill (trigger) | fontWeight     | 400 → 500                             |
| `cmp.model.dot`   | A · Model pill (trigger) | borderRadius   | 50% → 999px                           |
| `cmp.model.label` | A · Model pill (trigger) | fontWeight     | 400 → 500                             |
| `cmp.model.caret` | A · Model pill (trigger) | fontSize       | 10px → 11.2px (+1.2px)                |
| `cmp.model.caret` | A · Model pill (trigger) | fontWeight     | 400 → 500                             |
| `cmp.tools.pill`  | A · Tools pill           | padding        | 0px 8px → 4px 10px                    |
| `cmp.tools.pill`  | A · Tools pill           | borderRadius   | 7px → 999px                           |
| `cmp.tools.count` | A · Tools pill           | fontSize       | 10px → 11.2px (+1.2px)                |
| `cmp.tools.count` | A · Tools pill           | fontWeight     | 400 → 600                             |
| `cmp.tools.count` | A · Tools pill           | display        | block → flex                          |
| `cmp.tools.count` | A · Tools pill           | justifyContent | normal → center                       |
| `cmp.tools.count` | A · Tools pill           | alignItems     | normal → center                       |
| `cmp.tools.count` | A · Tools pill           | padding        | 0px → 0px 4px                         |
| `cmp.tools.count` | A · Tools pill           | borderRadius   | 0px → 999px                           |
| `cmp.hint`        | A · Hint                 | display        | block → flex                          |
| `cmp.hint`        | A · Hint                 | alignItems     | normal → center                       |
| `cmp.hint`        | A · Hint                 | padding        | 0px 3px 0px 0px → 4.8px 12px 4px 12px |
| `cmp.hint`        | A · Hint                 | margin         | 0px 0px 0px 220.531px → 0px           |
| `cmp.hint`        | A · Hint                 | borderWidth    | 0px → 1px 0px 0px 0px                 |
| `cmp.hint`        | A · Hint                 | gap            | normal → 8px                          |
| `cmp.send.btn`    | A · Send                 | fontWeight     | 400 → 600                             |
| `cmp.send.btn`    | A · Send                 | display        | grid → flex                           |
| `cmp.send.btn`    | A · Send                 | justifyContent | normal → center                       |
| `cmp.send.btn`    | A · Send                 | padding        | 1px 6px → 4px                         |
| `cmp.send.btn`    | A · Send                 | borderWidth    | 0px → 1px                             |

## 🟡 LOW (35)

| Element           | Group                    | Property    | Design → Live                                  |
| ----------------- | ------------------------ | ----------- | ---------------------------------------------- |
| `cmp.frame`       | A · Composer frame       | lineHeight  | 19.5px → normal                                |
| `cmp.frame`       | A · Composer frame       | height      | 77.375px → 168.734px                           |
| `cmp.textarea`    | A · Composer frame       | width       | 638px → 618px                                  |
| `cmp.textarea`    | A · Composer frame       | height      | 33.375px → 78.5px                              |
| `cmp.textarea`    | A · Composer frame       | borderStyle | none → solid                                   |
| `cmp.row`         | A · Composer bottom row  | lineHeight  | 19.5px → normal                                |
| `cmp.row`         | A · Composer bottom row  | width       | 638px → 618px                                  |
| `cmp.row`         | A · Composer bottom row  | height      | 42px → 32px                                    |
| `cmp.attach.btn`  | A · Composer bottom row  | width       | 26px → 28px                                    |
| `cmp.attach.btn`  | A · Composer bottom row  | height      | 26px → 28px                                    |
| `cmp.model.pill`  | A · Model pill (trigger) | width       | 149px → 146px                                  |
| `cmp.model.label` | A · Model pill (trigger) | lineHeight  | normal → 10px                                  |
| `cmp.model.label` | A · Model pill (trigger) | height      | 13px → 10px                                    |
| `cmp.model.caret` | A · Model pill (trigger) | width       | 11px → 12px                                    |
| `cmp.model.caret` | A · Model pill (trigger) | height      | 11px → 12px                                    |
| `cmp.tools.pill`  | A · Tools pill           | lineHeight  | normal → 12.48px                               |
| `cmp.tools.pill`  | A · Tools pill           | width       | 89px → 89.1562px                               |
| `cmp.tools.icon`  | A · Tools pill           | lineHeight  | normal → 13.6px                                |
| `cmp.tools.icon`  | A · Tools pill           | width       | 11px → 8.20312px                               |
| `cmp.tools.icon`  | A · Tools pill           | height      | 11px → 13.5938px                               |
| `cmp.tools.icon`  | A · Tools pill           | tag         | <svg> → <span> (semantic/default-style change) |
| `cmp.tools.label` | A · Tools pill           | lineHeight  | normal → 12.48px                               |
| `cmp.tools.label` | A · Tools pill           | width       | 30px → 30.9531px                               |
| `cmp.tools.label` | A · Tools pill           | height      | 13px → 12.4844px                               |
| `cmp.tools.count` | A · Tools pill           | lineHeight  | normal → 11.2px                                |
| `cmp.tools.count` | A · Tools pill           | width       | 18px → 16px                                    |
| `cmp.tools.count` | A · Tools pill           | height      | 13px → 16px                                    |
| `cmp.hint`        | A · Hint                 | width       | 89.4688px → 618px                              |
| `cmp.hint`        | A · Hint                 | height      | 13.5px → 24.2344px                             |
| `cmp.hint`        | A · Hint                 | borderStyle | none → solid none none none                    |
| `cmp.hint`        | A · Hint                 | tag         | <span> → <div> (semantic/default-style change) |
| `cmp.send.btn`    | A · Send                 | opacity     | 0.35 → 0.4                                     |
| `cmp.send.btn`    | A · Send                 | width       | 28px → 32px                                    |
| `cmp.send.btn`    | A · Send                 | height      | 28px → 32px                                    |
| `cmp.send.btn`    | A · Send                 | borderStyle | none → solid                                   |

## ⚪ INFO (29)

| Element              | Group                                          | Property      | Design → Live                                                                                          |
| -------------------- | ---------------------------------------------- | ------------- | ------------------------------------------------------------------------------------------------------ |
| `cmp.frame`          | A · Composer frame                             | text          | “Claude Sonnet 4.5Tools7/7⏎ send · ⇧⏎ line” → “+⚙Tools1Claude Sonnet 4.5↑/ skillsSources cited inline” |
| `cmp.row`            | A · Composer bottom row                        | text          | “Claude Sonnet 4.5Tools7/7⏎ send · ⇧⏎ line” → “+⚙Tools1Claude Sonnet 4.5↑”                             |
| `cmp.attach.btn`     | A · Composer bottom row                        | text          | “” → “+”                                                                                               |
| `cmp.tools.pill`     | A · Tools pill                                 | text          | “Tools7/7” → “⚙Tools1”                                                                                 |
| `cmp.tools.icon`     | A · Tools pill                                 | text          | “” → “⚙”                                                                                               |
| `cmp.tools.count`    | A · Tools pill                                 | text          | “7/7” → “1”                                                                                            |
| `cmp.hint`           | A · Hint                                       | text          | “⏎ send · ⇧⏎ line” → “/ skillsSources cited inline”                                                    |
| `cmp.send.btn`       | A · Send                                       | text          | “” → “↑”                                                                                               |
| `mic-button`         | C · Live-only                                  | extra-in-live | present in live, not in design map                                                                     |
| `mic-icon`           | C · Live-only                                  | extra-in-live | present in live, not in design map                                                                     |
| `tools-spacer`       | C · Live-only                                  | extra-in-live | present in live, not in design map                                                                     |
| `plus-root`          | C · Live-only wrappers                         | extra-in-live | present in live, not in design map                                                                     |
| `tools-cluster`      | C · Live-only wrappers                         | extra-in-live | present in live, not in design map                                                                     |
| `tools-trigger-wrap` | C · Live-only wrappers                         | extra-in-live | present in live, not in design map                                                                     |
| `model-pill-root`    | C · Live-only wrappers                         | extra-in-live | present in live, not in design map                                                                     |
| `send-wrap`          | C · Live-only wrappers                         | extra-in-live | present in live, not in design map                                                                     |
| `bottombar-slot`     | C · Live-only wrappers                         | extra-in-live | present in live, not in design map                                                                     |
| `hint-slot`          | C · Live-only wrappers                         | extra-in-live | present in live, not in design map                                                                     |
| `hint-skills`        | C · Hint internals (live-only)                 | extra-in-live | present in live, not in design map                                                                     |
| `hint-kbd`           | C · Hint internals (live-only)                 | extra-in-live | present in live, not in design map                                                                     |
| `hint-grow`          | C · Hint internals (live-only)                 | extra-in-live | present in live, not in design map                                                                     |
| `hint-meta`          | C · Hint internals (live-only)                 | extra-in-live | present in live, not in design map                                                                     |
| `hero-title`         | C · FTUE frame (covered by surfaces/run-empty) | extra-in-live | present in live, not in design map                                                                     |
| `chips-row`          | C · FTUE frame (covered by surfaces/run-empty) | extra-in-live | present in live, not in design map                                                                     |
| `chip-wallet`        | C · FTUE frame (covered by surfaces/run-empty) | extra-in-live | present in live, not in design map                                                                     |
| `chip-thread`        | C · FTUE frame (covered by surfaces/run-empty) | extra-in-live | present in live, not in design map                                                                     |
| `chip-csv`           | C · FTUE frame (covered by surfaces/run-empty) | extra-in-live | present in live, not in design map                                                                     |
| `chip-icon`          | C · FTUE frame (covered by surfaces/run-empty) | extra-in-live | present in live, not in design map                                                                     |
| `chip-label`         | C · FTUE frame (covered by surfaces/run-empty) | extra-in-live | present in live, not in design map                                                                     |
