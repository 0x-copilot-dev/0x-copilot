# Design-parity report — chat-tool-call-shell · `studio-wrap-file`

Design baseline (source of truth) vs live app, by computed style.

- Design: `surfaces/chat-tool-call-shell/out/design-studio-wrap-file.json`
- Live: `surfaces/chat-tool-call-shell/out/live-studio-wrap-file.json`

**Summary:** 🔴 HIGH 10 · 🟠 MEDIUM 36 · 🟡 LOW 50 · ⚪ INFO 11

## 🔴 HIGH (10)

| Element | Group | Property | Design → Live |
|---|---|---|---|
| `shell.frame` | Cockpit shell | borderColor | rgba(255, 255, 255, 0.1) (--line2) → rgb(236, 236, 241) (--tx) |
| `shell.mode-switcher` | Cockpit shell | backgroundColor | rgb(17, 17, 20) (--panel) → rgb(9, 9, 11) |
| `chat.column` | Chat | backgroundColor | rgb(9, 9, 11) → rgba(0, 0, 0, 0) (transparent) |
| `chat.column` | Chat | borderColor | rgb(236, 236, 241) rgba(255, 255, 255, 0.06) rgb(236, 236, 241) rgb(236, 236, 241) → rgb(236, 236, 241) (--tx) |
| `studio.canvas` | Studio | backgroundColor | rgb(9, 9, 11) → rgba(0, 0, 0, 0) (transparent) |
| `wrap.file-surface` | Wrap file | borderColor | rgba(87, 199, 133, 0.3) → rgba(255, 255, 255, 0.06) (--line) |
| `wrap.file-status` | Wrap file | fontFamily | typeface class changed (mono → sans) |
| `wrap.file-status` | Wrap file | fontSize | 8.5px → 12.48px (+4.0px) |
| `wrap.file-status` | Wrap file | color | rgb(87, 199, 133) (--jade) → rgb(152, 152, 159) (--mut) |
| `wrap.file-status` | Wrap file | borderColor | rgba(87, 199, 133, 0.35) → rgb(152, 152, 159) (--mut) |

## 🟠 MEDIUM (36)

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
| `tool.wrap-write.header` | Wrap file | display | flex → list-item |
| `tool.wrap-write.header` | Wrap file | alignItems | center → normal |
| `tool.wrap-write.header` | Wrap file | padding | 9px 11px → 0px |
| `tool.wrap-write.header` | Wrap file | gap | 9px → normal |
| `wrap.file-surface` | Wrap file | display | block → flex |
| `wrap.file-surface` | Wrap file | flexDirection | row → column |
| `wrap.file-surface` | Wrap file | boxShadow | none → rgba(0, 0, 0, 0.18) 0px 8px 32px 0px |
| `wrap.file-surface` | Wrap file | padding | 0px → 24px |
| `wrap.file-surface` | Wrap file | borderRadius | 10px → 16px |
| `wrap.file-surface` | Wrap file | gap | normal → 12px |
| `wrap.file-status` | Wrap file | fontWeight | 400 → 500 |
| `wrap.file-status` | Wrap file | display | flex → block |
| `wrap.file-status` | Wrap file | alignItems | center → normal |
| `wrap.file-status` | Wrap file | padding | 2px 6px → 0px |
| `wrap.file-status` | Wrap file | borderWidth | 1px → 0px |
| `wrap.file-status` | Wrap file | borderRadius | 5px → 0px |
| `wrap.file-status` | Wrap file | gap | 4px → normal |

## 🟡 LOW (50)

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
| `chat.column` | Chat | height | 727.031px → 418.766px |
| `chat.column` | Chat | borderStyle | none solid none none → none |
| `chat.column` | Chat | tag | <section> → <div> (semantic/default-style change) |
| `chat.transcript` | Chat | lineHeight | 19.5px → normal |
| `chat.transcript` | Chat | transition | all → none |
| `chat.transcript` | Chat | width | 582.891px → 336px |
| `chat.transcript` | Chat | height | 573.406px → 237.016px |
| `chat.composer` | Chat | lineHeight | 19.5px → normal |
| `chat.composer` | Chat | transition | border-color 0.12s → none |
| `chat.composer` | Chat | width | 562.891px → 336px |
| `chat.composer` | Chat | height | 77.375px → 147.75px |
| `studio.canvas` | Studio | lineHeight | 19.5px → normal |
| `studio.canvas` | Studio | transition | all → none |
| `studio.canvas` | Studio | width | 613.109px → 1200px |
| `studio.canvas` | Studio | height | 727.031px → 725.25px |
| `studio.canvas` | Studio | tag | <section> → <div> (semantic/default-style change) |
| `tool.wrap-write.card` | Wrap file | transition | all → none |
| `tool.wrap-write.card` | Wrap file | width | 550.891px → 320px |
| `tool.wrap-write.card` | Wrap file | height | 5.85938px → 74px |
| `tool.wrap-write.card` | Wrap file | tag | <div> → <details> (semantic/default-style change) |
| `tool.wrap-write.header` | Wrap file | transition | all → none |
| `tool.wrap-write.header` | Wrap file | width | 548.891px → 318px |
| `tool.wrap-write.header` | Wrap file | height | 55.5px → 72px |
| `tool.wrap-write.header` | Wrap file | tag | <div> → <summary> (semantic/default-style change) |
| `wrap.file-surface` | Wrap file | lineHeight | 19.5px → normal |
| `wrap.file-surface` | Wrap file | transition | all → none |
| `wrap.file-surface` | Wrap file | width | 550.891px → 807px |
| `wrap.file-surface` | Wrap file | height | 17.5938px → 400.75px |
| `wrap.file-surface` | Wrap file | tag | <div> → <section> (semantic/default-style change) |
| `wrap.file-status` | Wrap file | lineHeight | 12.75px → normal |
| `wrap.file-status` | Wrap file | transition | all → none |
| `wrap.file-status` | Wrap file | width | 57.5312px → 141.188px |
| `wrap.file-status` | Wrap file | height | 18.75px → 15px |
| `wrap.file-status` | Wrap file | borderStyle | solid → none |

## ⚪ INFO (11)

| Element | Group | Property | Design → Live |
|---|---|---|---|
| `shell.frame` | Cockpit shell | text | “” → “0ACTIVE RUNMonday catch-upStudioFocusWrites wait for youRun …” |
| `shell.header` | Cockpit shell | text | “” → “0ACTIVE RUNMonday catch-upStudioFocus” |
| `shell.mode-switcher` | Cockpit shell | text | “FocusStudio” → “StudioFocus” |
| `chat.column` | Chat | text | “” → “Catch me up on ENG-142, then prepare the requested file. @ke…” |
| `chat.transcript` | Chat | text | “” → “Catch me up on ENG-142, then prepare the requested file. @ke…” |
| `chat.composer` | Chat | text | “” → “ToolsOpus 4.7 · Balanced↵ send⇧+↵ new line/ skillsOpus 4.7 ·…” |
| `studio.canvas` | Studio | text | “” → “Run receipt readyCompletedThis receipt was assembled from th…” |
| `tool.wrap-write.card` | Wrap file | text | “” → “@keyframes tc-tool-card-spin { to { transform: rotate(360deg…” |
| `tool.wrap-write.header` | Wrap file | text | “” → “Ffs.writeMCP · Workspaceread820 msWorkspace file created✓▾” |
| `wrap.file-surface` | Wrap file | text | “” → “Workspace stageCreate workspace filerev 1 · YoucreateApplied…” |
| `wrap.file-status` | Wrap file | text | “new file” → “Applied state reported.” |
