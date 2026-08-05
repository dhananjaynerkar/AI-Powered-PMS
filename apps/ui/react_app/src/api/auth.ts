import type { DemoIdentity, DemoStatus, LocalAuthStatus, LocalLoginRole, Me } from "../types";
import { ApiError, requestJson } from "./client";

export function startLogin(portal: "staff" | "tenant"): void {
  window.location.assign(`/auth/login?portal=${portal}`);
}

export function logout(): void {
  window.location.assign("/auth/logout");
}

export async function endLocalSession(): Promise<void> {
  const response = await fetch("/auth/local/logout", { method: "POST", credentials: "include" });
  if (!response.ok) throw new ApiError(`Request failed with status ${response.status}`, response.status);
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
      const payload = (await response.json()) as { detail?: unknown };
      if (typeof payload.detail === "string") detail = payload.detail;
      else if (payload.detail !== undefined) detail = JSON.stringify(payload.detail);
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
  return fetch("/api/v1/demo/session", { method: "DELETE", credentials: "include" }).then(
    (response) => {
      if (!response.ok) throw new ApiError(`Request failed with status ${response.status}`, response.status);
    }
  );
}
