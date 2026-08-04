# projects-filing — a chat can be filed into a project

## Why this set exists

Projects on desktop was a **container nothing could enter**. `POST` and `PATCH`
have accepted `project_id` since Phase 6.5 and the facade already translates the
`filter[project_id]` axis — but no desktop surface ever wrote the field. Every
`project_id` in the renderer was a read filter. So a project card read
`0 chats · 0 files` forever, and the detail view was permanently empty. The
counts were never a bug; they were the honest output of a surface with no write.

Unit tests cannot see any of this. They can assert a slot was called, a callback
fired, a projector carried a field. They cannot assert that the write reached
the server, or that the popup it opened landed anywhere sensible on screen. Both
of those failed here, and only this journey caught them.

## `file_a_chat.py`

**Story.** A new user has no projects. They create one, file a brand-new chat
into it before typing anything, send the first message, and the project's card
finally shows the chat.

**The load-bearing assertion** is the create path, not the PATCH. Picking a
project _before the first message_ must carry `project_id` onto
`POST /v1/agent/conversations`, because a chat started inside a project is the
flow the whole design is built around. A PATCH-only implementation passes every
unit test and fails right here.

Every claim that matters is read back through the app's authenticated transport
rather than off the DOM. The DOM proves the UI changed; the server proves the
filing persisted. Those are different claims, and only the second is the feature.

| Step                 | Asserts                                                  |
| -------------------- | -------------------------------------------------------- |
| sign in → add key    | reaches the first-run composer                           |
| chip with 0 projects | recorded, not asserted — see Findings                    |
| skip → workspace     | the nav rail exists (first-run has none)                 |
| create a project     | `POST /v1/projects` produced a real id                   |
| back to Run          | the chip exists AND its rect is below the composer frame |
| open the menu        | the menu rect does **not** intersect the composer frame  |
| pick the project     | the chip adopts the name                                 |
| send first message   | **the created conversation carries `project_id`**        |
| open Projects        | the card no longer reads `0 chats`                       |

Two assertions are geometric on purpose. "The chip is below the composer" and
"the menu clears the composer" are the product decision (folders above, project
below); a refactor that moved the chip into the control row would still render a
chip and still pass every unit test.

### testIds it depends on

`first-run-skip` · `projects-destination` · `projects-create` · `project-editor`
· `project-editor-name-input` · `composer-project-filing` ·
`composer-project-filing-trigger` · `composer-project-filing-menu` ·
`composer-project-filing-option[data-project-id]` · `composer-textarea` ·
`project-card-counts`

### Run it

```bash
# frontend-only change ⇒ no re-stage; just rebuild the renderer
npm run build --workspace @0x-copilot/desktop

COPILOT_HOME=/path/to/main/apps/desktop/resources \
APP_DIR="$PWD/apps/desktop" \
  python3 tools/desktop-journeys/projects-filing/file_a_chat.py
```

Needs `ANTHROPIC_API_KEY` in `services/ai-backend/.env` (never printed). In a
worktree, symlink the main checkout's `.env` — it is gitignored.

## Findings — all four fixed, each now asserted here

The journey found these on its first green run; they are now regressions this
script catches rather than notes someone has to remember.

1. **The filing chip was hidden until a project existed.** The binder gated the
   zone on `options.length > 0`, so a fresh install never showed filing at all —
   at exactly the moment a user has no projects and most needs the way in. The
   chip now renders whenever there is something to pick **or** a way to create
   one, and `useProjectCreate` gives "New project…" something to do. Creating
   from the chip also **files the chat into the new project**: the click meant
   "put this chat somewhere new", so stopping at creation would be a no-op.
2. **The create sheet said "Edit project" / "Save"** for a project that did not
   exist yet. `ProjectEditor` takes `mode` (defaulting to `"edit"`, so no other
   caller changes) and says "New project" / "Create".
3. **A "Members" tab rendered for everyone.** Neither host passes
   `renderMembersTab`, so every user on both surfaces got a tab whose only
   content was an internal "not wired" notice — and on `single_user_desktop` it
   advertised a team surface the rail and settings nav both gate off. The tab now
   follows the package's standard "omitted ⇒ not rendered" rule.
4. **"1 chats"** is now "1 chat".

### Still open

**The FTUE composer has no filing zone.** `first-run-composer` is a different
binder (`FirstRunGate`), and it deliberately strips controls — thinking depth is
hidden there too. So the very first message a user sends cannot be filed. That is
arguably right (the FTUE's job is to reach a first run, and a picker with no
projects is noise), but it is a decision, not an accident. The journey prints the
state rather than asserting it, so a change either way is visible.

## Fixed by this journey

**The filing menu opened over the composer.** The desktop binder reused
`renderPlusMenu` verbatim as the chip's `renderMenu`. That renderer hard-codes
upward placement (`bottom: innerHeight - rect.top`), which is right for the `+`
button _inside_ the frame and wrong for a chip _below_ it — the panel drew
straight over the Tools / Manual / model row.

`DesktopAnchoredPlusMenu` now takes `placement` (`"up"` default, so the `+` menu
is byte-unchanged) and flips only when the preferred side genuinely cannot hold
the **measured** panel. The first attempt at that fix compared against a 320px
constant while the real panel is ~135px, so it flipped every time and the
journey caught it a second time — `.ui-pop__list` clamps itself to 264px and
scrolls, so the fits-check now measures the portal instead of guessing.
