// ProjectFilingChip — the "filed under" control below the composer (Option A).
//
// The three behaviours worth pinning, because each encodes a decision that a
// well-meaning refactor would undo:
//
//   * the unfiled state renders "No project" with NO tile — a placeholder tile
//     reads as a project whose name failed to load,
//   * "New project…" is ABSENT without `onCreateProject`, not disabled,
//   * the menu renders inline when the host supplies no `renderMenu`, so web
//     and tests get a working control with zero host wiring,
//   * ZERO projects is a different state from "unfiled": it draws a direct
//     "New project" action, or nothing at all when there is no way to create
//     one — never a picker over an empty set.

import type { ProjectColorHue, ProjectId } from "@0x-copilot/api-types";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import {
  ProjectFilingChip,
  type ProjectFilingOption,
} from "./ProjectFilingChip";

const ACME = "proj_acme" as ProjectId;
const KLEOS = "proj_kleos" as ProjectId;

const options: ReadonlyArray<ProjectFilingOption> = [
  { id: ACME, name: "Acme renewal", colorHue: 210 as ProjectColorHue },
  { id: KLEOS, name: "Kleos research", colorHue: 150 as ProjectColorHue },
];

function open(): void {
  fireEvent.click(screen.getByTestId("composer-project-filing-trigger"));
}

