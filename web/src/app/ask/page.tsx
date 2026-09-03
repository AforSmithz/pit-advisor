import { Console } from "./console";

export const metadata = { title: "Ask · Pit Advisor" };

export default function AskPage() {
  return (
    <div className="pt-2">
      <div className="flex flex-wrap items-end justify-between gap-x-10 gap-y-4 border-b border-engrave-lit pb-5">
        <div>
          <h1 className="legend text-4xl text-lume sm:text-5xl">Ask</h1>
          <p className="engraved mt-2">grounded in tools, never in the model</p>
        </div>
        <p className="engraved max-w-[46ch] normal-case tracking-normal">
          Every figure in an answer is quoted from a tool result. A draft that states a number
          no tool returned is withheld rather than shown, and the tools it called are listed
          under each answer so the claim can be checked.
        </p>
      </div>
      <Console />
      <p className="engraved mt-10 max-w-[68ch] border-t border-engrave pt-2 normal-case tracking-normal">
        This runs on a fixed credit budget, so the page answers twenty questions a day and then
        stops until the next one. The cap is deliberate: an open endpoint in front of a model is
        a bill somebody else can run up.
      </p>
    </div>
  );
}
