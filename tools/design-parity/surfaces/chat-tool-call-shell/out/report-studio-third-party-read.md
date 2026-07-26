# Design-parity report — chat-tool-call-shell · `studio-third-party-read`

Design baseline (source of truth) vs live app, by computed style.

- Design: `surfaces/chat-tool-call-shell/out/design-studio-third-party-read.json`
- Live: `surfaces/chat-tool-call-shell/out/live-studio-third-party-read.json`

**Summary:** 🔴 HIGH 1 · 🟠 MEDIUM 11 · 🟡 LOW 37 · ⚪ INFO 9

## 🔴 HIGH (1)

| Element | Group | Property | Design → Live |
|---|---|---|---|
| `shell.frame` | Cockpit shell | borderColor | rgba(255, 255, 255, 0.1) (--line2) → rgb(236, 236, 241) (--tx) |

## 🟠 MEDIUM (11)

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
| `studio.canvas` | Studio | display | flex → block |
| `studio.canvas` | Studio | flexDirection | column → row |
| `studio.canvas` | Studio | flexGrow | flex-grow 0 → 1 (affects vertical fill / button placement) |

## 🟡 LOW (37)

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
| `chat.column` | Chat | width | 583.891px → 584px |
| `chat.column` | Chat | height | 727.031px → 593px |
| `chat.column` | Chat | tag | <section> → <div> (semantic/default-style change) |
| `chat.transcript` | Chat | lineHeight | 19.5px → normal |
| `chat.transcript` | Chat | transition | all → none |
| `chat.transcript` | Chat | width | 582.891px → 583px |
| `chat.transcript` | Chat | height | 573.406px → 466.25px |
| `chat.composer` | Chat | lineHeight | 19.5px → normal |
| `chat.composer` | Chat | transition | border-color 0.12s → none |
| `chat.composer` | Chat | width | 562.891px → 583px |
| `chat.composer` | Chat | height | 77.375px → 126.75px |
| `studio.canvas` | Studio | lineHeight | 19.5px → normal |
| `studio.canvas` | Studio | transition | all → none |
| `studio.canvas` | Studio | width | 613.109px → 1198px |
| `studio.canvas` | Studio | height | 727.031px → 756px |
| `studio.canvas` | Studio | tag | <section> → <div> (semantic/default-style change) |
| `tool.linear.card` | Third-party read | transition | all → none |
| `tool.linear.card` | Third-party read | width | 550.891px → 551px |
| `tool.linear.card` | Third-party read | tag | <div> → <details> (semantic/default-style change) |
| `tool.linear.header` | Third-party read | transition | all → none |
| `tool.linear.header` | Third-party read | width | 548.891px → 549px |
| `tool.linear.header` | Third-party read | tag | <div> → <summary> (semantic/default-style change) |
| `canvas.issue-result` | Third-party read | transition | all → none |
| `canvas.issue-result` | Third-party read | width | 613.109px → 613px |
| `canvas.issue-result` | Third-party read | height | 695.781px → 599.5px |

## ⚪ INFO (9)

| Element | Group | Property | Design → Live |
|---|---|---|---|
| `shell.frame` | Cockpit shell | text | “” → “.run-destination [data-testid="tc-chat"] { box-sizing: borde…” |
| `shell.header` | Cockpit shell | text | “” → “0ACTIVE RUNMonday catch-up .run-header-pulse-dot { animation…” |
| `chat.column` | Chat | text | “” → “Catch me up on ENG-142, then prepare the requested file. @ke…” |
| `chat.transcript` | Chat | text | “” → “Catch me up on ENG-142, then prepare the requested file. @ke…” |
| `chat.composer` | Chat | text | “” → “ToolsOpus 4.7 · Balanced↵ send⇧+↵ new line/ skillsOpus 4.7 ·…” |
| `studio.canvas` | Studio | text | “” → “ENG-142×▦Connected recordENG-142The source returned a record…” |
| `tool.linear.card` | Third-party read | text | “” → “@keyframes tc-tool-card-spin { to { transform: rotate(360deg…” |
| `tool.linear.header` | Third-party read | text | “” → “Llinear.issues.getMCP · Linearread820 msENG-142 · reconnect …” |
| `canvas.issue-result` | Third-party read | text | “” → “▦Connected recordENG-142The source returned a record without…” |
