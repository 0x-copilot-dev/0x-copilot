import { SuggestionPrimitive, ThreadPrimitive } from "@assistant-ui/react";
import {
  useEffect,
  useMemo,
  useState,
  type ComponentType,
  type ReactElement,
} from "react";
import {
  CATEGORY_LABEL,
  CHAT_PROMPT_SUGGESTIONS,
  type ChatPromptSuggestion,
} from "../../prompts";
import { welcomeGreeting } from "../../utils/greeting";

/**
 * Empty-thread welcome surface. The runtime is already seeded with
 * `Suggestions(CHAT_PROMPT_SUGGESTIONS)` from `ChatScreen`, so each card uses
 * `ThreadPrimitive.SuggestionByIndex` to bind a `SuggestionPrimitive.Trigger`
 * to the correct runtime suggestion index. The eyebrow + title + sub render
 * from our own static data so we can attach a category color without leaking
 * the field into the runtime contract (which only carries title/label/prompt).
 *
 * `firstName` is optional. When the auth identity carries no display name,
 * the greeting drops the comma + name — same shape, no jitter.
 */
export function ThreadWelcome({
  firstName = null,
  now = new Date(),
}: {
  firstName?: string | null;
  now?: Date;
} = {}): ReactElement {
  const greeting = useMinuteAwareGreeting(now, firstName);

  // One stable Suggestion render-component per index. Memoised so the
  // identity is stable across renders; assistant-ui treats the component
  // reference as a remount key.
  const cards = useMemo(
    () =>
      CHAT_PROMPT_SUGGESTIONS.map((suggestion, index) => ({
        index,
        prompt: suggestion.prompt,
        Suggestion: makeSuggestionCard(suggestion),
      })),
    [],
  );

  return (
    <section className="aui-welcome aui-welcome--atlas">
      <h1 className="aui-welcome__greeting" data-testid="welcome-greeting">
        {greeting}
      </h1>
      <ul className="aui-welcome__suggestions" aria-label="Suggested prompts">
        {cards.map(({ index, prompt, Suggestion }) => (
          <li key={prompt}>
            <ThreadPrimitive.SuggestionByIndex
              index={index}
              components={{ Suggestion }}
            />
          </li>
        ))}
      </ul>
    </section>
  );
}

function makeSuggestionCard(suggestion: ChatPromptSuggestion): ComponentType {
  return function SuggestionCard() {
    return (
      <SuggestionPrimitive.Trigger
        className="aui-welcome-card"
        title="Send this suggestion"
        send
      >
        <span
          className="aui-welcome-card__eyebrow"
          data-category={suggestion.category}
        >
          {CATEGORY_LABEL[suggestion.category]}
        </span>
        <strong className="aui-welcome-card__title">{suggestion.title}</strong>
        <span className="aui-welcome-card__sub">{suggestion.label}</span>
      </SuggestionPrimitive.Trigger>
    );
  };
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
