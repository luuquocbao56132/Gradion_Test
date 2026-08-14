# Book Illustration Studio - AI working context

## What this is
Gradion intern take-home. FastAPI + React app that runs the Gemini pipeline from
`Book_illustration.ipynb` steps 1-5 over a book's text.

## Sources of truth, in order
1. `gradion-assessment-intern-software-engineer.md`
2. `Book_illustration.ipynb` - Gemini mechanics only
3. `app-demo.html` - product scope, visible behaviour, visual floor
4. `docs/superpowers/specs/2026-08-14-book-illustration-studio-design.md`
5. `docs/superpowers/plans/2026-08-14-book-illustration-studio.md`

## Non-negotiables
- Max 2 characters, max 1 chapter, enforced server-side. Never slice silently.
- No automatic Gemini retries. HttpRetryOptions(attempts=1) - `attempts` counts the
  original call, so 1 means never retry. Inside an HttpRetryOptions it defaults to 5.
- The book reaches Gemini once, in step 1. Exception: context-expiry recovery for steps 2/4.
- Single uvicorn worker. No Redis, no broker, no queue, no worker process, no polling, no JWT.
- The frontend owns no pipeline state and never advances it optimistically.
- Status pills are exactly Draft / In progress / Done.
- Step handlers are resume-aware, never called "idempotent".

## Commands
- `./start.sh` - backend (8000) + frontend (5173)
- `./test.sh` - pytest + vitest
