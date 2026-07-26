# Design-parity report — chat-tool-call-shell · `studio-write-held`

Design baseline (source of truth) vs live app, by computed style.

- Design: `surfaces/chat-tool-call-shell/out/design-studio-write-held.json`
- Live: `surfaces/chat-tool-call-shell/out/live-studio-write-held.json`

**Summary:** 🔴 HIGH 9 · 🟠 MEDIUM 33 · 🟡 LOW 50 · ⚪ INFO 11

## 🔴 HIGH (9)

| Element | Group | Property | Design → Live |
|---|---|---|---|
| `shell.frame` | Cockpit shell | borderColor | rgba(255, 255, 255, 0.1) (--line2) → rgb(236, 236, 241) (--tx) |
| `shell.mode-switcher` | Cockpit shell | backgroundColor | rgb(17, 17, 20) (--panel) → rgb(9, 9, 11) |
| `chat.column` | Chat | backgroundColor | rgb(9, 9, 11) → rgba(0, 0, 0, 0) (transparent) |
| `chat.column` | Chat | borderColor | rgb(236, 236, 241) rgba(255, 255, 255, 0.06) rgb(236, 236, 241) rgb(236, 236, 241) → rgb(236, 236, 241) (--tx) |
| `studio.canvas` | Studio | backgroundColor | rgb(9, 9, 11) → rgba(0, 0, 0, 0) (transparent) |
| `write.review-surface` | Held write | borderColor | rgba(87, 199, 133, 0.3) → rgba(255, 255, 255, 0.06) (--line) |
| `write.review-status` | Held write | fontSize | 8.5px → 10.5px (+2.0px) |
| `write.review-status` | Held write | color | rgb(87, 199, 133) (--jade) → rgb(152, 152, 159) (--mut) |
| `write.review-status` | Held write | borderColor | rgba(87, 199, 133, 0.35) → rgba(255, 255, 255, 0.1) (--line2) |

## 🟠 MEDIUM (33)

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
| `tool.write.header` | Held write | display | flex → list-item |
| `tool.write.header` | Held write | alignItems | center → normal |
| `tool.write.header` | Held write | padding | 9px 11px → 0px |
| `tool.write.header` | Held write | gap | 9px → normal |
| `write.review-surface` | Held write | display | block → flex |
| `write.review-surface` | Held write | flexDirection | row → column |
| `write.review-surface` | Held write | boxShadow | none → rgba(0, 0, 0, 0.18) 0px 8px 32px 0px |
| `write.review-surface` | Held write | padding | 0px → 24px |
| `write.review-surface` | Held write | borderRadius | 10px → 16px |
| `write.review-surface` | Held write | gap | normal → 12px |
| `write.review-status` | Held write | fontWeight | 400 → 500 |
| `write.review-status` | Held write | padding | 2px 6px → 1px 8px |
| `write.review-status` | Held write | borderRadius | 5px → 999px |
| `write.review-status` | Held write | gap | 4px → 5px |

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
| `tool.write.card` | Held write | transition | all → none |
| `tool.write.card` | Held write | width | 550.891px → 320px |
| `tool.write.card` | Held write | height | 10.8281px → 74px |
| `tool.write.card` | Held write | tag | <div> → <details> (semantic/default-style change) |
| `tool.write.header` | Held write | transition | all → none |
| `tool.write.header` | Held write | width | 548.891px → 318px |
| `tool.write.header` | Held write | height | 55.5px → 72px |
| `tool.write.header` | Held write | tag | <div> → <summary> (semantic/default-style change) |
| `write.review-surface` | Held write | lineHeight | 19.5px → normal |
| `write.review-surface` | Held write | transition | all → none |
| `write.review-surface` | Held write | width | 550.891px → 807px |
| `write.review-surface` | Held write | height | 37.6562px → 448.75px |
| `write.review-surface` | Held write | tag | <div> → <section> (semantic/default-style change) |
| `write.review-status` | Held write | lineHeight | 12.75px → 15.75px |
| `write.review-status` | Held write | letterSpacing | 0.34px → normal |
| `write.review-status` | Held write | transition | all → none |
| `write.review-status` | Held write | width | 57.5312px → 112.5px |
| `write.review-status` | Held write | height | 18.75px → 19.75px |

## ⚪ INFO (11)

| Element | Group | Property | Design → Live |
|---|---|---|---|
| `shell.frame` | Cockpit shell | text | “” → “0ACTIVE RUNMonday catch-up .run-header-pulse-dot { animation…” |
| `shell.header` | Cockpit shell | text | “” → “0ACTIVE RUNMonday catch-up .run-header-pulse-dot { animation…” |
| `shell.mode-switcher` | Cockpit shell | text | “FocusStudio” → “StudioFocus” |
| `chat.column` | Chat | text | “” → “Catch me up on ENG-142, then prepare the requested file. @ke…” |
| `chat.transcript` | Chat | text | “” → “Catch me up on ENG-142, then prepare the requested file. @ke…” |
| `chat.composer` | Chat | text | “” → “ToolsOpus 4.7 · Balanced↵ send⇧+↵ new line/ skillsOpus 4.7 ·…” |
| `studio.canvas` | Studio | text | “” → “Run receipt readyIn progressThis receipt was assembled from …” |
| `tool.write.card` | Held write | text | “” → “@keyframes tc-tool-card-spin { to { transform: rotate(360deg…” |
| `tool.write.header` | Held write | text | “” → “Ffs.writeMCP · Workspaceread820 msChange staged for approval…” |
| `write.review-surface` | Held write | text | “” → “Workspace stageCreate workspace filerev 1 · YoucreateAwaitin…” |
| `write.review-status` | Held write | text | “new file” → “Awaiting review” |
