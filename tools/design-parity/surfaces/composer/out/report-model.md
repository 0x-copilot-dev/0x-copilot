# Design-parity report — `model`

Design baseline (source of truth) vs live app, by computed style.

- Design: `surfaces/composer/out/design-model.json`
- Live: `surfaces/composer/out/live-model.json`

**Summary:** 🔴 HIGH 16 · 🟠 MEDIUM 48 · 🟡 LOW 35 · ⚪ INFO 33

## 🔴 HIGH (16)

| Element                  | Group                    | Property        | Design → Live                                                           |
| ------------------------ | ------------------------ | --------------- | ----------------------------------------------------------------------- |
| `cmp.attach.icon`        | A · Composer bottom row  | missing-in-live | present in design, ABSENT in live                                       |
| `cmp.model.caret`        | A · Model pill (trigger) | color           | rgb(236, 236, 241) (--tx) → rgb(152, 152, 159) (--mut)                  |
| `cmp.send.icon`          | A · Send                 | missing-in-live | present in design, ABSENT in live                                       |
| `pop.scrim`              | B · Popover frame        | missing-in-live | present in design, ABSENT in live                                       |
| `pop.frame`              | B · Popover frame        | backgroundColor | rgb(17, 17, 20) (--panel) → rgb(13, 13, 16)                             |
| `pop.frame`              | B · Popover frame        | borderColor     | rgba(255, 255, 255, 0.1) (--line2) → rgba(255, 255, 255, 0.06) (--line) |
| `pop.header`             | B · Popover header       | missing-in-live | present in design, ABSENT in live                                       |
| `pop.header.meta`        | B · Popover header       | missing-in-live | present in design, ABSENT in live                                       |
| `pop.list`               | B · Popover frame        | backgroundColor | rgba(0, 0, 0, 0) (transparent) → rgb(13, 13, 16)                        |
| `pop.list`               | B · Popover frame        | borderColor     | rgb(236, 236, 241) (--tx) → rgba(255, 255, 255, 0.06) (--line)          |
| `pop.row`                | B · Rows                 | backgroundColor | rgba(0, 0, 0, 0) (transparent) → rgb(22, 22, 26) (--panel2)             |
| `pop.rowSelected`        | B · Rows                 | backgroundColor | rgba(0, 0, 0, 0) (transparent) → rgb(22, 22, 26) (--panel2)             |
| `pop.rowLocal.badge`     | B · Rows (local)         | missing-in-live | present in design, ABSENT in live                                       |
| `pop.rowLocal.badgeIcon` | B · Rows (local)         | missing-in-live | present in design, ABSENT in live                                       |
| `pop.footer.spacer`      | B · Footer               | missing-in-live | present in design, ABSENT in live                                       |
| `pop.footer.linkLocal`   | B · Footer               | missing-in-live | present in design, ABSENT in live                                       |

## 🟠 MEDIUM (48)

