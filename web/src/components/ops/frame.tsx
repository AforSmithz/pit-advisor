import { Intro } from "./intro";

// every page keeps the circuit clear on the left: header top left, one wide strip bottom left, cards in the right column
export function Frame({
  head,
  strip,
  children,
}: {
  head: React.ReactNode;
  strip?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <div data-ops-intro className="min-h-dvh">
      <div className="pointer-events-none relative grid gap-4 p-4 lg:fixed lg:inset-0 lg:grid-cols-[minmax(0,1fr)_540px] lg:p-5 lg:pr-[112px] lg:pb-[30px]">
        <section className="flex min-h-[calc(56dvh-32px)] min-w-0 flex-col justify-between gap-4 lg:min-h-0">
          <div className="pointer-events-auto">{head}</div>
          {strip && (
            <div data-ops-strip className="pointer-events-auto lg:mr-2">
              {strip}
            </div>
          )}
        </section>
        <section data-ops-panels className="pointer-events-auto flex min-h-0 flex-col gap-3 pb-1 lg:overflow-y-auto">
          {children}
        </section>
      </div>
      <Intro />
    </div>
  );
}
