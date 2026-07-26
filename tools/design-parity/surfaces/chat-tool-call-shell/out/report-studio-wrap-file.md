# Design-parity report — chat-tool-call-shell · `studio-wrap-file`

Design baseline (source of truth) vs live app, by computed style.

- Design: `surfaces/chat-tool-call-shell/out/design-studio-wrap-file.json`
- Live: `surfaces/chat-tool-call-shell/out/live-studio-wrap-file.json`

**Summary:** 🔴 HIGH 1 · 🟠 MEDIUM 11 · 🟡 LOW 41 · ⚪ INFO 10

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
| `shell.mode-switcher` | Cockpit shell | margin | 0px 0px 0px 982.969px → 0px 0px 0px 697.75px |
| `chat.composer` | Chat | flexDirection | row → column |
| `chat.composer` | Chat | gap | normal → 0px |
| `studio.canvas` | Studio | display | flex → block |
| `studio.canvas` | Studio | flexDirection | column → row |
| `studio.canvas` | Studio | flexGrow | flex-grow 0 → 1 (affects vertical fill / button placement) |

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
| `chat.column` | Chat | height | 727.031px → 566.203px |
| `chat.column` | Chat | tag | <section> → <div> (semantic/default-style change) |
| `chat.transcript` | Chat | lineHeight | 19.5px → normal |
| `chat.transcript` | Chat | transition | all → none |
| `chat.transcript` | Chat | width | 582.891px → 583px |
| `chat.transcript` | Chat | height | 573.406px → 439.453px |
| `chat.composer` | Chat | lineHeight | 19.5px → normal |
| `chat.composer` | Chat | transition | border-color 0.12s → none |
| `chat.composer` | Chat | width | 562.891px → 583px |
| `chat.composer` | Chat | height | 77.375px → 126.75px |
| `studio.canvas` | Studio | lineHeight | 19.5px → normal |
| `studio.canvas` | Studio | transition | all → none |
| `studio.canvas` | Studio | width | 613.109px → 1198px |
| `studio.canvas` | Studio | height | 727.031px → 756px |
| `studio.canvas` | Studio | tag | <section> → <div> (semantic/default-style change) |
| `tool.wrap-write.card` | Wrap file | transition | all → none |
| `tool.wrap-write.card` | Wrap file | width | 550.891px → 551px |
| `tool.wrap-write.card` | Wrap file | height | 5.85938px → 57.5px |
| `tool.wrap-write.card` | Wrap file | tag | <div> → <details> (semantic/default-style change) |
| `tool.wrap-write.header` | Wrap file | transition | all → none |
| `tool.wrap-write.header` | Wrap file | width | 548.891px → 549px |
| `tool.wrap-write.header` | Wrap file | tag | <div> → <summary> (semantic/default-style change) |
| `wrap.file-surface` | Wrap file | transition | all → none |
| `wrap.file-surface` | Wrap file | width | 550.891px → 613px |
| `wrap.file-surface` | Wrap file | height | 17.5938px → 443.75px |
| `wrap.file-surface` | Wrap file | tag | <div> → <section> (semantic/default-style change) |
| `wrap.file-status` | Wrap file | transition | all → none |
| `wrap.file-status` | Wrap file | width | 57.5312px → 52.0938px |

## ⚪ INFO (10)

| Element | Group | Property | Design → Live |
|---|---|---|---|
| `shell.frame` | Cockpit shell | text | “” → “.run-destination [data-testid="tc-chat"] { box-sizing: borde…” |
| `shell.header` | Cockpit shell | text | “” → “0ACTIVE RUNMonday catch-upWrites wait for youFocusStudio” |
| `chat.column` | Chat | text | “” → “Catch me up on ENG-142, then prepare the requested file. @ke…” |
| `chat.transcript` | Chat | text | “” → “Catch me up on ENG-142, then prepare the requested file. @ke…” |
| `chat.composer` | Chat | text | “” → “ToolsOpus 4.7 · Balanced↵ send⇧+↵ new line/ skillsOpus 4.7 ·…” |
| `studio.canvas` | Studio | text | “” → “Run receiptCompletedReview/workspace/standup-2026-02-09.md×W…” |
| `tool.wrap-write.card` | Wrap file | text | “” → “@keyframes tc-tool-card-spin { to { transform: rotate(360deg…” |
| `tool.wrap-write.header` | Wrap file | text | “” → “Ffs.writeMCP · Workspaceread820 msWorkspace file created✓▾” |
| `wrap.file-surface` | Wrap file | text | “” → “Workspace stageCreate workspace filerev 1 · YoucreateApplied…” |
| `wrap.file-status` | Wrap file | text | “new file” → “Applied” |
