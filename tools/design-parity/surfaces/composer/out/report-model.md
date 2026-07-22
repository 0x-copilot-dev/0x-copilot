# Design-parity report — composer · `model`

Design baseline (source of truth) vs live app, by computed style.

- Design: `surfaces/composer/out/design-model.json`
- Live: `surfaces/composer/out/live-model.json`

**Summary:** 🔴 HIGH 64 · 🟠 MEDIUM 106 · 🟡 LOW 88 · ⚪ INFO 43

## 🔴 HIGH (64)

| Element                   | Group                    | Property        | Design → Live                                                                                                        |
| ------------------------- | ------------------------ | --------------- | -------------------------------------------------------------------------------------------------------------------- |
| `cmp.frame`               | A · Composer frame       | borderColor     | rgba(255, 255, 255, 0.1) (--line2) → rgba(255, 255, 255, 0.06) (--line)                                              |
| `cmp.textarea`            | A · Composer frame       | backgroundColor | rgba(0, 0, 0, 0) (transparent) → rgb(13, 13, 16)                                                                     |
| `cmp.textarea`            | A · Composer frame       | borderColor     | rgb(236, 236, 241) (--tx) → rgba(255, 255, 255, 0.06) (--line)                                                       |
| `cmp.attach.icon`         | A · Composer bottom row  | missing-in-live | present in design, ABSENT in live                                                                                    |
| `cmp.model.pill`          | A · Model pill (trigger) | backgroundColor | rgb(22, 22, 26) (--panel2) → rgb(17, 17, 20) (--panel)                                                               |
| `cmp.model.pill`          | A · Model pill (trigger) | borderColor     | rgba(255, 255, 255, 0.06) (--line) → rgb(95, 178, 236) (--accent/--sky)                                              |
| `cmp.model.dot`           | A · Model pill (trigger) | backgroundColor | rgb(217, 119, 87) → rgb(95, 178, 236) (--accent/--sky)                                                               |
| `cmp.model.caret`         | A · Model pill (trigger) | color           | rgb(236, 236, 241) (--tx) → rgb(152, 152, 159) (--mut)                                                               |
| `cmp.model.caret`         | A · Model pill (trigger) | borderColor     | rgb(236, 236, 241) (--tx) → rgb(152, 152, 159) (--mut)                                                               |
| `cmp.model.pill.open`     | A · Model pill (trigger) | backgroundColor | rgb(22, 22, 26) (--panel2) → rgb(17, 17, 20) (--panel)                                                               |
| `cmp.model.pill.open`     | A · Model pill (trigger) | borderColor     | rgba(255, 255, 255, 0.06) (--line) → rgb(95, 178, 236) (--accent/--sky)                                              |
| `cmp.tools.pill`          | A · Tools pill           | fontFamily      | typeface class changed (mono → sans)                                                                                 |
| `cmp.tools.pill`          | A · Tools pill           | fontSize        | 10px → 12.48px (+2.5px)                                                                                              |
| `cmp.tools.pill`          | A · Tools pill           | borderColor     | rgba(0, 0, 0, 0) (transparent) → rgba(255, 255, 255, 0.06) (--line)                                                  |
| `cmp.tools.icon`          | A · Tools pill           | fontFamily      | typeface class changed (mono → sans)                                                                                 |
| `cmp.tools.icon`          | A · Tools pill           | fontSize        | 10px → 13.6px (+3.6px)                                                                                               |
| `cmp.tools.label`         | A · Tools pill           | fontFamily      | typeface class changed (mono → sans)                                                                                 |
| `cmp.tools.label`         | A · Tools pill           | fontSize        | 10px → 12.48px (+2.5px)                                                                                              |
| `cmp.tools.count`         | A · Tools pill           | fontFamily      | typeface class changed (mono → sans)                                                                                 |
| `cmp.tools.count`         | A · Tools pill           | color           | rgb(100, 100, 109) (--mut2) → rgb(95, 178, 236) (--accent/--sky)                                                     |
| `cmp.tools.count`         | A · Tools pill           | backgroundColor | rgba(0, 0, 0, 0) (transparent) → color(srgb 0.372549 0.698039 0.92549 / 0.18)                                        |
| `cmp.tools.count`         | A · Tools pill           | borderColor     | rgb(100, 100, 109) (--mut2) → rgb(95, 178, 236) (--accent/--sky)                                                     |
| `cmp.hint`                | A · Hint                 | fontFamily      | typeface class changed (mono → sans)                                                                                 |
| `cmp.hint`                | A · Hint                 | fontSize        | 9px → 11.2px (+2.2px)                                                                                                |
| `cmp.hint`                | A · Hint                 | borderColor     | rgb(100, 100, 109) (--mut2) → color(srgb 1 1 1 / 0.0235294) rgb(100, 100, 109) rgb(100, 100, 109) rgb(100, 100, 109) |
| `cmp.send.btn`            | A · Send                 | borderColor     | rgb(8, 19, 29) (--accent-ink) → rgb(95, 178, 236) (--accent/--sky)                                                   |
| `cmp.send.icon`           | A · Send                 | missing-in-live | present in design, ABSENT in live                                                                                    |
| `pop.scrim`               | B · Popover frame        | missing-in-live | present in design, ABSENT in live                                                                                    |
| `pop.frame`               | B · Popover frame        | backgroundColor | rgb(17, 17, 20) (--panel) → rgb(13, 13, 16)                                                                          |
| `pop.frame`               | B · Popover frame        | borderColor     | rgba(255, 255, 255, 0.1) (--line2) → rgba(255, 255, 255, 0.06) (--line)                                              |
| `pop.header`              | B · Popover header       | missing-in-live | present in design, ABSENT in live                                                                                    |
| `pop.header.meta`         | B · Popover header       | missing-in-live | present in design, ABSENT in live                                                                                    |
| `pop.list`                | B · Popover frame        | backgroundColor | rgba(0, 0, 0, 0) (transparent) → rgb(13, 13, 16)                                                                     |
| `pop.list`                | B · Popover frame        | borderColor     | rgb(236, 236, 241) (--tx) → rgba(255, 255, 255, 0.06) (--line)                                                       |
| `pop.group.keys`          | B · Group headings       | fontFamily      | typeface class changed (mono → sans)                                                                                 |
| `pop.group.keys`          | B · Group headings       | fontSize        | 8.5px → 11.2px (+2.7px)                                                                                              |
| `pop.group.keys`          | B · Group headings       | color           | rgb(100, 100, 109) (--mut2) → rgb(152, 152, 159) (--mut)                                                             |
| `pop.group.keys`          | B · Group headings       | borderColor     | rgb(100, 100, 109) (--mut2) → rgb(152, 152, 159) (--mut)                                                             |
| `pop.group.local`         | B · Group headings       | fontFamily      | typeface class changed (mono → sans)                                                                                 |
| `pop.group.local`         | B · Group headings       | fontSize        | 8.5px → 11.2px (+2.7px)                                                                                              |
| `pop.group.local`         | B · Group headings       | color           | rgb(100, 100, 109) (--mut2) → rgb(152, 152, 159) (--mut)                                                             |
| `pop.group.local`         | B · Group headings       | borderColor     | rgb(100, 100, 109) (--mut2) → rgb(152, 152, 159) (--mut)                                                             |
| `pop.row`                 | B · Rows                 | backgroundColor | rgba(0, 0, 0, 0) (transparent) → rgb(22, 22, 26) (--panel2)                                                          |
| `pop.row.badge`           | B · Rows                 | color           | rgb(212, 212, 219) (--tx2) → rgb(152, 152, 159) (--mut)                                                              |
| `pop.row.badge`           | B · Rows                 | backgroundColor | rgb(29, 29, 35) (--panel3) → rgb(22, 22, 26) (--panel2)                                                              |
| `pop.row.badge`           | B · Rows                 | borderColor     | rgb(212, 212, 219) (--tx2) → rgb(152, 152, 159) (--mut)                                                              |
| `pop.row.name`            | B · Rows                 | fontSize        | 12px → 14px (+2.0px)                                                                                                 |
| `pop.row.sub`             | B · Rows                 | color           | rgb(100, 100, 109) (--mut2) → rgb(152, 152, 159) (--mut)                                                             |
| `pop.row.sub`             | B · Rows                 | borderColor     | rgb(100, 100, 109) (--mut2) → rgb(152, 152, 159) (--mut)                                                             |
| `pop.row.radio`           | B · Rows                 | color           | rgb(8, 19, 29) (--accent-ink) → rgb(255, 255, 255)                                                                   |
| `pop.row.radioCheck`      | B · Rows                 | color           | rgb(8, 19, 29) (--accent-ink) → rgb(255, 255, 255)                                                                   |
| `pop.row.radioCheck`      | B · Rows                 | borderColor     | rgb(8, 19, 29) (--accent-ink) → rgb(255, 255, 255)                                                                   |
| `pop.rowSelected`         | B · Rows                 | backgroundColor | rgba(0, 0, 0, 0) (transparent) → rgb(22, 22, 26) (--panel2)                                                          |
| `pop.rowUnselected.radio` | B · Rows                 | color           | rgb(8, 19, 29) (--accent-ink) → rgb(255, 255, 255)                                                                   |
| `pop.rowUnselected.radio` | B · Rows                 | borderColor     | rgba(255, 255, 255, 0.18) (--line3) → rgba(255, 255, 255, 0.1) (--line2)                                             |
| `pop.rowLocal.badge`      | B · Rows (local)         | color           | rgb(212, 212, 219) (--tx2) → rgb(152, 152, 159) (--mut)                                                              |
| `pop.rowLocal.badge`      | B · Rows (local)         | backgroundColor | rgb(29, 29, 35) (--panel3) → rgb(22, 22, 26) (--panel2)                                                              |
| `pop.rowLocal.badge`      | B · Rows (local)         | borderColor     | rgb(212, 212, 219) (--tx2) → rgb(152, 152, 159) (--mut)                                                              |
| `pop.rowLocal.badgeIcon`  | B · Rows (local)         | missing-in-live | present in design, ABSENT in live                                                                                    |
| `pop.rowLocal.sub`        | B · Rows (local)         | color           | rgb(100, 100, 109) (--mut2) → rgb(152, 152, 159) (--mut)                                                             |
| `pop.rowLocal.sub`        | B · Rows (local)         | borderColor     | rgb(100, 100, 109) (--mut2) → rgb(152, 152, 159) (--mut)                                                             |
| `pop.footer`              | B · Footer               | backgroundColor | rgb(13, 13, 16) → rgb(9, 9, 11)                                                                                      |
| `pop.footer.spacer`       | B · Footer               | missing-in-live | present in design, ABSENT in live                                                                                    |
| `pop.footer.linkLocal`    | B · Footer               | missing-in-live | present in design, ABSENT in live                                                                                    |

