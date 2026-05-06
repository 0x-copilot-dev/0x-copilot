import type { TextMessagePartProps } from "../../runtime/types";
import type { ReactElement } from "react";

export function PlainText({ text }: TextMessagePartProps): ReactElement {
  return <div className="aui-plain-text">{text}</div>;
}
