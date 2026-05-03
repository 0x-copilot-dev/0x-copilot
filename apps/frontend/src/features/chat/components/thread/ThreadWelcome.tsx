import { SuggestionPrimitive, ThreadPrimitive } from "@assistant-ui/react";
import type { ReactElement } from "react";
import { LogoMark } from "./LogoMark";

export function ThreadWelcome(): ReactElement {
  return (
    <section className="aui-welcome">
      <LogoMark compact />
      <h2>Hello there!</h2>
      <p>How can I help you today?</p>
      <div className="aui-suggestions">
        <ThreadPrimitive.Suggestions>
          {() => (
            <SuggestionPrimitive.Trigger
              className="aui-suggestion"
              title="Send this suggestion"
              send
            >
              <strong>
                <SuggestionPrimitive.Title />
              </strong>
              <span>
                <SuggestionPrimitive.Description />
              </span>
            </SuggestionPrimitive.Trigger>
          )}
        </ThreadPrimitive.Suggestions>
      </div>
    </section>
  );
}
