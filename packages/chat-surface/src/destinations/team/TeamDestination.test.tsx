// TeamDestination — shell + filter/search/sort behavior.

import type { Person, TenantId, UserId } from "@enterprise-search/api-types";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import {
  TeamDestination,
  applyRoleFilter,
  applySearch,
  applySort,
} from "./TeamDestination";

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

describe("TeamDestination", () => {
  it("renders header + filter tabs + search + sort", () => {
    render(<TeamDestination people={[makePerson()]} />);
    expect(screen.getByTestId("page-header")).toBeInTheDocument();
    expect(screen.getByTestId("page-header-title")).toHaveTextContent("Team");
    const tablist = screen.getByRole("tablist", { name: "Team filter (role)" });
    expect(tablist).toBeInTheDocument();
    for (const slug of ["all", "admins", "members", "guests"]) {
      expect(
        within(tablist).getByTestId(`filter-tab-${slug}`),
      ).toBeInTheDocument();
    }
    expect(screen.getByTestId("team-search")).toBeInTheDocument();
    expect(screen.getByTestId("team-sort")).toBeInTheDocument();
  });

  it("renders a loading sentinel when people is null", () => {
    render(<TeamDestination people={null} />);
    expect(screen.getByTestId("team-loading")).toBeInTheDocument();
  });

  it("renders an empty state with invite CTA when no people", () => {
    const onInvite = vi.fn();
    render(<TeamDestination people={[]} onInvite={onInvite} />);
    const empty = screen.getByTestId("empty-state");
    expect(empty).toBeInTheDocument();
    fireEvent.click(within(empty).getByTestId("empty-state-action"));
    expect(onInvite).toHaveBeenCalledTimes(1);
  });

  it("renders one PersonCard per person", () => {
    render(
      <TeamDestination
        people={[
          makePerson({ id: "u_1" as UserId, display_name: "A" }),
          makePerson({ id: "u_2" as UserId, display_name: "B", role: "admin" }),
        ]}
      />,
    );
    const cards = screen.getAllByTestId("person-card");
    expect(cards).toHaveLength(2);
  });

  it("filters by role tab selection", () => {
    const people = [
      makePerson({ id: "u_1" as UserId, role: "owner" }),
      makePerson({ id: "u_2" as UserId, role: "admin" }),
      makePerson({ id: "u_3" as UserId, role: "member" }),
      makePerson({ id: "u_4" as UserId, role: "guest" }),
    ];
    render(<TeamDestination people={people} />);
    fireEvent.click(screen.getByTestId("filter-tab-admins"));
    expect(screen.getAllByTestId("person-card")).toHaveLength(2); // owner + admin
    fireEvent.click(screen.getByTestId("filter-tab-guests"));
    expect(screen.getAllByTestId("person-card")).toHaveLength(1);
  });

  it("filters by search query", () => {
    const people = [
      makePerson({ id: "u_1" as UserId, display_name: "Sarah Acme" }),
      makePerson({ id: "u_2" as UserId, display_name: "Marcus Admin" }),
    ];
    render(<TeamDestination people={people} />);
    fireEvent.change(screen.getByTestId("team-search"), {
      target: { value: "marcus" },
    });
    expect(screen.getAllByTestId("person-card")).toHaveLength(1);
  });

  it("invokes onInvite when the primary CTA fires", () => {
    const onInvite = vi.fn();
    render(<TeamDestination people={[makePerson()]} onInvite={onInvite} />);
    fireEvent.click(screen.getByTestId("page-header-primary-action"));
    expect(onInvite).toHaveBeenCalledTimes(1);
  });

  it("invokes onOpenPerson when a card is clicked", () => {
    const onOpen = vi.fn();
    const p = makePerson();
    render(<TeamDestination people={[p]} onOpenPerson={onOpen} />);
    fireEvent.click(screen.getByTestId("person-card"));
    expect(onOpen).toHaveBeenCalledWith(p);
  });

  it("supports controlled filter/sort props with onChange callbacks", () => {
    const onFilterChange = vi.fn();
    const onSortChange = vi.fn();
    render(
      <TeamDestination
        people={[makePerson()]}
        filter="admins"
        onFilterChange={onFilterChange}
        sort="last_seen:desc"
        onSortChange={onSortChange}
      />,
    );
    fireEvent.click(screen.getByTestId("filter-tab-members"));
    expect(onFilterChange).toHaveBeenCalledWith("members");
    fireEvent.change(screen.getByTestId("team-sort"), {
      target: { value: "joined_at:desc" },
    });
    expect(onSortChange).toHaveBeenCalledWith("joined_at:desc");
  });
});

describe("TeamDestination — pure transforms", () => {
  it("applyRoleFilter('all') returns input", () => {
    const ps = [makePerson({ id: "u_1" as UserId, role: "owner" })];
    expect(applyRoleFilter(ps, "all")).toEqual(ps);
  });

  it("applyRoleFilter('admins') keeps owner + admin", () => {
    const ps = [
      makePerson({ id: "u_1" as UserId, role: "owner" }),
      makePerson({ id: "u_2" as UserId, role: "admin" }),
      makePerson({ id: "u_3" as UserId, role: "member" }),
    ];
    expect(applyRoleFilter(ps, "admins").map((p) => p.id)).toEqual([
      "u_1",
      "u_2",
    ]);
  });

  it("applySearch matches name + email case-insensitively", () => {
    const ps = [
      makePerson({ id: "u_1" as UserId, display_name: "Sarah Acme" }),
      makePerson({
        id: "u_2" as UserId,
        display_name: "Marcus",
        email: "MARCUS@acme.test",
      }),
    ];
    expect(applySearch(ps, "sarah").map((p) => p.id)).toEqual(["u_1"]);
    expect(applySearch(ps, "MARCUS").map((p) => p.id)).toEqual(["u_2"]);
    expect(applySearch(ps, "@acme")).toHaveLength(2);
  });

  it("applySort('display_name:asc') sorts by name", () => {
    const ps = [
      makePerson({ id: "u_2" as UserId, display_name: "Zed" }),
      makePerson({ id: "u_1" as UserId, display_name: "Anna" }),
    ];
    expect(applySort(ps, "display_name:asc").map((p) => p.id)).toEqual([
      "u_1",
      "u_2",
    ]);
  });

  it("applySort('last_seen:desc') sinks null last_seen to the bottom", () => {
    const ps = [
      makePerson({
        id: "u_a" as UserId,
        last_seen_at: "2026-05-01T00:00:00.000Z",
      }),
      makePerson({ id: "u_b" as UserId, last_seen_at: null }),
      makePerson({
        id: "u_c" as UserId,
        last_seen_at: "2026-05-18T00:00:00.000Z",
      }),
    ];
    expect(applySort(ps, "last_seen:desc").map((p) => p.id)).toEqual([
      "u_c",
      "u_a",
      "u_b",
    ]);
  });

  it("applySort('joined_at:desc') sorts newest joiners first", () => {
    const ps = [
      makePerson({
        id: "u_a" as UserId,
        joined_at: "2025-01-01T00:00:00.000Z",
      }),
      makePerson({
        id: "u_b" as UserId,
        joined_at: "2026-05-01T00:00:00.000Z",
      }),
    ];
    expect(applySort(ps, "joined_at:desc").map((p) => p.id)).toEqual([
      "u_b",
      "u_a",
    ]);
  });
});
