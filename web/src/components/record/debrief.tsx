import { plain, raceShort, stamp } from "@/lib/format";
import { heat } from "@/lib/geometry";
import { teamColor } from "@/lib/teams";
import type { DebriefView } from "@/lib/schemas";

const STAGE: Record<string, string> = {
  before_qualifying: "before qualifying",
  after_qualifying: "after qualifying",
  after_sprint: "after the sprint",
};

export function Debrief({ view }: { view: DebriefView }) {
  const race = raceShort(view.event.race_name);
  const done = view.finish.length > 0;
  const called = view.called;
  const rows = called ? [...called.drivers].sort((a, b) => b.win - a.win).slice(0, 8) : [];
  const brightest = Math.max(...rows.flatMap((driver) => driver.position), 0.01);
  const field = Math.max(...rows.map((driver) => driver.position.length), 1);
  return (
    <section className="glass flex min-w-0 flex-[1_1_420px] flex-col gap-2.5 rounded-[28px] px-5 py-4">
      <div className="flex items-baseline justify-between">
        <h2 className="display text-[17px]">{race} debrief</h2>
        <span className={`text-[11px] ${done ? "text-mute" : "text-signal"}`}>
          {done ? "result in" : "after the race"}
        </span>
      </div>
      <span className="text-[12.5px] leading-snug text-ink-2">
        {called
          ? `The call standing at lights out, published ${STAGE[called.stage] ?? called.stage}, ${stamp(called.generated_at)}. ${
              done ? "Each finish is ringed on the forecast it was given." : "Once the result is in, each finish lands on its forecast."
            }`
          : "No forecast was published for this race before it started, so there is nothing to grade."}
      </span>
      {called && (
        <div className="inset flex flex-1 flex-col justify-around gap-1 rounded-[20px] px-3 py-2.5">
          {rows.map((driver) => {
            const result = view.finish.find((item) => item.driver_code === driver.driver_code);
            const color = teamColor(driver.constructor_id);
            return (
              <div key={driver.driver_code} className="grid grid-cols-[3px_34px_minmax(0,1fr)_54px] items-center gap-2">
                <span className="h-3 w-[3px] rounded-sm" style={{ background: color }} />
                <span className="code text-sm">{driver.driver_code}</span>
                <span
                  className="grid h-3 gap-[1.5px]"
                  style={{ gridTemplateColumns: `repeat(${field}, minmax(0, 1fr))` }}
                >
                  {driver.position.map((share, cell) => (
                    <span
                      key={cell}
                      className="rounded-[3px]"
                      style={{
                        background: heat(Math.pow(share / brightest, 0.6)),
                        outline: result?.position === cell + 1 ? "2px solid #ffffff" : "none",
                        outlineOffset: 1,
                      }}
                    />
                  ))}
                </span>
                {result ? (
                  <span className="figure rounded-[7px] text-center text-[11px] text-ink">
                    {result.position ? `P${result.position}` : result.status}
                  </span>
                ) : (
                  <span className="rounded-[7px] border border-dashed border-[#34353c] py-0.5 text-center text-[10px] text-mute">
                    P?
                  </span>
                )}
              </div>
            );
          })}
        </div>
      )}
      {view.record.length > 0 ? (
        <div className="flex flex-col gap-1">
          <span className="text-[11px] text-mute">The published record, log loss on the race, lower is better</span>
          <ul className="figure flex flex-col gap-0.5 text-[11.5px]">
            {view.record.map((score) => (
              <li key={score.run_id} className="grid grid-cols-[1fr_auto_auto] gap-3">
                <span className="text-ink-3">
                  R{score.round} {STAGE[score.stage] ?? score.stage}
                </span>
                <span className="text-signal">
                  {score.log_loss.simulation === undefined ? "–" : plain(score.log_loss.simulation, 3)}
                </span>
                <span className="text-blue">
                  grid {score.log_loss.grid === undefined ? "–" : plain(score.log_loss.grid, 3)}
                </span>
              </li>
            ))}
          </ul>
        </div>
      ) : (
        <span className="text-[11px] text-mute">
          The published record starts with this race: every forecast is kept as it stood before the session and graded
          once it is run.
        </span>
      )}
    </section>
  );
}
