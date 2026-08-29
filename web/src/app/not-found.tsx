import Link from "next/link";

export default function NotFound() {
  return (
    <div className="pt-2">
      <h1 className="legend text-4xl text-lume sm:text-5xl">No such reading</h1>
      <p className="mt-4 max-w-prose text-lume-dim">
        This dial only carries what the backend emitted for the current event.
      </p>
      <Link href="/" className="engraved mt-8 inline-block text-lume hover:text-split-lit">
        back to the weekend
      </Link>
    </div>
  );
}
