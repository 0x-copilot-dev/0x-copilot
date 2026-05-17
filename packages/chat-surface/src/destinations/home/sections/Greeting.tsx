// <Greeting> — Home morning-briefing headline.
//
// Source: docs/atlas-new-design/destinations/home-prd.md §3.1.1 + §4.1 +
// cross-audit.md §9.5 Q5 (binding 2026-05-17).
//
// Three rules the test plan locks down:
//
// 1. `time_of_day` is SERVER-computed against tenant timezone. The
//    component does NOT recompute from `Date.now()` — that would make the
//    text drift between substrates and tenants. We accept whatever string
//    the wire carries and map it to a display word.
// 2. First-name fallback chain stops at the no-name greeting. The wire
//    contract makes `user_first_name` optional; an absent / empty value
//    renders `"Good morning."` (no `User`, no email-local-part — see
//    cross-audit §9.5 Q5 deviation from sub-PRD).
// 3. Sub-line counts are pluralized via a single helper. Zero values are
//    NOT hidden — the user wants to know "0 need you" as much as "3 need
//    you".

// TODO(merge): rewire to "@enterprise-search/api-types" once home.ts ships.
import type { HomeGreeting } from "../_home-stub";
import type { CSSProperties, ReactElement } from "react";

export interface GreetingProps {
  readonly greeting: HomeGreeting;
}

const timeOfDayLabel: Readonly<Record<HomeGreeting["time_of_day"], string>> = {
  morning: "morning",
  afternoon: "afternoon",
  evening: "evening",
  late: "evening",
};

function plural(n: number, one: string, many: string): string {
  return `${n} ${n === 1 ? one : many}`;
}

const rootStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 6,
};

const headlineStyle: CSSProperties = {
  fontSize: "var(--font-size-2xl, 22px)",
  fontWeight: 600,
  color: "var(--color-text, #ededee)",
  margin: 0,
  lineHeight: 1.2,
};

const sublineStyle: CSSProperties = {
  fontSize: "var(--font-size-sm, 13px)",
  color: "var(--color-text-muted, #b4b4b8)",
  display: "flex",
  flexWrap: "wrap",
  gap: 6,
};

const separatorStyle: CSSProperties = {
  color: "var(--color-text-subtle, #7e7e84)",
};

function formatHeadline(g: HomeGreeting): string {
  const tod = timeOfDayLabel[g.time_of_day];
  const name = g.user_first_name?.trim();
  // Fallback chain ends here: no name → no name interpolation.
  // (cross-audit §9.5 Q5: email local-part is NOT used.)
  if (name === undefined || name.length === 0) {
    return `Good ${tod}.`;
  }
  return `Good ${tod}, ${name}.`;
}

export function Greeting({ greeting }: GreetingProps): ReactElement {
  const headline = formatHeadline(greeting);
  return (
    <section
      data-testid="home-greeting"
      data-time-of-day={greeting.time_of_day}
      style={rootStyle}
      aria-label="Greeting"
    >
      <h1 style={headlineStyle} data-testid="home-greeting-headline">
        {headline}
      </h1>
      <div style={sublineStyle} data-testid="home-greeting-subline">
        <span data-testid="home-greeting-agents-count">
          {plural(
            greeting.agents_working_count,
            "agent working",
            "agents working",
          )}
        </span>
        <span style={separatorStyle} aria-hidden="true">
          ·
        </span>
        <span data-testid="home-greeting-needs-you-count">
          {plural(greeting.needs_you_count, "needs you", "need you")}
        </span>
        <span style={separatorStyle} aria-hidden="true">
          ·
        </span>
        <span data-testid="home-greeting-date">
          {greeting.tenant_local_date}
        </span>
      </div>
    </section>
  );
}
