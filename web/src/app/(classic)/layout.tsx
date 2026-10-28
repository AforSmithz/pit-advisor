import { Curtain } from "@/components/shell/curtain";
import { Sidebar } from "@/components/shell/sidebar";
import { lightsOut, raceShort, stamp } from "@/lib/format";
import { loadForecast } from "@/lib/load";

export default async function ClassicLayout({ children }: { children: React.ReactNode }) {
  const forecast = await loadForecast();
  const race = `R${forecast.event.round} ${raceShort(forecast.event.race_name)}`;
  return (
    <>
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
          <Sidebar race={race} lightsOut={lightsOut(forecast.event.start_utc)} asOf={stamp(forecast.generated_at)} />
          <main className="flex min-w-0 flex-1 flex-col gap-4">{children}</main>
        </div>
      </div>
      <Curtain label={`${race}  ·  ${lightsOut(forecast.event.start_utc)}`} />
    </>
  );
}
