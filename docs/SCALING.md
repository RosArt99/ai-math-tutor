# Scaling to Web, iOS and Android

## Principle: one brain, thin clients

All teaching logic, the mathematics engine and the AI key stay on the server, behind the HTTP API
this prototype already uses. A client only shows the lesson and sends what the student writes.
A better explanation or a new error type therefore reaches every platform at once, with no app
store release, and the key can never be extracted from an app.

```text
Web (React)  ─┐
iOS (Expo)   ─┼─► HTTPS API ─► Tutor service ─► Mathematics engine
Android (Expo)┘                     │        ─► Model gateway ─► AI providers
                                    └────────► Postgres, Redis
```

## Clients

- **Web:** continue with the current React app, add a PWA manifest and offline shell.
- **iOS and Android:** one React Native (Expo) codebase. It reuses the TypeScript API types, lesson
  logic and translations. Formulas render with KaTeX in a small web view at first, or a native
  math library later.
- **Mobile additions:** push reminders for spaced review, and a photo of handwritten work (the
  vision model transcribes it, and the existing engine checks the result).
- **Shared package:** API client, types and translations in a monorepo, so the API is generated
  from FastAPI's OpenAPI schema and clients cannot drift from it.
- **Native only if needed:** Expo is right until a real limit appears, such as handwriting input.

## Backend stages

| Stage | Roughly | Changes |
|---|---|---|
| **1. Pilot** | up to 1,000 students | Accounts and sign-in, Postgres with migrations, a paid model, error tracking and logs |
| **2. Growth** | up to 100,000 students | Stateless API instances behind a load balancer; Redis for rate limits and turn locks (in-process today); queue for slow work; the engine in a worker pool with timeouts; streaming after a server-side guard pass on each sentence |
| **3. Scale** | beyond | Read replicas, region-aware routing, separate analytics store, per-school tenancy |

## AI quality and cost

- **Model gateway:** routing, fallback and budgets in one place; the provider can change without
  touching teaching logic.
- **Tiered models:** use a cheap model for routine turns and a stronger one for repeated confusion.
  Per-token cost then follows pedagogical need.
- **Automatic evaluation:** a set of scripted students runs against each prompt or model change,
  scoring whether errors were found, answers withheld and practice problems sound. This must pass
  before release, as ordinary tests do.
- **Cost controls:** per-student quotas, prompt caching, limited history length and a dashboard of
  cost per completed lesson.

## Product and compliance

- **Children's data:** COPPA and GDPR-K consent flows, data minimisation and deletion on request.
  Providers are chosen under contracts with no training on student data.
- **Curriculum:** the engine grows from linear equations to systems, inequalities, fractions and
  geometry. Each topic adds an error catalogue, which is the product's lasting advantage.
- **Teachers and parents:** class dashboards built on the mistake data already being recorded.
