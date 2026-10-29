"use client";

import { useState } from "react";

export function ShareSheet({
  onClose,
  initial,
  title,
  race,
}: {
  onClose: () => void;
  initial: "share" | "follow";
  title: string;
  race: string;
}) {
  const [tab, setTab] = useState(initial);
  const [copied, setCopied] = useState<string | null>(null);

  async function copy(what: string, text: string) {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(what);
    } catch {
      setCopied(null);
    }
  }

  const origin = typeof window === "undefined" ? "" : window.location.origin;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 px-4" onClick={onClose}>
      <div
        role="dialog"
        aria-label="Share and follow"
        onClick={(event) => event.stopPropagation()}
        className="pane flex w-full max-w-[720px] flex-col gap-5 rounded-[34px] p-6"
        style={{ background: "rgb(24 24 28 / 0.8)" }}
      >
        <div className="flex items-center justify-between gap-4">
          <div role="tablist" aria-label="Share or follow" className="pane-well flex gap-1 rounded-full p-1">
            {(["share", "follow"] as const).map((name) => (
              <button
                key={name}
                role="tab"
                type="button"
                aria-selected={tab === name}
                onClick={() => setTab(name)}
                className={`press min-h-9 rounded-full px-4 text-[13px] font-medium capitalize ${
                  tab === name ? "pane-on" : "text-ink-3"
                }`}
              >
                {name}
              </button>
            ))}
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            className="pane press h-10 w-10 rounded-full text-lg text-ink-3"
          >
            ×
          </button>
        </div>

        {tab === "share" ? (
          <>
            <div className="pane-well overflow-hidden rounded-3xl p-2.5">
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img
                src="/share.png"
                alt={`Share image: ${title}`}
                width={1200}
                height={630}
                className="h-auto w-full rounded-2xl"
              />
            </div>
            <p className="text-sm text-ink-3">{title}</p>
            <div className="grid grid-cols-1 gap-2.5 sm:grid-cols-3">
              <button
                type="button"
                onClick={() => void copy("link", origin)}
                className="pane press min-h-12 rounded-2xl text-[13px] font-medium"
              >
                {copied === "link" ? "Link copied" : "Copy link"}
              </button>
              <a
                href="/share.png"
                download={`pit-advisor-${race}.png`}
                className="pane press flex min-h-12 items-center justify-center rounded-2xl text-[13px] font-medium"
              >
                Save image
              </a>
              <a
                href={`https://bsky.app/intent/compose?text=${encodeURIComponent(`${title} ${origin}`)}`}
                target="_blank"
                rel="noreferrer"
                className="pane-on press flex min-h-12 items-center justify-center rounded-2xl text-[13px] font-semibold"
              >
                Post it
              </a>
            </div>
          </>
        ) : (
          <>
            <p className="text-sm leading-relaxed text-ink-3">
              Subscribe once. The calendar carries the coming race, and the sprint when there is one,
              and moves on to the next weekend by itself. The feed gets a new item each time a
              forecast is published. Both are plain files next to the site, so there is no account
              and nothing to sign up for.
            </p>
            <div className="grid grid-cols-1 gap-2.5 sm:grid-cols-2">
              <a
                href="/calendar.ics"
                className="pane-on press flex min-h-12 items-center justify-center rounded-2xl text-[13px] font-semibold"
              >
                Add to calendar
              </a>
              <button
                type="button"
                onClick={() => void copy("feed", `${origin}/feed.xml`)}
                className="pane press min-h-12 rounded-2xl text-[13px] font-medium"
              >
                {copied === "feed" ? "Feed address copied" : "Copy RSS feed"}
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