| Element               | Group                    | Property       | Design → Live                                                      |
| --------------------- | ------------------------ | -------------- | ------------------------------------------------------------------ |
| `cmp.frame`           | A · Composer frame       | display        | block → flex                                                       |
| `cmp.frame`           | A · Composer frame       | flexDirection  | row → column                                                       |
| `cmp.frame`           | A · Composer frame       | margin         | 0px → 8px 0px 0px 0px                                              |
| `cmp.frame`           | A · Composer frame       | gap            | normal → 0px                                                       |
| `cmp.row`             | A · Composer bottom row  | justifyContent | normal → space-between                                             |
| `cmp.attach.btn`      | A · Composer bottom row  | display        | grid → flex                                                        |
| `cmp.attach.btn`      | A · Composer bottom row  | justifyContent | normal → center                                                    |
| `cmp.attach.btn`      | A · Composer bottom row  | padding        | 1px 6px → 0px                                                      |
| `cmp.model.pill`      | A · Model pill (trigger) | display        | flex → inline-flex                                                 |
| `cmp.model.pill`      | A · Model pill (trigger) | boxShadow      | none → oklab(0.734829 -0.0561896 -0.102596 / 0.22) 0px 0px 0px 3px |
| `cmp.model.dot`       | A · Model pill (trigger) | borderRadius   | 50% → 999px                                                        |
| `cmp.model.label`     | A · Model pill (trigger) | fontWeight     | 400 → 500                                                          |
| `cmp.model.caret`     | A · Model pill (trigger) | fontSize       | 10px → 11.2px (+1.2px)                                             |
| `cmp.model.pill.open` | A · Model pill (trigger) | display        | flex → inline-flex                                                 |
| `cmp.model.pill.open` | A · Model pill (trigger) | boxShadow      | none → oklab(0.734829 -0.0561896 -0.102596 / 0.22) 0px 0px 0px 3px |
| `cmp.send.btn`        | A · Send                 | fontWeight     | 400 → 600                                                          |
| `cmp.send.btn`        | A · Send                 | display        | grid → flex                                                        |
| `cmp.send.btn`        | A · Send                 | justifyContent | normal → center                                                    |
| `cmp.send.btn`        | A · Send                 | padding        | 1px 6px → 0px                                                      |
| `pop.frame`           | B · Popover frame        | borderRadius   | 10px → 8px                                                         |
| `pop.frame`           | B · Popover frame        | gap            | normal → 0px                                                       |
| `pop.list`            | B · Popover frame        | boxShadow      | none → rgba(0, 0, 0, 0.75) 0px 18px 50px -12px                     |
| `pop.list`            | B · Popover frame        | padding        | 0px 5px 5px 5px → 0px                                              |
| `pop.list`            | B · Popover frame        | borderWidth    | 0px → 1px                                                          |
| `pop.list`            | B · Popover frame        | borderRadius   | 0px → 8px                                                          |
| `pop.list`            | B · Popover frame        | gap            | normal → 0px                                                       |
| `pop.row`             | B · Rows                 | justifyContent | normal → space-between                                             |
| `pop.row`             | B · Rows                 | padding        | 6px → 8px 10px                                                     |
| `pop.row`             | B · Rows                 | borderRadius   | 7px → 6px                                                          |
| `pop.row`             | B · Rows                 | gap            | 9px → 8px                                                          |
| `pop.row.meta`        | B · Rows                 | display        | block → flex                                                       |
| `pop.row.meta`        | B · Rows                 | flexDirection  | row → column                                                       |
| `pop.row.meta`        | B · Rows                 | gap            | normal → 2px                                                       |
| `pop.row.name`        | B · Rows                 | gap            | 6px → 4px                                                          |
| `pop.row.sub`         | B · Rows                 | display        | inline → block                                                     |
| `pop.rowSelected`     | B · Rows                 | justifyContent | normal → space-between                                             |
| `pop.rowSelected`     | B · Rows                 | padding        | 6px → 8px 10px                                                     |
| `pop.rowSelected`     | B · Rows                 | borderRadius   | 7px → 6px                                                          |
| `pop.rowSelected`     | B · Rows                 | gap            | 9px → 8px                                                          |
| `pop.rowUnselected`   | B · Rows                 | justifyContent | normal → space-between                                             |
| `pop.rowUnselected`   | B · Rows                 | padding        | 6px → 8px 10px                                                     |
| `pop.rowUnselected`   | B · Rows                 | borderRadius   | 7px → 6px                                                          |
| `pop.rowUnselected`   | B · Rows                 | gap            | 9px → 8px                                                          |
| `pop.rowLocal`        | B · Rows (local)         | justifyContent | normal → space-between                                             |
| `pop.rowLocal`        | B · Rows (local)         | padding        | 6px → 8px 10px                                                     |
| `pop.rowLocal`        | B · Rows (local)         | borderRadius   | 7px → 6px                                                          |
| `pop.rowLocal`        | B · Rows (local)         | gap            | 9px → 8px                                                          |
| `pop.rowLocal.sub`    | B · Rows (local)         | display        | inline → block                                                     |

