import { Badge } from "@enterprise-search/design-system";
import type { ReactElement } from "react";
import { truncateText, type SearchSource } from "../../utils/jsonUtils";

export function SearchSourceList({
  sources,
}: {
  sources: SearchSource[];
}): ReactElement {
  const rows = sources.slice(0, 4);
  return (
    <div className="aui-mcp-result-preview">
      <p>{sources.length} sources found</p>
      <ul className="aui-mcp-result-preview__list">
        {rows.map((source, index) => (
          <li key={`${source.link ?? source.title}-${index}`}>
            <span className="aui-mcp-result-preview__primary">
              <span>{source.title}</span>
              {source.snippet ? (
                <small>{truncateText(source.snippet, 150)}</small>
              ) : null}
            </span>
            {source.trust ? <Badge tone="neutral">{source.trust}</Badge> : null}
            {source.link ? (
              <a href={source.link} target="_blank" rel="noreferrer">
                Open
              </a>
            ) : null}
          </li>
        ))}
      </ul>
    </div>
  );
}
