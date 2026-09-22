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

// cloudfront signs the request to the function url, and for a POST it can only do that when
// the viewer supplies the payload hash. without it lambda answers 403
export async function payloadHash(body: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(body));
  return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join("");
}

/** The backend owns every figure, so this parses and never computes. */
export async function ask(question: string, signal?: AbortSignal): Promise<Answer> {
  const body = JSON.stringify({ question });
  const response = await fetch("/api/ask", {
    method: "POST",
    headers: {
      "content-type": "application/json",
      "x-amz-content-sha256": await payloadHash(body),
    },
    body,
    signal,
  });
  const reply: unknown = await response.json().catch(() => null);
  if (!response.ok) {
    const parsed = refusal.safeParse(reply);
    if (parsed.success) {
      throw new AskError(parsed.data.error, parsed.data.resets_at);
    }
    throw new AskError(`the agent answered ${response.status}`);
  }
  return answer.parse(reply);
}
