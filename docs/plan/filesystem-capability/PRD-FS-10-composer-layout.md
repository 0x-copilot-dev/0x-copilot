# PRD-FS-10 — Composer layout: folder bar, model pill right, bypass pill

**Status:** specified, not started
**Supersedes:** task #8 (FTUE wiring) — folded in here, see §7
**Depends on:** nothing shipped-blocking. PRD-FS-11 (bypass mode) supplies the
pill's behaviour; this PRD ships the pill in its **disabled/Manual** state so
the two can land independently.

---

## 1. Why

Two problems, one surface.

**The folder affordance is buried and inert.** "Attach Folder" is a row inside
the composer's `+` menu. A user has to open a menu to discover that the agent
can be given a folder at all, and once attached there is no persistent sign
that it happened. Claude Code and Codex both put the working folder _on the
frame_, above the input, where it reads as context rather than as an action.

**The `+` menu is the wrong home for it.** Every other row in that menu attaches
something _to the message_ — an image, a file. A folder grant is not an
attachment: nothing is copied into the message, and the grant **outlives the
message**, persisting until revoked. Housing it beside "Attach Image" teaches
the wrong mental model, which is why the current `×` on the pill has to say
"Stop sharing" rather than "Remove".

**The control row wastes its most valuable slot.** The model pill sits
immediately right of `Tools`, the first thing the eye reaches. Model choice is
set-and-forget. Execution mode — will this run _ask me_ or _just go_ — is the
decision a user re-makes per task, and it currently has no home at all.

## 2. Outcomes

A user can, without opening a menu:

1. see which folder the agent has, by name, before starting a chat;
2. attach or change it in one click;
3. see and change whether this run will ask before acting;
4. still change model, one slot further right.

## 3. Scope

**In:** `ComposerPlusMenu`, `AssistantComposer`, `composer.css`, the FTUE
composer mount, both host binders, tests.
**Out:** bypass _behaviour_ (PRD-FS-11), grant persistence (approval → durable
grant), `.copilot` creation, Windows.

---

## 4. Layout

### 4.1 Folder bar — above the composer

```
┌────────────────────────────────────────────┐
│ 🗀  kaleidoscope                            │   ← folder bar (new)
├────────────────────────────────────────────┤
│ Do anything                                │
│                                            │
│ +   ⏱ Manual ▾            🎙  ▣ 5.4 Sonnet │   ← control row (reordered)
└────────────────────────────────────────────┘
```

Reference: the Claude Code header row the user supplied — folder icon plus the
folder's **basename**, quiet weight, sitting on the composer's top edge.