## 🟡 LOW (35)

| Element               | Group                    | Property    | Design → Live                                                                                                                                                                            |
| --------------------- | ------------------------ | ----------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `cmp.frame`           | A · Composer frame       | lineHeight  | 19.5px → normal                                                                                                                                                                          |
| `cmp.frame`           | A · Composer frame       | transition  | border-color 0.12s → border-color 0.15s, box-shadow 0.15s                                                                                                                                |
| `cmp.frame`           | A · Composer frame       | height      | 77.375px → 116.125px                                                                                                                                                                     |
| `cmp.textarea`        | A · Composer frame       | transition  | all → background-color 0.12s cubic-bezier(0.2, 0, 0, 1), border-color 0.12s cubic-bezier(0.2, 0, 0, 1), color 0.12s cubic-bezier(0.2, 0, 0, 1), opacity 0.12s cubic-bezier(0.2, 0, 0, 1) |
| `cmp.textarea`        | A · Composer frame       | height      | 33.375px → 72.125px                                                                                                                                                                      |
| `cmp.row`             | A · Composer bottom row  | lineHeight  | 19.5px → normal                                                                                                                                                                          |
| `cmp.attach.btn`      | A · Composer bottom row  | transition  | all → background-color 0.12s cubic-bezier(0.2, 0, 0, 1), border-color 0.12s cubic-bezier(0.2, 0, 0, 1), color 0.12s cubic-bezier(0.2, 0, 0, 1), opacity 0.12s cubic-bezier(0.2, 0, 0, 1) |
| `cmp.model.pill`      | A · Model pill (trigger) | transition  | all → border-color 0.12s, box-shadow 0.12s                                                                                                                                               |
| `cmp.model.label`     | A · Model pill (trigger) | lineHeight  | normal → 10px                                                                                                                                                                            |
| `cmp.model.label`     | A · Model pill (trigger) | height      | 13px → 10px                                                                                                                                                                              |
| `cmp.model.pill.open` | A · Model pill (trigger) | transition  | all → border-color 0.12s, box-shadow 0.12s                                                                                                                                               |
| `cmp.tools.pill`      | A · Tools pill           | transition  | all → background-color 0.12s cubic-bezier(0.2, 0, 0, 1), border-color 0.12s cubic-bezier(0.2, 0, 0, 1), color 0.12s cubic-bezier(0.2, 0, 0, 1), opacity 0.12s cubic-bezier(0.2, 0, 0, 1) |
| `cmp.tools.pill`      | A · Tools pill           | width       | 89px → 77px                                                                                                                                                                              |
| `cmp.tools.count`     | A · Tools pill           | width       | 18px → 6px                                                                                                                                                                               |
| `cmp.send.btn`        | A · Send                 | transition  | all → color 0.12s, border-color 0.12s, background 0.12s                                                                                                                                  |
| `pop.frame`           | B · Popover frame        | height      | 335.25px → 281.5px                                                                                                                                                                       |
| `pop.list`            | B · Popover frame        | width       | 298px → 300px                                                                                                                                                                            |
| `pop.list`            | B · Popover frame        | height      | 264px → 281.5px                                                                                                                                                                          |
| `pop.list`            | B · Popover frame        | borderStyle | none → solid                                                                                                                                                                             |
| `pop.row`             | B · Rows                 | transition  | all → background-color 0.12s cubic-bezier(0.2, 0, 0, 1), border-color 0.12s cubic-bezier(0.2, 0, 0, 1), color 0.12s cubic-bezier(0.2, 0, 0, 1), opacity 0.12s cubic-bezier(0.2, 0, 0, 1) |
| `pop.row`             | B · Rows                 | height      | 49.5px → 51.25px                                                                                                                                                                         |
| `pop.row.meta`        | B · Rows                 | width       | 219px → 213px                                                                                                                                                                            |
| `pop.row.meta`        | B · Rows                 | height      | 37.5px → 35.25px                                                                                                                                                                         |
| `pop.row.name`        | B · Rows                 | width       | 219px → 213px                                                                                                                                                                            |
| `pop.row.sub`         | B · Rows                 | width       | auto → 213px                                                                                                                                                                             |
| `pop.row.sub`         | B · Rows                 | height      | auto → 14.25px                                                                                                                                                                           |
| `pop.rowSelected`     | B · Rows                 | transition  | all → background-color 0.12s cubic-bezier(0.2, 0, 0, 1), border-color 0.12s cubic-bezier(0.2, 0, 0, 1), color 0.12s cubic-bezier(0.2, 0, 0, 1), opacity 0.12s cubic-bezier(0.2, 0, 0, 1) |
| `pop.rowSelected`     | B · Rows                 | height      | 49.5px → 51.25px                                                                                                                                                                         |
| `pop.rowUnselected`   | B · Rows                 | transition  | all → background-color 0.12s cubic-bezier(0.2, 0, 0, 1), border-color 0.12s cubic-bezier(0.2, 0, 0, 1), color 0.12s cubic-bezier(0.2, 0, 0, 1), opacity 0.12s cubic-bezier(0.2, 0, 0, 1) |
| `pop.rowUnselected`   | B · Rows                 | height      | 49.5px → 51.25px                                                                                                                                                                         |
| `pop.rowLocal`        | B · Rows (local)         | transition  | all → background-color 0.12s cubic-bezier(0.2, 0, 0, 1), border-color 0.12s cubic-bezier(0.2, 0, 0, 1), color 0.12s cubic-bezier(0.2, 0, 0, 1), opacity 0.12s cubic-bezier(0.2, 0, 0, 1) |
| `pop.rowLocal`        | B · Rows (local)         | height      | 49.5px → 51.25px                                                                                                                                                                         |
| `pop.rowLocal.sub`    | B · Rows (local)         | width       | auto → 213px                                                                                                                                                                             |
| `pop.rowLocal.sub`    | B · Rows (local)         | height      | auto → 14.25px                                                                                                                                                                           |
| `pop.footer.linkAdd`  | B · Footer               | transition  | all → background-color 0.12s cubic-bezier(0.2, 0, 0, 1), border-color 0.12s cubic-bezier(0.2, 0, 0, 1), color 0.12s cubic-bezier(0.2, 0, 0, 1), opacity 0.12s cubic-bezier(0.2, 0, 0, 1) |

