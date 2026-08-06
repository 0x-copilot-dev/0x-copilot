// <ProjectFilingChip> — "filed under [▾ (A) Acme renewal]", the control that
// says which project a chat belongs to, mounted BELOW the composer frame.
//
// Approved design (Option A — deliberately the quiet one): a mono micro-label
// and ONE pill. Filing is a fact about the chat, not an action the user takes
// per message, so it reads at the chrome tier the composer's other metadata
// uses (`.ui-cpill`) rather than competing with Send. The two rejected options
// were a full-width bar and a segmented control; both made a set-once decision
// look like a per-turn one.
//
// The MENU is option B of a second round: a compact 212px list, 28px rows, a
// 14px monogram and a bare check. The first cut reused `.ui-pop-row` — right
// for the `+` menu it was built for, and roughly twice the furniture a two-item
// pick-list needs; its per-row radios in particular read as a form you submit.
// Rejected alongside it: inline chips with no popover at all (deletes the
// positioning bug class, but reflows the page and dies past ~6 projects), a
// typeahead (scales, but contends with the composer for the caret), and a
// tile-less hue-dot list (quietest, but a project would read as a dot here and
// a lettered tile on its own card).
//
// Three rules this file exists to keep:
//
//   1. NEVER render `icon_emoji`. The server defaults it to 📁 for every
//      project (`0043_projects.sql:39`), which on desktop produced a wall of
//      identical folders. The glyph is the name's MONOGRAM on the project's
//      hue — the same rule, and the same `projectHueRamp`, that
//      `destinations/_shared/ProjectIconTile` established, so there is still
//      exactly one place a per-project colour is computed.
//   2. The popover is HOST-OWNED when the host wants it. This package cannot
//      portal (no `document`), so the component owns open state + the anchor
//      and hands both to an optional `renderMenu` slot — byte-for-byte the
//      seam `AssistantComposer` already uses for its `+` menu
//      (`AssistantComposerPlusMenuSlotArgs`). One popover pattern in the
//      composer, not two.
//   3. Outside-click is the HOST's, via `onDismiss` — deliberately NOT an
//      `onBlur` on the wrapper. A blur handler comparing `relatedTarget`
//      against the wrapper's DOM subtree closes the menu the instant a
//      portalling host renders it (a portal is outside that subtree), so the
//      convenience would break precisely the substrate the slot exists for.
//      The inline fallback therefore closes on selection or Escape only.
//
// Presentational: props + callbacks, no fetching, no router. The host binds
// `options` from its projects list and turns `onChange` into the chat's
// `project_id` write.

import type { ProjectColorHue, ProjectId } from "@0x-copilot/api-types";
import {
  useCallback,
  useRef,
  useState,
  type CSSProperties,
  type KeyboardEvent as ReactKeyboardEvent,
  type ReactElement,
  type ReactNode,
  type RefObject,
} from "react";

import { Icon } from "../icons/Icon";
// Direct file import rather than the `_shared` barrel: this is a cross-module
// reach (composer → destinations) and the narrow import keeps it honest about
// what it actually needs — the ONE per-project hue ramp.
import { projectHueRamp } from "../destinations/_shared/ProjectIconTile";

/** One project the chat can be filed under. */
export interface ProjectFilingOption {
  readonly id: ProjectId;
  readonly name: string;
  readonly colorHue: ProjectColorHue;
}

/**
 * Render-prop arguments for the host-owned menu popover, mirroring
 * {@link AssistantComposerPlusMenuSlotArgs}. The chip owns the open state, the
 * anchor element and the dismissal action; the host owns `createPortal` +
 * outside-click, both of which need `document`.
 *
 * - `open` — whether the menu should be shown.
 * - `anchorRef` — the chip root to position against.
 * - `onDismiss` — close the menu (the host's outside-click handler calls this).
 * - `children` — the already-rendered menu body.
 */
export interface ProjectFilingMenuSlotArgs {
  readonly open: boolean;
  readonly anchorRef: RefObject<HTMLDivElement | null>;
  readonly onDismiss: () => void;
  readonly children: ReactNode;
}

