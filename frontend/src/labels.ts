import type { CheckRow, JobStatus, RowStatus } from "./types";

export const jobLabel: Record<JobStatus, string> = {
  queued: "у черзі",
  running: "виконується",
  paused: "пауза",
  completed: "завершено",
  failed: "помилка",
};

export function jobText(status: string): string {
  if (status in jobLabel) {
    return jobLabel[status as JobStatus];
  }
  return status;
}

export const rowLabel: Record<Exclude<RowStatus, null>, string> = {
  found: "є акаунт",
  not_found_or_private: "немає акаунта",
  invalid_phone: "некоректний номер",
  retry_later: "пізніше",
  error: "помилка",
};

export function rowText(status: RowStatus): string {
  if (!status) {
    return "очікує";
  }
  return rowLabel[status];
}

export function formatWhen(value: string | null): string {
  if (!value) {
    return "—";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return date.toLocaleString("uk-UA");
}

export function lastSeen(row: CheckRow): string {
  switch (row.presence) {
    case "online":
      return "у мережі";
    case "offline":
      return row.was_online ? formatWhen(row.was_online) : "офлайн";
    case "recently":
      return "нещодавно";
    case "last_week":
      return "минулого тижня";
    case "last_month":
      return "минулого місяця";
    case "hidden":
      return "приховано";
    default:
      return "—";
  }
}

export function personName(row: CheckRow): string {
  const name = [row.first_name, row.last_name].filter(Boolean).join(" ").trim();
  return name || "—";
}

export function accountFlags(row: CheckRow): string[] {
  if (row.status !== "found") {
    return [];
  }
  const flags: string[] = [];
  if (row.premium) flags.push("Premium");
  if (row.verified) flags.push("підтверджений");
  if (row.bot) flags.push("бот");
  if (row.scam) flags.push("шахрайський");
  if (row.fake) flags.push("фейковий");
  return flags;
}

export function sessionTitle(session: {
  label: string | null;
  first_name: string | null;
  username: string | null;
  phone: string | null;
}): string {
  if (session.label) return session.label;
  if (session.first_name) return session.first_name;
  if (session.username) return `@${session.username}`;
  if (session.phone) return session.phone;
  return "Акаунт";
}

export function sessionOptionLabel(session: {
  label: string | null;
  first_name: string | null;
  username: string | null;
  phone: string | null;
}): string {
  const title = sessionTitle(session);
  const extra = [session.username ? `@${session.username}` : "", session.phone ?? ""].filter(Boolean).join(" · ");
  return extra && extra !== title ? `${title} · ${extra}` : title;
}

export function parsePhoneList(text: string): string[] {
  return text
    .split(/[\r\n,;]+/)
    .map((part) => part.trim().replace(/^"|"$/g, ""))
    .filter((part) => part && !/^(phone|номер|телефон)$/i.test(part));
}
