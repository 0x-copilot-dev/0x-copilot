# Design-parity report — `tools`

Design baseline (source of truth) vs live app, by computed style.

- Design: `surfaces/composer/out/design-tools.json`
- Live: `surfaces/composer/out/live-tools.json`

**Summary:** 🔴 HIGH 0 · 🟠 MEDIUM 6 · 🟡 LOW 13 · ⚪ INFO 11

## 🟠 MEDIUM (6)

| Element             | Group             | Property    | Design → Live                                 |
| ------------------- | ----------------- | ----------- | --------------------------------------------- |
| `tools.panel`       | B · Popover frame | gap         | normal → 0px                                  |
| `tools.header.meta` | B · Popover frame | margin      | 0px 0px 0px 116.469px → 0px 0px 0px 127.609px |
| `tools.web.toggle`  | C · Web Search    | display     | block → flex                                  |
| `tools.web.toggle`  | C · Web Search    | alignItems  | normal → center                               |
| `tools.web.toggle`  | C · Web Search    | padding     | 0px → 2px                                     |
| `tools.web.toggle`  | C · Web Search    | borderWidth | 1px → 0px                                     |

## 🟡 LOW (13)

| Element             | Group             | Property    | Design → Live                                                                                                                                                                            |
| ------------------- | ----------------- | ----------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `tools.trigger`     | A · Tools pill    | transition  | all → background-color 0.12s cubic-bezier(0.2, 0, 0, 1), border-color 0.12s cubic-bezier(0.2, 0, 0, 1), color 0.12s cubic-bezier(0.2, 0, 0, 1), opacity 0.12s cubic-bezier(0.2, 0, 0, 1) |
| `tools.trigger`     | A · Tools pill    | width       | 89px → 77px                                                                                                                                                                              |
| `tools.panel`       | B · Popover frame | height      | 389.75px → 188.25px                                                                                                                                                                      |
| `tools.header.meta` | B · Popover frame | width       | 48.625px → 108px                                                                                                                                                                         |
| `tools.list`        | B · Popover frame | height      | 264px → 95.75px                                                                                                                                                                          |
| `tools.web.row`     | C · Web Search    | transition  | all → background-color 0.12s cubic-bezier(0.2, 0, 0, 1), border-color 0.12s cubic-bezier(0.2, 0, 0, 1), color 0.12s cubic-bezier(0.2, 0, 0, 1), opacity 0.12s cubic-bezier(0.2, 0, 0, 1) |
| `tools.web.row`     | C · Web Search    | tag         | <div> → <button> (semantic/default-style change)                                                                                                                                         |
| `tools.web.toggle`  | C · Web Search    | lineHeight  | normal → 19.5px                                                                                                                                                                          |
| `tools.web.toggle`  | C · Web Search    | textAlign   | center → left                                                                                                                                                                            |
| `tools.web.toggle`  | C · Web Search    | transition  | background 0.15s, border-color 0.15s → background 0.12s cubic-bezier(0.2, 0, 0, 1)                                                                                                       |
| `tools.web.toggle`  | C · Web Search    | borderStyle | solid → none                                                                                                                                                                             |
| `tools.web.toggle`  | C · Web Search    | tag         | <button> → <span> (semantic/default-style change)                                                                                                                                        |
| `tools.custom`      | D · Custom MCP    | transition  | all → background-color 0.12s cubic-bezier(0.2, 0, 0, 1), border-color 0.12s cubic-bezier(0.2, 0, 0, 1), color 0.12s cubic-bezier(0.2, 0, 0, 1), opacity 0.12s cubic-bezier(0.2, 0, 0, 1) |

## ⚪ INFO (11)

| Element               | Group             | Property        | Design → Live                                                                                                                                                                              |
| --------------------- | ----------------- | --------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `tools.trigger`       | A · Tools pill    | text            | “Tools7/7” → “Tools1”                                                                                                                                                                      |
| `tools.trigger.count` | A · Tools pill    | text            | expected: Live displays the active count only (1), not the design mock's mock-data ratio (4/4). — “7/7” → “1”                                                                              |
| `tools.trigger.count` | A · Tools pill    | width           | expected: The active-only count is intentionally narrower than the mock-data ratio. — 18px → 6px                                                                                           |
| `tools.panel`         | B · Popover frame | text            | “Tools & connections 7 of 7 onWeb searchbuilt-in◇Safe{Wallet}…” → “Tools1 on · none required×Web searchbuilt-inLoading connecto…”                                                          |
| `tools.panel.scrim`   | B · Popover frame | missing-in-live | expected: Live Menu owns click-out through its body portal listener; it does not render a transparent scrim below the panel.                                                               |
| `tools.header`        | B · Popover frame | text            | expected: The product label is Tools, not the mock's Tools & connections. — “Tools & connections” → “Tools”                                                                                |
| `tools.header.meta`   | B · Popover frame | text            | expected: Live states active tools plus the approval posture; mock shows mock-data on/total. — “7 of 7 on” → “1 on · none required”                                                        |
| `tools.list`          | B · Popover frame | text            | “Web searchbuilt-in◇Safe{Wallet}acts3-of-5 multisig · BaseSGo…” → “Web searchbuilt-inLoading connectors…”                                                                                  |
| `tools.web.toggle`    | C · Web Search    | borderColor     | expected: Both tracks are borderless (0px); the live accent token on its inert border color has no rendered border. — rgba(0, 0, 0, 0) (transparent) → rgb(95, 178, 236) (--accent/--sky)  |
| `tools.custom`        | D · Custom MCP    | text            | expected: The product routes to Custom MCP server directly; the design mock says Connect a tool…. — “Connect a tool…catalog or custom MCP server” → “Custom MCP serverpaste a JSON config” |
| `tools.footer`        | D · Footer        | missing-in-live | expected: The shared composer content receives no management or policy navigation callbacks, so it does not invent the mock footer actions.                                                |