export interface ProjectFilingChipProps {
  /** The chat's current project, or `null` when it is filed nowhere. */
  readonly value: ProjectId | null;
  /** Every project the chat can be filed under. Host-supplied. */
  readonly options: ReadonlyArray<ProjectFilingOption>;
  /** Fires with the picked project, or `null` for "No project". */
  readonly onChange: (next: ProjectId | null) => void;
  /**
   * Optional escape hatch to project creation. Omitted → the "New project…"
   * row is ABSENT, not disabled: a host with nowhere to create a project must
   * not offer a row that does nothing.
   */
  readonly onCreateProject?: () => void;
  /** Read-only chrome (shared-chat recipient view, in-flight write). */
  readonly disabled?: boolean;
  /**
   * Has this chat already sent a message? Mirrors the composer prop of the same
   * name.
   *
   * Once the chat is under way the zone renders only when it has a FACT to
   * state — i.e. the chat IS filed. Before that it renders whatever helps you
   * decide: "New project" with no projects yet, the pill otherwise. The two
   * things it withdraws mid-conversation are a chore ("+ New project") and an
   * absence ("No project"); both are setup, and setup belongs before the work,
   * which is the same rule that takes the folder bar away after message one.
   *
   * Defaults to `false` (pre-first-message), so a caller that has not thought
   * about it gets the affordance rather than silently losing it.
   */
  readonly hasSentFirstMessage?: boolean;
  /** Host slot for the menu popover (portal + outside-click). */
  readonly renderMenu?: (args: ProjectFilingMenuSlotArgs) => ReactNode;
}

/**
 * The monogram glyph rule, kept byte-identical to `ProjectIconTile` (first
 * letter, upper-cased, `?` for an empty name) so the tile in this pill and the
 * tile on a project card can never disagree about a project's identity.
 */
function monogram(name: string): string {
  return (name.trim()[0] ?? "?").toUpperCase();
}

/**
 * The trigger's tile. `ProjectIconTile` cannot be reused verbatim here: its
 * `size` prop is typed as the literal `32` (32 is the design's only `.proj-ic`
 * size) and the pill is 26px tall, so a 32px tile would blow the pill's box
 * open. Only the GEOMETRY is restated — the colour still comes from the one
 * `projectHueRamp`, so no `hsl(...)` literal escapes that file.
 */
function triggerTileStyle(colorHue: ProjectColorHue): CSSProperties {
  const ramp = projectHueRamp(colorHue);
  return {
    width: 16,
    height: 16,
    // `--radius-sm` (6px), not a hand-written 4px: the design has no 4px rung,
    // and referencing an undefined token behind a fallback is how this layer
    // has twice shipped a value nothing could change.
    borderRadius: "var(--radius-sm)",
    display: "grid",
    placeItems: "center",
    flex: "none",
    boxSizing: "border-box",
    fontFamily: "var(--font-sans)",
    fontSize: 9,
    fontWeight: "var(--font-weight-semibold)",
    backgroundColor: ramp.background,
    border: ramp.border,
    color: ramp.color,
  };
}

/**
 * The menu row's tile — the same 14px geometry as the trigger's, because a row
 * is 28px tall and the shared `.ui-pop-row__lg` is a 24px glyph sized for the
 * `+` menu's much taller rows. Colour still comes from the one `projectHueRamp`.
 */
function rowTileStyle(colorHue: ProjectColorHue): CSSProperties {
  return triggerTileStyle(colorHue);
}

const rootStyle: CSSProperties = {
  position: "relative",
  display: "flex",
  alignItems: "center",
  gap: 8,
};

const labelStyle: CSSProperties = {
  // The recipe's ONE documented per-role override is colour; this role is the
  // quietest thing on the composer, so it takes the subtle rung.
  color: "var(--color-text-subtle)",
  flex: "none",
};

