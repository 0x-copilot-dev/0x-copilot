import type { ReasoningMessagePartProps } from "@assistant-ui/react";
import type { ReactElement } from "react";
import { Streamdown } from "streamdown";

export function Reasoning({
  text,
  status,
}: ReasoningMessagePartProps): ReactElement {
  return (
    <Streamdown
      className="reasoning-markdown"
      mode={status.type === "running" ? "streaming" : "static"}
    >
      {text}
    </Streamdown>
  );
}
