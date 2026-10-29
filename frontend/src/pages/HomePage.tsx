import { useEffect, useState, type FormEvent } from "react";
import {
  createCheck,
  deleteSession,
  downloadExport,
  getCheck,
  getQr,
  listSessions,
  resendCode,
  saveSession,
  sendCode,
  signIn,
  startQr,
  submitQrPassword,
} from "../api";
import type { CodeDelivery } from "../api";
import { accountFlags, formatWhen, jobText, lastSeen, parsePhoneList, personName, rowText, sessionOptionLabel } from "../labels";
import type { CheckDetail, SessionPublic } from "../types";

const SESSION_KEY = "tgchecker.sessionId";
const API_ID_KEY = "tgchecker.apiId";
const API_HASH_KEY = "tgchecker.apiHash";

function readSessionId(): string {
  return localStorage.getItem(SESSION_KEY) ?? "";
}

function writeSessionId(id: string): void {
  if (id) {
    localStorage.setItem(SESSION_KEY, id);
  } else {
    localStorage.removeItem(SESSION_KEY);
  }
}

export function HomePage({ onLock }: { onLock: () => void }) {
  const [ready, setReady] = useState(false);
  const [sessions, setSessions] = useState<SessionPublic[]>([]);
  const [sessionId, setSessionId] = useState(readSessionId());
  const [error, setError] = useState("");
  const [phonesText, setPhonesText] = useState("");
  const [picked, setPicked] = useState<string[]>([]);
  const [timeoutText, setTimeoutText] = useState("");
  const [adding, setAdding] = useState(false);
  const [busy, setBusy] = useState(false);
  const [checkId, setCheckId] = useState<string | null>(null);
  const [check, setCheck] = useState<CheckDetail | null>(null);

  async function reloadSessions(preferId?: string) {
    const rows = await listSessions();
    setSessions(rows);
    const wanted = preferId || sessionId || readSessionId();
    const pickedRow = rows.find((row) => row.id === wanted) ?? rows[0];
    const next = pickedRow?.id ?? "";
    setSessionId(next);
    writeSessionId(next);
    setPicked((current) => {
      const valid = current.filter((id) => rows.some((row) => row.id === id));
      if (preferId && rows.some((row) => row.id === preferId) && !valid.includes(preferId)) {
        valid.push(preferId);
      }
      if (valid.length) return valid;
      return next ? [next] : [];
    });
    return rows;
  }

  useEffect(() => {
    reloadSessions()
      .catch((err: unknown) => setError(err instanceof Error ? err.message : "Помилка"))
      .finally(() => setReady(true));
  }, []);

  useEffect(() => {
    if (!checkId) return;
    let stop = false;
    let timer = 0;

    async function tick() {
      try {
        const data = await getCheck(checkId as string);
        if (stop) return;
        setCheck(data);
        setError("");
        if (data.status === "queued" || data.status === "running" || data.status === "paused") {
          timer = window.setTimeout(() => {
            void tick();
          }, 2000);
        }
      } catch (err) {
        if (!stop) setError(err instanceof Error ? err.message : "Помилка");
      }
    }

    void tick();
    return () => {
      stop = true;
      window.clearTimeout(timer);
    };
  }, [checkId]);

  const current = sessions.find((row) => row.id === sessionId) ?? null;

  async function removeSession(id: string) {
    if (!window.confirm("Від’єднати цей акаунт Telegram?")) return;
    setError("");
    try {
      await deleteSession(id);
      await reloadSessions();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Помилка");
    }
  }

  async function onFile(file: File | undefined) {
    if (!file) return;
    const text = await file.text();
    setPhonesText(parsePhoneList(text).join("\n"));
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    const phones = parsePhoneList(phonesText);
    if (picked.length === 0) {
      setError("Оберіть хоча б один акаунт Telegram.");
      return;
    }
    if (phones.length === 0) {
      setError("Додайте хоча б один номер.");
      return;
    }
    const timeoutRaw = timeoutText.trim().replace(",", ".");
    let timeout: number | undefined;
    if (timeoutRaw !== "") {
      timeout = Number(timeoutRaw);
      if (!Number.isFinite(timeout) || timeout < 0 || timeout > 3600) {
        setError("Пауза між запитами має бути від 0 до 3600 секунд.");
        return;
      }
    }
    setBusy(true);
    setError("");
    try {
      const created = await createCheck({
        phones,
        session_ids: picked,
        timeout,
      });
      setCheck(null);
      setCheckId(created.id);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Помилка");
    } finally {
      setBusy(false);
    }
  }

  if (!ready) {
    return <p className="boot">Завантаження…</p>;
  }

  return (
    <div className="page">
      <header className="top">
        <div>
          <h1>Перевірка номерів</h1>
          {picked.length > 1 ? (
            <p className="muted">Для перевірки обрано акаунтів: {picked.length}</p>
          ) : current ? (
            <p className="muted">{sessionOptionLabel(current)}</p>
          ) : (
            <p className="muted">Вхід в акаунт Telegram</p>
          )}
        </div>
        <div className="top-actions">
          <button className="text" type="button" onClick={onLock}>
            Вийти
          </button>
        </div>
      </header>

      {error ? <p className="banner bad">{error}</p> : null}

      {current && adding ? (
        <div className="stack">
          <button className="quiet" type="button" onClick={() => setAdding(false)}>
            Скасувати
          </button>
          <LoginCard
            onReady={(session) => {
              setAdding(false);
              setError("");
              reloadSessions(session.id).catch((err: unknown) =>
                setError(err instanceof Error ? err.message : "Помилка"),
              );
            }}
            onError={setError}
          />
        </div>
      ) : current ? (
        <form className="card stack" onSubmit={submit}>
          <fieldset className="accounts">
            <legend>Акаунти</legend>
            <div className="stack">
              {sessions.map((session) => (
                <div className="account-row" key={session.id}>
                  <label className="check">
                    <input
                      type="checkbox"
                      checked={picked.includes(session.id)}
                      onChange={(event) => {
                        const checked = event.target.checked;
                        setPicked((currentIds) =>
                          checked
                            ? currentIds.includes(session.id)
                              ? currentIds
                              : [...currentIds, session.id]
                            : currentIds.filter((id) => id !== session.id),
                        );
                      }}
                    />
                    <span>{sessionOptionLabel(session)}</span>
                  </label>
                  <button className="text" type="button" onClick={() => void removeSession(session.id)}>
                    Від’єднати
                  </button>
                </div>
              ))}
            </div>
          </fieldset>
          <p className="muted">Кожен позначений акаунт одночасно обробляє свою частку номерів.</p>
          <button className="quiet" type="button" onClick={() => setAdding(true)}>
            Додати акаунт
          </button>
          <label className="field">
            <span>Пауза між запитами, секунди</span>
            <input
              value={timeoutText}
              onChange={(event) => setTimeoutText(event.target.value)}
              inputMode="decimal"
              placeholder="стандартна"
            />
          </label>
          <p className="muted">Необов’язково. Пауза між запитами одного акаунта. Інші акаунти від неї не чекають.</p>
          <label className="field">
            <span>Номери</span>
            <textarea
              rows={12}
              value={phonesText}
              onChange={(event) => setPhonesText(event.target.value)}
              placeholder={"Один номер на рядок\n+380501112233"}
            />
          </label>
          <div className="file-row">
            <label className="file">
              <input
                type="file"
                accept=".csv,text/csv,text/plain"
                onChange={(event) => {
                  void onFile(event.target.files?.[0]);
                }}
              />
              <span>Файл TXT або CSV</span>
            </label>
            <span className="muted">{countLabel(parsePhoneList(phonesText).length)}</span>
          </div>
          <button className="primary" type="submit" disabled={busy}>
            {busy ? "Надсилання…" : "Перевірити"}
          </button>
        </form>
      ) : (
        <LoginCard
          onReady={(session) => {
            setError("");
            reloadSessions(session.id).catch((err: unknown) =>
              setError(err instanceof Error ? err.message : "Помилка"),
            );
          }}
          onError={setError}
        />
      )}

      {check ? <Results check={check} /> : null}
    </div>
  );
}

