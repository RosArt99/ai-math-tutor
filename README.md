# AI Math Tutor

A web-based algebra tutor built on the method of the Soviet school. It finds the flaw in a
student's reasoning, leads them to fix it with questions instead of answers, insists on checking
by substitution, and then sets a similar problem to confirm they really understood.

The interface is laid out like a modern algebra textbook. Each chapter in the contents shows a
sample problem, because students recognise a type of problem by its shape before they know its
name. Each chapter also lists the skills it grades, with a score for each.

| Chapter | Sample | Errors the engine recognises |
|---|---|---|
| 1. Linear equations | `3(x + 4) = 2x + 19` | factor not applied to every term in the bracket, wrong sign when opening a bracket, moving a term without changing its sign, changing one side only, wrong inverse operation |
| 2. Equations with fractions | `x/2 + x/3 = 10` | not every term multiplied by the common denominator, only part of a numerator multiplied |
| 3. Expanding and simplifying | `(x + 3)² − x(x − 2)` | the middle term of the square left out or not doubled, or given the wrong sign; sign errors in brackets |

Arithmetic errors are caught in every chapter, including when the student checks an answer.

## Run it

**Live demo:** _link added after deployment_

**With Docker:**

```bash
cp .env.example .env    # then add your OPENROUTER_API_KEY
docker build -t ai-math-tutor .
docker run --rm -p 8000:8000 --env-file .env ai-math-tutor
```

**Without Docker** (requires [uv](https://docs.astral.sh/uv/) and Node.js 20+):

```bash
./run.sh
```

Then open <http://localhost:8000>. A free key is available at
<https://openrouter.ai/keys>. To try the interface with no key at all, set `LLM_PROVIDER=stub`
in `.env`; replies are then clearly labelled placeholders.

## Try the assignment's scenario

The first problem is `3(x + 4) = 2x + 19`. Type the student's error:

```text
3x + 4 = 2x + 19
```

1. The step is marked wrong, and the error is recorded as "factor didn't reach every term".
2. The tutor asks a question about the bracket and does not reveal `3x + 12` or `x = 7`.
3. Fix it (`3x + 12 = 2x + 19`), then finish (`x = 7`). A correct answer is not the end: the tutor
   asks for a substitution check.
4. Check it (`3(7 + 4) = 2*7 + 19`). The tutor announces a consolidation problem of the same kind,
   created by the model and verified by the engine.
5. Solve it without errors: the "Opening brackets" score in the contents rises and the skill is
   marked as overcome.

Other things to try: answer `x = 15` straight away (the bracket error is still traced from the
answer alone), ask "just tell me the answer", write a false check such as `57 = 33`, open chapter
3 and expand `(x + 3)²` as `x² + 9`, or enter your own problem.

## How it works

The central decision: **the model teaches, but it does not do the mathematics.** Language models
are persuasive and can be wrong about algebra, which is unacceptable in a tutor. So a deterministic
engine establishes what is true, and the model decides how to teach it.

```text
Student message
   │
   ▼
1. Engine (SymPy)     Extracts equations from free text, compares each step's solution set with
   │                  the last correct step, and names the error: "3 multiplied only the first term"
   ▼
2. Lesson state       Server-side phases: solving → correcting → checking → next problem.
   │                  Sets the directive and how much help to give (hint level 1 to 4)
   ▼
3. Language model     Turns those facts into one teaching move, in the student's language
   │                  (OpenRouter, one request per turn)
   ▼
4. Verification       Rejects replies that reveal the answer (one rewrite, then a safe hint) and
   │                  practice problems without one whole-number solution (then a generator)
   ▼
5. Saved atomically   If the model fails, nothing is saved and the student can simply resend
```

How this meets each requirement:

| Requirement | Implementation |
|---|---|
| **AI tutor** | Textbook-style interface with rendered formulas: contents with chapters on the left, the lesson page on the right |
| **Error detection in reasoning** | Names the faulty step, not just a wrong result: see the error column in the chapter table above. It works even from a bare answer: `x = 15` is traced back to the bracket. False arithmetic such as `57 = 33` is caught too. Wrong ideas stated in words are identified by the model |
| **Adaptive teaching** | Hints escalate from an open question to a worked example with other numbers to a stated rule, using the student's mistake history. The answer guard runs on the server, so this does not rely on the model behaving |
| **Personalised practice** | After a mistake, the tutor announces a consolidation problem on the same skill. The model writes it, and the engine verifies it, with a generator as fallback. Clean work raises the difficulty; weak skills come first |
| **Progress tracking** | Per chapter, a score out of 5 for each skill, from the last five problems that used it. Also problems solved, errors by type and whether they were fixed, and consolidation problems passed |

### The Soviet school influence

This shows in the method rather than the decoration:
- An answer is not accepted until it is checked by substitution, and the tutor will not finish
  that check for the student.
- Each transformation is named as it is taught: "moving a term to the other side, changing its
  sign" is recognised as its own rule, not re-described as another method.
- Problems progress in graded series, as in a *zadachnik*.
- Weak skills are revisited, and the five-point scale scores each skill.

### Why a free model works here

The engine does the hard mathematical work, so the model only has to teach, and its mistakes are
caught before students see them. The server makes one request per turn and sends all checkable
facts up front. Free models share a daily quota, so `LLM_MODELS` is an ordered fallback list.
Moving to a stronger paid model is a one-line configuration change.

### Stack

| Part | Choice | Why |
|---|---|---|
| Backend | Python, FastAPI, SQLAlchemy | SymPy is the reason for Python; all logic stays behind an API |
| Mathematics | SymPy, plus a custom parser | `sympy.parse_expr` uses `eval`, which is unsafe on student input, and SymPy expands `3(x + 4)` at once, losing the structure needed for diagnosis |
| Interface | React, TypeScript, KaTeX | Fast formula rendering; responsive; follows the system's light or dark theme |
| Storage | SQLite (Postgres through `DATABASE_URL`) | No setup for the prototype |

Replies appear when complete instead of streaming: a reply must pass the answer guard before the
student sees it, because a streamed giveaway cannot be taken back.

## Scaling to Web, iOS and Android

See [docs/SCALING.md](docs/SCALING.md).

## Development

```bash
cd backend
uv run pytest
uv run ruff check .
LLM_PROVIDER=stub uv run uvicorn --factory app.main:create_app --reload   # API on port 8000
```

```bash
cd frontend
npm run dev    # interface with hot reload; proxies /api to port 8000
```

To compare models on the scenario (uses about 3 requests per model):

```bash
cd backend
uv run python scripts/compare_models.py
```

## Current limits

- Three chapters, with one variable written as `x`. Each new chapter needs its own checks and its
  own catalogue of errors, which is where the product's value lies.
- Anonymous students: progress belongs to one browser.
- The rate limiter and per-student lock assume a single server process.
- The answer guard catches revealed equations and expressions, not an answer spelled out in words.
- The free tier allows 50 requests per day unless at least 10 credits have been purchased
  (then 1000 per day).
