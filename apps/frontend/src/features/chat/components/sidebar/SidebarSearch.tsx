import { forwardRef, type ChangeEvent, type KeyboardEvent } from "react";

/**
 * Controlled chat-search input (PR 2.2). Forwards a ref so the global
 * keymap (`$mod+K`) can imperatively focus it.
 *
 * Esc clears the query while the input is focused — convenient for
 * "I changed my mind" without reaching for the mouse. Outside Esc is
 * not handled here; the `$mod+\\` chord (toggle sidebar) is what closes
 * the surface entirely.
 */
export const SidebarSearch = forwardRef<HTMLInputElement, SidebarSearchProps>(
  function SidebarSearch({ value, onChange, listId }, ref) {
    function handleChange(event: ChangeEvent<HTMLInputElement>): void {
      onChange(event.target.value);
    }
    function handleKeyDown(event: KeyboardEvent<HTMLInputElement>): void {
      if (event.key === "Escape" && value.length > 0) {
        event.preventDefault();
        onChange("");
      }
    }
    return (
      <label className="aui-sidebar-search">
        <svg
          className="aui-sidebar-search__icon"
          aria-hidden="true"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.7"
          strokeLinecap="round"
          strokeLinejoin="round"
        >
          <circle cx="11" cy="11" r="7" />
          <path d="m20 20-3.5-3.5" />
        </svg>
        <span className="sr-only">Search threads</span>
        <input
          ref={ref}
          type="search"
          autoComplete="off"
          spellCheck={false}
          className="aui-sidebar-search__input"
          placeholder="Search chats…"
          aria-controls={listId}
          aria-label="Search chats"
          value={value}
          onChange={handleChange}
          onKeyDown={handleKeyDown}
        />
      </label>
    );
  },
);

interface SidebarSearchProps {
  value: string;
  onChange: (next: string) => void;
  listId: string;
}
