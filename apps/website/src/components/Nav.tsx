const DISCORD = "https://discord.gg/NhCv7zDkmX";

const LINKS = [
  { href: "./#what", label: "What it is", hideSm: true },
  { href: "./#run", label: "Run it", hideSm: true },
  { href: "./token.html", label: "Token" },
  {
    href: "https://github.com/0x-copilot-dev/0x-copilot",
    label: "GitHub ↗",
    ext: true,
  },
] as const;

/** Discord brand glyph — inherits `currentColor` from `.nav__links a`. */
export function DiscordIcon({ size = 18 }: { readonly size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="currentColor"
      aria-hidden="true"
      focusable="false"
    >
      <path d="M20.317 4.369a19.79 19.79 0 0 0-4.885-1.515.074.074 0 0 0-.079.037c-.211.375-.445.865-.608 1.249a18.27 18.27 0 0 0-5.487 0 12.64 12.64 0 0 0-.617-1.25.077.077 0 0 0-.079-.036A19.736 19.736 0 0 0 3.677 4.37a.07.07 0 0 0-.032.027C.533 9.046-.32 13.58.099 18.057a.082.082 0 0 0 .031.057 19.9 19.9 0 0 0 5.993 3.03.078.078 0 0 0 .084-.028c.462-.63.874-1.295 1.226-1.994a.076.076 0 0 0-.041-.106 13.107 13.107 0 0 1-1.872-.892.077.077 0 0 1-.008-.128c.126-.094.252-.192.372-.291a.074.074 0 0 1 .077-.01c3.928 1.793 8.18 1.793 12.062 0a.074.074 0 0 1 .078.009c.12.099.246.198.373.292a.077.077 0 0 1-.006.127 12.3 12.3 0 0 1-1.873.891.077.077 0 0 0-.041.108c.36.697.772 1.362 1.225 1.993a.076.076 0 0 0 .084.028 19.839 19.839 0 0 0 6.002-3.03.077.077 0 0 0 .032-.054c.5-5.177-.838-9.674-3.549-13.66a.061.061 0 0 0-.031-.03zM8.02 15.331c-1.183 0-2.157-1.085-2.157-2.419 0-1.333.956-2.419 2.157-2.419 1.21 0 2.176 1.096 2.157 2.42 0 1.333-.956 2.418-2.157 2.418zm7.975 0c-1.183 0-2.157-1.085-2.157-2.419 0-1.333.955-2.419 2.157-2.419 1.21 0 2.176 1.096 2.157 2.42 0 1.333-.946 2.418-2.157 2.418z" />
    </svg>
  );
}

export function Nav({ here }: { readonly here?: "token" }) {
  return (
    <nav className="nav" id="nav">
      <a className="mark" href="./">
        <b>0x</b>Copilot
      </a>
      <div className="nav__links">
        {LINKS.map((l) => (
          <a
            key={l.href}
            href={l.href}
            data-hide-sm={"hideSm" in l && l.hideSm ? "" : undefined}
            aria-current={
              here === "token" && l.label === "Token" ? "page" : undefined
            }
            {...("ext" in l && l.ext ? { rel: "noopener" } : {})}
          >
            {l.label}
          </a>
        ))}
        <a
          className="nav__icon"
          href={DISCORD}
          rel="noopener"
          target="_blank"
          aria-label="Join the 0xCopilot Discord"
        >
          <DiscordIcon />
        </a>
      </div>
    </nav>
  );
}
