# Design-parity report — chat-tool-call-shell · `focus-thinking`

Design baseline (source of truth) vs live app, by computed style.

- Design: `surfaces/chat-tool-call-shell/out/design-focus-thinking.json`
- Live: `surfaces/chat-tool-call-shell/out/live-focus-thinking.json`

**Summary:** 🔴 HIGH 7 · 🟠 MEDIUM 18 · 🟡 LOW 41 · ⚪ INFO 9

## 🔴 HIGH (7)

| Element | Group | Property | Design → Live |
|---|---|---|---|
| `shell.frame` | Cockpit shell | borderColor | rgba(255, 255, 255, 0.1) (--line2) → rgb(236, 236, 241) (--tx) |
| `shell.mode-switcher` | Cockpit shell | backgroundColor | rgb(17, 17, 20) (--panel) → rgb(9, 9, 11) |
| `chat.column` | Chat | backgroundColor | rgb(9, 9, 11) → rgba(0, 0, 0, 0) (transparent) |
| `chat.column` | Chat | borderColor | rgb(236, 236, 241) rgba(255, 255, 255, 0.06) rgb(236, 236, 241) rgb(236, 236, 241) → rgb(236, 236, 241) (--tx) |
| `thinking.activity-panel` | Focus thinking | borderColor | rgb(236, 236, 241) (--tx) → rgb(236, 236, 241) rgb(236, 236, 241) rgb(236, 236, 241) rgba(255, 255, 255, 0.06) |
| `thinking.reasoning` | Focus thinking | color | rgb(152, 152, 159) (--mut) → rgb(236, 236, 241) (--tx) |
| `thinking.plan` | Focus thinking | missing-in-live | present in design, ABSENT in live |

## 🟠 MEDIUM (18)

| Element | Group | Property | Design → Live |
|---|---|---|---|
| `shell.frame` | Cockpit shell | boxShadow | rgba(0, 0, 0, 0.7) 0px 0px 0px 1px, rgba(0, 0, 0, 0.8) 0px 40px 100px -30px → none |
| `shell.frame` | Cockpit shell | borderWidth | 1px → 0px |
| `shell.frame` | Cockpit shell | borderRadius | 12px → 0px |
| `shell.header` | Cockpit shell | padding | 0px 13px → 12px 16px |
| `shell.mode-switcher` | Cockpit shell | padding | 2px → 3px |
| `shell.mode-switcher` | Cockpit shell | margin | 0px 0px 0px 982.969px → 0px |
| `shell.mode-switcher` | Cockpit shell | borderRadius | 7px → 999px |
| `shell.mode-switcher` | Cockpit shell | gap | 2px → 4px |
| `chat.column` | Chat | padding | 0px → 12px |
| `chat.column` | Chat | borderWidth | 0px 1px 0px 0px → 0px |
| `chat.transcript` | Chat | padding | 16px → 8px |
| `chat.transcript` | Chat | gap | 14px → 8px |
| `chat.composer` | Chat | display | block → flex |
| `chat.composer` | Chat | flexDirection | row → column |
| `chat.composer` | Chat | gap | normal → 0px |
| `thinking.activity-panel` | Focus thinking | borderWidth | 0px → 0px 0px 0px 1px |
| `thinking.reasoning` | Focus thinking | fontSize | 11.5px → 13px (+1.5px) |
| `thinking.reasoning` | Focus thinking | display | block → list-item |

## 🟡 LOW (41)

