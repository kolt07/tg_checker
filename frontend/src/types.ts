export type RowStatus =
  | "found"
  | "not_found_or_private"
  | "invalid_phone"
  | "retry_later"
  | "error"
  | null;

export type JobStatus = "queued" | "running" | "paused" | "completed" | "failed";

export interface SessionPublic {
  id: string;
  label: string | null;
  phone: string | null;
  username: string | null;
  first_name: string | null;
  created_at: string;
}

export interface SignInResponse {
  password_required: boolean;
  login_id: string | null;
  session: SessionPublic | null;
}

export interface CheckSummary {
  id: string;
  session_id: string | null;
  status: JobStatus;
  total: number;
  processed: number;
  found: number;
  pause_reason: string | null;
  flood_wait_until: string | null;
  last_flood_wait_seconds: number | null;
  flood_wait_count: number;
  error_message: string | null;
  created_at: string;
  account_count: number;
  timeout: number | null;
}

export interface CheckRow {
  input_phone: string;
  phone: string | null;
  status: RowStatus;
  telegram_id: number | null;
  username: string | null;
  first_name: string | null;
  last_name: string | null;
  premium: boolean;
  verified: boolean;
  bot: boolean;
  scam: boolean;
  fake: boolean;
  presence: string | null;
  was_online: string | null;
  bio: string | null;
  error_detail: string | null;
}

export interface CheckDetail extends CheckSummary {
  counts: Record<string, number>;
  flood_wait_seconds: number | null;
  callback_url: string | null;
  callback_status: string | null;
  started_at: string | null;
  finished_at: string | null;
  rows: CheckRow[];
}
