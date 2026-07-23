# Design-parity report — `default`

Design baseline (source of truth) vs live app, by computed style.

- Design: `surfaces/projects/out/design-default.json`
- Live: `surfaces/projects/out/live-default.json`

**Summary:** 🔴 HIGH 8 · 🟠 MEDIUM 24 · 🟡 LOW 30 · ⚪ INFO 4

## 🔴 HIGH (8)

| Element                | Group        | Property        | Design → Live                                                  |
| ---------------------- | ------------ | --------------- | -------------------------------------------------------------- |
| `default.page.lead`    | Layout       | missing-in-live | present in design, ABSENT in live                              |
| `default.card.hitarea` | Project card | backgroundColor | rgb(17, 17, 20) (--panel) → rgba(0, 0, 0, 0) (transparent)     |
| `default.card.hitarea` | Project card | borderColor     | rgba(255, 255, 255, 0.06) (--line) → rgb(236, 236, 241) (--tx) |
| `default.card.icon`    | Project card | color           | rgb(212, 212, 219) (--tx2) → rgb(177, 215, 241)                |
| `default.card.icon`    | Project card | backgroundColor | rgb(29, 29, 35) (--panel3) → rgba(29, 79, 114, 0.45)           |
| `default.card.icon`    | Project card | borderColor     | rgb(212, 212, 219) (--tx2) → rgba(51, 140, 204, 0.55)          |
| `default.card.desc`    | Project card | color           | rgb(100, 100, 109) (--mut2) → rgb(152, 152, 159) (--mut)       |
| `default.card.meta`    | Project card | fontFamily      | typeface class changed (mono → sans)                           |

## 🟠 MEDIUM (24)

| Element                  | Group        | Property       | Design → Live                                              |
| ------------------------ | ------------ | -------------- | ---------------------------------------------------------- |
| `default.page.container` | Layout       | flexGrow       | flex-grow 1 → 0 (affects vertical fill / button placement) |
| `default.page.container` | Layout       | padding        | 20px 24px 40px 24px → 24px                                 |
| `default.grid`           | Layout       | gap            | 10px → 12px                                                |
| `default.card`           | Project card | display        | block → flex                                               |
| `default.card`           | Project card | flexDirection  | row → column                                               |
| `default.card`           | Project card | padding        | 13px → 0px                                                 |
| `default.card`           | Project card | borderRadius   | 8px → 12px                                                 |
| `default.card.hitarea`   | Project card | display        | block → flex                                               |
| `default.card.hitarea`   | Project card | flexDirection  | row → column                                               |
| `default.card.hitarea`   | Project card | alignItems     | normal → flex-start                                        |
| `default.card.hitarea`   | Project card | padding        | 13px → 14px 14px 10px 14px                                 |
| `default.card.hitarea`   | Project card | borderWidth    | 1px → 0px                                                  |
| `default.card.hitarea`   | Project card | borderRadius   | 8px → 0px                                                  |
| `default.card.hitarea`   | Project card | gap            | normal → 6px                                               |
| `default.card.icon`      | Project card | fontSize       | 13px → 14px (+1.0px)                                       |
| `default.card.icon`      | Project card | fontWeight     | 600 → 700                                                  |
| `default.card.icon`      | Project card | display        | grid → flex                                                |
| `default.card.icon`      | Project card | justifyContent | normal → center                                            |
| `default.card.icon`      | Project card | borderWidth    | 0px → 1px                                                  |
| `default.card.desc`      | Project card | fontSize       | 11px → 12px (+1.0px)                                       |
| `default.card.desc`      | Project card | display        | block → flow-root                                          |
| `default.card.desc`      | Project card | margin         | 10px 0px 0px 0px → 0px                                     |
| `default.card.meta`      | Project card | fontSize       | 11px → 12px (+1.0px)                                       |
| `default.card.meta`      | Project card | margin         | 10px 0px 0px 0px → 2px 0px 0px 0px                         |

## 🟡 LOW (30)

