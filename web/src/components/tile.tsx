export function Tile({
  title,
  note,
  action,
  children,
  className = "",
  glass = false,
}: {
  title: string;
  note?: string;
  action?: React.ReactNode;
  children: React.ReactNode;
  className?: string;
  glass?: boolean;
}) {
  return (
    <section
      className={`${glass ? "glass" : "raised"} flex min-w-0 flex-col gap-3 rounded-[30px] px-5 pt-4 pb-4 ${className}`}
    >
      <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1">
        <h2 className="display text-[18px]">{title}</h2>
        {action ?? (note && <span className="text-[11.5px] text-mute">{note}</span>)}
      </div>
      {children}
    </section>
  );
}

export function PageHead({
  title,
  sub,
  children,
}: {
  title: string;
  sub: string;
  children?: React.ReactNode;
}) {
  return (
    <header className="flex min-h-[52px] flex-wrap items-center justify-between gap-3 px-1">
      <div>
        <h1 className="display text-[28px]">{title}</h1>
        <p className="mt-1 text-[12.5px] text-ink-3">{sub}</p>
      </div>
      {children}
    </header>
  );
}

export function Toggle<T extends string>({
  label,
  options,
  value,
  onChange,
  tone = "warm",
}: {
  label: string;
  options: readonly (readonly [T, string])[];
  value: T;
  onChange: (value: T) => void;
  tone?: "warm" | "cool";
}) {
  return (
    <div role="group" aria-label={label} className="inset-sm flex gap-1 rounded-full p-1">
      {options.map(([key, text]) => {
        const on = key === value;
        return (
          <button
            key={key}
            type="button"
            aria-pressed={on}
            onClick={() => onChange(key)}
            className={`press min-h-8 rounded-full px-3 text-xs font-medium ${
              on ? (tone === "warm" ? "pill-on" : "pill-cool") : "text-ink-3 hover:text-ink"
            }`}
          >
            {text}
          </button>
        );
      })}
    </div>
  );
}
