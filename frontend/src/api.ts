export type Locale = "en" | "ru";

export interface Step {
  equation: string;
  latex: string;
  status:
    | "valid"
    | "restated"
    | "final_correct"
    | "invalid"
    | "continues_error"
    | "true_statement"
    | "false_statement";
  kind: string | null;
  mistake: string | null;
}

export interface Message {
  id: string;
  role: "user" | "assistant";
  content: string;
  problem_id: string;
  steps: Step[];
  source: string | null;
  model: string | null;
}

export interface Problem {
  id: string;
  equation: string;
  latex: string;
  topic: string;
  topic_number: number;
  topic_title: string;
  task: string;
  skill: string;
  difficulty: number;
  purpose: "practice" | "follow_up";
  origin: string;
  phase: "solving" | "correcting" | "checking";
  mistake_count: number;
}

export interface SkillReport {
  skill: string;
  title: string;
  grade: number | null;
  attempts: number;
  mistakes: number;
  status: "new" | "strong" | "confirmed" | "needs_practice";
}

export interface TopicReport {
  id: string;
  number: number;
  title: string;
  summary: string;
  task: string;
  example: string;
  example_latex: string;
  completed: number;
  skills: SkillReport[];
}

export interface Progress {
  completed: number;
  clean: number;
  mistakes_total: number;
  mistakes_open: number;
  follow_ups_passed: number;
  follow_ups_total: number;
  by_type: { type: string; count: number }[];
  topics: TopicReport[];
  recent_mistakes: {
    type: string;
    skill: string;
    equation: string | null;
    explanation: string | null;
    resolved: boolean;
  }[];
}

export interface TutorState {
  locale: Locale;
  problem: Problem;
  problems: Problem[];
  messages: Message[];
  progress: Progress;
}

export interface Health {
  provider: string;
  configured: boolean;
  models: string[];
}

export class ApiError extends Error {
  constructor(
    readonly code: string,
    readonly status: number,
  ) {
    super(code);
  }
}

const TOKEN_KEY = "math-tutor-token";

// Browsers can refuse storage (private windows, blocked site data), so a failure only means the
// student's record will not survive a reload.
export function loadToken(): string | null {
  try {
    return localStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

export function saveToken(token: string | null): void {
  try {
    if (token) localStorage.setItem(TOKEN_KEY, token);
    else localStorage.removeItem(TOKEN_KEY);
  } catch {
    /* See loadToken. */
  }
}

async function request<T>(
  path: string,
  token: string | null,
  options: { method?: string; body?: unknown } = {},
): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`/api${path}`, {
      method: options.method ?? "GET",
      headers: {
        "Content-Type": "application/json",
        ...(token ? { "X-Student-Token": token } : {}),
      },
      body: options.body === undefined ? undefined : JSON.stringify(options.body),
    });
  } catch {
    throw new ApiError("network", 0);
  }
  if (!response.ok) {
    const payload = await response.json().catch(() => null);
    throw new ApiError(payload?.detail?.code ?? "unexpected", response.status);
  }
  return response.json() as Promise<T>;
}

export const api = {
  health: () => request<Health>("/health", null),
  createStudent: (locale: Locale) =>
    request<TutorState & { token: string }>("/students", null, {
      method: "POST",
      body: { locale },
    }),
  state: (token: string) => request<TutorState>("/state", token),
  setLocale: (token: string, locale: Locale) =>
    request<TutorState>("/locale", token, { method: "PUT", body: { locale } }),
  send: (token: string, text: string, locale: Locale) =>
    request<TutorState & { reply: string; problem_completed: boolean }>("/messages", token, {
      method: "POST",
      body: { text, locale },
    }),
  startTopic: (token: string, topic: string) =>
    request<TutorState>(`/topics/${topic}/start`, token, { method: "POST" }),
  startProblem: (token: string, equation: string) =>
    request<TutorState>("/problems", token, { method: "POST", body: { equation } }),
};