// Inline fallback frame. Hosts that portal ignore this entirely; web and tests
// get a real menu with no host wiring at all. `bottom: 100%` opens UPWARD — the
// chip is the last row of the composer, so a downward menu would land off the
// bottom of the viewport.
const inlineMenuStyle: CSSProperties = {
  position: "absolute",
  bottom: "calc(100% + 6px)",
  left: 0,
  minWidth: 220,
  maxWidth: 300,
  zIndex: 71,
};

export function ProjectFilingChip(
  props: ProjectFilingChipProps,
): ReactElement | null {
  const {
    value,
    options,
    onChange,
    onCreateProject,
    disabled = false,
    hasSentFirstMessage = false,
    renderMenu,
  } = props;

  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement | null>(null);
  const triggerRef = useRef<HTMLButtonElement | null>(null);

  const selected = options.find((option) => option.id === value) ?? null;

  // Dismissal always returns focus to the pill. Without it, closing from a menu
  // item drops focus to <body> and the next Tab restarts at the top of the
  // document — the classic menu-close focus loss.
  const dismiss = useCallback((): void => {
    setOpen(false);
    triggerRef.current?.focus();
  }, []);

  const pick = useCallback(
    (next: ProjectId | null): void => {
      onChange(next);
      dismiss();
    },
    [dismiss, onChange],
  );

  const handleEscape = useCallback(
    (event: ReactKeyboardEvent<HTMLElement>): void => {
      if (event.key !== "Escape") return;
      event.preventDefault();
      event.stopPropagation();
      dismiss();
    },
    [dismiss],
  );

  const renderOption = (option: ProjectFilingOption): ReactElement => {
    const active = option.id === value;
    return (
      <button
        key={option.id}
        type="button"
        role="menuitemradio"
        aria-checked={active}
        className="aui-filing-row"
        data-testid="composer-project-filing-option"
        data-project-id={option.id}
        data-on={active || undefined}
        onClick={() => pick(option.id)}
      >
        <span style={rowTileStyle(option.colorHue)} aria-hidden="true">
          {monogram(option.name)}
        </span>
        <span className="aui-filing-row__name">{option.name}</span>
        <span className="aui-filing-row__check" aria-hidden="true">
          {active ? <Icon name="check" size={11} strokeWidth={2.5} /> : null}
        </span>
      </button>
    );
  };

  const menuBody = (
    <div
      role="menu"
      aria-label="File this chat under a project"
      // `.ui-pop` for the CHROME (one popover background/border/shadow), plus
      // this menu's own row density — see the `.aui-filing-*` block in
      // composer.css for why the `+` menu's recipe is wrong here.
      className="ui-pop aui-filing-menu"
      data-testid="composer-project-filing-menu"
      // Escape is bound HERE as well as on the root: with a portalling host the
      // menu is not a DOM descendant of the root, so a root-only handler would
      // never see a keystroke made while focus is inside the menu.
      onKeyDown={handleEscape}
    >
      {options.map(renderOption)}

      <div className="aui-filing-sep" aria-hidden="true" />

      {/* "No project" is a real filing choice, not a reset affordance on the
          trigger — unfiling a chat is as deliberate as filing it. It sits below
          the separator with "New project…" because both are about the LIST
          rather than a member of it. */}
      <button
        type="button"
        role="menuitemradio"
        aria-checked={value === null}
        className="aui-filing-row"
        data-testid="composer-project-filing-none"
        data-on={value === null || undefined}
        onClick={() => pick(null)}
      >
        <span className="aui-filing-row__name">No project</span>
        <span className="aui-filing-row__check" aria-hidden="true">
          {value === null ? (
            <Icon name="check" size={11} strokeWidth={2.5} />
          ) : null}
        </span>
      </button>

      {onCreateProject !== undefined ? (
        <button
          type="button"
          role="menuitem"
          className="aui-filing-row aui-filing-row--new"
          data-testid="composer-project-filing-new"
          onClick={() => {
            dismiss();
            onCreateProject();
          }}
        >
          <span className="aui-filing-row__name">New project…</span>
        </button>
      ) : null}
    </div>
  );

  // With a host slot the slot is called UNCONDITIONALLY and receives `open` —
  // the host may want to keep a portal mounted across open/close. Without one,
  // the inline frame mounts only while open.
  const menuSlot: ReactNode =
    renderMenu !== undefined ? (
      renderMenu({
        open,
        anchorRef: rootRef,
        onDismiss: dismiss,
        children: menuBody,
      })
    ) : open ? (
      <div style={inlineMenuStyle}>{menuBody}</div>
    ) : null;

  // The zone is PRE-FIRST-MESSAGE ONLY, in both Studio and Focus.
  //
  // Filing is orientation: you decide where work belongs as you start it, the
  // same moment you decide which folder the agent may read. Once a transcript
  // exists the row is chrome under the thing you are actually reading, and both
  // of its states earn their place badly — "+ New project" is a chore, and
  // "FILED UNDER · No project" is an absence dressed as a decision.
  //
  // This mirrors the folder bar exactly (PRD-FS-10 §4.1), which leaves for the
  // same reason. Re-filing an in-progress chat is NOT lost with it: the Chats
  // row's ⋯ → "Move to project" owns that, which is the surface whose job is
  // acting on a chat you are not currently in.
  if (hasSentFirstMessage) return null;

  // ZERO PROJECTS is not "filed under nothing" — it is "you have none yet", and
  // the only useful thing to offer is the way to make one. The picker's own
  // chrome (`FILED UNDER` + a `No project` pill) reports an absence as though it
  // were a filing decision, and buries the one live affordance a click deep at
  // the bottom of a menu whose other rows do not exist. So the empty state is a
  // direct action instead, at the same `.ui-cpill` tier.
  //
  // With nothing to pick AND no way to create, there is nothing to say at all —
  // render nothing rather than a control over an empty set. The desktop host
  // guards this case too; keeping it here means the component is honest on its
  // own rather than relying on every host to hold it.
  //
  // Known window: the host's project list is module-cached but empty on the very
  // first render of a session, so a user who HAS projects can see this for one
  // frame before the list lands. Today that same frame shows them "No project",
  // which is equally untrue; fixing it properly means a `loading` signal on the
  // binding, not a guess here.
  if (options.length === 0) {
    if (onCreateProject === undefined) return null;
    return (
      <div
        style={rootStyle}
        className="aui-composer-filing"
        data-testid="composer-project-filing"
      >
        <button
          type="button"
          className="ui-cpill aui-composer-filing__pill"
          data-testid="composer-project-filing-create"
          disabled={disabled}
          onClick={onCreateProject}
        >
          <Icon name="plus" size={11} />
          <span className="ui-cpill__lb">New project</span>
        </button>
      </div>
    );
  }

  return (
    <div
      ref={rootRef}
      style={rootStyle}
      className="aui-composer-filing"
      data-testid="composer-project-filing"
      data-open={open || undefined}
      onKeyDown={handleEscape}
    >
      <span
        className="ui-mono-caps ui-mono-caps--9"
        style={labelStyle}
        aria-hidden="true"
      >
        filed under
      </span>
      <button
        ref={triggerRef}
        type="button"
        className="ui-cpill aui-composer-filing__pill"
        data-testid="composer-project-filing-trigger"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label={
          selected === null
            ? "Filed under: no project"
            : `Filed under: ${selected.name}`
        }
        disabled={disabled}
        data-open={open || undefined}
        onClick={() => setOpen((current) => !current)}
      >
        {/* No tile in the unfiled state: an empty or placeholder tile would
            read as a project whose name we failed to load. */}
        {selected !== null ? (
          <span style={triggerTileStyle(selected.colorHue)} aria-hidden="true">
            {monogram(selected.name)}
          </span>
        ) : null}
        <span
          className="ui-cpill__lb"
          style={
            selected === null
              ? { color: "var(--color-text-subtle)" }
              : undefined
          }
        >
          {selected?.name ?? "No project"}
        </span>
        <Icon name="chevronDown" size={11} />
      </button>
      {menuSlot}
    </div>
  );
}
