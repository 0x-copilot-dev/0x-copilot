const LINKS = [
  { href: "/#what", label: "What it is", hideSm: true },
  { href: "/#run", label: "Run it", hideSm: true },
  { href: "/token.html", label: "Token" },
  {
    href: "https://github.com/0x-copilot-dev/0x-copilot",
    label: "GitHub ↗",
    ext: true,
  },
] as const;

export function Nav({ here }: { readonly here?: "token" }) {
  return (
    <nav className="nav" id="nav">
      <a className="mark" href="/">
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
      </div>
    </nav>
  );
}
