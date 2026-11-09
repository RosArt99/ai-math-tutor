import { Fragment, useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import type { Message, Problem, Step } from "./api";
import { lookup, t } from "./i18n";
import { Formula, MathText } from "./MathText";
import { previewLatex, SyntaxGuide } from "./Syntax";

// Keys that are awkward on a keyboard for a student, with where the cursor should land.
const INSERTS: { label: string; text: string; cursor: number; title: string }[] = [
  { label: "x", text: "x", cursor: 1, title: "x" },
  { label: "x²", text: "^2", cursor: 2, title: "squared: ^2" },
  { label: "( )", text: "()", cursor: 1, title: "brackets" },
  { label: "a/b", text: "/", cursor: 1, title: "fraction: /" },
  { label: "·", text: "*", cursor: 1, title: "times: *" },
  { label: "=", text: " = ", cursor: 3, title: "equals" },
];

function isWrong(step: Step): boolean {
  return ["invalid", "continues_error", "false_statement"].includes(step.status);
}

export function StepLine({ step }: { step: Step }) {
  const wrong = isWrong(step);
  const status = lookup(t.statuses, step.status);
  const kind = !wrong && step.kind ? lookup(t.stepKinds, step.kind) : null;
  return (
    <li className={`step ${wrong ? "step-wrong" : "step-right"}`}>
      <span className="step-mark" aria-hidden="true">
        {wrong ? "✕" : "✓"}
      </span>
      <span className="step-formula">
        <Formula latex={step.latex} />
      </span>
      <span className="step-label">
        {status}
        {kind && <span className="step-kind"> · {kind}</span>}
      </span>
    </li>
  );
}

function ProblemHeader({ problem, number }: { problem: Problem; number: number }) {
  const isExpression = problem.task !== "Solve the equation";
  const phases: [Problem["phase"], string][] = [
    ["solving", isExpression ? t.phaseSimplify : t.phaseSolve],
    ...(problem.mistake_count > 0 || problem.phase === "correcting"
      ? ([["correcting", t.phaseCorrect]] as [Problem["phase"], string][])
      : []),
    ...(isExpression ? [] : ([["checking", t.phaseCheck]] as [Problem["phase"], string][])),
  ];
  return (
    <header className="page-header">
      <div className="running-head">
        <span>
          {t.chapter} {problem.topic_number} · {problem.topic_title}
        </span>
        <span className="running-number">
          {t.problem} {number}
        </span>
      </div>
      <div className="task-line">
        <span className="task">{problem.task}</span>
        {problem.purpose === "follow_up" && <span className="badge-accent">{t.consolidation}</span>}
      </div>
      <div className="problem-formula">
        <Formula latex={problem.latex} display />
      </div>
      <ol className="phases" aria-label="Progress on this problem">
        {phases.map(([phase, label]) => (
          <li key={phase} className={problem.phase === phase ? "phase-now" : ""}>
            {label}
          </li>
        ))}
      </ol>
    </header>
  );
}

function ChatMessage({ message }: { message: Message }) {
  const isTutor = message.role === "assistant";
  return (
    <article className={`message ${isTutor ? "message-tutor" : "message-student"}`}>
      <div className="message-author">{isTutor ? t.teacher : t.student}</div>
      <div className="message-body">
        <MathText text={message.content} />
      </div>
      {message.steps.length > 0 && (
        <ul className="steps">
          {message.steps.map((step, index) => (
            <StepLine key={index} step={step} />
          ))}
        </ul>
      )}
      {message.source === "guard_fallback" && <div className="message-note">{t.rewriteNote}</div>}
    </article>
  );
}

interface LessonProps {
  problem: Problem;
  problems: Problem[];
  messages: Message[];
  busy: boolean;
  error: string | null;
  onSend: (text: string) => Promise<boolean>;
}

export function Lesson({ problem, problems, messages, busy, error, onSend }: LessonProps) {
  const [draft, setDraft] = useState("");
  const [showHelp, setShowHelp] = useState(false);
  const preview = previewLatex(draft);

  function insert(text: string, cursor: number) {
    const field = inputRef.current;
    const start = field?.selectionStart ?? draft.length;
    const end = field?.selectionEnd ?? draft.length;
    const next = draft.slice(0, start) + text + draft.slice(end);
    setDraft(next);
    requestAnimationFrame(() => {
      field?.focus();
      field?.setSelectionRange(start + cursor, start + cursor);
    });
  }
  const endRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages.length, busy, error, problem.id]);

  useEffect(() => {
    if (!busy) inputRef.current?.focus({ preventScroll: true });
  }, [busy]);

  async function submit(event?: FormEvent) {
    event?.preventDefault();
    const text = draft.trim();
    if (!text || busy) return;
    // The draft is cleared only on success, so nothing is lost if the tutor can't answer.
    if (await onSend(text)) setDraft("");
  }

  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault();
      void submit();
    }
  }

  const number = problems.findIndex((item) => item.id === problem.id) + 1;

  return (
    <main className="page">
      <ProblemHeader problem={problem} number={number} />
      <div className="chat" role="log" aria-live="polite" aria-label={t.tabLesson}>
        {problems.map((item, index) => {
          // The open chapter's own pages only; other chapters keep their history.
          if (item.topic !== problem.topic) return null;
          const own = messages.filter((message) => message.problem_id === item.id);
          if (own.length === 0 && item.id !== problem.id) return null;
          return (
            <Fragment key={item.id}>
              <div className="divider">
                <span>
                  {t.problem} {index + 1}
                  {item.purpose === "follow_up" ? ` · ${t.consolidation}` : ""}
                </span>
                <Formula latex={item.latex} />
              </div>
              {own.map((message) => (
                <ChatMessage key={message.id} message={message} />
              ))}
            </Fragment>
          );
        })}
        {busy && (
          <div className="thinking" role="status">
            <span className="dot" />
            {t.thinking}
          </div>
        )}
        {error && (
          <div className="error-note" role="alert">
            {error}
          </div>
        )}
        <div ref={endRef} />
      </div>
      <form className="composer" onSubmit={(event) => void submit(event)}>
        <div className="composer-tools">
          <div className="inserts" role="toolbar" aria-label={t.insert}>
            {INSERTS.map((item) => (
              <button
                key={item.label}
                type="button"
                className="key"
                title={item.title}
                onClick={() => insert(item.text, item.cursor)}
              >
                {item.label}
              </button>
            ))}
          </div>
          <button
            type="button"
            className="link-button"
            aria-expanded={showHelp}
            onClick={() => setShowHelp((open) => !open)}
          >
            {t.syntaxHelp} {showHelp ? "▴" : "▾"}
          </button>
        </div>
        {showHelp && (
          <div className="composer-help">
            <SyntaxGuide compact />
          </div>
        )}
        <label className="sr-only" htmlFor="composer-input">
          {t.composerLabel}
        </label>
        <textarea
          id="composer-input"
          ref={inputRef}
          rows={2}
          maxLength={1000}
          value={draft}
          placeholder={t.composerPlaceholder}
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={onKeyDown}
          aria-describedby="composer-hint"
        />
        <button type="submit" className="button-primary" disabled={busy || !draft.trim()}>
          {t.send}
        </button>
        <div id="composer-hint" className="composer-hint">
          {preview ? (
            <span className="preview">
              {t.preview}: <Formula latex={preview} />
            </span>
          ) : (
            t.composerHint
          )}
        </div>
      </form>
    </main>
  );
}
