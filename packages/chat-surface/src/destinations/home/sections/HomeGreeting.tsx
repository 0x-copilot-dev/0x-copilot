// <HomeGreeting> — morning-briefing headline.
//
// Sub-PRD §3.1.1 + api-types/home.ts HomeGreeting.
//
// Rules:
//   1. `time_segment` is SERVER-computed against tenant timezone — never
//      recompute from `Date.now()`. The component maps the wire value
//      to a display word ("morning" / "afternoon" / "evening").
//   2. `display_name` resolution chain stops at the no-name greeting
//      (cross-audit §9.5 Q5). `null` or empty -> "Good morning."; no
//      email-local-part fallback in the UI.
//   3. Sub-line is `tenant_local_date` only — Phase 9 retires the
//      agents-working / needs-you counts (those moved to TriageStrip).
//
// Renders through `<PageHeader>` (SP-1) so destination chrome is
// substrate-uniform.

import type { ReactElement } from "react";

import type { HomeGreeting as HomeGreetingT } from "@0x-copilot/api-types";

import { PageHeader } from "../../../shell/PageHeader";

export interface HomeGreetingProps {
  readonly greeting: HomeGreetingT;
}

const TIME_OF_DAY_LABEL: Readonly<
  Record<HomeGreetingT["time_segment"], string>
> = {
  morning: "morning",
  afternoon: "afternoon",
  evening: "evening",
};

function formatHeadline(g: HomeGreetingT): string {
  const tod = TIME_OF_DAY_LABEL[g.time_segment];
  const name = g.display_name?.trim();
  if (name === undefined || name === null || name.length === 0) {
    return `Good ${tod}.`;
  }
  return `Good ${tod}, ${name}.`;
}

export function HomeGreeting({ greeting }: HomeGreetingProps): ReactElement {
  return (
    <PageHeader
      title={formatHeadline(greeting)}
      subtitle={greeting.tenant_local_date}
    />
  );
}
