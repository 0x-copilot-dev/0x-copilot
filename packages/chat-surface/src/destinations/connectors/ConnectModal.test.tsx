// <ConnectModal /> — the Tools "Connect a tool" flow (DESIGN-SPEC §5, FR-4.23):
// catalog pick → OAuth spinner → permission (Read only / Read & act) → Connect.
// The host drives OAuth via the `pending` / `error` props; the test flips them
// on rerender to walk the flow forward.

import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type {
  ConnectorCatalogEntry,
  ConnectorSlug,
} from "@0x-copilot/api-types";

import { ConnectModal, type ConnectModalProps } from "./ConnectModal";

const CATALOG: readonly ConnectorCatalogEntry[] = [
  {
    slug: "notion" as ConnectorSlug,
    display_name: "Notion",
    description: "Docs & wikis",
  },
  {
    slug: "linear" as ConnectorSlug,
    display_name: "Linear",
    description: "Issues & projects",
  },
];

function renderModal(overrides: Partial<ConnectModalProps> = {}) {
  const onClose = vi.fn();
  const onConnect = vi.fn();
  const onSelectEntry = vi.fn();
  const utils = render(
    <ConnectModal
      open
      onClose={onClose}
      catalog={CATALOG}
      onConnect={onConnect}
      onSelectEntry={onSelectEntry}
      {...overrides}
    />,
  );
  const rerender = (next: Partial<ConnectModalProps> = {}) =>
    utils.rerender(
      <ConnectModal
        open
        onClose={onClose}
        catalog={CATALOG}
        onConnect={onConnect}
        onSelectEntry={onSelectEntry}
        {...next}
      />,
    );
  return { onClose, onConnect, onSelectEntry, rerender };
}

function pickNotion(): void {
  act(() => {
    fireEvent.click(screen.getAllByTestId("connect-catalog-option")[0]);
  });
}

function stepLabel(): string | null {
  return screen.getByTestId("step-dots").getAttribute("aria-label");
}

describe("<ConnectModal>", () => {
  it("does not render when closed", () => {
    renderModal({ open: false });
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("lists the generic-SaaS catalog on step 1 of 3", () => {
    renderModal();
    const list = screen.getByTestId("connect-catalog-list");
    expect(within(list).getByText("Notion")).toBeInTheDocument();
    expect(within(list).getByText("Linear")).toBeInTheDocument();
    expect(stepLabel()).toBe("Step 1 of 3");
  });

  it("picking an entry fires onSelectEntry and shows the OAuth spinner (step 2)", () => {
    const { onSelectEntry } = renderModal({ pending: true });
    pickNotion();
    expect(onSelectEntry).toHaveBeenCalledWith("notion");
    expect(screen.getByTestId("connect-oauth")).toHaveAttribute(
      "role",
      "status",
    );
    expect(screen.getByText(/Authorizing with Notion/)).toBeInTheDocument();
    expect(stepLabel()).toBe("Step 2 of 3");
  });

  it("clearing pending advances to the permission choice (step 3)", () => {
    const { rerender } = renderModal({ pending: true });
    pickNotion();
    act(() => rerender({ pending: false }));
    expect(screen.getByTestId("connect-permission")).toHaveAttribute(
      "role",
      "radiogroup",
    );
    expect(stepLabel()).toBe("Step 3 of 3");
  });

  it("Connect fires onConnect with the picked entry and the default read permission", () => {
    const { onConnect, rerender } = renderModal({ pending: true });
    pickNotion();
    act(() => rerender({ pending: false }));
    act(() => fireEvent.click(screen.getByTestId("connect-confirm")));
    expect(onConnect).toHaveBeenCalledWith("notion", "read");
  });

  it("choosing Read & act connects with the read_act permission", () => {
    const { onConnect, rerender } = renderModal({ pending: true });
    pickNotion();
    act(() => rerender({ pending: false }));
    const options = screen.getAllByTestId("connect-permission-option");
    const readAct = options.find(
      (el) => el.getAttribute("data-value") === "read_act",
    )!;
    act(() => fireEvent.click(readAct));
    expect(readAct).toHaveAttribute("aria-checked", "true");
    act(() => fireEvent.click(screen.getByTestId("connect-confirm")));
    expect(onConnect).toHaveBeenCalledWith("notion", "read_act");
  });

  it("an OAuth error renders a role=alert and Retry re-fires onSelectEntry", () => {
    const { onSelectEntry, rerender } = renderModal({ pending: true });
    pickNotion();
    act(() => rerender({ pending: false, error: "window closed" }));
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent(/window closed/);
    // Still on the OAuth step — no permission choice leaked through.
    expect(screen.queryByTestId("connect-permission")).toBeNull();
    act(() => fireEvent.click(screen.getByTestId("connect-retry")));
    expect(onSelectEntry).toHaveBeenCalledTimes(2);
    expect(onSelectEntry).toHaveBeenLastCalledWith("notion");
  });

  it("Back from the OAuth error returns to the catalog (step 1)", () => {
    const { rerender } = renderModal({ pending: true });
    pickNotion();
    act(() => rerender({ pending: false, error: "denied" }));
    act(() => fireEvent.click(screen.getByTestId("connect-back")));
    expect(screen.getByTestId("connect-catalog-list")).toBeInTheDocument();
    expect(stepLabel()).toBe("Step 1 of 3");
  });

  it("disables Connect while a connect persist is pending", () => {
    const { rerender } = renderModal({ pending: true });
    pickNotion();
    act(() => rerender({ pending: false }));
    // Host flips pending back on while persisting the connection.
    act(() => rerender({ pending: true }));
    const confirm = screen.getByTestId("connect-confirm") as HTMLButtonElement;
    expect(confirm.disabled).toBe(true);
    expect(confirm).toHaveTextContent(/Connecting/);
  });

  it("Cancel on the catalog step closes the modal", () => {
    const { onClose } = renderModal();
    act(() => fireEvent.click(screen.getByTestId("connect-cancel")));
    expect(onClose).toHaveBeenCalled();
  });
});
