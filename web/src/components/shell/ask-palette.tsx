"use client";

import { useEffect, useRef, useState } from "react";
import { AskError, ask, type Answer } from "@/lib/ask";

const EXAMPLES = [
  "Why did the favourite change after qualifying?",
  "What penalties have the stewards given for causing a collision?",
  "How sure is the model about Norris this weekend?",
  "How did the forecast score against the grid on the holdout?",
];

// figures in the answer are quoted from tool results; marking them makes that visible
function marked(text: string): React.ReactNode[] {
  return text.split(/(\d[\d.,]*%?)/g).map((part, index) =>
    /^\d/.test(part) ? (
      <span key={index} className="figure rounded-md bg-[rgba(79,125,255,0.14)] px-1.5 text-sky">
        {part}
      </span>
    ) : (
      part
    ),
  );
}

export function AskPalette({ onClose }: { onClose: () => void }) {
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState<Answer | null>(null);
  const [failure, setFailure] = useState<AskError | null>(null);
  const [asking, setAsking] = useState(false);
  const field = useRef<HTMLInputElement>(null);

  useEffect(() => field.current?.focus(), []);

  async function submit(text: string) {
    const asked = text.trim();
    if (!asked || asking) return;
    setQuestion(asked);
    setAsking(true);
    setFailure(null);
    setAnswer(null);
    try {
      setAnswer(await ask(asked));
    } catch (error) {
      setFailure(error instanceof AskError ? error : new AskError("the agent could not be reached"));
    } finally {
      setAsking(false);
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center bg-black/60 px-4 pt-[10vh]" onClick={onClose}>
      <div
        role="dialog"
        aria-label="Ask Pit Advisor"
        onClick={(event) => event.stopPropagation()}
        className="glass flex w-full max-w-[640px] flex-col gap-4 rounded-[30px] p-5"
        style={{ background: "rgb(24 24 28 / 0.8)" }}
      >
        <form
          className="inset flex items-center gap-3 rounded-[18px] px-4 py-3.5"
          onSubmit={(event) => {
            event.preventDefault();
            void submit(question);
          }}
        >
          <svg width="18" height="18" viewBox="0 0 18 18" aria-hidden="true">
            <circle cx="8" cy="8" r="5.5" stroke="#2ee6c5" strokeWidth="1.6" fill="none" />
            <path d="M12.2 12.2l3.3 3.3" stroke="#2ee6c5" strokeWidth="1.6" strokeLinecap="round" />
          </svg>
          <label htmlFor="ask" className="sr-only">
            Ask a question
          </label>
          <input
            id="ask"
            ref={field}
            value={question}
            maxLength={500}
            onChange={(event) => setQuestion(event.target.value)}
            placeholder="Form, pace, the forecast, the regulations, the stewards"
            className="min-w-0 flex-1 bg-transparent text-[15px] text-ink outline-none placeholder:text-mute"
          />
          <kbd className="raised-sm figure rounded-md px-2 py-1 text-[11px] text-ink-3">esc</kbd>
        </form>

        {asking && <p className="px-1 text-sm text-ink-3">Asking the tools…</p>}

        {failure && (
          <div className="inset-sm rounded-2xl px-4 py-3 text-sm">
            <p>{failure.message}</p>
            {failure.resetsAt && (
              <p className="mt-1 text-xs text-mute">
                The daily count resets at {failure.resetsAt.replace("T", " ").slice(0, 16)} UTC.
              </p>
            )}
          </div>
        )}

        {answer && (
          <div className="flex flex-col gap-3">
            <div className="flex flex-wrap gap-2">
              {answer.calls.map((call, index) => (
                <span
                  key={`${call.name}-${index}`}
                  className="inset-sm figure flex items-center gap-1.5 rounded-full px-2.5 py-1.5 text-[11px]"
                  style={{ color: call.ok ? "#2ee6c5" : "#ff5f6d" }}
                >
                  <span className="h-1.5 w-1.5 rounded-full" style={{ background: call.ok ? "#2ee6c5" : "#ff5f6d" }} />
                  {call.name}
                </span>
              ))}
            </div>
            <p className="text-[15px] leading-relaxed">
              {answer.ungrounded.length > 0
                ? "A figure in the draft answer was not in any tool result, so the answer was withheld."
                : marked(answer.text)}
            </p>
            {answer.ungrounded.length === 0 && !answer.refused && (
              <span className="flex items-center gap-2 text-xs text-aqua">
                <svg width="14" height="14" viewBox="0 0 14 14" aria-hidden="true">
                  <path d="M3 7.2l2.6 2.6L11 4.4" stroke="#2ee6c5" strokeWidth="1.8" fill="none" strokeLinecap="round" />
                </svg>
                Every figure matched a tool result
              </span>
            )}
          </div>
        )}

        {!answer && !asking && (
          <div className="flex flex-wrap gap-2">
            {EXAMPLES.map((example) => (
              <button
                key={example}
                type="button"
                onClick={() => void submit(example)}
                className="raised-sm press min-h-10 rounded-full px-3.5 text-[12.5px]"
              >
                {example}
              </button>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
