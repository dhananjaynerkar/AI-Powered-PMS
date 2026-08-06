import type { ChatResponse, ChatSummary, ChatType } from "../types";
import { requestJson } from "./client";

export function createChat(
  payload: { title?: string; chat_type?: ChatType; case_id?: string | null },
  accessToken: string
): Promise<ChatResponse> {
  return requestJson<ChatResponse>("/assistant/chats", accessToken, {
    method: "POST",
    body: JSON.stringify(payload)
  });
}

export function loadChats(accessToken: string, includeArchived = false): Promise<ChatSummary[]> {
  const suffix = includeArchived ? "?include_archived=true" : "";
  return requestJson<ChatSummary[]>(`/assistant/chats${suffix}`, accessToken);
}

export function loadChat(chatId: string, accessToken: string): Promise<ChatResponse> {
  return requestJson<ChatResponse>(`/assistant/chats/${encodeURIComponent(chatId)}`, accessToken);
}

export function updateChat(
  chatId: string,
  payload: { title?: string; status?: string; first_question?: string },
  accessToken: string
): Promise<ChatResponse> {
  return requestJson<ChatResponse>(`/assistant/chats/${encodeURIComponent(chatId)}`, accessToken, {
    method: "PATCH",
    body: JSON.stringify(payload)
  });
}

export function archiveChat(chatId: string, accessToken: string): Promise<void> {
  return requestJson<void>(`/assistant/chats/${encodeURIComponent(chatId)}`, accessToken, { method: "DELETE" });
}
