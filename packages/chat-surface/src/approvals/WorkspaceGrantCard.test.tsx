// The folder-grant ask, and the parser that raises it.
//
// The assertions here are mostly about what the card REFUSES to say. The defect
// this surface replaces was a filesystem question answered with a confident
// empty listing, so: the path is printed in full and never abbreviated, an
// unknown access mode withholds the Grant button instead of guessing "Read-only",
// and a failure shows its own message rather than reading as "nothing happened".

import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import {
  WorkspaceGrantCard,
  grantAccessLabel,
  parseWorkspaceGrantRequest,
  WORKSPACE_GRANT_PAYLOAD_KEY,
  type WorkspaceGrantRequest,
} from "./index";

const DOWNLOADS: WorkspaceGrantRequest = {
  path: "/Users/parthpahwa/Downloads",
  folderName: "Downloads",
  mode: "read_only",
  reason: "to list what you downloaded today",
};

describe("<WorkspaceGrantCard>", () => {
  it("names the exact folder and the access being granted", () => {
    render(<WorkspaceGrantCard request={DOWNLOADS} state="pending" />);

    // The whole path, verbatim — the subject of the decision.
    expect(screen.getByTestId("wg-path").textContent).toBe(
      "/Users/parthpahwa/Downloads",
    );
    // The short name carries the sentence.
    expect(
      screen.getByRole("group", {
        name: "Folder access: /Users/parthpahwa/Downloads",
      }),
    ).toBeTruthy();
    expect(screen.getByText(/Let the agent read Downloads\?/)).toBeTruthy();
    expect(
      screen.getByText("Read-only · this folder only · revoke anytime"),
    ).toBeTruthy();
    // The model's stated reason, on the pending ask only.
    expect(screen.getByText("to list what you downloaded today")).toBeTruthy();
  });

  it("fires Grant and Deny", () => {
    const onGrant = vi.fn();
    const onDeny = vi.fn();
    render(
      <WorkspaceGrantCard
        request={DOWNLOADS}
        state="pending"
        onGrant={onGrant}
        onDeny={onDeny}
      />,
    );

    fireEvent.click(screen.getByTestId("wg-grant"));
    expect(onGrant).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByTestId("wg-deny"));
    expect(onDeny).toHaveBeenCalledTimes(1);
  });

  it("withholds Grant when the ask named no access, and says why", () => {
    render(
      <WorkspaceGrantCard
        request={{ ...DOWNLOADS, mode: null }}
        state="pending"
      />,
    );

    expect(screen.getByTestId("wg-grant").hasAttribute("disabled")).toBe(true);
    expect(screen.getByTestId("wg-unknown-access")).toBeTruthy();
    // No guessed clause: the trust line drops the access rather than inventing.
    expect(screen.getByText("this folder only · revoke anytime")).toBeTruthy();
    // The folder is still named — the user learns what was asked for.
    expect(screen.getByTestId("wg-path").textContent).toBe(
      "/Users/parthpahwa/Downloads",
    );
  });

  it("renders inert (but readable) when no grant port is wired", () => {
    render(
      <WorkspaceGrantCard
        request={DOWNLOADS}
        state="pending"
        actionable={false}
      />,
    );

    expect(screen.getByTestId("wg-grant").hasAttribute("disabled")).toBe(true);
    expect(screen.getByTestId("wg-path").textContent).toBe(
      "/Users/parthpahwa/Downloads",
    );
  });

  it("shows the host's failure message, and keeps `failed` distinct from `denied`", () => {
    const { container, unmount } = render(
      <WorkspaceGrantCard
        request={DOWNLOADS}
        state="failed"
        failureMessage="macOS refused access to that folder."
      />,
    );

    expect(container.querySelector(".cc")?.getAttribute("data-state")).toBe(
      "failed",
    );
    expect(screen.getByTestId("wg-failure").textContent).toBe(
      "macOS refused access to that folder.",
    );
    expect(screen.getByTestId("wg-retry").textContent).toBe("Try again");
    unmount();

    render(<WorkspaceGrantCard request={DOWNLOADS} state="denied" />);
    expect(screen.queryByTestId("wg-failure")).toBeNull();
    expect(screen.getByTestId("wg-retry").textContent).toBe("Reconsider");
    expect(screen.getByText(/the run continues without it/)).toBeTruthy();
  });

  it("degrades a message-less failure to a line, never an empty card", () => {
    render(<WorkspaceGrantCard request={DOWNLOADS} state="failed" />);
    expect(screen.getByTestId("wg-failure").textContent).toContain(
      "still cannot read it",
    );
  });

  it("offers Cancel while the OS dialog is up, and no decision once granted", () => {
    const onCancel = vi.fn();
    const { unmount } = render(
      <WorkspaceGrantCard
        request={DOWNLOADS}
        state="granting"
        onCancel={onCancel}
      />,
    );
    fireEvent.click(screen.getByTestId("wg-cancel"));
    expect(onCancel).toHaveBeenCalledTimes(1);
    unmount();

    render(<WorkspaceGrantCard request={DOWNLOADS} state="granted" />);
    expect(screen.queryByTestId("wg-grant")).toBeNull();
    expect(screen.queryByTestId("wg-deny")).toBeNull();
    // Still names the folder — now it is what you would revoke.
    expect(screen.getByTestId("wg-path").textContent).toBe(
      "/Users/parthpahwa/Downloads",
    );
  });
});

