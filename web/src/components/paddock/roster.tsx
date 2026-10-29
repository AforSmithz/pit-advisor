import Link from "next/link";
import { signed } from "@/lib/format";
import { along } from "@/lib/geometry";
import { teamColor } from "@/lib/teams";
import type { DriverHistory } from "@/lib/schemas";

const THIN = 20;

export function Roster({ drivers, current }: { drivers: DriverHistory[]; current?: string }) {
  const rated = drivers.filter((driver) => driver.form);
  const reach = Math.max(...rated.flatMap((driver) => [Math.abs(driver.form!.low), Math.abs(driver.form!.high)]));
  const sorted = [...drivers].sort((a, b) => (a.form?.value ?? 99) - (b.form?.value ?? 99));
  const x = (value: number) => along(value, -reach, reach) * 100;
  return (
    <ul className="flex flex-col">
      {sorted.map((driver) => {
        const color = teamColor(driver.constructor_id);
        const on = driver.driver_code === current;
        const form = driver.form;
        const thin = form !== null && form.samples < THIN;
        return (
          <li key={driver.driver_code}>
            <Link
              href={`/paddock/${driver.driver_code}/`}
              aria-current={on ? "page" : undefined}
              aria-label={`${driver.driver_code}: ${form ? `form ${signed(form.value)}, ${form.samples} races` : "no form estimate"}`}
              className={`press grid h-[27px] grid-cols-[3px_34px_minmax(0,1fr)_44px] items-center gap-2 rounded-[10px] px-1.5 ${
                on ? "pane" : "hover:bg-white/[0.03]"
              }`}
            >
              <span className="h-[13px] w-[3px] rounded-sm" style={{ background: color, boxShadow: `0 0 5px ${color}` }} />
              <span className={`code text-sm ${on ? "text-ink" : "text-ink-2"}`}>{driver.driver_code}</span>
              <span className="relative block h-2.5">
                <span className="absolute top-[-2px] bottom-[-2px] left-1/2 w-px bg-rule-lit" />
                {form && (
                  <>
                    <span
                      className="grow absolute top-[3px] h-1 rounded-full"
                      style={{
                        left: `${x(form.low)}%`,
                        width: `${x(form.high) - x(form.low)}%`,
                        background: thin ? "#4e5058" : "var(--cool)",
                        opacity: thin ? 1 : 0.85,
                      }}
                    />
                    <span
                      className="absolute top-0 h-2.5 w-[3px] -translate-x-1/2 rounded-sm bg-white"
                      style={{ left: `${x(form.value)}%` }}
                    />
                  </>
                )}
              </span>
              <span className={`figure text-right text-[11px] ${thin || !form ? "text-mute" : "text-ink-2"}`}>
                {form ? signed(form.value) : "–"}
              </span>
            </Link>
          </li>
        );
      })}
    </ul>
  );
}
