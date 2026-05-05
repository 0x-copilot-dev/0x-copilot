// PR 3.3 — `<MentionLabel />` tests.
//
// Pins the user-facing contract:
//   - falls back to raw user_id when no AuthProvider (storybook / preview)
//   - resolves to "@<handle>" when the cache is primed
//   - prefers display_name when no handle is provided
//   - tolerates null userId (renders nothing)

import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { MentionLabel } from "./MentionLabel";
import {
  _resetWorkspaceMemberCache,
  primeWorkspaceMember,
} from "./useWorkspaceMember";

describe("<MentionLabel />", () => {
  afterEach(() => {
    _resetWorkspaceMemberCache();
  });

  it("renders raw user_id when no AuthProvider mounted", () => {
    render(<MentionLabel userId="usr_marcus" />);
    expect(screen.getByText("@usr_marcus")).toBeInTheDocument();
  });

  it("renders @handle when the cache is primed", () => {
    primeWorkspaceMember({
      user_id: "usr_marcus",
      display_name: "Marcus Tate",
      handle: "marcus",
    });
    render(<MentionLabel userId="usr_marcus" />);
    expect(screen.getByText("@marcus")).toBeInTheDocument();
    // resolved chip carries data-resolved for CSS hooks.
    const chip = screen.getByLabelText(/marcus tate.*member/i);
    expect(chip).toHaveAttribute("data-resolved", "true");
  });

  it("falls back to display_name when no handle is set", () => {
    primeWorkspaceMember({
      user_id: "usr_priya",
      display_name: "Priya Naidu",
    });
    render(<MentionLabel userId="usr_priya" />);
    expect(screen.getByText("@Priya Naidu")).toBeInTheDocument();
  });

  it("renders nothing for a null userId", () => {
    const { container } = render(<MentionLabel userId={null} />);
    expect(container).toBeEmptyDOMElement();
  });
});
