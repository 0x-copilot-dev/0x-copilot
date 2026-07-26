# Composer Tools journeys

This matrix covers every supported Tools outcome across the desktop user journey.
The composer has one entry point: **`+` → Tools**. There is no standalone
`Tools N` pill or independently positioned Tools dialog. Rows marked **fixture**
need a configured provider/connector or a local test fixture; they must not be
simulated as a successful real integration.

Run the executable safe smoke from this worktree after staging the desktop
runtime:

```bash
APP_DIR="$PWD/apps/desktop" \
COPILOT_HOME="/Users/parthpahwa/Documents/work/enterprise-search/apps/desktop/resources" \
python3 tools/desktop-journeys/composer-tools/tools_popover.py
```

## Core navigation and interaction

| ID    | Entry state                 | Actions                                       | Expected outcome                                                             | Automation         |
| ----- | --------------------------- | --------------------------------------------- | ---------------------------------------------------------------------------- | ------------------ |
| CT-01 | First-run composer          | `+` → Tools                                   | No standalone pill; Tools view is visible and Web Search is the hit target.  | `tools_popover.py` |
| CT-02 | New Chat, Studio            | `+` → Tools                                   | View stays inside the correctly anchored `+` menu, never clipped.            | `tools_popover.py` |
| CT-03 | New Chat, Focus             | `+` → Tools                                   | Same disclosure and hit-target contract as Studio.                           | `tools_popover.py` |
| CT-04 | Bound Run, Studio and Focus | `+` → Tools                                   | Same menu in both modes; Web Search selection persists per composer.         | `tools_popover.py` |
| CT-05 | Any panel                   | Back, Escape, click outside                   | Back returns to `+` root; Escape and click-out close the menu.               | `tools_popover.py` |
| CT-06 | Keyboard-only               | Tab through, Space/Enter a switch             | Logical order, visible focus, truthful roles and `aria-checked`.             | component + E2E    |
| CT-07 | Narrow window / long names  | Resize to 320px and use long connector labels | Panel stays within viewport, scrolls internally, and rows remain actionable. | visual fixture     |
| CT-08 | Reduced motion              | Open/close with reduced motion preference     | No disruptive animation; menu is immediately usable.                         | visual fixture     |

## Built-ins and selection

| ID    | Entry state                          | Actions                                            | Expected outcome                                                                                     | Automation                        |
| ----- | ------------------------------------ | -------------------------------------------------- | ---------------------------------------------------------------------------------------------------- | --------------------------------- |
| CT-09 | Default policy permits Web search    | Send without touching Tools                        | Default run enables Web search; request does not send a contradictory opt-out.                       | API/E2E fixture                   |
| CT-10 | Web search on                        | Toggle it off, then send                           | The Tools header updates and the run sends `web_search_enabled: false`.                              | `tools_popover.py` + payload test |
| CT-11 | Web search policy blocked            | Open Tools                                         | Disabled row explains the policy state; it cannot be enabled client-side.                            | policy fixture                    |
| CT-12 | Research request with Web search     | Run a cited search task                            | Activity/citation card identifies Web search, has source links and handles no-result/provider error. | reuse `chat-rich-cards` fixture   |
| CT-13 | Local file/CSV available             | Attach/select approved CSV and ask for explanation | File read provenance is visible; no arbitrary path becomes selectable from the mock alone.           | local fixture                     |
| CT-14 | Local/browser capability unavailable | Open Tools                                         | It is absent or clearly unavailable, never a deceptive active control.                               | catalogue fixture                 |

## Connectors, OAuth, and permissions

| ID    | Entry state                            | Actions                                        | Expected outcome                                                                        | Automation            |
| ----- | -------------------------------------- | ---------------------------------------------- | --------------------------------------------------------------------------------------- | --------------------- |
| CT-15 | Installed authenticated read connector | Enable and send                                | Selected connector becomes server-known run scope; read card shows provider provenance. | connector fixture     |
| CT-16 | Installed connector paused             | Toggle off and send                            | It is omitted from run scope; trigger count updates.                                    | component/API         |
| CT-17 | Curated OAuth connector                | Connect → browser consent → return             | Only successful grant becomes selectable; cancel/failure leaves safe needs-auth state.  | desktop OAuth fixture |
| CT-18 | Pre-registered client connector        | Choose Set up                                  | Opens custom configuration; no blind catalog install/422.                               | component/desktop     |
| CT-19 | Custom MCP                             | Choose Custom MCP, submit valid/invalid config | Routes to configuration, validates safely, refreshes only on success.                   | desktop fixture       |
| CT-20 | OAuth expired after catalogue load     | Enable/send                                    | Run resolution rejects/remediates without leaking token details.                        | backend E2E           |
| CT-21 | Workspace/role policy blocks connector | Open/attempt selection                         | Clear blocked reason; no connector scope payload.                                       | policy fixture        |
| CT-22 | Read/write or mixed connector          | Select then prompt a mutation                  | Selection is allowed if policy permits; mutation pauses at explicit runtime approval.   | approval fixture      |

## Failure, recovery, and lifecycle

| ID    | Entry state                     | Actions                                   | Expected outcome                                                                          | Automation        |
| ----- | ------------------------------- | ----------------------------------------- | ----------------------------------------------------------------------------------------- | ----------------- |
| CT-23 | Catalogue loading/network error | Open/retry Tools                          | Stable loading/error state; composer and existing selection remain safe.                  | component/API     |
| CT-24 | Stale catalogue revision        | Load, revoke policy, then send            | Structured stale/invalid selection response refreshes the picker; no unsafe fallback.     | backend E2E       |
| CT-25 | Tool invocation fails           | Send permitted task with failing provider | Run activity/error card identifies capability and offers safe retry where supported.      | fixture           |
| CT-26 | Run reconnect/replay            | Disconnect while a tool runs              | Persisted event stream replays monotonic tool/activity events without duplicate approval. | runtime E2E       |
| CT-27 | Account/workspace switch        | Open tools before/after switch            | Catalogue and selection reset to the new verified scope.                                  | auth fixture      |
| CT-28 | Revocation/deletion             | Revoke connector/delete conversation      | Future runs cannot use grant; retention/deletion/audit rules hold.                        | backend lifecycle |

## Evidence required for release

- CT-01, CT-02, CT-05, CT-06, CT-10 and visual narrow/reduced-motion checks are
  required on every desktop composer change.
- CT-09, CT-11, CT-15–22, and CT-24–28 require service fixtures and run in the
  owning backend/AI-backend suites before a policy or connector change ships.
- The design-parity `chat-tool-call-shell` fixture reports zero high/medium
  visual regressions. It does not replace CT-01/02: a visual renderer cannot
  prove that the row, rather than a transparent scrim, receives pointer input.
