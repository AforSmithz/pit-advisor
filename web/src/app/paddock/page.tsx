import { DriverPanel } from "@/components/paddock/driver-panel";
import { loadDriver, loadForecast, loadUpgrades } from "@/lib/load";

export const metadata = { title: "Paddock · Pit Advisor" };

export default async function Paddock() {
  const [view, forecast, upgrades] = await Promise.all([loadDriver(), loadForecast(), loadUpgrades()]);
  const favourite = [...forecast.drivers]
    .sort((a, b) => b.win - a.win)
    .find((driver) => view.drivers.some((item) => item.driver_code === driver.driver_code));
  const code = favourite?.driver_code ?? view.drivers[0]!.driver_code;
  return <DriverPanel code={code} view={view} forecast={forecast} upgrades={upgrades} />;
}
