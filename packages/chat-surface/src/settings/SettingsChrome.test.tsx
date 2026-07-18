import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import {
  Frow,
  Krow,
  SecHead,
  SetCard,
  SetNote,
  SettingsNavItem,
} from "./SettingsChrome";

describe("<SetCard>", () => {
  it("renders a titled card with meta, actions, and body", () => {
    render(
      <SetCard
        title="Provider keys"
        meta="Bring your own key"
        actions={<button>Add</button>}
      >
        <p>body content</p>
      </SetCard>,
    );
    expect(
      screen.getByRole("heading", { name: "Provider keys" }),
    ).toBeInTheDocument();
    expect(screen.getByText("Bring your own key")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Add" })).toBeInTheDocument();
    expect(screen.getByText("body content")).toBeInTheDocument();
  });

  it("omits the head entirely when no title/meta/actions are given", () => {
    render(
      <SetCard>
        <p>just a body</p>
      </SetCard>,
    );
    expect(screen.queryByRole("heading")).not.toBeInTheDocument();
    expect(screen.getByText("just a body")).toBeInTheDocument();
  });
});

describe("<SecHead>", () => {
  it("renders its label text", () => {
    render(<SecHead>Account</SecHead>);
    expect(screen.getByText("Account")).toBeInTheDocument();
  });
});

describe("<SetNote>", () => {
  it("renders the note text and default info tone", () => {
    render(<SetNote>Keys stored in your macOS Keychain.</SetNote>);
    const note = screen.getByText("Keys stored in your macOS Keychain.")
      .parentElement as HTMLElement;
    expect(note).toHaveAttribute("data-tone", "info");
  });

  it("supports warning and danger tones and an icon", () => {
    const { rerender } = render(
      <SetNote tone="warning" icon={<span data-testid="note-icon">!</span>}>
        heads up
      </SetNote>,
    );
    expect(screen.getByTestId("note-icon")).toBeInTheDocument();
    expect(screen.getByText("heads up").parentElement).toHaveAttribute(
      "data-tone",
      "warning",
    );
    rerender(<SetNote tone="danger">danger</SetNote>);
    expect(screen.getByText("danger").parentElement).toHaveAttribute(
      "data-tone",
      "danger",
    );
  });
});

describe("<Frow>", () => {
  it("renders a label, hint and control", () => {
    render(
      <Frow label="Web access" hint="Let the agent browse">
        <input aria-label="Web access toggle" />
      </Frow>,
    );
    expect(screen.getByText("Web access")).toBeInTheDocument();
    expect(screen.getByText("Let the agent browse")).toBeInTheDocument();
    expect(screen.getByLabelText("Web access toggle")).toBeInTheDocument();
  });

  it("associates the label with the control via htmlFor", () => {
    render(
      <Frow label="Display name" htmlFor="display-name">
        <input id="display-name" data-testid="frow-input" />
      </Frow>,
    );
    const label = screen.getByText("Display name").closest("label");
    expect(label).not.toBeNull();
    expect(label).toHaveAttribute("for", "display-name");
  });
});

describe("<Krow>", () => {
  it("renders logo, name, sub and actions", () => {
    render(
      <Krow
        logo={<span data-testid="krow-logo">A</span>}
        name="Anthropic"
        sub="claude-opus · sk-…abcd"
        actions={<button>Remove</button>}
      />,
    );
    expect(screen.getByTestId("krow-logo")).toBeInTheDocument();
    expect(screen.getByText("Anthropic")).toBeInTheDocument();
    expect(screen.getByText("claude-opus · sk-…abcd")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Remove" })).toBeInTheDocument();
  });
});

describe("<SettingsNavItem>", () => {
  it("renders label, optional mono tag, and fires onClick", () => {
    const onClick = vi.fn();
    render(
      <SettingsNavItem label="Provider keys" tag="BYOK" onClick={onClick} />,
    );
    expect(screen.getByText("Provider keys")).toBeInTheDocument();
    expect(screen.getByText("BYOK")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Provider keys/ }));
    expect(onClick).toHaveBeenCalledTimes(1);
  });

  it("marks the active item with aria-current and a data-active flag", () => {
    render(<SettingsNavItem label="Appearance" active />);
    const button = screen.getByRole("button", { name: /Appearance/ });
    expect(button).toHaveAttribute("aria-current", "page");
    expect(button).toHaveAttribute("data-active", "true");
  });

  it("does not set aria-current when inactive", () => {
    render(<SettingsNavItem label="Shortcuts" />);
    const button = screen.getByRole("button", { name: /Shortcuts/ });
    expect(button).not.toHaveAttribute("aria-current");
    expect(button).not.toHaveAttribute("data-active");
  });
});
