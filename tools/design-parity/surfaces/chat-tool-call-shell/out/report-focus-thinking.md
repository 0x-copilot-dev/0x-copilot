# Design-parity report — chat-tool-call-shell · `focus-thinking`

Design baseline (source of truth) vs live app, by computed style.

- Design: `surfaces/chat-tool-call-shell/out/design-focus-thinking.json`
- Live: `surfaces/chat-tool-call-shell/out/live-focus-thinking.json`

**Summary:** 🔴 HIGH 2 · 🟠 MEDIUM 8 · 🟡 LOW 33 · ⚪ INFO 7

## 🔴 HIGH (2)

| Element | Group | Property | Design → Live |
|---|---|---|---|
| `shell.frame` | Cockpit shell | borderColor | rgba(255, 255, 255, 0.1) (--line2) → rgb(236, 236, 241) (--tx) |
| `thinking.plan` | Focus thinking | missing-in-live | present in design, ABSENT in live |

## 🟠 MEDIUM (8)

| Element | Group | Property | Design → Live |
|---|---|---|---|
| `shell.frame` | Cockpit shell | boxShadow | rgba(0, 0, 0, 0.7) 0px 0px 0px 1px, rgba(0, 0, 0, 0.8) 0px 40px 100px -30px → none |
| `shell.frame` | Cockpit shell | borderWidth | 1px → 0px |
| `shell.frame` | Cockpit shell | borderRadius | 12px → 0px |
| `shell.header` | Cockpit shell | padding | 0px 13px → 8px 20px |
| `shell.header` | Cockpit shell | gap | 12px → 14px |
| `shell.mode-switcher` | Cockpit shell | margin | 0px 0px 0px 982.969px → 0px 0px 0px 623px |
| `chat.composer` | Chat | flexDirection | row → column |
| `chat.composer` | Chat | gap | normal → 0px |

## 🟡 LOW (33)

| Element | Group | Property | Design → Live |
|---|---|---|---|
| `shell.frame` | Cockpit shell | lineHeight | 19.5px → normal |
| `shell.frame` | Cockpit shell | transition | all → none |
| `shell.frame` | Cockpit shell | height | 767.031px → 816px |
| `shell.frame` | Cockpit shell | borderStyle | solid → none |
| `shell.header` | Cockpit shell | lineHeight | 19.5px → normal |
| `shell.header` | Cockpit shell | transition | all → none |
| `shell.header` | Cockpit shell | height | 38px → 58px |
| `shell.header` | Cockpit shell | tag | <div> → <header> (semantic/default-style change) |
| `shell.mode-switcher` | Cockpit shell | lineHeight | 19.5px → normal |
| `shell.mode-switcher` | Cockpit shell | transition | all → none |
| `chat.column` | Chat | lineHeight | 19.5px → normal |
| `chat.column` | Chat | transition | all → none |
| `chat.column` | Chat | width | 857px → 858px |
| `chat.column` | Chat | height | 727.031px → 724px |
| `chat.column` | Chat | tag | <section> → <div> (semantic/default-style change) |
| `chat.transcript` | Chat | lineHeight | 19.5px → normal |
| `chat.transcript` | Chat | transition | all → none |
| `chat.transcript` | Chat | width | 856px → 857px |
| `chat.transcript` | Chat | height | 573.406px → 597.25px |
| `chat.composer` | Chat | lineHeight | 19.5px → normal |
| `chat.composer` | Chat | transition | border-color 0.12s → none |
| `chat.composer` | Chat | width | 836px → 857px |
| `chat.composer` | Chat | height | 77.375px → 126.75px |
| `thinking.activity-panel` | Focus thinking | lineHeight | 19.5px → normal |
| `thinking.activity-panel` | Focus thinking | transition | all → none |
| `thinking.activity-panel` | Focus thinking | height | 727.031px → 724px |
| `thinking.activity-heading` | Focus thinking | lineHeight | 19.5px → normal |
| `thinking.activity-heading` | Focus thinking | transition | all → none |
| `thinking.reasoning` | Focus thinking | lineHeight | 17.25px → normal |
| `thinking.reasoning` | Focus thinking | transition | all → none |
| `thinking.reasoning` | Focus thinking | width | 211.484px → 825px |
| `thinking.reasoning` | Focus thinking | height | 17.25px → 31.1562px |
| `thinking.reasoning` | Focus thinking | tag | <span> → <li> (semantic/default-style change) |

## ⚪ INFO (7)

| Element | Group | Property | Design → Live |
|---|---|---|---|
| `shell.frame` | Cockpit shell | text | “” → “.run-destination [data-testid="tc-chat"] { box-sizing: borde…” |
| `shell.header` | Cockpit shell | text | “” → “0ACTIVE RUNMonday catch-up .run-header-pulse-dot { animation…” |
| `chat.column` | Chat | text | “” → “Catch me up on ENG-142, then prepare the requested file.Read…” |
| `chat.transcript` | Chat | text | “” → “Catch me up on ENG-142, then prepare the requested file.Read…” |
| `chat.composer` | Chat | text | “” → “ToolsOpus 4.7 · Balanced↵ send⇧+↵ new line/ skillsOpus 4.7 ·…” |
| `thinking.activity-panel` | Focus thinking | text | “” → “ActivityliveAgentsApprovalsSourcesSubagents run here when Co…” |
| `thinking.reasoning` | Focus thinking | text | “Thought for 4s — 4 tools, 1 file to write” → “Reading the issue history and planning the next step…I’m che…” |
