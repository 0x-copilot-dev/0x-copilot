// PRD-shell-execution §7.3 / §14.4 — the per-workspace "may the agent run
// commands here?" section.
//
// This is the SETTING side of the flag. The store's fail-closed decode and the
// runtime's read of it are asserted elsewhere (`grant-store.test.ts`,
// `test_workspace_shell_enablement.py`); what this file pins is that the
// surface cannot grant command authority by accident:
//
//   · absent capability ⇒ absent section (never a control that does nothing);
//   · a workspace with no flag reads OFF and stays off until a human acts;
//   · ENABLING takes two deliberate steps and the confirm NAMES the folder;
//   · DISABLING takes one and asks nothing;
//   · the decision is per-folder — one row's switch touches one grantId;
//   · the residual-risk sentence is present, verbatim, and not paraphrased.

import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { WORKSPACE_SHELL_ACCESS_NOTICE } from "../composer/useWorkspaceFolderGrants";
import type { WorkspaceGrant } from "../ports/WorkspaceGrantPort";

import {
  WORKSPACE_SHELL_ACCESS_CANCEL_LABEL,
  WORKSPACE_SHELL_ACCESS_CONFIRM_LABEL,
  WORKSPACE_SHELL_ACCESS_EMPTY,
  WorkspaceShellAccess,
  workspaceShellAccessConfirmPrompt,
} from "./WorkspaceShellAccess";

function grant(
  grantId: string,
  label: string,
  shellEnabled = false,
): WorkspaceGrant {
  return {
    grantId,
    mount: `m_${grantId}`,
    label,
    mode: "read_write",
    shellEnabled,
  };
}

const ATLAS = grant("g_atlas", "atlas");
const NOTES = grant("g_notes", "notes");

