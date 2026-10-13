import type { Metadata } from "next";
import { Archivo, Chivo_Mono, Saira_Condensed } from "next/font/google";
import { Overlays, type ViewStamp } from "@/components/shell/overlays";
import { Sidebar } from "@/components/shell/sidebar";
import { lightsOut, raceShort, stamp } from "@/lib/format";
import {
  loadCalibration,
  loadDebrief,
  loadDriver,
  loadForecast,
  loadHistory,
  loadLineage,
  loadStewards,
  loadTrack,
  loadUpgrades,
  loadWeekend,
} from "@/lib/load";
import { SITE } from "@/lib/site";
import "@/styles/tokens.css";

const archivo = Archivo({ subsets: ["latin"], variable: "--font-archivo", display: "swap" });
const chivoMono = Chivo_Mono({ subsets: ["latin"], variable: "--font-chivo-mono", display: "swap" });
const sairaCondensed = Saira_Condensed({
  subsets: ["latin"],
  weight: ["500", "600"],
  variable: "--font-saira-condensed",
  display: "swap",
});

export const metadata: Metadata = {
  metadataBase: new URL(SITE),
  title: "Pit Advisor",
  description:
    "Formula 1 race-weekend forecast, form, track fit and stewards, every figure with its interval and its record.",
  openGraph: { images: [{ url: "/share.png", width: 1200, height: 630 }] },
  twitter: { card: "summary_large_image", images: ["/share.png"] },
  alternates: { types: { "application/rss+xml": "/feed.xml" } },
};

export default async function RootLayout({ children }: { children: React.ReactNode }) {
  const [forecast, weekend, driver, track, calibration, history, stewards, upgrades, debrief, lineage] =
    await Promise.all([
      loadForecast(),
      loadWeekend(),
      loadDriver(),
      loadTrack(),
      loadCalibration(),
      loadHistory(),
      loadStewards(),
      loadUpgrades(),
      loadDebrief(),
      loadLineage(),
    ]);
  const stamps: Record<string, ViewStamp> = {};
  for (const view of [forecast, weekend, driver, track, calibration, history, stewards, upgrades, debrief]) {
    if (view) stamps[view.view] = { run_id: view.run_id, generated_at: view.generated_at };
  }
  const leader = [...forecast.drivers].sort((a, b) => b.win - a.win)[0];
  const title = leader
    ? `${forecast.event.race_name}: ${leader.driver_code} wins in ${(leader.win * 100).toFixed(1)}% of ${forecast.paths.toLocaleString("en")} simulated races`
    : forecast.event.race_name;
  return (
    <html lang="en" className={`${archivo.variable} ${chivoMono.variable} ${sairaCondensed.variable}`}>
      <body className="min-h-dvh">
        <Overlays
          lineage={lineage}
          stamps={stamps}
          share={{ title, race: `${forecast.event.season}-${forecast.event.round}` }}
        >
          <div className="relative min-h-dvh overflow-clip">
            <div
              aria-hidden="true"
              className="pointer-events-none absolute -top-60 left-44 h-[620px] w-[820px] rounded-full bg-[radial-gradient(closest-side,rgba(79,125,255,0.18),transparent)]"
            />
            <div
              aria-hidden="true"
              className="pointer-events-none absolute top-72 -right-52 h-[700px] w-[760px] rounded-full bg-[radial-gradient(closest-side,rgba(255,95,109,0.13),transparent)]"
            />
            <div className="relative flex gap-4 p-4 pb-28 lg:pb-4">
              <Sidebar
                race={`R${forecast.event.round} ${raceShort(forecast.event.race_name)}`}
                lightsOut={lightsOut(forecast.event.start_utc)}
                asOf={stamp(forecast.generated_at)}
              />
              <main className="flex min-w-0 flex-1 flex-col gap-4">{children}</main>
            </div>
          </div>
        </Overlays>
      </body>
    </html>
  );
}