## 🟠 MEDIUM (106)

| Element                   | Group                    | Property       | Design → Live                                              |
| ------------------------- | ------------------------ | -------------- | ---------------------------------------------------------- |
| `cmp.frame`               | A · Composer frame       | fontSize       | 13px → 13.6px (+0.6px)                                     |
| `cmp.frame`               | A · Composer frame       | display        | block → flex                                               |
| `cmp.frame`               | A · Composer frame       | flexDirection  | row → column                                               |
| `cmp.frame`               | A · Composer frame       | padding        | 0px → 10px                                                 |
| `cmp.frame`               | A · Composer frame       | margin         | 0px → 8px 0px 0px 0px                                      |
| `cmp.frame`               | A · Composer frame       | borderRadius   | 11px → 12px                                                |
| `cmp.frame`               | A · Composer frame       | gap            | normal → 6px                                               |
| `cmp.textarea`            | A · Composer frame       | fontSize       | 12.5px → 13px (+0.5px)                                     |
| `cmp.textarea`            | A · Composer frame       | padding        | 10px 12px 4px 12px → 10px 12px                             |
| `cmp.textarea`            | A · Composer frame       | borderWidth    | 0px → 1px                                                  |
| `cmp.textarea`            | A · Composer frame       | borderRadius   | 0px → 8px                                                  |
| `cmp.row`                 | A · Composer bottom row  | fontSize       | 13px → 13.6px (+0.6px)                                     |
| `cmp.row`                 | A · Composer bottom row  | justifyContent | normal → space-between                                     |
| `cmp.row`                 | A · Composer bottom row  | padding        | 6px 8px 8px 8px → 0px                                      |
| `cmp.row`                 | A · Composer bottom row  | gap            | 5px → 8px                                                  |
| `cmp.attach.btn`          | A · Composer bottom row  | fontSize       | 13.3333px → 14px (+0.7px)                                  |
| `cmp.attach.btn`          | A · Composer bottom row  | display        | grid → flex                                                |
| `cmp.attach.btn`          | A · Composer bottom row  | justifyContent | normal → center                                            |
| `cmp.attach.btn`          | A · Composer bottom row  | padding        | 1px 6px → 4px                                              |
| `cmp.attach.btn`          | A · Composer bottom row  | borderRadius   | 7px → 8px                                                  |
| `cmp.model.pill`          | A · Model pill (trigger) | fontWeight     | 400 → 500                                                  |
| `cmp.model.pill`          | A · Model pill (trigger) | display        | flex → inline-flex                                         |
| `cmp.model.pill`          | A · Model pill (trigger) | borderRadius   | 7px → 8px                                                  |
| `cmp.model.pill`          | A · Model pill (trigger) | gap            | 6px → 4px                                                  |
| `cmp.model.dot`           | A · Model pill (trigger) | fontWeight     | 400 → 500                                                  |
| `cmp.model.dot`           | A · Model pill (trigger) | borderRadius   | 50% → 999px                                                |
| `cmp.model.label`         | A · Model pill (trigger) | fontWeight     | 400 → 500                                                  |
| `cmp.model.caret`         | A · Model pill (trigger) | fontSize       | 10px → 11.2px (+1.2px)                                     |
| `cmp.model.caret`         | A · Model pill (trigger) | fontWeight     | 400 → 500                                                  |
| `cmp.model.pill.open`     | A · Model pill (trigger) | fontWeight     | 400 → 500                                                  |
| `cmp.model.pill.open`     | A · Model pill (trigger) | display        | flex → inline-flex                                         |
| `cmp.model.pill.open`     | A · Model pill (trigger) | borderRadius   | 7px → 8px                                                  |
| `cmp.model.pill.open`     | A · Model pill (trigger) | gap            | 6px → 4px                                                  |
| `cmp.tools.pill`          | A · Tools pill           | padding        | 0px 8px → 4px 10px                                         |
| `cmp.tools.pill`          | A · Tools pill           | borderRadius   | 7px → 999px                                                |
| `cmp.tools.count`         | A · Tools pill           | fontSize       | 10px → 11.2px (+1.2px)                                     |
| `cmp.tools.count`         | A · Tools pill           | fontWeight     | 400 → 600                                                  |
| `cmp.tools.count`         | A · Tools pill           | display        | block → flex                                               |
| `cmp.tools.count`         | A · Tools pill           | justifyContent | normal → center                                            |
| `cmp.tools.count`         | A · Tools pill           | alignItems     | normal → center                                            |
| `cmp.tools.count`         | A · Tools pill           | padding        | 0px → 0px 4px                                              |
| `cmp.tools.count`         | A · Tools pill           | borderRadius   | 0px → 999px                                                |
| `cmp.hint`                | A · Hint                 | display        | block → flex                                               |
| `cmp.hint`                | A · Hint                 | alignItems     | normal → center                                            |
| `cmp.hint`                | A · Hint                 | padding        | 0px 3px 0px 0px → 4.8px 12px 4px 12px                      |
| `cmp.hint`                | A · Hint                 | margin         | 0px 0px 0px 220.531px → 0px                                |
| `cmp.hint`                | A · Hint                 | borderWidth    | 0px → 1px 0px 0px 0px                                      |
| `cmp.hint`                | A · Hint                 | gap            | normal → 8px                                               |
| `cmp.send.btn`            | A · Send                 | fontWeight     | 400 → 600                                                  |
| `cmp.send.btn`            | A · Send                 | display        | grid → flex                                                |
| `cmp.send.btn`            | A · Send                 | justifyContent | normal → center                                            |
| `cmp.send.btn`            | A · Send                 | padding        | 1px 6px → 4px                                              |
| `cmp.send.btn`            | A · Send                 | borderWidth    | 0px → 1px                                                  |
| `pop.frame`               | B · Popover frame        | fontSize       | 13px → 14px (+1.0px)                                       |
| `pop.frame`               | B · Popover frame        | display        | block → grid                                               |
| `pop.frame`               | B · Popover frame        | padding        | 0px → 4px                                                  |
| `pop.frame`               | B · Popover frame        | borderRadius   | 10px → 8px                                                 |
| `pop.frame`               | B · Popover frame        | gap            | normal → 4px                                               |
| `pop.list`                | B · Popover frame        | fontSize       | 13px → 14px (+1.0px)                                       |
| `pop.list`                | B · Popover frame        | display        | block → grid                                               |
| `pop.list`                | B · Popover frame        | padding        | 0px 5px 5px 5px → 4px                                      |
| `pop.list`                | B · Popover frame        | borderWidth    | 0px → 1px                                                  |
| `pop.list`                | B · Popover frame        | borderRadius   | 0px → 8px                                                  |
| `pop.list`                | B · Popover frame        | gap            | normal → 4px                                               |
| `pop.group.keys`          | B · Group headings       | fontWeight     | 400 → 600                                                  |
| `pop.group.local`         | B · Group headings       | fontWeight     | 400 → 600                                                  |
| `pop.row`                 | B · Rows                 | fontSize       | 13px → 14px (+1.0px)                                       |
| `pop.row`                 | B · Rows                 | justifyContent | normal → space-between                                     |
| `pop.row`                 | B · Rows                 | padding        | 6px → 8px 10px                                             |
| `pop.row`                 | B · Rows                 | borderRadius   | 7px → 6px                                                  |
| `pop.row`                 | B · Rows                 | gap            | 9px → 8px                                                  |
| `pop.row.badge`           | B · Rows                 | fontSize       | 10px → 11.2px (+1.2px)                                     |
| `pop.row.meta`            | B · Rows                 | fontSize       | 13px → 14px (+1.0px)                                       |
| `pop.row.meta`            | B · Rows                 | display        | block → flex                                               |
| `pop.row.meta`            | B · Rows                 | flexDirection  | row → column                                               |
| `pop.row.meta`            | B · Rows                 | flexGrow       | flex-grow 1 → 0 (affects vertical fill / button placement) |
| `pop.row.meta`            | B · Rows                 | gap            | normal → 2px                                               |
| `pop.row.name`            | B · Rows                 | fontWeight     | 500 → 400                                                  |
| `pop.row.name`            | B · Rows                 | gap            | 6px → 4px                                                  |
| `pop.row.nameText`        | B · Rows                 | fontSize       | 12px → 13.6px (+1.6px)                                     |
| `pop.row.nameText`        | B · Rows                 | fontWeight     | 500 → 400                                                  |
| `pop.row.sub`             | B · Rows                 | fontSize       | 9.5px → 11.2px (+1.7px)                                    |
| `pop.row.sub`             | B · Rows                 | display        | inline → block                                             |
| `pop.row.radio`           | B · Rows                 | fontSize       | 13px → 14px (+1.0px)                                       |
| `pop.row.radioCheck`      | B · Rows                 | fontSize       | 13px → 14px (+1.0px)                                       |
| `pop.rowSelected`         | B · Rows                 | fontSize       | 13px → 14px (+1.0px)                                       |
| `pop.rowSelected`         | B · Rows                 | justifyContent | normal → space-between                                     |
| `pop.rowSelected`         | B · Rows                 | padding        | 6px → 8px 10px                                             |
| `pop.rowSelected`         | B · Rows                 | borderRadius   | 7px → 6px                                                  |
| `pop.rowSelected`         | B · Rows                 | gap            | 9px → 8px                                                  |
| `pop.rowUnselected`       | B · Rows                 | fontSize       | 13px → 14px (+1.0px)                                       |
| `pop.rowUnselected`       | B · Rows                 | justifyContent | normal → space-between                                     |
| `pop.rowUnselected`       | B · Rows                 | padding        | 6px → 8px 10px                                             |
| `pop.rowUnselected`       | B · Rows                 | borderRadius   | 7px → 6px                                                  |
| `pop.rowUnselected`       | B · Rows                 | gap            | 9px → 8px                                                  |
| `pop.rowUnselected.radio` | B · Rows                 | fontSize       | 13px → 14px (+1.0px)                                       |
| `pop.rowLocal`            | B · Rows (local)         | fontSize       | 13px → 14px (+1.0px)                                       |
| `pop.rowLocal`            | B · Rows (local)         | justifyContent | normal → space-between                                     |
| `pop.rowLocal`            | B · Rows (local)         | padding        | 6px → 8px 10px                                             |
| `pop.rowLocal`            | B · Rows (local)         | borderRadius   | 7px → 6px                                                  |
| `pop.rowLocal`            | B · Rows (local)         | gap            | 9px → 8px                                                  |
| `pop.rowLocal.badge`      | B · Rows (local)         | fontSize       | 10px → 11.2px (+1.2px)                                     |
| `pop.rowLocal.sub`        | B · Rows (local)         | fontSize       | 9.5px → 11.2px (+1.7px)                                    |
| `pop.rowLocal.sub`        | B · Rows (local)         | display        | inline → block                                             |
| `pop.footer`              | B · Footer               | fontSize       | 13px → 14px (+1.0px)                                       |
| `pop.footer.linkAdd`      | B · Footer               | fontSize       | 9.5px → 11.2px (+1.7px)                                    |