describe("parseWorkspaceGrantRequest", () => {
  it("parses the ask off its dedicated payload block", () => {
    const parsed = parseWorkspaceGrantRequest({
      approval_id: "appr-1",
      [WORKSPACE_GRANT_PAYLOAD_KEY]: {
        path: "/Users/parthpahwa/Downloads",
        mode: "read_only",
        reason: "to list what you downloaded today",
      },
    });
    expect(parsed).toEqual(DOWNLOADS);
  });

  it("takes the last segment on macOS AND Windows paths", () => {
    const mac = parseWorkspaceGrantRequest({
      [WORKSPACE_GRANT_PAYLOAD_KEY]: { path: "/Users/ada/Downloads/" },
    });
    expect(mac?.folderName).toBe("Downloads");

    const win = parseWorkspaceGrantRequest({
      [WORKSPACE_GRANT_PAYLOAD_KEY]: { path: "C:\\Users\\ada\\Downloads" },
    });
    expect(win?.folderName).toBe("Downloads");
    // A drive root has no segment to take; the whole path is still honest.
    const root = parseWorkspaceGrantRequest({
      [WORKSPACE_GRANT_PAYLOAD_KEY]: { path: "C:\\" },
    });
    expect(root?.folderName).toBe("C:");
  });

  it("returns null when there is no ask, and only then", () => {
    expect(parseWorkspaceGrantRequest(undefined)).toBeNull();
    expect(parseWorkspaceGrantRequest({ approval_id: "appr-1" })).toBeNull();
    // A block naming no folder is not an ask anybody could answer.
    expect(
      parseWorkspaceGrantRequest({
        [WORKSPACE_GRANT_PAYLOAD_KEY]: { mode: "read_only" },
      }),
    ).toBeNull();
    // An unrecognised mode still yields a card (the folder is knowable); the
    // access is dropped rather than guessed, which is what disables Grant.
    const odd = parseWorkspaceGrantRequest({
      [WORKSPACE_GRANT_PAYLOAD_KEY]: { path: "/tmp/x", mode: "sudo" },
    });
    expect(odd?.mode).toBeNull();
    expect(odd?.reason).toBeNull();
  });

  it("keeps `read_write_no_delete` distinct from `read_write`", () => {
    expect(grantAccessLabel("read_write_no_delete")).toBe(
      "Read & write, no deleting",
    );
    expect(grantAccessLabel("read_write")).toBe("Read & write");
    expect(grantAccessLabel("read_only")).toBe("Read-only");
    expect(grantAccessLabel(null)).toBeNull();
  });
});