describe("<WorkspaceShellAccess>", () => {
  it("renders NOTHING when the host supplies no setter", () => {
    // The capability gate. Web has no shell and never will; a desktop build
    // with shell execution off has none either. Both must show no trace of the
    // feature rather than a row of switches that cannot work — a disabled
    // control advertises a capability, and this one would advertise the ability
    // to run commands on the machine.
    const { container } = render(
      <WorkspaceShellAccess grants={[ATLAS, NOTES]} onSetShellEnabled={null} />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("says what to do next when nothing is attached", () => {
    render(<WorkspaceShellAccess grants={[]} onSetShellEnabled={vi.fn()} />);
    expect(
      screen.getByTestId("workspace-shell-access-empty"),
    ).toHaveTextContent(WORKSPACE_SHELL_ACCESS_EMPTY);
  });

  it("states the residual-risk sentence verbatim, at the top of the section", () => {
    // §11.5 has no OS sandbox in v1. The sentence is the product's only honest
    // account of that, and it is asserted against the exported constant so a
    // paraphrase in either place fails rather than quietly softening the claim.
    render(
      <WorkspaceShellAccess grants={[ATLAS]} onSetShellEnabled={vi.fn()} />,
    );
    expect(
      screen.getByTestId("workspace-shell-access-notice"),
    ).toHaveTextContent(WORKSPACE_SHELL_ACCESS_NOTICE);
  });

  it("a workspace with the flag off reads off, and one with it on reads on", () => {
    render(
      <WorkspaceShellAccess
        grants={[ATLAS, grant("g_on", "enabled-one", true)]}
        onSetShellEnabled={vi.fn()}
      />,
    );
    expect(
      screen.getByTestId("workspace-shell-toggle-g_atlas"),
    ).not.toBeChecked();
    expect(screen.getByTestId("workspace-shell-toggle-g_on")).toBeChecked();
  });

  it("ENABLING does not apply from the switch — it arms a confirm that names the folder", async () => {
    // The one property that matters most here. A single click on a switch is
    // exactly the interaction a user performs without reading, and this is the
    // control that lets an agent run code as them.
    const onSetShellEnabled = vi.fn();
    const user = userEvent.setup();
    render(
      <WorkspaceShellAccess
        grants={[ATLAS]}
        onSetShellEnabled={onSetShellEnabled}
      />,
    );

    await user.click(screen.getByTestId("workspace-shell-toggle-g_atlas"));
    expect(onSetShellEnabled).not.toHaveBeenCalled();

    const confirm = screen.getByTestId("workspace-shell-confirm-g_atlas");
    expect(confirm).toHaveTextContent(
      workspaceShellAccessConfirmPrompt("atlas"),
    );
    // The risk sentence is restated INSIDE the confirm, not only above the list.
    expect(confirm).toHaveTextContent(WORKSPACE_SHELL_ACCESS_NOTICE);
    expect(
      within(confirm).getByRole("button", {
        name: WORKSPACE_SHELL_ACCESS_CONFIRM_LABEL,
      }),
    ).toBeInTheDocument();

    // ARMED IS NOT ON. The switch still reports the state the host holds, so a
    // user who abandons the prompt does not return to a control that looks
    // enabled over a permission nothing recorded.
    expect(
      screen.getByTestId("workspace-shell-toggle-g_atlas"),
    ).not.toBeChecked();
  });

  it("only the confirm applies the enable, and it applies to THAT grant", async () => {
    const onSetShellEnabled = vi.fn();
    const user = userEvent.setup();
    render(
      <WorkspaceShellAccess
        grants={[ATLAS, NOTES]}
        onSetShellEnabled={onSetShellEnabled}
      />,
    );

    await user.click(screen.getByTestId("workspace-shell-toggle-g_notes"));
    await user.click(screen.getByTestId("workspace-shell-allow-g_notes"));

    expect(onSetShellEnabled).toHaveBeenCalledTimes(1);
    expect(onSetShellEnabled).toHaveBeenCalledWith("g_notes", true);
    // Per-workspace: the other folder was never mentioned.
    expect(onSetShellEnabled).not.toHaveBeenCalledWith("g_atlas", true);
    expect(
      screen.queryByTestId("workspace-shell-confirm-g_notes"),
    ).not.toBeInTheDocument();
  });

  it("cancelling the confirm applies nothing at all", async () => {
    const onSetShellEnabled = vi.fn();
    const user = userEvent.setup();
    render(
      <WorkspaceShellAccess
        grants={[ATLAS]}
        onSetShellEnabled={onSetShellEnabled}
      />,
    );

    await user.click(screen.getByTestId("workspace-shell-toggle-g_atlas"));
    await user.click(
      screen.getByRole("button", {
        name: WORKSPACE_SHELL_ACCESS_CANCEL_LABEL,
      }),
    );

    expect(onSetShellEnabled).not.toHaveBeenCalled();
    expect(
      screen.queryByTestId("workspace-shell-confirm-g_atlas"),
    ).not.toBeInTheDocument();
  });

  it("arming a second folder closes the first, so two prompts are never open at once", async () => {
    const user = userEvent.setup();
    render(
      <WorkspaceShellAccess
        grants={[ATLAS, NOTES]}
        onSetShellEnabled={vi.fn()}
      />,
    );

    await user.click(screen.getByTestId("workspace-shell-toggle-g_atlas"));
    await user.click(screen.getByTestId("workspace-shell-toggle-g_notes"));

    expect(
      screen.queryByTestId("workspace-shell-confirm-g_atlas"),
    ).not.toBeInTheDocument();
    expect(
      screen.getByTestId("workspace-shell-confirm-g_notes"),
    ).toBeInTheDocument();
  });

  it("DISABLING applies immediately and asks nothing", async () => {
    // Asymmetric on purpose: a control that removes authority must never be
    // harder to reach than the one that grants it.
    const onSetShellEnabled = vi.fn();
    const user = userEvent.setup();
    render(
      <WorkspaceShellAccess
        grants={[grant("g_on", "enabled-one", true)]}
        onSetShellEnabled={onSetShellEnabled}
      />,
    );

    await user.click(screen.getByTestId("workspace-shell-toggle-g_on"));

    expect(onSetShellEnabled).toHaveBeenCalledExactlyOnceWith("g_on", false);
    expect(
      screen.queryByTestId("workspace-shell-confirm-g_on"),
    ).not.toBeInTheDocument();
  });

  it("shows a host failure instead of pretending nothing is attached", () => {
    render(
      <WorkspaceShellAccess
        grants={[ATLAS]}
        onSetShellEnabled={vi.fn()}
        error="Couldn't allow commands in that folder."
      />,
    );
    expect(
      screen.getByTestId("workspace-shell-access-error"),
    ).toHaveTextContent("Couldn't allow commands in that folder.");
    // The list is still there — an error is not an empty state.
    expect(
      screen.queryByTestId("workspace-shell-access-empty"),
    ).not.toBeInTheDocument();
  });

  it("is inert while a host call is in flight", () => {
    render(
      <WorkspaceShellAccess
        grants={[ATLAS]}
        onSetShellEnabled={vi.fn()}
        busy
      />,
    );
    expect(screen.getByTestId("workspace-shell-toggle-g_atlas")).toBeDisabled();
  });
});
