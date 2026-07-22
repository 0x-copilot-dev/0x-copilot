# Design-parity report — `default-chatsurface`

Design baseline (source of truth) vs live app, by computed style.

- Design: `surfaces/projects/out/design-default-chatsurface.json`
- Live: `surfaces/projects/out/live-default-chatsurface.json`

**Summary:** 🔴 HIGH 9 · 🟠 MEDIUM 23 · 🟡 LOW 36 · ⚪ INFO 8

## 🔴 HIGH (9)

| Element                  | Group        | Property        | Design → Live                                                  |
| ------------------------ | ------------ | --------------- | -------------------------------------------------------------- |
| `default.page.container` | Layout       | backgroundColor | rgba(0, 0, 0, 0) (transparent) → rgb(9, 9, 11)                 |
| `default.page.lead`      | Layout       | missing-in-live | present in design, ABSENT in live                              |
| `default.card.icon`      | Project card | fontSize        | 13px → 16px (+3.0px)                                           |
| `default.card.icon`      | Project card | color           | rgb(212, 212, 219) (--tx2) → rgb(236, 236, 241) (--tx)         |
| `default.card.icon`      | Project card | backgroundColor | rgb(29, 29, 35) (--panel3) → rgb(29, 79, 114)                  |
| `default.card.name.link` | Project card | color           | rgb(236, 236, 241) (--tx) → rgb(95, 178, 236) (--accent/--sky) |
| `default.card.desc`      | Project card | color           | rgb(100, 100, 109) (--mut2) → rgb(152, 152, 159) (--mut)       |
| `default.card.meta`      | Project card | fontFamily      | typeface class changed (mono → sans)                           |
| `default.card.meta`      | Project card | color           | rgb(100, 100, 109) (--mut2) → rgb(152, 152, 159) (--mut)       |

## 🟠 MEDIUM (23)

| Element                  | Group        | Property       | Design → Live                                              |
| ------------------------ | ------------ | -------------- | ---------------------------------------------------------- |
| `default.page.container` | Layout       | display        | block → flex                                               |
| `default.page.container` | Layout       | flexDirection  | row → column                                               |
| `default.page.container` | Layout       | flexGrow       | flex-grow 1 → 0 (affects vertical fill / button placement) |
| `default.page.container` | Layout       | padding        | 20px 24px 40px 24px → 0px                                  |
| `default.grid`           | Layout       | gap            | 10px → 12px                                                |
| `default.card`           | Project card | display        | block → flex                                               |
| `default.card`           | Project card | flexDirection  | row → column                                               |
| `default.card`           | Project card | padding        | 13px → 14px                                                |
| `default.card`           | Project card | gap            | normal → 10px                                              |
| `default.card.icon`      | Project card | fontWeight     | 600 → 400                                                  |
| `default.card.icon`      | Project card | display        | grid → flex                                                |
| `default.card.icon`      | Project card | justifyContent | normal → center                                            |
| `default.card.icon`      | Project card | borderRadius   | 8px → 6px                                                  |
| `default.card.name`      | Project card | fontSize       | 14px → 13px (-1.0px)                                       |
| `default.card.name`      | Project card | flexGrow       | flex-grow 0 → 1 (affects vertical fill / button placement) |
| `default.card.name.link` | Project card | fontSize       | 14px → 13px (-1.0px)                                       |
| `default.card.name.link` | Project card | display        | block → inline-flex                                        |
| `default.card.name.link` | Project card | alignItems     | normal → center                                            |
| `default.card.name.link` | Project card | gap            | normal → 6px                                               |
| `default.card.desc`      | Project card | fontSize       | 11px → 12.48px (+1.5px)                                    |
| `default.card.desc`      | Project card | margin         | 10px 0px 0px 0px → 0px                                     |
| `default.card.meta`      | Project card | fontSize       | 11px → 12.48px (+1.5px)                                    |
| `default.card.meta`      | Project card | margin         | 10px 0px 0px 0px → 0px                                     |

## 🟡 LOW (36)

