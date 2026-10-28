export type Page = { href: string; label: string; color: string; icon: string };

export const PAGES: Page[] = [
  { href: "/", label: "Race ops", color: "#ff8a4c", icon: "M2.5 2.5h4.5v4.5h-4.5zM9 2.5h4.5v4.5H9zM2.5 9h4.5v4.5h-4.5zM9 9h4.5v4.5H9z" },
  { href: "/forecast/", label: "Forecast", color: "#ffd25a", icon: "M2.5 11a5.5 5.5 0 1 1 11 0M8 11l2.8-3.4" },
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

export function active(path: string, href: string): boolean {
  return href === "/" ? path === "/" : path.startsWith(href);
}
