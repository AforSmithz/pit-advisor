import { Plate } from "./plate";

export function NotBuilt({
  title,
  what,
  produces,
  gate,
}: {
  title: string;
  what: string;
  produces: string;
  gate: string;
}) {
  return (
    <Plate title={title}>
      <div className="cut-face px-6 py-10">
        <p className="max-w-prose text-lume">{what}</p>
      </div>
      <dl className="mt-8 max-w-prose">
        <div className="border-t border-engrave py-3">
          <dt className="engraved mb-1">what will fill it</dt>
          <dd className="text-sm text-lume-dim">{produces}</dd>
        </div>
        <div className="border-t border-engrave py-3">
          <dt className="engraved mb-1">what it has to pass first</dt>
          <dd className="text-sm text-lume-dim">{gate}</dd>
        </div>
      </dl>
    </Plate>
  );
}
