# Design-parity report — chat-tool-call-shell · `studio-web-chat-only`

Design baseline (source of truth) vs live app, by computed style.

- Design: `surfaces/chat-tool-call-shell/out/design-studio-web-chat-only.json`
- Live: `surfaces/chat-tool-call-shell/out/live-studio-web-chat-only.json`

**Summary:** 🔴 HIGH 0 · 🟠 MEDIUM 1 · 🟡 LOW 40 · ⚪ INFO 10

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
| `tool.web.card` | Web read | transition | all → none |
| `tool.web.card` | Web read | width | 550.891px → 551px |
| `tool.web.card` | Web read | height | 53.5625px → 57.5px |
| `tool.web.card` | Web read | tag | <div> → <details> (semantic/default-style change) |
| `tool.web.header` | Web read | textAlign | start → left |
| `tool.web.header` | Web read | transition | all → none |
| `tool.web.header` | Web read | width | 548.891px → 549px |
| `tool.web.header` | Web read | tag | <div> → <summary> (semantic/default-style change) |
| `web.sources-card` | Web read | transition | all → none |
| `web.chat-only-canvas` | Web read | lineHeight | 19.5px → normal |
| `web.chat-only-canvas` | Web read | transition | all → none |
| `web.chat-only-canvas` | Web read | width | 613.109px → 613px |
| `web.chat-only-canvas` | Web read | height | 695.781px → 660px |
| `web.chat-only-canvas` | Web read | tag | <div> → <section> (semantic/default-style change) |

## ⚪ INFO (10)

| Element | Group | Property | Design → Live |
|---|---|---|---|
| `shell.frame` | Cockpit shell | text | “” → “.run-destination [data-testid="tc-chat"] { box-sizing: borde…” |
| `shell.header` | Cockpit shell | text | “” → “0xCopilot—StudioACTIVE RUNClaude Sonnet 4.5 .run-header-puls…” |
| `chat.column` | Chat | text | “” → “Catch me up on ENG-142, then prepare the requested file. .tc…” |
| `chat.transcript` | Chat | text | “” → “Catch me up on ENG-142, then prepare the requested file. .tc…” |
| `chat.composer` | Chat | text | “” → “Tools1Model” |
| `studio.canvas` | Studio | text | “” → “RUN IN PROGRESSPreparing this runActivity will appear here i…” |
| `tool.web.card` | Web read | text | “” → “.tc-activity-card__head::-webkit-details-marker { display: n…” |
| `tool.web.header` | Web read | text | “” → “Wweb.searchMCP · Webread820 ms3 sources synthesized in chat✓…” |
| `web.sources-card` | Web read | text | “SOURCES ·” → “SOURCES · 3” |
| `web.chat-only-canvas` | Web read | text | “” → “RUN IN PROGRESSPreparing this runActivity will appear here i…” |
