import { useEffect, useState, type FormEvent } from "react";
import { clearApiKey, getApiKey, listSessions, setApiKey, setUnauthorizedHandler } from "./api";
import { HomePage } from "./pages/HomePage";

export function App() {
  const [unlocked, setUnlocked] = useState(false);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    setUnauthorizedHandler(() => {
      clearApiKey();
      setUnlocked(false);
    });
  }, []);

  useEffect(() => {
    const existing = getApiKey();
    if (!existing) {
      setReady(true);
      return;
    }
    listSessions()
      .then(() => setUnlocked(true))
      .catch(() => {
        clearApiKey();
        setUnlocked(false);
      })
      .finally(() => setReady(true));
  }, []);

  if (!ready) {
    return <p className="boot">Завантаження…</p>;
  }
  if (!unlocked) {
    return <ApiKeyGate onUnlock={() => setUnlocked(true)} />;
  }
  return (
    <HomePage
      onLock={() => {
        clearApiKey();
        setUnlocked(false);
      }}
    />
  );
}

function ApiKeyGate({ onUnlock }: { onUnlock: () => void }) {
  const [value, setValue] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    setApiKey(value);
    try {
      await listSessions();
      onUnlock();
    } catch (err) {
      clearApiKey();
      setError(err instanceof Error ? err.message : "Не вдалося підключитися");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="gate">
      <form className="card gate-card" onSubmit={submit}>
        <h1>Перевірка номерів</h1>
        <p className="muted">Ключ API з файлу .env</p>
        <label className="field">
          <span>Ключ</span>
          <input value={value} onChange={(event) => setValue(event.target.value)} autoComplete="off" required />
        </label>
        {error ? <p className="banner bad">{error}</p> : null}
        <button className="primary" type="submit" disabled={busy}>
          {busy ? "Перевірка…" : "Продовжити"}
        </button>
      </form>
    </div>
  );
}
