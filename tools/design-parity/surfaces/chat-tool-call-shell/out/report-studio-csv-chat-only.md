# Design-parity report — chat-tool-call-shell · `studio-csv-chat-only`

Design baseline (source of truth) vs live app, by computed style.

- Design: `surfaces/chat-tool-call-shell/out/design-studio-csv-chat-only.json`
- Live: `surfaces/chat-tool-call-shell/out/live-studio-csv-chat-only.json`

**Summary:** 🔴 HIGH 8 · 🟠 MEDIUM 29 · 🟡 LOW 45 · ⚪ INFO 10

## 🔴 HIGH (8)

| Element | Group | Property | Design → Live |
|---|---|---|---|
| `shell.frame` | Cockpit shell | borderColor | rgba(255, 255, 255, 0.1) (--line2) → rgb(236, 236, 241) (--tx) |
| `shell.mode-switcher` | Cockpit shell | backgroundColor | rgb(17, 17, 20) (--panel) → rgb(9, 9, 11) |
| `chat.column` | Chat | backgroundColor | rgb(9, 9, 11) → rgba(0, 0, 0, 0) (transparent) |
| `chat.column` | Chat | borderColor | rgb(236, 236, 241) rgba(255, 255, 255, 0.06) rgb(236, 236, 241) rgb(236, 236, 241) → rgb(236, 236, 241) (--tx) |
| `studio.canvas` | Studio | backgroundColor | rgb(9, 9, 11) → rgba(0, 0, 0, 0) (transparent) |
| `csv.summary-card` | Local CSV read | missing-in-live | present in design, ABSENT in live |
| `csv.chat-only-canvas` | Local CSV read | backgroundColor | rgba(0, 0, 0, 0) (transparent) → rgb(17, 17, 20) (--panel) |
| `csv.chat-only-canvas` | Local CSV read | borderColor | rgb(236, 236, 241) (--tx) → rgba(255, 255, 255, 0.06) (--line) |

## 🟠 MEDIUM (29)

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
| `chat.column` | Chat | gap | normal → 10px |
| `chat.transcript` | Chat | padding | 16px → 8px |
| `chat.transcript` | Chat | gap | 14px → 8px |
| `chat.composer` | Chat | display | block → flex |
| `chat.composer` | Chat | flexDirection | row → column |
| `chat.composer` | Chat | gap | normal → 0px |
| `studio.canvas` | Studio | display | flex → block |
| `studio.canvas` | Studio | flexDirection | column → row |
| `studio.canvas` | Studio | flexGrow | flex-grow 0 → 1 (affects vertical fill / button placement) |
| `tool.csv.header` | Local CSV read | display | flex → list-item |
| `tool.csv.header` | Local CSV read | alignItems | center → normal |
| `tool.csv.header` | Local CSV read | padding | 9px 11px → 0px |
| `tool.csv.header` | Local CSV read | gap | 9px → normal |
| `csv.chat-only-canvas` | Local CSV read | alignItems | center → normal |
| `csv.chat-only-canvas` | Local CSV read | flexGrow | flex-grow 1 → 0 (affects vertical fill / button placement) |
| `csv.chat-only-canvas` | Local CSV read | padding | 26px → 32px |
| `csv.chat-only-canvas` | Local CSV read | borderWidth | 0px → 1px |
| `csv.chat-only-canvas` | Local CSV read | borderRadius | 0px → 12px |
| `csv.chat-only-canvas` | Local CSV read | gap | normal → 8px |

## 🟡 LOW (45)

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
| `chat.column` | Chat | width | 583.891px → 360px |
| `chat.column` | Chat | height | 727.031px → 562.25px |
| `chat.column` | Chat | borderStyle | none solid none none → none |
| `chat.column` | Chat | tag | <section> → <div> (semantic/default-style change) |
| `chat.transcript` | Chat | lineHeight | 19.5px → normal |
| `chat.transcript` | Chat | transition | all → none |
| `chat.transcript` | Chat | width | 582.891px → 336px |
| `chat.transcript` | Chat | height | 573.406px → 380.5px |
| `chat.composer` | Chat | lineHeight | 19.5px → normal |
| `chat.composer` | Chat | transition | border-color 0.12s → none |
| `chat.composer` | Chat | width | 562.891px → 336px |
| `chat.composer` | Chat | height | 77.375px → 147.75px |
| `studio.canvas` | Studio | lineHeight | 19.5px → normal |
| `studio.canvas` | Studio | transition | all → none |
| `studio.canvas` | Studio | width | 613.109px → 1200px |
| `studio.canvas` | Studio | height | 727.031px → 725.25px |
| `studio.canvas` | Studio | tag | <section> → <div> (semantic/default-style change) |
| `tool.csv.card` | Local CSV read | transition | all → none |
| `tool.csv.card` | Local CSV read | width | 550.891px → 320px |
| `tool.csv.card` | Local CSV read | height | 19px → 57.5px |
| `tool.csv.card` | Local CSV read | tag | <div> → <details> (semantic/default-style change) |
| `tool.csv.header` | Local CSV read | transition | all → none |
| `tool.csv.header` | Local CSV read | width | 548.891px → 318px |
| `tool.csv.header` | Local CSV read | tag | <div> → <summary> (semantic/default-style change) |
| `csv.chat-only-canvas` | Local CSV read | lineHeight | 19.5px → normal |
| `csv.chat-only-canvas` | Local CSV read | transition | all → none |
| `csv.chat-only-canvas` | Local CSV read | width | 613.109px → 807px |
| `csv.chat-only-canvas` | Local CSV read | height | 695.781px → 138px |
| `csv.chat-only-canvas` | Local CSV read | borderStyle | none → solid |
| `csv.chat-only-canvas` | Local CSV read | tag | <div> → <section> (semantic/default-style change) |

## ⚪ INFO (10)

| Element | Group | Property | Design → Live |
|---|---|---|---|
| `shell.frame` | Cockpit shell | text | “” → “0ACTIVE RUNMonday catch-up .run-header-pulse-dot { animation…” |
| `shell.header` | Cockpit shell | text | “” → “0ACTIVE RUNMonday catch-up .run-header-pulse-dot { animation…” |
| `shell.mode-switcher` | Cockpit shell | text | “FocusStudio” → “StudioFocus” |
| `chat.column` | Chat | text | “” → “Catch me up on ENG-142, then prepare the requested file. @ke…” |
| `chat.transcript` | Chat | text | “” → “Catch me up on ENG-142, then prepare the requested file. @ke…” |
| `chat.composer` | Chat | text | “” → “ToolsOpus 4.7 · Balanced↵ send⇧+↵ new line/ skillsOpus 4.7 ·…” |
| `studio.canvas` | Studio | text | “” → “RUN IN PROGRESSPreparing this runActivity will appear here i…” |
| `tool.csv.card` | Local CSV read | text | “” → “@keyframes tc-tool-card-spin { to { transform: rotate(360deg…” |
| `tool.csv.header` | Local CSV read | text | “” → “Ffs.readMCP · Workspaceread820 ms742 rows · 9 columns✓▾” |
| `csv.chat-only-canvas` | Local CSV read | text | “” → “RUN IN PROGRESSPreparing this runActivity will appear here i…” |
