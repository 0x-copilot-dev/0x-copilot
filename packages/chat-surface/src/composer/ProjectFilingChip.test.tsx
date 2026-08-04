// ProjectFilingChip — the "filed under" control below the composer (Option A).
//
// The three behaviours worth pinning, because each encodes a decision that a
// well-meaning refactor would undo:
//
//   * the unfiled state renders "No project" with NO tile — a placeholder tile
//     reads as a project whose name failed to load,
//   * "New project…" is ABSENT without `onCreateProject`, not disabled,
//   * the menu renders inline when the host supplies no `renderMenu`, so web
//     and tests get a working control with zero host wiring.

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

  it("renders an empty option list without offering a broken menu", () => {
    render(<ProjectFilingChip value={null} options={[]} onChange={vi.fn()} />);
    open();

    expect(
      screen.queryAllByTestId("composer-project-filing-option"),
    ).toHaveLength(0);
    // "No project" still renders — it is the honest state of an unfiled chat.
    expect(
      screen.getByTestId("composer-project-filing-none"),
    ).toBeInTheDocument();
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
