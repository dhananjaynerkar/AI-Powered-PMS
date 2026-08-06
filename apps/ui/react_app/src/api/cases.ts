import type { CaseMessage, CaseRecord, CaseTimeline, LocalLoginRole, StaffRecipient } from "../types";
import { requestJson } from "./client";

export function loadCaseQueue(accessToken: string): Promise<CaseRecord[]> {
  return requestJson<CaseRecord[]>("/cases", accessToken);
}

export function loadTimeline(
  caseId: string,
  accessToken: string
): Promise<CaseTimeline> {
  return requestJson<CaseTimeline>(`/cases/${caseId}/timeline`, accessToken);
}

export function postCaseMessage(
  caseId: string,
  body: string,
  accessToken: string,
  evidence: Array<{ reference_type: string; reference_id: string; version: string | null }> = []
): Promise<CaseMessage> {
  return requestJson<CaseMessage>(`/cases/${caseId}/messages`, accessToken, {
    method: "POST",
    body: JSON.stringify({ body, evidence, artifacts: [] })
  });
}

export function createCase(
  payload: { title: string; objective: string; initial_message: string; unit_id: string },
  accessToken: string
): Promise<CaseRecord> {
  return requestJson<CaseRecord>("/cases", accessToken, {
    method: "POST",
    body: JSON.stringify(payload)
  });
}

export function loadCaseRecipients(
  role: LocalLoginRole,
  accessToken: string
): Promise<StaffRecipient[]> {
  return requestJson<StaffRecipient[]>(`/case-recipients?role=${encodeURIComponent(role)}`, accessToken);
}

export function submitToNo(
  caseId: string,
  accessToken: string,
  assignedSubject: string,
  remarks: string
): Promise<CaseRecord> {
  return requestJson<CaseRecord>(`/cases/${caseId}/submit-to-no`, accessToken, {
    method: "POST",
    body: JSON.stringify({ assigned_subject: assignedSubject, remarks })
  });
}

export function verifyCase(caseId: string, accessToken: string): Promise<CaseRecord> {
  return requestJson<CaseRecord>(`/cases/${caseId}/verify`, accessToken, {
    method: "POST",
    body: JSON.stringify({ remarks: "NO verified the controlled structured and cited document evidence." })
  });
}

export function submitToHod(
  caseId: string,
  accessToken: string,
  assignedSubject: string,
  remarks: string
): Promise<CaseRecord> {
  return requestJson<CaseRecord>(`/cases/${caseId}/submit-to-hod`, accessToken, {
    method: "POST",
    body: JSON.stringify({ assigned_subject: assignedSubject, remarks })
  });
}
