"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useOverlay } from "./overlays";

const PAGES = [
  { href: "/", label: "Race week", color: "#ff8a4c", icon: "M2.5 11a5.5 5.5 0 1 1 11 0M8 11l2.8-3.4" },
  {
    href: "/paddock/",
    label: "Paddock",
    color: "#ff8a1f",
    icon: "M3 13c.6-2.6 2.6-4 5-4s4.4 1.4 5 4M8 7.2a2.4 2.4 0 1 0 0-4.8 2.4 2.4 0 0 0 0 4.8z",
  },
  {
    href: "/circuits/",
    label: "Circuits",
    color: "#2ee6c5",
    icon: "M3 12.5c0-3 2.5-2.5 2.5-5S4 3.5 6.5 3.5h3.5c2 0 3 1.4 3 3s-1.6 2.3-1.6 3.6S13 11.5 13 12.5H3z",
  },
  { href: "/stewards/", label: "Stewards", color: "#c03fd0", icon: "M3 13.5h7M4.5 6.5l4-4 3 3-4 4zM8 8l5 5" },
  { href: "/record/", label: "Track record", color: "#3a8bff", icon: "M2.5 13.5h11M4.5 11V8M8 11V4.5M11.5 11V6.5" },
];

function active(path: string, href: string): boolean {
  return href === "/" ? path === "/" : path.startsWith(href);
}

export function Sidebar({
  race,
  lightsOut,
  asOf,
}: {
  race: string;
  lightsOut: string;
  asOf: string;
}) {
  const path = usePathname();
  const { open } = useOverlay();
  return (
    <>
      <nav
        data-split
        aria-label="Pit Advisor"
        className="raised sticky top-4 hidden h-[calc(100dvh-2rem)] min-h-[620px] w-[232px] shrink-0 flex-col gap-5 rounded-[30px] px-4 pt-5 pb-4 lg:flex"
      >
        <Link href="/" className="flex items-center gap-2.5 px-1.5">
          <span className="raised-sm flex h-9 w-9 items-center justify-center rounded-xl">
            <svg width="20" height="20" viewBox="0 0 20 20" aria-hidden="true">
              <path d="M3.5 13a6.5 6.5 0 1 1 13 0" stroke="#ff8a4c" strokeWidth="1.8" fill="none" strokeLinecap="round" />
              <path d="M10 13l3.4-4" stroke="#ececef" strokeWidth="1.8" strokeLinecap="round" />
              <circle cx="10" cy="13" r="1.4" fill="#ececef" />
            </svg>
          </span>
          <span className="display text-[17px] font-semibold tracking-[0.08em]">Pit Advisor</span>
        </Link>

        <div className="inset-sm flex flex-col gap-1.5 rounded-[20px] p-3.5">
          <span className="text-[10.5px] tracking-[0.12em] text-mute uppercase">This weekend</span>
          <span className="display text-lg leading-tight">{race}</span>
          <span className="flex items-center gap-2 text-xs text-ink-3">
            <span className="h-[7px] w-[7px] rounded-full bg-coral shadow-[0_0_8px_#ff5f6d]" />
            {lightsOut}
          </span>
        </div>

        <ul className="flex flex-col gap-1.5">
          {PAGES.map((page) => {
            const on = active(path, page.href);
            return (
              <li key={page.href}>
                <Link
                  href={page.href}
                  aria-current={on ? "page" : undefined}
                  className={`press flex items-center gap-3 rounded-2xl px-3 py-2.5 text-sm font-medium ${
                    on ? "raised-sm text-ink" : "text-[#8e909a] hover:text-ink"
                  }`}
                >
                  <span
                    className={`flex h-[30px] w-[30px] items-center justify-center rounded-[10px] ${on ? "" : "inset-sm"}`}
                    style={on ? { background: page.color, boxShadow: `0 0 12px ${page.color}` } : undefined}
                  >
                    <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true">
                      <path
                        d={page.icon}
                        stroke={on ? "#111113" : page.color}
                        strokeWidth="1.6"
                        fill="none"
                        strokeLinecap="round"
                        strokeLinejoin="round"
                      />
                    </svg>
                  </span>
                  {page.label}
                </Link>
              </li>
            );
          })}
        </ul>

        <div className="mt-auto flex flex-col gap-2.5">
          <button
            type="button"
            onClick={() => open({ kind: "ask" })}
            className="raised-sm press flex items-center justify-between rounded-2xl py-3 pr-3 pl-4 text-[13.5px] font-medium"
          >
            <span className="flex items-center gap-2.5">
              <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true">
                <circle cx="7" cy="7" r="4.5" stroke="#2ee6c5" strokeWidth="1.6" fill="none" />
                <path d="M10.4 10.4l3 3" stroke="#2ee6c5" strokeWidth="1.6" strokeLinecap="round" />
              </svg>
              Ask anything
            </span>
            <kbd className="inset-sm figure rounded-md px-1.5 py-0.5 text-[10.5px] text-ink-3">⌘K</kbd>
          </button>
          <div className="grid grid-cols-2 gap-2.5">
            <button
              type="button"
              onClick={() => open({ kind: "share" })}
              className="raised-sm press min-h-11 rounded-[14px] text-[12.5px] text-ink-3 hover:text-ink"
            >
              Share
            </button>
            <button
              type="button"
              onClick={() => open({ kind: "share", tab: "follow" })}
              className="raised-sm press min-h-11 rounded-[14px] text-[12.5px] text-ink-3 hover:text-ink"
            >
              Follow
            </button>
          </div>
          <span className="figure px-1.5 text-[10.5px] text-faint">data as of {asOf}</span>
        </div>
      </nav>

      <nav
        data-split
        aria-label="Pages"
        className="glass fixed inset-x-3 bottom-4 z-30 grid grid-cols-5 gap-1 rounded-3xl p-2 lg:hidden"
        style={{ background: "rgb(20 20 24 / 0.82)" }}
      >
        {PAGES.map((page) => {
          const on = active(path, page.href);
          return (
            <Link
              key={page.href}
              href={page.href}
              aria-current={on ? "page" : undefined}
              className={`flex min-h-11 flex-col items-center gap-1 rounded-2xl py-2 text-[10px] ${
                on ? "pill-on" : "text-ink-3"
              }`}
            >
              <svg width="18" height="18" viewBox="0 0 16 16" aria-hidden="true">
                <path
                  d={page.icon}
                  stroke={on ? "#111113" : page.color}
                  strokeWidth="1.6"
                  fill="none"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                />
              </svg>
              {page.label.split(" ")[0]}
            </Link>
          );
        })}
      </nav>
    </>
  );
}
