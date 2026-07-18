import { type ReactElement } from "react";

import {
  DestinationPlaceholder,
  type ConversationId,
  type ShellDestinationSlug,
} from "@0x-copilot/chat-surface";

import {
  ActivityBinder,
  ChatsBinder,
  ConnectorsBinder,
  ProjectsBinder,
  RunBinder,
  SkillsBinder,
} from "./destinationBinders";

// The desktop destination outlet. It maps the shell's active destination to
// the surface that owns that destination's main-column content. The renderer
// is a real profile-gated shell — the host feeds a slug in, the outlet renders
// the surface out.
//
// - `run` → the flagship cockpit `RunDestination` (PR-3.5): `useRunSession` +
//   `useRunMode` + `ThreadCanvas`.
// - `chats` / `projects` / `activity` / `connectors` (Tools) / `tools`
//   (Skills) → the real Phase-4 surfaces from `@0x-copilot/chat-surface`, each
//   fed by a desktop binder (`./destinationBinders`) that fetches over the
//   shell's Transport port and honestly degrades to the surface's own
//   loading / empty / error state where a flow (create / edit / connect) is
//   not yet wired on desktop (PR-6.7).
// - Any other (unexpected) slug → the sanctioned `DestinationPlaceholder`
//   primitive, so the outlet never renders a blank pane.
//
// Folding (DESIGN-SPEC §1/§3): the deprecated `agents` and `inbox` concepts
// are absorbed by Activity ("everything the agent has done"), so a
// defensive/legacy navigation to either slug resolves to the Activity surface
// rather than dead-ending.

// The conversation the Run cockpit binds to when the host does not supply one.
// PR-3.11 landed the cockpit's empty/idle + multi-run states, so an empty run
// list against this default id renders the honest goal composer (start a run)
// rather than a bare idle cockpit. Threading the *real* active conversation
// (Chats → reopen-into-Run) is still deferred. `useRunSession` resolves this
// conversation's runs over the Transport port.
const DESKTOP_DEFAULT_CONVERSATION_ID = "desktop-default" as ConversationId;

// Slugs the outlet folds onto another destination before rendering. Activity is
// the recast of the old audit-log + agents + inbox surfaces.
const FOLDED_DESTINATIONS: Partial<
  Record<ShellDestinationSlug, ShellDestinationSlug>
> = {
  agents: "activity",
  inbox: "activity",
};

// A generic honest placeholder for any destination the outlet does not render
// a real surface for. The solo rail never navigates here — it is a defensive
// fallback so an unexpected slug never yields a blank pane.
function fallbackCopy(slug: ShellDestinationSlug): {
  readonly title: string;
  readonly description: string;
  readonly phaseLabel: string;
} {
  return {
    title: slug.charAt(0).toUpperCase() + slug.slice(1),
    description:
      "This destination isn't built yet. It will land in a later phase of the desktop redesign.",
    phaseLabel: "Coming soon",
  };
}

function resolveDestination(slug: ShellDestinationSlug): ShellDestinationSlug {
  return FOLDED_DESTINATIONS[slug] ?? slug;
}

export interface DestinationOutletProps {
  /** The shell's active destination slug (host-controlled). */
  readonly destination: ShellDestinationSlug;
  /**
   * Conversation the Run cockpit binds to. Optional so bootstrap (which has no
   * active-conversation concept yet) mounts the outlet unchanged; defaults to
   * {@link DESKTOP_DEFAULT_CONVERSATION_ID}.
   */
  readonly conversationId?: ConversationId;
  /**
   * Navigate the shell to the Run cockpit. Wired by bootstrap to the shell's
   * `handleNavigate("run")`. The Phase-4 surfaces call it for reopen /
   * open-run / run-skill (an honest interim — desktop has no per-conversation
   * run binding yet, so all three land on the cockpit front door).
   */
  readonly onOpenRun?: () => void;
  /** Open Settings → Privacy & retention (Activity's retention link). */
  readonly onOpenRetentionSettings?: () => void;
  /** Open Settings → Model & behavior (Tools' approval-policy note). */
  readonly onOpenApprovalSettings?: () => void;
}

export function DestinationOutlet({
  destination,
  conversationId = DESKTOP_DEFAULT_CONVERSATION_ID,
  onOpenRun,
  onOpenRetentionSettings,
  onOpenApprovalSettings,
}: DestinationOutletProps): ReactElement {
  // Fold deprecated slugs onto their recast surface BEFORE resolving content,
  // so `agents`/`inbox` render Activity (FR-2.23) rather than a dead pane.
  const resolved = resolveDestination(destination);

  return (
    <div
      data-testid="destination-outlet"
      data-destination={resolved}
      style={{ width: "100%", height: "100%", minHeight: 0 }}
    >
      {renderSurface(resolved, {
        conversationId,
        onOpenRun,
        onOpenRetentionSettings,
        onOpenApprovalSettings,
      })}
    </div>
  );
}

interface SurfaceContext {
  readonly conversationId: ConversationId;
  readonly onOpenRun?: () => void;
  readonly onOpenRetentionSettings?: () => void;
  readonly onOpenApprovalSettings?: () => void;
}

function renderSurface(
  resolved: ShellDestinationSlug,
  ctx: SurfaceContext,
): ReactElement {
  switch (resolved) {
    case "run":
      // Transport / KeyValueStore come from the providers `ChatShell`
      // installs above this outlet, so only the conversation binding is
      // threaded. `enabled` defaults to true — the outlet only mounts this
      // for the `run` slug, so the session + ⌘M handler are live exactly
      // while Run is active.
      return <RunBinder conversationId={ctx.conversationId} />;
    case "chats":
      return <ChatsBinder onOpenRun={ctx.onOpenRun} />;
    case "projects":
      return <ProjectsBinder />;
    case "activity":
      return (
        <ActivityBinder
          onOpenRun={ctx.onOpenRun}
          onOpenRetentionSettings={ctx.onOpenRetentionSettings}
        />
      );
    case "connectors":
      return (
        <ConnectorsBinder onOpenApprovalSettings={ctx.onOpenApprovalSettings} />
      );
    case "tools":
      return <SkillsBinder onOpenRun={ctx.onOpenRun} />;
    default:
      return <DestinationPlaceholder {...fallbackCopy(resolved)} />;
  }
}
