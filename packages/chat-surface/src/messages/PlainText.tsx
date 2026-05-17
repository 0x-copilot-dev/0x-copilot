import type { ReactElement } from "react";

import type { TextMessagePartProps } from "./types";

export function PlainText({ text }: TextMessagePartProps): ReactElement {
  return <div className="aui-plain-text">{text}</div>;
}
