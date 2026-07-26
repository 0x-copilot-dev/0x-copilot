# Design-parity report — chat-tool-call-shell · `focus-thinking`

Design baseline (source of truth) vs live app, by computed style.

- Design: `surfaces/chat-tool-call-shell/out/design-focus-thinking.json`
- Live: `surfaces/chat-tool-call-shell/out/live-focus-thinking.json`

**Summary:** 🔴 HIGH 0 · 🟠 MEDIUM 1 · 🟡 LOW 36 · ⚪ INFO 8

## 🟠 MEDIUM (1)

| Element | Group | Property | Design → Live |
|---|---|---|---|
| `shell.mode-switcher` | Cockpit shell | margin | 0px 0px 0px 982.969px → 0px 0px 0px 1043.97px |

## 🟡 LOW (36)

| Element | Group | Property | Design → Live |
|---|---|---|---|
| `shell.frame` | Cockpit shell | lineHeight | 19.5px → normal |
| `shell.frame` | Cockpit shell | transition | all → none |
| `shell.frame` | Cockpit shell | height | 767.031px → 784px |
| `shell.header` | Cockpit shell | lineHeight | 19.5px → normal |
| `shell.header` | Cockpit shell | transition | all → none |
| `shell.header` | Cockpit shell | tag | <div> → <header> (semantic/default-style change) |
| `shell.mode-switcher` | Cockpit shell | lineHeight | 19.5px → normal |
| `shell.mode-switcher` | Cockpit shell | transition | all → none |
| `chat.column` | Chat | lineHeight | 19.5px → normal |
| `chat.column` | Chat | transition | all → none |
| `chat.column` | Chat | width | 857px → 858px |
| `chat.column` | Chat | height | 727.031px → 712px |
| `chat.column` | Chat | tag | <section> → <div> (semantic/default-style change) |
| `chat.transcript` | Chat | lineHeight | 19.5px → normal |
| `chat.transcript` | Chat | transition | all → none |
| `chat.transcript` | Chat | width | 856px → 857px |
| `chat.transcript` | Chat | height | 573.406px → 612.25px |
| `chat.composer` | Chat | lineHeight | 19.5px → normal |
| `chat.composer` | Chat | transition | border-color 0.12s → none |
| `chat.composer` | Chat | width | 836px → 857px |
| `chat.composer` | Chat | height | 77.375px → 99.75px |
| `thinking.activity-panel` | Focus thinking | lineHeight | 19.5px → normal |
| `thinking.activity-panel` | Focus thinking | transition | all → none |
| `thinking.activity-panel` | Focus thinking | height | 727.031px → 712px |
| `thinking.activity-heading` | Focus thinking | lineHeight | 19.5px → normal |
| `thinking.activity-heading` | Focus thinking | transition | all → none |
| `thinking.reasoning` | Focus thinking | lineHeight | 17.25px → normal |
| `thinking.reasoning` | Focus thinking | transition | all → none |
| `thinking.reasoning` | Focus thinking | width | 211.484px → 825px |
| `thinking.reasoning` | Focus thinking | height | 17.25px → 31.1562px |
| `thinking.reasoning` | Focus thinking | tag | <span> → <li> (semantic/default-style change) |
| `thinking.plan` | Focus thinking | lineHeight | 19.5px → normal |
| `thinking.plan` | Focus thinking | transition | all → none |
| `thinking.plan` | Focus thinking | width | 312px → 340px |
| `thinking.plan` | Focus thinking | height | 90px → 38.1875px |
| `thinking.plan` | Focus thinking | tag | <div> → <section> (semantic/default-style change) |

## ⚪ INFO (8)

| Element | Group | Property | Design → Live |
|---|---|---|---|
| `shell.frame` | Cockpit shell | text | “” → “.run-destination [data-testid="tc-chat"] { box-sizing: borde…” |
| `shell.header` | Cockpit shell | text | “” → “0xCopilot—FocusACTIVE RUNClaude Sonnet 4.5 .run-header-pulse…” |
| `chat.column` | Chat | text | “” → “Catch me up on ENG-142, then prepare the requested file.Read…” |
| `chat.transcript` | Chat | text | “” → “Catch me up on ENG-142, then prepare the requested file.Read…” |
| `chat.composer` | Chat | text | “” → “Tools1Model” |
| `thinking.activity-panel` | Focus thinking | text | “” → “ActivityliveAgentsApprovalsSourcesSubagents run here when Co…” |
| `thinking.reasoning` | Focus thinking | text | “Thought for 4s — 4 tools, 1 file to write” → “Reading the issue history and planning the next step…I’m che…” |
| `thinking.plan` | Focus thinking | text | “” → “PlanUnderstanding your request” |
