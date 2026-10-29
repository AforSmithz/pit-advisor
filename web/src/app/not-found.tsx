import Link from "next/link";

export default function NotFound() {
  return (
    <section data-split className="pane flex flex-col items-start gap-4 rounded-3xl px-7 py-8">
      <h1 className="display text-[40px]">No such page</h1>
      <p className="max-w-prose text-sm text-ink-3">
        The site only carries what the pipeline published for the current weekend. A driver who has not raced in the
        lake, or a circuit off this weekend, has no page.
      </p>
      <Link href="/" className="pane-on press rounded-full px-5 py-2.5 text-[13px] font-semibold">
        Back to race week
      </Link>
    </section>
  );
}
