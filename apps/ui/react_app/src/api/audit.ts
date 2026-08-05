import type { AuditEvent } from "../types";
import { requestJson } from "./client";

export function loadAudit(accessToken: string): Promise<AuditEvent[]> {
  return requestJson<AuditEvent[]>("/audit/my-queries?limit=50", accessToken);
}
