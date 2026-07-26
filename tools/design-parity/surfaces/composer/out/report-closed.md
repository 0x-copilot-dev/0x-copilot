# Design-parity report — `closed`

Design baseline (source of truth) vs live app, by computed style.

- Design: `surfaces/composer/out/design-closed.json`
- Live: `surfaces/composer/out/live-closed.json`

**Summary:** 🔴 HIGH 2 · 🟠 MEDIUM 16 · 🟡 LOW 14 · ⚪ INFO 19

## 🔴 HIGH (2)

| Element           | Group                   | Property        | Design → Live                     |
| ----------------- | ----------------------- | --------------- | --------------------------------- |
| `cmp.attach.icon` | A · Composer bottom row | missing-in-live | present in design, ABSENT in live |
| `cmp.send.icon`   | A · Send                | missing-in-live | present in design, ABSENT in live |

## 🟠 MEDIUM (16)

| Element           | Group                    | Property       | Design → Live          |
| ----------------- | ------------------------ | -------------- | ---------------------- |
| `cmp.frame`       | A · Composer frame       | display        | block → flex           |
| `cmp.frame`       | A · Composer frame       | flexDirection  | row → column           |
| `cmp.frame`       | A · Composer frame       | margin         | 0px → 8px 0px 0px 0px  |
| `cmp.frame`       | A · Composer frame       | gap            | normal → 0px           |
| `cmp.row`         | A · Composer bottom row  | justifyContent | normal → space-between |
| `cmp.attach.btn`  | A · Composer bottom row  | display        | grid → flex            |
| `cmp.attach.btn`  | A · Composer bottom row  | justifyContent | normal → center        |
| `cmp.attach.btn`  | A · Composer bottom row  | padding        | 1px 6px → 0px          |
| `cmp.model.pill`  | A · Model pill (trigger) | display        | flex → inline-flex     |
| `cmp.model.dot`   | A · Model pill (trigger) | borderRadius   | 50% → 999px            |
| `cmp.model.label` | A · Model pill (trigger) | fontWeight     | 400 → 500              |
| `cmp.model.caret` | A · Model pill (trigger) | fontSize       | 10px → 11.2px (+1.2px) |
| `cmp.send.btn`    | A · Send                 | fontWeight     | 400 → 600              |
| `cmp.send.btn`    | A · Send                 | display        | grid → flex            |
| `cmp.send.btn`    | A · Send                 | justifyContent | normal → center        |
| `cmp.send.btn`    | A · Send                 | padding        | 1px 6px → 0px          |

## 🟡 LOW (14)

| Element           | Group                    | Property   | Design → Live                                                                                                                                                                            |
| ----------------- | ------------------------ | ---------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `cmp.frame`       | A · Composer frame       | lineHeight | 19.5px → normal                                                                                                                                                                          |
| `cmp.frame`       | A · Composer frame       | transition | border-color 0.12s → border-color 0.15s, box-shadow 0.15s                                                                                                                                |
| `cmp.frame`       | A · Composer frame       | height     | 77.375px → 116.125px                                                                                                                                                                     |
| `cmp.textarea`    | A · Composer frame       | transition | all → background-color 0.12s cubic-bezier(0.2, 0, 0, 1), border-color 0.12s cubic-bezier(0.2, 0, 0, 1), color 0.12s cubic-bezier(0.2, 0, 0, 1), opacity 0.12s cubic-bezier(0.2, 0, 0, 1) |
| `cmp.textarea`    | A · Composer frame       | height     | 33.375px → 72.125px                                                                                                                                                                      |
| `cmp.row`         | A · Composer bottom row  | lineHeight | 19.5px → normal                                                                                                                                                                          |
| `cmp.attach.btn`  | A · Composer bottom row  | transition | all → background-color 0.12s cubic-bezier(0.2, 0, 0, 1), border-color 0.12s cubic-bezier(0.2, 0, 0, 1), color 0.12s cubic-bezier(0.2, 0, 0, 1), opacity 0.12s cubic-bezier(0.2, 0, 0, 1) |
| `cmp.model.pill`  | A · Model pill (trigger) | transition | all → border-color 0.12s, box-shadow 0.12s                                                                                                                                               |
| `cmp.model.label` | A · Model pill (trigger) | lineHeight | normal → 10px                                                                                                                                                                            |
| `cmp.model.label` | A · Model pill (trigger) | height     | 13px → 10px                                                                                                                                                                              |
| `cmp.tools.pill`  | A · Tools pill           | transition | all → background-color 0.12s cubic-bezier(0.2, 0, 0, 1), border-color 0.12s cubic-bezier(0.2, 0, 0, 1), color 0.12s cubic-bezier(0.2, 0, 0, 1), opacity 0.12s cubic-bezier(0.2, 0, 0, 1) |
| `cmp.tools.pill`  | A · Tools pill           | width      | 89px → 77px                                                                                                                                                                              |
| `cmp.tools.count` | A · Tools pill           | width      | 18px → 6px                                                                                                                                                                               |
| `cmp.send.btn`    | A · Send                 | transition | all → color 0.12s, border-color 0.12s, background 0.12s                                                                                                                                  |