## ⚪ INFO (33)

| Element                  | Group                                          | Property        | Design → Live                                                                                                                     |
| ------------------------ | ---------------------------------------------- | --------------- | --------------------------------------------------------------------------------------------------------------------------------- |
| `cmp.frame`              | A · Composer frame                             | text            | “Model this chatYour keysAClaude Sonnet 4.5Anthropic · your k…” → “Tools1Claude Sonnet 4.5”                                       |
| `cmp.row`                | A · Composer bottom row                        | text            | “Claude Sonnet 4.5Tools7/7⏎ send · ⇧⏎ line” → “Tools1Claude Sonnet 4.5”                                                           |
| `cmp.tools.pill`         | A · Tools pill                                 | text            | “Tools7/7” → “Tools1”                                                                                                             |
| `cmp.tools.count`        | A · Tools pill                                 | text            | “7/7” → “1”                                                                                                                       |
| `cmp.hint`               | A · Hint                                       | missing-in-live | expected: Product decision: omit the static send/newline hint from the desktop composer.                                          |
| `pop.frame`              | B · Popover frame                              | text            | “Model this chatYour keysAClaude Sonnet 4.5Anthropic · your k…” → “Model this chatYour keysClaude Sonnet 4.5reasoningAnthropic …” |
| `pop.list`               | B · Popover frame                              | text            | “Your keysAClaude Sonnet 4.5Anthropic · your keyOGPT-5OpenAI …” → “Model this chatYour keysClaude Sonnet 4.5reasoningAnthropic …” |
| `pop.row`                | B · Rows                                       | text            | “AClaude Sonnet 4.5Anthropic · your key” → “Claude Sonnet 4.5reasoningAnthropic · your key”                                       |
| `pop.row.badge`          | B · Rows                                       | text            | “A” → “”                                                                                                                          |
| `pop.row.meta`           | B · Rows                                       | text            | “Claude Sonnet 4.5Anthropic · your key” → “Claude Sonnet 4.5reasoningAnthropic · your key”                                        |
| `pop.row.name`           | B · Rows                                       | text            | “Claude Sonnet 4.5” → “Claude Sonnet 4.5reasoning”                                                                                |
| `pop.rowSelected`        | B · Rows                                       | text            | “AClaude Sonnet 4.5Anthropic · your key” → “Claude Sonnet 4.5reasoningAnthropic · your key”                                       |
| `pop.rowUnselected`      | B · Rows                                       | text            | “OGPT-5OpenAI · your key” → “GPT-5.4OpenAI · your key”                                                                            |
| `pop.rowLocal`           | B · Rows (local)                               | text            | “Llama 3.3 70B42 GB · never leaves this machine” → “Llama 3.3 70Blocal · never leaves this machine”                               |
| `pop.rowLocal.sub`       | B · Rows (local)                               | text            | “42 GB · never leaves this machine” → “local · never leaves this machine”                                                         |
| `pop.footer`             | B · Footer                                     | text            | “Add a provider key →Get local models →” → “Add a provider key →”                                                                 |
| `mic-button`             | C · Live-only                                  | extra-in-live   | present in live, not in design map                                                                                                |
| `mic-icon`               | C · Live-only                                  | extra-in-live   | present in live, not in design map                                                                                                |
| `row-reasoning-badge`    | C · Live-only                                  | extra-in-live   | present in live, not in design map                                                                                                |
| `model-menu-group-cloud` | C · Live-only                                  | extra-in-live   | present in live, not in design map                                                                                                |
| `model-menu-group-local` | C · Live-only                                  | extra-in-live   | present in live, not in design map                                                                                                |
| `plus-root`              | C · Live-only wrappers                         | extra-in-live   | present in live, not in design map                                                                                                |
| `tools-cluster`          | C · Live-only wrappers                         | extra-in-live   | present in live, not in design map                                                                                                |
| `model-pill-root`        | C · Live-only wrappers                         | extra-in-live   | present in live, not in design map                                                                                                |
| `send-wrap`              | C · Live-only wrappers                         | extra-in-live   | present in live, not in design map                                                                                                |
| `bottombar-slot`         | C · Live-only wrappers                         | extra-in-live   | present in live, not in design map                                                                                                |
| `hero-title`             | C · FTUE frame (covered by surfaces/run-empty) | extra-in-live   | present in live, not in design map                                                                                                |
| `chips-row`              | C · FTUE frame (covered by surfaces/run-empty) | extra-in-live   | present in live, not in design map                                                                                                |
| `chip-wallet`            | C · FTUE frame (covered by surfaces/run-empty) | extra-in-live   | present in live, not in design map                                                                                                |
| `chip-thread`            | C · FTUE frame (covered by surfaces/run-empty) | extra-in-live   | present in live, not in design map                                                                                                |
| `chip-csv`               | C · FTUE frame (covered by surfaces/run-empty) | extra-in-live   | present in live, not in design map                                                                                                |
| `chip-icon`              | C · FTUE frame (covered by surfaces/run-empty) | extra-in-live   | present in live, not in design map                                                                                                |
| `chip-label`             | C · FTUE frame (covered by surfaces/run-empty) | extra-in-live   | present in live, not in design map                                                                                                |
