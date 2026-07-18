import { describe, expect, it } from "vitest";

import type { DeploymentProfile } from "../providers/DeploymentProfileProvider";

import {
  DEFAULT_SHELL_DESTINATION,
  SHELL_DESTINATIONS,
  defaultDestinationForProfile,
  destinationsForProfile,
  type ShellDestination,
} from "./destinations";

const slugs = (list: readonly ShellDestination[]): string[] =>
  list.map((d) => d.slug);
const labels = (list: readonly ShellDestination[]): string[] =>
  list.map((d) => d.label);

describe("destinationsForProfile — single_user_desktop (solo)", () => {
  const solo = destinationsForProfile("single_user_desktop");

  it("returns exactly the 6 solo destinations in order (FR-2.3)", () => {
    expect(slugs(solo)).toEqual([
      "run",
      "chats",
      "projects",
      "activity",
      "connectors",
      "tools",
    ]);
  });

  it("relabels connectors→Tools and tools→Skills, slug identity preserved (FR-2.8)", () => {
    expect(labels(solo)).toEqual([
      "Run",
      "Chats",
      "Projects",
      "Activity",
      "Tools",
      "Skills",
    ]);
    // The relabel is a LABEL change only — the underlying slugs stay
    // `connectors` / `tools` so web URLs and routing stay green.
    expect(solo.find((d) => d.label === "Tools")?.slug).toBe("connectors");
    expect(solo.find((d) => d.label === "Skills")?.slug).toBe("tools");
  });

  it("exposes none of the folded/legacy or team slugs (US-2.1)", () => {
    const soloSlugs = new Set(slugs(solo));
    for (const absent of [
      "home",
      "agents",
      "library",
      "inbox",
      "todos",
      "memory",
      "routines",
      "team",
      "members",
      "billing",
    ]) {
      expect(soloSlugs.has(absent)).toBe(false);
    }
  });
});

describe("destinationsForProfile — team", () => {
  const team = destinationsForProfile("team");

  it("returns the 6 solo destinations followed by team, members, billing (FR-2.4)", () => {
    expect(slugs(team)).toEqual([
      "run",
      "chats",
      "projects",
      "activity",
      "connectors",
      "tools",
      "team",
      "members",
      "billing",
    ]);
    expect(team).toHaveLength(9);
  });

  it("carries the team-only labels", () => {
    expect(labels(team).slice(6)).toEqual(["Team", "Members", "Billing"]);
  });
});

describe("destinationsForProfile — fail-safe fallback (FR-2.5)", () => {
  it("falls back to the solo set for an unknown profile (no team leakage)", () => {
    const unknown = destinationsForProfile(
      "enterprise" as unknown as DeploymentProfile,
    );
    expect(slugs(unknown)).toEqual(
      slugs(destinationsForProfile("single_user_desktop")),
    );
    expect(slugs(unknown)).not.toContain("team");
  });

  it("falls back to the solo set for an undefined profile", () => {
    const undef = destinationsForProfile(
      undefined as unknown as DeploymentProfile,
    );
    expect(slugs(undef)).toEqual([
      "run",
      "chats",
      "projects",
      "activity",
      "connectors",
      "tools",
    ]);
  });
});

describe("defaultDestinationForProfile (FR-2.6)", () => {
  it("returns run for both solo and team", () => {
    expect(defaultDestinationForProfile("single_user_desktop")).toBe("run");
    expect(defaultDestinationForProfile("team")).toBe("run");
  });
});

describe("legacy SHELL_DESTINATIONS is unchanged (FR-2.7)", () => {
  it("is still the 12 legacy destinations in original order with original labels", () => {
    expect(SHELL_DESTINATIONS).toEqual([
      { slug: "home", label: "Home" },
      { slug: "chats", label: "Chats" },
      { slug: "agents", label: "Agents" },
      { slug: "library", label: "Library" },
      { slug: "inbox", label: "Inbox" },
      { slug: "tools", label: "Tools" },
      { slug: "projects", label: "Projects" },
      { slug: "todos", label: "Todos" },
      { slug: "connectors", label: "Connectors" },
      { slug: "team", label: "Team" },
      { slug: "memory", label: "Memory" },
      { slug: "routines", label: "Routines" },
    ]);
  });

  it("keeps the legacy labels for the slugs the solo view relabels (FR-2.8)", () => {
    // In the legacy list `connectors` stays "Connectors" and `tools` stays
    // "Tools" — the relabel is scoped to the profile views only.
    expect(SHELL_DESTINATIONS.find((d) => d.slug === "connectors")?.label).toBe(
      "Connectors",
    );
    expect(SHELL_DESTINATIONS.find((d) => d.slug === "tools")?.label).toBe(
      "Tools",
    );
  });

  it("keeps DEFAULT_SHELL_DESTINATION as home (web landing unchanged)", () => {
    expect(DEFAULT_SHELL_DESTINATION).toBe("home");
  });
});
