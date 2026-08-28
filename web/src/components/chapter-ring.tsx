import { isoDate, label } from "@/lib/format";
import { Since } from "./since";

type Props = {
  season: number;
  round: number;
  raceName: string;
  circuitId: string;
  raceDate: string;
  asOf: string;
  runId: string;
  generatedAt: string;
};

export function ChapterRing(props: Props) {
  return (
    <header className="border-b border-engrave-lit pb-5">
      <div className="relative mb-5 h-3">
        <div className="absolute inset-x-0 bottom-0 h-px bg-engrave-lit" />
        {Array.from({ length: 61 }, (_, index) => (
          <span
            key={index}
            className={`absolute bottom-0 w-px ${
              index % 5 === 0 ? "h-3 bg-steel" : "h-1.5 bg-engrave"
            }`}
            style={{ left: `${(index / 60) * 100}%` }}
          />
        ))}
      </div>
      <div className="flex flex-wrap items-end justify-between gap-x-10 gap-y-4">
        <div className="flex items-end gap-5 sm:gap-7">
          <div className="border-r border-engrave-lit pr-5 sm:pr-7">
            <span className="engraved block">round</span>
            <span className="figure block text-5xl leading-none text-lume sm:text-6xl">
              {props.round}
            </span>
          </div>
          <div>
            <h1 className="text-3xl tracking-plate text-lume sm:text-4xl">{props.raceName}</h1>
            <p className="engraved mt-2">
              {props.season} · {label(props.circuitId)}
            </p>
          </div>
        </div>
        <dl className="flex flex-wrap gap-x-8 gap-y-2 sm:text-right">
          <div>
            <dt className="engraved">race</dt>
            <dd className="figure text-sm text-lume">{props.raceDate}</dd>
          </div>
          <div>
            <dt className="engraved">as of</dt>
            <dd className="figure text-sm text-lume">{isoDate(props.asOf)}</dd>
          </div>
          <div>
            <dt className="engraved">run</dt>
            <dd className="figure text-sm text-lume">{props.runId}</dd>
          </div>
          <div>
            <dt className="engraved">emitted</dt>
            <dd className="figure text-sm text-lume">
              <Since iso={props.generatedAt} />
            </dd>
          </div>
        </dl>
      </div>
    </header>
  );
}
