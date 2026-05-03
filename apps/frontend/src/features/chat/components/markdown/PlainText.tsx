import type { TextMessagePartProps } from "@assistant-ui/react";
import type { ReactElement } from "react";

export function PlainText({ text }: TextMessagePartProps): ReactElement {
  return <div className="aui-plain-text">{text}</div>;
}
