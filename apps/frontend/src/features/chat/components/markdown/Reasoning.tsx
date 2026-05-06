import type { ReasoningMessagePartProps } from "../../runtime/types";
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
