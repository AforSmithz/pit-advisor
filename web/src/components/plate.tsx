export function Plate({
  title,
  note,
  children,
  footer,
}: {
  title: string;
  note?: string;
  children: React.ReactNode;
  footer?: React.ReactNode;
}) {
  return (
    <section className="border-t border-engrave-lit pt-3">
      <header className="mb-4 flex flex-col gap-2 sm:flex-row sm:items-baseline sm:justify-between sm:gap-10">
        <h2 className="engraved shrink-0 text-lume">{title}</h2>
        {note && (
          <p className="engraved max-w-[46ch] normal-case tracking-normal sm:text-left">{note}</p>
        )}
      </header>
      {children}
      {footer}
    </section>
  );
}

export function Engraved({ term, children }: { term: string; children: React.ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-4 border-t border-engrave py-1.5">
      <dt className="engraved">{term}</dt>
      <dd className="figure text-sm text-lume">{children}</dd>
    </div>
  );
}