## 🟡 LOW (88)

| Element                   | Group                    | Property      | Design → Live                                  |
| ------------------------- | ------------------------ | ------------- | ---------------------------------------------- |
| `cmp.frame`               | A · Composer frame       | lineHeight    | 19.5px → normal                                |
| `cmp.frame`               | A · Composer frame       | height        | 77.375px → 168.734px                           |
| `cmp.textarea`            | A · Composer frame       | width         | 638px → 618px                                  |
| `cmp.textarea`            | A · Composer frame       | height        | 33.375px → 78.5px                              |
| `cmp.textarea`            | A · Composer frame       | borderStyle   | none → solid                                   |
| `cmp.row`                 | A · Composer bottom row  | lineHeight    | 19.5px → normal                                |
| `cmp.row`                 | A · Composer bottom row  | width         | 638px → 618px                                  |
| `cmp.row`                 | A · Composer bottom row  | height        | 42px → 32px                                    |
| `cmp.attach.btn`          | A · Composer bottom row  | width         | 26px → 28px                                    |
| `cmp.attach.btn`          | A · Composer bottom row  | height        | 26px → 28px                                    |
| `cmp.model.pill`          | A · Model pill (trigger) | width         | 149px → 146px                                  |
| `cmp.model.label`         | A · Model pill (trigger) | lineHeight    | normal → 10px                                  |
| `cmp.model.label`         | A · Model pill (trigger) | height        | 13px → 10px                                    |
| `cmp.model.caret`         | A · Model pill (trigger) | width         | 11px → 12px                                    |
| `cmp.model.caret`         | A · Model pill (trigger) | height        | 11px → 12px                                    |
| `cmp.model.pill.open`     | A · Model pill (trigger) | width         | 149px → 146px                                  |
| `cmp.tools.pill`          | A · Tools pill           | lineHeight    | normal → 12.48px                               |
| `cmp.tools.pill`          | A · Tools pill           | width         | 89px → 89.1562px                               |
| `cmp.tools.icon`          | A · Tools pill           | lineHeight    | normal → 13.6px                                |
| `cmp.tools.icon`          | A · Tools pill           | width         | 11px → 8.20312px                               |
| `cmp.tools.icon`          | A · Tools pill           | height        | 11px → 13.5938px                               |
| `cmp.tools.icon`          | A · Tools pill           | tag           | <svg> → <span> (semantic/default-style change) |
| `cmp.tools.label`         | A · Tools pill           | lineHeight    | normal → 12.48px                               |
| `cmp.tools.label`         | A · Tools pill           | width         | 30px → 30.9531px                               |
| `cmp.tools.label`         | A · Tools pill           | height        | 13px → 12.4844px                               |
| `cmp.tools.count`         | A · Tools pill           | lineHeight    | normal → 11.2px                                |
| `cmp.tools.count`         | A · Tools pill           | width         | 18px → 16px                                    |
| `cmp.tools.count`         | A · Tools pill           | height        | 13px → 16px                                    |
| `cmp.hint`                | A · Hint                 | width         | 89.4688px → 618px                              |
| `cmp.hint`                | A · Hint                 | height        | 13.5px → 24.2344px                             |
| `cmp.hint`                | A · Hint                 | borderStyle   | none → solid none none none                    |
| `cmp.hint`                | A · Hint                 | tag           | <span> → <div> (semantic/default-style change) |
| `cmp.send.btn`            | A · Send                 | opacity       | 0.35 → 0.4                                     |
| `cmp.send.btn`            | A · Send                 | width         | 28px → 32px                                    |
| `cmp.send.btn`            | A · Send                 | height        | 28px → 32px                                    |
| `cmp.send.btn`            | A · Send                 | borderStyle   | none → solid                                   |
| `pop.frame`               | B · Popover frame        | lineHeight    | 19.5px → 18.9px                                |
| `pop.frame`               | B · Popover frame        | width         | 300px → 306.766px                              |
| `pop.frame`               | B · Popover frame        | height        | 335.25px → 265.688px                           |
| `pop.list`                | B · Popover frame        | lineHeight    | 19.5px → 18.9px                                |
| `pop.list`                | B · Popover frame        | width         | 298px → 306.766px                              |
| `pop.list`                | B · Popover frame        | height        | 264px → 265.688px                              |
| `pop.list`                | B · Popover frame        | borderStyle   | none → solid                                   |
| `pop.group.keys`          | B · Group headings       | lineHeight    | 12.75px → 15.12px                              |
| `pop.group.keys`          | B · Group headings       | letterSpacing | 1.105px → 0.56px                               |
| `pop.group.keys`          | B · Group headings       | width         | 288px → 296.766px                              |
| `pop.group.keys`          | B · Group headings       | height        | 24.75px → 27.1094px                            |
| `pop.group.local`         | B · Group headings       | lineHeight    | 12.75px → 15.12px                              |
| `pop.group.local`         | B · Group headings       | letterSpacing | 1.105px → 0.56px                               |
| `pop.group.local`         | B · Group headings       | width         | 288px → 296.766px                              |
| `pop.group.local`         | B · Group headings       | height        | 24.75px → 27.1094px                            |
| `pop.row`                 | B · Rows                 | lineHeight    | 19.5px → 18.9px                                |
| `pop.row`                 | B · Rows                 | width         | 288px → 296.766px                              |
| `pop.row`                 | B · Rows                 | height        | 49.5px → 52.4531px                             |
| `pop.row.meta`            | B · Rows                 | lineHeight    | 19.5px → 18.9px                                |
| `pop.row.meta`            | B · Rows                 | width         | 219px → 190.641px                              |
| `pop.row.meta`            | B · Rows                 | height        | 37.5px → 36.4531px                             |
| `pop.row.name`            | B · Rows                 | lineHeight    | 18px → 18.9px                                  |
| `pop.row.name`            | B · Rows                 | width         | 219px → 190.641px                              |
| `pop.row.name`            | B · Rows                 | height        | 18px → 18.3438px                               |
| `pop.row.nameText`        | B · Rows                 | width         | 106.344px → 115.953px                          |
| `pop.row.nameText`        | B · Rows                 | height        | 18px → 18.3438px                               |
| `pop.row.sub`             | B · Rows                 | lineHeight    | 14.25px → 15.12px                              |
| `pop.row.sub`             | B · Rows                 | width         | auto → 190.641px                               |
| `pop.row.sub`             | B · Rows                 | height        | auto → 15.1094px                               |
| `pop.row.radio`           | B · Rows                 | lineHeight    | 19.5px → 18.9px                                |
| `pop.row.radioCheck`      | B · Rows                 | lineHeight    | 19.5px → 18.9px                                |
| `pop.row.radioCheck`      | B · Rows                 | width         | 9px → 10px                                     |
| `pop.row.radioCheck`      | B · Rows                 | height        | 9px → 10px                                     |
| `pop.rowSelected`         | B · Rows                 | lineHeight    | 19.5px → 18.9px                                |
| `pop.rowSelected`         | B · Rows                 | width         | 288px → 296.766px                              |
| `pop.rowSelected`         | B · Rows                 | height        | 49.5px → 52.4531px                             |
| `pop.rowUnselected`       | B · Rows                 | lineHeight    | 19.5px → 18.9px                                |
| `pop.rowUnselected`       | B · Rows                 | width         | 288px → 296.766px                              |
| `pop.rowUnselected`       | B · Rows                 | height        | 49.5px → 52.4531px                             |
| `pop.rowUnselected.radio` | B · Rows                 | lineHeight    | 19.5px → 18.9px                                |
| `pop.rowLocal`            | B · Rows (local)         | lineHeight    | 19.5px → 18.9px                                |
| `pop.rowLocal`            | B · Rows (local)         | width         | 288px → 296.766px                              |
| `pop.rowLocal`            | B · Rows (local)         | height        | 49.5px → 52.4531px                             |
| `pop.rowLocal.sub`        | B · Rows (local)         | lineHeight    | 14.25px → 15.12px                              |
| `pop.rowLocal.sub`        | B · Rows (local)         | width         | auto → 221.766px                               |
| `pop.rowLocal.sub`        | B · Rows (local)         | height        | auto → 15.1094px                               |
| `pop.footer`              | B · Footer               | lineHeight    | 19.5px → 18.9px                                |
| `pop.footer`              | B · Footer               | width         | 298px → 296.766px                              |
| `pop.footer`              | B · Footer               | height        | 33.25px → 34.1094px                            |
| `pop.footer.linkAdd`      | B · Footer               | lineHeight    | 14.25px → 15.12px                              |
| `pop.footer.linkAdd`      | B · Footer               | width         | 114.031px → 134.438px                          |
| `pop.footer.linkAdd`      | B · Footer               | height        | 14.25px → 15.1094px                            |

