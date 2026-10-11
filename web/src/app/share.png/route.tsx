import { ImageResponse } from "next/og";
import { BASELINES, pct } from "@/lib/format";
import { arc } from "@/lib/geometry";
import { loadForecast, loadHistory } from "@/lib/load";
import { teamColor } from "@/lib/teams";

export const dynamic = "force-static";

const STAGE: Record<string, string> = {
  before_qualifying: "before qualifying",
  after_qualifying: "after qualifying",
};

export async function GET() {
  const [forecast, history] = await Promise.all([loadForecast(), loadHistory()]);
  const ranked = [...forecast.drivers].sort((a, b) => b.win - a.win);
  const lead = ranked[0]!;
  const top = ranked.slice(0, 5);
  const stage = history?.snapshots.find((snapshot) => snapshot.run_id === forecast.run_id)?.stage;
  const evidence = forecast.evidence;
  const beaten = evidence ? evidence.separated_from.filter((name) => name in BASELINES).map((name) => BASELINES[name]) : [];

  let at = 0;
  const ring = ranked.flatMap((driver, index) => {
    const span = driver.win * 360;
    const from = at;
    at += span;
    if (span <= 2.6) return [];
    return [{ d: arc(200, 200, 151, from + 1.4, from + span - 1.4), color: teamColor(driver.constructor_id), first: index === 0 }];
  });

  return new ImageResponse(
    (
      <div
        style={{
          width: 1200,
          height: 630,
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          padding: "54px 64px",
          background: "#111113",
          backgroundImage:
            "radial-gradient(circle at 10% 0%, rgba(79,125,255,0.3), transparent 45%), radial-gradient(circle at 100% 100%, rgba(255,95,109,0.22), transparent 45%)",
          color: "#ececef",
        }}
      >
        <div style={{ display: "flex", flexDirection: "column", gap: 26, width: 640 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 12, fontSize: 19, color: "#a2a3ab" }}>
            <span style={{ color: "#ececef", letterSpacing: 3 }}>PIT ADVISOR</span>
            <span>
              {forecast.event.race_name}
              {stage ? `, ${STAGE[stage] ?? stage}` : ""}
            </span>
          </div>
          <div style={{ display: "flex", flexDirection: "column" }}>
            <span style={{ fontSize: 96, lineHeight: 0.95, letterSpacing: 2 }}>{lead.driver_code}</span>
            <span style={{ fontSize: 40, color: "#a2a3ab" }}>
              wins in&nbsp;<span style={{ color: "#7ea2ff" }}>{pct(lead.win)}</span>&nbsp;of races
            </span>
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
            {top.map((driver) => {
              const color = teamColor(driver.constructor_id);
              return (
                <div key={driver.driver_code} style={{ display: "flex", alignItems: "center", gap: 14 }}>
                  <span style={{ width: 54, fontSize: 22 }}>{driver.driver_code}</span>
                  <div style={{ display: "flex", width: 440, height: 18, borderRadius: 999, background: "#0d0d0f", padding: 4 }}>
                    <div
                      style={{
                        width: `${Math.max(driver.win * 100, 1.5)}%`,
                        height: 10,
                        borderRadius: 999,
                        background: color,
                        boxShadow: `0 0 12px ${color}`,
                      }}
                    />
                  </div>
                  <span style={{ width: 90, fontSize: 20, textAlign: "right" }}>{pct(driver.win)}</span>
                </div>
              );
            })}
          </div>
          <span style={{ fontSize: 16, color: "#7f818a" }}>
            {`${forecast.paths.toLocaleString("en")} simulated races.${
              evidence
                ? ` Tested on ${evidence.races} past races${beaten.length ? `, clearly better than ${beaten.join(" and ")}` : ""}.`
                : ""
            }`}
          </span>
        </div>
        <div
          style={{
            display: "flex",
            position: "relative",
            width: 400,
            height: 400,
            borderRadius: 400,
            background: "linear-gradient(145deg, #18181b, #0f0f11)",
            boxShadow: "-10px -10px 24px rgba(255,255,255,0.03), 16px 18px 40px rgba(0,0,0,0.85)",
          }}
        >
          <div
            style={{
              position: "absolute",
              left: 30,
              top: 30,
              width: 340,
              height: 340,
              borderRadius: 340,
              background: "#0d0d0f",
            }}
          />
          <svg width="400" height="400" viewBox="0 0 400 400" style={{ position: "absolute", left: 0, top: 0 }}>
            {ring.map((segment) => (
              <path
                key={segment.d}
                d={segment.d}
                fill="none"
                stroke={segment.color}
                strokeOpacity={segment.first ? 1 : 0.75}
                strokeWidth={segment.first ? 17 : 13}
                strokeLinecap="round"
              />
            ))}
          </svg>
          <div
            style={{
              position: "absolute",
              left: 0,
              top: 0,
              width: 400,
              height: 400,
              display: "flex",
              flexDirection: "column",
              alignItems: "center",
              justifyContent: "center",
            }}
          >
            <span style={{ fontSize: 64, letterSpacing: 2 }}>{lead.driver_code}</span>
            <span style={{ fontSize: 24, color: "#7ea2ff" }}>{pct(lead.win)}</span>
          </div>
        </div>
      </div>
    ),
    { width: 1200, height: 630 },
  );
}
