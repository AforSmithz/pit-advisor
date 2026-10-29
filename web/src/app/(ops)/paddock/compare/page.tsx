import { Compare } from "@/components/paddock/compare";
import { PaddockTabs } from "@/components/paddock/driver-panel";
import { PageHead } from "@/components/tile";
import { loadDriver, loadForecast } from "@/lib/load";

export const metadata = { title: "Head to head · Pit Advisor" };

export default async function HeadToHead() {
  const [view, forecast] = await Promise.all([loadDriver(), loadForecast()]);
  const ranked = [...forecast.drivers]
    .sort((a, b) => b.win - a.win)
    .filter((driver) => view.drivers.some((item) => item.driver_code === driver.driver_code));
  const field = view.drivers.filter((driver) => ranked.some((item) => item.driver_code === driver.driver_code));
  const drivers = (field.length >= 2 ? field : view.drivers).sort((a, b) => a.driver_code.localeCompare(b.driver_code));
  const initial: [string, string] = [
    ranked[0]?.driver_code ?? drivers[0]!.driver_code,
    ranked[1]?.driver_code ?? drivers[1]!.driver_code,
  ];
  return (
    <Compare
      head={
        <PageHead title="Paddock" sub="Any two drivers, every instrument mirrored.">
          <PaddockTabs on="compare" />
        </PageHead>
      }
      drivers={drivers}
      field={forecast.drivers}
      initial={initial}
    />
  );
}
