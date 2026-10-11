import { notFound } from "next/navigation";
import { DriverPanel } from "@/components/paddock/driver-panel";
import { loadDriver, loadForecast, loadUpgrades } from "@/lib/load";

export async function generateStaticParams() {
  const view = await loadDriver();
  return view.drivers.map((driver) => ({ code: driver.driver_code }));
}

export async function generateMetadata({ params }: { params: Promise<{ code: string }> }) {
  const { code } = await params;
  return { title: `${code} · Pit Advisor` };
}

export default async function Driver({ params }: { params: Promise<{ code: string }> }) {
  const { code } = await params;
  const [view, forecast, upgrades] = await Promise.all([loadDriver(), loadForecast(), loadUpgrades()]);
  if (!view.drivers.some((driver) => driver.driver_code === code)) notFound();
  return <DriverPanel code={code} view={view} forecast={forecast} upgrades={upgrades} />;
}
