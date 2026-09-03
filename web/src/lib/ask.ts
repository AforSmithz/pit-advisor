import { z } from "zod";

const toolCall = z
  .object({
    name: z.string(),
    arguments: z.record(z.string(), z.unknown()),
    ok: z.boolean(),
    detail: z.string().default(""),
  })
  .strict();

export const answer = z
  .object({
    text: z.string(),
    question: z.string(),
    calls: z.array(toolCall),
    stop_reason: z.string(),
    iterations: z.number(),
    usage: z.record(z.string(), z.number()),
    ungrounded: z.array(z.string()).default([]),
    citations: z.array(z.string()).default([]),
    refused: z.boolean().default(false),
    budget: z.object({ used: z.number(), limit: z.number() }).optional(),
  })
  .strict();

export const refusal = z
  .object({
    error: z.string(),
    limit: z.number().optional(),
    resets_at: z.string().optional(),
  })
  .strict();

export type Answer = z.infer<typeof answer>;
export type ToolCall = z.infer<typeof toolCall>;
export type Refusal = z.infer<typeof refusal>;

export class AskError extends Error {
  constructor(
    message: string,
    readonly resetsAt?: string,
  ) {
    super(message);
  }
}

/** The backend owns every figure, so this parses and never computes. */
export async function ask(question: string, signal?: AbortSignal): Promise<Answer> {
  const response = await fetch("/api/ask", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ question }),
    signal,
  });
  const body: unknown = await response.json().catch(() => null);
  if (!response.ok) {
    const parsed = refusal.safeParse(body);
    if (parsed.success) {
      throw new AskError(parsed.data.error, parsed.data.resets_at);
    }
    throw new AskError(`the agent answered ${response.status}`);
  }
  return answer.parse(body);
}
