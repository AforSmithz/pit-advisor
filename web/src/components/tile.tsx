export function PageHead({ title, sub, children }: { title: string; sub?: string; children?: React.ReactNode }) {
  return (
    <header>
      <h1 className="mt-2 ml-1.5 text-[34px] leading-[1.05] font-extralight tracking-[-0.02em] lg:text-[clamp(36px,4.2vw,60px)]">
        {title}
      </h1>
      {sub && <p className="mt-2 ml-2 max-w-[52ch] text-sm leading-snug text-ink-2">{sub}</p>}
      {children && <div className="mt-3.5 ml-1 flex flex-wrap items-center gap-2.5">{children}</div>}
    </header>
  );
}

export function Toggle<T extends string>({
  label,
  options,
  value,
  onChange,
}: {
  label: string;
  options: readonly (readonly [T, string])[];
  value: T;
  onChange: (value: T) => void;
}) {
  return (
    <div role="group" aria-label={label} className="pane-well flex gap-1 rounded-full p-1">
      {options.map(([key, text]) => {
        const on = key === value;
        return (
          <button
            key={key}
            type="button"
            aria-pressed={on}
            onClick={() => onChange(key)}
            className={`press min-h-8 rounded-full px-3 text-xs font-medium ${on ? "pane-on" : "text-ink-3 hover:text-ink"}`}
          >
            {text}
          </button>
        );
      })}
    </div>
  );
}
