import type { DocumentMetadata, DocumentUploadResult } from "../types";
import { API_ROOT, ApiError, requestJson } from "./client";

export function loadDocument(
  documentId: string,
  accessToken: string
): Promise<DocumentMetadata> {
  return requestJson<DocumentMetadata>(`/documents/${encodeURIComponent(documentId)}`, accessToken);
}

export async function uploadDocument(
  payload: {
    file: File;
    title: string;
    documentId?: string;
    classification: string;
  },
  accessToken: string
): Promise<DocumentUploadResult> {
  const form = new FormData();
  form.append("file", payload.file);
  form.append("title", payload.title);
  if (payload.documentId) form.append("document_id", payload.documentId);
  form.append("classification", payload.classification);
  const response = await fetch(`${API_ROOT}/documents`, {
    method: "POST",
    credentials: "include",
    headers: accessToken ? { Authorization: `Bearer ${accessToken}` } : undefined,
    body: form
  });
  if (!response.ok) {
    let detail = `Request failed with status ${response.status}`;
    try {
      const body = (await response.json()) as { detail?: string };
      if (body.detail) detail = body.detail;
    } catch {
      // Keep the status-based message when the server did not return JSON.
    }
    throw new ApiError(detail, response.status);
  }
  return (await response.json()) as DocumentUploadResult;
}
