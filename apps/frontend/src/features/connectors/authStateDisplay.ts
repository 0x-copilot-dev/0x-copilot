// Display map for McpAuthState. Kept separate so adding a new state
// requires updating exactly one place — never let raw enum values
// (`auth_skipped` -> "auth skipped") leak into the UI.

import type { McpAuthState } from "@0x-copilot/api-types";

export type AuthStateTone =
  | "neutral"
  | "success"
  | "warning"
  | "danger"
  | "accent";

export interface AuthStateDisplay {
  label: string;
  tone: AuthStateTone;
  /** Human-friendly help line for the row (1 sentence). */
  hint: string;
}

const FALLBACK: AuthStateDisplay = {
  label: "Unknown",
  tone: "neutral",
  hint: "Connector status is unknown.",
};

const DISPLAY: Record<McpAuthState, AuthStateDisplay> = {
  unauthenticated: {
    label: "Not signed in",
    tone: "neutral",
    hint: "Sign in to let the agent call this connector.",
  },
  auth_pending: {
    label: "Waiting for sign-in",
    tone: "warning",
    hint: "OAuth flow started — finish in the popup window.",
  },
  authenticated: {
    label: "Connected",
    tone: "success",
    hint: "Signed in and available to the agent.",
  },
  auth_skipped: {
    label: "Auth skipped",
    tone: "accent",
    hint: "Agent will call this connector without OAuth.",
  },
  auth_failed: {
    label: "Sign-in failed",
    tone: "danger",
    hint: "The last sign-in attempt failed. Try again.",
  },
  auth_unsupported: {
    label: "No auth required",
    tone: "neutral",
    hint: "This connector does not require sign-in.",
  },
};

export function authStateDisplay(state: McpAuthState): AuthStateDisplay {
  return DISPLAY[state] ?? FALLBACK;
}

export function isAuthenticated(state: McpAuthState): boolean {
  return (
    state === "authenticated" ||
    state === "auth_skipped" ||
    state === "auth_unsupported"
  );
}
