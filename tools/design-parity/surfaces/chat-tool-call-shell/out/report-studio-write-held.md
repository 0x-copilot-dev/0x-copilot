# Design-parity report — chat-tool-call-shell · `studio-write-held`

Design baseline (source of truth) vs live app, by computed style.

- Design: `surfaces/chat-tool-call-shell/out/design-studio-write-held.json`
- Live: `surfaces/chat-tool-call-shell/out/live-studio-write-held.json`

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
| `chat.column` | Chat | height | 727.031px → 554.203px |
| `chat.column` | Chat | tag | <section> → <div> (semantic/default-style change) |
| `chat.transcript` | Chat | lineHeight | 19.5px → normal |
| `chat.transcript` | Chat | transition | all → none |
| `chat.transcript` | Chat | width | 582.891px → 583px |
| `chat.transcript` | Chat | height | 573.406px → 454.453px |
| `chat.composer` | Chat | lineHeight | 19.5px → normal |
| `chat.composer` | Chat | transition | border-color 0.12s → none |
| `chat.composer` | Chat | width | 562.891px → 583px |
| `chat.composer` | Chat | height | 77.375px → 99.75px |
| `studio.canvas` | Studio | lineHeight | 19.5px → normal |
| `studio.canvas` | Studio | transition | all → none |
| `studio.canvas` | Studio | width | 613.109px → 613px |
| `studio.canvas` | Studio | height | 727.031px → 597.203px |
| `studio.canvas` | Studio | tag | <section> → <div> (semantic/default-style change) |
| `tool.write.card` | Held write | transition | all → none |
| `tool.write.card` | Held write | width | 550.891px → 551px |
| `tool.write.card` | Held write | height | 10.8281px → 57.5px |
| `tool.write.card` | Held write | tag | <div> → <details> (semantic/default-style change) |
| `tool.write.header` | Held write | textAlign | start → left |
| `tool.write.header` | Held write | transition | all → none |
| `tool.write.header` | Held write | width | 548.891px → 549px |
| `tool.write.header` | Held write | tag | <div> → <summary> (semantic/default-style change) |
| `write.review-surface` | Held write | transition | all → none |
| `write.review-surface` | Held write | width | 550.891px → 613px |
| `write.review-surface` | Held write | height | 37.6562px → 491.75px |
| `write.review-surface` | Held write | tag | <div> → <section> (semantic/default-style change) |
| `write.review-status` | Held write | transition | all → none |
| `write.review-status` | Held write | width | 57.5312px → 95.6094px |

## ⚪ INFO (10)

| Element | Group | Property | Design → Live |
|---|---|---|---|
| `shell.frame` | Cockpit shell | text | “” → “.run-destination [data-testid="tc-chat"] { box-sizing: borde…” |
| `shell.header` | Cockpit shell | text | “” → “0xCopilot—StudioACTIVE RUNClaude Sonnet 4.5 .run-header-puls…” |
| `chat.column` | Chat | text | “” → “Catch me up on ENG-142, then prepare the requested file. .tc…” |
| `chat.transcript` | Chat | text | “” → “Catch me up on ENG-142, then prepare the requested file. .tc…” |
| `chat.composer` | Chat | text | “” → “Tools1Model” |
| `studio.canvas` | Studio | text | “” → “Workspace stageCreate workspace filerev 1 · YoucreateAwaitin…” |
| `tool.write.card` | Held write | text | “” → “.tc-activity-card__head::-webkit-details-marker { display: n…” |
| `tool.write.header` | Held write | text | “” → “Ffs.writeMCP · Workspaceread820 msChange staged for approval…” |
| `write.review-surface` | Held write | text | “” → “Workspace stageCreate workspace filerev 1 · YoucreateAwaitin…” |
| `write.review-status` | Held write | text | “new file” → “Awaiting review” |
