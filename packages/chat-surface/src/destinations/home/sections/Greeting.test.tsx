import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { HomeGreeting } from "../_home-stub";

import { Greeting } from "./Greeting";

function makeGreeting(overrides: Partial<HomeGreeting> = {}): HomeGreeting {
  return {
    time_of_day: "morning",
    user_first_name: "Parth",
    tenant_local_date: "2026-05-17",
    tenant_local_iso: "2026-05-17T08:00:00-07:00",
    agents_working_count: 3,
    needs_you_count: 2,
    ...overrides,
  };
}

describe("<Greeting>", () => {
  describe("headline + fallback chain (cross-audit §9.5 Q5)", () => {
    it("renders 'Good morning, {name}.' when user_first_name is present", () => {
      render(<Greeting greeting={makeGreeting({ time_of_day: "morning" })} />);
      expect(screen.getByTestId("home-greeting-headline")).toHaveTextContent(
        "Good morning, Parth.",
      );
    });

    it("renders 'Good afternoon, {name}.' for afternoon segment", () => {
      render(
        <Greeting greeting={makeGreeting({ time_of_day: "afternoon" })} />,
      );
      expect(screen.getByTestId("home-greeting-headline")).toHaveTextContent(
        "Good afternoon, Parth.",
      );
    });

    it("renders 'Good evening, {name}.' for evening segment", () => {
      render(<Greeting greeting={makeGreeting({ time_of_day: "evening" })} />);
      expect(screen.getByTestId("home-greeting-headline")).toHaveTextContent(
        "Good evening, Parth.",
      );
    });

    it("maps late→evening per home-prd time segments", () => {
      // The 4th segment 'late' (post-midnight) reads as 'evening' to the
      // user — keeps the copy library short and natural.
      render(<Greeting greeting={makeGreeting({ time_of_day: "late" })} />);
      expect(screen.getByTestId("home-greeting-headline")).toHaveTextContent(
        "Good evening, Parth.",
      );
    });

    it("falls back to 'Good morning.' (no name) when user_first_name is undefined", () => {
      // cross-audit §9.5 Q5 deviation from sub-PRD: when both IdP
      // `given_name` and first-token of `name` are missing the backend
      // sends NO name; we render the no-name greeting. Email local-part
      // is NEVER used (the deviation locks this in).
      render(
        <Greeting
          greeting={makeGreeting({
            time_of_day: "morning",
            user_first_name: undefined,
          })}
        />,
      );
      expect(screen.getByTestId("home-greeting-headline")).toHaveTextContent(
        "Good morning.",
      );
    });

    it("falls back to 'Good evening.' when user_first_name is the empty string", () => {
      // Defensive: an empty string from the wire is treated as no-name.
      // (Defensive because the wire contract says omit-the-field; some
      //  upstream proxies coerce missing → "".)
      render(
        <Greeting
          greeting={makeGreeting({
            time_of_day: "evening",
            user_first_name: "",
          })}
        />,
      );
      expect(screen.getByTestId("home-greeting-headline")).toHaveTextContent(
        "Good evening.",
      );
    });

    it("trims whitespace-only first names back to the no-name fallback", () => {
      render(
        <Greeting
          greeting={makeGreeting({
            time_of_day: "morning",
            user_first_name: "   ",
          })}
        />,
      );
      expect(screen.getByTestId("home-greeting-headline")).toHaveTextContent(
        "Good morning.",
      );
    });
  });

  describe("sub-line counts + date", () => {
    it("renders pluralized agents-working + needs-you counts", () => {
      render(
        <Greeting
          greeting={makeGreeting({
            agents_working_count: 3,
            needs_you_count: 2,
          })}
        />,
      );
      expect(
        screen.getByTestId("home-greeting-agents-count"),
      ).toHaveTextContent("3 agents working");
      expect(
        screen.getByTestId("home-greeting-needs-you-count"),
      ).toHaveTextContent("2 need you");
    });

    it("uses singular forms when counts are 1", () => {
      render(
        <Greeting
          greeting={makeGreeting({
            agents_working_count: 1,
            needs_you_count: 1,
          })}
        />,
      );
      expect(
        screen.getByTestId("home-greeting-agents-count"),
      ).toHaveTextContent("1 agent working");
      expect(
        screen.getByTestId("home-greeting-needs-you-count"),
      ).toHaveTextContent("1 needs you");
    });

    it("does not hide zero counts — '0 need you' still surfaces", () => {
      render(
        <Greeting
          greeting={makeGreeting({
            agents_working_count: 0,
            needs_you_count: 0,
          })}
        />,
      );
      expect(
        screen.getByTestId("home-greeting-agents-count"),
      ).toHaveTextContent("0 agents working");
      expect(
        screen.getByTestId("home-greeting-needs-you-count"),
      ).toHaveTextContent("0 need you");
    });

    it("renders the tenant-local date string verbatim", () => {
      // The date is server-formatted against tenant timezone — the
      // component does not re-format. Verifying we pass it through as-is.
      render(
        <Greeting
          greeting={makeGreeting({ tenant_local_date: "2026-05-17" })}
        />,
      );
      expect(screen.getByTestId("home-greeting-date")).toHaveTextContent(
        "2026-05-17",
      );
    });
  });

  describe("a11y + data attributes", () => {
    it("uses <h1> as the headline (LCP-friendly per home-prd §10)", () => {
      render(<Greeting greeting={makeGreeting()} />);
      const headline = screen.getByTestId("home-greeting-headline");
      expect(headline.tagName).toBe("H1");
    });

    it("tags the root with data-time-of-day for telemetry / styling", () => {
      render(<Greeting greeting={makeGreeting({ time_of_day: "evening" })} />);
      expect(screen.getByTestId("home-greeting")).toHaveAttribute(
        "data-time-of-day",
        "evening",
      );
    });

    it("exposes an aria-label on the root section", () => {
      render(<Greeting greeting={makeGreeting()} />);
      expect(screen.getByLabelText("Greeting")).toBeInTheDocument();
    });
  });
});
