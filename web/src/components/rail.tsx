"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const ROUTES = [
  { href: "/", name: "Weekend" },
  { href: "/driver/", name: "Drivers" },
  { href: "/track/", name: "Track" },
  { href: "/forecast/", name: "Forecast" },
  { href: "/calibration/", name: "Calibration" },
  { href: "/pipeline/", name: "Pipeline" },
];

export function Rail({ trackHref }: { trackHref: string }) {
  const path = usePathname();
  return (
    <nav className="flex shrink-0 gap-x-6 gap-y-1 border-b border-engrave-lit px-5 py-3 lg:sticky lg:top-0 lg:h-dvh lg:w-52 lg:flex-col lg:justify-start lg:overflow-y-auto lg:border-b-0 lg:border-r lg:px-6 lg:py-8">
      <Link href="/" className="mb-0 hidden lg:mb-10 lg:block">
        <span className="legend block text-base text-lume">Pit Advisor</span>
        <span className="engraved block">race weekend dial</span>
      </Link>
      <ul className="flex flex-1 flex-wrap gap-x-5 gap-y-1 lg:flex-col lg:gap-y-0">
        {ROUTES.map((route) => {
          const href = route.href === "/track/" ? trackHref : route.href;
          const active = route.href === "/" ? path === "/" : path.startsWith(route.href);
          return (
            <li key={route.href} className="lg:border-t lg:border-engrave">
              <Link
                href={href}
                aria-current={active ? "page" : undefined}
                className={`flex items-baseline gap-2 py-1 text-sm tracking-plate lg:py-2 ${
                  active ? "text-lume" : "text-steel hover:text-lume"
                }`}
              >
                <span
                  className={`h-3 w-px shrink-0 ${active ? "bg-split" : "bg-transparent"}`}
                  aria-hidden
                />
                <span>{route.name}</span>
              </Link>
            </li>
          );
        })}
      </ul>
      <p className="engraved hidden normal-case tracking-normal lg:mt-auto lg:block">
        Every figure on this dial was computed in the backend and arrives with its interval and
        sample count. Nothing here is calculated in the browser.
      </p>
    </nav>
  );
}
