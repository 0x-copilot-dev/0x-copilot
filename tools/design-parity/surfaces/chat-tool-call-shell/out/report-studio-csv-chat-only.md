# Design-parity report — chat-tool-call-shell · `studio-csv-chat-only`

Design baseline (source of truth) vs live app, by computed style.

- Design: `surfaces/chat-tool-call-shell/out/design-studio-csv-chat-only.json`
- Live: `surfaces/chat-tool-call-shell/out/live-studio-csv-chat-only.json`

**Summary:** 🔴 HIGH 0 · 🟠 MEDIUM 1 · 🟡 LOW 40 · ⚪ INFO 9

## 🟠 MEDIUM (1)

| Element | Group | Property | Design → Live |
|---|---|---|---|
| `shell.mode-switcher` | Cockpit shell | margin | 0px 0px 0px 982.969px → 0px 0px 0px 1043.97px |

## 🟡 LOW (40)

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
| `chat.column` | Chat | width | 583.891px → 584px |
| `chat.column` | Chat | height | 727.031px → 617px |
| `chat.column` | Chat | tag | <section> → <div> (semantic/default-style change) |
| `chat.transcript` | Chat | lineHeight | 19.5px → normal |
| `chat.transcript` | Chat | transition | all → none |
| `chat.transcript` | Chat | width | 582.891px → 583px |
| `chat.transcript` | Chat | height | 573.406px → 517.25px |
| `chat.composer` | Chat | lineHeight | 19.5px → normal |
| `chat.composer` | Chat | transition | border-color 0.12s → none |
| `chat.composer` | Chat | width | 562.891px → 583px |
| `chat.composer` | Chat | height | 77.375px → 99.75px |
| `studio.canvas` | Studio | lineHeight | 19.5px → normal |
| `studio.canvas` | Studio | transition | all → none |
| `studio.canvas` | Studio | width | 613.109px → 613px |
| `studio.canvas` | Studio | height | 727.031px → 660px |
| `studio.canvas` | Studio | tag | <section> → <div> (semantic/default-style change) |
| `tool.csv.card` | Local CSV read | transition | all → none |
| `tool.csv.card` | Local CSV read | width | 550.891px → 551px |
| `tool.csv.card` | Local CSV read | height | 19px → 57.5px |
| `tool.csv.card` | Local CSV read | tag | <div> → <details> (semantic/default-style change) |
| `tool.csv.header` | Local CSV read | textAlign | start → left |
| `tool.csv.header` | Local CSV read | transition | all → none |
| `tool.csv.header` | Local CSV read | width | 548.891px → 549px |
| `tool.csv.header` | Local CSV read | tag | <div> → <summary> (semantic/default-style change) |
| `csv.summary-card` | Local CSV read | transition | all → none |
| `csv.chat-only-canvas` | Local CSV read | lineHeight | 19.5px → normal |
| `csv.chat-only-canvas` | Local CSV read | transition | all → none |
| `csv.chat-only-canvas` | Local CSV read | width | 613.109px → 613px |
| `csv.chat-only-canvas` | Local CSV read | height | 695.781px → 660px |
| `csv.chat-only-canvas` | Local CSV read | tag | <div> → <section> (semantic/default-style change) |

## ⚪ INFO (9)

| Element | Group | Property | Design → Live |
|---|---|---|---|
| `shell.frame` | Cockpit shell | text | “” → “.run-destination [data-testid="tc-chat"] { box-sizing: borde…” |
| `shell.header` | Cockpit shell | text | “” → “0xCopilot—StudioACTIVE RUNClaude Sonnet 4.5 .run-header-puls…” |
| `chat.column` | Chat | text | “” → “Catch me up on ENG-142, then prepare the requested file. .tc…” |
| `chat.transcript` | Chat | text | “” → “Catch me up on ENG-142, then prepare the requested file. .tc…” |
| `chat.composer` | Chat | text | “” → “Tools1Model” |
| `studio.canvas` | Studio | text | “” → “RUN IN PROGRESSPreparing this runActivity will appear here i…” |
| `tool.csv.card` | Local CSV read | text | “” → “.tc-activity-card__head::-webkit-details-marker { display: n…” |
| `tool.csv.header` | Local CSV read | text | “” → “Ffs.readMCP · Workspaceread820 ms742 rows · 9 columns✓▾” |
| `csv.chat-only-canvas` | Local CSV read | text | “” → “RUN IN PROGRESSPreparing this runActivity will appear here i…” |