## ⚪ INFO (43)

| Element                  | Group                                          | Property      | Design → Live                                                                                                                     |
| ------------------------ | ---------------------------------------------- | ------------- | --------------------------------------------------------------------------------------------------------------------------------- |
| `cmp.frame`              | A · Composer frame                             | text          | “Model this chatYour keysAClaude Sonnet 4.5Anthropic · your k…” → “+⚙Tools1Claude Sonnet 4.5↑/ skillsSources cited inline”        |
| `cmp.row`                | A · Composer bottom row                        | text          | “Claude Sonnet 4.5Tools7/7⏎ send · ⇧⏎ line” → “+⚙Tools1Claude Sonnet 4.5↑”                                                        |
| `cmp.attach.btn`         | A · Composer bottom row                        | text          | “” → “+”                                                                                                                          |
| `cmp.tools.pill`         | A · Tools pill                                 | text          | “Tools7/7” → “⚙Tools1”                                                                                                            |
| `cmp.tools.icon`         | A · Tools pill                                 | text          | “” → “⚙”                                                                                                                          |
| `cmp.tools.count`        | A · Tools pill                                 | text          | “7/7” → “1”                                                                                                                       |
| `cmp.hint`               | A · Hint                                       | text          | “⏎ send · ⇧⏎ line” → “/ skillsSources cited inline”                                                                               |
| `cmp.send.btn`           | A · Send                                       | text          | “” → “↑”                                                                                                                          |
| `pop.frame`              | B · Popover frame                              | text          | “Model this chatYour keysAClaude Sonnet 4.5Anthropic · your k…” → “Your keysAClaude Sonnet 4.5reasoningAnthropic · your keyOGPT…” |
| `pop.list`               | B · Popover frame                              | text          | “Your keysAClaude Sonnet 4.5Anthropic · your keyOGPT-5OpenAI …” → “Your keysAClaude Sonnet 4.5reasoningAnthropic · your keyOGPT…” |
| `pop.row`                | B · Rows                                       | text          | “AClaude Sonnet 4.5Anthropic · your key” → “AClaude Sonnet 4.5reasoningAnthropic · your key”                                      |
| `pop.row.meta`           | B · Rows                                       | text          | “Claude Sonnet 4.5Anthropic · your key” → “Claude Sonnet 4.5reasoningAnthropic · your key”                                        |
| `pop.row.name`           | B · Rows                                       | text          | “Claude Sonnet 4.5” → “Claude Sonnet 4.5reasoning”                                                                                |
| `pop.rowSelected`        | B · Rows                                       | text          | “AClaude Sonnet 4.5Anthropic · your key” → “AClaude Sonnet 4.5reasoningAnthropic · your key”                                      |
| `pop.rowUnselected`      | B · Rows                                       | text          | “OGPT-5OpenAI · your key” → “OGPT-5.4OpenAI · your key”                                                                           |
| `pop.rowLocal`           | B · Rows (local)                               | text          | “Llama 3.3 70B42 GB · never leaves this machine” → “◇Llama 3.3 70Blocal · never leaves this machine”                              |
| `pop.rowLocal.badge`     | B · Rows (local)                               | text          | “” → “◇”                                                                                                                          |
| `pop.rowLocal.sub`       | B · Rows (local)                               | text          | “42 GB · never leaves this machine” → “local · never leaves this machine”                                                         |
| `pop.footer`             | B · Footer                                     | text          | “Add a provider key →Get local models →” → “Add a provider key →”                                                                 |
| `mic-button`             | C · Live-only                                  | extra-in-live | present in live, not in design map                                                                                                |
| `mic-icon`               | C · Live-only                                  | extra-in-live | present in live, not in design map                                                                                                |
| `tools-spacer`           | C · Live-only                                  | extra-in-live | present in live, not in design map                                                                                                |
| `row-reasoning-badge`    | C · Live-only                                  | extra-in-live | present in live, not in design map                                                                                                |
| `model-menu-group-cloud` | C · Live-only                                  | extra-in-live | present in live, not in design map                                                                                                |
| `model-menu-group-local` | C · Live-only                                  | extra-in-live | present in live, not in design map                                                                                                |
| `plus-root`              | C · Live-only wrappers                         | extra-in-live | present in live, not in design map                                                                                                |
| `tools-cluster`          | C · Live-only wrappers                         | extra-in-live | present in live, not in design map                                                                                                |
| `tools-trigger-wrap`     | C · Live-only wrappers                         | extra-in-live | present in live, not in design map                                                                                                |
| `model-pill-root`        | C · Live-only wrappers                         | extra-in-live | present in live, not in design map                                                                                                |
| `send-wrap`              | C · Live-only wrappers                         | extra-in-live | present in live, not in design map                                                                                                |
| `bottombar-slot`         | C · Live-only wrappers                         | extra-in-live | present in live, not in design map                                                                                                |
| `hint-slot`              | C · Live-only wrappers                         | extra-in-live | present in live, not in design map                                                                                                |
| `hint-skills`            | C · Hint internals (live-only)                 | extra-in-live | present in live, not in design map                                                                                                |
| `hint-kbd`               | C · Hint internals (live-only)                 | extra-in-live | present in live, not in design map                                                                                                |
| `hint-grow`              | C · Hint internals (live-only)                 | extra-in-live | present in live, not in design map                                                                                                |
| `hint-meta`              | C · Hint internals (live-only)                 | extra-in-live | present in live, not in design map                                                                                                |
| `hero-title`             | C · FTUE frame (covered by surfaces/run-empty) | extra-in-live | present in live, not in design map                                                                                                |
| `chips-row`              | C · FTUE frame (covered by surfaces/run-empty) | extra-in-live | present in live, not in design map                                                                                                |
| `chip-wallet`            | C · FTUE frame (covered by surfaces/run-empty) | extra-in-live | present in live, not in design map                                                                                                |
| `chip-thread`            | C · FTUE frame (covered by surfaces/run-empty) | extra-in-live | present in live, not in design map                                                                                                |
| `chip-csv`               | C · FTUE frame (covered by surfaces/run-empty) | extra-in-live | present in live, not in design map                                                                                                |
| `chip-icon`              | C · FTUE frame (covered by surfaces/run-empty) | extra-in-live | present in live, not in design map                                                                                                |
| `chip-label`             | C · FTUE frame (covered by surfaces/run-empty) | extra-in-live | present in live, not in design map                                                                                                |
