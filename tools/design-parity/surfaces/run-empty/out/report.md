# Design-parity report — `composer`

Design baseline (source of truth) vs live app, by computed style.

- Design: `surfaces/run-empty/out/design-composer.json`
- Live: `surfaces/run-empty/out/live-composer.json`

**Summary:** 🔴 HIGH 9 · 🟠 MEDIUM 23 · 🟡 LOW 11 · ⚪ INFO 2

## 🔴 HIGH (9)

| Element             | Group    | Property        | Design → Live                                                                     |
| ------------------- | -------- | --------------- | --------------------------------------------------------------------------------- |
| `composer.box`      | Composer | borderColor     | rgba(255, 255, 255, 0.1) (--line2) → rgba(255, 255, 255, 0.06) (--line)           |
| `composer.textarea` | Composer | backgroundColor | rgba(0, 0, 0, 0) (transparent) → rgb(13, 13, 16)                                  |
| `composer.textarea` | Composer | borderColor     | rgb(236, 236, 241) (--tx) → rgba(255, 255, 255, 0.06) (--line)                    |
| `composer.send`     | Composer | color           | rgb(8, 19, 29) (--accent-ink) → color(srgb 0.377451 0.621569 0.80098)             |
| `composer.send`     | Composer | backgroundColor | rgb(95, 178, 236) (--accent/--sky) → color(srgb 0.372549 0.698039 0.92549 / 0.18) |
| `composer.send`     | Composer | borderColor     | rgb(8, 19, 29) (--accent-ink) → color(srgb 0.372549 0.698039 0.92549 / 0.35)      |
| `model.pill`        | Composer | color           | rgb(152, 152, 159) (--mut) → rgb(236, 236, 241) (--tx)                            |
| `model.pill`        | Composer | backgroundColor | rgba(0, 0, 0, 0) (transparent) → rgb(17, 17, 20) (--panel)                        |
| `model.pill`        | Composer | borderColor     | rgba(0, 0, 0, 0) (transparent) → rgba(255, 255, 255, 0.06) (--line)               |

## 🟠 MEDIUM (23)

| Element             | Group    | Property       | Design → Live                  |
| ------------------- | -------- | -------------- | ------------------------------ |
| `hero.h1`           | Hero     | margin         | 0px 0px 7px 0px → 0px          |
| `chips`             | Chips    | fontSize       | 13px → 13.6px (+0.6px)         |
| `chip`              | Chips    | borderRadius   | 99px → 999px                   |
| `composer.box`      | Composer | fontSize       | 13px → 13.6px (+0.6px)         |
| `composer.box`      | Composer | display        | block → flex                   |
| `composer.box`      | Composer | flexDirection  | row → column                   |
| `composer.box`      | Composer | padding        | 0px → 10px                     |
| `composer.box`      | Composer | margin         | 0px → 8px 0px 0px 0px          |
| `composer.box`      | Composer | borderRadius   | 11px → 12px                    |
| `composer.box`      | Composer | gap            | normal → 6px                   |
| `composer.textarea` | Composer | fontSize       | 12.5px → 13px (+0.5px)         |
| `composer.textarea` | Composer | padding        | 10px 12px 4px 12px → 10px 12px |
| `composer.textarea` | Composer | borderWidth    | 0px → 1px                      |
| `composer.textarea` | Composer | borderRadius   | 0px → 8px                      |
| `composer.send`     | Composer | fontWeight     | 400 → 600                      |
| `composer.send`     | Composer | display        | grid → flex                    |
| `composer.send`     | Composer | justifyContent | normal → center                |
| `composer.send`     | Composer | padding        | 1px 6px → 4px                  |
| `composer.send`     | Composer | borderWidth    | 0px → 1px                      |
| `model.pill`        | Composer | fontWeight     | 400 → 500                      |
| `model.pill`        | Composer | display        | flex → inline-flex             |
| `model.pill`        | Composer | borderRadius   | 7px → 8px                      |
| `model.pill`        | Composer | gap            | 6px → 4px                      |

## 🟡 LOW (11)

| Element             | Group    | Property    | Design → Live       |
| ------------------- | -------- | ----------- | ------------------- |
| `chips`             | Chips    | lineHeight  | 19.5px → normal     |
| `composer.box`      | Composer | lineHeight  | 19.5px → normal     |
| `composer.box`      | Composer | height      | 96.75px → 170.734px |
| `composer.textarea` | Composer | width       | 638px → 618px       |
| `composer.textarea` | Composer | height      | 52.75px → 80.5px    |
| `composer.textarea` | Composer | borderStyle | none → solid        |
| `composer.send`     | Composer | opacity     | 0.35 → 0.85         |
| `composer.send`     | Composer | width       | 28px → 32px         |
| `composer.send`     | Composer | height      | 28px → 32px         |
| `composer.send`     | Composer | borderStyle | none → solid        |
| `model.pill`        | Composer | width       | 149px → 146px       |

## ⚪ INFO (2)

| Element         | Group    | Property | Design → Live                                                                                               |
| --------------- | -------- | -------- | ----------------------------------------------------------------------------------------------------------- |
| `composer.box`  | Composer | text     | “Claude Sonnet 4.5Tools1⏎ send · ⇧⏎ line” → “+Claude Sonnet 4.5↑↵ send⇧+↵ new line/ skillsSources cited i…” |
| `composer.send` | Composer | text     | “” → “↑”                                                                                                    |
