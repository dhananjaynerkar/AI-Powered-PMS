import type {
  AuditEvent,
  CaseMessage,
  CaseRecord,
  CaseTimeline,
  DemoIdentity,
  GroundedAnswer,
  DemoAnswer,
  DemoStatus,
  LocalAuthStatus,
  LocalLoginRole,
  Me,
  StructuredAnswer
} from "./types";

const API_ROOT = "/api/v1";

export class ApiError extends Error {
  constructor(
    message: string,
    public readonly status: number
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function requestJson<T>(
  path: string,
  accessToken: string,
  init?: RequestInit
): Promise<T> {
  const response = await fetch(`${API_ROOT}${path}`, {
    ...init,
    credentials: "include",
    headers: {
      ...(accessToken ? { Authorization: `Bearer ${accessToken}` } : {}),
      ...(init?.body ? { "Content-Type": "application/json" } : {}),
      ...init?.headers
    }
  });
  if (!response.ok) {
    let detail = `Request failed with status ${response.status}`;
    try {
      const payload = (await response.json()) as { detail?: string };
      if (payload.detail) detail = payload.detail;
    } catch {
      // Keep the status-based message when the server did not return JSON.
    }
    throw new ApiError(detail, response.status);
  }
  return (await response.json()) as T;
}

export function startLogin(portal: "staff" | "tenant"): void {
  window.location.assign(`/auth/login?portal=${portal}`);
}

export function logout(): void {
  window.location.assign("/auth/logout");
}

export async function loadLocalAuthStatus(): Promise<LocalAuthStatus> {
  const response = await fetch("/auth/local/status", { credentials: "include" });
  if (!response.ok) throw new ApiError(`Request failed with status ${response.status}`, response.status);
  return (await response.json()) as LocalAuthStatus;
}

export async function loginLocally(
  username: string,
  password: string,
  role: LocalLoginRole
): Promise<Me> {
  const response = await fetch("/auth/local/login", {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password, role })
  });
  if (!response.ok) {
    let detail = "Local login failed";
    try {
      const payload = (await response.json()) as { detail?: string };
      if (payload.detail) detail = payload.detail;
    } catch {
      // Keep the generic login failure.
    }
    throw new ApiError(detail, response.status);
  }
  return (await response.json()) as Me;
}

export function loadMe(accessToken: string): Promise<Me> {
  return requestJson<Me>("/me", accessToken);
}

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

export function runPolicyQuery(
  question: string,
  accessToken: string
): Promise<GroundedAnswer> {
  return requestJson<GroundedAnswer>("/policy/query", accessToken, {
    method: "POST",
    body: JSON.stringify({ question, response_language: "auto" })
  });
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

export function loadAudit(accessToken: string): Promise<AuditEvent[]> {
  return requestJson<AuditEvent[]>("/audit/my-queries?limit=50", accessToken);
}

export function loadDemoStatus(): Promise<DemoStatus> {
  return requestJson<DemoStatus>("/demo/status", "");
}

export function loadDemoMe(): Promise<Me> {
  return requestJson<Me>("/demo/me", "");
}

export function startDemoSession(identity: DemoIdentity): Promise<Me> {
  return requestJson<Me>("/demo/session", "", {
    method: "POST",
    body: JSON.stringify({ identity })
  }).then(() => loadDemoMe());
}

export function endDemoSession(): Promise<void> {
  return fetch(`${API_ROOT}/demo/session`, { method: "DELETE", credentials: "include" }).then(
    (response) => {
      if (!response.ok) throw new ApiError(`Request failed with status ${response.status}`, response.status);
    }
  );
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

export function submitToNo(caseId: string, accessToken: string): Promise<CaseRecord> {
  return requestJson<CaseRecord>(`/cases/${caseId}/submit-to-no`, accessToken, {
    method: "POST",
    body: JSON.stringify({ assigned_subject: "demo.no", remarks: "Controlled local evidence package forwarded for NO review." })
  });
}

export function verifyCase(caseId: string, accessToken: string): Promise<CaseRecord> {
  return requestJson<CaseRecord>(`/cases/${caseId}/verify`, accessToken, {
    method: "POST",
    body: JSON.stringify({ remarks: "NO verified the controlled structured and cited document evidence." })
  });
}

export function submitToHod(caseId: string, accessToken: string): Promise<CaseRecord> {
  return requestJson<CaseRecord>(`/cases/${caseId}/submit-to-hod`, accessToken, {
    method: "POST",
    body: JSON.stringify({ assigned_subject: "demo.hod", remarks: "NO forwarded the verified controlled evidence package to HOD." })
  });
}

export function runDemoQuery(question: string, limit = 5): Promise<DemoAnswer> {
  return requestJson<DemoAnswer>("/demo/query", "", {
    method: "POST",
    body: JSON.stringify({ question, limit })
  });
}
