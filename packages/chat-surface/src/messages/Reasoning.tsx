import type { ReactElement } from "react";
import { Streamdown } from "streamdown";

import type { ReasoningMessagePartProps } from "./types";

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
