import type { Metadata } from "next";
import { Archivo, Chivo_Mono, Saira_Condensed } from "next/font/google";
import { Rail } from "@/components/rail";
import { loadWeekend } from "@/lib/load";
import "@/styles/tokens.css";

const archivo = Archivo({ subsets: ["latin"], variable: "--font-archivo", display: "swap" });
const chivoMono = Chivo_Mono({
  subsets: ["latin"],
  variable: "--font-chivo-mono",
  display: "swap",
});
const sairaCondensed = Saira_Condensed({
  subsets: ["latin"],
  weight: "500",
  variable: "--font-saira-condensed",
  display: "swap",
});

export const metadata: Metadata = {
  title: "Pit Advisor",
  description:
    "Race-weekend form, quali-race delta, track fit, weather and reliability, every figure with its interval.",
};

const CONTRACT = `<!--
THESIS: every figure is engraved on its own calibrated track, so a number cannot be read
without the scale and interval it came with. Refuses the tiled KPI dashboard and the
broadcast timing tower.
OWN-WORLD: matte dial black, lume cream, engraved steel hairlines, one split-second red.
Saira Condensed for the legend a plate is titled with, Archivo for labels, Chivo Mono for
figures, all three Omnibus-Type. No cards, no shadows, no gauges: rows sit on hairlines and
a missing fit is a hatched cut face carrying its reason.
STORY: the reader arrives days before a race, reads form, quali-race delta, track fit and
reliability, and can tell a real signal from a thin sample without leaving the page.
FIRST VIEWPORT: chapter ring across the top carrying event, as-of and run; route rail left;
driver tracks under one shared ruler; coverage and exclusions engraved to the right.
SIGNATURE: scale lock. Hover or focus any track and a split hairline drops through every
other track at that value; click pins it, the way a rattrapante freezes one hand.
FORM: Dial Plate, candidate 5 of the grounded list, seed key 4df33edb.
FINISH: unreviewed and undocumented is unfinished; this build ends with the finish review,
the verdict, DESIGN.md, and every shipping raster carrying its provenance.
-->`;

export default async function RootLayout({ children }: { children: React.ReactNode }) {
  const weekend = await loadWeekend();
  return (
    <html lang="en" className={`${archivo.variable} ${chivoMono.variable} ${sairaCondensed.variable}`}>
      <body className="min-h-dvh">
        <div hidden aria-hidden="true" dangerouslySetInnerHTML={{ __html: CONTRACT }} />
        <div className="lg:flex">
          <Rail trackHref={`/track/${weekend.event.circuit_id}/`} />
          <div className="min-w-0 flex-1 px-5 py-8 sm:px-8 lg:px-12 lg:py-10">{children}</div>
        </div>
      </body>
    </html>
  );
}
