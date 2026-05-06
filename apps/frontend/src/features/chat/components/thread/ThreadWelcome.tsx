import { useEffect, useMemo, useState, type ReactElement } from "react";
import { CATEGORY_LABEL, CHAT_PROMPT_SUGGESTIONS } from "../../prompts";
import { welcomeGreeting } from "../../utils/greeting";

/**
 * Empty-thread welcome surface. Renders a time-aware greeting headline and
 * four suggestion cards (`DRAFT`, `SUMMARIZE`, `FIND`, `COMPARE`) that send
 * a fixed prompt when clicked. Receives the suggestion-pick callback from
 * the host (`ChatScreen`) which routes it to the runtime — keeps this
 * component free of any runtime context dependency so it can be rendered
 * standalone (storybook, shared-thread preview).
 *
 * `firstName` is optional. When the auth identity carries no display name,
 * the greeting drops the comma + name — same shape, no jitter.
 */
export function ThreadWelcome({
  firstName = null,
  now = new Date(),
  onSelectSuggestion,
}: {
  firstName?: string | null;
  now?: Date;
  /**
   * Fired when the user clicks one of the four prompt cards. The host
   * appends the prompt to the runtime as a user message. When omitted
   * (e.g. read-only previews) the card renders as a no-op button.
   */
  onSelectSuggestion?: (prompt: string) => void;
} = {}): ReactElement {
  const greeting = useMinuteAwareGreeting(now, firstName);

  return (
    <section className="aui-welcome aui-welcome--atlas">
      <h1 className="aui-welcome__greeting" data-testid="welcome-greeting">
        {greeting}{" "}
        <em className="aui-welcome__question">What are we shipping today?</em>
      </h1>
      <ul className="aui-welcome__suggestions" aria-label="Suggested prompts">
        {CHAT_PROMPT_SUGGESTIONS.map((suggestion) => (
          <li key={suggestion.prompt}>
            <button
              type="button"
              className="aui-welcome-card"
              title="Send this suggestion"
              onClick={() => onSelectSuggestion?.(suggestion.prompt)}
            >
              <span
                className="aui-welcome-card__eyebrow"
                data-category={suggestion.category}
              >
                {CATEGORY_LABEL[suggestion.category]}
              </span>
              <strong className="aui-welcome-card__title">
                {suggestion.title}
              </strong>
              <span className="aui-welcome-card__sub">{suggestion.label}</span>
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}

/**
 * Re-evaluates the greeting once a minute so a user who left an empty thread
 * open across a bucket boundary (e.g. 22:59 → 23:00) sees the next bucket.
 * Tests inject `now` directly to avoid timer flake.
 */
function useMinuteAwareGreeting(
  initialNow: Date,
  firstName: string | null,
): string {
  const [tick, setTick] = useState(() => initialNow.getTime());

  useEffect(() => {
    const id = window.setInterval(() => setTick(Date.now()), 60_000);
    return () => window.clearInterval(id);
  }, []);

  return useMemo(
    () => welcomeGreeting(new Date(tick), firstName),
    [tick, firstName],
  );
}