function countLabel(count: number): string {
  if (count === 0) return "";
  const mod10 = count % 10;
  const mod100 = count % 100;
  if (mod10 === 1 && mod100 !== 11) return `${count} номер`;
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return `${count} номери`;
  return `${count} номерів`;
}

function LoginCard({
  onReady,
  onError,
}: {
  onReady: (session: SessionPublic) => void;
  onError: (message: string) => void;
}) {
  const [apiId, setApiId] = useState(() => localStorage.getItem(API_ID_KEY) ?? "");
  const [apiHash, setApiHash] = useState(() => localStorage.getItem(API_HASH_KEY) ?? "");
  const [phone, setPhone] = useState("");
  const [loginId, setLoginId] = useState("");
  const [hint, setHint] = useState("");
  const [delivery, setDelivery] = useState("");
  const [canResend, setCanResend] = useState(false);
  const [resendIn, setResendIn] = useState(0);
  const [code, setCode] = useState("");
  const [password, setPassword] = useState("");
  const [needPassword, setNeedPassword] = useState(false);
  const [busy, setBusy] = useState(false);
  const [qrId, setQrId] = useState("");
  const [qrSvg, setQrSvg] = useState("");
  const [qrHint, setQrHint] = useState("");
  const [qrPhase, setQrPhase] = useState("");
  const [qrPassword, setQrPassword] = useState("");

  useEffect(() => {
    if (!qrId) return;
    let stop = false;
    let timer = 0;

    async function tick() {
      try {
        const status = await getQr(qrId);
        if (stop) return;
        if (status.qr_svg) setQrSvg(status.qr_svg);
        if (status.hint) setQrHint(status.hint);
        setQrPhase(status.status);
        if (status.status === "ready" && status.session) {
          onReady(status.session);
          return;
        }
        if (status.status === "error") {
          onError(status.hint || "Не вдалося увійти");
          return;
        }
        timer = window.setTimeout(() => {
          void tick();
        }, 2000);
      } catch (err) {
        if (!stop) onError(err instanceof Error ? err.message : "Помилка");
      }
    }

    void tick();
    return () => {
      stop = true;
      window.clearTimeout(timer);
    };
  }, [qrId]);

  function rememberCredentials() {
    localStorage.setItem(API_ID_KEY, apiId.trim());
    localStorage.setItem(API_HASH_KEY, apiHash.trim());
  }

  function applyDelivery(response: CodeDelivery) {
    setLoginId(response.login_id);
    setHint(response.hint);
    setDelivery(response.delivery);
    setCanResend(response.can_resend);
    setResendIn(response.resend_in ?? 0);
  }

  useEffect(() => {
    if (resendIn <= 0) return;
    const timer = window.setTimeout(() => setResendIn((value) => Math.max(0, value - 1)), 1000);
    return () => window.clearTimeout(timer);
  }, [resendIn]);

  function credentialsReady(): boolean {
    if (!apiId.trim() || !apiHash.trim()) {
      onError("Вкажіть api_id і api_hash.");
      return false;
    }
    return true;
  }

  async function beginQr() {
    if (!credentialsReady()) return;
    setBusy(true);
    onError("");
    rememberCredentials();
    try {
      const response = await startQr({ api_id: Number(apiId), api_hash: apiHash.trim() });
      setQrId(response.login_id);
      setQrSvg(response.qr_svg);
      setQrHint(response.hint);
      setQrPhase("pending");
      setQrPassword("");
    } catch (err) {
      onError(err instanceof Error ? err.message : "Помилка");
    } finally {
      setBusy(false);
    }
  }

  async function confirmQrPassword(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    onError("");
    try {
      const status = await submitQrPassword(qrId, qrPassword);
      if (status.session) {
        onReady(status.session);
        return;
      }
      if (status.hint) setQrHint(status.hint);
      setQrPhase(status.status);
    } catch (err) {
      onError(err instanceof Error ? err.message : "Помилка");
    } finally {
      setBusy(false);
    }
  }

  async function requestCode(event?: FormEvent) {
    event?.preventDefault();
    if (!credentialsReady()) return;
    setBusy(true);
    onError("");
    rememberCredentials();
    try {
      const response = await sendCode({
        api_id: Number(apiId),
        api_hash: apiHash.trim(),
        phone: phone.trim(),
      });
      applyDelivery(response);
      setNeedPassword(false);
      setCode("");
      setPassword("");
    } catch (err) {
      onError(err instanceof Error ? err.message : "Помилка");
    } finally {
      setBusy(false);
    }
  }

  async function resend() {
    setBusy(true);
    onError("");
    try {
      applyDelivery(await resendCode(loginId));
    } catch (err) {
      onError(err instanceof Error ? err.message : "Помилка");
    } finally {
      setBusy(false);
    }
  }

  async function confirm(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    onError("");
    try {
      const response = await signIn({
        login_id: loginId,
        code: code.replace(/\s/g, ""),
        password: password.trim() || undefined,
      });
      if (response.password_required) {
        setNeedPassword(true);
        setHint("Потрібен пароль двоетапної перевірки.");
        return;
      }
      if (response.session) {
        onReady(response.session);
      }
    } catch (err) {
      onError(err instanceof Error ? err.message : "Помилка");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="card login-card stack">
      <label className="field">
        <span>api_id</span>
        <input value={apiId} onChange={(event) => setApiId(event.target.value)} inputMode="numeric" required />
      </label>
      <label className="field">
        <span>api_hash</span>
        <input value={apiHash} onChange={(event) => setApiHash(event.target.value)} autoComplete="off" required />
      </label>
      <p className="muted">api_id і api_hash — з my.telegram.org. SMS для такого входу Telegram не надсилає.</p>
      <button className="primary" type="button" disabled={busy} onClick={() => void beginQr()}>
        {busy && !qrId ? "Зачекайте…" : "Увійти QR-кодом"}
      </button>
      {qrSvg && qrPhase !== "ready" ? (
        <div className="stack">
          {qrPhase === "pending" ? (
            <div className="qr-frame">
              <img src={qrSvg} alt="QR-код для входу в Telegram" />
            </div>
          ) : null}
          {qrHint ? <p className="muted">{qrHint}</p> : null}
          {qrPhase === "password" ? (
            <form className="stack" onSubmit={(event) => void confirmQrPassword(event)}>
              <label className="field">
                <span>Пароль двоетапної перевірки</span>
                <input
                  type="password"
                  value={qrPassword}
                  onChange={(event) => setQrPassword(event.target.value)}
                  autoComplete="current-password"
                  required
                />
              </label>
              <button className="primary" type="submit" disabled={busy}>
                {busy ? "Зачекайте…" : "Підтвердити"}
              </button>
            </form>
          ) : null}
        </div>
      ) : null}
      <details className="advanced">
        <summary>Увійти кодом</summary>
      <form className="stack" onSubmit={loginId ? confirm : (event) => void requestCode(event)}>
        <label className="field">
          <span>Номер акаунта</span>
          <input value={phone} onChange={(event) => setPhone(event.target.value)} placeholder="+380…" required />
        </label>
        {loginId ? (
          <>
            {hint ? <p className="banner good">{hint}</p> : null}
            <label className="field">
              <span>Код</span>
              <input
                value={code}
                onChange={(event) => setCode(event.target.value)}
                inputMode="numeric"
                autoComplete="one-time-code"
                required={!needPassword}
              />
            </label>
            {needPassword ? (
              <label className="field">
                <span>Пароль двоетапної перевірки</span>
                <input
                  type="password"
                  value={password}
                  onChange={(event) => setPassword(event.target.value)}
                  autoComplete="current-password"
                  required
                />
              </label>
            ) : null}
          </>
        ) : null}
        <div className="actions">
          <button className="primary" type="submit" disabled={busy}>
            {busy ? "Зачекайте…" : loginId ? "Увійти" : "Надіслати код"}
          </button>
          {loginId && canResend ? (
            <button className="quiet" type="button" disabled={busy || resendIn > 0} onClick={() => void resend()}>
              {resendIn > 0
                ? `Надіслати SMS через ${resendIn} с`
                : delivery === "app"
                  ? "Надіслати SMS"
                  : "Надіслати код ще раз"}
            </button>
          ) : null}
        </div>
      </form>
      </details>
      <PasteSession onReady={onReady} onError={onError} />
    </section>
  );
}

function PasteSession({
  onReady,
  onError,
}: {
  onReady: (session: SessionPublic) => void;
  onError: (message: string) => void;
}) {
  const [apiId, setApiId] = useState(() => localStorage.getItem(API_ID_KEY) ?? "");
  const [apiHash, setApiHash] = useState(() => localStorage.getItem(API_HASH_KEY) ?? "");
  const [stringSession, setStringSession] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    onError("");
    localStorage.setItem(API_ID_KEY, apiId.trim());
    localStorage.setItem(API_HASH_KEY, apiHash.trim());
    try {
      const session = await saveSession({
        api_id: Number(apiId),
        api_hash: apiHash.trim(),
        string_session: stringSession.trim(),
      });
      setStringSession("");
      onReady(session);
    } catch (err) {
      onError(err instanceof Error ? err.message : "Помилка");
    } finally {
      setBusy(false);
    }
  }

  return (
    <details className="advanced">
      <summary>Вставити готову сесію</summary>
      <form className="stack" onSubmit={submit}>
        <label className="field">
          <span>api_id</span>
          <input value={apiId} onChange={(event) => setApiId(event.target.value)} inputMode="numeric" required />
        </label>
        <label className="field">
          <span>api_hash</span>
          <input value={apiHash} onChange={(event) => setApiHash(event.target.value)} autoComplete="off" required />
        </label>
        <label className="field">
          <span>StringSession</span>
          <textarea value={stringSession} onChange={(event) => setStringSession(event.target.value)} rows={4} required />
        </label>
        <button className="primary" type="submit" disabled={busy}>
          {busy ? "Перевірка…" : "Зберегти"}
        </button>
      </form>
    </details>
  );
}

