import { pct, snapshotLabel } from "@/lib/format";
import { loadForecast, loadHistory } from "@/lib/load";
import { SITE } from "@/lib/site";

export const dynamic = "force-static";

function escape(text: string): string {
  return text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

export async function GET() {
  const [forecast, history] = await Promise.all([loadForecast(), loadHistory()]);
  const snapshots = (history?.snapshots ?? []).filter((snapshot) => !snapshot.repeat).reverse();
  const items = snapshots.map((snapshot) => {
    const top = [...snapshot.drivers].sort((a, b) => b.win - a.win).slice(0, 3);
    const title = `${forecast.event.race_name}, ${snapshotLabel(snapshot).toLowerCase()}: ${top
      .map((driver) => `${driver.driver_code} ${pct(driver.win)}`)
      .join(", ")}`;
    return [
      "<item>",
      `<title>${escape(title)}</title>`,
      `<link>${SITE}/</link>`,
      `<guid isPermaLink="false">${snapshot.run_id}</guid>`,
      `<pubDate>${new Date(snapshot.generated_at).toUTCString()}</pubDate>`,
      `<description>${escape("Chance to win at this published forecast, from the simulated races behind it.")}</description>`,
      "</item>",
    ].join("");
  });
  const xml = [
    '<?xml version="1.0" encoding="UTF-8"?>',
    '<rss version="2.0">',
    "<channel>",
    "<title>Pit Advisor</title>",
    `<link>${SITE}/</link>`,
    "<description>Every race forecast as it is published, before and after qualifying.</description>",
    ...items,
    "</channel>",
    "</rss>",
  ].join("\n");
  return new Response(xml, { headers: { "Content-Type": "application/rss+xml; charset=utf-8" } });
}