## ⚪ INFO (19)

| Element           | Group                                          | Property        | Design → Live                                                                            |
| ----------------- | ---------------------------------------------- | --------------- | ---------------------------------------------------------------------------------------- |
| `cmp.frame`       | A · Composer frame                             | text            | “Claude Sonnet 4.5Tools7/7⏎ send · ⇧⏎ line” → “Tools1Claude Sonnet 4.5”                  |
| `cmp.row`         | A · Composer bottom row                        | text            | “Claude Sonnet 4.5Tools7/7⏎ send · ⇧⏎ line” → “Tools1Claude Sonnet 4.5”                  |
| `cmp.tools.pill`  | A · Tools pill                                 | text            | “Tools7/7” → “Tools1”                                                                    |
| `cmp.tools.count` | A · Tools pill                                 | text            | “7/7” → “1”                                                                              |
| `cmp.hint`        | A · Hint                                       | missing-in-live | expected: Product decision: omit the static send/newline hint from the desktop composer. |
| `mic-button`      | C · Live-only                                  | extra-in-live   | present in live, not in design map                                                       |
| `mic-icon`        | C · Live-only                                  | extra-in-live   | present in live, not in design map                                                       |
| `plus-root`       | C · Live-only wrappers                         | extra-in-live   | present in live, not in design map                                                       |
| `tools-cluster`   | C · Live-only wrappers                         | extra-in-live   | present in live, not in design map                                                       |
| `model-pill-root` | C · Live-only wrappers                         | extra-in-live   | present in live, not in design map                                                       |
| `send-wrap`       | C · Live-only wrappers                         | extra-in-live   | present in live, not in design map                                                       |
| `bottombar-slot`  | C · Live-only wrappers                         | extra-in-live   | present in live, not in design map                                                       |
| `hero-title`      | C · FTUE frame (covered by surfaces/run-empty) | extra-in-live   | present in live, not in design map                                                       |
| `chips-row`       | C · FTUE frame (covered by surfaces/run-empty) | extra-in-live   | present in live, not in design map                                                       |
| `chip-wallet`     | C · FTUE frame (covered by surfaces/run-empty) | extra-in-live   | present in live, not in design map                                                       |
| `chip-thread`     | C · FTUE frame (covered by surfaces/run-empty) | extra-in-live   | present in live, not in design map                                                       |
| `chip-csv`        | C · FTUE frame (covered by surfaces/run-empty) | extra-in-live   | present in live, not in design map                                                       |
| `chip-icon`       | C · FTUE frame (covered by surfaces/run-empty) | extra-in-live   | present in live, not in design map                                                       |
| `chip-label`      | C · FTUE frame (covered by surfaces/run-empty) | extra-in-live   | present in live, not in design map                                                       |