function Results({ check }: { check: CheckDetail }) {
  const active = check.status === "queued" || check.status === "running" || check.status === "paused";
  const [exportError, setExportError] = useState("");

  function saveExport(format: "json" | "csv") {
    setExportError("");
    downloadExport(check.id, format).catch((err: unknown) =>
      setExportError(err instanceof Error ? err.message : "Помилка"),
    );
  }

  return (
    <section className="stack">
      <div className="toolbar">
        <p className="muted">
          {jobText(check.status)} · {check.processed} з {check.total} · з акаунтом {check.found}
          {check.account_count > 1 ? ` · акаунтів ${check.account_count}` : ""}
          {check.timeout != null ? ` · пауза ${check.timeout} с` : ""}
        </p>
        <div className="actions">
          <button className="quiet" type="button" onClick={() => saveExport("json")}>
            JSON
          </button>
          <button className="quiet" type="button" onClick={() => saveExport("csv")}>
            CSV
          </button>
        </div>
      </div>
      {check.status === "paused" ? (
        <p className="banner warn">
          Telegram просить зачекати
          {check.flood_wait_seconds != null ? ` ${check.flood_wait_seconds} с` : ""}. Продовжимо{" "}
          {formatWhen(check.flood_wait_until)}.
        </p>
      ) : null}
      {check.status === "failed" && check.error_message ? <p className="banner bad">{check.error_message}</p> : null}
      {exportError ? <p className="banner bad">{exportError}</p> : null}
      {active ? <p className="muted">Результати оновлюються самі.</p> : null}
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Номер</th>
              <th>Ім’я</th>
              <th>Username</th>
              <th>ID</th>
              <th>Востаннє</th>
              <th>Біо</th>
              <th>Статус</th>
            </tr>
          </thead>
          <tbody>
            {check.rows.length === 0 ? (
              <tr>
                <td colSpan={7}>Ще немає рядків.</td>
              </tr>
            ) : (
              check.rows.map((row, index) => {
                const flags = accountFlags(row);
                const name = personName(row);
                const nameLabel = [name !== "—" ? name : "", flags.join(", ")].filter(Boolean).join(" · ") || "—";
                return (
                  <tr key={`${row.phone ?? row.input_phone}-${index}`}>
                    <td className="mono">{row.phone || row.input_phone}</td>
                    <td>{nameLabel}</td>
                    <td>{row.username ? `@${row.username}` : "—"}</td>
                    <td className="mono">{row.telegram_id ?? "—"}</td>
                    <td>{row.status === "found" ? lastSeen(row) : "—"}</td>
                    <td className="bio">{row.bio || "—"}</td>
                    <td>
                      <span className={`pill ${row.status ?? "pending"}`} title={row.error_detail ?? undefined}>
                        {rowText(row.status)}
                      </span>
                    </td>
                  </tr>
                );
              })
            )}
          </tbody>
        </table>
      </div>
    </section>
  );
}
