# Design-parity report — `web-search`

Design baseline (source of truth) vs live app, by computed style.

- Design: `tools/design-parity/surfaces/chat-tool-calls/out/design-web-search.json`
- Live: `tools/design-parity/surfaces/chat-tool-calls/out/live-web-search.json`

**Summary:** 🔴 HIGH 0 · 🟠 MEDIUM 7 · 🟡 LOW 31 · ⚪ INFO 11

## 🟠 MEDIUM (7)

| Element         | Group     | Property       | Design → Live        |
| --------------- | --------- | -------------- | -------------------- |
| `tool.logo`     | Tool call | display        | grid → flex          |
| `tool.logo`     | Tool call | justifyContent | normal → center      |
| `agents.icon`   | Agents    | display        | block → flex         |
| `agents.icon`   | Agents    | justifyContent | normal → center      |
| `agents.icon`   | Agents    | alignItems     | normal → center      |
| `agents.status` | Agents    | display        | inline-block → block |
| `agents.status` | Agents    | borderRadius   | 50% → 999px          |

## 🟡 LOW (31)

| Element               | Group     | Property   | Design → Live                                     |
| --------------------- | --------- | ---------- | ------------------------------------------------- |
| `tool.card`           | Tool call | width      | 824px → 720px                                     |
| `tool.card`           | Tool call | height     | 168.672px → 57.5px                                |
| `tool.card`           | Tool call | tag        | <div> → <details> (semantic/default-style change) |
| `tool.header`         | Tool call | width      | 822px → 718px                                     |
| `tool.logo`           | Tool call | lineHeight | 13.5px → 9px                                      |
| `tool.name`           | Tool call | lineHeight | 16.5px → 15px                                     |
| `tool.name`           | Tool call | height     | 16.5px → 15px                                     |
| `tool.summary`        | Tool call | width      | 715.391px → 623px                                 |
| `tool.summary`        | Tool call | tag        | <div> → <span> (semantic/default-style change)    |
| `tool.details`        | Tool call | width      | 822px → 718px                                     |
| `tool.details`        | Tool call | height     | 175px → 188px                                     |
| `tool.details.label`  | Tool call | height     | 16.25px → 45px                                    |
| `tool.details.label`  | Tool call | tag        | <b> → <span> (semantic/default-style change)      |
| `tool.provenance`     | Tool call | width      | 104.609px → 101.047px                             |
| `agents.panel`        | Agents    | lineHeight | 19.5px → normal                                   |
| `agents.panel`        | Agents    | height     | 776px → 816px                                     |
| `agents.orchestrator` | Agents    | lineHeight | 19.5px → normal                                   |
| `agents.orchestrator` | Agents    | width      | 312px → 272px                                     |
| `agents.orchestrator` | Agents    | height     | 55.25px → 53.75px                                 |
| `agents.card`         | Agents    | lineHeight | 19.5px → normal                                   |
| `agents.card`         | Agents    | width      | 294px → 254px                                     |
| `agents.card`         | Agents    | height     | 71px → 54.5px                                     |
| `agents.icon`         | Agents    | lineHeight | 19.5px → 13px                                     |
| `agents.icon`         | Agents    | height     | 19.5px → 14px                                     |
| `agents.name`         | Agents    | width      | 177.031px → 109.031px                             |
| `agents.name`         | Agents    | height     | 34.5px → 17.25px                                  |
| `agents.model`        | Agents    | lineHeight | 12.75px → 12px                                    |
| `agents.model`        | Agents    | height     | 18.75px → 18px                                    |
| `agents.activity`     | Agents    | width      | 247px → 178px                                     |
| `agents.activity`     | Agents    | tag        | <div> → <p> (semantic/default-style change)       |
| `agents.status`       | Agents    | lineHeight | 19.5px → 13px                                     |

## ⚪ INFO (11)

| Element               | Group     | Property | Design → Live                                                        |
| --------------------- | --------- | -------- | -------------------------------------------------------------------- |
| `tool.card`           | Tool call | text     | “” → “@keyframes tc-tool-card-spin { to { transform: rotate(360deg…” |
| `tool.header`         | Tool call | text     | “” → “Wweb_searchMCP · web.searchread1.2s3 relevant sources · synt…” |
| `tool.logo`           | Tool call | text     | “◍” → “W”                                                            |
| `tool.name`           | Tool call | text     | “web.search” → “web_search”                                          |
| `tool.summary`        | Tool call | text     | “” → “3 relevant sources · synthesized below”                        |
| `tool.details`        | Tool call | text     | “” → “args{ "q": "payments gateway incident postmortem Jan 28" }re…” |
| `tool.provenance`     | Tool call | text     | “MCP · 3rd-party” → “MCP · web.search”                               |
| `agents.panel`        | Agents    | text     | “” → “AgentsOrchestratorCoordinating 1 active agentResearch · inci…” |
| `agents.orchestrator` | Agents    | text     | “” → “OrchestratorCoordinating 1 active agent”                       |
| `agents.card`         | Agents    | text     | “” → “Research · incident postmortemHaiku 4.5web.fetch × 3 · ranki…” |
| `agents.activity`     | Agents    | text     | “” → “web.fetch × 3 · ranking pages”                                 |
