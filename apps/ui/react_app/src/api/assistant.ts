import type { DemoAnswer, GroundedAnswer, RetrievalReadiness, StructuredAnswer } from "../types";
import { requestJson } from "./client";

export function runPolicyQuery(
  question: string,
  accessToken: string
): Promise<GroundedAnswer> {
  return requestJson<GroundedAnswer>("/policy/query", accessToken, {
    method: "POST",
    body: JSON.stringify({ question, response_language: "auto" })
  });
}

export async function runPolicyQueryStream(
  question: string,
  accessToken: string,
  onStatus: (stage: string) => void,
  signal?: AbortSignal,
  chatId?: string
): Promise<GroundedAnswer> {
  const response = await fetch("/api/v1/policy/query", {
    method: "POST",
    credentials: "include",
    headers: {
      Accept: "text/event-stream",
      "Content-Type": "application/json",
      ...(accessToken ? { Authorization: `Bearer ${accessToken}` } : {})
    },
    body: JSON.stringify({ question, response_language: "auto", ...(chatId ? { chat_id: chatId } : {}) }),
    signal
  });
  if (!response.ok || response.body === null) {
    throw new Error(`Request failed with status ${response.status}`);
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { value, done } = await reader.read();
    buffer += decoder.decode(value, { stream: !done });
    let separator = buffer.indexOf("\n\n");
    while (separator >= 0) {
      const event = buffer.slice(0, separator);
      buffer = buffer.slice(separator + 2);
      const name = event.match(/^event: (.+)$/m)?.[1];
      const data = event.match(/^data: (.+)$/m)?.[1];
      if (name && data) {
        const payload = JSON.parse(data) as { stage?: string; detail?: string } | GroundedAnswer;
        if (name === "status" && "stage" in payload && typeof payload.stage === "string") onStatus(payload.stage);
        if (name === "error" && "detail" in payload) throw new Error(String(payload.detail));
        if (name === "answer") return payload as GroundedAnswer;
      }
      separator = buffer.indexOf("\n\n");
    }
    if (done) break;
  }
  throw new Error("The document answer stream ended before a validated answer was returned.");
}

export function runStructuredQuery(
  question: string,
  accessToken: string
): Promise<StructuredAnswer> {
  return requestJson<StructuredAnswer>("/query", accessToken, {
    method: "POST",
    body: JSON.stringify({ question, limit: 50 })
  });
}

export function runDemoQuery(question: string, limit = 5): Promise<DemoAnswer> {
  return requestJson<DemoAnswer>("/demo/query", "", {
    method: "POST",
    body: JSON.stringify({ question, limit })
  });
}

export function loadRetrievalReadiness(accessToken: string): Promise<RetrievalReadiness> {
  return requestJson<RetrievalReadiness>("/retrieval/readiness", accessToken);
}
