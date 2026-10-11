import { loadForecast, loadSprint } from "@/lib/load";
import { SITE } from "@/lib/site";

export const dynamic = "force-static";

function utc(iso: string): string {
  return `${iso.replace(/[-:]/g, "").slice(0, 15)}Z`;
}

function event(uid: string, start: string, hours: number, summary: string, stamp: string): string[] {
  const end = new Date(new Date(start).getTime() + hours * 3_600_000).toISOString();
  return [
    "BEGIN:VEVENT",
    `UID:${uid}@pit-advisor`,
    `DTSTAMP:${utc(stamp)}`,
    `DTSTART:${utc(start)}`,
    `DTEND:${utc(end)}`,
    `SUMMARY:${summary}`,
    `URL:${SITE}/`,
    `DESCRIPTION:Forecast and form at ${SITE}/`,
    "END:VEVENT",
  ];
}

export async function GET() {
  const [forecast, sprint] = await Promise.all([loadForecast(), loadSprint()]);
  const { season, round, race_name } = forecast.event;
  const lines = [
    "BEGIN:VCALENDAR",
    "VERSION:2.0",
    "PRODID:-//Pit Advisor//Race weekend//EN",
    "CALSCALE:GREGORIAN",
    "X-WR-CALNAME:Pit Advisor",
    "REFRESH-INTERVAL;VALUE=DURATION:PT12H",
  ];
  if (sprint?.sprint_utc && sprint.state !== "no_sprint") {
    lines.push(...event(`${season}-${round}-sprint`, sprint.sprint_utc, 1, `${race_name}, sprint`, forecast.generated_at));
  }
  if (forecast.event.start_utc) {
    lines.push(...event(`${season}-${round}-race`, forecast.event.start_utc, 2, race_name, forecast.generated_at));
  }
  lines.push("END:VCALENDAR");
  return new Response(`${lines.join("\r\n")}\r\n`, { headers: { "Content-Type": "text/calendar; charset=utf-8" } });
}
