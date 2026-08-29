import { label, percentOf, plain } from "@/lib/format";
import type { ForecastDriver } from "@/lib/schemas";

const COLUMNS =
  "grid grid-cols-[7.5rem_1fr_3.25rem_3.25rem_3.25rem] items-center gap-x-4";

function at(place: number, field: number): number {
  return ((place - 0.5) / field) * 100;
}

export function OrderRuler({ field }: { field: number }) {
  const ticks = [1, 5, 10, 15, 20].filter((tick) => tick <= field);
  return (
    <div className={`${COLUMNS} pb-1`}>
      <div className="engraved">finishing place</div>
      <div className="relative h-6">
        <div className="absolute inset-x-0 bottom-0 h-px bg-engrave-lit" />
        {ticks.map((tick) => (
          <div
            key={tick}
            className="absolute bottom-0 flex -translate-x-1/2 flex-col items-center"
            style={{ left: `${at(tick, field)}%` }}
          >
            <span className="figure mb-1 text-[0.625rem] leading-none text-steel">
              P{tick}
            </span>
            <span className="h-1.5 w-px bg-engrave-lit" />
          </div>
        ))}
      </div>
      <div className="engraved text-right">win</div>
      <div className="engraved text-right">pod</div>
      <div className="engraved text-right">pts</div>
    </div>
  );
}

export function Order({
  driver,
  field,
}: {
  driver: ForecastDriver;
  field: number;
}) {
  const left = at(driver.position_low, field);
  const right = at(driver.position_high, field);
  const mark = at(driver.expected_position, field);
  const grid = at(driver.grid, field);

  return (
    <div
      className={`${COLUMNS} border-t border-engrave py-2 hover:bg-subplate`}
      aria-label={`${driver.driver_code} starts ${driver.grid}, expected ${plain(
        driver.expected_position,
        1,
      )}, eight paths in ten finish between ${driver.position_low} and ${driver.position_high}`}
    >
      <div>
        <a
          href={`/driver/${driver.driver_code}/`}
          className="block text-sm tracking-plate text-lume hover:text-split-lit"
        >
          {driver.driver_code}
        </a>
        <span className="engraved block">{label(driver.constructor_id)}</span>
      </div>

      <div className="relative h-7">
        <div className="absolute inset-x-0 top-1/2 h-px -translate-y-1/2 bg-engrave" />
        {[1, 5, 10, 15, 20]
          .filter((tick) => tick <= field)
          .map((tick) => (
            <span
              key={tick}
              className="absolute top-1/2 h-2 w-px -translate-x-1/2 -translate-y-1/2 bg-engrave"
              style={{ left: `${at(tick, field)}%` }}
            />
          ))}
        <div
          className="absolute top-1/2 h-2 -translate-y-1/2 bg-lume/15"
          style={{ left: `${left}%`, width: `${Math.max(right - left, 0.6)}%` }}
        />
        <span
          className="absolute top-1/2 h-2 w-px -translate-y-1/2 bg-lume/60"
          style={{ left: `${left}%` }}
        />
        <span
          className="absolute top-1/2 h-2 w-px -translate-y-1/2 bg-lume/60"
          style={{ left: `${right}%` }}
        />
        {/* where the car actually starts, so the plate shows what the race is expected to
            do to the grid rather than only where it ends up */}
        <span
          className="absolute top-1/2 h-5 w-px -translate-y-1/2 border-l border-dashed border-steel"
          style={{ left: `${grid}%` }}
          title={`starts P${driver.grid}`}
        />
        <span
          className="absolute top-1/2 h-5 w-0.5 -translate-x-1/2 -translate-y-1/2 bg-split"
          style={{ left: `${mark}%` }}
        />
      </div>

      <span className="figure text-right text-sm text-lume">
        {percentOf(driver.win)}
      </span>
      <span className="figure text-right text-sm text-lume-dim">
        {percentOf(driver.podium)}
      </span>
      <span className="figure text-right text-sm text-lume-dim">
        {percentOf(driver.points)}
      </span>
    </div>
  );
}

export function Spread({
  driver,
  field,
}: {
  driver: ForecastDriver;
  field: number;
}) {
  const cells = driver.position.slice(0, field);
  const peak = Math.max(...cells, 1e-9);
  return (
    <div className="grid grid-cols-[7.5rem_1fr_3.5rem] items-center gap-x-4 border-t border-engrave py-1.5">
      <span className="text-sm tracking-plate text-lume">
        {driver.driver_code}
      </span>
      <div className="flex h-4 gap-px">
        {cells.map((share, place) => (
          <span
            key={place}
            className="flex-1 bg-lume"
            style={{ opacity: Math.max(share / peak, 0.03) }}
            title={`P${place + 1} in ${(share * 100).toFixed(1)}% of paths`}
          />
        ))}
      </div>
      <span className="figure text-right text-xs text-lume-dim">
        {percentOf(driver.finish)}
      </span>
    </div>
  );
}