**Visibility rule — the one that needs care.** The bar renders **only before the
first message of the chat is sent**. Rationale: it is orientation ("this is what
I'm working on"), and orientation is needed when starting, not mid-conversation
where the transcript already shows what the agent has been touching. After the
first send it disappears for the life of that chat.

- No grant + pre-first-message → bar renders as an **empty affordance**
  ("Attach a folder"), because a bar that only appears once you already have a
  folder cannot teach anyone that folders exist.
- Grant exists + post-first-message → **hidden**. The grant is still live; the
  Attach-Folder capability remains reachable from Settings. Do NOT re-add a `+`
  menu row as a fallback — that is the thing being removed.
- More than one grant → show the first by name and `+N` (e.g. `kaleidoscope +2`).

**Click** opens the OS folder picker via `WorkspaceGrantPort.requestGrant`.
**A `granted` outcome replaces/adds the grant; `cancelled` and `failed` must stay
distinguishable** — a dismissed dialog leaves the bar unchanged, a failure shows
its message inline. Collapsing those two is the defect (already pinned in
`WorkspaceGrantPort.test.ts`).

### 4.2 Control row order

| Before                                   | After                                                        |
| ---------------------------------------- | ------------------------------------------------------------ |
| `+` · Tools · **model** · … · mic · send | `+` · Tools · **bypass/manual** · … · mic · **model** · send |

Model moves right, immediately **before** the mic. The bypass pill takes the
slot the model pill vacated.

### 4.3 Bypass pill

Label: **Manual** (default) or **Bypass**. Same pill recipe as the model pill —
no new component, no new glyph.

Gating is three-tier and the master switch is **off by default**:

- master OFF (Settings) → pill renders **Manual, disabled**, with a tooltip
  pointing at Settings. It must **not** offer Bypass. Offered-but-ignored is
  worse than absent.
- master ON → pill is a real menu: Manual · Bypass.
- selection applies at **run** or **message** scope (PRD-FS-11 owns precedence:
  message > run > master).

---

## 5. Non-goals / explicit refusals

- **Do not** keep an Attach Folder row in the `+` menu "for discoverability".
  Two entry points to one capability is how the grant model got muddled.
- **Do not** show the bar mid-conversation "because the user might want it".
  The visibility rule is the product decision; relitigate it in a follow-up if
  it proves wrong, but do not soften it silently.
- **Do not** put a host path on screen. The bar shows the **basename** only.
  `WorkspaceGrantPort` is path-free by construction, enforced at compile time by
  `type PathFree<T>`; the bar must not become the reason that is loosened.

---

## 6. Implementation

### 6.1 `packages/chat-surface/src/composer/ComposerPlusMenu.tsx`

Delete the `onAttachFolder !== undefined` block (the `MenuRow` with
`Icon name="folder"`) and the `onAttachFolder` prop. Leave Attach Image and
Attach File untouched.

### 6.2 `packages/chat-surface/src/composer/WorkspaceFolderBar.tsx` (new)

Presentational. Props:

```ts
interface WorkspaceFolderBarProps {
  grants: readonly WorkspaceGrant[]; // path-free; `label` is the basename
  error: string | null; // a failed list/revoke is SHOWN
  busy: boolean; // picker open
  onAttach: () => void;
  onRevoke?: (grantId: string) => void;
}
```

Renders nothing when the host supplies no port (`grants` absent ⇒ caller passes
nothing and does not mount it). Reuses `Icon name="folder"` from the icon SSOT
and the existing pill/eyebrow recipes — **no new tokens**.

### 6.3 `packages/chat-surface/src/composer/AssistantComposer.tsx`

- Stop passing `onAttachFolder` to `ComposerPlusMenu`.
- Keep `useWorkspaceFolderGrants(workspaceGrantPort)` — it already owns the
  broker-is-truth refresh and the failed-read-keeps-last-list rule.
- Add prop `hasSentFirstMessage: boolean` (host-supplied; see §7 — do NOT infer
  it from transcript length inside the package, the hosts disagree on what
  counts as a message).
- Mount `WorkspaceFolderBar` above the composer frame when
  `workspaceGrantPort != null && !hasSentFirstMessage`.
- Remove the folder `AttachmentPill`s from the top bar (they move into the bar);
  keep skill pills there.

### 6.4 `packages/chat-surface/src/composer/BypassPill.tsx` (new)

Props: `mode: "manual" | "bypass"`, `enabled: boolean`, `onChange`. When
`enabled` is false it is a disabled Manual pill. No behaviour beyond selection —
PRD-FS-11 consumes the value.

### 6.5 `composer.css`

Folder-bar rules go in the **package's** `composer.css`, which both hosts
import. Not in `apps/frontend/src/styles.css`: the approval card's rules live
only there and that is exactly the stranded-CSS split that made the desktop
cockpit render a bare frame. Check the built desktop bundle for a duplicate
selector before assuming a rule applies (see `feedback_desktop_css_shadowing`).

### 6.6 Control row reorder

Single JSX move in `AssistantComposer`. Verify the tab order follows the visual
order afterwards — moving the model pill past the mic must not leave keyboard
focus jumping backwards.

---

## 7. Host wiring (absorbs task #8)

The FTUE composer never receives `workspaceGrantPort`, which is why Attach
Folder renders in the Run composer and not on first run — the exact screen where
attaching matters most. Both hosts must pass **the same bridged port** to both
mounts.

- **Desktop:** `renderer/destinationBinders.tsx` already calls
  `bridgeWorkspaceGrantPort()` for the Run composer; pass the same instance to
  the onboarding/FTUE composer mount. It memoizes per bridge, so this is not a
  second port.
- **Web:** no `WorkspaceGrantPort` exists. Pass `null`. The bar and the pill must
  both degrade to _absent_, not to a broken control.
- `hasSentFirstMessage` comes from each host's own conversation state.

---

## 8. Test plan

Package (`packages/chat-surface`):

1. `+` menu no longer offers Attach Folder — assert by **accessible name**, not
   test id: if a user cannot reach it by label it does not exist for them.
2. Bar renders with the folder **basename** pre-first-message.
3. Bar is **absent** post-first-message, with a live grant.
4. Bar renders its empty affordance with zero grants pre-first-message.
5. `cancelled` leaves the bar unchanged; `failed` shows its message.
6. Bar renders nothing when `workspaceGrantPort` is null (web).
7. No host-absolute path appears in the rendered DOM (guards §5).
8. Control row order: bypass pill precedes the mic; model pill follows it.
9. Bypass pill with master OFF is disabled and offers no Bypass option.

Desktop (`apps/desktop`): FTUE mount receives the port and shows the bar.
Run mount unchanged. Run from `apps/desktop`, not the repo root — the airdrop
fixture test reads `process.cwd()`.

Live (`tools/desktop-journeys/filesystem-access/`): extend
`attach_folder_row.py` — it currently asserts the `+` menu row exists, which
this PRD deletes, so **that assertion inverts**. New journey: attach a folder
pre-first-message, confirm the name on the bar, send a message, confirm the bar
disappears, confirm a read inside the folder does **not** prompt while a read
outside it still does.

## 9. Definition of done

- [ ] All nine package tests + desktop tests green
- [ ] `npm run build --workspace @0x-copilot/desktop` and re-stage
- [ ] Live journey passes, screenshot of the bar with a real folder name
- [ ] Web renders no bar and no bypass pill (null port), no console errors
- [ ] No host path in any rendered DOM or screenshot