| Element | Group | Property | Design → Live |
|---|---|---|---|
| `shell.frame` | Cockpit shell | lineHeight | 19.5px → normal |
| `shell.frame` | Cockpit shell | transition | all → none |
| `shell.frame` | Cockpit shell | height | 767.031px → 816px |
| `shell.frame` | Cockpit shell | borderStyle | solid → none |
| `shell.header` | Cockpit shell | lineHeight | 19.5px → normal |
| `shell.header` | Cockpit shell | transition | all → none |
| `shell.header` | Cockpit shell | width | 1198px → 1200px |
| `shell.header` | Cockpit shell | height | 38px → 58px |
| `shell.header` | Cockpit shell | tag | <div> → <header> (semantic/default-style change) |
| `shell.mode-switcher` | Cockpit shell | lineHeight | 19.5px → normal |
| `shell.mode-switcher` | Cockpit shell | transition | all → none |
| `shell.mode-switcher` | Cockpit shell | width | 128.031px → 148.203px |
| `shell.mode-switcher` | Cockpit shell | height | 31px → 33px |
| `chat.column` | Chat | lineHeight | 19.5px → normal |
| `chat.column` | Chat | transition | all → none |
| `chat.column` | Chat | width | 857px → 730px |
| `chat.column` | Chat | height | 727.031px → 693.25px |
| `chat.column` | Chat | borderStyle | none solid none none → none |
| `chat.column` | Chat | tag | <section> → <div> (semantic/default-style change) |
| `chat.transcript` | Chat | lineHeight | 19.5px → normal |
| `chat.transcript` | Chat | transition | all → none |
| `chat.transcript` | Chat | width | 856px → 706px |
| `chat.transcript` | Chat | height | 573.406px → 545.5px |
| `chat.composer` | Chat | lineHeight | 19.5px → normal |
| `chat.composer` | Chat | transition | border-color 0.12s → none |
| `chat.composer` | Chat | width | 836px → 706px |
| `chat.composer` | Chat | height | 77.375px → 123.75px |
| `thinking.activity-panel` | Focus thinking | lineHeight | 19.5px → normal |
| `thinking.activity-panel` | Focus thinking | transition | all → none |
| `thinking.activity-panel` | Focus thinking | width | 340px → 324px |
| `thinking.activity-panel` | Focus thinking | height | 727.031px → 693.25px |
| `thinking.activity-panel` | Focus thinking | borderStyle | none → none none none solid |
| `thinking.activity-heading` | Focus thinking | lineHeight | 19.5px → normal |
| `thinking.activity-heading` | Focus thinking | transition | all → none |
| `thinking.activity-heading` | Focus thinking | width | 340px → 323px |
| `thinking.activity-heading` | Focus thinking | height | 37px → 43px |
| `thinking.reasoning` | Focus thinking | lineHeight | 17.25px → normal |
| `thinking.reasoning` | Focus thinking | transition | all → none |
| `thinking.reasoning` | Focus thinking | width | 211.484px → 690px |
| `thinking.reasoning` | Focus thinking | height | 17.25px → 36.5312px |
| `thinking.reasoning` | Focus thinking | tag | <span> → <li> (semantic/default-style change) |

## ⚪ INFO (9)

| Element | Group | Property | Design → Live |
|---|---|---|---|
| `shell.frame` | Cockpit shell | text | “” → “0ACTIVE RUNMonday catch-up .run-header-pulse-dot { animation…” |
| `shell.header` | Cockpit shell | text | “” → “0ACTIVE RUNMonday catch-up .run-header-pulse-dot { animation…” |
| `shell.mode-switcher` | Cockpit shell | text | “FocusStudio” → “StudioFocus” |
| `chat.column` | Chat | text | “” → “Catch me up on ENG-142, then prepare the requested file.Read…” |
| `chat.transcript` | Chat | text | “” → “Catch me up on ENG-142, then prepare the requested file.Read…” |
| `chat.composer` | Chat | text | “” → “ToolsOpus 4.7 · Balanced↵ send⇧+↵ new line/ skillsOpus 4.7 ·…” |
| `thinking.activity-panel` | Focus thinking | text | “” → “Run detailsAgentsApprovalsSourcesSubagents run here when Cop…” |
| `thinking.activity-heading` | Focus thinking | text | “Activitylive” → “Run details” |
| `thinking.reasoning` | Focus thinking | text | “Thought for 4s — 4 tools, 1 file to write” → “Reading the issue history and planning the next step…I’m che…” |
