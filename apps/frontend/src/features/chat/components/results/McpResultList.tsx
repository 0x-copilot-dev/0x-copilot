import { Badge } from "@0x-copilot/design-system";
import type { ReactElement } from "react";
import { asRecord, stringValue } from "../../utils/jsonUtils";

export function McpResultList({
  results,
}: {
  results: unknown[];
}): ReactElement {
  const rows = results.map(asRecord).slice(0, 3);
  if (rows.length === 0) {
    return <p>No results returned.</p>;
  }
  return (
    <ul className="aui-mcp-result-preview__list">
      {rows.map((row, index) => {
        const name =
          stringValue(row.name) ?? stringValue(row.title) ?? "Result";
        const status = stringValue(row.status);
        const url = stringValue(row.url);
        return (
          <li key={`${name}-${index}`}>
            <span>{name}</span>
            {status ? <Badge tone="neutral">{status}</Badge> : null}
            {url ? (
              <a href={url} target="_blank" rel="noreferrer">
                Open
              </a>
            ) : null}
          </li>
        );
      })}
    </ul>
  );
}
