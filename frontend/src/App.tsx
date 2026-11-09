import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import { api, ApiError, loadToken, saveToken, type Health, type TutorState } from "./api";
import { Book, ChapterMargin } from "./Contents";
import { lookup, t } from "./i18n";
import { Lesson } from "./Lesson";

type Theme = "light" | "dark";
const THEME_KEY = "math-tutor-theme";

function initialTheme(): Theme {
  try {
    const saved = localStorage.getItem(THEME_KEY);
    if (saved === "light" || saved === "dark") return saved;
  } catch {
    /* Storage can be unavailable; the theme then follows the system. */
  }
  return window.matchMedia?.("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

function errorCode(error: unknown): string {
  return error instanceof ApiError ? error.code : "unexpected";
}

export function App() {
  const [state, setState] = useState<TutorState | null>(null);
  const [health, setHealth] = useState<Health | null>(null);
  const [token, setToken] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [errorKey, setErrorKey] = useState<string | null>(null);
  const [loadFailed, setLoadFailed] = useState(false);
  const [view, setView] = useState<"book" | "lesson">("book");
  const [tab, setTab] = useState<"lesson" | "margin">("lesson");
  const [theme, setTheme] = useState<Theme>(initialTheme);
  const [dialogError, setDialogError] = useState<string | null>(null);
  const dialogRef = useRef<HTMLDialogElement>(null);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    try {
      localStorage.setItem(THEME_KEY, theme);
    } catch {
      /* A remembered theme is only a convenience. */
    }
  }, [theme]);

  const begin = useCallback(async () => {
    const created = await api.createStudent("en");
    saveToken(created.token);
    setToken(created.token);
    setState(created);
  }, []);

  const load = useCallback(async () => {
    setLoadFailed(false);
    try {
      const saved = loadToken();
      if (saved) {
        try {
          setState(await api.state(saved));
          setToken(saved);
          return;
        } catch (error) {
          // A database reset on the host invalidates old tokens; just begin again.
          if (!(error instanceof ApiError && error.status === 401)) throw error;
          saveToken(null);
        }
      }
      await begin();
    } catch {
      setLoadFailed(true);
    }
  }, [begin]);

  useEffect(() => {
    void load();
    api.health().then(setHealth, () => setHealth(null));
  }, [load]);

  function openLesson() {
    setView("lesson");
    setTab("lesson");
    window.scrollTo(0, 0);
  }

  async function openTopic(topic: string) {
    if (!token || !state || busy) return;
    setErrorKey(null);
    // Opening the chapter already in progress continues it rather than starting over.
    if (state.problem.topic === topic) {
      openLesson();
      return;
    }
    try {
      setState(await api.startTopic(token, topic));
      openLesson();
    } catch (error) {
      setErrorKey(errorCode(error));
    }
  }

  async function send(text: string): Promise<boolean> {
    if (!token) return false;
    setBusy(true);
    setErrorKey(null);
    try {
      setState(await api.send(token, text, "en"));
      return true;
    } catch (error) {
      setErrorKey(errorCode(error));
      return false;
    } finally {
      setBusy(false);
    }
  }

  async function startOver() {
    if (!window.confirm(t.startOverConfirm)) return;
    saveToken(null);
    setErrorKey(null);
    setView("book");
    try {
      await begin();
    } catch (error) {
      setErrorKey(errorCode(error));
    }
  }

  async function submitProblem(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!token) return;
    const field = event.currentTarget.elements.namedItem("equation") as HTMLInputElement;
    try {
      setState(await api.startProblem(token, field.value));
      field.value = "";
      setDialogError(null);
      setErrorKey(null);
      dialogRef.current?.close();
      openLesson();
    } catch (error) {
      setDialogError(errorCode(error));
    }
  }

  if (!state) {
    return (
      <div className="loading">
        {loadFailed ? (
          <>
            <p>{t.loadError}</p>
            <button className="button-primary" onClick={() => void load()}>
              {t.retry}
            </button>
          </>
        ) : (
          <span className="dot" role="status" aria-label="Loading" />
        )}
      </div>
    );
  }

  return (
    <div className={`app view-${view}`}>
      <header className="topbar">
        <div className="brand">
          {view === "lesson" ? (
            <button className="button-back" onClick={() => setView("book")}>
              ← {t.backToContents}
            </button>
          ) : (
            <>
              <span className="brand-mark" aria-hidden="true">
                x
              </span>
              <div>
                <h1>{t.appName}</h1>
                <p>{t.tagline}</p>
              </div>
            </>
          )}
        </div>
        <div className="actions">
          <button
            className="button-ghost"
            onClick={() => {
              setDialogError(null);
              dialogRef.current?.showModal();
            }}
          >
            {t.ownProblem}
          </button>
          <button className="button-ghost" onClick={() => void startOver()}>
            {t.startOver}
          </button>
          <div className="theme-switch" role="group" aria-label="Theme">
            {(["light", "dark"] as Theme[]).map((option) => (
              <button
                key={option}
                aria-pressed={theme === option}
                onClick={() => setTheme(option)}
                title={option === "light" ? t.themeLight : t.themeDark}
              >
                <span aria-hidden="true">{option === "light" ? "☀" : "☾"}</span>
                <span className="sr-only">{option === "light" ? t.themeLight : t.themeDark}</span>
              </button>
            ))}
          </div>
        </div>
      </header>

      {health?.provider === "stub" && <div className="banner">{t.stubBanner}</div>}
      {view === "book" && errorKey && (
        <div className="error-note" role="alert">
          {lookup(t.errors, errorKey)}
        </div>
      )}

      {view === "book" ? (
        <Book progress={state.progress} problem={state.problem} onOpenTopic={(id) => void openTopic(id)} />
      ) : (
        <>
          <div className="tabs" role="tablist">
            <button role="tab" aria-selected={tab === "lesson"} onClick={() => setTab("lesson")}>
              {t.tabLesson}
            </button>
            <button role="tab" aria-selected={tab === "margin"} onClick={() => setTab("margin")}>
              {t.yourRecord}
            </button>
          </div>
          <div className={`desk show-${tab}`}>
            <ChapterMargin progress={state.progress} problem={state.problem} />
            <Lesson
              problem={state.problem}
              problems={state.problems}
              messages={state.messages}
              busy={busy}
              error={errorKey ? lookup(t.errors, errorKey) : null}
              onSend={send}
            />
          </div>
        </>
      )}

      <dialog ref={dialogRef} className="dialog">
        <form onSubmit={(event) => void submitProblem(event)}>
          <h2>{t.dialogTitle}</h2>
          <p>{t.dialogText}</p>
          <label htmlFor="equation-input">{t.dialogLabel}</label>
          <input id="equation-input" name="equation" required maxLength={200} autoComplete="off" />
          {dialogError && (
            <p className="error-note" role="alert">
              {lookup(t.errors, dialogError)}
            </p>
          )}
          <div className="dialog-actions">
            <button
              type="button"
              className="button-ghost"
              onClick={() => dialogRef.current?.close()}
            >
              {t.cancel}
            </button>
            <button type="submit" className="button-primary">
              {t.dialogSubmit}
            </button>
          </div>
        </form>
      </dialog>
    </div>
  );
}
