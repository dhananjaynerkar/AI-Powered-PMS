export const API_ROOT = "/api/v1";

export class ApiError extends Error {
  constructor(
    message: string,
    public readonly status: number
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export async function requestJson<T>(
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
      const payload = (await response.json()) as { detail?: unknown };
      if (typeof payload.detail === "string") detail = payload.detail;
      else if (payload.detail !== undefined) detail = JSON.stringify(payload.detail);
    } catch {
      // Keep the status-based message when the server did not return JSON.
    }
    throw new ApiError(detail, response.status);
  }
  return (await response.json()) as T;
}

export async function loadRuntimeHealth<T>(): Promise<T> {
  const response = await fetch("/health/runtime", { credentials: "include" });
  if (!response.ok) throw new ApiError(`Backend health request failed (${response.status})`, response.status);
  return (await response.json()) as T;
}