describe("ProjectFilingChip", () => {
  it("names the project the chat is filed under", () => {
    render(
      <ProjectFilingChip value={ACME} options={options} onChange={vi.fn()} />,
    );

    const trigger = screen.getByTestId("composer-project-filing-trigger");
    expect(trigger).toHaveTextContent("Acme renewal");
    expect(trigger).toHaveAttribute("aria-label", "Filed under: Acme renewal");
    expect(trigger).toHaveAttribute("aria-expanded", "false");
  });

  it("reads 'No project' when the chat is filed nowhere", () => {
    render(
      <ProjectFilingChip value={null} options={options} onChange={vi.fn()} />,
    );

    const trigger = screen.getByTestId("composer-project-filing-trigger");
    expect(trigger).toHaveTextContent("No project");
    expect(trigger).toHaveAttribute("aria-label", "Filed under: no project");
  });

  it("keeps the menu closed until the pill is clicked", () => {
    render(
      <ProjectFilingChip value={ACME} options={options} onChange={vi.fn()} />,
    );
    expect(
      screen.queryByTestId("composer-project-filing-menu"),
    ).not.toBeInTheDocument();

    open();

    expect(
      screen.getByTestId("composer-project-filing-menu"),
    ).toBeInTheDocument();
    expect(
      screen.getByTestId("composer-project-filing-trigger"),
    ).toHaveAttribute("aria-expanded", "true");
  });

  it("lists every option and checks the current one", () => {
    render(
      <ProjectFilingChip value={KLEOS} options={options} onChange={vi.fn()} />,
    );
    open();

    const rows = screen.getAllByTestId("composer-project-filing-option");
    expect(rows.map((row) => row.getAttribute("data-project-id"))).toEqual([
      ACME,
      KLEOS,
    ]);
    expect(rows[1]).toHaveAttribute("aria-checked", "true");
    expect(rows[0]).toHaveAttribute("aria-checked", "false");
    // The unfiled row is a real radio in the same group, not a reset button.
    expect(screen.getByTestId("composer-project-filing-none")).toHaveAttribute(
      "aria-checked",
      "false",
    );
  });

  it("files the chat into the picked project and closes", () => {
    const onChange = vi.fn();
    render(
      <ProjectFilingChip value={null} options={options} onChange={onChange} />,
    );
    open();

    fireEvent.click(
      screen
        .getAllByTestId("composer-project-filing-option")
        .find((row) => row.getAttribute("data-project-id") === KLEOS)!,
    );

    expect(onChange).toHaveBeenCalledWith(KLEOS);
    expect(
      screen.queryByTestId("composer-project-filing-menu"),
    ).not.toBeInTheDocument();
  });

  it("unfiles through 'No project'", () => {
    const onChange = vi.fn();
    render(
      <ProjectFilingChip value={ACME} options={options} onChange={onChange} />,
    );
    open();

    fireEvent.click(screen.getByTestId("composer-project-filing-none"));

    expect(onChange).toHaveBeenCalledWith(null);
  });

  it("omits 'New project…' when the host cannot create one", () => {
    render(
      <ProjectFilingChip value={ACME} options={options} onChange={vi.fn()} />,
    );
    open();

    expect(
      screen.queryByTestId("composer-project-filing-new"),
    ).not.toBeInTheDocument();
  });

  it("offers 'New project…' when the host can, and closes before delegating", () => {
    const onCreateProject = vi.fn();
    render(
      <ProjectFilingChip
        value={ACME}
        options={options}
        onChange={vi.fn()}
        onCreateProject={onCreateProject}
      />,
    );
    open();

    fireEvent.click(screen.getByTestId("composer-project-filing-new"));

    expect(onCreateProject).toHaveBeenCalledTimes(1);
    expect(
      screen.queryByTestId("composer-project-filing-menu"),
    ).not.toBeInTheDocument();
  });

  it("closes on Escape and returns focus to the pill", () => {
    render(
      <ProjectFilingChip value={ACME} options={options} onChange={vi.fn()} />,
    );
    open();

    fireEvent.keyDown(screen.getByTestId("composer-project-filing-menu"), {
      key: "Escape",
    });

    expect(
      screen.queryByTestId("composer-project-filing-menu"),
    ).not.toBeInTheDocument();
    // Without the refocus the next Tab restarts at the top of the document.
    expect(screen.getByTestId("composer-project-filing-trigger")).toHaveFocus();
  });

  it("offers the way to MAKE one when there are no projects", () => {
    // Zero projects used to draw `FILED UNDER` + a `No project` pill — a status
    // report about an absence, with the only live affordance ("New project…")
    // one click deep at the bottom of an otherwise empty menu. The empty state
    // is a direct action now, and there is no menu to open.
    const onCreateProject = vi.fn();
    render(
      <ProjectFilingChip
        value={null}
        options={[]}
        onChange={vi.fn()}
        onCreateProject={onCreateProject}
      />,
    );

    expect(screen.queryByTestId("composer-project-filing-trigger")).toBeNull();
    expect(screen.queryByText(/filed under/i)).toBeNull();

    const create = screen.getByTestId("composer-project-filing-create");
    expect(create).toHaveTextContent("New project");
    fireEvent.click(create);
    expect(onCreateProject).toHaveBeenCalledTimes(1);
  });

  it("renders NOTHING with no projects and no way to make one", () => {
    // The original concern this replaces — never offer a menu over an empty set.
    // With nothing to pick and nothing to create there is no control to draw at
    // all, so the component holds that itself rather than trusting every host to.
    const { container } = render(
      <ProjectFilingChip value={null} options={[]} onChange={vi.fn()} />,
    );

    expect(container).toBeEmptyDOMElement();
    expect(screen.queryByTestId("composer-project-filing")).toBeNull();
  });

  it("keeps the create affordance disabled with the rest of the control", () => {
    const onCreateProject = vi.fn();
    render(
      <ProjectFilingChip
        value={null}
        options={[]}
        onChange={vi.fn()}
        onCreateProject={onCreateProject}
        disabled
      />,
    );

    const create = screen.getByTestId("composer-project-filing-create");
    expect(create).toBeDisabled();
    fireEvent.click(create);
    expect(onCreateProject).not.toHaveBeenCalled();
  });

  it("offers the create-only variant when the chat has no projects yet", () => {
    render(
      <ProjectFilingChip
        value={null}
        options={[]}
        onChange={vi.fn()}
        onCreateProject={vi.fn()}
      />,
    );

    // A pill whose menu's only real entry is "No project" reports an absence as
    // though it were a decision; the direct action is the honest empty state.
    expect(
      screen.getByTestId("composer-project-filing-create"),
    ).toBeInTheDocument();
    expect(
      screen.queryByTestId("composer-project-filing-trigger"),
    ).not.toBeInTheDocument();
  });

  it("withdraws the create-only variant once the chat is under way", () => {
    render(
      <ProjectFilingChip
        value={null}
        options={[]}
        onChange={vi.fn()}
        onCreateProject={vi.fn()}
        hasSentFirstMessage
      />,
    );

    // "Make a project" is SETUP. Under a transcript it is only an invitation to
    // stop working — the same reason the folder bar leaves after message one.
    expect(
      screen.queryByTestId("composer-project-filing"),
    ).not.toBeInTheDocument();
  });

  it("keeps the pill mid-conversation — filing is a fact, not a chore", () => {
    render(
      <ProjectFilingChip
        value={ACME}
        options={options}
        onChange={vi.fn()}
        onCreateProject={vi.fn()}
        hasSentFirstMessage
      />,
    );

    // Only the empty state is gated. Where the work lands stays visible, and
    // re-filing stays reachable, for the chat's whole life.
    const trigger = screen.getByTestId("composer-project-filing-trigger");
    expect(trigger).toHaveTextContent("Acme renewal");
  });

  it("disables the trigger without hiding what the chat is filed under", () => {
    render(
      <ProjectFilingChip
        value={ACME}
        options={options}
        onChange={vi.fn()}
        disabled
      />,
    );

    const trigger = screen.getByTestId("composer-project-filing-trigger");
    expect(trigger).toBeDisabled();
    expect(trigger).toHaveTextContent("Acme renewal");
  });

  it("hands the menu body to a host slot instead of rendering it inline", () => {
    const renderMenu = vi.fn(({ open: isOpen, children }) => (
      <div data-testid="host-portal" data-open={isOpen ? "true" : "false"}>
        {isOpen ? children : null}
      </div>
    ));

    render(
      <ProjectFilingChip
        value={ACME}
        options={options}
        onChange={vi.fn()}
        renderMenu={renderMenu}
      />,
    );

    // The slot is called UNCONDITIONALLY so a host may keep a portal mounted.
    expect(screen.getByTestId("host-portal")).toHaveAttribute(
      "data-open",
      "false",
    );

    open();

    const portal = screen.getByTestId("host-portal");
    expect(portal).toHaveAttribute("data-open", "true");
    expect(
      screen
        .getByTestId("composer-project-filing-menu")
        .closest("[data-testid='host-portal']"),
    ).toBe(portal);
  });
});
