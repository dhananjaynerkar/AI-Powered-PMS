import type { ChatAttachment, DemoAnswer, GroundedAnswer, RetrievalReadiness, StructuredAnswer } from "../types";
import { API_ROOT, requestJson } from "./client";

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
  chatId?: string,
  idempotencyKey?: string,
  onToken?: (delta: string) => void,
  onCitation?: (sourceId: string, pageNumbers: number[]) => void,
  attachmentIds: string[] = []
): Promise<GroundedAnswer> {
  const response = await fetch("/api/v1/policy/query", {
    method: "POST",
    credentials: "include",
    headers: {
      Accept: "text/event-stream",
      "Content-Type": "application/json",
      ...(accessToken ? { Authorization: `Bearer ${accessToken}` } : {})
    },
    body: JSON.stringify({
      question,
      response_language: "auto",
      ...(chatId ? { chat_id: chatId } : {}),
      ...(idempotencyKey ? { idempotency_key: idempotencyKey } : {})
      ,...(attachmentIds.length ? { attachment_ids: attachmentIds } : {})
    }),
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
        const payload = JSON.parse(data) as {
          stage?: string;
          detail?: string;
          code?: string;
          message?: string;
          delta?: string;
          source_id?: string;
          page_numbers?: number[];
        } | GroundedAnswer;
        if (name === "status" && "stage" in payload && typeof payload.stage === "string") onStatus(payload.stage);
        if (name === "token" && "delta" in payload && typeof payload.delta === "string") onToken?.(payload.delta);
        if (name === "citation" && "source_id" in payload && typeof payload.source_id === "string") {
          onCitation?.(payload.source_id, Array.isArray(payload.page_numbers) ? payload.page_numbers : []);
        }
        if (name === "error") {
          const message = "message" in payload ? payload.message : "detail" in payload ? payload.detail : undefined;
          throw new Error(String(message ?? "The assistant request failed."));
        }
        if (name === "final" || name === "answer") return payload as GroundedAnswer;
      }
      separator = buffer.indexOf("\n\n");
    }
    if (done) break;
  }
  throw new Error("The document answer stream ended before a validated answer was returned.");
}

export function uploadChatAttachment(
  chatId: string,
  file: File,
  accessToken: string,
  onProgress: (percent: number) => void
): Promise<ChatAttachment> {
  return new Promise((resolve, reject) => {
    const request = new XMLHttpRequest();
    request.open("POST", `${API_ROOT}/assistant/chats/${encodeURIComponent(chatId)}/attachments`);
    request.withCredentials = true;
    if (accessToken) request.setRequestHeader("Authorization", `Bearer ${accessToken}`);
    request.upload.onprogress = (event) => {
      if (event.lengthComputable) onProgress(Math.round((event.loaded / event.total) * 100));
    };
    request.onerror = () => reject(new Error("Attachment upload failed."));
    request.onload = () => {
      try {
        const payload = JSON.parse(request.responseText) as ChatAttachment & { detail?: unknown };
        if (request.status < 200 || request.status >= 300) {
          reject(new Error(typeof payload.detail === "string" ? payload.detail : `Request failed with status ${request.status}`));
          return;
        }
        resolve(payload);
      } catch {
        reject(new Error("The attachment response was invalid."));
      }
    };
    const body = new FormData();
    body.append("file", file, file.name);
    request.send(body);
  });
}

export function removeChatAttachment(chatId: string, attachmentId: string, accessToken: string): Promise<void> {
  return requestJson<void>(`/assistant/chats/${encodeURIComponent(chatId)}/attachments/${encodeURIComponent(attachmentId)}`, accessToken, { method: "DELETE" });
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
