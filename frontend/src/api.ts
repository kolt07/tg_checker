import type { CheckDetail, CheckSummary, SessionPublic, SignInResponse } from "./types";

const KEY = "tgchecker.apiKey";

let onUnauthorized = () => {};

export function setUnauthorizedHandler(handler: () => void): void {
  onUnauthorized = handler;
}

export function getApiKey(): string {
  return sessionStorage.getItem(KEY) ?? "";
}

export function setApiKey(value: string): void {
  sessionStorage.setItem(KEY, value.trim());
}

export function clearApiKey(): void {
  sessionStorage.removeItem(KEY);
}

export class ApiError extends Error {
  status: number;

  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

function detailMessage(detail: unknown): string {
  if (typeof detail === "string" && detail.trim()) {
    return detail;
  }
  if (Array.isArray(detail)) {
    const parts = detail
      .map((item) => {
        if (!item || typeof item !== "object" || !("msg" in item)) {
          return "";
        }
        return String(item.msg);
      })
      .filter(Boolean);
    if (parts.length) {
      return parts.join("; ");
    }
  }
  return "Запит не виконано";
}

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  headers.set("X-API-Key", getApiKey());
  if (init.body && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  const response = await fetch(path, { ...init, headers });
  if (response.status === 401) {
    onUnauthorized();
    throw new ApiError(401, "Невірний API-ключ");
  }
  if (!response.ok) {
    let message = "Запит не виконано";
    try {
      const data = (await response.json()) as { detail?: unknown };
      message = detailMessage(data.detail);
    } catch {
      message = "Запит не виконано";
    }
    throw new ApiError(response.status, message);
  }
  if (response.status === 204) {
    return undefined as T;
  }
  return (await response.json()) as T;
}

export function listSessions(): Promise<SessionPublic[]> {
  return api<SessionPublic[]>("/api/v1/sessions");
}

export interface CodeDelivery {
  login_id: string;
  phone: string;
  hint: string;
  delivery: string;
  can_resend: boolean;
  resend_in: number | null;
}

export function sendCode(body: { api_id: number; api_hash: string; phone: string }): Promise<CodeDelivery> {
  return api("/api/v1/auth/send-code", { method: "POST", body: JSON.stringify(body) });
}

export function resendCode(loginId: string): Promise<CodeDelivery> {
  return api("/api/v1/auth/resend-code", { method: "POST", body: JSON.stringify({ login_id: loginId }) });
}

export interface QrStart {
  login_id: string;
  qr_svg: string;
  hint: string;
}

export interface QrStatus {
  status: "pending" | "password" | "ready" | "error";
  qr_svg: string | null;
  hint: string | null;
  session: SessionPublic | null;
}

export function startQr(body: { api_id: number; api_hash: string }): Promise<QrStart> {
  return api("/api/v1/auth/qr", { method: "POST", body: JSON.stringify(body) });
}

export function getQr(loginId: string): Promise<QrStatus> {
  return api(`/api/v1/auth/qr/${loginId}`);
}

export function submitQrPassword(loginId: string, password: string): Promise<QrStatus> {
  return api("/api/v1/auth/qr/password", { method: "POST", body: JSON.stringify({ login_id: loginId, password }) });
}

export function signIn(body: { login_id: string; code: string; password?: string }): Promise<SignInResponse> {
  return api("/api/v1/auth/sign-in", { method: "POST", body: JSON.stringify(body) });
}

export function saveSession(body: {
  api_id: number;
  api_hash: string;
  string_session: string;
  label?: string;
}): Promise<SessionPublic> {
  return api("/api/v1/sessions", { method: "POST", body: JSON.stringify(body) });
}

export function deleteSession(id: string): Promise<void> {
  return api(`/api/v1/sessions/${id}`, { method: "DELETE" });
}

export function listChecks(): Promise<CheckSummary[]> {
  return api<CheckSummary[]>("/api/v1/checks?limit=20");
}

export function createCheck(body: {
  phones: string[];
  session_id?: string;
  session_ids?: string[];
  timeout?: number;
}): Promise<{ id: string }> {
  return api("/api/v1/checks", { method: "POST", body: JSON.stringify(body) });
}

export function getCheck(id: string): Promise<CheckDetail> {
  return api<CheckDetail>(`/api/v1/checks/${id}?limit=5000`);
}

export async function downloadExport(id: string, format: "json" | "csv"): Promise<void> {
  const headers = new Headers();
  headers.set("X-API-Key", getApiKey());
  const response = await fetch(`/api/v1/checks/${id}/export?format=${format}`, { headers });
  if (response.status === 401) {
    onUnauthorized();
    throw new ApiError(401, "Невірний API-ключ");
  }
  if (!response.ok) {
    throw new ApiError(response.status, format === "json" ? "Не вдалося експортувати JSON" : "Не вдалося експортувати CSV");
  }
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = format === "json" ? `perevirka-${id}.json` : `perevirka-${id}.csv`;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}
