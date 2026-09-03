"use client";

import { useRef, useState } from "react";
import { Plate } from "@/components/plate";
import { AskError, ask, type Answer } from "@/lib/ask";

const EXAMPLES = [
  "What is VER's form rating going into the next race, and how wide is the interval?",
  "How many stewards' rulings cite the International Sporting Code?",
  "Under Article 33.4, what penalties have the stewards handed out before?",
  "How did the forecast score against the baselines on the holdout?",
];

export function Console() {
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState<Answer | null>(null);
  const [failure, setFailure] = useState<AskError | null>(null);
  const [asking, setAsking] = useState(false);
  const field = useRef<HTMLTextAreaElement>(null);

  async function submit(text: string) {
    const asked = text.trim();
    if (!asked || asking) return;
    setAsking(true);
    setFailure(null);
    setAnswer(null);
    try {
      setAnswer(await ask(asked));
    } catch (error) {
      setFailure(
        error instanceof AskError ? error : new AskError("the agent could not be reached"),
      );
    } finally {
      setAsking(false);
    }
  }

  return (
    <div className="mt-8 flex flex-col gap-8">
      <form
        onSubmit={(event) => {
          event.preventDefault();
          void submit(question);
        }}
      >
        <label htmlFor="question" className="engraved block text-lume">
          ask
        </label>
        <textarea
          id="question"
          ref={field}
          rows={2}
          value={question}
          maxLength={500}
          onChange={(event) => setQuestion(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey) {
              event.preventDefault();
              void submit(question);
            }
          }}
          placeholder="a question about form, pace, the forecast, the regulations or the stewards"
          className="mt-2 w-full resize-none border-0 border-b border-engrave-lit bg-transparent py-2 text-lume outline-none placeholder:text-steel focus:border-split"
        />
        <div className="mt-3 flex flex-wrap items-baseline justify-between gap-x-8 gap-y-2">
          <p className="engraved normal-case tracking-normal">
            enter to ask, shift-enter for a new line
          </p>
          <button
            type="submit"
            disabled={asking || question.trim() === ""}
            className="engraved border-b border-split pb-0.5 text-lume disabled:border-engrave disabled:text-steel"
          >
            {asking ? "asking" : "ask"}
          </button>
        </div>
      </form>

      <div className="flex flex-wrap gap-x-6 gap-y-1">
        {EXAMPLES.map((example) => (
          <button
            key={example}
            type="button"
            onClick={() => {
              setQuestion(example);
              field.current?.focus();
              void submit(example);
            }}
            className="engraved normal-case tracking-normal text-lume-dim hover:text-split-lit"
          >
            {example}
          </button>
        ))}
      </div>

      {failure && (
        <Plate title="not answered">
          <p className="max-w-[62ch] text-lume">{failure.message}</p>
          {failure.resetsAt && (
            <p className="engraved mt-2 normal-case tracking-normal">
              the count resets at {failure.resetsAt.replace("T", " ").slice(0, 16)} UTC
            </p>
          )}
        </Plate>
      )}

      {answer && <Rendered answer={answer} />}
    </div>
  );
}

function Rendered({ answer }: { answer: Answer }) {
  return (
    <div className="flex flex-col gap-8">
      <Plate
        title={answer.refused ? "declined" : "answer"}
        note={
          answer.ungrounded.length > 0
            ? "a figure in the draft was not in any tool result, so the answer was withheld"
            : undefined
        }
      >
        <p className="max-w-[68ch] whitespace-pre-wrap text-lume">{answer.text}</p>
      </Plate>

      <Plate
        title="where every figure came from"
        note="the model calls these and quotes what comes back. it computes nothing itself."
      >
        {answer.calls.length === 0 ? (
          <p className="engraved normal-case tracking-normal">
            no tool was called, so this answer states no figure
          </p>
        ) : (
          <ol className="flex flex-col">
            {answer.calls.map((call, index) => (
              <li
                key={`${call.name}-${index}`}
                className="flex flex-wrap items-baseline justify-between gap-x-6 gap-y-1 border-t border-engrave py-1.5"
              >
                <span className="figure text-sm text-lume">{call.name}</span>
                <span className="engraved normal-case tracking-normal">
                  {Object.entries(call.arguments)
                    .map(([key, value]) => `${key}: ${String(value)}`)
                    .join(" · ") || "no arguments"}
                </span>
                <span
                  className={`engraved ${call.ok ? "text-lume-dim" : "text-split-lit"}`}
                >
                  {call.ok ? "answered" : call.detail || "failed"}
                </span>
              </li>
            ))}
          </ol>
        )}
      </Plate>

      {answer.citations.length > 0 && (
        <Plate title="cited">
          <ul className="flex flex-col">
            {answer.citations.map((citation) => (
              <li
                key={citation}
                className="figure border-t border-engrave py-1.5 text-[0.7rem] break-all text-steel"
              >
                {citation}
              </li>
            ))}
          </ul>
        </Plate>
      )}

      <footer className="flex flex-wrap items-baseline gap-x-6 gap-y-1 border-t border-engrave pt-2">
        <span className="engraved">
          {answer.iterations} {answer.iterations === 1 ? "turn" : "turns"} · stopped on{" "}
          {answer.stop_reason}
        </span>
        <span className="figure text-[0.625rem] tracking-wide text-steel">
          {answer.usage.totalTokens ?? 0} tokens
        </span>
        {answer.budget && (
          <span className="figure text-[0.625rem] tracking-wide text-steel">
            {answer.budget.used} of {answer.budget.limit} questions today
          </span>
        )}
      </footer>
    </div>
  );
}
