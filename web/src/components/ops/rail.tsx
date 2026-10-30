"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { PAGES, active } from "@/lib/pages";

function countdown(startUtc: string | null, now: number): string {
  if (!startUtc) return "";
  const ms = new Date(startUtc).getTime() - now;
  if (ms <= 0) return "live";
  const h = Math.floor(ms / 3.6e6);
  return h >= 24 ? `${Math.floor(h / 24)}d ${h % 24}h` : `${h}h ${Math.floor((ms % 3.6e6) / 6e4)}m`;
}

function Icon({ d, colour }: { d: string; colour: string }) {
  return (
    <svg width="20" height="20" viewBox="0 0 16 16" aria-hidden="true">
      <path d={d} stroke={colour} strokeWidth="1.5" fill="none" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

export function Rail({ startUtc }: { startUtc: string | null }) {
  const path = usePathname();
  const [left, setLeft] = useState("");
  useEffect(() => {
    const tick = () => setLeft(countdown(startUtc, Date.now()));
    const first = setTimeout(tick, 0);
    const every = setInterval(tick, 30_000);
    return () => {
      clearTimeout(first);
      clearInterval(every);
    };
  }, [startUtc]);

  return (
    <aside className="pointer-events-auto fixed inset-x-4 bottom-4 z-30 flex items-center gap-2.5 lg:inset-x-auto lg:top-5 lg:right-5 lg:bottom-[30px] lg:w-[76px] lg:flex-col lg:gap-4">
      <div className="hidden pt-1 text-center lg:block">
        <div className="figure text-[17px] text-ink">{left || " "}</div>
        <div className="mt-0.5 text-[10px] text-mute">to lights out</div>
      </div>
      <nav aria-label="Pages" className="pane flex w-full justify-between gap-1 rounded-full p-1.5 lg:mt-auto lg:w-auto lg:flex-col lg:gap-2.5 lg:p-2">
        {PAGES.map((page) => {
          const on = active(path, page.href);
          return (
            <Link
              key={page.href}
              href={page.href}
              aria-label={page.label}
              title={page.label}
              aria-current={on ? "page" : undefined}
              className={`press grid h-11 w-11 place-items-center rounded-full lg:h-[52px] lg:w-[52px] ${on ? "pane-on" : "hover:bg-white/5"}`}
            >
              <Icon d={page.icon} colour={on ? "#111113" : page.color} />
            </Link>
          );
        })}
      </nav>
    </aside>
  );
}
