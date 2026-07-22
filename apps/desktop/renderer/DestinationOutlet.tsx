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
   * The active conversation the Run cockpit binds to. `null` (or omitted) = a
   * brand-new chat with no conversation yet (the cockpit shows its empty
   * composer; the first send creates the conversation). Threaded from the nav
   * (Router URL → bootstrap `activeConversationId`); there is no hardcoded
   * default anymore.
   */
  readonly conversationId?: ConversationId | null;
  /**
   * Navigate the shell to the Run cockpit (no conversation id). Wired by
   * bootstrap to a new-run intent. The Phase-4 surfaces call it for open-run /
   * run-skill / new chat — the cockpit front door for STARTING a run.
   */
  readonly onOpenRun?: () => void;
  /**
   * Reopen a specific conversation (Chats → Run) with its REAL id. Bootstrap
   * navigates the Router to the conversation route; the outlet re-keys the
   * cockpit on the id so it resolves that conversation's transcript + run.
   */
  readonly onOpenConversation?: (id: ConversationId) => void;
  /**
   * The first send of a new chat created this conversation server-side.
   * Bootstrap navigates to it (updating the URL + active conversation), which
   * re-keys the cockpit onto the just-created conversation.
   */
  readonly onConversationCreated?: (id: ConversationId) => void;
  /** Open Settings → Privacy & retention (Activity's retention link). */
  readonly onOpenRetentionSettings?: () => void;
  /** Open Settings → Model & behavior (Tools' approval-policy note). */
  readonly onOpenApprovalSettings?: () => void;
  /**
   * Open Settings → Provider keys. The Run cockpit's empty-state uses it for the
   * "Set up your model" readiness CTA and the `configuration_error` "Add a
   * provider key" CTA (Issues 1 + 2).
   */
  readonly onOpenModelSettings?: () => void;
  /**
   * Navigate to the Tools (connectors) surface. The Run composer's connectors
   * trigger + `+`-menu "show connectors" use it for the MCP + non-MCP view.
   */
  readonly onOpenConnectors?: () => void;
  /** Navigate to the Skills surface (Run composer's skills settings link). */
  readonly onOpenSkills?: () => void;
}

export function DestinationOutlet({
  destination,
  conversationId = null,
  onOpenRun,
  onOpenConversation,
  onConversationCreated,
  onOpenRetentionSettings,
  onOpenApprovalSettings,
  onOpenModelSettings,
  onOpenConnectors,
  onOpenSkills,
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
        onOpenConversation,
        onConversationCreated,
        onOpenRetentionSettings,
        onOpenApprovalSettings,
        onOpenModelSettings,
        onOpenConnectors,
        onOpenSkills,
      })}
    </div>
  );
}

interface SurfaceContext {
  readonly conversationId: ConversationId | null;
  readonly onOpenRun?: () => void;
  readonly onOpenConversation?: (id: ConversationId) => void;
  readonly onConversationCreated?: (id: ConversationId) => void;
  readonly onOpenRetentionSettings?: () => void;
  readonly onOpenApprovalSettings?: () => void;
  readonly onOpenModelSettings?: () => void;
  readonly onOpenConnectors?: () => void;
  readonly onOpenSkills?: () => void;
}

function renderSurface(
  resolved: ShellDestinationSlug,
  ctx: SurfaceContext,
): ReactElement {
  switch (resolved) {
    case "run":
      // Transport / KeyValueStore come from the providers `ChatShell`
      // installs above this outlet, so only the conversation binding is
      // threaded. The `key` is load-bearing: it forces a CLEAN remount when the
      // bound conversation changes (reopen-from-Chats, or a new chat's first
      // send resolving to a real id), so the cockpit head-resolves the new
      // conversation instead of trying to bind across an identity change. A
      // brand-new chat (null) keys on the `"new"` sentinel.
      return (
        <RunBinder
          key={ctx.conversationId ?? "new"}
          conversationId={ctx.conversationId}
          onConversationCreated={ctx.onConversationCreated}
          onOpenModelSettings={ctx.onOpenModelSettings}
          onOpenConnectors={ctx.onOpenConnectors}
          onOpenSkills={ctx.onOpenSkills}
        />
      );
    case "chats":
      return (
        <ChatsBinder
          onOpenRun={ctx.onOpenRun}
          onOpenConversation={ctx.onOpenConversation}
        />
      );
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
