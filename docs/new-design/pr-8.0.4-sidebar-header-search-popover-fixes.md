# PR 8.0.4 — Sidebar header / search / user-card popover fixes

> **Status:** Draft (PRD + Spec)
> **Plan reference:** Wave 8 follow-up to [PR 8.0](./pr-8.0-atlas-visual-fidelity.md). The parent PR's "After" column for the Sidebar row is implemented; this micro-PR closes the three concrete visible regressions still showing in the running app.
> **Owner:** frontend (sidebar JSX trim + sidebar-search CSS + user-card positioning rule)
> **Size:** **XS.** ≈ 25 LOC across 2 files (`Sidebar.tsx`, `apps/frontend/src/styles.css`). Zero schema, zero new dep, zero net new component.
> **Reads alongside:** [`pr-8.0-atlas-visual-fidelity.md`](./pr-8.0-atlas-visual-fidelity.md), [`pr-2.2-sidebar-user-card-keymap.md`](./pr-2.2-sidebar-user-card-keymap.md).
> **Sibling PRs:** none — this is a hotfix-shaped slice of PR 8.0's sidebar row.

---

## 0 · TL;DR

Three visible regressions in the live sidebar against the Atlas design (`shell.jsx`):

| #   | Today                                                                                                                                                                                                                                    | After                                                                                                                                                                                |
| --- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| 1   | Header top-right shows two icons: `↻` (Refresh) and `⤺` (Hide sidebar). The Atlas design has only the collapse toggle.                                                                                                                   | Header top-right shows only the collapse toggle. Refresh stays callable from the parent (it's auto-fetched on mount + via the workspace switcher), but is no longer chrome-level UI. |
| 2   | The "Search chats…" input has no scoped CSS, so it renders with the browser-default `<input type="search">` chrome (boxed border, native search affordances, off-theme focus ring).                                                      | The input is a flush row: small magnifier glyph + borderless input on the sidebar background. Focus ring is the single accent token used elsewhere.                                  |
| 3   | Clicking the Sarah-Chen user card "does nothing" — the popover does mount, but `.ui-dropdown__menu { position: absolute }` resolves against `<body>` because `.aui-user-card` has no `position: relative`. The popover lands off-screen. | Popover anchors against the user-card container (the intended ancestor), opens up-and-left, sits above the sidebar as designed.                                                      |

All three are within the parent PR 8.0's stated "Sidebar — After" column but were not actually wired in the followups (8.0.1 / 8.0.2 / 8.0.3) — they fall through the cracks because they are CSS-only or tiny JSX trims.

---

## 1 · PRD

### 1.1 Problem

Direct evidence:

- **(1) Refresh icon.** [`Sidebar.tsx:96-116`](../../apps/frontend/src/features/chat/components/sidebar/Sidebar.tsx#L96-L116) renders a refresh `IconButton` next to the sidebar-collapse `IconButton`. The Atlas design (`shell.jsx`) shows only a single `panel-collapse` icon-button on the top-right with the brand on the top-left. The refresh button is dead weight: list refresh runs on `ChatScreen` mount and on `auth.switchWorkspace()`; users almost never need a manual refresh, and chrome-level icons should be reserved for things they actually use.
- **(2) Search styling.** [`SidebarSearch.tsx`](../../apps/frontend/src/features/chat/components/sidebar/SidebarSearch.tsx) renders `<input type="search" class="aui-sidebar-search__input" />` inside `<label class="aui-sidebar-search">`. **There is no CSS rule** scoping `.aui-sidebar-search` or `.aui-sidebar-search__input` anywhere in `apps/frontend/src/styles.css` — verified by `grep`. The `<input>` is therefore subject to the browser default for `type="search"`, which is what the user sees in the screenshot.
- **(3) UserCard popover.** [`UserCard.tsx`](../../apps/frontend/src/features/chat/components/sidebar/UserCard.tsx) wraps the design-system `<Menu>` with `anchorRef={triggerRef}` and `side="up"`. The Menu primitive renders a single `<div class="ui-dropdown__menu ui-dropdown__menu--up">` — see [`packages/design-system/src/styles.css:515-543`](../../packages/design-system/src/styles.css#L515-L543) — which is `position: absolute` and resolves `bottom: calc(100% + var(--space-sm))` against the **nearest positioned ancestor**. `.aui-user-card` (the intended ancestor) has **no positioning rule** — verified by reading [`apps/frontend/src/styles.css:874-877`](../../apps/frontend/src/styles.css#L874-L877). The menu therefore lands relative to the page (or whichever ancestor _happens_ to be positioned at the time), so the user perceives "nothing happens."

### 1.2 Goals

1. **One vocabulary, used everywhere.** Don't rebuild the popover or the Menu primitive — fix the missing positioning context where it belongs (on the anchor wrapper).
2. **Cheapest possible fix per gap.** No new component, no new design-system token. CSS-only for #2 and #3; one-line JSX trim for #1.
3. **No keymap regression.** `⌘K` still focuses the search input. `⌘N` still opens a new chat. `⌘\` still toggles. None of these depend on the deleted refresh button.
4. **No prop-drop on `Sidebar`.** `onRefresh` stays in the prop signature so no callers break — it just isn't surfaced as chrome any more. (Removing the prop is a typing churn that buys nothing today and risks a `pr-8.0-followup`-shaped diff in `ChatScreen.tsx` and tests.)

### 1.3 Non-goals

- **Don't promote the magnifier glyph into design-system.** It's used in exactly one place; the design-system promotion path requires "stable + reusable + > 1 consumer."
- **Don't restyle the conversation list rows.** The screenshot's row layout is fine — that's downstream of PR 8.0's `<ConversationRow>`. The complaint is the chrome around it.
- **Don't change the popover's items.** The DevPersonaSwitcher / WorkspacePicker / Settings / Sign out menu contents are correct already; only the _positioning_ is broken.
- **Don't touch the sidebar-collapsed (icon-rail) variant.** It's already empty in the collapsed state; nothing in this PR makes that state worse.

### 1.4 Success criteria

- ✅ Sidebar header has exactly **one** icon-button on the top-right (collapse toggle) plus the brand on the top-left.
- ✅ The search input renders with a magnifier glyph + borderless field on the sidebar background; focus paints an accent ring matching the rest of the surface; placeholder reads `Search chats…`.
- ✅ Clicking Sarah Chen opens an anchored popover above the user card, fully visible, dismissable by Escape and outside-click. WorkspacePicker / Settings / Sign out items render and click through.
- ✅ Existing `Sidebar.test.tsx` + `UserCard.test.tsx` pass without modification (because we removed UI, not behavior, and the tests assert on roles/aria not on the removed refresh button — verified before the implementation lands).
- ✅ `npm run typecheck --workspace @enterprise-search/frontend` clean.
- ✅ `npm run build --workspace @enterprise-search/frontend` clean.
- ✅ Visual smoke in `make dev` against the Atlas design's sidebar mock: matches.

---

## 2 · Spec

### 2.1 Files touched

| File                                                                                                                                                 | Change                                                                                                                                                                                                                    |
| ---------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| [`apps/frontend/src/features/chat/components/sidebar/Sidebar.tsx`](../../apps/frontend/src/features/chat/components/sidebar/Sidebar.tsx)             | Remove the refresh `IconButton` from `aui-sidebar__header-actions`. Drop the now-unused `IconButton` import only if no other JSX in the file uses it (it isn't).                                                          |
| [`apps/frontend/src/styles.css`](../../apps/frontend/src/styles.css)                                                                                 | Add a small block of rules: `.aui-sidebar-search` (flex row + magnifier slot + padding) and `.aui-sidebar-search__input` (transparent background, no border, accent focus). Add `position: relative` to `.aui-user-card`. |
| [`apps/frontend/src/features/chat/components/sidebar/SidebarSearch.tsx`](../../apps/frontend/src/features/chat/components/sidebar/SidebarSearch.tsx) | Inject an inline `<svg>` magnifier glyph before the `<input>` so the visual reads "🔍 chat search field". Glyph is decorative; `aria-hidden`.                                                                             |

### 2.2 JSX shape after this PR

```tsx
// Sidebar.tsx — header
<div className="aui-sidebar__header">
  <LogoMark />
  {onToggleSidebar ? (
    <IconButton
      type="button"
      aria-label="Hide sidebar"
      data-tooltip="Hide sidebar (⌘\)"
      onClick={onToggleSidebar}
    >
      ⤺
    </IconButton>
  ) : null}
</div>
```

```tsx
// SidebarSearch.tsx
<label className="aui-sidebar-search">
  <svg
    className="aui-sidebar-search__icon"
    aria-hidden="true" /* magnifier path */
  />
  <span className="sr-only">Search threads</span>
  <input
    ref={ref}
    type="search"
    className="aui-sidebar-search__input"
    placeholder="Search chats…"
    /* …existing props… */
  />
</label>
```

### 2.3 CSS

```css
/* Sidebar search — flush row + glyph + borderless input. */
.aui-sidebar-search {
  align-items: center;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  display: flex;
  gap: 0.4rem;
  padding: 0 0.625rem;
}
.aui-sidebar-search:focus-within {
  border-color: var(--color-accent);
}
.aui-sidebar-search__icon {
  color: var(--color-text-subtle);
  flex: none;
  height: 0.85rem;
  width: 0.85rem;
}
.aui-sidebar-search__input {
  background: transparent;
  border: 0;
  color: inherit;
  flex: 1;
  font: inherit;
  font-size: 0.82rem;
  outline: none;
  padding: 0.45rem 0;
}
.aui-sidebar-search__input::-webkit-search-cancel-button {
  appearance: none;
}

/* Anchor positioning context for the user-card menu. The Menu primitive
 * renders position:absolute; without this rule the popover would resolve
 * against <body> and land off-screen. */
.aui-user-card {
  margin-top: auto;
  padding-top: var(--space-sm);
  position: relative; /* <-- new */
}
```

### 2.4 Behavior unchanged

- `⌘K` still focuses the search input via `searchRef.current?.focus()`.
- The `onRefresh` prop is preserved (parent still calls it on mount / workspace switch); it simply has no chrome trigger.
- `Menu`'s outside-pointerdown dismissal continues to fire, including when the click lands on the now-correctly-positioned popover's outside.

---

## 3 · Test plan

- **Existing unit tests** for `Sidebar` and `UserCard` run unchanged. Both currently assert on aria roles / labels / list rendering; neither asserts on the refresh button — confirmed by `grep -n "Refresh" apps/frontend/src/features/chat/components/sidebar/*.test.tsx`.
- **Visual smoke** in `make dev`: open the sidebar, verify header has only the collapse icon, search field reads as designed, clicking Sarah Chen pops a menu anchored above the card.
- **Typecheck** + **build** clean.

## 4 · Rollout

Single PR, single commit. No flag, no schema, no migration. Reverts cleanly by reverting the commit.
