/**
 * Auth API helpers — login, logout, session check.
 *
 * All requests use credentials: 'include' so the httpOnly qlib_session cookie
 * is sent cross-origin (FastAPI lives on a separate origin in dev; same-origin
 * via nginx in prod, but the flag is harmless either way).
 */

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:5001";

export interface LoginResponse {
  ok: boolean;
  username: string;
}

export interface MeResponse {
  username: string;
}

export async function login(username: string, password: string): Promise<LoginResponse> {
  const res = await fetch(`${API_BASE}/api/v1/auth/login`, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(body.detail || `Login failed (${res.status})`);
  }
  return res.json();
}

export async function logout(): Promise<void> {
  await fetch(`${API_BASE}/api/v1/auth/logout`, {
    method: "POST",
    credentials: "include",
  });
}

export async function fetchMe(): Promise<MeResponse | null> {
  try {
    const res = await fetch(`${API_BASE}/api/v1/auth/me`, {
      credentials: "include",
    });
    if (res.ok) return res.json();
  } catch {
    /* network errors → treat as not logged in */
  }
  return null;
}

// `next` must be a path on THIS site. Anything else — `javascript:…` (runs
// script in our origin with the new session) or `//evil.com` / `/\\evil.com`
// (a protocol-relative jump off-site) — falls back to "/".
export function safeNext(raw: string | null): string {
  if (!raw || !raw.startsWith("/") || raw.startsWith("//") || raw.startsWith("/\\")) return "/";
  return raw;
}
