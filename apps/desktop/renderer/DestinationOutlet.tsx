import { type ReactElement } from "react";

import {
  DestinationPlaceholder,
  RunDestination,
  type ConversationId,
  type ShellDestinationSlug,
} from "@0x-copilot/chat-surface";

// The desktop destination outlet. It maps the shell's active destination to
// the surface that owns that destination's main-column content. Phase 2 mounts
// this in place of the static `DesktopPlaceholder`, so the renderer is a real
// profile-gated shell rather than a single "phase 1" signal.
//
// Phase 3 (PR-3.5) swaps the `run` case for the real cockpit: `RunDestination`
// (which composes `useRunSession` + `useRunMode` + `ThreadCanvas`). The other
// solo surfaces — Chats / Projects / Activity / Tools / Skills — are Phase 4 and
// still render the sanctioned `DestinationPlaceholder` primitive (honest: names
// the intent, names the phase, no fake data, no fetch). The outlet's contract is
// unchanged (a slug in, a surface out).
//
// Folding (DESIGN-SPEC §1/§3): the deprecated `agents` and `inbox` concepts are
// absorbed by Activity ("everything the agent has done"), so a defensive/legacy
// navigation to either slug resolves to the Activity surface rather than
// dead-ending.

// The conversation the Run cockpit binds to when the host does not supply one.
// PR-3.11 landed the cockpit's empty/idle + multi-run states, so an empty run
// list against this default id now renders the honest goal composer (start a
// run) rather than a bare idle cockpit — the surface is usable before any real
// conversation binding exists. Threading the *real* active conversation is
// still deferred: it arrives with the Chats → reopen-into-Run flow (Phase 4)
// and the full desktop outlet mount (PR-6.7). `useRunSession` resolves this
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

interface PlaceholderCopy {
  readonly title: string;
  readonly description: string;
  readonly phaseLabel: string;
}

// Per-destination placeholder copy, grounded in DESIGN-SPEC §1–§3. Keyed by the
// RESOLVED slug (after folding). Only the surfaces reachable from the solo rail
// (plus the Activity fold targets) need an entry; anything else falls back to a
// generic honest placeholder via `fallbackCopy`.
const DESTINATION_COPY: Partial<Record<ShellDestinationSlug, PlaceholderCopy>> =
  {
    run: {
      title: "Run",
      description:
        "The flagship cockpit — give the agent a goal and watch it do multi-step work across your files and connected apps, with every step laid out so you can watch, rewind, and stop it before it acts.",
      phaseLabel: "Coming in Phase 3",
    },
    chats: {
      title: "Chats",
      description:
        "Your conversations — pinned, recent, and archived — each reopening straight into its run. A place to pick a thread back up, not a second cockpit.",
      phaseLabel: "Coming in Phase 4",
    },
    projects: {
      title: "Projects",
      description:
        "Group related chats and files into a project, then open any of them from one place. A workspace for a body of work, not a single task.",
      phaseLabel: "Coming in Phase 4",
    },
    activity: {
      title: "Activity",
      description:
        "Everything the agent has done, grouped by day — every run, every step, recorded to local history. Retention, export, and delete live in Settings → Privacy.",
      phaseLabel: "Coming in Phase 4",
    },
    // Tools (slug `connectors`): the apps the agent can read from and act
    // through — a destination, not a settings tab.
    connectors: {
      title: "Tools",
      description:
        "The apps the agent can read from and act through, each with its own Read / Read & act / Off control. A destination for connecting tools — the approval policy itself lives in Settings → Model & behavior.",
      phaseLabel: "Coming in Phase 4",
    },
    // Skills (slug `tools`): saved multi-step workflows.
    tools: {
      title: "Skills",
      description:
        "Saved multi-step workflows you can re-run in one click — their own place, not a settings tab. Build a skill once, then hand it a goal whenever you need it.",
      phaseLabel: "Coming in Phase 4",
    },
  };

// A generic honest placeholder for any destination the map does not name (a
// defensive fallback — the solo rail never navigates here, but the outlet must
// never render a blank pane for an unexpected slug).
function fallbackCopy(slug: ShellDestinationSlug): PlaceholderCopy {
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
   * Conversation the Run cockpit binds to. Optional so the current bootstrap
   * (which has no active-conversation concept yet) mounts the outlet unchanged;
   * defaults to {@link DESKTOP_DEFAULT_CONVERSATION_ID}. A later PR threads the
   * real active conversation here.
   */
  readonly conversationId?: ConversationId;
}

export function DestinationOutlet({
  destination,
  conversationId = DESKTOP_DEFAULT_CONVERSATION_ID,
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
      {resolved === "run" ? (
        // PR-3.5: the flagship Run cockpit. Transport / KeyValueStore come from
        // the providers `ChatShell` installs above this outlet, so no props are
        // threaded here beyond the conversation binding. `enabled` defaults to
        // true — the outlet only mounts this for the `run` slug, so the session
        // + ⌘M handler are live exactly while Run is active.
        <RunDestination conversationId={conversationId} />
      ) : (
        <DestinationPlaceholder
          {...(DESTINATION_COPY[resolved] ?? fallbackCopy(resolved))}
        />
      )}
    </div>
  );
}
