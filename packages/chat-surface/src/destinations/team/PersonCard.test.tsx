// PersonCard — card primitive (rendering + click).

import type { Person, TenantId, UserId } from "@0x-copilot/api-types";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { PersonCard } from "./PersonCard";

function makePerson(over: Partial<Person> = {}): Person {
  return {
    id: "u_1" as UserId,
    tenant_id: "tnt_1" as TenantId,
    display_name: "Sarah Acme",
    email: "sarah@acme.test",
    role: "owner",
    presence: "active",
    last_seen_at: "2026-05-18T10:00:00.000Z",
    joined_at: "2025-01-01T00:00:00.000Z",
    agents_count: 2,
    projects_count: 3,
    is_self: false,
    ...over,
  };
}

describe("PersonCard", () => {
  it("renders name, email, role chip, presence dot, and counts", () => {
    render(<PersonCard person={makePerson()} />);
    expect(screen.getByTestId("person-card-name")).toHaveTextContent(
      "Sarah Acme",
    );
    expect(screen.getByTestId("person-card-email")).toHaveTextContent(
      "sarah@acme.test",
    );
    expect(screen.getByTestId("person-card-role-owner")).toHaveTextContent(
      "Owner",
    );
    expect(
      screen.getByTestId("person-card-presence-active"),
    ).toBeInTheDocument();
    expect(screen.getByTestId("person-card-agents-count")).toHaveTextContent(
      "2 agents",
    );
    expect(screen.getByTestId("person-card-projects-count")).toHaveTextContent(
      "3 projects",
    );
  });

  it("renders initials tile when avatar_url is missing", () => {
    render(<PersonCard person={makePerson()} />);
    expect(screen.getByTestId("person-card-avatar-initials")).toHaveTextContent(
      "SA",
    );
  });

  it("renders an image avatar when avatar_url is present", () => {
    render(
      <PersonCard
        person={makePerson({ avatar_url: "https://example/a.png" })}
      />,
    );
    const img = screen.getByTestId(
      "person-card-avatar-img",
    ) as HTMLImageElement;
    expect(img.src).toBe("https://example/a.png");
  });

  it("shows (you) suffix when is_self", () => {
    render(<PersonCard person={makePerson({ is_self: true })} />);
    expect(screen.getByTestId("person-card-name")).toHaveTextContent("(you)");
  });

  it("singular labels when counts equal 1", () => {
    render(
      <PersonCard
        person={makePerson({ agents_count: 1, projects_count: 1 })}
      />,
    );
    expect(screen.getByTestId("person-card-agents-count")).toHaveTextContent(
      "1 agent",
    );
    expect(screen.getByTestId("person-card-projects-count")).toHaveTextContent(
      "1 project",
    );
  });

  it("fires onOpen with the person when clicked", () => {
    const onOpen = vi.fn();
    const p = makePerson();
    render(<PersonCard person={p} onOpen={onOpen} />);
    fireEvent.click(screen.getByTestId("person-card"));
    expect(onOpen).toHaveBeenCalledWith(p);
  });
});
