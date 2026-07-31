// FR-5.17 — the approval-policy block. Three axes with distinct mode sets
// (read-only 2 / write 4 / on-chain-spend-destructive 2), radiogroup a11y, the
// per-connector scope note, and edits reported as the whole next value.

import { render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import {
  APPROVAL_POLICY_CONNECTOR_NOTE,
  ApprovalPolicy,
  type ApprovalPolicyValue,
} from "./ApprovalPolicy";

const VALUE: ApprovalPolicyValue = {
  readOnly: "auto",
  write: "require",
  danger: "require",
};

function group(name: RegExp) {
  return screen.getByRole("radiogroup", { name });
}

describe("<ApprovalPolicy>", () => {
  it("renders the three axes with their spec mode sets", () => {
    render(<ApprovalPolicy value={VALUE} onChange={vi.fn()} />);

    const readOnly = group(/read-only actions approval/i);
    expect(within(readOnly).getAllByRole("radio")).toHaveLength(2);
    expect(
      within(readOnly).getByRole("radio", { name: "Auto-approve" }),
    ).toBeInTheDocument();
    expect(
      within(readOnly).getByRole("radio", { name: "Ask first" }),
    ).toBeInTheDocument();

    const write = group(/write actions approval/i);
    expect(within(write).getAllByRole("radio")).toHaveLength(4);
    for (const name of [
      "Require approval",
      "Ask first",
      "Auto-approve",
      "Block",
    ]) {
      expect(within(write).getByRole("radio", { name })).toBeInTheDocument();
    }

    const danger = group(/on-chain, spend and destructive/i);
    expect(within(danger).getAllByRole("radio")).toHaveLength(2);
    expect(
      within(danger).getByRole("radio", { name: "Require approval" }),
    ).toBeInTheDocument();
    expect(
      within(danger).getByRole("radio", { name: "Block" }),
    ).toBeInTheDocument();
  });

  it("marks the current mode per axis via aria-checked", () => {
    render(
      <ApprovalPolicy
        value={{ readOnly: "ask", write: "block", danger: "block" }}
        onChange={vi.fn()}
      />,
    );
    expect(
      within(group(/read-only/i)).getByRole("radio", { name: "Ask first" }),
    ).toHaveAttribute("aria-checked", "true");
    expect(
      within(group(/write actions/i)).getByRole("radio", { name: "Block" }),
    ).toHaveAttribute("aria-checked", "true");
    expect(
      within(group(/on-chain/i)).getByRole("radio", { name: "Block" }),
    ).toHaveAttribute("aria-checked", "true");
  });

  it("reports the whole next value when a pill is chosen", () => {
    const onChange = vi.fn();
    render(<ApprovalPolicy value={VALUE} onChange={onChange} />);

    within(group(/write actions/i))
      .getByRole("radio", { name: "Block" })
      .click();

    // Only the edited axis flips; the rest of the value rides along untouched.
    expect(onChange).toHaveBeenCalledWith({
      readOnly: "auto",
      write: "block",
      danger: "require",
    });
  });

  it("shows the per-connector scope note", () => {
    render(<ApprovalPolicy value={VALUE} onChange={vi.fn()} />);
    expect(
      screen.getByTestId("approval-policy-connector-note"),
    ).toHaveTextContent(APPROVAL_POLICY_CONNECTOR_NOTE);
  });

  it("disables every pill when disabled", () => {
    render(<ApprovalPolicy value={VALUE} onChange={vi.fn()} disabled />);
    for (const radio of screen.getAllByRole("radio")) {
      expect(radio).toBeDisabled();
    }
  });
});

// PRD-FS-10 §4.3 tier 1 — the filesystem-bypass MASTER switch. It lives on
// this block rather than in a second approval system, and its default is the
// product decision, not an implementation detail.
describe("<ApprovalPolicy> — filesystem bypass master switch", () => {
  const toggle = (): HTMLElement =>
    screen.getByTestId("filesystem-bypass-toggle");

  it("is OFF for a value that predates the field", () => {
    // VALUE has no `filesystemBypassEnabled`. An existing persisted row must
    // read as off, never as "unset means allow".
    render(<ApprovalPolicy value={VALUE} onChange={vi.fn()} />);
    expect(toggle()).not.toBeChecked();
  });

  it("reflects an explicit false and an explicit true", () => {
    const { rerender } = render(
      <ApprovalPolicy
        value={{ ...VALUE, filesystemBypassEnabled: false }}
        onChange={vi.fn()}
      />,
    );
    expect(toggle()).not.toBeChecked();

    rerender(
      <ApprovalPolicy
        value={{ ...VALUE, filesystemBypassEnabled: true }}
        onChange={vi.fn()}
      />,
    );
    expect(toggle()).toBeChecked();
  });

  it("reports the whole next value, leaving the three axes untouched", () => {
    const onChange = vi.fn();
    render(<ApprovalPolicy value={VALUE} onChange={onChange} />);

    toggle().click();

    expect(onChange).toHaveBeenCalledWith({
      readOnly: "auto",
      write: "require",
      danger: "require",
      filesystemBypassEnabled: true,
    });
  });

  it("is disabled with the rest of the block", () => {
    render(<ApprovalPolicy value={VALUE} onChange={vi.fn()} disabled />);
    expect(toggle()).toBeDisabled();
  });
});