| Element                  | Group        | Property    | Design → Live                                                                                                                                                                            |
| ------------------------ | ------------ | ----------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `default.page.container` | Layout       | lineHeight  | 19.5px → normal                                                                                                                                                                          |
| `default.page.container` | Layout       | width       | 960px → 1040px                                                                                                                                                                           |
| `default.page.container` | Layout       | height      | 754px → 760px                                                                                                                                                                            |
| `default.page.container` | Layout       | tag         | <div> → <section> (semantic/default-style change)                                                                                                                                        |
| `default.grid`           | Layout       | lineHeight  | 19.5px → normal                                                                                                                                                                          |
| `default.grid`           | Layout       | width       | 912px → 992px                                                                                                                                                                            |
| `default.grid`           | Layout       | height      | 113px → 168px                                                                                                                                                                            |
| `default.card`           | Project card | lineHeight  | 19.5px → normal                                                                                                                                                                          |
| `default.card`           | Project card | textAlign   | left → start                                                                                                                                                                             |
| `default.card`           | Project card | width       | 297.328px → 322.656px                                                                                                                                                                    |
| `default.card`           | Project card | height      | 113px → 168px                                                                                                                                                                            |
| `default.card`           | Project card | tag         | <button> → <div> (semantic/default-style change)                                                                                                                                         |
| `default.card.hitarea`   | Project card | lineHeight  | 19.5px → normal                                                                                                                                                                          |
| `default.card.hitarea`   | Project card | transition  | all → background-color 0.12s cubic-bezier(0.2, 0, 0, 1), border-color 0.12s cubic-bezier(0.2, 0, 0, 1), color 0.12s cubic-bezier(0.2, 0, 0, 1), opacity 0.12s cubic-bezier(0.2, 0, 0, 1) |
| `default.card.hitarea`   | Project card | width       | 297.328px → 320.656px                                                                                                                                                                    |
| `default.card.hitarea`   | Project card | height      | 113px → 123px                                                                                                                                                                            |
| `default.card.hitarea`   | Project card | borderStyle | solid → none                                                                                                                                                                             |
| `default.card.icon`      | Project card | lineHeight  | 19.5px → normal                                                                                                                                                                          |
| `default.card.icon`      | Project card | borderStyle | none → solid                                                                                                                                                                             |
| `default.card.name`      | Project card | lineHeight  | 21px → normal                                                                                                                                                                            |
| `default.card.name`      | Project card | height      | 21px → 17px                                                                                                                                                                              |
| `default.card.name`      | Project card | tag         | <div> → <span> (semantic/default-style change)                                                                                                                                           |
| `default.card.desc`      | Project card | lineHeight  | 16.5px → normal                                                                                                                                                                          |
| `default.card.desc`      | Project card | width       | 269.328px → 125.578px                                                                                                                                                                    |
| `default.card.desc`      | Project card | height      | 16.5px → 15px                                                                                                                                                                            |
| `default.card.desc`      | Project card | tag         | <div> → <span> (semantic/default-style change)                                                                                                                                           |
| `default.card.meta`      | Project card | lineHeight  | 16.5px → normal                                                                                                                                                                          |
| `default.card.meta`      | Project card | width       | 269.328px → 91.7969px                                                                                                                                                                    |
| `default.card.meta`      | Project card | height      | 16.5px → 15px                                                                                                                                                                            |
| `default.card.meta`      | Project card | tag         | <div> → <span> (semantic/default-style change)                                                                                                                                           |

## ⚪ INFO (4)

| Element                  | Group            | Property      | Design → Live                                                                                                                     |
| ------------------------ | ---------------- | ------------- | --------------------------------------------------------------------------------------------------------------------------------- |
| `default.page.container` | Layout           | text          | “Group related chats, files, and context. Open a project to s…” → “.projects-grid3 { display: grid; grid-template-columns: repe…” |
| `default.grid`           | Layout           | text          | “LLaunch WeekGTM for the v2 launch3 chats · 12 filesTTreasury…” → “LLaunch WeekGTM for the v2 launch3 chats · 12 filesStarArchi…” |
| `default.card`           | Project card     | text          | “LLaunch WeekGTM for the v2 launch3 chats · 12 files” → “LLaunch WeekGTM for the v2 launch3 chats · 12 filesStarArchi…”           |
| `default.x.card.actions` | Live-only chrome | extra-in-live | present in live, not in design map                                                                                                |
