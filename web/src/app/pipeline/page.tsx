import { Engraved, Plate } from "@/components/plate";
import { Since } from "@/components/since";
import { label, usd } from "@/lib/format";
import { loadCost, loadPipeline } from "@/lib/load";

export const metadata = { title: "Pipeline · Pit Advisor" };

const MARK: Record<"ok" | "warn" | "fail", string> = {
  ok: "passes",
  warn: "warns",
  fail: "FAILS",
};

export default async function PipelinePage() {
  const [view, cost] = await Promise.all([loadPipeline(), loadCost()]);

  return (
    <div className="pt-2">
      <div className="flex flex-wrap items-end justify-between gap-x-10 gap-y-4 border-b border-engrave-lit pb-5">
        <div>
          <h1 className="legend text-4xl text-lume sm:text-5xl">Pipeline</h1>
          <p className="engraved mt-2">
            {view.layer} · {view.healthy ? "gate open" : "gate closed"}
          </p>
        </div>
        <dl className="flex flex-wrap gap-x-8 gap-y-2 text-right">
          <div>
            <dt className="engraved">run</dt>
            <dd className="figure text-sm text-lume">{view.run_id}</dd>
          </div>
          <div>
            <dt className="engraved">checked</dt>
            <dd className="figure text-sm text-lume">
              <Since iso={view.generated_at} />
            </dd>
          </div>
        </dl>
      </div>

      <div className="mt-10 grid gap-10 xl:grid-cols-[minmax(0,1fr)_20rem]">
        <div className="flex flex-col gap-10">
          <Plate
            title="Tables"
            note="Row counts, freshness, duplicate keys and referential integrity, run over the bronze layer on every emit."
          >
            <ul>
              {view.tables.map((table) => (
                <li key={table.table} className="border-t border-engrave py-3">
                  <div className="flex flex-wrap items-baseline justify-between gap-x-6 gap-y-1">
                    <span className="text-sm tracking-plate text-lume">{table.table}</span>
                    <span className="engraved">
                      {table.source} · {MARK[table.status]}
                    </span>
                  </div>
                  <p className="figure mt-1 text-xs leading-relaxed text-lume-dim">
                    {table.detail}
                  </p>
                </li>
              ))}
            </ul>
          </Plate>

          <Plate
            title="Diagnostics"
            note="Counts that are worth publishing rather than discarding: real, explained, and not defects."
          >
            {view.diagnostics.length ? (
              <ul>
                {view.diagnostics.map((item) => (
                  <li
                    key={`${item.table}-${item.name}`}
                    className="flex items-baseline justify-between gap-6 border-t border-engrave py-3"
                  >
                    <span>
                      <span className="block text-sm tracking-plate text-lume">
                        {item.name.replace(/_/g, " ")}
                      </span>
                      <span className="engraved normal-case tracking-normal text-lume-dim">
                        {item.detail}
                      </span>
                    </span>
                    <span className="figure text-lg text-lume">{item.value}</span>
                  </li>
                ))}
              </ul>
            ) : (
              <div className="cut-face flex h-16 items-center px-4">
                <span className="engraved text-lume-dim">nothing to report</span>
              </div>
            )}
          </Plate>

          <Plate
            title="Spend"
            note="Usage before credits, from Cost Explorer. Credits zero the bill, so they cannot be what the ceiling measures."
          >
            <div className="flex flex-wrap items-baseline justify-between gap-x-6 gap-y-1">
              <span className="engraved">
                {cost.month} · {cost.start} to {cost.through}
                {cost.estimated ? " · month open" : " · final"}
              </span>
              <span className="engraved">
                {cost.under_ceiling ? "under ceiling" : "OVER CEILING"}
              </span>
            </div>
            <div className="mt-3 flex items-baseline gap-3">
              <span className="figure text-4xl text-lume">{usd(cost.usage_usd)}</span>
              <span className="figure text-sm text-lume-dim">of {usd(cost.ceiling_usd)}</span>
            </div>
            <div className="mt-3 h-1 bg-engrave">
              <div
                className="h-1 bg-steel"
                style={{ width: `${Math.min(cost.usage_usd / cost.ceiling_usd, 1) * 100}%` }}
              />
            </div>
            <dl className="mt-4 grid gap-x-10 sm:grid-cols-2">
              <Engraved term="credits">{usd(cost.credits_usd)}</Engraved>
              <Engraved term="tagged project">{usd(cost.tagged_usd)}</Engraved>
              <Engraved term="untagged">{usd(cost.untagged_usd)}</Engraved>
              <Engraved term="checked">
                <Since iso={cost.generated_at} />
              </Engraved>
            </dl>
            <ul className="mt-6">
              {cost.services.map((row) => (
                <li
                  key={row.service}
                  className="flex items-baseline justify-between gap-6 border-t border-engrave py-2"
                >
                  <span className="text-sm text-lume">
                    {row.service}
                    {row.tagged_usd === 0 ? (
                      <span className="engraved ml-2 text-lume-dim">untagged</span>
                    ) : null}
                  </span>
                  <span className="figure text-sm text-lume">{usd(row.usage_usd)}</span>
                </li>
              ))}
            </ul>
            <ul className="mt-6">
              {cost.history.map((item) => (
                <li
                  key={item.month}
                  className="flex items-baseline justify-between gap-6 border-t border-engrave py-2"
                >
                  <span className="engraved">
                    {item.month}
                    {item.estimated ? " · open" : ""}
                  </span>
                  <span className="figure text-sm text-lume">{usd(item.usage_usd)}</span>
                </li>
              ))}
            </ul>
          </Plate>
        </div>

        <aside className="flex flex-col gap-10">
          <Plate title="Quarantine">
            {view.quarantine.length ? (
              <ul>
                {view.quarantine.map((item) => (
                  <li
                    key={`${item.table}-${item.reason}`}
                    className="flex items-baseline justify-between gap-4 border-t border-engrave py-2"
                  >
                    <span className="engraved normal-case tracking-normal">
                      {item.table} · {item.reason}
                      {item.explained ? "" : " · UNEXPLAINED"}
                    </span>
                    <span className="figure text-sm text-lume">{item.rows}</span>
                  </li>
                ))}
              </ul>
            ) : (
              <div className="cut-face flex h-16 items-center px-4">
                <span className="engraved text-lume-dim">no rows quarantined</span>
              </div>
            )}
          </Plate>

          <Plate title="Upstream budget" note="Token buckets, measured rather than assumed.">
            {view.quota.map((bucket) => (
              <div key={bucket.name} className="border-t border-engrave py-3">
                <div className="mb-2 flex items-baseline justify-between">
                  <span className="text-sm tracking-plate text-lume">{label(bucket.name)}</span>
                  <span className="figure text-sm text-lume">
                    {Math.floor(bucket.tokens_left)}/{bucket.capacity}
                  </span>
                </div>
                <div className="h-1 bg-engrave">
                  <div
                    className="h-1 bg-steel"
                    style={{ width: `${(bucket.tokens_left / bucket.capacity) * 100}%` }}
                  />
                </div>
                <p className="engraved mt-2 normal-case tracking-normal text-lume-dim">
                  refills {(bucket.refill_per_second * 3600).toFixed(0)} per hour
                </p>
              </div>
            ))}
          </Plate>

          <Plate title="Contract">
            <dl>
              <Engraved term="view">{view.view}</Engraved>
              <Engraved term="schema">{view.schema_version}</Engraved>
              <Engraved term="layer">{view.layer}</Engraved>
            </dl>
          </Plate>
        </aside>
      </div>
    </div>
  );
}
