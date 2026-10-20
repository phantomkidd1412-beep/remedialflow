import { Platform } from "react-native";
import { storage } from "@/src/utils/storage";

export const BASE = process.env.EXPO_PUBLIC_BACKEND_URL as string;
const isWeb = Platform.OS === "web";
const REFRESH_KEY = "refresh_token";

export type User = { id: string; email: string; name: string; role: "admin" | "employee" | "installer"; active: boolean };

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

let accessToken: string | null = null;
let onAuthLost: (() => void) | null = null;
let refreshing: Promise<User | null> | null = null;

export function setAuthLostHandler(fn: () => void) {
  onAuthLost = fn;
}
export function getAccessToken() {
  return accessToken;
}
export const absUrl = (p: string) => (p.startsWith("http") ? p : `${BASE}${p}`);

async function handleTokens(data: any): Promise<User> {
  accessToken = data.access_token;
  if (!isWeb && data.refresh_token) await storage.secureSet(REFRESH_KEY, data.refresh_token);
  return data.user;
}

/** Changing the password revokes every session server-side; adopt the fresh one returned for this device. */
export async function changePassword(current_password: string, new_password: string) {
  const data = await api<any>("/auth/change-password", { method: "POST", body: { current_password, new_password } });
  await handleTokens(data);
}

async function readError(r: Response): Promise<string> {
  try {
    const j = await r.json();
    if (typeof j.detail === "string") return j.detail;
    if (Array.isArray(j.detail)) return j.detail.map((d: any) => d.msg).join(", ");
  } catch {}
  return `Request failed (${r.status})`;
}

export async function login(email: string, password: string): Promise<User> {
  const r = await fetch(`${BASE}/api/auth/login`, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json", "X-Client": isWeb ? "web" : "native" },
    body: JSON.stringify({ email, password }),
  });
  if (!r.ok) throw new ApiError(r.status, await readError(r));
  return handleTokens(await r.json());
}

/** Rotates the refresh token. Returns user, null if session is gone; throws on network failure. */
export function refreshSession(): Promise<User | null> {
  if (refreshing) return refreshing;
  refreshing = (async () => {
    try {
      const rt = isWeb ? null : await storage.secureGet(REFRESH_KEY, null);
      if (!isWeb && !rt) return null;
      const r = await fetch(`${BASE}/api/auth/refresh`, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json", "X-Client": isWeb ? "web" : "native" },
        body: JSON.stringify({ refresh_token: rt }),
      });
      if (r.status === 401 || r.status === 403) {
        accessToken = null;
        if (!isWeb) await storage.secureRemove(REFRESH_KEY);
        return null;
      }
      if (!r.ok) throw new ApiError(r.status, "Server unavailable");
      return await handleTokens(await r.json());
    } finally {
      refreshing = null;
    }
  })();
  return refreshing;
}

export async function logout() {
  const rt = isWeb ? null : await storage.secureGet(REFRESH_KEY, null);
  try {
    await fetch(`${BASE}/api/auth/logout`, {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json", "X-Client": isWeb ? "web" : "native" },
      body: JSON.stringify({ refresh_token: rt }),
    });
  } catch {}
  accessToken = null;
  if (!isWeb) await storage.secureRemove(REFRESH_KEY);
}

/** Runs a request with the access token, refreshing once on 401. */
export async function authed(run: (token: string | null) => Promise<Response>): Promise<Response> {
  let r = await run(accessToken);
  if (r.status === 401) {
    const u = await refreshSession();
    if (!u) {
      onAuthLost?.();
      throw new ApiError(401, "Session expired. Please sign in again.");
    }
    r = await run(accessToken);
  }
  return r;
}

export async function api<T = any>(path: string, init: { method?: string; body?: any } = {}): Promise<T> {
  const r = await authed((t) =>
    fetch(`${BASE}/api${path}`, {
      method: init.method ?? "GET",
      headers: {
        ...(init.body !== undefined ? { "Content-Type": "application/json" } : {}),
        ...(t ? { Authorization: `Bearer ${t}` } : {}),
      },
      body: init.body !== undefined ? JSON.stringify(init.body) : undefined,
    }),
  );
  if (!r.ok) throw new ApiError(r.status, await readError(r));
  return r.json();
}

export { readError };
