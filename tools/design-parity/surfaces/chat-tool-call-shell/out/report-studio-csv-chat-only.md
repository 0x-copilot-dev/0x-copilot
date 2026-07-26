# Design-parity report — chat-tool-call-shell · `studio-csv-chat-only`

Design baseline (source of truth) vs live app, by computed style.

- Design: `surfaces/chat-tool-call-shell/out/design-studio-csv-chat-only.json`
- Live: `surfaces/chat-tool-call-shell/out/live-studio-csv-chat-only.json`

**Summary:** 🔴 HIGH 4 · 🟠 MEDIUM 17 · 🟡 LOW 41 · ⚪ INFO 9

## 🔴 HIGH (4)

| Element | Group | Property | Design → Live |
|---|---|---|---|
| `shell.frame` | Cockpit shell | borderColor | rgba(255, 255, 255, 0.1) (--line2) → rgb(236, 236, 241) (--tx) |
| `csv.summary-card` | Local CSV read | missing-in-live | present in design, ABSENT in live |
| `csv.chat-only-canvas` | Local CSV read | backgroundColor | rgba(0, 0, 0, 0) (transparent) → rgb(17, 17, 20) (--panel) |
| `csv.chat-only-canvas` | Local CSV read | borderColor | rgb(236, 236, 241) (--tx) → rgba(255, 255, 255, 0.06) (--line) |

## 🟠 MEDIUM (17)

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
| `csv.chat-only-canvas` | Local CSV read | alignItems | center → normal |
| `csv.chat-only-canvas` | Local CSV read | flexGrow | flex-grow 1 → 0 (affects vertical fill / button placement) |
| `csv.chat-only-canvas` | Local CSV read | padding | 26px → 32px |
| `csv.chat-only-canvas` | Local CSV read | borderWidth | 0px → 1px |
| `csv.chat-only-canvas` | Local CSV read | borderRadius | 0px → 12px |
| `csv.chat-only-canvas` | Local CSV read | gap | normal → 8px |

## 🟡 LOW (41)

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
| `tool.csv.card` | Local CSV read | transition | all → none |
| `tool.csv.card` | Local CSV read | width | 550.891px → 551px |
| `tool.csv.card` | Local CSV read | height | 19px → 57.5px |
| `tool.csv.card` | Local CSV read | tag | <div> → <details> (semantic/default-style change) |
| `tool.csv.header` | Local CSV read | transition | all → none |
| `tool.csv.header` | Local CSV read | width | 548.891px → 549px |
| `tool.csv.header` | Local CSV read | tag | <div> → <summary> (semantic/default-style change) |
| `csv.chat-only-canvas` | Local CSV read | lineHeight | 19.5px → normal |
| `csv.chat-only-canvas` | Local CSV read | transition | all → none |
| `csv.chat-only-canvas` | Local CSV read | width | 613.109px → 613px |
| `csv.chat-only-canvas` | Local CSV read | height | 695.781px → 636px |
| `csv.chat-only-canvas` | Local CSV read | borderStyle | none → solid |
| `csv.chat-only-canvas` | Local CSV read | tag | <div> → <section> (semantic/default-style change) |

## ⚪ INFO (9)

| Element | Group | Property | Design → Live |
|---|---|---|---|
| `shell.frame` | Cockpit shell | text | “” → “.run-destination [data-testid="tc-chat"] { box-sizing: borde…” |
| `shell.header` | Cockpit shell | text | “” → “0ACTIVE RUNMonday catch-up .run-header-pulse-dot { animation…” |
| `chat.column` | Chat | text | “” → “Catch me up on ENG-142, then prepare the requested file. @ke…” |
| `chat.transcript` | Chat | text | “” → “Catch me up on ENG-142, then prepare the requested file. @ke…” |
| `chat.composer` | Chat | text | “” → “ToolsOpus 4.7 · Balanced↵ send⇧+↵ new line/ skillsOpus 4.7 ·…” |
| `studio.canvas` | Studio | text | “” → “RUN IN PROGRESSPreparing this runActivity will appear here i…” |
| `tool.csv.card` | Local CSV read | text | “” → “@keyframes tc-tool-card-spin { to { transform: rotate(360deg…” |
| `tool.csv.header` | Local CSV read | text | “” → “Ffs.readMCP · Workspaceread820 ms742 rows · 9 columns✓▾” |
| `csv.chat-only-canvas` | Local CSV read | text | “” → “RUN IN PROGRESSPreparing this runActivity will appear here i…” |