| Element                  | Group        | Property   | Design → Live                                                                                                                                                                            |
| ------------------------ | ------------ | ---------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `default.page.container` | Layout       | lineHeight | 19.5px → normal                                                                                                                                                                          |
| `default.page.container` | Layout       | width      | 960px → 1040px                                                                                                                                                                           |
| `default.page.container` | Layout       | height     | 754px → 760px                                                                                                                                                                            |
| `default.page.container` | Layout       | tag        | <div> → <section> (semantic/default-style change)                                                                                                                                        |
| `default.grid`           | Layout       | lineHeight | 19.5px → normal                                                                                                                                                                          |
| `default.grid`           | Layout       | width      | 912px → 944px                                                                                                                                                                            |
| `default.grid`           | Layout       | height     | 113px → 129px                                                                                                                                                                            |
| `default.card`           | Project card | lineHeight | 19.5px → normal                                                                                                                                                                          |
| `default.card`           | Project card | textAlign  | left → start                                                                                                                                                                             |
| `default.card`           | Project card | width      | 297.328px → 306.656px                                                                                                                                                                    |
| `default.card`           | Project card | height     | 113px → 129px                                                                                                                                                                            |
| `default.card`           | Project card | tag        | <button> → <article> (semantic/default-style change)                                                                                                                                     |
| `default.card.icon`      | Project card | lineHeight | 19.5px → normal                                                                                                                                                                          |
| `default.card.icon`      | Project card | textAlign  | left → start                                                                                                                                                                             |
| `default.card.icon`      | Project card | width      | 32px → 28px                                                                                                                                                                              |
| `default.card.icon`      | Project card | height     | 32px → 28px                                                                                                                                                                              |
| `default.card.name`      | Project card | lineHeight | 21px → normal                                                                                                                                                                            |
| `default.card.name`      | Project card | textAlign  | left → start                                                                                                                                                                             |
| `default.card.name`      | Project card | width      | 90.1094px → 67.0312px                                                                                                                                                                    |
| `default.card.name`      | Project card | height     | 21px → 16px                                                                                                                                                                              |
| `default.card.name`      | Project card | tag        | <div> → <span> (semantic/default-style change)                                                                                                                                           |
| `default.card.name.link` | Project card | lineHeight | 21px → normal                                                                                                                                                                            |
| `default.card.name.link` | Project card | textAlign  | left → start                                                                                                                                                                             |
| `default.card.name.link` | Project card | transition | all → background-color 0.12s cubic-bezier(0.2, 0, 0, 1), border-color 0.12s cubic-bezier(0.2, 0, 0, 1), color 0.12s cubic-bezier(0.2, 0, 0, 1), opacity 0.12s cubic-bezier(0.2, 0, 0, 1) |
| `default.card.name.link` | Project card | width      | 90.1094px → 84.375px                                                                                                                                                                     |
| `default.card.name.link` | Project card | height     | 21px → 16px                                                                                                                                                                              |
| `default.card.name.link` | Project card | tag        | <div> → <a> (semantic/default-style change)                                                                                                                                              |
| `default.card.desc`      | Project card | lineHeight | 16.5px → normal                                                                                                                                                                          |
| `default.card.desc`      | Project card | textAlign  | left → start                                                                                                                                                                             |
| `default.card.desc`      | Project card | width      | 269.328px → 276.656px                                                                                                                                                                    |
| `default.card.desc`      | Project card | height     | 16.5px → 15px                                                                                                                                                                            |
| `default.card.meta`      | Project card | lineHeight | 16.5px → normal                                                                                                                                                                          |
| `default.card.meta`      | Project card | textAlign  | left → start                                                                                                                                                                             |
| `default.card.meta`      | Project card | width      | 269.328px → 242.219px                                                                                                                                                                    |
| `default.card.meta`      | Project card | height     | 16.5px → 15px                                                                                                                                                                            |
| `default.card.meta`      | Project card | tag        | <div> → <span> (semantic/default-style change)                                                                                                                                           |

## ⚪ INFO (8)

| Element                     | Group            | Property      | Design → Live                                                                                                                     |
| --------------------------- | ---------------- | ------------- | --------------------------------------------------------------------------------------------------------------------------------- |
| `default.page.container`    | Layout           | text          | “Group related chats, files, and context. Open a project to s…” → “Projects3 activeNew projectAll3Active3Archived0Starred0🚀Lau…” |
| `default.grid`              | Layout           | text          | “LLaunch WeekGTM for the v2 launch3 chats · 12 filesTTreasury…” → “🚀Launch WeekActive☆ArchiveGTM for the v2 launch3h ago3 chat…” |
| `default.card`              | Project card     | text          | “LLaunch WeekGTM for the v2 launch3 chats · 12 files” → “🚀Launch WeekActive☆ArchiveGTM for the v2 launch3h ago3 chat…”           |
| `default.card.icon`         | Project card     | text          | “L” → “🚀”                                                                                                                        |
| `default.card.meta`         | Project card     | text          | “3 chats · 12 files” → “3 chats · 0 todos · 0 routines · 1 members”                                                               |
| `default.x.card.statuspill` | Live-only chrome | extra-in-live | present in live, not in design map                                                                                                |
| `default.x.pageheader`      | Live-only chrome | extra-in-live | present in live, not in design map                                                                                                |
| `default.x.filtertabs`      | Live-only chrome | extra-in-live | present in live, not in design map                                                                                                |
