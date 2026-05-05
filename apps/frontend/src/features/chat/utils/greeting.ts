export type GreetingBucket = "morning" | "afternoon" | "evening" | "late";

const HEAD: Record<GreetingBucket, string> = {
  morning: "Good morning",
  afternoon: "Good afternoon",
  evening: "Good evening",
  late: "Working late",
};

export function greetingForHour(hour: number): GreetingBucket {
  if (hour >= 5 && hour < 12) return "morning";
  if (hour >= 12 && hour < 18) return "afternoon";
  if (hour >= 18 && hour < 23) return "evening";
  return "late";
}

export function welcomeGreeting(now: Date, firstName: string | null): string {
  const head = HEAD[greetingForHour(now.getHours())];
  const trimmed = firstName?.trim();
  return trimmed ? `${head}, ${trimmed}.` : `${head}.`;
}

export function firstNameFromDisplayName(
  displayName: string | null | undefined,
): string | null {
  if (!displayName) return null;
  const first = displayName.trim().split(/\s+/)[0];
  return first || null;
}
