export function apiBase(): string {
  return (process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8000").replace(/\/$/, "");
}

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

let authToken: string | null = null;

export function setAuthToken(token: string | null) {
  authToken = token;
}

function detailMessage(body: unknown, fallback: string): string {
  if (typeof body === "object" && body && "detail" in body) {
    const detail = (body as { detail: unknown }).detail;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail) && detail[0] && typeof detail[0] === "object" && "msg" in detail[0]) {
      return String((detail[0] as { msg: string }).msg);
    }
  }
  return fallback;
}

async function wait(ms: number) {
  await new Promise((r) => setTimeout(r, ms));
}

export async function api<T>(
  path: string,
  init: RequestInit = {},
  timeoutMs = 45000,
): Promise<T> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const headers: Record<string, string> = {
      "Content-Type": "application/json",
      ...(init.headers as Record<string, string> | undefined),
    };
    if (authToken) headers.Authorization = `Bearer ${authToken}`;
    const response = await fetch(`${apiBase()}${path}`, {
      ...init,
      signal: controller.signal,
      headers,
    });
    const text = await response.text();
    let body: unknown = null;
    try {
      body = text ? JSON.parse(text) : null;
    } catch {
      body = { detail: text };
    }
    if (!response.ok) {
      if (response.status === 401 && typeof window !== "undefined" && !path.startsWith("/api/auth/")) {
        window.dispatchEvent(new Event("narcograph:unauthorized"));
      }
      throw new ApiError(response.status, detailMessage(body, response.statusText));
    }
    return body as T;
  } finally {
    clearTimeout(timer);
  }
}

export async function pingHealth(): Promise<{ ok: boolean; waking: boolean; payload?: Record<string, unknown> }> {
  for (let attempt = 0; attempt < 4; attempt += 1) {
    try {
      const payload = await api<Record<string, unknown>>("/health", {}, 8000);
      return { ok: true, waking: false, payload };
    } catch (err) {
      if (attempt === 3) {
        return { ok: false, waking: err instanceof ApiError && err.status >= 500 };
      }
      await wait(2500 * (attempt + 1));
    }
  }
  return { ok: false, waking: true };
}
