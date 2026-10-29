import type { UpgradesView } from "@/lib/schemas";

const CATEGORIES = [
  ["performance", "Performance", "#ff8a4c"],
  ["circuit", "Circuit specific", "#2ee6c5"],
  ["reliability", "Reliability", "#8a5cff"],
] as const;

export function Upgrades({ view, team }: { view: UpgradesView; team: string }) {
  const record = view.teams.find((item) => item.constructor_id === team);
  const declared = view.this_event.filter((item) => item.constructor_id === team);
  if (!record) return <p className="text-sm text-ink-3">No upgrade documents read for this team.</p>;
  const most = Math.max(1, ...view.teams.flatMap((item) => item.rounds.flatMap((round) => Object.values(round.parts))));
  const unread = record.rounds.filter((round) => !round.read).map((round) => round.round);
  const columns = `86px repeat(${view.rounds.length}, minmax(0, 1fr))`;
  return (
    <div className="flex flex-col gap-1.5">
      <div className="figure grid gap-[3px] text-[9px] text-faint" style={{ gridTemplateColumns: columns }}>
        <span />
        {view.rounds.map((round) => (
          <span key={round} className={`text-center ${round === view.event.round ? "text-ink" : ""}`}>
            {round}
          </span>
        ))}
      </div>
      {CATEGORIES.map(([key, name, color]) => (
        <div key={key} className="grid h-[22px] items-center gap-[3px]" style={{ gridTemplateColumns: columns }}>
          <span className="flex items-center gap-1.5 text-[11px] text-ink-3">
            <span className="h-[11px] w-[3px] rounded-sm" style={{ background: color }} />
            {name}
          </span>
          {view.rounds.map((number) => {
            const round = record.rounds.find((item) => item.round === number);
            const parts = round?.parts[key] ?? 0;
            if (!round?.read) {
              return (
                <span key={number} className="flex justify-center" title={`Round ${number}: not read`}>
                  <span className="h-px w-2 bg-faint" />
                </span>
              );
            }
            const size = parts ? Math.round(7 + Math.sqrt(parts / most) * 9) : 4;
            return (
              <span key={number} className="flex justify-center">
                <span
                  title={`Round ${number}: ${parts} ${name.toLowerCase()} part${parts === 1 ? "" : "s"}`}
                  className="rounded-full"
                  style={{
                    width: size,
                    height: size,
                    background: parts ? color : "#26272c",
                    boxShadow: parts ? `0 0 8px ${color}` : "none",
                  }}
                />
              </span>
            );
          })}
        </div>
      ))}
      {declared.length > 0 && (
        <ul className="mt-1.5 flex flex-col gap-1.5">
          {declared.map((item) => (
            <li key={item.ordinal} className="pane-well rounded-xl px-3 py-2 text-[11.5px] leading-snug text-ink-2">
              <span className="mr-2 text-[10px] tracking-[0.08em] text-mute uppercase">{item.category}</span>
              {item.text.replace(/\s*Car Presentation\s*[–-].*$/i, "")}
            </li>
          ))}
        </ul>
      )}
      <span className="text-[10.5px] leading-snug text-mute">
        Parts each team declared in the FIA car presentation, per round.
        {unread.length > 0 ? ` Rounds ${unread.join(", ")} had no document to read.` : ""}
      </span>
    </div>
  );
}
