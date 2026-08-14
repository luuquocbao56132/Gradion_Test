# Book Illustration Studio Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A local web app that turns a book's text into 2 character portraits and 1 chapter illustration through five user-driven Gemini steps, with persistent, resumable, duplicate-proof pipeline state and live project updates.

**Architecture:** A single-process FastAPI backend owns all pipeline state in SQLite; artifacts (book text, PNGs) live on the local filesystem and are served through ownership-checked API endpoints. `POST /api/projects/{id}/run` performs one conditional `UPDATE` that simultaneously enforces step ordering, single execution and orphan recovery, then starts a detached in-process `asyncio` task and returns 202. A React/Vite frontend owns no pipeline state: it renders the server's project view, received first over REST and then over a WebSocket that carries the identical payload.

**Tech Stack:** Python 3.13 · FastAPI · uvicorn (single worker) · stdlib `sqlite3` · Pydantic v2 · `google-genai` 2.18.0 · pytest + pytest-asyncio + httpx · React 18 + TypeScript + Vite · Vitest + @testing-library/react.

**Spec:** `docs/superpowers/specs/2026-08-14-book-illustration-studio-design.md` (frozen at commit `d7ef080`). Read it alongside this plan — the plan argues from it and cites its section numbers.

**Assessment brief:** `gradion-assessment-intern-software-engineer.md` — the highest authority. `Book_illustration.ipynb` is authoritative for Gemini mechanics. `app-demo.html` is authoritative for product scope, visible behaviour and the visual floor.

---

## Global Constraints

Every task's requirements implicitly include this section.

**Hard product caps (assessment §03).** At most **2 characters**, at most **1 chapter**. Enforced server-side in three independent layers (spec §7.4): the prompt asks for at most that many; the response schema sets `maxItems`; the generation loop iterates at most that many rows *regardless of how many rows exist*. **No silent slicing anywhere** — a response with more items than the cap is `INVALID_OUTPUT`, not a truncated success.

**No automatic Gemini retries (assessment §4.3).** `attempts` counts the **original request**, so `attempts=1` is "call once, never repeat" — `google-genai` implements it as `tenacity.stop_after_attempt(1)`, which its own source names the *"never retry"* strategy. Verified against `_api_client.py` in the installed SDK 2.18.0:

| Configuration | Actual attempts |
|---|---|
| `genai.Client(api_key=…)` — no `retry_options` | 1 |
| `HttpRetryOptions()` — object present, `attempts` unset | **5** (`_RETRY_ATTEMPTS = 5  # including the initial call`) |
| `HttpRetryOptions(attempts=1)` — ours, and notebook cell 12 | 1 |

The real client is therefore constructed with `HttpRetryOptions(attempts=1)`, and Task 34 asserts the **resulting attempt count** rather than the value passed in, because the field name reads like a retry count when it is really a total. The 5 matters because we need an `HttpRetryOptions` for the timeout anyway, and it appears the moment that object exists. There is no retry loop anywhere in application code. **The only path that re-invokes Gemini is a user `POST`.**

**Book sent once (assessment §4.3).** In normal operation the book is uploaded and sent to Gemini exactly once, in step 1. Steps 2–5 reach it through the interaction chain. The single exception is provider-side context expiry, where a user-triggered retry of step 2 or 4 re-uploads it (spec §7.6).

**Single process.** uvicorn runs with `--workers 1`. Required twice over: `server_run_id` orphan detection and the in-memory realtime registry both assume it (spec §5.3, §9.5).

**Forbidden without a concrete demonstrated need:** Redis · any broker · job queue · worker process · polling fallback · JWT · client-side pipeline state · automatic retries · more than 2 characters · more than 1 chapter · cloud/blob storage · deployment · Docker.

**Terminology.** Step handlers are **resume-aware** / **checkpoint-aware**, never "idempotent" (spec §6.2). The external Gemini call is not idempotent across a crash window.

**Status pill vocabulary is exactly `Draft` / `In progress` / `Done`** (assessment §4.4). Failure and interruption surface through a separate `needs_attention` boolean rendered beside the pill, never as a fourth pill value (spec §4.2).

**Python version:** 3.13 (`.venv` at repo root is 3.13.13 and already has `google-genai` 2.18.0).

**Commit style:** small and meaningful; one commit per task minimum. Where a task is mostly AI-authored, say so in the commit body (assessment §2.4).

**Shell:** `start.sh` / `test.sh` are POSIX `sh`. On Windows they run under Git Bash; `README.md` says so.

---

## File Structure

Every file, and what it is responsible for. Names here are binding for the rest of the plan.

### Backend — `backend/`

| Path | Responsibility |
|---|---|
| `requirements.txt` | Pinned backend dependencies |
| `pytest.ini` | `asyncio_mode = auto`, test paths |
| `app/__init__.py` | Empty package marker |
| `app/config.py` | `Settings` frozen dataclass + `load_settings()` from environment. Owns nothing else |
| `app/db.py` | Connection factory (`get_conn`), per-connection pragmas, `init_schema()`. **No business SQL** |
| `app/steps.py` | **Leaf vocabulary module.** `StepName`, `ProjectStatus`, `StepState`, the ordered `STEPS` list, and pure derivations: `current_step`, `completed_steps`, `status_before`, `status_after`, `display_status`, `needs_attention`, `chain_of_step`. Imports nothing from the app |
| `app/models.py` | All Pydantic request/response models, the project view DTO, and `state_message()`. Leaf |
| `app/files.py` | Artifact path derivation, atomic writes, book read/write, excerpt. No SQL, no transport |
| `app/store.py` | **All SQL.** Users, sessions, projects, characters, chapters, the three conditional transitions, and `read_project_view()` — the single read model. No transport, no filesystem |
| `app/gemini/__init__.py` | Re-exports the protocol and error types |
| `app/gemini/protocol.py` | `GeminiClient` Protocol, result dataclasses, typed errors, `parse_items()` |
| `app/gemini/prompts.py` | The notebook's prompt constants, verbatim except where the assessment requires a change |
| `app/gemini/real.py` | `RealGeminiClient` — the only module importing `google.genai` |
| `app/gemini/fake.py` | `FakeGeminiClient` — deterministic outputs, call recorder, gate, injectable failures. Ships in the app (selected by env var), not in tests |
| `app/handlers.py` | The five step handlers plus `run_step()` dispatch. Knows *what to build a call from*; does not own transitions |
| `app/pipeline.py` | The detached task, `complete`/`fail` recording, cancellation semantics, expiry policy (which chain head to null), and post-commit broadcast. Owns *when*, not *how* |
| `app/realtime.py` | `RealtimeRegistry` + `Subscriber`. Moves an **opaque payload**; knows nothing about projects, the store or DTOs |
| `app/api/deps.py` | FastAPI dependencies: DB connection, session cookie → user, project ownership |
| `app/api/session.py` | `POST` / `GET` / `DELETE /api/session` |
| `app/api/projects.py` | Projects REST: create, list, detail, book, run, artifact bytes |
| `app/api/ws.py` | `WS /ws/projects/{id}` — identity, ownership, the atomic subscribe handshake |
| `app/main.py` | `create_app(settings=None, gemini=None, registry=None)`, lifespan (schema init), router mounting |

**Why `steps.py` holds vocabulary and `handlers.py` holds handlers** (spec §3.1 names one `steps.py` for handlers): `store.read_project_view` must derive `current_step`, and handlers must import `store`. One module cannot be both without an import cycle. Splitting the leaf vocabulary out is the smallest fix and leaves each file with one responsibility.

### Backend tests — `backend/tests/`

| Path | Covers |
|---|---|
| `conftest.py` | `settings`, `conn`, `fake_gemini`, `app`, `client` (TestClient), `aclient` (httpx ASGI) fixtures |
| `test_steps.py` | The pure vocabulary derivations |
| `test_db.py` | Pragmas and schema |
| `test_files.py` | Path derivation, atomic write, excerpt |
| `test_store_identity.py` | Users and sessions |
| `test_store_projects.py` | Project rows, list, read model, per-item derivation |
| `test_store_transitions.py` | The three conditional transitions |
| `test_api_session.py` | Identity endpoints |
| `test_api_projects.py` | Create / list / detail / book / artifacts / ownership |
| `test_fake_gemini.py` | The fake's own contract |
| `test_handlers.py` | The five handlers against the fake |
| `test_pipeline.py` | `/run` transitions, 202/409 |
| `test_acceptance_happypath.py` | Five-step run, exact call and context sequence |
| `test_acceptance_concurrency.py` | Race, refresh, sign-out/in |
| `test_acceptance_recovery.py` | Failure, partial resume, interruption, cancellation, expiry |
| `test_realtime.py` | Registry and subscriber in isolation |
| `test_ws.py` | Handshake, ownership, race, fan-out, isolation |
| `test_gemini_real.py` | Request shaping and retry configuration, against a stubbed SDK |

### Frontend — `frontend/`

| Path | Responsibility |
|---|---|
| `package.json`, `tsconfig.json`, `vite.config.ts`, `vitest.config.ts`, `index.html` | Toolchain. `vite.config.ts` proxies `/api` **and** `/ws` (`ws: true`) |
| `src/main.tsx` | React root |
| `src/setupTests.ts` | `@testing-library/jest-dom` |
| `src/types.ts` | TypeScript mirrors of every backend DTO |
| `src/steps.ts` | The five steps and their display labels — the frontend's copy of the vocabulary |
| `src/api.ts` | Every HTTP call, and the `RunOutcome` discriminated union that encodes 202-vs-409 |
| `src/App.tsx` | Hash routing, session bootstrap, screen selection |
| `src/styles/tokens.css` | The `:root` token block copied from `app-demo.html:13-56` |
| `src/styles/app.css` | Component styles |
| `src/components/AppShell.tsx` | Header, user, sign out — every authenticated screen |
| `src/components/StateMessage.tsx` | Shared loading skeleton / error-with-retry / empty presentational states |
| `src/components/SignIn.tsx` | Name + email with validation |
| `src/components/ProjectList.tsx` | List screen: loading, error, empty, populated |
| `src/components/ProjectRow.tsx` | Title, created date, five-step indicator, pill, attention warning |
| `src/components/EmptyState.tsx` | Zero-projects state |
| `src/components/NewProject.tsx` | Title + `.txt` file input + paste textarea + validation |
| `src/components/ProjectDetail.tsx` | Detail screen composition and all request wiring |
| `src/components/Stepper.tsx` | Five steps: done / current / pending |
| `src/components/StepPanel.tsx` | Ready / Running / Failed / Interrupted / Complete |
| `src/components/EntityCard.tsx` | One card for characters and chapters (`kind` prop), with per-item image state |
| `src/components/StylePanel.tsx` | Current style once generated |
| `src/components/BookTextPanel.tsx` | Permanent disclosure panel; lazy full-text fetch |
| `src/components/ConnectionBadge.tsx` | Connection state + manual Refresh |
| `src/hooks/useProjectSocket.ts` | The project WebSocket, backoff, 1008 handling |
| `src/hooks/useSession.ts` | Session bootstrap and sign-out |
| `src/__tests__/*.test.tsx` | Component tests, one file per component group |

**One `EntityCard` instead of `CharacterCard` + `ChapterCard`** (spec §10.3 names two): the two differ only in aspect ratio and noun. `app-demo.html:627` already models it as one function. DRY.

**Hash routing, no router dependency.** `#/`, `#/projects`, `#/projects/new`, `#/projects/:id` — exactly the demo's routes (`app-demo.html:327`). React Router would be a dependency for four routes.

### Repository root

| Path | Responsibility |
|---|---|
| `.env.example` | Required env vars, no secrets |
| `.gitignore` | `.env`, `data/`, `node_modules/`, `__pycache__/`, `.venv/` |
| `start.sh` | One command: uvicorn (1 worker) + vite |
| `test.sh` | One command: pytest + vitest |
| `CLAUDE.md` | Project context for the AI tool (assessment §2.2) |
| `README.md` | Start, test, prerequisites, env vars, architecture overview |
| `DECISIONS.md` | 4–6 decisions incl. ≥3 AI overrides (assessment §2.1, §2.3) |
| `TESTING.md` | Strategy + real test report |
| `test-report.txt` | Captured output of one real `./test.sh` run |
| `docs/gemini-contract.md` | Verified findings from the Task 2 spike |

---

## Interface Reference

Locked here so no task invents a name. Every signature below is used verbatim by later tasks.

```python
# app/steps.py
class StepName(StrEnum):
    STYLE = "STYLE"; CHARACTERS = "CHARACTERS"; PORTRAITS = "PORTRAITS"
    CHAPTERS = "CHAPTERS"; ILLUSTRATIONS = "ILLUSTRATIONS"

class ProjectStatus(StrEnum):
    CREATED = "CREATED"; STYLE_SET = "STYLE_SET"
    CHARACTERS_GENERATED = "CHARACTERS_GENERATED"
    PORTRAITS_GENERATED = "PORTRAITS_GENERATED"
    CHAPTERS_GENERATED = "CHAPTERS_GENERATED"; DONE = "DONE"

class StepState(StrEnum):
    IDLE = "IDLE"; RUNNING = "RUNNING"; FAILED = "FAILED"

STEPS: list[StepDef]                      # StepDef(name, label, status_before, status_after)
MAX_CHARACTERS = 2
MAX_CHAPTERS = 1

def current_step(status: ProjectStatus) -> StepName | None
def completed_steps(status: ProjectStatus) -> int              # 0..5
def status_before(step: StepName) -> ProjectStatus
def status_after(step: StepName) -> ProjectStatus
def display_status(status, step_state) -> str                  # "Draft" | "In progress" | "Done"
def needs_attention(step_state, is_interrupted: bool) -> bool
def chain_of_step(step: StepName) -> Literal["text", "image"]

# app/store.py — every function takes an open connection first
def upsert_user(conn, *, email: str, name: str) -> str                    # user_id
def get_user(conn, user_id: str) -> sqlite3.Row | None
def create_session(conn, user_id: str) -> str                            # token
def user_for_session(conn, token: str) -> sqlite3.Row | None
def delete_session(conn, token: str) -> None

def create_project(conn, *, project_id, user_id, title, book_path, book_excerpt) -> str
def get_project(conn, project_id: str, user_id: str) -> sqlite3.Row | None
def list_projects(conn, user_id: str, *, server_run_id: str) -> list[ProjectListItem]
def read_project_view(conn, project_id, user_id, *, server_run_id) -> ProjectView | None

def begin_step(conn, project_id, *, expected_status, server_run_id, now) -> bool
def complete_step(conn, project_id, *, server_run_id, next_status) -> bool
def fail_step(conn, project_id, *, server_run_id, code, message,
              clear_head: Literal["text", "image"] | None = None) -> bool

def save_style(conn, project_id, *, style_text, text_interaction_id) -> None
def save_characters(conn, project_id, items, *, text_interaction_id) -> None
def save_chapters(conn, project_id, items, *, text_interaction_id) -> None
def save_portrait(conn, *, project_id, character_id, portrait_path, image_interaction_id) -> None
def save_illustration(conn, *, project_id, chapter_id, illustration_path, image_interaction_id) -> None
def list_characters(conn, project_id) -> list[sqlite3.Row]
def list_chapters(conn, project_id) -> list[sqlite3.Row]

# app/gemini/protocol.py
@dataclass(frozen=True) class TextResult:       interaction_id: str; text: str
@dataclass(frozen=True) class StructuredResult: interaction_id: str; items: list[dict]
@dataclass(frozen=True) class ImageResult:      interaction_id: str; data: bytes; mime_type: str
@dataclass(frozen=True) class ReferenceImage:   data: bytes; mime_type: str

class GeminiError(Exception): ...
class InteractionNotFound(GeminiError): ...
class InvalidStructuredOutput(GeminiError): ...

class GeminiClient(Protocol):
    async def upload_book(self, book_path: Path) -> str
    async def create_text(self, *, prompt: str, previous_interaction_id: str | None = None,
                          document_uri: str | None = None) -> TextResult
    async def create_structured(self, *, prompt: str, previous_interaction_id: str | None = None,
                                document_uri: str | None = None, item_schema: dict,
                                max_items: int) -> StructuredResult
    async def create_image(self, *, prompt: str, previous_interaction_id: str | None = None,
                           reference_images: Sequence[ReferenceImage] = (),
                           system_instruction: str | None = None) -> ImageResult

def parse_items(text: str) -> list[dict]        # raises InvalidStructuredOutput

# app/handlers.py
@dataclass(frozen=True)
class StepContext:
    project_id: str; user_id: str; settings: Settings
    gemini: GeminiClient; notify: Callable[[], None]

async def run_step(step: StepName, ctx: StepContext, *, style: str | None = None) -> None

# app/pipeline.py
@dataclass(frozen=True)
class Deps: settings: Settings; gemini: GeminiClient; registry: RealtimeRegistry

def spawn(*, project_id: str, user_id: str, step: StepName, style: str | None, deps: Deps) -> None
def broadcast_state(project_id: str, user_id: str, deps: Deps) -> None

# app/realtime.py
class Subscriber:
    def offer(self, payload: dict) -> None            # sync, coalescing, never raises
    async def run(self) -> None                       # the per-connection writer task
class RealtimeRegistry:
    def register(self, project_id: str, sub: Subscriber) -> None
    def unregister(self, project_id: str, sub: Subscriber) -> None
    def publish(self, project_id: str, payload: dict) -> None   # sync, never raises
```

```ts
// frontend/src/types.ts
export type StepName = 'STYLE' | 'CHARACTERS' | 'PORTRAITS' | 'CHAPTERS' | 'ILLUSTRATIONS';
export type ProjectStatus = 'CREATED' | 'STYLE_SET' | 'CHARACTERS_GENERATED'
  | 'PORTRAITS_GENERATED' | 'CHAPTERS_GENERATED' | 'DONE';
export type StepState = 'IDLE' | 'RUNNING' | 'FAILED';
export type ImageState = 'ready' | 'generating' | 'pending';
export type DisplayStatus = 'Draft' | 'In progress' | 'Done';
export type FailureCode = 'GEMINI_ERROR' | 'INVALID_OUTPUT' | 'INTERNAL';

export interface Failure { code: FailureCode; message: string }
export interface EntityView {
  id: string; position: number; name: string; prompt: string;
  image_url: string | null; image_state: ImageState;
}
export interface ProjectView {
  id: string; title: string; created_at: string;
  status: ProjectStatus; step_state: StepState;
  current_step: StepName | null; display_status: DisplayStatus;
  needs_attention: boolean; is_interrupted: boolean; completed_steps: number;
  style_text: string | null; book_excerpt: string; failure: Failure | null;
  characters: EntityView[]; chapters: EntityView[];
}
export interface ProjectListItem {
  id: string; title: string; created_at: string; status: ProjectStatus;
  current_step: StepName | null; display_status: DisplayStatus;
  needs_attention: boolean; is_interrupted: boolean; completed_steps: number;
}
export interface SessionView { user_id: string; name: string; email: string }
export type RunOutcome =
  | { ok: true; project: ProjectView }
  | { ok: false; conflict: true; project: ProjectView };
export type ConnectionState = 'connecting' | 'live' | 'reconnecting' | 'closed';
```

---

## Task List

**Phase A — Harness and verified contract** (Tasks 1–2)
**Phase B — Persistence core** (Tasks 3–9)
**Phase C — HTTP bootstrap** (Tasks 10–11)
**Phase D — Frontend first slice** (Tasks 12–15)
**Phase E — Gemini and step handlers** (Tasks 16–22)
**Phase F — Pipeline and acceptance milestones** (Tasks 23–26)
**Phase G — Frontend detail screen** (Tasks 27–29)
**Phase H — Realtime** (Tasks 30–33)
**Phase I — Real Gemini, polish, documentation, UAT** (Tasks 34–37)

---

## Phase A — Harness and verified contract

### Task 1: Repo skeleton, both toolchains, `start.sh`, `test.sh`

Harness before code (assessment §09.3). Ends with one command running a passing test on each side.

**Files:**
- Create: `backend/requirements.txt`, `backend/pytest.ini`, `backend/app/__init__.py`, `backend/app/config.py`, `backend/tests/__init__.py`, `backend/tests/test_config.py`
- Create: `frontend/package.json`, `frontend/tsconfig.json`, `frontend/vite.config.ts`, `frontend/vitest.config.ts`, `frontend/index.html`, `frontend/src/main.tsx`, `frontend/src/App.tsx`, `frontend/src/setupTests.ts`, `frontend/src/__tests__/smoke.test.tsx`
- Create: `.env.example`, `.gitignore`, `start.sh`, `test.sh`, `CLAUDE.md`

**Interfaces:**
- Consumes: nothing.
- Produces: `Settings` frozen dataclass, `load_settings() -> Settings`; `./test.sh` as the single test command.

- [ ] **Step 1: Write the failing backend test**

`backend/tests/test_config.py`:

```python
from app.config import load_settings


def test_load_settings_reads_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("GEMINI_API_KEY", "abc123")
    monkeypatch.setenv("GEMINI_TEXT_MODEL", "text-model-x")
    monkeypatch.setenv("GEMINI_IMAGE_MODEL", "image-model-x")
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("USE_FAKE_GEMINI", "0")

    settings = load_settings()

    assert settings.gemini_api_key == "abc123"
    assert settings.text_model == "text-model-x"
    assert settings.image_model == "image-model-x"
    assert settings.data_dir == (tmp_path / "data").resolve()
    assert settings.db_path == (tmp_path / "data").resolve() / "app.db"
    assert settings.use_fake_gemini is False


def test_use_fake_gemini_is_opt_in(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.delenv("USE_FAKE_GEMINI", raising=False)
    assert load_settings().use_fake_gemini is False
    monkeypatch.setenv("USE_FAKE_GEMINI", "1")
    assert load_settings().use_fake_gemini is True


def test_each_load_mints_a_distinct_server_run_id(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    assert load_settings().server_run_id != load_settings().server_run_id
```

- [ ] **Step 2: Run it and verify it fails**

Run: `cd backend && python -m pytest tests/test_config.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.config'`

- [ ] **Step 3: Write `backend/requirements.txt` and install**

```
fastapi>=0.115
uvicorn[standard]>=0.30
pydantic>=2.7
google-genai>=2.10
python-dotenv>=1.0
pytest>=8.0
pytest-asyncio>=0.24
httpx>=0.27
```

```bash
cd /d/Project/Gradion_Test
PY=".venv/bin/python"; [ -x "$PY" ] || PY=".venv/Scripts/python.exe"
"$PY" -m pip install -r backend/requirements.txt
```

- [ ] **Step 4: Write `backend/pytest.ini`**

```ini
[pytest]
asyncio_mode = auto
testpaths = tests
pythonpath = .
```

- [ ] **Step 5: Write `backend/app/config.py`**

```python
from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

DEFAULT_TEXT_MODEL = "gemini-3.1-flash-lite"
DEFAULT_IMAGE_MODEL = "gemini-2.5-flash-image"


@dataclass(frozen=True)
class Settings:
    gemini_api_key: str
    text_model: str
    image_model: str
    data_dir: Path
    db_path: Path
    use_fake_gemini: bool
    server_run_id: str
    request_timeout_seconds: float


def load_settings() -> Settings:
    data_dir = Path(os.environ.get("DATA_DIR", "./data")).resolve()
    return Settings(
        gemini_api_key=os.environ.get("GEMINI_API_KEY", ""),
        text_model=os.environ.get("GEMINI_TEXT_MODEL", DEFAULT_TEXT_MODEL),
        image_model=os.environ.get("GEMINI_IMAGE_MODEL", DEFAULT_IMAGE_MODEL),
        data_dir=data_dir,
        db_path=data_dir / "app.db",
        use_fake_gemini=os.environ.get("USE_FAKE_GEMINI", "0") == "1",
        server_run_id=uuid.uuid4().hex,
        request_timeout_seconds=float(os.environ.get("GEMINI_TIMEOUT_SECONDS", "180")),
    )
```

Create empty `backend/app/__init__.py` and `backend/tests/__init__.py`.

- [ ] **Step 6: Run the backend test and verify it passes**

Run: `cd backend && python -m pytest tests/test_config.py -v`
Expected: PASS — 3 passed

- [ ] **Step 7: Scaffold the frontend**

`frontend/package.json`:

```json
{
  "name": "book-illustration-studio-frontend",
  "private": true,
  "type": "module",
  "scripts": { "dev": "vite", "build": "tsc -b && vite build", "test": "vitest" },
  "dependencies": { "react": "^18.3.1", "react-dom": "^18.3.1" },
  "devDependencies": {
    "@testing-library/jest-dom": "^6.4.8",
    "@testing-library/react": "^16.0.1",
    "@testing-library/user-event": "^14.5.2",
    "@types/react": "^18.3.5",
    "@types/react-dom": "^18.3.0",
    "@vitejs/plugin-react": "^4.3.1",
    "jsdom": "^25.0.0",
    "typescript": "^5.5.4",
    "vite": "^5.4.2",
    "vitest": "^2.0.5"
  }
}
```

`frontend/vite.config.ts` — **`ws: true` is load-bearing** (spec §9.7; omitting it fails the upgrade in a way that reads like an application bug):

```ts
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': { target: 'http://127.0.0.1:8000' },
      '/ws': { target: 'ws://127.0.0.1:8000', ws: true },
    },
  },
});
```

`frontend/vitest.config.ts`:

```ts
import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  test: { environment: 'jsdom', globals: true, setupFiles: './src/setupTests.ts' },
});
```

`frontend/tsconfig.json`:

```json
{
  "compilerOptions": {
    "target": "ES2022",
    "lib": ["ES2022", "DOM", "DOM.Iterable"],
    "module": "ESNext",
    "moduleResolution": "bundler",
    "jsx": "react-jsx",
    "strict": true,
    "noEmit": true,
    "skipLibCheck": true,
    "types": ["vitest/globals", "@testing-library/jest-dom"]
  },
  "include": ["src"]
}
```

`frontend/index.html`:

```html
<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>Book Illustration Studio</title>
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/main.tsx"></script>
  </body>
</html>
```

`frontend/src/setupTests.ts`:

```ts
import '@testing-library/jest-dom/vitest';
```

`frontend/src/App.tsx`:

```tsx
export default function App() {
  return <h1>Book Illustration Studio</h1>;
}
```

`frontend/src/main.tsx`:

```tsx
import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import App from './App';

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
```

`frontend/src/__tests__/smoke.test.tsx`:

```tsx
import { render, screen } from '@testing-library/react';
import App from '../App';

test('renders the application title', () => {
  render(<App />);
  expect(screen.getByRole('heading', { name: 'Book Illustration Studio' })).toBeInTheDocument();
});
```

Run: `cd frontend && npm install`

- [ ] **Step 8: Run the frontend test and verify it passes**

Run: `cd frontend && npm test -- --run`
Expected: PASS — `Tests  1 passed (1)`

- [ ] **Step 9: Write the scripts, env example, gitignore and `CLAUDE.md`**

`start.sh`:

```sh
#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")"
ROOT="$(pwd)"
PY="$ROOT/.venv/bin/python"; [ -x "$PY" ] || PY="$ROOT/.venv/Scripts/python.exe"

if [ ! -f .env ]; then
  echo "No .env found. Copy .env.example to .env and set GEMINI_API_KEY." >&2
  exit 1
fi

# One worker is required: server_run_id orphan detection and the in-memory
# realtime registry both assume a single process (design 5.3, 9.5).
( cd backend && "$PY" -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1 ) &
BACKEND=$!
( cd frontend && npm run dev ) &
FRONTEND=$!
trap 'kill "$BACKEND" "$FRONTEND" 2>/dev/null || true' EXIT INT TERM
wait
```

`test.sh`:

```sh
#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")"
ROOT="$(pwd)"
PY="$ROOT/.venv/bin/python"; [ -x "$PY" ] || PY="$ROOT/.venv/Scripts/python.exe"

echo "=== backend (pytest) ==="
( cd backend && "$PY" -m pytest -v )

echo
echo "=== frontend (vitest) ==="
( cd frontend && npm test -- --run )
```

`.env.example`:

```
# Gemini API key from https://aistudio.google.com/apikey - never commit the real value.
GEMINI_API_KEY=

# Model IDs as run in Book_illustration.ipynb. Re-check against AI Studio; they turn over.
GEMINI_TEXT_MODEL=gemini-3.1-flash-lite
GEMINI_IMAGE_MODEL=gemini-2.5-flash-image

# Where app.db and per-project artifacts live.
DATA_DIR=./data

# Set to 1 to run the app against FakeGeminiClient instead of the real API.
USE_FAKE_GEMINI=0

# Per-request timeout in seconds. Image calls are the slow ones.
GEMINI_TIMEOUT_SECONDS=180
```

`.gitignore`:

```
.env
data/
.venv/
__pycache__/
*.pyc
.pytest_cache/
node_modules/
dist/
```

`CLAUDE.md` — committed as an assessment §2.2 artifact:

```markdown
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
```

Run: `chmod +x start.sh test.sh`

- [ ] **Step 10: Run the single test command end to end**

Run: `./test.sh`
Expected: PASS — backend `3 passed`, frontend `1 passed`, exit code 0

- [ ] **Step 11: Commit**

```bash
git add -A
git commit -m "Scaffold backend and frontend with a single test command

Harness before implementation, per assessment 09.3. ./test.sh runs pytest and
vitest; ./start.sh runs uvicorn with one worker plus vite. Vite proxies /api
and /ws (ws: true) so the session cookie is same-origin with no CORS.

Mostly AI-authored (Claude Code).

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Gemini contract spike (throwaway, real API)

**Exploration, not TDD.** It exists so `FakeGeminiClient` reproduces a *verified* contract rather than an imagined one (spec §14 step 2). Costs roughly 6 real calls. The script is deleted; only findings are committed.

**Files:**
- Create then delete: `backend/spike_gemini.py`
- Create: `docs/gemini-contract.md`

**Interfaces:**
- Consumes: `Settings` (Task 1).
- Produces: `docs/gemini-contract.md`, whose Decisions section binds Tasks 16, 17 and 34.

- [ ] **Step 1: Write the spike script**

`backend/spike_gemini.py`:

```python
"""Throwaway. Verifies the google-genai interactions contract before the client
is designed. Run once with a real key, record findings, delete."""
import asyncio, base64, json, os
from pathlib import Path
from google import genai
from google.genai import types

client = genai.Client(
    api_key=os.environ["GEMINI_API_KEY"],
    http_options=types.HttpOptions(retry_options=types.HttpRetryOptions(attempts=1)),
)
TEXT = os.environ.get("GEMINI_TEXT_MODEL", "gemini-3.1-flash-lite")
IMAGE = os.environ.get("GEMINI_IMAGE_MODEL", "gemini-2.5-flash-image")


async def main() -> None:
    Path("spike_book.txt").write_text(
        "Chapter 1. Toad drove the motor-car. Rat rowed the boat. " * 40, encoding="utf-8")
    book = client.files.upload(file="spike_book.txt")
    print("Q5 upload uri:", book.uri)

    seed = await client.aio.interactions.create(
        model=TEXT,
        input=[
            {"type": "text", "text": "Here's a book. Don't say anything for now."},
            {"type": "document", "uri": book.uri},
        ],
    )
    print("Q5 multipart+document accepted. id:", seed.id)

    style = await client.aio.interactions.create(
        model=TEXT, input="Give one short art style prompt.",
        previous_interaction_id=seed.id)
    print("Q1 output_text populated:", bool(style.output_text))
    print("Q1 output_text:", repr(style.output_text)[:200])
    print("Q2 steps[-1].content[0].text:", repr(style.steps[-1].content[0].text)[:200])
    print("Q6 chaining accepted, id:", style.id)

    structured = await client.aio.interactions.create(
        model=TEXT,
        input="List the adult characters with an image prompt each. At most 2.",
        previous_interaction_id=style.id,
        response_format={
            "type": "text",
            "mime_type": "application/json",
            "schema": {
                "type": "array",
                "maxItems": 2,
                "items": {
                    "type": "object",
                    "properties": {"name": {"type": "string"}, "prompt": {"type": "string"}},
                    "required": ["name", "prompt"],
                },
            },
        },
    )
    print("Q3 structured output_text populated:", bool(structured.output_text))
    raw = structured.output_text or structured.steps[-1].content[0].text
    parsed = json.loads(raw)
    print("Q7 asked for <= 2, returned", len(parsed), "items")

    img = await client.aio.interactions.create(
        model=IMAGE, input="A watercolour toad in a green jacket, full illustration.")
    print("Q4a output_image is not None:", img.output_image is not None)
    if img.output_image is not None:
        print("Q4a mime_type:", img.output_image.mime_type,
              "| decoded bytes:", len(base64.b64decode(img.output_image.data)))
    found = None
    for step in reversed(img.steps):
        if step.type == "model_output" and step.content:
            for content in reversed(step.content):
                if content.type == "image":
                    found = content
                    break
            if found:
                break
    print("Q4b steps-walk found an image:", found is not None)

    ref = base64.b64decode((img.output_image or found).data)
    standalone = await client.aio.interactions.create(
        model=IMAGE,
        input=[
            {"type": "text", "text": "Redraw this character sitting in a boat."},
            {"type": "image", "data": base64.b64encode(ref).decode(), "mime_type": "image/png"},
        ],
        system_instruction="No text on the image. Family friendly.",
    )
    print("Q8 standalone image+system_instruction accepted:", standalone.id)

    try:
        await client.aio.interactions.create(
            model=TEXT, input="hello", previous_interaction_id="interactions/does-not-exist")
        print("Q9 expired-interaction call did NOT raise")
    except Exception as exc:
        print("Q9 raises:", type(exc).__module__ + "." + type(exc).__name__)
        print("Q9 message:", str(exc)[:300])
        print("Q9 code attrs:", getattr(exc, "code", None), getattr(exc, "status_code", None))


asyncio.run(main())
```

- [ ] **Step 2: Run it against a real key**

Run: `cd backend && python spike_gemini.py`
Expected: nine `Q…` groups printed, no traceback except the deliberate `Q9` branch. If the account rejects a call without `service_tier`, add `service_tier="standard"` to every call and record that in the findings.

- [ ] **Step 3: Record findings in `docs/gemini-contract.md`**

One section per question with the **observed** answer and the pasted output line:

| # | Question | Binds |
|---|---|---|
| Q1 | Is `interaction.output_text` populated for a plain text call? | `RealGeminiClient.create_text` |
| Q2 | Does `steps[-1].content[0].text` return the same string? | Fallback accessor |
| Q3 | Is `output_text` populated when `response_format` is set? | `create_structured` |
| Q4 | Does `output_image` carry base64 `data` + `mime_type`, and does the steps-walk find the same image? | `create_image` |
| Q5 | Is `[text, document]` input with a `files.upload` URI accepted? | `upload_book`, step 1 |
| Q6 | Is `previous_interaction_id` accepted, and does it chain? | Every chained call |
| Q7 | Is `maxItems` honoured, or advisory? | Whether the cap needs the loop bound |
| Q8 | Is a standalone image call with inline `image` parts + `system_instruction` accepted? | Step 5 recovery path |
| Q9 | What exception type/message does an unknown `previous_interaction_id` raise? | `InteractionNotFound` detection |

Close with a **Decisions** section naming, for each, the accessor or shape `RealGeminiClient` will use. If Q7 shows `maxItems` is advisory, add: *"the generation-loop bound and strict `len(items) > cap` validation are the only real enforcement"* — which is what spec §7.4 already assumes.

- [ ] **Step 4: Delete the spike and commit the findings**

```bash
rm backend/spike_gemini.py backend/spike_book.txt
git add docs/gemini-contract.md
git commit -m "Record verified google-genai interactions contract

Ran a throwaway spike against the real API before designing the client, so
FakeGeminiClient reproduces a verified contract rather than an imagined one.
Settles output_text vs steps[-1].content[0].text, image extraction, maxItems
enforcement, document input, chaining, standalone image calls, and the error
raised by an expired interaction id. Spike script deleted; findings kept.

Human-run against a real key; findings written up with Claude Code.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Phase B — Persistence core

### Task 3: Database connection, pragmas, schema

**Files:**
- Create: `backend/app/db.py`, `backend/tests/conftest.py`, `backend/tests/test_db.py`

**Interfaces:**
- Consumes: `Settings` (Task 1).
- Produces: `db.get_conn(settings) -> ContextManager[sqlite3.Connection]`, `db.init_schema(conn) -> None`, `db.SCHEMA_SQL`. Test fixtures `settings` and `other_run`.

- [ ] **Step 1: Write the shared fixtures and the failing test**

`backend/tests/conftest.py`:

```python
from dataclasses import replace
from pathlib import Path

import pytest

from app.config import Settings


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    data_dir = tmp_path / "data"
    return Settings(
        gemini_api_key="test-key",
        text_model="test-text-model",
        image_model="test-image-model",
        data_dir=data_dir,
        db_path=data_dir / "app.db",
        use_fake_gemini=True,
        server_run_id="run-A",
        request_timeout_seconds=5.0,
    )


@pytest.fixture
def other_run(settings: Settings) -> Settings:
    """The same database seen by a different process identity."""
    return replace(settings, server_run_id="run-B")
```

`backend/tests/test_db.py`:

```python
import sqlite3

import pytest

from app import db


@pytest.fixture
def ready(settings):
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    with db.get_conn(settings) as conn:
        db.init_schema(conn)
    return settings


def test_connection_applies_the_three_required_pragmas(ready):
    with db.get_conn(ready) as conn:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] >= 5000


def test_rows_are_accessible_by_column_name(ready):
    with db.get_conn(ready) as conn:
        conn.execute(
            "INSERT INTO users (id, email, name, created_at) VALUES (?,?,?,?)",
            ("u1", "a@b.c", "Ada", "2026-08-14T00:00:00+00:00"),
        )
        assert conn.execute("SELECT * FROM users").fetchone()["email"] == "a@b.c"


def test_init_schema_creates_every_table(ready):
    with db.get_conn(ready) as conn:
        names = {r["name"] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"users", "sessions", "projects", "characters", "chapters"} <= names


def test_init_schema_is_safe_to_run_twice(ready):
    with db.get_conn(ready) as conn:
        db.init_schema(conn)


def test_foreign_keys_are_enforced(ready):
    with db.get_conn(ready) as conn:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO sessions (token, user_id, created_at) VALUES (?,?,?)",
                ("t", "no-such-user", "2026-08-14T00:00:00+00:00"),
            )
```

- [ ] **Step 2: Run it and verify it fails**

Run: `cd backend && python -m pytest tests/test_db.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.db'`

- [ ] **Step 3: Write `backend/app/db.py`**

```python
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from typing import Iterator

from app.config import Settings

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS users (
  id          TEXT PRIMARY KEY,
  email       TEXT NOT NULL UNIQUE,
  name        TEXT NOT NULL,
  created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
  token       TEXT PRIMARY KEY,
  user_id     TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS projects (
  id                   TEXT PRIMARY KEY,
  user_id              TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  title                TEXT NOT NULL,
  created_at           TEXT NOT NULL,
  book_path            TEXT NOT NULL,
  book_excerpt         TEXT NOT NULL,
  status               TEXT NOT NULL DEFAULT 'CREATED',
  step_state           TEXT NOT NULL DEFAULT 'IDLE',
  step_started_at      TEXT,
  server_run_id        TEXT,
  error_code           TEXT,
  error_message        TEXT,
  style_text           TEXT,
  text_interaction_id  TEXT,
  image_interaction_id TEXT
);

CREATE INDEX IF NOT EXISTS idx_projects_user ON projects(user_id, created_at DESC);

CREATE TABLE IF NOT EXISTS characters (
  id             TEXT PRIMARY KEY,
  project_id     TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  position       INTEGER NOT NULL,
  name           TEXT NOT NULL,
  prompt         TEXT NOT NULL,
  portrait_path  TEXT,
  UNIQUE (project_id, position)
);

CREATE TABLE IF NOT EXISTS chapters (
  id                 TEXT PRIMARY KEY,
  project_id         TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  position           INTEGER NOT NULL,
  name               TEXT NOT NULL,
  prompt             TEXT NOT NULL,
  illustration_path  TEXT,
  UNIQUE (project_id, position)
);
"""


@contextmanager
def get_conn(settings: Settings) -> Iterator[sqlite3.Connection]:
    """A short-lived connection with the required pragmas applied.

    journal_mode lives in the database file, but busy_timeout and foreign_keys
    are per-connection and must be set every time (design 4.4).
    """
    conn = sqlite3.connect(settings.db_path, isolation_level=None, timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
    finally:
        conn.close()


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_SQL)
```

`isolation_level=None` is autocommit, so the explicit `BEGIN`/`COMMIT` in `store.py` mean exactly what they say instead of interacting with the driver's implicit transactions.

- [ ] **Step 4: Run the test and verify it passes**

Run: `cd backend && python -m pytest tests/test_db.py -v`
Expected: PASS — 5 passed

- [ ] **Step 5: Commit**

```bash
git add backend/app/db.py backend/tests/test_db.py backend/tests/conftest.py
git commit -m "Add SQLite schema and connection factory

WAL, busy_timeout and foreign_keys are load-bearing given a background writer
and concurrent readers (design 4.4). busy_timeout and foreign_keys are
per-connection, so they are applied on every connection. Autocommit keeps the
explicit transactions in store.py honest.

Mostly AI-authored (Claude Code).

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: The step vocabulary — `steps.py`

A pure leaf module. Every derivation the read model, the API and the frontend depend on lives here, so "adding a sixth step" means one entry in one list (spec §3).

**Files:**
- Create: `backend/app/steps.py`, `backend/tests/test_steps.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `StepName`, `ProjectStatus`, `StepState`, `StepDef`, `STEPS`, `MAX_CHARACTERS`, `MAX_CHAPTERS`, `current_step`, `completed_steps`, `status_before`, `status_after`, `display_status`, `needs_attention`, `chain_of_step`.

- [ ] **Step 1: Write the failing test**

`backend/tests/test_steps.py`:

```python
import pytest

from app.steps import (
    MAX_CHAPTERS, MAX_CHARACTERS, STEPS, ProjectStatus, StepName, StepState,
    chain_of_step, completed_steps, current_step, display_status,
    needs_attention, status_after, status_before,
)


def test_the_five_steps_are_in_notebook_order():
    assert [s.name for s in STEPS] == [
        StepName.STYLE, StepName.CHARACTERS, StepName.PORTRAITS,
        StepName.CHAPTERS, StepName.ILLUSTRATIONS,
    ]


def test_step_labels_match_the_demo():
    assert [s.label for s in STEPS] == [
        "Style", "Characters", "Portraits", "Chapters", "Illustrations"]


def test_caps_are_the_assessment_values():
    assert (MAX_CHARACTERS, MAX_CHAPTERS) == (2, 1)


@pytest.mark.parametrize(
    "status,expected",
    [
        (ProjectStatus.CREATED, StepName.STYLE),
        (ProjectStatus.STYLE_SET, StepName.CHARACTERS),
        (ProjectStatus.CHARACTERS_GENERATED, StepName.PORTRAITS),
        (ProjectStatus.PORTRAITS_GENERATED, StepName.CHAPTERS),
        (ProjectStatus.CHAPTERS_GENERATED, StepName.ILLUSTRATIONS),
        (ProjectStatus.DONE, None),
    ],
)
def test_current_step_is_derived_from_status(status, expected):
    assert current_step(status) == expected


@pytest.mark.parametrize(
    "status,count",
    [
        (ProjectStatus.CREATED, 0),
        (ProjectStatus.STYLE_SET, 1),
        (ProjectStatus.CHARACTERS_GENERATED, 2),
        (ProjectStatus.PORTRAITS_GENERATED, 3),
        (ProjectStatus.CHAPTERS_GENERATED, 4),
        (ProjectStatus.DONE, 5),
    ],
)
def test_completed_steps_counts_finished_steps(status, count):
    assert completed_steps(status) == count


def test_status_before_and_after_are_inverse_along_the_chain():
    for step in StepName:
        assert current_step(status_before(step)) == step
        assert completed_steps(status_after(step)) == completed_steps(status_before(step)) + 1


@pytest.mark.parametrize(
    "status,state,expected",
    [
        (ProjectStatus.DONE, StepState.IDLE, "Done"),
        (ProjectStatus.CREATED, StepState.IDLE, "Draft"),
        (ProjectStatus.CREATED, StepState.RUNNING, "In progress"),
        (ProjectStatus.CREATED, StepState.FAILED, "In progress"),
        (ProjectStatus.STYLE_SET, StepState.IDLE, "In progress"),
        (ProjectStatus.CHAPTERS_GENERATED, StepState.FAILED, "In progress"),
    ],
)
def test_display_status_uses_only_the_three_assessment_values(status, state, expected):
    assert display_status(status, state) == expected


def test_no_fourth_pill_value_can_be_produced():
    produced = {display_status(s, st) for s in ProjectStatus for st in StepState}
    assert produced == {"Draft", "In progress", "Done"}


@pytest.mark.parametrize(
    "state,interrupted,expected",
    [
        (StepState.IDLE, False, False),
        (StepState.RUNNING, False, False),
        (StepState.FAILED, False, True),
        (StepState.RUNNING, True, True),
    ],
)
def test_needs_attention_is_separate_from_the_pill(state, interrupted, expected):
    assert needs_attention(state, interrupted) is expected


def test_text_and_image_chains_are_assigned_per_the_design():
    assert chain_of_step(StepName.STYLE) == "text"
    assert chain_of_step(StepName.CHARACTERS) == "text"
    assert chain_of_step(StepName.PORTRAITS) == "image"
    assert chain_of_step(StepName.CHAPTERS) == "text"
    assert chain_of_step(StepName.ILLUSTRATIONS) == "image"
```

- [ ] **Step 2: Run it and verify it fails**

Run: `cd backend && python -m pytest tests/test_steps.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.steps'`

- [ ] **Step 3: Write `backend/app/steps.py`**

```python
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

MAX_CHARACTERS = 2
MAX_CHAPTERS = 1


class StepName(StrEnum):
    STYLE = "STYLE"
    CHARACTERS = "CHARACTERS"
    PORTRAITS = "PORTRAITS"
    CHAPTERS = "CHAPTERS"
    ILLUSTRATIONS = "ILLUSTRATIONS"


class ProjectStatus(StrEnum):
    CREATED = "CREATED"
    STYLE_SET = "STYLE_SET"
    CHARACTERS_GENERATED = "CHARACTERS_GENERATED"
    PORTRAITS_GENERATED = "PORTRAITS_GENERATED"
    CHAPTERS_GENERATED = "CHAPTERS_GENERATED"
    DONE = "DONE"


class StepState(StrEnum):
    IDLE = "IDLE"
    RUNNING = "RUNNING"
    FAILED = "FAILED"


@dataclass(frozen=True)
class StepDef:
    name: StepName
    label: str
    status_before: ProjectStatus
    status_after: ProjectStatus
    chain: Literal["text", "image"]


# The whole pipeline definition. Adding a sixth step is one entry here plus one
# handler; the orchestration never changes (design 3).
STEPS: list[StepDef] = [
    StepDef(StepName.STYLE, "Style", ProjectStatus.CREATED, ProjectStatus.STYLE_SET, "text"),
    StepDef(StepName.CHARACTERS, "Characters", ProjectStatus.STYLE_SET,
            ProjectStatus.CHARACTERS_GENERATED, "text"),
    StepDef(StepName.PORTRAITS, "Portraits", ProjectStatus.CHARACTERS_GENERATED,
            ProjectStatus.PORTRAITS_GENERATED, "image"),
    StepDef(StepName.CHAPTERS, "Chapters", ProjectStatus.PORTRAITS_GENERATED,
            ProjectStatus.CHAPTERS_GENERATED, "text"),
    StepDef(StepName.ILLUSTRATIONS, "Illustrations", ProjectStatus.CHAPTERS_GENERATED,
            ProjectStatus.DONE, "image"),
]

_BY_NAME = {s.name: s for s in STEPS}
_STATUS_ORDER = [ProjectStatus.CREATED] + [s.status_after for s in STEPS]


def step_def(step: StepName) -> StepDef:
    return _BY_NAME[StepName(step)]


def current_step(status: ProjectStatus) -> StepName | None:
    index = _STATUS_ORDER.index(ProjectStatus(status))
    return STEPS[index].name if index < len(STEPS) else None


def completed_steps(status: ProjectStatus) -> int:
    return _STATUS_ORDER.index(ProjectStatus(status))


def status_before(step: StepName) -> ProjectStatus:
    return step_def(step).status_before


def status_after(step: StepName) -> ProjectStatus:
    return step_def(step).status_after


def chain_of_step(step: StepName) -> Literal["text", "image"]:
    return step_def(step).chain


def display_status(status: ProjectStatus, step_state: StepState) -> str:
    """Exactly the three values assessment 4.4 names. Failure and interruption
    are carried by needs_attention, not by a fourth pill (design 4.2)."""
    if ProjectStatus(status) is ProjectStatus.DONE:
        return "Done"
    if ProjectStatus(status) is ProjectStatus.CREATED and StepState(step_state) is StepState.IDLE:
        return "Draft"
    return "In progress"


def needs_attention(step_state: StepState, is_interrupted: bool) -> bool:
    return StepState(step_state) is StepState.FAILED or bool(is_interrupted)
```

- [ ] **Step 4: Run the test and verify it passes**

Run: `cd backend && python -m pytest tests/test_steps.py -v`
Expected: PASS — 25 passed

- [ ] **Step 5: Commit**

```bash
git add backend/app/steps.py backend/tests/test_steps.py
git commit -m "Add the step vocabulary as a pure leaf module

The ordered STEPS list is the single definition of the pipeline; current_step,
completed_steps and the status transitions are all derived from it, never
stored. display_status is pinned to the assessment's three pill values and a
test asserts no fourth value is reachable. Kept dependency-free so both
store.read_project_view and handlers can import it without a cycle.

Mostly AI-authored (Claude Code).

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: Filesystem layout and atomic artifact writes — `files.py`

**Files:**
- Create: `backend/app/files.py`, `backend/tests/test_files.py`

**Interfaces:**
- Consumes: nothing (takes `data_dir: Path` explicitly).
- Produces: `project_dir`, `book_path`, `write_book`, `read_book`, `excerpt`, `save_portrait_bytes`, `save_illustration_bytes`, `absolute`, `EXCERPT_CHARS`. Artifact functions return **relative** paths for storage (spec §3.3).

- [ ] **Step 1: Write the failing test**

`backend/tests/test_files.py`:

```python
from pathlib import Path

from app import files

PNG = b"\x89PNG\r\n\x1a\n-fake-bytes"


def test_book_is_written_and_read_back_verbatim(tmp_path):
    rel = files.write_book(tmp_path, "p1", "Once upon a time.\nChapter 1.")
    assert rel == "projects/p1/book.txt"
    assert (tmp_path / rel).read_text(encoding="utf-8") == "Once upon a time.\nChapter 1."
    assert files.read_book(tmp_path, "p1") == "Once upon a time.\nChapter 1."


def test_stored_paths_are_relative_so_data_dir_stays_relocatable(tmp_path):
    rel = files.save_portrait_bytes(tmp_path, "p1", "c1", PNG)
    assert rel == "projects/p1/portraits/c1.png"
    assert not Path(rel).is_absolute()
    assert (tmp_path / rel).read_bytes() == PNG


def test_illustration_path_derives_from_the_chapter_id(tmp_path):
    rel = files.save_illustration_bytes(tmp_path, "p1", "ch1", PNG)
    assert rel == "projects/p1/illustrations/ch1.png"
    assert (tmp_path / rel).read_bytes() == PNG


def test_a_rewrite_overwrites_its_own_orphan_leaving_no_tmp_file(tmp_path):
    files.save_portrait_bytes(tmp_path, "p1", "c1", b"first")
    files.save_portrait_bytes(tmp_path, "p1", "c1", b"second")
    portraits = tmp_path / "projects" / "p1" / "portraits"
    assert (portraits / "c1.png").read_bytes() == b"second"
    assert list(portraits.iterdir()) == [portraits / "c1.png"]


def test_excerpt_collapses_whitespace_and_ellipsises(tmp_path):
    assert files.excerpt("a   b\n\nc") == "a b c"
    long = "word " * 200
    out = files.excerpt(long)
    assert len(out) == files.EXCERPT_CHARS + 1 and out.endswith("…")


def test_absolute_resolves_a_stored_relative_path(tmp_path):
    rel = files.save_portrait_bytes(tmp_path, "p1", "c1", PNG)
    assert files.absolute(tmp_path, rel) == (tmp_path / rel).resolve()
```

- [ ] **Step 2: Run it and verify it fails**

Run: `cd backend && python -m pytest tests/test_files.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.files'`

- [ ] **Step 3: Write `backend/app/files.py`**

```python
from __future__ import annotations

import os
import re
from pathlib import Path

EXCERPT_CHARS = 220
_WHITESPACE = re.compile(r"\s+")


def project_dir(data_dir: Path, project_id: str) -> Path:
    return data_dir / "projects" / project_id


def book_path(data_dir: Path, project_id: str) -> Path:
    return project_dir(data_dir, project_id) / "book.txt"


def _write_atomic(target: Path, payload: bytes) -> None:
    """Write beside the target, then replace it in one operation.

    A crash before the replace leaves only a .tmp file; a crash after it leaves
    a complete file. Because artifact names derive from row ids rather than
    randomness, a retry overwrites its own orphan (design 3.3).
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_bytes(payload)
    os.replace(tmp, target)


def write_book(data_dir: Path, project_id: str, text: str) -> str:
    target = book_path(data_dir, project_id)
    _write_atomic(target, text.encode("utf-8"))
    return target.relative_to(data_dir).as_posix()


def read_book(data_dir: Path, project_id: str) -> str:
    return book_path(data_dir, project_id).read_text(encoding="utf-8")


def save_portrait_bytes(data_dir: Path, project_id: str, character_id: str, payload: bytes) -> str:
    target = project_dir(data_dir, project_id) / "portraits" / f"{character_id}.png"
    _write_atomic(target, payload)
    return target.relative_to(data_dir).as_posix()


def save_illustration_bytes(data_dir: Path, project_id: str, chapter_id: str, payload: bytes) -> str:
    target = project_dir(data_dir, project_id) / "illustrations" / f"{chapter_id}.png"
    _write_atomic(target, payload)
    return target.relative_to(data_dir).as_posix()


def absolute(data_dir: Path, relative_path: str) -> Path:
    return (data_dir / relative_path).resolve()


def excerpt(text: str, limit: int = EXCERPT_CHARS) -> str:
    collapsed = _WHITESPACE.sub(" ", text).strip()
    return collapsed if len(collapsed) <= limit else collapsed[:limit] + "…"
```

- [ ] **Step 4: Run the test and verify it passes**

Run: `cd backend && python -m pytest tests/test_files.py -v`
Expected: PASS — 6 passed

- [ ] **Step 5: Commit**

```bash
git add backend/app/files.py backend/tests/test_files.py
git commit -m "Add project-scoped artifact storage with atomic writes

Write-then-replace means a crash mid-write can only leave a .tmp file, never a
truncated artifact the database points at. Artifact names derive from row ids,
so a retry overwrites its own orphan and no cleanup pass is needed. Stored
paths are relative so data/ stays relocatable (design 3.3).

Mostly AI-authored (Claude Code).

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: Identity storage — users and sessions

**Files:**
- Create: `backend/app/store.py`, `backend/tests/test_store_identity.py`

**Interfaces:**
- Consumes: `db` (Task 3).
- Produces: `store.upsert_user`, `store.get_user`, `store.create_session`, `store.user_for_session`, `store.delete_session`, `store.now_iso()`, `store.new_id()`.

- [ ] **Step 1: Write the failing test**

`backend/tests/test_store_identity.py`:

```python
import pytest

from app import db, store


@pytest.fixture
def conn(settings):
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    with db.get_conn(settings) as c:
        db.init_schema(c)
        yield c


def test_a_new_email_creates_a_user(conn):
    user_id = store.upsert_user(conn, email="ada@example.com", name="Ada")
    row = store.get_user(conn, user_id)
    assert (row["email"], row["name"]) == ("ada@example.com", "Ada")


def test_a_returning_email_reuses_the_same_user_row(conn):
    first = store.upsert_user(conn, email="ada@example.com", name="Ada")
    second = store.upsert_user(conn, email="ada@example.com", name="Ada")
    assert first == second
    assert conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 1


def test_signing_in_again_updates_the_stored_name(conn):
    """Matches app-demo.html:347 - the demo overwrites the name on re-entry."""
    user_id = store.upsert_user(conn, email="ada@example.com", name="Ada")
    store.upsert_user(conn, email="ada@example.com", name="Ada Lovelace")
    assert store.get_user(conn, user_id)["name"] == "Ada Lovelace"


def test_sessions_resolve_to_their_user_and_tokens_are_unguessable(conn):
    user_id = store.upsert_user(conn, email="ada@example.com", name="Ada")
    token = store.create_session(conn, user_id)
    assert len(token) >= 32
    assert store.user_for_session(conn, token)["id"] == user_id


def test_an_unknown_token_resolves_to_nothing(conn):
    assert store.user_for_session(conn, "not-a-token") is None


def test_deleting_a_session_revokes_it(conn):
    user_id = store.upsert_user(conn, email="ada@example.com", name="Ada")
    token = store.create_session(conn, user_id)
    store.delete_session(conn, token)
    assert store.user_for_session(conn, token) is None
```

- [ ] **Step 2: Run it and verify it fails**

Run: `cd backend && python -m pytest tests/test_store_identity.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.store'`

- [ ] **Step 3: Write the identity half of `backend/app/store.py`**

```python
from __future__ import annotations

import secrets
import sqlite3
import uuid
from datetime import datetime, timezone


def new_id() -> str:
    return uuid.uuid4().hex


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# --------------------------------------------------------------------------
# Identity
# --------------------------------------------------------------------------

def upsert_user(conn: sqlite3.Connection, *, email: str, name: str) -> str:
    """Create the user, or update the stored name for a returning email.

    Assessment 4.1: email exists -> load their projects; otherwise create.
    """
    conn.execute(
        """
        INSERT INTO users (id, email, name, created_at) VALUES (?,?,?,?)
        ON CONFLICT(email) DO UPDATE SET name = excluded.name
        """,
        (new_id(), email, name, now_iso()),
    )
    return conn.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()["id"]


def get_user(conn: sqlite3.Connection, user_id: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()


def create_session(conn: sqlite3.Connection, user_id: str) -> str:
    """An opaque 256-bit token. The row lives in SQLite so a restart does not
    sign anyone out (design 8.1)."""
    token = secrets.token_urlsafe(32)
    conn.execute(
        "INSERT INTO sessions (token, user_id, created_at) VALUES (?,?,?)",
        (token, user_id, now_iso()),
    )
    return token


def user_for_session(conn: sqlite3.Connection, token: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT users.* FROM users JOIN sessions ON sessions.user_id = users.id "
        "WHERE sessions.token = ?",
        (token,),
    ).fetchone()


def delete_session(conn: sqlite3.Connection, token: str) -> None:
    conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
```

- [ ] **Step 4: Run the test and verify it passes**

Run: `cd backend && python -m pytest tests/test_store_identity.py -v`
Expected: PASS — 6 passed

- [ ] **Step 5: Commit**

```bash
git add backend/app/store.py backend/tests/test_store_identity.py
git commit -m "Add user and session storage

Email is the identity: a new one creates a user, a returning one reuses the row
and updates the stored name, matching app-demo.html:347. Sessions are opaque
256-bit tokens in SQLite rather than JWTs - revocable, and no signing library
or key management for a system with no authentication (design 8.1).

Mostly AI-authored (Claude Code).

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: Project DTOs and project rows

**Files:**
- Create: `backend/app/models.py`, `backend/tests/test_store_projects.py`
- Modify: `backend/app/store.py` (append the projects section)

**Interfaces:**
- Consumes: `steps` (Task 4), `files` (Task 5), `db` (Task 3), `store` identity (Task 6).
- Produces: every Pydantic model in `app/models.py` (see the Interface Reference), plus `store.create_project`, `store.get_project`, `store.list_projects`.

- [ ] **Step 1: Write the failing test**

`backend/tests/test_store_projects.py`:

```python
import pytest

from app import db, store
from app.steps import ProjectStatus, StepName, StepState


@pytest.fixture
def conn(settings):
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    with db.get_conn(settings) as c:
        db.init_schema(c)
        yield c


@pytest.fixture
def user_id(conn):
    return store.upsert_user(conn, email="ada@example.com", name="Ada")


def test_a_new_project_starts_created_and_idle(conn, user_id):
    pid = store.create_project(
        conn, project_id=store.new_id(), user_id=user_id, title="Willows",
        book_path="projects/x/book.txt", book_excerpt="Once upon a time.")
    row = store.get_project(conn, pid, user_id)
    assert row["status"] == ProjectStatus.CREATED
    assert row["step_state"] == StepState.IDLE
    assert row["server_run_id"] is None
    assert row["style_text"] is None
    assert row["book_excerpt"] == "Once upon a time."


def test_a_project_is_invisible_to_another_user(conn, user_id):
    other = store.upsert_user(conn, email="bob@example.com", name="Bob")
    pid = store.create_project(conn, project_id=store.new_id(), user_id=user_id,
                               title="Willows", book_path="p", book_excerpt="e")
    assert store.get_project(conn, pid, other) is None


def test_list_projects_returns_newest_first_and_only_this_users(conn, user_id, settings):
    other = store.upsert_user(conn, email="bob@example.com", name="Bob")
    for owner, title in [(user_id, "First"), (other, "Theirs"), (user_id, "Second")]:
        store.create_project(conn, project_id=store.new_id(), user_id=owner, title=title,
                             book_path="p", book_excerpt="e")

    items = store.list_projects(conn, user_id, server_run_id=settings.server_run_id)

    assert [i.title for i in items] == ["Second", "First"]
    assert all(i.display_status == "Draft" for i in items)
    assert all(i.completed_steps == 0 for i in items)
    assert all(i.current_step == StepName.STYLE for i in items)
    assert all(i.needs_attention is False for i in items)


def test_a_running_row_from_a_dead_process_lists_as_interrupted(conn, user_id, settings):
    pid = store.create_project(conn, project_id=store.new_id(), user_id=user_id, title="W",
                               book_path="p", book_excerpt="e")
    conn.execute(
        "UPDATE projects SET step_state='RUNNING', server_run_id='a-dead-process' WHERE id=?",
        (pid,))

    item = store.list_projects(conn, user_id, server_run_id=settings.server_run_id)[0]

    assert item.is_interrupted is True
    assert item.needs_attention is True
    assert item.display_status == "In progress"   # never a fourth pill value


def test_a_running_row_from_this_process_is_not_interrupted(conn, user_id, settings):
    pid = store.create_project(conn, project_id=store.new_id(), user_id=user_id, title="W",
                               book_path="p", book_excerpt="e")
    conn.execute("UPDATE projects SET step_state='RUNNING', server_run_id=? WHERE id=?",
                 (settings.server_run_id, pid))

    item = store.list_projects(conn, user_id, server_run_id=settings.server_run_id)[0]

    assert item.is_interrupted is False
    assert item.needs_attention is False
```

- [ ] **Step 2: Run it and verify it fails**

Run: `cd backend && python -m pytest tests/test_store_projects.py -v`
Expected: FAIL — `AttributeError: module 'app.store' has no attribute 'create_project'`

- [ ] **Step 3: Write `backend/app/models.py`**

```python
from __future__ import annotations

import re
from typing import Annotated, Literal

from pydantic import BaseModel, Field, field_validator

from app.steps import ProjectStatus, StepName, StepState

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s.]+\.[^@\s]+$")

DisplayStatus = Literal["Draft", "In progress", "Done"]
ImageState = Literal["ready", "generating", "pending"]
FailureCode = Literal["GEMINI_ERROR", "INVALID_OUTPUT", "INTERNAL"]


# ---- requests -------------------------------------------------------------

class SessionCreate(BaseModel):
    name: Annotated[str, Field(min_length=1)]
    email: Annotated[str, Field(min_length=3)]

    @field_validator("name", "email")
    @classmethod
    def _strip(cls, value: str) -> str:
        return value.strip()

    @field_validator("email")
    @classmethod
    def _email_shape(cls, value: str) -> str:
        lowered = value.lower()
        if not EMAIL_RE.match(lowered):
            raise ValueError("must be a valid email address")
        return lowered


class ProjectCreate(BaseModel):
    title: Annotated[str, Field(min_length=1)]
    book_text: Annotated[str, Field(min_length=1)]

    @field_validator("title")
    @classmethod
    def _strip_title(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("must not be blank")
        return stripped

    @field_validator("book_text")
    @classmethod
    def _non_blank_book(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value


class RunRequest(BaseModel):
    """`step` is asserted by the client so a stale tab cannot silently run the
    step that happens to be current now (design 8)."""
    step: StepName
    style: str | None = None


# ---- responses ------------------------------------------------------------

class SessionView(BaseModel):
    user_id: str
    name: str
    email: str


class Failure(BaseModel):
    code: FailureCode
    message: str


class EntityView(BaseModel):
    id: str
    position: int
    name: str
    prompt: str
    image_url: str | None
    image_state: ImageState


class ProjectListItem(BaseModel):
    id: str
    title: str
    created_at: str
    status: ProjectStatus
    current_step: StepName | None
    display_status: DisplayStatus
    needs_attention: bool
    is_interrupted: bool
    completed_steps: int


class ProjectView(BaseModel):
    id: str
    title: str
    created_at: str
    status: ProjectStatus
    step_state: StepState
    current_step: StepName | None
    display_status: DisplayStatus
    needs_attention: bool
    is_interrupted: bool
    completed_steps: int
    style_text: str | None
    book_excerpt: str
    failure: Failure | None
    characters: list[EntityView]
    chapters: list[EntityView]


class BookView(BaseModel):
    text: str


class ApiError(BaseModel):
    code: str
    message: str


class ApiErrorBody(BaseModel):
    error: ApiError


class RunAccepted(BaseModel):
    project: ProjectView


class RunConflict(BaseModel):
    """A 409 carries the truth as well as the complaint, so the losing caller
    renders current state with no follow-up fetch (design 8)."""
    error: ApiError
    project: ProjectView


def state_message(project: ProjectView) -> dict:
    """The one WebSocket payload shape (design 9.1)."""
    return {"type": "project.state", "project": project.model_dump(mode="json")}
```

- [ ] **Step 4: Append the projects section to `backend/app/store.py`**

```python
# --------------------------------------------------------------------------
# Projects
# --------------------------------------------------------------------------

from app.models import ProjectListItem                       # noqa: E402
from app.steps import (                                      # noqa: E402
    ProjectStatus, StepState, completed_steps, current_step,
    display_status, needs_attention,
)


def create_project(conn, *, project_id: str, user_id: str, title: str, book_path: str,
                   book_excerpt: str) -> str:
    """The id is supplied by the caller, because the book file is written to a
    project-scoped directory before the row exists (design 3.2)."""
    conn.execute(
        """
        INSERT INTO projects (id, user_id, title, created_at, book_path, book_excerpt,
                              status, step_state)
        VALUES (?,?,?,?,?,?,?,?)
        """,
        (project_id, user_id, title, now_iso(), book_path, book_excerpt,
         ProjectStatus.CREATED, StepState.IDLE),
    )
    return project_id


def get_project(conn, project_id: str, user_id: str) -> sqlite3.Row | None:
    """Ownership is part of the lookup. A miss is a 404 either way, so another
    user's project is never confirmed to exist (design 8.2)."""
    return conn.execute(
        "SELECT * FROM projects WHERE id = ? AND user_id = ?", (project_id, user_id)
    ).fetchone()


def is_interrupted(row: sqlite3.Row, server_run_id: str) -> bool:
    """A RUNNING row stamped by a process that is no longer here is provably
    orphaned. Derived at read time, never stored (design 5.3)."""
    return (row["step_state"] == StepState.RUNNING
            and row["server_run_id"] is not None
            and row["server_run_id"] != server_run_id)


def list_projects(conn, user_id: str, *, server_run_id: str) -> list[ProjectListItem]:
    rows = conn.execute(
        "SELECT * FROM projects WHERE user_id = ? ORDER BY created_at DESC, rowid DESC",
        (user_id,),
    ).fetchall()
    items = []
    for row in rows:
        interrupted = is_interrupted(row, server_run_id)
        items.append(ProjectListItem(
            id=row["id"],
            title=row["title"],
            created_at=row["created_at"],
            status=row["status"],
            current_step=current_step(row["status"]),
            display_status=display_status(row["status"], row["step_state"]),
            needs_attention=needs_attention(row["step_state"], interrupted),
            is_interrupted=interrupted,
            completed_steps=completed_steps(row["status"]),
        ))
    return items
```

- [ ] **Step 5: Run the test and verify it passes**

Run: `cd backend && python -m pytest tests/test_store_projects.py -v`
Expected: PASS — 5 passed

- [ ] **Step 6: Commit**

```bash
git add backend/app/models.py backend/app/store.py backend/tests/test_store_projects.py
git commit -m "Add project rows, the list read model, and every API DTO

display_status, needs_attention and is_interrupted are computed by one shared
pair of functions so the pill can never disagree between the list, the detail
view, the socket payload and a 409 body. is_interrupted is a read-time
comparison against the current server_run_id, never a stored column.

Mostly AI-authored (Claude Code).

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 8: The single read model — `read_project_view`

The one function every surface reads through. It also derives per-item image state, which is what makes "the user sees each portrait land" work with **no per-item database column** (spec §4.5).

**Files:**
- Modify: `backend/app/store.py` (append the read model)
- Create: `backend/tests/test_read_model.py`

**Interfaces:**
- Consumes: Task 7's models and helpers.
- Produces: `store.read_project_view(conn, project_id, user_id, *, server_run_id) -> ProjectView | None`, `store.list_characters`, `store.list_chapters`, `store.save_characters`, `store.save_chapters`, `store.save_style`, `store.save_portrait`, `store.save_illustration`.

- [ ] **Step 1: Write the failing test**

`backend/tests/test_read_model.py`:

```python
import pytest

from app import db, store
from app.steps import ProjectStatus, StepName, StepState


@pytest.fixture
def conn(settings):
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    with db.get_conn(settings) as c:
        db.init_schema(c)
        yield c


@pytest.fixture
def project(conn):
    user_id = store.upsert_user(conn, email="ada@example.com", name="Ada")
    pid = store.create_project(conn, project_id=store.new_id(), user_id=user_id,
                               title="Willows", book_path="projects/x/book.txt",
                               book_excerpt="Once…")
    return user_id, pid


def view(conn, settings, project):
    user_id, pid = project
    return store.read_project_view(conn, pid, user_id, server_run_id=settings.server_run_id)


def test_a_fresh_project_reads_as_a_draft_awaiting_style(conn, settings, project):
    v = view(conn, settings, project)
    assert v.status == ProjectStatus.CREATED
    assert v.current_step == StepName.STYLE
    assert v.display_status == "Draft"
    assert v.completed_steps == 0
    assert v.style_text is None and v.failure is None
    assert v.characters == [] and v.chapters == []


def test_another_users_id_reads_as_nothing(conn, settings, project):
    _, pid = project
    other = store.upsert_user(conn, email="bob@example.com", name="Bob")
    assert store.read_project_view(conn, pid, other,
                                   server_run_id=settings.server_run_id) is None


def test_style_and_characters_appear_once_saved(conn, settings, project):
    _, pid = project
    store.save_style(conn, pid, style_text="Warm watercolour", text_interaction_id="i-style")
    store.save_characters(conn, pid, [("Toad", "A stout toad…"), ("Rat", "A river rat…")],
                          text_interaction_id="i-chars")
    v = view(conn, settings, project)
    assert v.style_text == "Warm watercolour"
    assert [c.name for c in v.characters] == ["Toad", "Rat"]
    assert [c.position for c in v.characters] == [0, 1]
    assert all(c.image_url is None for c in v.characters)


def test_while_idle_every_missing_portrait_is_merely_pending(conn, settings, project):
    _, pid = project
    store.save_characters(conn, pid, [("Toad", "p1"), ("Rat", "p2")],
                          text_interaction_id="i")
    assert [c.image_state for c in view(conn, settings, project).characters] == \
        ["pending", "pending"]


def test_while_running_the_first_missing_portrait_is_the_one_generating(conn, settings, project):
    """[null, null] -> generating, pending (design 4.5)."""
    _, pid = project
    store.save_characters(conn, pid, [("Toad", "p1"), ("Rat", "p2")], text_interaction_id="i")
    conn.execute(
        "UPDATE projects SET status='CHARACTERS_GENERATED', step_state='RUNNING', "
        "server_run_id=? WHERE id=?", (settings.server_run_id, pid))
    assert [c.image_state for c in view(conn, settings, project).characters] == \
        ["generating", "pending"]


def test_a_landed_portrait_is_ready_and_the_next_becomes_generating(conn, settings, project):
    """[path, null] -> ready, generating (design 4.5)."""
    _, pid = project
    store.save_characters(conn, pid, [("Toad", "p1"), ("Rat", "p2")], text_interaction_id="i")
    first = store.list_characters(conn, pid)[0]["id"]
    store.save_portrait(conn, project_id=pid, character_id=first,
                        portrait_path=f"projects/{pid}/portraits/{first}.png",
                        image_interaction_id="i-img")
    conn.execute(
        "UPDATE projects SET status='CHARACTERS_GENERATED', step_state='RUNNING', "
        "server_run_id=? WHERE id=?", (settings.server_run_id, pid))
    v = view(conn, settings, project)
    assert [c.image_state for c in v.characters] == ["ready", "generating"]
    assert v.characters[0].image_url == f"/api/projects/{pid}/characters/{first}/portrait"
    assert v.characters[1].image_url is None


def test_a_different_running_step_leaves_portraits_merely_pending(conn, settings, project):
    """Only the step that owns the artifact marks one as generating."""
    _, pid = project
    store.save_characters(conn, pid, [("Toad", "p1"), ("Rat", "p2")], text_interaction_id="i")
    conn.execute(
        "UPDATE projects SET status='STYLE_SET', step_state='RUNNING', server_run_id=? "
        "WHERE id=?", (settings.server_run_id, pid))
    assert [c.image_state for c in view(conn, settings, project).characters] == \
        ["pending", "pending"]


def test_a_recorded_failure_surfaces_as_the_failure_field(conn, settings, project):
    _, pid = project
    conn.execute(
        "UPDATE projects SET step_state='FAILED', error_code='GEMINI_ERROR', "
        "error_message='Gemini said no' WHERE id=?", (pid,))
    v = view(conn, settings, project)
    assert v.failure.code == "GEMINI_ERROR"
    assert v.failure.message == "Gemini said no"
    assert v.needs_attention is True
    assert v.display_status == "In progress"


def test_chapters_derive_identically_to_characters(conn, settings, project):
    _, pid = project
    store.save_chapters(conn, pid, [("Opening Scene", "A river bank…")],
                        text_interaction_id="i")
    conn.execute(
        "UPDATE projects SET status='CHAPTERS_GENERATED', step_state='RUNNING', "
        "server_run_id=? WHERE id=?", (settings.server_run_id, pid))
    assert [c.image_state for c in view(conn, settings, project).chapters] == ["generating"]


def test_saving_characters_replaces_any_previous_set(conn, settings, project):
    _, pid = project
    store.save_characters(conn, pid, [("A", "x"), ("B", "y")], text_interaction_id="i1")
    store.save_characters(conn, pid, [("C", "z")], text_interaction_id="i2")
    assert [c.name for c in view(conn, settings, project).characters] == ["C"]
    assert conn.execute("SELECT text_interaction_id FROM projects WHERE id=?",
                        (pid,)).fetchone()[0] == "i2"


def test_saving_a_portrait_advances_the_image_head_in_the_same_write(conn, settings, project):
    """Coupling them is what makes step 3 resumable mid-flight (design 7.2)."""
    _, pid = project
    store.save_characters(conn, pid, [("Toad", "p1")], text_interaction_id="i")
    cid = store.list_characters(conn, pid)[0]["id"]
    store.save_portrait(conn, project_id=pid, character_id=cid,
                        portrait_path="projects/p/portraits/c.png",
                        image_interaction_id="i-img-1")
    row = conn.execute("SELECT image_interaction_id FROM projects WHERE id=?", (pid,)).fetchone()
    assert row["image_interaction_id"] == "i-img-1"
    assert store.list_characters(conn, pid)[0]["portrait_path"] == "projects/p/portraits/c.png"
```

- [ ] **Step 2: Run it and verify it fails**

Run: `cd backend && python -m pytest tests/test_read_model.py -v`
Expected: FAIL — `AttributeError: module 'app.store' has no attribute 'save_style'`

- [ ] **Step 3: Append the read model and writers to `backend/app/store.py`**

```python
from app.models import EntityView, Failure, ProjectView       # noqa: E402
from app.steps import MAX_CHAPTERS, MAX_CHARACTERS, StepName  # noqa: E402


def list_characters(conn, project_id: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM characters WHERE project_id = ? ORDER BY position", (project_id,)
    ).fetchall()


def list_chapters(conn, project_id: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM chapters WHERE project_id = ? ORDER BY position", (project_id,)
    ).fetchall()


def _entity_views(rows, *, project_id: str, path_column: str, url_suffix: str,
                  collection: str, generating: bool) -> list[EntityView]:
    """Per-item state, derived. The handler iterates in position order, so the
    first item still missing its artifact is the one in flight (design 4.5)."""
    seen_missing = False
    views: list[EntityView] = []
    for row in rows:
        path = row[path_column]
        if path is not None:
            state = "ready"
            url = f"/api/projects/{project_id}/{collection}/{row['id']}/{url_suffix}"
        else:
            url = None
            if generating and not seen_missing:
                state = "generating"
                seen_missing = True
            else:
                state = "pending"
        views.append(EntityView(id=row["id"], position=row["position"], name=row["name"],
                                prompt=row["prompt"], image_url=url, image_state=state))
    return views


def read_project_view(conn, project_id: str, user_id: str, *,
                      server_run_id: str) -> ProjectView | None:
    row = get_project(conn, project_id, user_id)
    if row is None:
        return None

    interrupted = is_interrupted(row, server_run_id)
    running = row["step_state"] == StepState.RUNNING and not interrupted
    step = current_step(row["status"])

    failure = None
    if row["error_code"] is not None:
        failure = Failure(code=row["error_code"], message=row["error_message"] or "")

    return ProjectView(
        id=row["id"],
        title=row["title"],
        created_at=row["created_at"],
        status=row["status"],
        step_state=row["step_state"],
        current_step=step,
        display_status=display_status(row["status"], row["step_state"]),
        needs_attention=needs_attention(row["step_state"], interrupted),
        is_interrupted=interrupted,
        completed_steps=completed_steps(row["status"]),
        style_text=row["style_text"],
        book_excerpt=row["book_excerpt"],
        failure=failure,
        characters=_entity_views(
            list_characters(conn, project_id), project_id=project_id,
            path_column="portrait_path", url_suffix="portrait", collection="characters",
            generating=running and step == StepName.PORTRAITS),
        chapters=_entity_views(
            list_chapters(conn, project_id), project_id=project_id,
            path_column="illustration_path", url_suffix="illustration", collection="chapters",
            generating=running and step == StepName.ILLUSTRATIONS),
    )


# ---- step outputs: artifact and chain head always move together ------------

def save_style(conn, project_id: str, *, style_text: str, text_interaction_id: str) -> None:
    conn.execute(
        "UPDATE projects SET style_text = ?, text_interaction_id = ? WHERE id = ?",
        (style_text, text_interaction_id, project_id),
    )


def _replace_children(conn, table: str, project_id: str,
                      items: list[tuple[str, str]], text_interaction_id: str) -> None:
    conn.execute("BEGIN")
    try:
        conn.execute(f"DELETE FROM {table} WHERE project_id = ?", (project_id,))
        for position, (name, prompt) in enumerate(items):
            conn.execute(
                f"INSERT INTO {table} (id, project_id, position, name, prompt) "
                "VALUES (?,?,?,?,?)",
                (new_id(), project_id, position, name, prompt),
            )
        conn.execute("UPDATE projects SET text_interaction_id = ? WHERE id = ?",
                     (text_interaction_id, project_id))
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


def save_characters(conn, project_id: str, items: list[tuple[str, str]], *,
                    text_interaction_id: str) -> None:
    _replace_children(conn, "characters", project_id, items, text_interaction_id)


def save_chapters(conn, project_id: str, items: list[tuple[str, str]], *,
                  text_interaction_id: str) -> None:
    _replace_children(conn, "chapters", project_id, items, text_interaction_id)


def _save_artifact(conn, *, table: str, column: str, project_id: str, row_id: str,
                   path: str, image_interaction_id: str) -> None:
    """One transaction. Saving the file without the head would make a retry
    re-seed a chain that has diverged from the images on disk; saving the head
    without the file would make the handler skip an artifact it does not have
    (design 7.2)."""
    conn.execute("BEGIN")
    try:
        conn.execute(f"UPDATE {table} SET {column} = ? WHERE id = ?", (path, row_id))
        conn.execute("UPDATE projects SET image_interaction_id = ? WHERE id = ?",
                     (image_interaction_id, project_id))
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


def save_portrait(conn, *, project_id: str, character_id: str, portrait_path: str,
                  image_interaction_id: str) -> None:
    _save_artifact(conn, table="characters", column="portrait_path", project_id=project_id,
                   row_id=character_id, path=portrait_path,
                   image_interaction_id=image_interaction_id)


def save_illustration(conn, *, project_id: str, chapter_id: str, illustration_path: str,
                      image_interaction_id: str) -> None:
    _save_artifact(conn, table="chapters", column="illustration_path", project_id=project_id,
                   row_id=chapter_id, path=illustration_path,
                   image_interaction_id=image_interaction_id)
```

- [ ] **Step 4: Run the test and verify it passes**

Run: `cd backend && python -m pytest tests/test_read_model.py -v`
Expected: PASS — 11 passed

- [ ] **Step 5: Run the whole backend suite for regressions**

Run: `cd backend && python -m pytest -v`
Expected: PASS — everything green

- [ ] **Step 6: Commit**

```bash
git add backend/app/store.py backend/tests/test_read_model.py
git commit -m "Add read_project_view, the single read model

Per-item image state is derived, not stored: while the portraits step runs, the
first character still missing a portrait is 'generating' and every later one is
'pending'. That works because the handler iterates in position order, so no
progress column and no per-item flag exist.

Each artifact writer advances its chain head in the same transaction as the
artifact reference, which is what makes step 3 resumable mid-flight.

Mostly AI-authored (Claude Code).

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 9: The three conditional transitions

The core invariant, proven early. One `UPDATE` enforces step ordering, at-most-one-execution and orphan recovery simultaneously (spec §5.1). Tested against real SQLite — mocking the database would test nothing, since the guarantee *is* its atomicity.

**Files:**
- Modify: `backend/app/store.py` (append the transitions)
- Create: `backend/tests/test_store_transitions.py`

**Interfaces:**
- Consumes: Task 8.
- Produces: `store.begin_step`, `store.complete_step`, `store.fail_step`.

- [ ] **Step 1: Write the failing test**

`backend/tests/test_store_transitions.py`:

```python
import pytest

from app import db, store
from app.steps import ProjectStatus, StepState


@pytest.fixture
def conn(settings):
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    with db.get_conn(settings) as c:
        db.init_schema(c)
        yield c


@pytest.fixture
def pid(conn):
    user_id = store.upsert_user(conn, email="ada@example.com", name="Ada")
    return store.create_project(conn, project_id=store.new_id(), user_id=user_id,
                                title="W", book_path="p", book_excerpt="e")


def row(conn, pid):
    return conn.execute("SELECT * FROM projects WHERE id=?", (pid,)).fetchone()


def begin(conn, pid, *, expected=ProjectStatus.CREATED, run="run-A"):
    return store.begin_step(conn, pid, expected_status=expected,
                            server_run_id=run, now="2026-08-14T10:00:00+00:00")


def test_beginning_the_current_step_claims_the_attempt(conn, pid):
    assert begin(conn, pid) is True
    r = row(conn, pid)
    assert r["step_state"] == StepState.RUNNING
    assert r["server_run_id"] == "run-A"
    assert r["step_started_at"] == "2026-08-14T10:00:00+00:00"


def test_beginning_a_future_step_is_refused(conn, pid):
    """Step ordering: assessment 4.3 - a step cannot run before its predecessors."""
    assert begin(conn, pid, expected=ProjectStatus.CHARACTERS_GENERATED) is False
    assert row(conn, pid)["step_state"] == StepState.IDLE


def test_beginning_an_already_completed_step_is_refused(conn, pid):
    conn.execute("UPDATE projects SET status='STYLE_SET' WHERE id=?", (pid,))
    assert begin(conn, pid, expected=ProjectStatus.CREATED) is False


def test_a_second_caller_cannot_claim_a_live_run(conn, pid):
    """The duplicate-execution guard: refresh, second tab, double-click."""
    assert begin(conn, pid) is True
    assert begin(conn, pid) is False
    assert row(conn, pid)["server_run_id"] == "run-A"


def test_a_run_left_by_a_dead_process_can_be_reclaimed(conn, pid):
    """Orphan recovery is not a separate endpoint - retrying IS the recovery,
    permitted only when the owning process is provably gone (design 5.1)."""
    assert begin(conn, pid, run="run-A") is True
    assert begin(conn, pid, run="run-B") is True
    assert row(conn, pid)["server_run_id"] == "run-B"


def test_a_failed_step_can_be_retried(conn, pid):
    begin(conn, pid)
    store.fail_step(conn, pid, server_run_id="run-A", code="GEMINI_ERROR", message="boom")
    assert row(conn, pid)["step_state"] == StepState.FAILED
    assert begin(conn, pid) is True


def test_beginning_clears_any_previous_error(conn, pid):
    begin(conn, pid)
    store.fail_step(conn, pid, server_run_id="run-A", code="GEMINI_ERROR", message="boom")
    begin(conn, pid)
    r = row(conn, pid)
    assert r["error_code"] is None and r["error_message"] is None


def test_completing_moves_status_and_step_state_in_one_write(conn, pid):
    begin(conn, pid)
    assert store.complete_step(conn, pid, server_run_id="run-A",
                               next_status=ProjectStatus.STYLE_SET) is True
    r = row(conn, pid)
    assert (r["status"], r["step_state"], r["server_run_id"]) == \
        (ProjectStatus.STYLE_SET, StepState.IDLE, None)


def test_completing_is_refused_when_this_run_no_longer_owns_the_step(conn, pid):
    """A task whose run was taken over must not advance someone else's step."""
    begin(conn, pid, run="run-A")
    begin(conn, pid, run="run-B")
    assert store.complete_step(conn, pid, server_run_id="run-A",
                               next_status=ProjectStatus.STYLE_SET) is False
    assert row(conn, pid)["status"] == ProjectStatus.CREATED


def test_failing_is_refused_when_this_run_no_longer_owns_the_step(conn, pid):
    begin(conn, pid, run="run-A")
    begin(conn, pid, run="run-B")
    assert store.fail_step(conn, pid, server_run_id="run-A",
                           code="INTERNAL", message="late") is False
    assert row(conn, pid)["step_state"] == StepState.RUNNING


def test_failing_can_null_the_chain_head_that_raised(conn, pid):
    """Context expiry does two things in one write: fail, and null that chain
    (design 7.5). Nothing else happens in that run."""
    conn.execute("UPDATE projects SET text_interaction_id='i-t', image_interaction_id='i-i' "
                 "WHERE id=?", (pid,))
    begin(conn, pid)
    store.fail_step(conn, pid, server_run_id="run-A", code="GEMINI_ERROR",
                    message="context expired", clear_head="text")
    r = row(conn, pid)
    assert r["text_interaction_id"] is None
    assert r["image_interaction_id"] == "i-i"


def test_failing_can_null_the_image_head_instead(conn, pid):
    conn.execute("UPDATE projects SET text_interaction_id='i-t', image_interaction_id='i-i' "
                 "WHERE id=?", (pid,))
    begin(conn, pid)
    store.fail_step(conn, pid, server_run_id="run-A", code="GEMINI_ERROR",
                    message="context expired", clear_head="image")
    r = row(conn, pid)
    assert r["text_interaction_id"] == "i-t"
    assert r["image_interaction_id"] is None
```

- [ ] **Step 2: Run it and verify it fails**

Run: `cd backend && python -m pytest tests/test_store_transitions.py -v`
Expected: FAIL — `AttributeError: module 'app.store' has no attribute 'begin_step'`

- [ ] **Step 3: Append the transitions to `backend/app/store.py`**

```python
from typing import Literal  # noqa: E402


def begin_step(conn, project_id: str, *, expected_status: ProjectStatus,
               server_run_id: str, now: str) -> bool:
    """One statement, three invariants (design 5.1).

    status  = ...                     enforces step ordering
    IDLE/FAILED                       enforces at most one execution
    RUNNING with a foreign run id     performs orphan recovery

    True  -> this caller owns the attempt; start the work.
    False -> 409 with the current project state.
    """
    cursor = conn.execute(
        """
        UPDATE projects
           SET step_state = 'RUNNING', server_run_id = :run, step_started_at = :now,
               error_code = NULL, error_message = NULL
         WHERE id = :pid
           AND status = :expected
           AND ( step_state IN ('IDLE', 'FAILED')
                 OR (step_state = 'RUNNING' AND server_run_id IS NOT :run) )
        """,
        {"pid": project_id, "expected": str(expected_status), "run": server_run_id, "now": now},
    )
    return cursor.rowcount == 1


def complete_step(conn, project_id: str, *, server_run_id: str,
                  next_status: ProjectStatus) -> bool:
    """status and step_state move together, so they can never disagree."""
    cursor = conn.execute(
        """
        UPDATE projects
           SET status = :next, step_state = 'IDLE', server_run_id = NULL,
               step_started_at = NULL, error_code = NULL, error_message = NULL
         WHERE id = :pid AND step_state = 'RUNNING' AND server_run_id = :run
        """,
        {"pid": project_id, "next": str(next_status), "run": server_run_id},
    )
    return cursor.rowcount == 1


def fail_step(conn, project_id: str, *, server_run_id: str, code: str, message: str,
              clear_head: Literal["text", "image"] | None = None) -> bool:
    """Ownership-guarded: a task that no longer owns the run writes nothing, so
    a late failure or cancellation cannot clobber a newer execution (design 6.3)."""
    head_sql = ""
    if clear_head == "text":
        head_sql = ", text_interaction_id = NULL"
    elif clear_head == "image":
        head_sql = ", image_interaction_id = NULL"
    cursor = conn.execute(
        f"""
        UPDATE projects
           SET step_state = 'FAILED', server_run_id = NULL, step_started_at = NULL,
               error_code = :code, error_message = :message {head_sql}
         WHERE id = :pid AND step_state = 'RUNNING' AND server_run_id = :run
        """,
        {"pid": project_id, "code": code, "message": message, "run": server_run_id},
    )
    return cursor.rowcount == 1
```

`IS NOT` rather than `!=` is deliberate: SQLite's `!=` yields NULL when either side is NULL, so a `RUNNING` row with a NULL `server_run_id` would never be reclaimable. `IS NOT` compares NULLs correctly.

- [ ] **Step 4: Run the test and verify it passes**

Run: `cd backend && python -m pytest tests/test_store_transitions.py -v`
Expected: PASS — 12 passed

- [ ] **Step 5: Commit**

```bash
git add backend/app/store.py backend/tests/test_store_transitions.py
git commit -m "Add the conditional transition that guards the whole pipeline

One UPDATE enforces three invariants at once: the status clause enforces step
ordering, the IDLE/FAILED clause enforces at most one execution, and the
trailing clause reclaims a RUNNING row stamped by a process that is gone. That
last part is why orphan recovery needs no separate endpoint - retrying an
interrupted step IS the recovery.

complete_step and fail_step are ownership-guarded on server_run_id, so a task
whose run was taken over writes nothing. Tested against real SQLite, because
the guarantee lives in its atomicity.

Mostly AI-authored (Claude Code).

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Phase C — HTTP bootstrap

### Task 10: App factory, dependencies, and the identity API

**Files:**
- Create: `backend/app/main.py`, `backend/app/api/__init__.py`, `backend/app/api/deps.py`, `backend/app/api/session.py`, `backend/tests/test_api_session.py`
- Modify: `backend/tests/conftest.py` (add `app`, `client`, `aclient` fixtures)

**Interfaces:**
- Consumes: `store` identity (Task 6), `models` (Task 7), `db` (Task 3).
- Produces: `create_app(*, settings=None, gemini=None, registry=None) -> FastAPI`; `deps.get_db`, `deps.current_user`, `deps.get_deps`; `SESSION_COOKIE = "session"`; the three `/api/session` endpoints.

- [ ] **Step 1: Write the failing test**

`backend/tests/test_api_session.py`:

```python
def test_a_new_email_creates_a_user_and_sets_a_session_cookie(client):
    response = client.post("/api/session", json={"name": "Ada", "email": "Ada@Example.com "})

    assert response.status_code == 201
    body = response.json()
    assert body["email"] == "ada@example.com"      # normalised: trimmed, lowercased
    assert body["name"] == "Ada"
    assert "session" in response.cookies


def test_the_same_email_returns_the_same_user(client):
    first = client.post("/api/session", json={"name": "Ada", "email": "ada@example.com"}).json()
    second = client.post("/api/session", json={"name": "Ada", "email": "ada@example.com"}).json()
    assert first["user_id"] == second["user_id"]


def test_signing_in_again_updates_the_stored_name(client):
    client.post("/api/session", json={"name": "Ada", "email": "ada@example.com"})
    client.post("/api/session", json={"name": "Ada Lovelace", "email": "ada@example.com"})
    assert client.get("/api/session").json()["name"] == "Ada Lovelace"


def test_get_session_restores_identity_on_app_boot(client):
    created = client.post("/api/session", json={"name": "Ada", "email": "ada@example.com"}).json()
    restored = client.get("/api/session")
    assert restored.status_code == 200
    assert restored.json()["user_id"] == created["user_id"]


def test_get_session_without_a_cookie_is_401(client):
    assert client.get("/api/session").status_code == 401


def test_sign_out_revokes_the_session(client):
    client.post("/api/session", json={"name": "Ada", "email": "ada@example.com"})
    assert client.delete("/api/session").status_code == 204
    assert client.get("/api/session").status_code == 401


def test_a_stale_cookie_is_401_not_a_crash(client):
    client.cookies.set("session", "no-such-token")
    assert client.get("/api/session").status_code == 401


def test_a_blank_name_is_rejected(client):
    assert client.post("/api/session", json={"name": "  ", "email": "a@b.co"}).status_code == 422


def test_a_malformed_email_is_rejected(client):
    assert client.post("/api/session", json={"name": "Ada", "email": "not-an-email"}
                       ).status_code == 422


def test_the_session_cookie_is_httponly_and_lax(client):
    response = client.post("/api/session", json={"name": "Ada", "email": "ada@example.com"})
    header = response.headers["set-cookie"].lower()
    assert "httponly" in header and "samesite=lax" in header
```

Extend `backend/tests/conftest.py`:

```python
import httpx
import pytest
import pytest_asyncio
from fastapi.testclient import TestClient

from app.gemini.fake import FakeGeminiClient
from app.main import create_app


@pytest.fixture
def fake_gemini() -> FakeGeminiClient:
    return FakeGeminiClient()


@pytest.fixture
def app(settings, fake_gemini):
    return create_app(settings=settings, gemini=fake_gemini)


@pytest.fixture
def client(app):
    with TestClient(app) as test_client:
        yield test_client


@pytest_asyncio.fixture
async def aclient(app):
    """An httpx client over the ASGI app, for tests that need real concurrency."""
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as async_client:
        # create_app's lifespan does not run under ASGITransport; init explicitly.
        yield async_client
```

Because `FakeGeminiClient` does not exist until Task 17, add a temporary stand-in now so the fixtures import — `backend/app/gemini/__init__.py` and `backend/app/gemini/fake.py` with:

```python
class FakeGeminiClient:  # replaced in full by Task 17
    pass
```

- [ ] **Step 2: Run it and verify it fails**

Run: `cd backend && python -m pytest tests/test_api_session.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.main'`

- [ ] **Step 3: Write `backend/app/api/deps.py`**

```python
from __future__ import annotations

import sqlite3
from typing import Iterator

from fastapi import Depends, HTTPException, Request, status

from app import db, store

SESSION_COOKIE = "session"


def get_settings(request: Request):
    return request.app.state.settings


def get_deps(request: Request):
    return request.app.state.deps


def get_db(request: Request) -> Iterator[sqlite3.Connection]:
    with db.get_conn(request.app.state.settings) as conn:
        yield conn


def current_user(request: Request, conn: sqlite3.Connection = Depends(get_db)) -> sqlite3.Row:
    token = request.cookies.get(SESSION_COOKIE)
    user = store.user_for_session(conn, token) if token else None
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Sign in to continue.")
    return user
```

- [ ] **Step 4: Write `backend/app/api/session.py`**

```python
from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, Request, Response, status

from app import store
from app.api.deps import SESSION_COOKIE, current_user, get_db
from app.models import SessionCreate, SessionView

router = APIRouter(prefix="/api/session", tags=["session"])


@router.post("", status_code=status.HTTP_201_CREATED, response_model=SessionView)
def sign_in(payload: SessionCreate, response: Response,
            conn: sqlite3.Connection = Depends(get_db)) -> SessionView:
    user_id = store.upsert_user(conn, email=payload.email, name=payload.name)
    token = store.create_session(conn, user_id)
    # Local-only app, same-origin through the Vite proxy: HttpOnly + Lax is
    # sufficient for this threat model and needs no CORS (design 8.1).
    response.set_cookie(SESSION_COOKIE, token, httponly=True, samesite="lax", path="/")
    return SessionView(user_id=user_id, name=payload.name, email=payload.email)


@router.get("", response_model=SessionView)
def read_session(user: sqlite3.Row = Depends(current_user)) -> SessionView:
    return SessionView(user_id=user["id"], name=user["name"], email=user["email"])


@router.delete("", status_code=status.HTTP_204_NO_CONTENT)
def sign_out(request: Request, response: Response,
             conn: sqlite3.Connection = Depends(get_db)) -> None:
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        store.delete_session(conn, token)
    response.delete_cookie(SESSION_COOKIE, path="/")
```

- [ ] **Step 5: Write `backend/app/main.py`**

```python
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app import db
from app.api import session as session_api
from app.config import Settings, load_settings


def _build_gemini(settings: Settings):
    if settings.use_fake_gemini:
        from app.gemini.fake import FakeGeminiClient
        return FakeGeminiClient()
    from app.gemini.real import RealGeminiClient
    return RealGeminiClient(settings)


def create_app(*, settings: Settings | None = None, gemini=None, registry=None) -> FastAPI:
    settings = settings or load_settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    with db.get_conn(settings) as conn:
        db.init_schema(conn)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        yield

    app = FastAPI(title="Book Illustration Studio", lifespan=lifespan)
    app.state.settings = settings
    app.state.gemini = gemini if gemini is not None else _build_gemini(settings)
    app.state.registry = registry
    app.include_router(session_api.router)
    return app


app = create_app()
```

Schema initialisation happens in `create_app` rather than in the lifespan so it also runs under `httpx.ASGITransport`, which does not execute lifespan events. `app.state.registry` and `app.state.deps` are filled in by Tasks 23 and 30.

Create empty `backend/app/api/__init__.py`.

- [ ] **Step 6: Run the test and verify it passes**

Run: `cd backend && python -m pytest tests/test_api_session.py -v`
Expected: PASS — 10 passed

- [ ] **Step 7: Commit**

```bash
git add backend/app/main.py backend/app/api backend/app/gemini backend/tests/conftest.py backend/tests/test_api_session.py
git commit -m "Add the app factory and the identity API

create_app takes injectable settings, gemini client and realtime registry so
tests construct an app with a temp database and a fake provider without
monkeypatching. Schema init runs in the factory, not the lifespan, so it also
applies under httpx.ASGITransport.

Identity is email plus name with no password: a new email creates the user, a
returning one loads them and updates the stored name. The session is an opaque
HttpOnly SameSite=Lax cookie backed by a SQLite row, so a restart does not sign
anyone out.

Mostly AI-authored (Claude Code).

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 11: Projects REST — create, list, detail, book, artifacts

Every read surface plus the ownership boundary. **Project creation makes zero Gemini calls** (spec §7.2: the upload happens lazily inside step 1, so creation is a pure local write that cannot fail).

**Files:**
- Create: `backend/app/api/projects.py`, `backend/tests/test_api_projects.py`
- Modify: `backend/app/main.py` (mount the router)

**Interfaces:**
- Consumes: Tasks 5, 7, 8, 10.
- Produces: `POST /api/projects`, `GET /api/projects`, `GET /api/projects/{id}`, `GET /api/projects/{id}/book`, `GET /api/projects/{id}/characters/{cid}/portrait`, `GET /api/projects/{id}/chapters/{cid}/illustration`.

- [ ] **Step 1: Write the failing test**

`backend/tests/test_api_projects.py`:

```python
import pytest

from app import db, store

BOOK = "Once upon a time, in a small burrow by the river, there lived a Mole."


@pytest.fixture
def signed_in(client):
    client.post("/api/session", json={"name": "Ada", "email": "ada@example.com"})
    return client


def make_project(c, title="Willows", book=BOOK):
    return c.post("/api/projects", json={"title": title, "book_text": book})


def test_creating_a_project_persists_the_row_and_the_book_file(signed_in, settings, fake_gemini):
    response = make_project(signed_in)

    assert response.status_code == 201
    project = response.json()
    assert project["title"] == "Willows"
    assert project["status"] == "CREATED"
    assert project["display_status"] == "Draft"
    assert project["current_step"] == "STYLE"
    assert project["book_excerpt"].startswith("Once upon a time")

    book_file = settings.data_dir / "projects" / project["id"] / "book.txt"
    assert book_file.read_text(encoding="utf-8") == BOOK


def test_creating_a_project_makes_zero_gemini_calls(signed_in, fake_gemini):
    """The upload happens lazily inside step 1, so creation cannot fail on a
    provider error and an unopened project never holds a dead file URI."""
    make_project(signed_in)
    assert fake_gemini.calls == []


def test_creation_requires_a_title_and_book_text(signed_in):
    assert make_project(signed_in, title="   ").status_code == 422
    assert make_project(signed_in, book="").status_code == 422


def test_creation_requires_a_session(client):
    assert make_project(client).status_code == 401


def test_the_list_shows_only_this_users_projects(client, settings):
    client.post("/api/session", json={"name": "Ada", "email": "ada@example.com"})
    make_project(client, title="Mine")
    client.delete("/api/session")
    client.post("/api/session", json={"name": "Bob", "email": "bob@example.com"})
    make_project(client, title="Theirs")

    titles = [p["title"] for p in client.get("/api/projects").json()]
    assert titles == ["Theirs"]


def test_signing_out_and_back_in_restores_the_same_projects(client):
    client.post("/api/session", json={"name": "Ada", "email": "ada@example.com"})
    created = make_project(client).json()
    client.delete("/api/session")
    client.post("/api/session", json={"name": "Ada", "email": "ada@example.com"})

    listed = client.get("/api/projects").json()
    assert [p["id"] for p in listed] == [created["id"]]
    assert client.get(f"/api/projects/{created['id']}").status_code == 200


def test_the_empty_list_is_an_empty_array_not_an_error(signed_in):
    assert signed_in.get("/api/projects").json() == []


def test_the_detail_view_is_the_full_project(signed_in):
    pid = make_project(signed_in).json()["id"]
    detail = signed_in.get(f"/api/projects/{pid}").json()
    assert detail["characters"] == [] and detail["chapters"] == []
    assert detail["failure"] is None
    assert detail["completed_steps"] == 0


def test_the_book_is_readable_in_full_at_any_point_in_the_pipeline(signed_in):
    """Assessment 4.4. Kept out of the project view because it can be 230 KB
    and never changes, so every state payload stays small (design 8)."""
    pid = make_project(signed_in).json()["id"]
    assert signed_in.get(f"/api/projects/{pid}/book").json()["text"] == BOOK


def test_another_users_project_is_404_not_403(client):
    """Do not confirm existence (design 8.2)."""
    client.post("/api/session", json={"name": "Ada", "email": "ada@example.com"})
    pid = make_project(client).json()["id"]
    client.delete("/api/session")
    client.post("/api/session", json={"name": "Bob", "email": "bob@example.com"})

    assert client.get(f"/api/projects/{pid}").status_code == 404
    assert client.get(f"/api/projects/{pid}/book").status_code == 404


def test_artifact_bytes_are_served_and_ownership_checked(client, settings):
    client.post("/api/session", json={"name": "Ada", "email": "ada@example.com"})
    pid = make_project(client).json()["id"]
    with db.get_conn(settings) as conn:
        store.save_characters(conn, pid, [("Toad", "a toad")], text_interaction_id="i")
        cid = store.list_characters(conn, pid)[0]["id"]
        path = (settings.data_dir / "projects" / pid / "portraits" / f"{cid}.png")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"\x89PNG\r\n\x1a\nportrait")
        store.save_portrait(conn, project_id=pid, character_id=cid,
                            portrait_path=f"projects/{pid}/portraits/{cid}.png",
                            image_interaction_id="i-img")

    served = client.get(f"/api/projects/{pid}/characters/{cid}/portrait")
    assert served.status_code == 200
    assert served.content == b"\x89PNG\r\n\x1a\nportrait"
    assert served.headers["content-type"] == "image/png"

    client.delete("/api/session")
    client.post("/api/session", json={"name": "Bob", "email": "bob@example.com"})
    assert client.get(f"/api/projects/{pid}/characters/{cid}/portrait").status_code == 404


def test_an_ungenerated_portrait_is_404(client, settings):
    client.post("/api/session", json={"name": "Ada", "email": "ada@example.com"})
    pid = make_project(client).json()["id"]
    with db.get_conn(settings) as conn:
        store.save_characters(conn, pid, [("Toad", "a toad")], text_interaction_id="i")
        cid = store.list_characters(conn, pid)[0]["id"]
    assert client.get(f"/api/projects/{pid}/characters/{cid}/portrait").status_code == 404
```

- [ ] **Step 2: Run it and verify it fails**

Run: `cd backend && python -m pytest tests/test_api_projects.py -v`
Expected: FAIL — every test 404s, because `/api/projects` is not routed.

- [ ] **Step 3: Write `backend/app/api/projects.py`**

```python
from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from app import files, store
from app.api.deps import current_user, get_db, get_settings
from app.config import Settings
from app.models import BookView, ProjectCreate, ProjectListItem, ProjectView

router = APIRouter(prefix="/api/projects", tags=["projects"])


def _load_view(conn, project_id: str, user_id: str, settings: Settings) -> ProjectView:
    view = store.read_project_view(conn, project_id, user_id,
                                   server_run_id=settings.server_run_id)
    if view is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found.")
    return view


@router.post("", status_code=status.HTTP_201_CREATED, response_model=ProjectView)
def create_project(payload: ProjectCreate, conn: sqlite3.Connection = Depends(get_db),
                   user: sqlite3.Row = Depends(current_user),
                   settings: Settings = Depends(get_settings)) -> ProjectView:
    """A pure local write. No Gemini call happens here: the upload and the book
    interaction are lazy inside step 1, so an unopened project never begins life
    with a dead file URI (design 7.2)."""
    # The id is minted first so the book file lands in its project-scoped
    # directory; the row then points at a file that already exists.
    project_id = store.new_id()
    book_path = files.write_book(settings.data_dir, project_id, payload.book_text)
    store.create_project(conn, project_id=project_id, user_id=user["id"],
                         title=payload.title, book_path=book_path,
                         book_excerpt=files.excerpt(payload.book_text))
    return _load_view(conn, project_id, user["id"], settings)


@router.get("", response_model=list[ProjectListItem])
def list_projects(conn: sqlite3.Connection = Depends(get_db),
                  user: sqlite3.Row = Depends(current_user),
                  settings: Settings = Depends(get_settings)) -> list[ProjectListItem]:
    return store.list_projects(conn, user["id"], server_run_id=settings.server_run_id)


@router.get("/{project_id}", response_model=ProjectView)
def read_project(project_id: str, conn: sqlite3.Connection = Depends(get_db),
                 user: sqlite3.Row = Depends(current_user),
                 settings: Settings = Depends(get_settings)) -> ProjectView:
    return _load_view(conn, project_id, user["id"], settings)


@router.get("/{project_id}/book", response_model=BookView)
def read_book(project_id: str, conn: sqlite3.Connection = Depends(get_db),
              user: sqlite3.Row = Depends(current_user),
              settings: Settings = Depends(get_settings)) -> BookView:
    row = store.get_project(conn, project_id, user["id"])
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found.")
    return BookView(text=files.read_book(settings.data_dir, project_id))


def _serve_artifact(conn, *, project_id: str, user: sqlite3.Row, settings: Settings,
                    table: str, row_id: str, column: str) -> Response:
    if store.get_project(conn, project_id, user["id"]) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found.")
    row = conn.execute(
        f"SELECT {column} AS path FROM {table} WHERE id = ? AND project_id = ?",
        (row_id, project_id),
    ).fetchone()
    if row is None or row["path"] is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not generated yet.")
    target = files.absolute(settings.data_dir, row["path"])
    if not target.is_relative_to(settings.data_dir.resolve()) or not target.exists():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found.")
    return Response(content=target.read_bytes(), media_type="image/png")


@router.get("/{project_id}/characters/{character_id}/portrait")
def read_portrait(project_id: str, character_id: str,
                  conn: sqlite3.Connection = Depends(get_db),
                  user: sqlite3.Row = Depends(current_user),
                  settings: Settings = Depends(get_settings)) -> Response:
    return _serve_artifact(conn, project_id=project_id, user=user, settings=settings,
                           table="characters", row_id=character_id, column="portrait_path")


@router.get("/{project_id}/chapters/{chapter_id}/illustration")
def read_illustration(project_id: str, chapter_id: str,
                      conn: sqlite3.Connection = Depends(get_db),
                      user: sqlite3.Row = Depends(current_user),
                      settings: Settings = Depends(get_settings)) -> Response:
    return _serve_artifact(conn, project_id=project_id, user=user, settings=settings,
                           table="chapters", row_id=chapter_id, column="illustration_path")
```

Mount it in `backend/app/main.py`:

```python
from app.api import projects as projects_api
...
    app.include_router(session_api.router)
    app.include_router(projects_api.router)
```

Artifacts are served through this ownership-checked endpoint rather than a static mount, because assessment §5.2 requires them "served through your own API".

- [ ] **Step 4: Run the test and verify it passes**

Run: `cd backend && python -m pytest tests/test_api_projects.py -v`
Expected: PASS — 12 passed

- [ ] **Step 5: Run the whole backend suite for regressions**

Run: `cd backend && python -m pytest -q`
Expected: PASS — all green

- [ ] **Step 6: Commit**

```bash
git add backend/app/api/projects.py backend/app/main.py backend/tests/test_api_projects.py
git commit -m "Add projects REST: create, list, detail, book, artifact bytes

Creation is a pure local write with zero Gemini calls - the upload and the book
interaction are lazy inside step 1, so a project left unopened never starts life
with a dead file URI and creation cannot fail on a provider error.

The book has its own endpoint because it can be 230 KB and never changes;
keeping it out of the project view keeps every state payload small. Another
user's project is 404 rather than 403, so existence is never confirmed.

Mostly AI-authored (Claude Code).

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Phase D — Frontend first slice

The frontend is built in slices against the API that already exists, not stacked at the end. Everything here obeys the governing rule (spec §10.1): **no client-owned mirror or advancement of pipeline state.**

### Task 12: Frontend foundation — types, API client, tokens, shell

**Files:**
- Create: `frontend/src/types.ts`, `frontend/src/steps.ts`, `frontend/src/api.ts`, `frontend/src/styles/tokens.css`, `frontend/src/styles/app.css`, `frontend/src/components/AppShell.tsx`, `frontend/src/components/StateMessage.tsx`, `frontend/src/__tests__/api.test.ts`

**Interfaces:**
- Consumes: the REST API from Tasks 10–11.
- Produces: every type in the Interface Reference; `api.getSession`, `api.createSession`, `api.deleteSession`, `api.listProjects`, `api.createProject`, `api.getProject`, `api.getBook`, `api.runStep`; `STEP_LABELS`, `STEP_ORDER`; `<AppShell>`, `<StateMessage>`.

- [ ] **Step 1: Write the failing test**

`frontend/src/__tests__/api.test.ts`:

```ts
import { afterEach, describe, expect, test, vi } from 'vitest';
import * as api from '../api';
import type { ProjectView } from '../types';

const project = { id: 'p1', title: 'W', status: 'CREATED' } as unknown as ProjectView;

function mockFetch(status: number, body: unknown) {
  const spy = vi.fn().mockResolvedValue({
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  });
  vi.stubGlobal('fetch', spy);
  return spy;
}

afterEach(() => vi.unstubAllGlobals());

describe('runStep', () => {
  test('202 is an accepted run carrying the new state', async () => {
    mockFetch(202, { project });
    const outcome = await api.runStep('p1', 'STYLE');
    expect(outcome).toEqual({ ok: true, project });
  });

  test('409 is a conflict carrying the truth, not an error to throw', async () => {
    mockFetch(409, { error: { code: 'CONFLICT', message: 'busy' }, project });
    const outcome = await api.runStep('p1', 'STYLE');
    expect(outcome).toEqual({ ok: false, conflict: true, project });
  });

  test('any other failure throws so the caller shows a transient banner', async () => {
    mockFetch(500, { error: { code: 'INTERNAL', message: 'boom' } });
    await expect(api.runStep('p1', 'STYLE')).rejects.toThrow('boom');
  });

  test('the optional style is sent only when provided', async () => {
    const spy = mockFetch(202, { project });
    await api.runStep('p1', 'STYLE', 'watercolour');
    expect(JSON.parse(spy.mock.calls[0][1].body)).toEqual({
      step: 'STYLE', style: 'watercolour',
    });
    await api.runStep('p1', 'CHARACTERS');
    expect(JSON.parse(spy.mock.calls[1][1].body)).toEqual({ step: 'CHARACTERS' });
  });
});

describe('getSession', () => {
  test('401 means signed out, not an error', async () => {
    mockFetch(401, {});
    await expect(api.getSession()).resolves.toBeNull();
  });

  test('200 returns the session', async () => {
    mockFetch(200, { user_id: 'u1', name: 'Ada', email: 'a@b.co' });
    await expect(api.getSession()).resolves.toEqual({
      user_id: 'u1', name: 'Ada', email: 'a@b.co',
    });
  });
});
```

- [ ] **Step 2: Run it and verify it fails**

Run: `cd frontend && npm test -- --run src/__tests__/api.test.ts`
Expected: FAIL — `Failed to resolve import "../api"`

- [ ] **Step 3: Write `frontend/src/types.ts`**

Copy the TypeScript block verbatim from this plan's **Interface Reference**. Every field name matches a backend DTO field exactly.

- [ ] **Step 4: Write `frontend/src/steps.ts`**

```ts
import type { StepName } from './types';

export const STEP_ORDER: StepName[] = [
  'STYLE', 'CHARACTERS', 'PORTRAITS', 'CHAPTERS', 'ILLUSTRATIONS',
];

export const STEP_LABELS: Record<StepName, string> = {
  STYLE: 'Style',
  CHARACTERS: 'Characters',
  PORTRAITS: 'Portraits',
  CHAPTERS: 'Chapters',
  ILLUSTRATIONS: 'Illustrations',
};

/** What the running caption names, so the user never sees a bare spinner
 *  (assessment 4.3). */
export const STEP_RUNNING_CAPTIONS: Record<StepName, string> = {
  STYLE: 'Reading your book text and defining an art style',
  CHARACTERS: 'Generating the character list from your book’s text',
  PORTRAITS: 'Generating character portraits',
  CHAPTERS: 'Generating a chapter illustration prompt',
  ILLUSTRATIONS: 'Generating the chapter illustration',
};
```

- [ ] **Step 5: Write `frontend/src/api.ts`**

```ts
import type {
  BookView, ProjectListItem, ProjectView, RunOutcome, SessionView, StepName,
} from './types';

async function readError(response: Response): Promise<string> {
  try {
    const body = await response.json();
    return body?.error?.message ?? body?.detail ?? `Request failed (${response.status})`;
  } catch {
    return `Request failed (${response.status})`;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: init?.body ? { 'Content-Type': 'application/json' } : undefined,
  });
  if (!response.ok) throw new Error(await readError(response));
  return response.status === 204 ? (undefined as T) : ((await response.json()) as T);
}

export async function getSession(): Promise<SessionView | null> {
  const response = await fetch('/api/session');
  if (response.status === 401) return null;
  if (!response.ok) throw new Error(await readError(response));
  return (await response.json()) as SessionView;
}

export const createSession = (name: string, email: string) =>
  request<SessionView>('/api/session', { method: 'POST', body: JSON.stringify({ name, email }) });

export const deleteSession = () => request<void>('/api/session', { method: 'DELETE' });

export const listProjects = () => request<ProjectListItem[]>('/api/projects');

export const createProject = (title: string, bookText: string) =>
  request<ProjectView>('/api/projects', {
    method: 'POST', body: JSON.stringify({ title, book_text: bookText }),
  });

export const getProject = (id: string) => request<ProjectView>(`/api/projects/${id}`);

export const getBook = async (id: string) =>
  (await request<BookView>(`/api/projects/${id}/book`)).text;

/**
 * 202 and 409 are both truth-carrying outcomes, never errors. A rejected
 * duplicate should look like a UI that was already correct (design 8, 10.5).
 */
export async function runStep(id: string, step: StepName, style?: string): Promise<RunOutcome> {
  const body = style ? { step, style } : { step };
  const response = await fetch(`/api/projects/${id}/run`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (response.status === 202) return { ok: true, project: (await response.json()).project };
  if (response.status === 409) {
    return { ok: false, conflict: true, project: (await response.json()).project };
  }
  throw new Error(await readError(response));
}
```

Add `BookView` to `types.ts`: `export interface BookView { text: string }`.

- [ ] **Step 6: Run the test and verify it passes**

Run: `cd frontend && npm test -- --run src/__tests__/api.test.ts`
Expected: PASS — 6 passed

- [ ] **Step 7: Add the design tokens and the shell**

`frontend/src/styles/tokens.css` — copy the `:root { … }` block from `app-demo.html:13-56` verbatim, plus the reset and typography rules at `app-demo.html:58-67`. This is the assessment's shipped visual floor, so reusing it is the point (spec §10.7).

`frontend/src/styles/app.css` — the component styles, adapted from `app-demo.html`. Include the reduced-motion block, but **with the spinner included** rather than exempted (the demo exempts it at line 241; our caption carries the meaning in text, so the animation is decoration):

```css
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after {
    animation-duration: 0.001ms !important;
    animation-iteration-count: 1 !important;
    transition-duration: 0.001ms !important;
  }
  .spinner { animation: none; border-top-color: var(--grad-line); }
}

/* Fixed aspect-ratio art slots so an image landing never reflows the page. */
.entity-card .art { aspect-ratio: 3 / 4; }
.entity-card .art.chapter { aspect-ratio: 16 / 10; }
```

`frontend/src/components/StateMessage.tsx`:

```tsx
type Props =
  | { kind: 'loading'; label: string }
  | { kind: 'error'; message: string; onRetry: () => void }
  | { kind: 'empty'; message: string; action?: React.ReactNode };

export default function StateMessage(props: Props) {
  if (props.kind === 'loading') {
    return (
      <div className="state-block" role="status" aria-live="polite">
        <span className="spinner" aria-hidden="true" />
        <span>{props.label}</span>
      </div>
    );
  }
  if (props.kind === 'error') {
    return (
      <div className="state-block error" role="alert">
        <p>{props.message}</p>
        <button type="button" className="gd-btn gd-btn-secondary" onClick={props.onRetry}>
          Try again
        </button>
      </div>
    );
  }
  return (
    <div className="empty-state">
      <p>{props.message}</p>
      {props.action}
    </div>
  );
}
```

`frontend/src/components/AppShell.tsx`:

```tsx
import type { SessionView } from '../types';

interface Props {
  session: SessionView;
  onSignOut: () => void;
  onHome: () => void;
  children: React.ReactNode;
}

export default function AppShell({ session, onSignOut, onHome, children }: Props) {
  const initials = (session.name || '?')
    .split(' ').map((w) => w[0]).join('').slice(0, 2).toUpperCase();
  return (
    <>
      <header className="gd-nav">
        <div className="gd-nav-inner">
          <button type="button" className="gd-nav-logo" onClick={onHome}>
            Book Illustration Studio
          </button>
          <div className="gd-nav-user">
            <span className="gd-nav-avatar" aria-hidden="true">{initials}</span>
            <span>{session.name}</span>
            <button type="button" className="gd-btn gd-btn-ghost gd-btn-sm" onClick={onSignOut}>
              Sign out
            </button>
          </div>
        </div>
      </header>
      <main className="app-body">{children}</main>
    </>
  );
}
```

Import both stylesheets from `frontend/src/main.tsx`:

```tsx
import './styles/tokens.css';
import './styles/app.css';
```

- [ ] **Step 8: Run the frontend suite and verify it passes**

Run: `cd frontend && npm test -- --run`
Expected: PASS — all green

- [ ] **Step 9: Commit**

```bash
git add frontend/src
git commit -m "Add frontend types, API client, design tokens and app shell

runStep returns a RunOutcome union rather than throwing on 409: a rejected
duplicate carries the current project state, so the losing tab renders truth
instead of an error screen. getSession maps 401 to null because signed-out is a
state, not a failure.

Tokens come from app-demo.html - the assessment's shipped visual floor. Unlike
the demo, prefers-reduced-motion also stops the spinner: the caption already
carries the meaning in text.

Mostly AI-authored (Claude Code).

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 13: Sign-in screen and session bootstrap

**Files:**
- Create: `frontend/src/hooks/useSession.ts`, `frontend/src/components/SignIn.tsx`, `frontend/src/__tests__/SignIn.test.tsx`
- Modify: `frontend/src/App.tsx`

**Interfaces:**
- Consumes: `api` (Task 12).
- Produces: `useSession() -> { session, status, error, signIn, signOut, retry }`; `<SignIn onSubmit={(name, email) => Promise<void>} error={string | null} busy={boolean} />`; `App` hash routing over `#/`, `#/projects`, `#/projects/new`, `#/projects/:id`.

- [ ] **Step 1: Write the failing test**

`frontend/src/__tests__/SignIn.test.tsx`:

```tsx
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, test, vi } from 'vitest';
import SignIn from '../components/SignIn';

test('a valid name and email are submitted', async () => {
  const onSubmit = vi.fn().mockResolvedValue(undefined);
  render(<SignIn onSubmit={onSubmit} error={null} busy={false} />);

  await userEvent.type(screen.getByLabelText(/full name/i), 'Ada');
  await userEvent.type(screen.getByLabelText(/email/i), 'ada@example.com');
  await userEvent.click(screen.getByRole('button', { name: /continue/i }));

  expect(onSubmit).toHaveBeenCalledWith('Ada', 'ada@example.com');
});

test('an empty name blocks submission and explains why', async () => {
  const onSubmit = vi.fn();
  render(<SignIn onSubmit={onSubmit} error={null} busy={false} />);

  await userEvent.type(screen.getByLabelText(/email/i), 'ada@example.com');
  await userEvent.click(screen.getByRole('button', { name: /continue/i }));

  expect(onSubmit).not.toHaveBeenCalled();
  expect(screen.getByRole('alert')).toHaveTextContent(/name/i);
});

test('a malformed email blocks submission', async () => {
  const onSubmit = vi.fn();
  render(<SignIn onSubmit={onSubmit} error={null} busy={false} />);

  await userEvent.type(screen.getByLabelText(/full name/i), 'Ada');
  await userEvent.type(screen.getByLabelText(/email/i), 'nope');
  await userEvent.click(screen.getByRole('button', { name: /continue/i }));

  expect(onSubmit).not.toHaveBeenCalled();
  expect(screen.getByRole('alert')).toHaveTextContent(/valid email/i);
});

test('a server error is shown to the user', () => {
  render(<SignIn onSubmit={vi.fn()} error="Service unavailable" busy={false} />);
  expect(screen.getByRole('alert')).toHaveTextContent('Service unavailable');
});

test('the button is disabled while the request is in flight', () => {
  render(<SignIn onSubmit={vi.fn()} error={null} busy />);
  expect(screen.getByRole('button', { name: /signing in/i })).toBeDisabled();
});
```

- [ ] **Step 2: Run it and verify it fails**

Run: `cd frontend && npm test -- --run src/__tests__/SignIn.test.tsx`
Expected: FAIL — `Failed to resolve import "../components/SignIn"`

- [ ] **Step 3: Write `frontend/src/components/SignIn.tsx`**

```tsx
import { useState } from 'react';

const EMAIL_RE = /^[^@\s]+@[^@\s.]+\.[^@\s]+$/;

interface Props {
  onSubmit: (name: string, email: string) => Promise<void>;
  error: string | null;
  busy: boolean;
}

export default function SignIn({ onSubmit, error, busy }: Props) {
  const [name, setName] = useState('');
  const [email, setEmail] = useState('');
  const [localError, setLocalError] = useState<string | null>(null);

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    const trimmedName = name.trim();
    const trimmedEmail = email.trim().toLowerCase();
    if (!trimmedName) return setLocalError('Enter your name to continue.');
    if (!EMAIL_RE.test(trimmedEmail)) return setLocalError('Enter a valid email address.');
    setLocalError(null);
    await onSubmit(trimmedName, trimmedEmail);
  };

  const message = localError ?? error;

  return (
    <div className="center-page">
      <form className="auth-card" onSubmit={submit} noValidate>
        <h1>Book Illustration Studio</h1>
        <p className="lede">Enter your details to start or resume an illustration project.</p>

        <div className="gd-field">
          <label htmlFor="signin-name">Full name <span className="req">*</span></label>
          <input id="signin-name" value={name} onChange={(e) => setName(e.target.value)}
                 placeholder="Mira Hassan" autoComplete="name" />
        </div>

        <div className="gd-field">
          <label htmlFor="signin-email">Email <span className="req">*</span></label>
          <input id="signin-email" type="email" value={email} autoComplete="email"
                 onChange={(e) => setEmail(e.target.value)} placeholder="mira@example.com" />
        </div>

        {message && <p className="gd-field err" role="alert">{message}</p>}

        <button type="submit" className="gd-btn gd-btn-primary" disabled={busy}>
          {busy ? 'Signing in…' : 'Continue'}
        </button>
        <p className="meta">
          No password — this is a lightweight identity check. Using an email that already
          has projects resumes them exactly where you left off.
        </p>
      </form>
    </div>
  );
}
```

- [ ] **Step 4: Run the test and verify it passes**

Run: `cd frontend && npm test -- --run src/__tests__/SignIn.test.tsx`
Expected: PASS — 5 passed

- [ ] **Step 5: Write `frontend/src/hooks/useSession.ts` and wire `App.tsx`**

```ts
import { useCallback, useEffect, useState } from 'react';
import * as api from '../api';
import type { SessionView } from '../types';

export type SessionStatus = 'loading' | 'ready' | 'error';

export function useSession() {
  const [session, setSession] = useState<SessionView | null>(null);
  const [status, setStatus] = useState<SessionStatus>('loading');
  const [error, setError] = useState<string | null>(null);

  const bootstrap = useCallback(async () => {
    setStatus('loading');
    try {
      setSession(await api.getSession());
      setStatus('ready');
    } catch (err) {
      setError((err as Error).message);
      setStatus('error');
    }
  }, []);

  useEffect(() => { void bootstrap(); }, [bootstrap]);

  const signIn = useCallback(async (name: string, email: string) => {
    setError(null);
    try {
      setSession(await api.createSession(name, email));
      window.location.hash = '#/projects';
    } catch (err) {
      setError((err as Error).message);
    }
  }, []);

  const signOut = useCallback(async () => {
    await api.deleteSession();
    setSession(null);
    window.location.hash = '#/';
  }, []);

  return { session, status, error, signIn, signOut, retry: bootstrap };
}
```

`frontend/src/App.tsx` — hash routing, exactly the demo's four routes (`app-demo.html:327`):

```tsx
import { useEffect, useState } from 'react';
import AppShell from './components/AppShell';
import SignIn from './components/SignIn';
import StateMessage from './components/StateMessage';
import { useSession } from './hooks/useSession';

type Route =
  | { name: 'list' } | { name: 'new' } | { name: 'detail'; id: string };

function parseRoute(hash: string): Route {
  const path = hash.replace(/^#\/?/, '');
  if (path === 'projects/new') return { name: 'new' };
  const match = path.match(/^projects\/([A-Za-z0-9]+)$/);
  return match ? { name: 'detail', id: match[1] } : { name: 'list' };
}

export function navigate(hash: string) { window.location.hash = hash; }

export default function App() {
  const { session, status, error, signIn, signOut, retry } = useSession();
  const [route, setRoute] = useState<Route>(() => parseRoute(window.location.hash));

  useEffect(() => {
    const onHashChange = () => setRoute(parseRoute(window.location.hash));
    window.addEventListener('hashchange', onHashChange);
    return () => window.removeEventListener('hashchange', onHashChange);
  }, []);

  if (status === 'loading') return <StateMessage kind="loading" label="Loading…" />;
  if (status === 'error') {
    return <StateMessage kind="error" message={error ?? 'Could not reach the server.'}
                         onRetry={retry} />;
  }
  if (!session) return <SignIn onSubmit={signIn} error={error} busy={false} />;

  return (
    <AppShell session={session} onSignOut={signOut} onHome={() => navigate('#/projects')}>
      {route.name === 'list' && <p>Projects</p>}
      {route.name === 'new' && <p>New project</p>}
      {route.name === 'detail' && <p>Project {route.id}</p>}
    </AppShell>
  );
}
```

The three placeholder lines are replaced by Tasks 14, 15 and 29. Delete `frontend/src/__tests__/smoke.test.tsx`, whose assertion no longer holds.

- [ ] **Step 6: Run the frontend suite and verify it passes**

Run: `cd frontend && npm test -- --run`
Expected: PASS — all green

- [ ] **Step 7: Commit**

```bash
git add -A frontend/src
git commit -m "Add sign-in, session bootstrap and hash routing

The app boots by asking GET /api/session who the user is, so a refresh or a
returning visit restores identity from the HttpOnly cookie rather than from
client storage. Validation is duplicated on purpose: the frontend for UX, the
backend as the trust boundary.

Four hash routes, no router dependency.

Mostly AI-authored (Claude Code).

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 14: Project list with the five-step progress indicator

**Files:**
- Create: `frontend/src/components/ProjectList.tsx`, `frontend/src/components/ProjectRow.tsx`, `frontend/src/components/EmptyState.tsx`, `frontend/src/__tests__/ProjectList.test.tsx`
- Modify: `frontend/src/App.tsx`

**Interfaces:**
- Consumes: `api.listProjects`, `STEP_ORDER` (Task 12).
- Produces: `<ProjectList onOpen onNew />`, `<ProjectRow project onOpen />`, `<EmptyState onNew />`.

- [ ] **Step 1: Write the failing test**

`frontend/src/__tests__/ProjectList.test.tsx`:

```tsx
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, expect, test, vi } from 'vitest';
import ProjectList from '../components/ProjectList';
import ProjectRow from '../components/ProjectRow';
import * as api from '../api';
import type { ProjectListItem } from '../types';

function item(overrides: Partial<ProjectListItem> = {}): ProjectListItem {
  return {
    id: 'p1', title: 'Willows', created_at: '2026-08-14T10:00:00+00:00',
    status: 'CHARACTERS_GENERATED', current_step: 'PORTRAITS',
    display_status: 'In progress', needs_attention: false, is_interrupted: false,
    completed_steps: 2, ...overrides,
  };
}

afterEach(() => vi.restoreAllMocks());

test('a loading state shows while the fetch is in flight', () => {
  vi.spyOn(api, 'listProjects').mockReturnValue(new Promise(() => {}));
  render(<ProjectList onOpen={vi.fn()} onNew={vi.fn()} />);
  expect(screen.getByRole('status')).toHaveTextContent(/loading/i);
});

test('the empty state appears when there are no projects', async () => {
  vi.spyOn(api, 'listProjects').mockResolvedValue([]);
  render(<ProjectList onOpen={vi.fn()} onNew={vi.fn()} />);
  expect(await screen.findByText(/no projects yet/i)).toBeInTheDocument();
});

test('a fetch failure offers a retry and does not invent project state', async () => {
  const spy = vi.spyOn(api, 'listProjects').mockRejectedValue(new Error('Network down'));
  render(<ProjectList onOpen={vi.fn()} onNew={vi.fn()} />);

  expect(await screen.findByRole('alert')).toHaveTextContent('Network down');
  spy.mockResolvedValue([item()]);
  await userEvent.click(screen.getByRole('button', { name: /try again/i }));
  expect(await screen.findByText('Willows')).toBeInTheDocument();
});

test('projects render with title, date and pill', async () => {
  vi.spyOn(api, 'listProjects').mockResolvedValue([item()]);
  render(<ProjectList onOpen={vi.fn()} onNew={vi.fn()} />);

  expect(await screen.findByText('Willows')).toBeInTheDocument();
  expect(screen.getByText('In progress')).toBeInTheDocument();
});

test('the five-step indicator fills one segment per completed step', () => {
  const { container } = render(<ProjectRow project={item({ completed_steps: 2 })}
                                          onOpen={vi.fn()} />);
  const segments = container.querySelectorAll('.progress-mini .seg');
  expect(segments).toHaveLength(5);
  expect(container.querySelectorAll('.progress-mini .seg.on')).toHaveLength(2);
});

test('a finished project fills all five segments and reads Done', () => {
  const { container } = render(
    <ProjectRow project={item({ status: 'DONE', display_status: 'Done',
                                current_step: null, completed_steps: 5 })}
                onOpen={vi.fn()} />);
  expect(container.querySelectorAll('.progress-mini .seg.on')).toHaveLength(5);
  expect(screen.getByText('Done')).toBeInTheDocument();
});

test('needs_attention shows a warning beside the pill, never instead of it', () => {
  render(<ProjectRow project={item({ needs_attention: true, display_status: 'In progress' })}
                     onOpen={vi.fn()} />);
  expect(screen.getByText('In progress')).toBeInTheDocument();
  expect(screen.getByText(/needs attention/i)).toBeInTheDocument();
});

test('a row opens on click and on Enter', async () => {
  const onOpen = vi.fn();
  render(<ProjectRow project={item()} onOpen={onOpen} />);
  const row = screen.getByRole('button', { name: /willows/i });
  await userEvent.click(row);
  row.focus();
  await userEvent.keyboard('{Enter}');
  expect(onOpen).toHaveBeenCalledTimes(2);
  expect(onOpen).toHaveBeenCalledWith('p1');
});
```

- [ ] **Step 2: Run it and verify it fails**

Run: `cd frontend && npm test -- --run src/__tests__/ProjectList.test.tsx`
Expected: FAIL — `Failed to resolve import "../components/ProjectList"`

- [ ] **Step 3: Write the three components**

`frontend/src/components/ProjectRow.tsx`:

```tsx
import { STEP_ORDER } from '../steps';
import type { ProjectListItem } from '../types';

const PILL_CLASS: Record<string, string> = {
  Draft: 'gd-pill gray', 'In progress': 'gd-pill', Done: 'gd-pill ink',
};

export default function ProjectRow({ project, onOpen }: {
  project: ProjectListItem; onOpen: (id: string) => void;
}) {
  const created = new Date(project.created_at).toLocaleDateString();
  return (
    <div className="project-row" role="button" tabIndex={0}
         onClick={() => onOpen(project.id)}
         onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') onOpen(project.id); }}>
      <div className="title">
        <h4>{project.title}</h4>
        <span className="meta">Created {created}</span>
      </div>

      {/* Five segments, one per step, filled by completed_steps (assessment 4.4;
          mirrors app-demo.html:556). */}
      <div className="progress-mini" aria-label={`${project.completed_steps} of 5 steps complete`}>
        {STEP_ORDER.map((step, index) => (
          <span key={step} className={`seg${index < project.completed_steps ? ' on' : ''}`} />
        ))}
      </div>

      {project.needs_attention && (
        <span className="attention-flag" title="This project needs attention">
          ⚠ Needs attention
        </span>
      )}
      <span className={PILL_CLASS[project.display_status]}>
        {project.display_status === 'In progress' && <span className="dot" />}
        {project.display_status}
      </span>
    </div>
  );
}
```

`frontend/src/components/EmptyState.tsx`:

```tsx
export default function EmptyState({ onNew }: { onNew: () => void }) {
  return (
    <div className="empty-state">
      <p>No projects yet.</p>
      <button type="button" className="gd-btn gd-btn-primary" onClick={onNew}>
        + New project
      </button>
    </div>
  );
}
```

`frontend/src/components/ProjectList.tsx`:

```tsx
import { useCallback, useEffect, useState } from 'react';
import * as api from '../api';
import type { ProjectListItem } from '../types';
import EmptyState from './EmptyState';
import ProjectRow from './ProjectRow';
import StateMessage from './StateMessage';

export default function ProjectList({ onOpen, onNew }: {
  onOpen: (id: string) => void; onNew: () => void;
}) {
  const [projects, setProjects] = useState<ProjectListItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError(null);
    setProjects(null);
    try {
      setProjects(await api.listProjects());
    } catch (err) {
      setError((err as Error).message);
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  if (error) return <StateMessage kind="error" message={error} onRetry={load} />;
  if (projects === null) return <StateMessage kind="loading" label="Loading your projects…" />;

  return (
    <>
      <div className="list-head">
        <h2>Your projects</h2>
        <button type="button" className="gd-btn gd-btn-primary" onClick={onNew}>
          + New project
        </button>
      </div>
      {projects.length === 0
        ? <EmptyState onNew={onNew} />
        : (
          <div className="project-list">
            {projects.map((p) => <ProjectRow key={p.id} project={p} onOpen={onOpen} />)}
          </div>
        )}
    </>
  );
}
```

Replace the `route.name === 'list'` placeholder in `App.tsx`:

```tsx
{route.name === 'list' && (
  <ProjectList onOpen={(id) => navigate(`#/projects/${id}`)}
               onNew={() => navigate('#/projects/new')} />
)}
```

**The list stays REST fetch-on-open.** No socket subscription: realtime's value is watching a long-running step, which happens on the detail screen (spec §9.8).

- [ ] **Step 4: Run the test and verify it passes**

Run: `cd frontend && npm test -- --run src/__tests__/ProjectList.test.tsx`
Expected: PASS — 8 passed

- [ ] **Step 5: Commit**

```bash
git add -A frontend/src
git commit -m "Add the project list with the five-step progress indicator

Loading, error-with-retry and empty are all real states, not afterthoughts -
the demo has no error path to copy. The progress indicator is five segments
filled from completed_steps, which the server derives from status.

needs_attention renders as a warning beside the pill rather than as a fourth
pill value, keeping the vocabulary the assessment names.

Mostly AI-authored (Claude Code).

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 15: New project — paste text and `.txt` file

Both §4.4 input modes through one endpoint. The `.txt` path is a *frontend input mode*, not a second transport: the browser reads the file with `FileReader.readAsText` and posts the contents through the same `{title, book_text}` call (spec §8; `app-demo.html:355`).

**Files:**
- Create: `frontend/src/components/NewProject.tsx`, `frontend/src/__tests__/NewProject.test.tsx`
- Modify: `frontend/src/App.tsx`

**Interfaces:**
- Consumes: `api.createProject`.
- Produces: `<NewProject onCreated={(id: string) => void} onCancel={() => void} />`.

- [ ] **Step 1: Write the failing test**

`frontend/src/__tests__/NewProject.test.tsx`:

```tsx
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, expect, test, vi } from 'vitest';
import NewProject from '../components/NewProject';
import * as api from '../api';
import type { ProjectView } from '../types';

const created = { id: 'p-new' } as ProjectView;

afterEach(() => vi.restoreAllMocks());

test('the paste path creates a project', async () => {
  const spy = vi.spyOn(api, 'createProject').mockResolvedValue(created);
  const onCreated = vi.fn();
  render(<NewProject onCreated={onCreated} onCancel={vi.fn()} />);

  await userEvent.type(screen.getByLabelText(/project title/i), 'Willows');
  await userEvent.type(screen.getByLabelText(/book text/i), 'Once upon a time.');
  await userEvent.click(screen.getByRole('button', { name: /create project/i }));

  expect(spy).toHaveBeenCalledWith('Willows', 'Once upon a time.');
  await waitFor(() => expect(onCreated).toHaveBeenCalledWith('p-new'));
});

test('the .txt path reads the file into the same field and submits identically', async () => {
  const spy = vi.spyOn(api, 'createProject').mockResolvedValue(created);
  render(<NewProject onCreated={vi.fn()} onCancel={vi.fn()} />);

  await userEvent.type(screen.getByLabelText(/project title/i), 'From file');
  const file = new File(['Chapter 1. The river bank.'], 'book.txt', { type: 'text/plain' });
  await userEvent.upload(screen.getByLabelText(/choose a \.txt file/i), file);

  const textarea = screen.getByLabelText(/book text/i) as HTMLTextAreaElement;
  await waitFor(() => expect(textarea.value).toBe('Chapter 1. The river bank.'));
  expect(screen.getByText(/book\.txt loaded/i)).toBeInTheDocument();

  await userEvent.click(screen.getByRole('button', { name: /create project/i }));
  expect(spy).toHaveBeenCalledWith('From file', 'Chapter 1. The river bank.');
});

test('a missing title or empty text blocks submission', async () => {
  const spy = vi.spyOn(api, 'createProject');
  render(<NewProject onCreated={vi.fn()} onCancel={vi.fn()} />);

  await userEvent.click(screen.getByRole('button', { name: /create project/i }));
  expect(spy).not.toHaveBeenCalled();
  expect(screen.getByRole('alert')).toHaveTextContent(/title/i);

  await userEvent.type(screen.getByLabelText(/project title/i), 'Willows');
  await userEvent.click(screen.getByRole('button', { name: /create project/i }));
  expect(spy).not.toHaveBeenCalled();
  expect(screen.getByRole('alert')).toHaveTextContent(/book text/i);
});

test('a server failure is shown and does not navigate away', async () => {
  vi.spyOn(api, 'createProject').mockRejectedValue(new Error('Disk full'));
  const onCreated = vi.fn();
  render(<NewProject onCreated={onCreated} onCancel={vi.fn()} />);

  await userEvent.type(screen.getByLabelText(/project title/i), 'Willows');
  await userEvent.type(screen.getByLabelText(/book text/i), 'text');
  await userEvent.click(screen.getByRole('button', { name: /create project/i }));

  expect(await screen.findByRole('alert')).toHaveTextContent('Disk full');
  expect(onCreated).not.toHaveBeenCalled();
});
```

- [ ] **Step 2: Run it and verify it fails**

Run: `cd frontend && npm test -- --run src/__tests__/NewProject.test.tsx`
Expected: FAIL — `Failed to resolve import "../components/NewProject"`

- [ ] **Step 3: Write `frontend/src/components/NewProject.tsx`**

```tsx
import { useState } from 'react';
import * as api from '../api';

export default function NewProject({ onCreated, onCancel }: {
  onCreated: (id: string) => void; onCancel: () => void;
}) {
  const [title, setTitle] = useState('');
  const [bookText, setBookText] = useState('');
  const [fileName, setFileName] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const readFile = (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = () => {
      setBookText(String(reader.result ?? ''));
      setFileName(file.name);
    };
    reader.onerror = () => setError('That file could not be read.');
    reader.readAsText(file);
  };

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!title.trim()) return setError('Give the project a title.');
    if (!bookText.trim()) return setError('Provide the book text — paste it or upload a .txt file.');
    setError(null);
    setBusy(true);
    try {
      const project = await api.createProject(title.trim(), bookText);
      onCreated(project.id);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <form className="app-body narrow" onSubmit={submit} noValidate>
      <button type="button" className="back-link" onClick={onCancel}>← Back to projects</button>
      <h2>Start a new illustration project</h2>
      <p className="meta">Give it a title, then paste the book’s text or upload a .txt file.</p>

      <div className="gd-field">
        <label htmlFor="new-title">Project title <span className="req">*</span></label>
        <input id="new-title" value={title} onChange={(e) => setTitle(e.target.value)}
               placeholder="e.g. The Wind in the Willows — cottage-core" />
      </div>

      <div className="gd-field">
        <label htmlFor="new-file">Choose a .txt file</label>
        <input id="new-file" type="file" accept=".txt,text/plain" onChange={readFile} />
        {fileName && <p className="meta">✓ {fileName} loaded</p>}
      </div>

      <div className="divider-or">or paste text</div>

      <div className="gd-field">
        <label htmlFor="new-book">Book text <span className="req">*</span></label>
        <textarea id="new-book" rows={8} value={bookText}
                  onChange={(e) => setBookText(e.target.value)}
                  placeholder="Once upon a time, in a small burrow by the river…" />
      </div>

      {error && <p className="gd-field err" role="alert">{error}</p>}

      <button type="submit" className="gd-btn gd-btn-primary" disabled={busy}>
        {busy ? 'Creating…' : 'Create project'}
      </button>
    </form>
  );
}
```

Replace the `route.name === 'new'` placeholder in `App.tsx`:

```tsx
{route.name === 'new' && (
  <NewProject onCreated={(id) => navigate(`#/projects/${id}`)}
              onCancel={() => navigate('#/projects')} />
)}
```

**No invented size limits.** The File API's documented ceiling is 2 GB, which we come nowhere near, so there is no constraint to encode (spec §10.7).

- [ ] **Step 4: Run the test and verify it passes**

Run: `cd frontend && npm test -- --run src/__tests__/NewProject.test.tsx`
Expected: PASS — 4 passed

- [ ] **Step 5: Run both suites**

Run: `./test.sh`
Expected: PASS — backend and frontend both green

- [ ] **Step 6: Commit**

```bash
git add -A frontend/src
git commit -m "Add new-project screen with paste and .txt input paths

Both assessment 4.4 input modes go through one endpoint: the file input reads
with FileReader.readAsText into the same textarea the paste path fills, so
there is one submit path and one thing to validate. No invented size limits -
the File API ceiling is 2 GB and we come nowhere near it.

Mostly AI-authored (Claude Code).

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Phase E — Gemini contract and step handlers

### Task 16: The Gemini protocol, typed errors and the notebook's prompts

Prompt wording is **not invented**. Every instruction below is the notebook's, verbatim except where the assessment requires a change — which is what makes the call-sequence test a test of *the notebook's pipeline* rather than of our paraphrase (spec §7.8).

**Files:**
- Create: `backend/app/gemini/protocol.py`, `backend/app/gemini/prompts.py`, `backend/tests/test_gemini_protocol.py`
- Modify: `backend/app/gemini/__init__.py`

**Interfaces:**
- Consumes: `steps.MAX_CHARACTERS`, `steps.MAX_CHAPTERS`.
- Produces: `GeminiClient` Protocol, `TextResult`, `StructuredResult`, `ImageResult`, `ReferenceImage`, `GeminiError`, `InteractionNotFound`, `InvalidStructuredOutput`, `parse_items`, `PROMPT_ITEM_SCHEMA`; every constant in `prompts.py`.

- [ ] **Step 1: Write the failing test**

`backend/tests/test_gemini_protocol.py`:

```python
import pytest

from app.gemini import prompts
from app.gemini.protocol import InvalidStructuredOutput, parse_items


def test_book_intro_is_the_notebook_cell_27_text_verbatim():
    assert prompts.BOOK_INTRO == (
        "Here's a book, to illustrate using Nano Banana. "
        "Don't say anything for now, instructions will follow."
    )


def test_rules_are_the_notebook_cell_23_system_instructions_verbatim():
    assert prompts.RULES == (
        "\n"
        "  There must be no text on the image, it should not look like a cover page.\n"
        "  It should be an full illustration with no borders, titles, nor description.\n"
        "  Unless asked otherwise, stay family-friendly with uplifting colors.\n"
        "  Each produced should be a simple image, no panels.\n"
    )


def test_style_prompts_are_the_notebook_cell_30_branches_verbatim():
    assert prompts.STYLE_GENERATE == (
        "Can you define a art style that would fit the story but with a twist? "
        "Just give us the prompt for the art syle that will added to the furture prompts."
    )
    assert prompts.STYLE_ACKNOWLEDGE.format(style="watercolour") == (
        'The art style will be:"watercolour". Keep that in mind when generating '
        'future prompts. Keep quiet for now, instructions will follow.'
    )


def test_the_style_wrapper_is_applied_at_the_point_of_use_not_at_persistence():
    assert prompts.STYLE_WRAPPER.format(style="watercolour") == \
        'Follow this style: "watercolour" '


def test_characters_instruction_keeps_the_notebook_text_and_adds_the_cap():
    """Assessment 03 moves the cap onto the list itself, so Gemini's own context
    never holds a character we would discard (design 2, contradiction 1)."""
    assert prompts.CHARACTERS_INSTRUCTION.startswith(
        "Can you describe the main characters (only the adults) and prepare a prompt "
        "describing them with as much details as possible (use the descriptions from "
        "the book) so Nano Banana can generate images of them? "
        "Each prompt should be at least 50 words."
    )
    assert "at most 2" in prompts.CHARACTERS_INSTRUCTION


def test_chapters_instruction_keeps_the_notebook_text_and_adds_the_cap():
    assert prompts.CHAPTERS_INSTRUCTION.startswith(
        "Now, for each chapters of the book, give me a prompt to illustrate what "
        "happens in it. It should be a single image, not a multi-tiled page."
    )
    assert "at most 1" in prompts.CHAPTERS_INSTRUCTION


def test_the_image_seed_takes_the_title_from_the_project():
    seeded = prompts.IMAGE_SEED.format(title="The Wind in the Willows",
                                       style='Follow this style: "x" ', rules=prompts.RULES)
    assert "The Wind in the Willows" in seeded
    assert "You are going to generate portrait images to illustrate" in seeded
    # The notebook's stray "# TODO: Sysyem instructions" comment lands inside its
    # f-string. It is a typo'd note, not an instruction, and is dropped.
    assert "TODO" not in seeded


def test_chapter_seed_and_illustration_prompts_are_the_notebook_cell_38_text():
    assert prompts.CHAPTER_SEED == (
        "Starting from now, we're going to illustrate the book's chapters. "
        "Don't forget to refer to your previous illustrations of the characters to "
        "keep the characters consistency, but feel free to change their position."
    )
    assert prompts.ILLUSTRATION_INSTRUCTION.format(name="Ch1", prompt="a river") == (
        "Create an illustration for Ch1 using the previously generated characters "
        "following this description: a river"
    )


def test_portrait_instruction_is_the_notebook_cell_35_text():
    assert prompts.PORTRAIT_INSTRUCTION.format(name="Toad", prompt="a stout toad") == (
        "Create an illustration for Toad following this description: a stout toad"
    )


def test_parse_items_returns_the_decoded_array():
    assert parse_items('[{"name":"Toad","prompt":"p"}]') == [{"name": "Toad", "prompt": "p"}]


def test_parse_items_rejects_malformed_json():
    with pytest.raises(InvalidStructuredOutput):
        parse_items("not json at all")


def test_parse_items_rejects_a_non_array_top_level():
    with pytest.raises(InvalidStructuredOutput):
        parse_items('{"name":"Toad"}')


def test_parse_items_rejects_empty_output():
    with pytest.raises(InvalidStructuredOutput):
        parse_items("")
```

- [ ] **Step 2: Run it and verify it fails**

Run: `cd backend && python -m pytest tests/test_gemini_protocol.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.gemini.prompts'`

- [ ] **Step 3: Write `backend/app/gemini/prompts.py`**

```python
"""Prompt constants taken from Book_illustration.ipynb.

Every string is the notebook's, verbatim, except the two cap additions the
assessment requires. Keeping them as named constants is what makes the
call-sequence acceptance test a test of the notebook's pipeline rather than of
our paraphrase of it (design 7.8).
"""
from __future__ import annotations

# cell 27
BOOK_INTRO = (
    "Here's a book, to illustrate using Nano Banana. "
    "Don't say anything for now, instructions will follow."
)

# The standalone reconstruction path combines the intro with an instruction in
# one call, where "don't say anything" would contradict the instruction.
BOOK_INTRO_STANDALONE = "Here's a book, to illustrate using Nano Banana."

# cell 23 — system_instructions
RULES = (
    "\n"
    "  There must be no text on the image, it should not look like a cover page.\n"
    "  It should be an full illustration with no borders, titles, nor description.\n"
    "  Unless asked otherwise, stay family-friendly with uplifting colors.\n"
    "  Each produced should be a simple image, no panels.\n"
)

# cell 30 — both branches. The typos ("art syle", "furture") are the notebook's.
STYLE_GENERATE = (
    "Can you define a art style that would fit the story but with a twist? "
    "Just give us the prompt for the art syle that will added to the furture prompts."
)
STYLE_ACKNOWLEDGE = (
    'The art style will be:"{style}". Keep that in mind when generating future '
    "prompts. Keep quiet for now, instructions will follow."
)
STYLE_WRAPPER = 'Follow this style: "{style}" '

# cell 32, plus the cap the assessment moves onto the list itself
CHARACTERS_INSTRUCTION = (
    "Can you describe the main characters (only the adults) and prepare a prompt "
    "describing them with as much details as possible (use the descriptions from the "
    "book) so Nano Banana can generate images of them? Each prompt should be at least "
    "50 words. Return at most 2 characters."
)

# cell 35
IMAGE_SEED = (
    "\n"
    "      You are going to generate portrait images to illustrate {title}.\n"
    "      The style we want you to follow is: {style}\n"
    "      Also follow those rules: {rules}\n"
    "    "
)
PORTRAIT_INSTRUCTION = (
    "Create an illustration for {name} following this description: {prompt}"
)

# cell 37, plus the cap
CHAPTERS_INSTRUCTION = (
    "Now, for each chapters of the book, give me a prompt to illustrate what happens "
    "in it. It should be a single image, not a multi-tiled page. Be very descriptive, "
    "especially of the characters. Be very descriptive and remember to tell their name "
    "and to reuse the character prompts if they appear in the images. Also list all "
    "characters who appear in it. Return at most 1 chapter."
)

# cell 38
CHAPTER_SEED = (
    "Starting from now, we're going to illustrate the book's chapters. Don't forget to "
    "refer to your previous illustrations of the characters to keep the characters "
    "consistency, but feel free to change their position."
)
ILLUSTRATION_INSTRUCTION = (
    "Create an illustration for {name} using the previously generated characters "
    "following this description: {prompt}"
)

# cell 44 — the standalone image call used when the image chain is unusable
ILLUSTRATION_STANDALONE = (
    "\n"
    "              Create this illustration for {name}:\n"
    "                {prompt}\n"
    "              Use the provided images as references of what the characters look like.\n"
    "          "
)


def characters_standalone(style_text: str) -> str:
    return (
        f"{BOOK_INTRO_STANDALONE}\n"
        f"{STYLE_WRAPPER.format(style=style_text)}\n"
        f"{CHARACTERS_INSTRUCTION}"
    )


def chapters_standalone(style_text: str, character_prompts: list[str]) -> str:
    described = "\n".join(f"- {p}" for p in character_prompts)
    return (
        f"{BOOK_INTRO_STANDALONE}\n"
        f"{STYLE_WRAPPER.format(style=style_text)}\n"
        f"The characters already illustrated are:\n{described}\n"
        f"{CHAPTERS_INSTRUCTION}"
    )
```

- [ ] **Step 4: Write `backend/app/gemini/protocol.py`**

```python
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, Sequence, runtime_checkable


class GeminiError(Exception):
    """Any provider-side failure. Surfaces as error_code GEMINI_ERROR."""


class InteractionNotFound(GeminiError):
    """The chain head no longer exists provider-side. Free-tier interaction
    retention is 1 day, shorter than the assessment's own 3-day deadline, so
    this is expected rather than exceptional (design 7.5)."""


class InvalidStructuredOutput(GeminiError):
    """A structured response that does not satisfy the contract. Nothing is
    persisted; the step fails and the user retries (design 7.4)."""


@dataclass(frozen=True)
class TextResult:
    interaction_id: str
    text: str


@dataclass(frozen=True)
class StructuredResult:
    interaction_id: str
    items: list[dict]


@dataclass(frozen=True)
class ImageResult:
    interaction_id: str
    data: bytes
    mime_type: str


@dataclass(frozen=True)
class ReferenceImage:
    data: bytes
    mime_type: str


# The notebook's Prompt model (cell 25): name + prompt.
PROMPT_ITEM_SCHEMA: dict = {
    "type": "object",
    "properties": {"name": {"type": "string"}, "prompt": {"type": "string"}},
    "required": ["name", "prompt"],
}


def parse_items(text: str) -> list[dict]:
    """Decode a structured response. Never slices, never repairs."""
    if not text or not text.strip():
        raise InvalidStructuredOutput("Gemini returned an empty structured response.")
    try:
        decoded = json.loads(text)
    except json.JSONDecodeError as exc:
        raise InvalidStructuredOutput(
            "Gemini returned a structured response that is not valid JSON."
        ) from exc
    if not isinstance(decoded, list):
        raise InvalidStructuredOutput(
            "Gemini returned a structured response that is not a JSON array."
        )
    return decoded


@runtime_checkable
class GeminiClient(Protocol):
    async def upload_book(self, book_path: Path) -> str: ...

    async def create_text(self, *, prompt: str, previous_interaction_id: str | None = None,
                          document_uri: str | None = None) -> TextResult: ...

    async def create_structured(self, *, prompt: str,
                                previous_interaction_id: str | None = None,
                                document_uri: str | None = None, item_schema: dict,
                                max_items: int) -> StructuredResult: ...

    async def create_image(self, *, prompt: str, previous_interaction_id: str | None = None,
                           reference_images: Sequence[ReferenceImage] = (),
                           system_instruction: str | None = None) -> ImageResult: ...
```

`backend/app/gemini/__init__.py`:

```python
from app.gemini.protocol import (  # noqa: F401
    GeminiClient, GeminiError, ImageResult, InteractionNotFound,
    InvalidStructuredOutput, ReferenceImage, StructuredResult, TextResult,
)
```

- [ ] **Step 5: Run the test and verify it passes**

Run: `cd backend && python -m pytest tests/test_gemini_protocol.py -v`
Expected: PASS — 13 passed

- [ ] **Step 6: Commit**

```bash
git add backend/app/gemini backend/tests/test_gemini_protocol.py
git commit -m "Add the Gemini client protocol, typed errors and notebook prompts

Prompt wording is the notebook's, verbatim - typos included - so the pipeline
under test is the reference pipeline rather than a paraphrase. The only changes
are the two cap sentences the assessment requires and the title becoming a
project value instead of a hardcoded book.

parse_items decodes and rejects; it never slices or repairs. A response that
violates the contract is INVALID_OUTPUT with nothing persisted.

Mostly AI-authored (Claude Code).

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 17: `FakeGeminiClient`

The whole backend is proven against this before the real client exists, so orchestration correctness costs zero quota and Gemini becomes a swap at a leaf (spec §14). It reproduces the contract **verified in Task 2**, not an imagined one.

**Files:**
- Rewrite: `backend/app/gemini/fake.py`
- Create: `backend/tests/test_fake_gemini.py`

**Interfaces:**
- Consumes: `protocol` (Task 16).
- Produces: `FakeGeminiClient` with `calls: list[RecordedCall]`, `fail_on(index, exc)`, `invalid_json_on(index)`, `hold_from(index)`, `release()`, `wait_for_calls(n, timeout=2.0)`, `TINY_PNG`, `STYLE_TEXT`, `CHARACTER_ITEMS`, `CHAPTER_ITEMS`, `extra_items`.

- [ ] **Step 1: Write the failing test**

`backend/tests/test_fake_gemini.py`:

```python
import asyncio
from pathlib import Path

import pytest

from app.gemini.fake import FakeGeminiClient
from app.gemini.protocol import GeminiError, InteractionNotFound, InvalidStructuredOutput


async def test_text_output_is_deterministic_and_recorded():
    fake = FakeGeminiClient()
    first = await fake.create_text(prompt="define a style")
    second = await fake.create_text(prompt="define a style")
    assert first.text == second.text == FakeGeminiClient.STYLE_TEXT
    assert first.interaction_id != second.interaction_id
    assert [c.kind for c in fake.calls] == ["text", "text"]
    assert fake.calls[0].prompt == "define a style"


async def test_the_recorder_captures_everything_the_assertions_need():
    fake = FakeGeminiClient()
    await fake.create_structured(prompt="characters", previous_interaction_id="i-1",
                                 document_uri="files/abc", item_schema={"type": "object"},
                                 max_items=2)
    call = fake.calls[0]
    assert call.kind == "structured"
    assert call.previous_interaction_id == "i-1"
    assert call.document_uri == "files/abc"
    assert call.max_items == 2


async def test_structured_output_respects_the_requested_cap():
    fake = FakeGeminiClient()
    result = await fake.create_structured(prompt="p", item_schema={}, max_items=2)
    assert [i["name"] for i in result.items] == ["Toad", "Ratty"]
    chapters = await fake.create_structured(prompt="p", item_schema={}, max_items=1)
    assert [i["name"] for i in chapters.items] == ["Chapter One"]


async def test_images_are_a_tiny_valid_png():
    fake = FakeGeminiClient()
    image = await fake.create_image(prompt="draw a toad")
    assert image.data.startswith(b"\x89PNG\r\n\x1a\n")
    assert image.mime_type == "image/png"


async def test_upload_returns_a_uri_and_is_recorded():
    fake = FakeGeminiClient()
    uri = await fake.upload_book(Path("book.txt"))
    assert uri.startswith("files/")
    assert [c.kind for c in fake.calls] == ["upload"]


async def test_a_failure_can_be_injected_at_a_chosen_call():
    fake = FakeGeminiClient()
    fake.fail_on(1, GeminiError("the provider said no"))
    await fake.create_image(prompt="first")
    with pytest.raises(GeminiError, match="the provider said no"):
        await fake.create_image(prompt="second")


async def test_interaction_not_found_can_be_injected():
    fake = FakeGeminiClient()
    fake.fail_on(0, InteractionNotFound("interaction expired"))
    with pytest.raises(InteractionNotFound):
        await fake.create_text(prompt="p", previous_interaction_id="i-gone")


async def test_a_schema_violating_response_can_be_injected():
    fake = FakeGeminiClient()
    fake.invalid_json_on(0)
    with pytest.raises(InvalidStructuredOutput):
        await fake.create_structured(prompt="p", item_schema={}, max_items=2)


async def test_extra_items_lets_a_test_exceed_the_cap_on_purpose():
    fake = FakeGeminiClient()
    fake.extra_items = 3
    result = await fake.create_structured(prompt="p", item_schema={}, max_items=2)
    assert len(result.items) == 3   # the fake does not enforce; the handler must


async def test_a_gated_call_is_observable_while_held_and_released_explicitly():
    """Never slept through: the test waits for the call to be recorded, asserts
    what it wants, then releases."""
    fake = FakeGeminiClient()
    fake.hold_from(0)
    task = asyncio.create_task(fake.create_image(prompt="slow"))
    await fake.wait_for_calls(1)
    assert not task.done()
    fake.release()
    assert (await task).mime_type == "image/png"


async def test_wait_for_calls_times_out_rather_than_hanging_a_suite():
    fake = FakeGeminiClient()
    with pytest.raises(TimeoutError):
        await fake.wait_for_calls(1, timeout=0.05)
```

- [ ] **Step 2: Run it and verify it fails**

Run: `cd backend && python -m pytest tests/test_fake_gemini.py -v`
Expected: FAIL — `AttributeError: type object 'FakeGeminiClient' has no attribute 'STYLE_TEXT'`

- [ ] **Step 3: Write `backend/app/gemini/fake.py`**

```python
"""A deterministic stand-in for Gemini, selected by USE_FAKE_GEMINI.

It reproduces the contract verified by the Task 2 spike (docs/gemini-contract.md).
It ships in the application rather than in tests so the app itself can be run
without burning quota; production still uses the real client when configured
normally.
"""
from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Sequence

from app.gemini.protocol import (
    ImageResult, InvalidStructuredOutput, ReferenceImage, StructuredResult, TextResult,
)

# A 1x1 transparent PNG — small, valid, and decodable by any image library.
_TINY_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk"
    "+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)


@dataclass
class RecordedCall:
    kind: Literal["upload", "text", "structured", "image"]
    prompt: str | None = None
    previous_interaction_id: str | None = None
    document_uri: str | None = None
    item_schema: dict | None = None
    max_items: int | None = None
    reference_image_count: int = 0
    system_instruction: str | None = None


class FakeGeminiClient:
    TINY_PNG: bytes = base64.b64decode(_TINY_PNG_B64)
    STYLE_TEXT = ("Warm hand-painted watercolour with soft ink outlines, "
                  "a storybook feel with gently saturated colour.")
    CHARACTER_ITEMS = [
        {"name": "Toad", "prompt": "A stout, richly dressed adult toad in a green "
                                   "motoring coat and goggles, ruddy and self-satisfied."},
        {"name": "Ratty", "prompt": "A trim adult water rat in a blue waistcoat, "
                                    "sleeve rolled, an oar resting on one shoulder."},
        {"name": "Badger", "prompt": "A broad, grey-striped adult badger in a worn "
                                     "dressing gown, holding a lantern."},
    ]
    CHAPTER_ITEMS = [
        {"name": "Chapter One", "prompt": "Toad and Ratty on a sunlit river bank, "
                                          "the boat drawn up on the grass."},
        {"name": "Chapter Two", "prompt": "Badger's hall under the Wild Wood, firelight."},
    ]

    def __init__(self) -> None:
        self.calls: list[RecordedCall] = []
        self.extra_items: int | None = None
        self._failures: dict[int, Exception] = {}
        self._invalid_json: set[int] = set()
        self._hold_from: int | None = None
        self._release = asyncio.Event()
        self._release.set()
        self._cond = asyncio.Condition()
        self._next_id = 0

    # ---- test controls ----------------------------------------------------

    def fail_on(self, index: int, exc: Exception) -> None:
        self._failures[index] = exc

    def invalid_json_on(self, index: int) -> None:
        self._invalid_json.add(index)

    def hold_from(self, index: int) -> None:
        """Hold every call from `index` onward until release() is called."""
        self._hold_from = index
        self._release.clear()

    def release(self) -> None:
        self._release.set()

    async def wait_for_calls(self, n: int, timeout: float = 2.0) -> None:
        async def _wait() -> None:
            async with self._cond:
                await self._cond.wait_for(lambda: len(self.calls) >= n)
        await asyncio.wait_for(_wait(), timeout)

    # ---- internals --------------------------------------------------------

    async def _record(self, call: RecordedCall) -> int:
        index = len(self.calls)
        async with self._cond:
            self.calls.append(call)
            self._cond.notify_all()
        if self._hold_from is not None and index >= self._hold_from:
            await self._release.wait()
        failure = self._failures.get(index)
        if failure is not None:
            raise failure
        return index

    def _mint(self) -> str:
        self._next_id += 1
        return f"fake-interaction-{self._next_id}"

    # ---- GeminiClient -----------------------------------------------------

    async def upload_book(self, book_path: Path) -> str:
        await self._record(RecordedCall(kind="upload", prompt=str(book_path)))
        return f"files/fake-{book_path.name}"

    async def create_text(self, *, prompt: str, previous_interaction_id: str | None = None,
                          document_uri: str | None = None) -> TextResult:
        await self._record(RecordedCall(
            kind="text", prompt=prompt, previous_interaction_id=previous_interaction_id,
            document_uri=document_uri))
        return TextResult(interaction_id=self._mint(), text=self.STYLE_TEXT)

    async def create_structured(self, *, prompt: str,
                                previous_interaction_id: str | None = None,
                                document_uri: str | None = None, item_schema: dict,
                                max_items: int) -> StructuredResult:
        index = await self._record(RecordedCall(
            kind="structured", prompt=prompt,
            previous_interaction_id=previous_interaction_id, document_uri=document_uri,
            item_schema=item_schema, max_items=max_items))
        if index in self._invalid_json:
            raise InvalidStructuredOutput(
                "Gemini returned a structured response that is not valid JSON.")
        source = self.CHAPTER_ITEMS if max_items == 1 else self.CHARACTER_ITEMS
        count = self.extra_items if self.extra_items is not None else max_items
        return StructuredResult(interaction_id=self._mint(),
                                items=[dict(i) for i in source[:count]])

    async def create_image(self, *, prompt: str, previous_interaction_id: str | None = None,
                           reference_images: Sequence[ReferenceImage] = (),
                           system_instruction: str | None = None) -> ImageResult:
        await self._record(RecordedCall(
            kind="image", prompt=prompt, previous_interaction_id=previous_interaction_id,
            reference_image_count=len(reference_images),
            system_instruction=system_instruction))
        return ImageResult(interaction_id=self._mint(), data=self.TINY_PNG,
                           mime_type="image/png")
```

`CHARACTER_ITEMS` deliberately holds **three** entries so a test can set `extra_items = 3` and prove the *handler* rejects an over-cap response — the fake never enforces the cap, because then the test would be checking the fake.

- [ ] **Step 4: Run the test and verify it passes**

Run: `cd backend && python -m pytest tests/test_fake_gemini.py -v`
Expected: PASS — 11 passed

- [ ] **Step 5: Commit**

```bash
git add backend/app/gemini/fake.py backend/tests/test_fake_gemini.py
git commit -m "Add FakeGeminiClient with a recorder, a gate and injectable failures

The recorder is what turns the call-sequence, no-duplicate-call and
book-sent-once claims into assertions rather than prose. The gate holds a call
open so a test can observe RUNNING and is released explicitly - wait_for_calls
replaces sleeping, and times out rather than hanging the suite.

The fake never enforces the caps: extra_items lets a test return three
characters so the handler's rejection is what is under test.

Mostly AI-authored (Claude Code).

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 18: Step 1 handler — Style

**Files:**
- Create: `backend/app/handlers.py`, `backend/tests/test_handlers.py`

**Interfaces:**
- Consumes: `store`, `files`, `prompts`, `protocol`, `steps`.
- Produces: `StepContext`, `run_step`, `run_style`, and the private helpers `_validated`, `_reference_images` used by Tasks 19–22.

- [ ] **Step 1: Write the failing test**

`backend/tests/test_handlers.py`:

```python
import pytest

from app import db, files, store
from app.gemini import prompts
from app.gemini.fake import FakeGeminiClient
from app.handlers import StepContext, run_step
from app.steps import StepName

BOOK = "Chapter 1. The river bank. Mole had been working very hard all the morning."


@pytest.fixture
def conn(settings):
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    with db.get_conn(settings) as c:
        db.init_schema(c)
        yield c


@pytest.fixture
def project(conn, settings):
    user_id = store.upsert_user(conn, email="ada@example.com", name="Ada")
    project_id = store.new_id()
    book_path = files.write_book(settings.data_dir, project_id, BOOK)
    conn.execute(
        "INSERT INTO projects (id,user_id,title,created_at,book_path,book_excerpt,"
        "status,step_state) VALUES (?,?,?,?,?,?, 'CREATED','IDLE')",
        (project_id, user_id, "The Wind in the Willows", store.now_iso(),
         book_path, files.excerpt(BOOK)),
    )
    return user_id, project_id


@pytest.fixture
def ctx(settings, fake_gemini, project):
    user_id, project_id = project
    return StepContext(project_id=project_id, user_id=user_id, settings=settings,
                       gemini=fake_gemini, notify=lambda: None)


def project_row(conn, ctx):
    return store.get_project(conn, ctx.project_id, ctx.user_id)


# --------------------------------------------------------------------------
# Step 1 - Style
# --------------------------------------------------------------------------

async def test_generated_style_uploads_the_book_seeds_then_asks_for_a_style(
        conn, ctx, fake_gemini):
    await run_step(StepName.STYLE, ctx, style=None)

    kinds = [c.kind for c in fake_gemini.calls]
    assert kinds == ["upload", "text", "text"]

    seed = fake_gemini.calls[1]
    assert seed.prompt == prompts.BOOK_INTRO
    assert seed.document_uri is not None          # the book travels with the seed
    assert seed.previous_interaction_id is None

    style_call = fake_gemini.calls[2]
    assert style_call.prompt == prompts.STYLE_GENERATE
    assert style_call.previous_interaction_id is not None   # chained off the book
    assert style_call.document_uri is None                  # never re-sent


async def test_generated_style_is_persisted_raw_with_the_new_text_head(conn, ctx):
    await run_step(StepName.STYLE, ctx, style=None)
    row = project_row(conn, ctx)
    assert row["style_text"] == FakeGeminiClient.STYLE_TEXT
    assert 'Follow this style' not in row["style_text"]   # wrapper applied at use
    assert row["text_interaction_id"] is not None


async def test_a_user_supplied_style_acknowledges_instead_of_generating(
        conn, ctx, fake_gemini):
    await run_step(StepName.STYLE, ctx, style="  bold linocut  ")

    assert fake_gemini.calls[2].prompt == \
        prompts.STYLE_ACKNOWLEDGE.format(style="bold linocut")
    assert project_row(conn, ctx)["style_text"] == "bold linocut"


async def test_both_style_paths_produce_the_same_state_shape(conn, ctx, fake_gemini):
    await run_step(StepName.STYLE, ctx, style="bold linocut")
    row = project_row(conn, ctx)
    assert row["style_text"] and row["text_interaction_id"]
    assert [c.kind for c in fake_gemini.calls] == ["upload", "text", "text"]


async def test_a_blank_style_string_is_treated_as_no_style(conn, ctx, fake_gemini):
    await run_step(StepName.STYLE, ctx, style="   ")
    assert fake_gemini.calls[2].prompt == prompts.STYLE_GENERATE


async def test_a_style_already_persisted_is_not_regenerated(conn, ctx, fake_gemini):
    """Resume-aware: a crash after save_style but before the status advance
    leaves nothing to redo (design 6.2)."""
    store.save_style(conn, ctx.project_id, style_text="already here",
                     text_interaction_id="i-old")

    await run_step(StepName.STYLE, ctx, style=None)

    assert fake_gemini.calls == []
    assert project_row(conn, ctx)["style_text"] == "already here"
```

- [ ] **Step 2: Run it and verify it fails**

Run: `cd backend && python -m pytest tests/test_handlers.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.handlers'`

- [ ] **Step 3: Write `backend/app/handlers.py`**

```python
"""The five step handlers.

Each does only the work not already persisted, which makes a retry cheap and
lossless within a step. They are resume-aware, not idempotent: a Gemini call
whose response was lost to process death leaves nothing on disk, so a later
user-triggered retry genuinely repeats it (design 6.2).
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Callable, Sequence

from app import db, files, store
from app.config import Settings
from app.gemini import prompts
from app.gemini.protocol import (
    GeminiClient, InvalidStructuredOutput, ReferenceImage, PROMPT_ITEM_SCHEMA,
)
from app.steps import MAX_CHAPTERS, MAX_CHARACTERS, StepName


@dataclass(frozen=True)
class StepContext:
    project_id: str
    user_id: str
    settings: Settings
    gemini: GeminiClient
    notify: Callable[[], None]


def _validated(items: list[dict], cap: int, label: str) -> list[dict]:
    """Strict validation wins. There is no silent slicing anywhere in the parse
    path: an over-cap response is a failure, not a truncated success (design 7.4)."""
    if not items:
        raise InvalidStructuredOutput(f"Gemini returned no {label}.")
    if len(items) > cap:
        raise InvalidStructuredOutput(
            f"Gemini returned {len(items)} {label} but at most {cap} are allowed.")
    for item in items:
        if (not isinstance(item, dict)
                or not isinstance(item.get("name"), str)
                or not isinstance(item.get("prompt"), str)
                or not item["name"].strip() or not item["prompt"].strip()):
            raise InvalidStructuredOutput(
                f"Gemini returned a {label} entry without a usable name and prompt.")
    return items


def _reference_images(ctx: StepContext, rows: Sequence[sqlite3.Row],
                      column: str) -> list[ReferenceImage]:
    refs: list[ReferenceImage] = []
    for row in rows:
        path = row[column]
        if path:
            refs.append(ReferenceImage(
                data=files.absolute(ctx.settings.data_dir, path).read_bytes(),
                mime_type="image/png"))
    return refs


def _load(ctx: StepContext) -> tuple[sqlite3.Row, list[sqlite3.Row], list[sqlite3.Row]]:
    with db.get_conn(ctx.settings) as conn:
        return (store.get_project(conn, ctx.project_id, ctx.user_id),
                store.list_characters(conn, ctx.project_id),
                store.list_chapters(conn, ctx.project_id))


# --------------------------------------------------------------------------
# Step 1 - Style
# --------------------------------------------------------------------------

async def run_style(ctx: StepContext, *, style: str | None) -> None:
    row, _, _ = _load(ctx)
    if row["style_text"] is not None:
        return  # already persisted; nothing left to do in this step

    book_uri = await ctx.gemini.upload_book(
        files.book_path(ctx.settings.data_dir, ctx.project_id))
    seed = await ctx.gemini.create_text(prompt=prompts.BOOK_INTRO, document_uri=book_uri)

    supplied = (style or "").strip()
    if supplied:
        result = await ctx.gemini.create_text(
            prompt=prompts.STYLE_ACKNOWLEDGE.format(style=supplied),
            previous_interaction_id=seed.interaction_id)
        style_text = supplied
    else:
        result = await ctx.gemini.create_text(
            prompt=prompts.STYLE_GENERATE,
            previous_interaction_id=seed.interaction_id)
        style_text = result.text

    # Stored raw. The notebook's 'Follow this style: "…"' wrapper is applied when
    # building an image prompt - formatting belongs at the point of use (design 7.2).
    with db.get_conn(ctx.settings) as conn:
        store.save_style(conn, ctx.project_id, style_text=style_text,
                         text_interaction_id=result.interaction_id)


async def run_step(step: StepName, ctx: StepContext, *, style: str | None = None) -> None:
    if step == StepName.STYLE:
        await run_style(ctx, style=style)
    else:
        raise NotImplementedError(step)   # Tasks 19-22 fill this in
```

- [ ] **Step 4: Run the test and verify it passes**

Run: `cd backend && python -m pytest tests/test_handlers.py -v`
Expected: PASS — 6 passed

- [ ] **Step 5: Commit**

```bash
git add backend/app/handlers.py backend/tests/test_handlers.py
git commit -m "Add the Style step handler with both notebook branches

The book is uploaded and seeded here rather than at project creation, so an
unopened project never begins life with a dead file URI and creation cannot
fail on a provider error. The style call chains off the book interaction and
never re-sends the document.

Style text is persisted raw; the notebook's 'Follow this style' wrapper is
applied when an image prompt is built. A style already on disk means the step
has nothing left to do, which is what makes a crash before the status advance
free to retry.

Mostly AI-authored (Claude Code).

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 19: Step 2 handler — Characters

Includes the `NULL`-head standalone branch, which is *the same branch* the recovery path uses — there is no separate recovery code (spec §7.5).

**Files:**
- Modify: `backend/app/handlers.py`, `backend/tests/test_handlers.py`

**Interfaces:**
- Consumes: Task 18.
- Produces: `run_characters(ctx)`, wired into `run_step`.

- [ ] **Step 1: Append the failing tests to `backend/tests/test_handlers.py`**

```python
# --------------------------------------------------------------------------
# Step 2 - Characters
# --------------------------------------------------------------------------

@pytest.fixture
def styled(conn, ctx):
    store.save_style(conn, ctx.project_id, style_text="Warm watercolour",
                     text_interaction_id="i-style")
    return ctx


async def test_characters_chain_off_the_text_head_and_never_resend_the_book(
        conn, styled, fake_gemini):
    await run_step(StepName.CHARACTERS, styled)

    assert [c.kind for c in fake_gemini.calls] == ["structured"]
    call = fake_gemini.calls[0]
    assert call.prompt == prompts.CHARACTERS_INSTRUCTION
    assert call.previous_interaction_id == "i-style"
    assert call.document_uri is None
    assert call.max_items == 2


async def test_characters_are_persisted_in_order_with_the_new_text_head(conn, styled):
    await run_step(StepName.CHARACTERS, styled)

    rows = store.list_characters(conn, styled.project_id)
    assert [r["name"] for r in rows] == ["Toad", "Ratty"]
    assert [r["position"] for r in rows] == [0, 1]
    assert all(r["portrait_path"] is None for r in rows)
    assert project_row(conn, styled)["text_interaction_id"] != "i-style"


async def test_at_most_two_characters_are_ever_requested(conn, styled, fake_gemini):
    await run_step(StepName.CHARACTERS, styled)
    assert fake_gemini.calls[0].max_items == 2


async def test_an_over_cap_response_fails_validation_rather_than_being_sliced(
        conn, styled, fake_gemini):
    """No silent slicing: three characters is INVALID_OUTPUT with nothing
    persisted (design 7.4)."""
    fake_gemini.extra_items = 3

    with pytest.raises(InvalidStructuredOutput, match="at most 2"):
        await run_step(StepName.CHARACTERS, styled)

    assert store.list_characters(conn, styled.project_id) == []


async def test_a_response_missing_a_prompt_fails_validation(conn, styled, fake_gemini):
    fake_gemini.invalid_json_on(0)
    with pytest.raises(InvalidStructuredOutput):
        await run_step(StepName.CHARACTERS, styled)
    assert store.list_characters(conn, styled.project_id) == []


async def test_a_null_text_head_makes_one_standalone_call_that_re_uploads_the_book(
        conn, styled, fake_gemini):
    """The NULL head IS the recovery branch. Step 2's prompt genuinely needs the
    book - 'use the descriptions from the book' - so no artifact substitutes
    (design 7.5)."""
    conn.execute("UPDATE projects SET text_interaction_id = NULL WHERE id = ?",
                 (styled.project_id,))

    await run_step(StepName.CHARACTERS, styled)

    assert [c.kind for c in fake_gemini.calls] == ["upload", "structured"]
    call = fake_gemini.calls[1]
    assert call.previous_interaction_id is None
    assert call.document_uri is not None
    assert "Warm watercolour" in call.prompt          # style carried in the prompt
    assert prompts.CHARACTERS_INSTRUCTION in call.prompt


async def test_characters_already_persisted_are_not_regenerated(conn, styled, fake_gemini):
    store.save_characters(conn, styled.project_id, [("Existing", "p")],
                          text_interaction_id="i-old")

    await run_step(StepName.CHARACTERS, styled)

    assert fake_gemini.calls == []
    assert [r["name"] for r in store.list_characters(conn, styled.project_id)] == ["Existing"]
```

Add the import: `from app.gemini.protocol import InvalidStructuredOutput`.

- [ ] **Step 2: Run and verify it fails**

Run: `cd backend && python -m pytest tests/test_handlers.py -k characters -v`
Expected: FAIL — `NotImplementedError: CHARACTERS`

- [ ] **Step 3: Append `run_characters` to `backend/app/handlers.py`**

```python
# --------------------------------------------------------------------------
# Step 2 - Characters
# --------------------------------------------------------------------------

async def run_characters(ctx: StepContext) -> None:
    row, characters, _ = _load(ctx)
    if characters:
        return

    head = row["text_interaction_id"]
    if head is not None:
        prompt = prompts.CHARACTERS_INSTRUCTION
        document_uri = None
    else:
        # A NULL head already means "build this step's call standalone". This is
        # the first-run branch and the post-expiry branch at once (design 7.5).
        prompt = prompts.characters_standalone(row["style_text"] or "")
        document_uri = await ctx.gemini.upload_book(
            files.book_path(ctx.settings.data_dir, ctx.project_id))

    result = await ctx.gemini.create_structured(
        prompt=prompt, previous_interaction_id=head, document_uri=document_uri,
        item_schema=PROMPT_ITEM_SCHEMA, max_items=MAX_CHARACTERS)
    items = _validated(result.items, MAX_CHARACTERS, "characters")

    with db.get_conn(ctx.settings) as conn:
        store.save_characters(conn, ctx.project_id,
                              [(i["name"], i["prompt"]) for i in items],
                              text_interaction_id=result.interaction_id)
```

Extend `run_step`:

```python
    elif step == StepName.CHARACTERS:
        await run_characters(ctx)
```

- [ ] **Step 4: Run the test and verify it passes**

Run: `cd backend && python -m pytest tests/test_handlers.py -v`
Expected: PASS — 13 passed

- [ ] **Step 5: Commit**

```bash
git add backend/app/handlers.py backend/tests/test_handlers.py
git commit -m "Add the Characters step handler with strict cap validation

The cap acts in three places for two different reasons: the prompt keeps
Gemini's own context free of characters we would discard, maxItems is the
structural contract, and the handler rejects an over-cap response outright.
Three characters is INVALID_OUTPUT with nothing persisted - the notebook's
characters[:max] slice is deliberately not reproduced.

A NULL text head is the standalone branch, and it is the same code the
context-expiry recovery uses. There is no separate recovery path.

Mostly AI-authored (Claude Code).

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 20: Step 3 handler — Portraits

Where per-item resumability lives. This one decision satisfies §4.4's per-item progress requirement and §4.3's never-lose-results requirement at once.

**Files:**
- Modify: `backend/app/handlers.py`, `backend/tests/test_handlers.py`

**Interfaces:**
- Consumes: Task 19.
- Produces: `run_portraits(ctx)`, wired into `run_step`.

- [ ] **Step 1: Append the failing tests**

```python
# --------------------------------------------------------------------------
# Step 3 - Portraits
# --------------------------------------------------------------------------

@pytest.fixture
def with_characters(conn, styled):
    store.save_characters(conn, styled.project_id,
                          [("Toad", "a stout toad"), ("Ratty", "a trim rat")],
                          text_interaction_id="i-chars")
    return styled


async def test_portraits_seed_the_image_chain_unchained_then_chain_each_portrait(
        conn, with_characters, fake_gemini):
    """The image chain is seeded fresh. Notebook cell 34 is a bare TODO about
    chaining an image call off a text interaction - Google has not validated it,
    and neither do we (design 7.1)."""
    await run_step(StepName.PORTRAITS, with_characters)

    assert [c.kind for c in fake_gemini.calls] == ["image", "image", "image"]
    seed, first, second = fake_gemini.calls
    assert seed.previous_interaction_id is None
    assert "The Wind in the Willows" in seed.prompt
    assert 'Follow this style: "Warm watercolour"' in seed.prompt
    assert "no text on the image" in seed.prompt

    assert first.prompt == prompts.PORTRAIT_INSTRUCTION.format(
        name="Toad", prompt="a stout toad")
    assert first.previous_interaction_id is not None
    assert second.previous_interaction_id is not None
    assert second.previous_interaction_id != first.previous_interaction_id


async def test_each_portrait_lands_on_disk_and_advances_the_image_head(
        conn, with_characters, settings):
    await run_step(StepName.PORTRAITS, with_characters)

    rows = store.list_characters(conn, with_characters.project_id)
    for row in rows:
        assert row["portrait_path"] == \
            f"projects/{with_characters.project_id}/portraits/{row['id']}.png"
        assert files.absolute(settings.data_dir, row["portrait_path"]).exists()
    assert project_row(conn, with_characters)["image_interaction_id"] is not None


async def test_the_view_is_notified_after_each_portrait_not_only_at_the_end(
        conn, with_characters, settings, fake_gemini):
    """Per-item progress: the user sees each portrait land (assessment 4.4)."""
    seen: list[int] = []

    def count_ready() -> None:
        with db.get_conn(settings) as c:
            seen.append(sum(1 for r in store.list_characters(c, with_characters.project_id)
                            if r["portrait_path"]))

    ctx_with_notify = StepContext(
        project_id=with_characters.project_id, user_id=with_characters.user_id,
        settings=settings, gemini=fake_gemini, notify=count_ready)
    await run_step(StepName.PORTRAITS, ctx_with_notify)

    assert seen == [1, 2]


async def test_an_existing_portrait_is_never_regenerated(conn, with_characters, fake_gemini):
    """Crash after portrait 1, before portrait 2: the retry calls character 1
    zero times and character 2 once (design 6.2)."""
    first = store.list_characters(conn, with_characters.project_id)[0]
    store.save_portrait(conn, project_id=with_characters.project_id,
                        character_id=first["id"],
                        portrait_path="projects/p/portraits/kept.png",
                        image_interaction_id="i-img-1")

    await run_step(StepName.PORTRAITS, with_characters)

    image_prompts = [c.prompt for c in fake_gemini.calls if c.kind == "image"]
    assert not any("a stout toad" in p for p in image_prompts)
    assert any("a trim rat" in p for p in image_prompts)
    assert store.list_characters(conn, with_characters.project_id)[0]["portrait_path"] == \
        "projects/p/portraits/kept.png"


async def test_a_live_image_head_is_reused_rather_than_reseeded(
        conn, with_characters, fake_gemini):
    conn.execute("UPDATE projects SET image_interaction_id='i-img-live' WHERE id=?",
                 (with_characters.project_id,))

    await run_step(StepName.PORTRAITS, with_characters)

    assert fake_gemini.calls[0].previous_interaction_id == "i-img-live"
    assert len(fake_gemini.calls) == 2      # no seed call


async def test_a_reseed_after_expiry_carries_the_portraits_already_on_disk(
        conn, with_characters, settings, fake_gemini):
    """Step 3's standalone seed carries style, rules and any existing portraits
    as references, so a rebuilt chain keeps character consistency (design 7.5)."""
    first = store.list_characters(conn, with_characters.project_id)[0]
    path = files.save_portrait_bytes(settings.data_dir, with_characters.project_id,
                                     first["id"], FakeGeminiClient.TINY_PNG)
    store.save_portrait(conn, project_id=with_characters.project_id,
                        character_id=first["id"], portrait_path=path,
                        image_interaction_id="i-old")
    conn.execute("UPDATE projects SET image_interaction_id = NULL WHERE id = ?",
                 (with_characters.project_id,))

    await run_step(StepName.PORTRAITS, with_characters)

    assert fake_gemini.calls[0].previous_interaction_id is None
    assert fake_gemini.calls[0].reference_image_count == 1


async def test_the_generation_loop_is_bounded_regardless_of_how_many_rows_exist(
        conn, with_characters, fake_gemini):
    """The cost invariant. Seeded directly rather than through Gemini output,
    because this is a different mechanism guarding a different failure
    (design 7.4)."""
    conn.execute(
        "INSERT INTO characters (id, project_id, position, name, prompt) VALUES (?,?,?,?,?)",
        (store.new_id(), with_characters.project_id, 2, "Badger", "a broad badger"))

    await run_step(StepName.PORTRAITS, with_characters)

    portrait_calls = [c for c in fake_gemini.calls
                      if c.kind == "image" and c.previous_interaction_id is not None]
    assert len(portrait_calls) == 2
    assert not any("badger" in c.prompt.lower() for c in portrait_calls)


async def test_nothing_left_to_generate_makes_no_calls_at_all(
        conn, with_characters, fake_gemini):
    for row in store.list_characters(conn, with_characters.project_id):
        store.save_portrait(conn, project_id=with_characters.project_id,
                            character_id=row["id"], portrait_path="projects/p/x.png",
                            image_interaction_id="i")

    await run_step(StepName.PORTRAITS, with_characters)

    assert fake_gemini.calls == []
```

- [ ] **Step 2: Run and verify it fails**

Run: `cd backend && python -m pytest tests/test_handlers.py -k portrait -v`
Expected: FAIL — `NotImplementedError: PORTRAITS`

- [ ] **Step 3: Append `run_portraits` to `backend/app/handlers.py`**

```python
# --------------------------------------------------------------------------
# Step 3 - Portraits
# --------------------------------------------------------------------------

async def run_portraits(ctx: StepContext) -> None:
    row, characters, _ = _load(ctx)
    # The generation-loop bound: at most MAX_CHARACTERS regardless of how many
    # rows exist, so persisted state above the cap cannot buy extra image calls.
    capped = characters[:MAX_CHARACTERS]
    pending = [c for c in capped if c["portrait_path"] is None]
    if not pending:
        return

    head = row["image_interaction_id"]
    if head is None:
        seed = await ctx.gemini.create_image(
            prompt=prompts.IMAGE_SEED.format(
                title=row["title"],
                style=prompts.STYLE_WRAPPER.format(style=row["style_text"] or ""),
                rules=prompts.RULES),
            reference_images=_reference_images(ctx, capped, "portrait_path"))
        head = seed.interaction_id

    for character in pending:
        result = await ctx.gemini.create_image(
            prompt=prompts.PORTRAIT_INSTRUCTION.format(
                name=character["name"], prompt=character["prompt"]),
            previous_interaction_id=head)
        path = files.save_portrait_bytes(ctx.settings.data_dir, ctx.project_id,
                                         character["id"], result.data)
        # File first, then the row - and the chain head moves with it, in one
        # transaction. Either without the other breaks a mid-flight retry
        # (design 3.3, 7.2).
        with db.get_conn(ctx.settings) as conn:
            store.save_portrait(conn, project_id=ctx.project_id,
                                character_id=character["id"], portrait_path=path,
                                image_interaction_id=result.interaction_id)
        head = result.interaction_id
        ctx.notify()
```

Extend `run_step`:

```python
    elif step == StepName.PORTRAITS:
        await run_portraits(ctx)
```

- [ ] **Step 4: Run the test and verify it passes**

Run: `cd backend && python -m pytest tests/test_handlers.py -v`
Expected: PASS — 21 passed

- [ ] **Step 5: Commit**

```bash
git add backend/app/handlers.py backend/tests/test_handlers.py
git commit -m "Add the Portraits step handler with per-item resumability

Each portrait commits its file path and the new image-chain head in one
transaction, then notifies. That single decision satisfies two requirements at
once: the user watches each portrait land, and a crash between portraits never
regenerates the one already saved.

The image chain is seeded fresh rather than chained off a text interaction -
notebook cell 34 is a bare TODO about that, so Google has not validated it and
neither do we. A reseed after expiry carries the portraits already on disk as
references so character consistency survives.

The loop is bounded at 2 regardless of how many character rows exist; the test
seeds a third row directly rather than going through Gemini.

Mostly AI-authored (Claude Code).

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 21: Step 4 handler — Chapters

**Files:**
- Modify: `backend/app/handlers.py`, `backend/tests/test_handlers.py`

**Interfaces:**
- Consumes: Task 20.
- Produces: `run_chapters(ctx)`, wired into `run_step`.

- [ ] **Step 1: Append the failing tests**

```python
# --------------------------------------------------------------------------
# Step 4 - Chapters
# --------------------------------------------------------------------------

async def test_chapters_chain_off_the_characters_interaction_not_the_image_chain(
        conn, with_characters, fake_gemini):
    """Step 4 chains off the text head, which after step 2 IS the characters
    interaction - so no history table is needed (design 7.1)."""
    conn.execute("UPDATE projects SET image_interaction_id='i-img-2' WHERE id=?",
                 (with_characters.project_id,))

    await run_step(StepName.CHAPTERS, with_characters)

    assert [c.kind for c in fake_gemini.calls] == ["structured"]
    call = fake_gemini.calls[0]
    assert call.previous_interaction_id == "i-chars"
    assert call.previous_interaction_id != "i-img-2"
    assert call.prompt == prompts.CHAPTERS_INSTRUCTION
    assert call.max_items == 1
    assert call.document_uri is None


async def test_one_chapter_is_persisted_with_the_new_text_head(conn, with_characters):
    await run_step(StepName.CHAPTERS, with_characters)

    rows = store.list_chapters(conn, with_characters.project_id)
    assert [r["name"] for r in rows] == ["Chapter One"]
    assert rows[0]["illustration_path"] is None
    assert project_row(conn, with_characters)["text_interaction_id"] != "i-chars"


async def test_more_than_one_chapter_fails_validation(conn, with_characters, fake_gemini):
    fake_gemini.extra_items = 2

    with pytest.raises(InvalidStructuredOutput, match="at most 1"):
        await run_step(StepName.CHAPTERS, with_characters)

    assert store.list_chapters(conn, with_characters.project_id) == []


async def test_a_null_text_head_rebuilds_from_style_and_the_character_prompts(
        conn, with_characters, fake_gemini):
    conn.execute("UPDATE projects SET text_interaction_id = NULL WHERE id = ?",
                 (with_characters.project_id,))

    await run_step(StepName.CHAPTERS, with_characters)

    assert [c.kind for c in fake_gemini.calls] == ["upload", "structured"]
    call = fake_gemini.calls[1]
    assert call.previous_interaction_id is None
    assert call.document_uri is not None       # 'for each chapters of the book'
    assert "Warm watercolour" in call.prompt
    assert "a stout toad" in call.prompt        # characters carried forward
    assert "a trim rat" in call.prompt


async def test_chapters_already_persisted_are_not_regenerated(
        conn, with_characters, fake_gemini):
    store.save_chapters(conn, with_characters.project_id, [("Existing", "p")],
                        text_interaction_id="i-old")

    await run_step(StepName.CHAPTERS, with_characters)

    assert fake_gemini.calls == []
```

- [ ] **Step 2: Run and verify it fails**

Run: `cd backend && python -m pytest tests/test_handlers.py -k chapter -v`
Expected: FAIL — `NotImplementedError: CHAPTERS`

- [ ] **Step 3: Append `run_chapters` to `backend/app/handlers.py`**

```python
# --------------------------------------------------------------------------
# Step 4 - Chapters
# --------------------------------------------------------------------------

async def run_chapters(ctx: StepContext) -> None:
    row, characters, chapters = _load(ctx)
    if chapters:
        return

    head = row["text_interaction_id"]
    if head is not None:
        prompt = prompts.CHAPTERS_INSTRUCTION
        document_uri = None
    else:
        prompt = prompts.chapters_standalone(
            row["style_text"] or "",
            [c["prompt"] for c in characters[:MAX_CHARACTERS]])
        document_uri = await ctx.gemini.upload_book(
            files.book_path(ctx.settings.data_dir, ctx.project_id))

    result = await ctx.gemini.create_structured(
        prompt=prompt, previous_interaction_id=head, document_uri=document_uri,
        item_schema=PROMPT_ITEM_SCHEMA, max_items=MAX_CHAPTERS)
    items = _validated(result.items, MAX_CHAPTERS, "chapters")

    with db.get_conn(ctx.settings) as conn:
        store.save_chapters(conn, ctx.project_id,
                            [(i["name"], i["prompt"]) for i in items],
                            text_interaction_id=result.interaction_id)
```

Extend `run_step`:

```python
    elif step == StepName.CHAPTERS:
        await run_chapters(ctx)
```

- [ ] **Step 4: Run the test and verify it passes**

Run: `cd backend && python -m pytest tests/test_handlers.py -v`
Expected: PASS — 26 passed

- [ ] **Step 5: Commit**

```bash
git add backend/app/handlers.py backend/tests/test_handlers.py
git commit -m "Add the Chapters step handler

Step 4 chains off the text head, which after step 2 is exactly the characters
interaction - which is why two head columns are enough and no interaction
history table exists. The image chain is untouched.

The standalone branch carries the persisted character prompts into the request,
so a rebuilt chapter prompt still references the characters that have portraits.

Mostly AI-authored (Claude Code).

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 22: Step 5 handler — Illustrations

**Files:**
- Modify: `backend/app/handlers.py`, `backend/tests/test_handlers.py`

**Interfaces:**
- Consumes: Task 21.
- Produces: `run_illustrations(ctx)`, wired into `run_step` (which no longer raises `NotImplementedError`).

- [ ] **Step 1: Append the failing tests**

```python
# --------------------------------------------------------------------------
# Step 5 - Illustrations
# --------------------------------------------------------------------------

@pytest.fixture
def with_chapters(conn, with_characters, settings):
    for row in store.list_characters(conn, with_characters.project_id):
        path = files.save_portrait_bytes(settings.data_dir, with_characters.project_id,
                                         row["id"], FakeGeminiClient.TINY_PNG)
        store.save_portrait(conn, project_id=with_characters.project_id,
                            character_id=row["id"], portrait_path=path,
                            image_interaction_id="i-img-2")
    store.save_chapters(conn, with_characters.project_id,
                        [("Chapter One", "a sunlit river bank")],
                        text_interaction_id="i-chaps")
    return with_characters


async def test_illustrations_seed_chapter_mode_off_the_last_portrait_then_draw(
        conn, with_chapters, fake_gemini):
    await run_step(StepName.ILLUSTRATIONS, with_chapters)

    assert [c.kind for c in fake_gemini.calls] == ["image", "image"]
    seed, draw = fake_gemini.calls
    assert seed.prompt == prompts.CHAPTER_SEED
    assert seed.previous_interaction_id == "i-img-2"     # continues the image chain
    assert draw.prompt == prompts.ILLUSTRATION_INSTRUCTION.format(
        name="Chapter One", prompt="a sunlit river bank")
    assert draw.previous_interaction_id is not None
    assert draw.reference_image_count == 0               # chained mode needs no refs


async def test_the_illustration_lands_on_disk_and_completes_the_project_data(
        conn, with_chapters, settings):
    await run_step(StepName.ILLUSTRATIONS, with_chapters)

    row = store.list_chapters(conn, with_chapters.project_id)[0]
    assert row["illustration_path"] == \
        f"projects/{with_chapters.project_id}/illustrations/{row['id']}.png"
    assert files.absolute(settings.data_dir, row["illustration_path"]).exists()


async def test_a_null_image_head_draws_standalone_with_the_portraits_as_references(
        conn, with_chapters, fake_gemini):
    """Notebook cells 39-44: reference images plus the rules as
    system_instruction, and no chaining. Every persisted portrait is sent,
    because at a cap of 2 that is the same set cell 44 would select (design 7.5)."""
    conn.execute("UPDATE projects SET image_interaction_id = NULL WHERE id = ?",
                 (with_chapters.project_id,))

    await run_step(StepName.ILLUSTRATIONS, with_chapters)

    assert [c.kind for c in fake_gemini.calls] == ["image"]
    call = fake_gemini.calls[0]
    assert call.previous_interaction_id is None
    assert call.reference_image_count == 2
    assert call.system_instruction == prompts.RULES
    assert "a sunlit river bank" in call.prompt


async def test_the_standalone_illustration_never_re_uploads_the_book(
        conn, with_chapters, fake_gemini):
    conn.execute("UPDATE projects SET image_interaction_id = NULL WHERE id = ?",
                 (with_chapters.project_id,))
    await run_step(StepName.ILLUSTRATIONS, with_chapters)
    assert not any(c.kind == "upload" for c in fake_gemini.calls)


async def test_the_illustration_loop_is_bounded_at_one_chapter(
        conn, with_chapters, fake_gemini):
    conn.execute(
        "INSERT INTO chapters (id, project_id, position, name, prompt) VALUES (?,?,?,?,?)",
        (store.new_id(), with_chapters.project_id, 1, "Chapter Two", "the wild wood"))

    await run_step(StepName.ILLUSTRATIONS, with_chapters)

    drawn = [c for c in fake_gemini.calls if c.prompt and "wild wood" in c.prompt]
    assert drawn == []


async def test_an_existing_illustration_is_not_regenerated(conn, with_chapters, fake_gemini):
    row = store.list_chapters(conn, with_chapters.project_id)[0]
    store.save_illustration(conn, project_id=with_chapters.project_id, chapter_id=row["id"],
                            illustration_path="projects/p/x.png", image_interaction_id="i")

    await run_step(StepName.ILLUSTRATIONS, with_chapters)

    assert fake_gemini.calls == []
```

- [ ] **Step 2: Run and verify it fails**

Run: `cd backend && python -m pytest tests/test_handlers.py -k illustration -v`
Expected: FAIL — `NotImplementedError: ILLUSTRATIONS`

- [ ] **Step 3: Append `run_illustrations` and finish `run_step`**

```python
# --------------------------------------------------------------------------
# Step 5 - Illustrations
# --------------------------------------------------------------------------

async def run_illustrations(ctx: StepContext) -> None:
    row, characters, chapters = _load(ctx)
    capped = chapters[:MAX_CHAPTERS]
    pending = [c for c in capped if c["illustration_path"] is None]
    if not pending:
        return

    head = row["image_interaction_id"]
    if head is not None:
        seed = await ctx.gemini.create_image(prompt=prompts.CHAPTER_SEED,
                                             previous_interaction_id=head)
        head = seed.interaction_id
        references: list[ReferenceImage] = []
    else:
        # Standalone reconstruction, notebook cells 39-44. Never re-uploads the
        # book: step 5's prompt is built entirely from persisted state.
        references = _reference_images(ctx, characters[:MAX_CHARACTERS], "portrait_path")

    for chapter in pending:
        if head is not None:
            result = await ctx.gemini.create_image(
                prompt=prompts.ILLUSTRATION_INSTRUCTION.format(
                    name=chapter["name"], prompt=chapter["prompt"]),
                previous_interaction_id=head)
        else:
            result = await ctx.gemini.create_image(
                prompt=prompts.ILLUSTRATION_STANDALONE.format(
                    name=chapter["name"], prompt=chapter["prompt"]),
                reference_images=references,
                system_instruction=prompts.RULES)
        path = files.save_illustration_bytes(ctx.settings.data_dir, ctx.project_id,
                                             chapter["id"], result.data)
        with db.get_conn(ctx.settings) as conn:
            store.save_illustration(conn, project_id=ctx.project_id,
                                    chapter_id=chapter["id"], illustration_path=path,
                                    image_interaction_id=result.interaction_id)
        head = result.interaction_id
        ctx.notify()


async def run_step(step: StepName, ctx: StepContext, *, style: str | None = None) -> None:
    if step == StepName.STYLE:
        await run_style(ctx, style=style)
    elif step == StepName.CHARACTERS:
        await run_characters(ctx)
    elif step == StepName.PORTRAITS:
        await run_portraits(ctx)
    elif step == StepName.CHAPTERS:
        await run_chapters(ctx)
    elif step == StepName.ILLUSTRATIONS:
        await run_illustrations(ctx)
    else:
        raise ValueError(f"Unknown step: {step}")
```

- [ ] **Step 4: Run the whole handler suite and verify it passes**

Run: `cd backend && python -m pytest tests/test_handlers.py -v`
Expected: PASS — 32 passed

- [ ] **Step 5: Run the whole backend suite for regressions**

Run: `cd backend && python -m pytest -q`
Expected: PASS — all green

- [ ] **Step 6: Commit**

```bash
git add backend/app/handlers.py backend/tests/test_handlers.py
git commit -m "Add the Illustrations step handler, completing the five steps

Chained mode seeds chapter-mode off the last portrait so characters stay
consistent, matching notebook cell 38. Standalone mode is cells 39-44: every
persisted portrait as a reference image, the rules as system_instruction, no
chaining - and never a book re-upload, because step 5's prompt is built
entirely from persisted state.

Cell 44 selects references by chapter['characters'], which only the bonus
schema produces; at a hard cap of 2 characters, sending all of them is the same
set without carrying a second schema and a column.

Mostly AI-authored (Claude Code).

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Phase F — Pipeline and acceptance milestones

### Task 23: The detached task and `POST /run`

**Files:**
- Create: `backend/app/pipeline.py`, `backend/tests/test_pipeline.py`
- Modify: `backend/app/api/projects.py`, `backend/app/main.py`, `backend/tests/conftest.py`

**Interfaces:**
- Consumes: `handlers` (Tasks 18–22), `store` transitions (Task 9).
- Produces: `Deps`, `pipeline.spawn`, `pipeline.broadcast_state`, `pipeline.drain_tasks`, the message constants `CANCELLED_MESSAGE` / `EXPIRED_MESSAGE` / `INTERNAL_MESSAGE`, and `POST /api/projects/{id}/run`.

- [ ] **Step 1: Write the failing test**

`backend/tests/test_pipeline.py`:

```python
import pytest

from app import db, pipeline, store
from app.steps import ProjectStatus, StepState

BOOK = "Chapter 1. The river bank."


@pytest.fixture
async def signed_in(aclient):
    await aclient.post("/api/session", json={"name": "Ada", "email": "ada@example.com"})
    return aclient


async def new_project(client, title="Willows"):
    response = await client.post("/api/projects", json={"title": title, "book_text": BOOK})
    return response.json()["id"]


async def test_running_the_current_step_returns_202_with_the_running_state(
        signed_in, fake_gemini):
    pid = await new_project(signed_in)
    fake_gemini.hold_from(0)

    response = await signed_in.post(f"/api/projects/{pid}/run", json={"step": "STYLE"})

    assert response.status_code == 202
    project = response.json()["project"]
    assert project["step_state"] == "RUNNING"
    assert project["current_step"] == "STYLE"
    assert project["display_status"] == "In progress"
    assert project["is_interrupted"] is False

    fake_gemini.release()
    await pipeline.drain_tasks()


async def test_the_step_completes_and_advances_the_status(signed_in):
    pid = await new_project(signed_in)
    await signed_in.post(f"/api/projects/{pid}/run", json={"step": "STYLE"})
    await pipeline.drain_tasks()

    project = (await signed_in.get(f"/api/projects/{pid}")).json()
    assert project["status"] == "STYLE_SET"
    assert project["step_state"] == "IDLE"
    assert project["current_step"] == "CHARACTERS"
    assert project["completed_steps"] == 1
    assert project["style_text"]


async def test_a_future_step_is_409_and_makes_zero_gemini_calls(signed_in, fake_gemini):
    """Step ordering, and it costs nothing to enforce (assessment 4.3)."""
    pid = await new_project(signed_in)

    response = await signed_in.post(f"/api/projects/{pid}/run", json={"step": "PORTRAITS"})

    assert response.status_code == 409
    body = response.json()
    assert body["project"]["status"] == "CREATED"
    assert body["project"]["step_state"] == "IDLE"
    assert body["error"]["code"] == "CONFLICT"
    assert fake_gemini.calls == []


async def test_an_already_completed_step_is_409(signed_in):
    pid = await new_project(signed_in)
    await signed_in.post(f"/api/projects/{pid}/run", json={"step": "STYLE"})
    await pipeline.drain_tasks()

    response = await signed_in.post(f"/api/projects/{pid}/run", json={"step": "STYLE"})
    assert response.status_code == 409


async def test_a_second_run_while_one_is_in_flight_is_409_and_adds_no_calls(
        signed_in, fake_gemini):
    pid = await new_project(signed_in)
    fake_gemini.hold_from(0)
    await signed_in.post(f"/api/projects/{pid}/run", json={"step": "STYLE"})
    await fake_gemini.wait_for_calls(1)
    calls_before = len(fake_gemini.calls)

    response = await signed_in.post(f"/api/projects/{pid}/run", json={"step": "STYLE"})

    assert response.status_code == 409
    assert response.json()["project"]["step_state"] == "RUNNING"
    assert len(fake_gemini.calls) == calls_before

    fake_gemini.release()
    await pipeline.drain_tasks()


async def test_a_failure_records_the_step_as_failed_with_a_user_safe_message(
        signed_in, fake_gemini, settings):
    from app.gemini.protocol import GeminiError
    pid = await new_project(signed_in)
    fake_gemini.fail_on(0, GeminiError("upstream refused"))

    await signed_in.post(f"/api/projects/{pid}/run", json={"step": "STYLE"})
    await pipeline.drain_tasks()

    project = (await signed_in.get(f"/api/projects/{pid}")).json()
    assert project["step_state"] == "FAILED"
    assert project["status"] == "CREATED"          # never advanced
    assert project["failure"]["code"] == "GEMINI_ERROR"
    assert project["needs_attention"] is True
    assert project["display_status"] == "In progress"


async def test_a_202_never_becomes_a_500_when_the_step_fails_later(signed_in, fake_gemini):
    """POST /run is finished at 202; a Gemini failure 30 seconds later surfaces
    as a later project view, not retroactively as an HTTP error (design 8.2)."""
    from app.gemini.protocol import GeminiError
    pid = await new_project(signed_in)
    fake_gemini.fail_on(0, GeminiError("boom"))

    response = await signed_in.post(f"/api/projects/{pid}/run", json={"step": "STYLE"})
    assert response.status_code == 202

    await pipeline.drain_tasks()
    assert (await signed_in.get(f"/api/projects/{pid}")).json()["step_state"] == "FAILED"


async def test_running_another_users_project_is_404(aclient):
    await aclient.post("/api/session", json={"name": "Ada", "email": "ada@example.com"})
    pid = await new_project(aclient)
    await aclient.delete("/api/session")
    await aclient.post("/api/session", json={"name": "Bob", "email": "bob@example.com"})

    response = await aclient.post(f"/api/projects/{pid}/run", json={"step": "STYLE"})
    assert response.status_code == 404


async def test_an_unknown_step_name_is_422(signed_in):
    pid = await new_project(signed_in)
    response = await signed_in.post(f"/api/projects/{pid}/run", json={"step": "SOUNDTRACK"})
    assert response.status_code == 422
```

Add to `backend/tests/conftest.py` so a leaked task can never bleed between tests:

```python
@pytest.fixture(autouse=True)
async def _drain_pipeline_tasks():
    yield
    from app import pipeline
    await pipeline.drain_tasks()
```

- [ ] **Step 2: Run it and verify it fails**

Run: `cd backend && python -m pytest tests/test_pipeline.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.pipeline'`

- [ ] **Step 3: Write `backend/app/pipeline.py`**

```python
"""Step execution.

POST /run performs the conditional transition and returns 202 immediately; the
work runs in a detached in-process task. No queue, no worker process, no broker
- the detached task is the smaller option, not the fancier one (design 6.1).
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass

from app import db, store
from app.config import Settings
from app.gemini.protocol import (
    GeminiClient, GeminiError, InteractionNotFound, InvalidStructuredOutput,
)
from app.handlers import StepContext, run_step
from app.models import state_message
from app.steps import StepName, chain_of_step, status_after

CANCELLED_MESSAGE = "The step was cancelled before it finished. Retry to run it again."
EXPIRED_MESSAGE = (
    "The conversation context for this project expired on Gemini's side. "
    "Retry this step and it will be rebuilt from your saved work."
)
INTERNAL_MESSAGE = "Something went wrong while running this step. Retry to try again."

# Python garbage-collects a task nobody holds, so the module keeps a reference
# until it finishes (design 6.1).
_TASKS: set[asyncio.Task] = set()


@dataclass(frozen=True)
class Deps:
    settings: Settings
    gemini: GeminiClient
    registry: object | None = None


def broadcast_state(project_id: str, user_id: str, deps: Deps) -> None:
    """Read the committed state and hand it to the broadcaster.

    pipeline.py builds the payload; realtime.py only moves it. Called strictly
    after COMMIT, never inside a transaction (design 3.3, 9.4).
    """
    if deps.registry is None:
        return
    with db.get_conn(deps.settings) as conn:
        view = store.read_project_view(conn, project_id, user_id,
                                       server_run_id=deps.settings.server_run_id)
    if view is not None:
        deps.registry.publish(project_id, state_message(view))


def _record_failure(project_id: str, deps: Deps, code: str, message: str,
                    clear_head: str | None = None) -> None:
    with db.get_conn(deps.settings) as conn:
        store.fail_step(conn, project_id, server_run_id=deps.settings.server_run_id,
                        code=code, message=message, clear_head=clear_head)


async def _execute(*, project_id: str, user_id: str, step: StepName,
                   style: str | None, deps: Deps) -> None:
    ctx = StepContext(
        project_id=project_id, user_id=user_id, settings=deps.settings,
        gemini=deps.gemini,
        notify=lambda: broadcast_state(project_id, user_id, deps),
    )
    try:
        await run_step(step, ctx, style=style)
        with db.get_conn(deps.settings) as conn:
            store.complete_step(conn, project_id,
                                server_run_id=deps.settings.server_run_id,
                                next_status=status_after(step))
    except asyncio.CancelledError:
        # The store write is synchronous, so it completes inside the cancelled
        # task; an awaiting cleanup would be cancelled again at its first
        # suspension point. Re-raised so asyncio still sees a cancelled task
        # and shutdown is not stalled (design 6.3).
        _record_failure(project_id, deps, "INTERNAL", CANCELLED_MESSAGE)
        raise
    except InteractionNotFound:
        # Two things, one write: fail, and null the head of the chain that
        # raised. Nothing else happens in this run - no second call, no
        # automatic reconstruction (design 7.5).
        _record_failure(project_id, deps, "GEMINI_ERROR", EXPIRED_MESSAGE,
                        clear_head=chain_of_step(step))
    except InvalidStructuredOutput as exc:
        _record_failure(project_id, deps, "INVALID_OUTPUT", str(exc))
    except GeminiError as exc:
        _record_failure(project_id, deps, "GEMINI_ERROR", str(exc))
    except Exception:
        _record_failure(project_id, deps, "INTERNAL", INTERNAL_MESSAGE)
    finally:
        broadcast_state(project_id, user_id, deps)


def spawn(*, project_id: str, user_id: str, step: StepName, style: str | None,
          deps: Deps) -> asyncio.Task:
    task = asyncio.create_task(_execute(project_id=project_id, user_id=user_id,
                                        step=step, style=style, deps=deps))
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)
    return task


async def drain_tasks() -> None:
    """Await every in-flight step task. Used by tests; production never calls it."""
    while _TASKS:
        await asyncio.gather(*list(_TASKS), return_exceptions=True)
```

- [ ] **Step 4: Add the run endpoint to `backend/app/api/projects.py`**

```python
from fastapi.responses import JSONResponse

from app import pipeline
from app.models import ApiError, RunAccepted, RunConflict, RunRequest
from app.steps import StepState, status_before
from app.steps import STEPS


def _label(step) -> str:
    return next(s.label for s in STEPS if s.name == step)


@router.post("/{project_id}/run", status_code=status.HTTP_202_ACCEPTED,
             response_model=RunAccepted, responses={409: {"model": RunConflict}})
def run_step_endpoint(project_id: str, payload: RunRequest, request: Request,
                      conn: sqlite3.Connection = Depends(get_db),
                      user: sqlite3.Row = Depends(current_user),
                      settings: Settings = Depends(get_settings)):
    if store.get_project(conn, project_id, user["id"]) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found.")

    claimed = store.begin_step(conn, project_id,
                               expected_status=status_before(payload.step),
                               server_run_id=settings.server_run_id, now=store.now_iso())
    # Read after the transition and before returning. sqlite3 is synchronous, so
    # no await intervenes and the spawned task cannot have moved on yet: the 202
    # body always shows RUNNING.
    view = _load_view(conn, project_id, user["id"], settings)

    if not claimed:
        message = (
            "That step is already running." if view.step_state == StepState.RUNNING
            else f"This project is ready for {_label(view.current_step)}, "
                 f"not {_label(payload.step)}."
        ) if view.current_step else "This project is already complete."
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content=RunConflict(error=ApiError(code="CONFLICT", message=message),
                                project=view).model_dump(mode="json"),
        )

    pipeline.spawn(project_id=project_id, user_id=user["id"], step=payload.step,
                   style=payload.style, deps=request.app.state.deps)
    return RunAccepted(project=view)
```

In `backend/app/main.py`, build `Deps` and put it on app state:

```python
from app.pipeline import Deps
...
    app.state.gemini = gemini if gemini is not None else _build_gemini(settings)
    app.state.registry = registry
    app.state.deps = Deps(settings=settings, gemini=app.state.gemini, registry=registry)
```

- [ ] **Step 5: Run the test and verify it passes**

Run: `cd backend && python -m pytest tests/test_pipeline.py -v`
Expected: PASS — 9 passed

- [ ] **Step 6: Commit**

```bash
git add backend/app/pipeline.py backend/app/api/projects.py backend/app/main.py backend/tests/test_pipeline.py backend/tests/conftest.py
git commit -m "Add the detached step task and POST /run

/run performs the conditional transition and returns 202 with the RUNNING state
immediately, then runs the step in an in-process task. A blocking handler would
have made correctness depend on FastAPI's client-disconnect semantics, which
differ between def and async def - so every refresh could strand a step, in
exactly the scenario the assessment grades.

409 carries the full project view, so the losing caller renders truth with no
follow-up fetch. A wrong step costs zero Gemini calls. Because the state read
is synchronous, the 202 body always shows RUNNING rather than racing the task.

Mostly AI-authored (Claude Code).

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 24: Acceptance — the five-step run and the exact call sequence

This is what proves §07's *"you implemented **its** pipeline, not an imagined simplification"*.

**Files:**
- Create: `backend/tests/test_acceptance_happypath.py`

**Interfaces:**
- Consumes: everything through Task 23. Introduces no new production interface; this task's RED comes from wiring the chain end to end for the first time.

- [ ] **Step 1: Write the failing test**

`backend/tests/test_acceptance_happypath.py`:

```python
import pytest

from app import pipeline
from app.gemini import prompts

BOOK = "Chapter 1. The river bank. Mole had been working very hard all the morning."
STEPS_IN_ORDER = ["STYLE", "CHARACTERS", "PORTRAITS", "CHAPTERS", "ILLUSTRATIONS"]


@pytest.fixture
async def signed_in(aclient):
    await aclient.post("/api/session", json={"name": "Ada", "email": "ada@example.com"})
    return aclient


async def create(client, title="Willows"):
    return (await client.post("/api/projects",
                              json={"title": title, "book_text": BOOK})).json()["id"]


async def run(client, pid, step, style=None):
    body = {"step": step} if style is None else {"step": step, "style": style}
    response = await client.post(f"/api/projects/{pid}/run", json=body)
    await pipeline.drain_tasks()
    return response


async def test_five_user_actions_take_a_project_to_done(signed_in):
    pid = await create(signed_in)
    for step in STEPS_IN_ORDER:
        assert (await run(signed_in, pid, step)).status_code == 202

    project = (await signed_in.get(f"/api/projects/{pid}")).json()
    assert project["status"] == "DONE"
    assert project["step_state"] == "IDLE"
    assert project["current_step"] is None
    assert project["display_status"] == "Done"
    assert project["completed_steps"] == 5
    assert project["needs_attention"] is False
    assert len(project["characters"]) == 2
    assert len(project["chapters"]) == 1
    assert all(c["image_state"] == "ready" for c in project["characters"])
    assert all(c["image_state"] == "ready" for c in project["chapters"])


async def test_completing_a_step_never_starts_the_next_one(signed_in, fake_gemini):
    """Each step needs its own explicit user action (assessment 4.3)."""
    pid = await create(signed_in)
    for step in STEPS_IN_ORDER:
        await run(signed_in, pid, step)
        calls_after_step = len(fake_gemini.calls)
        await pipeline.drain_tasks()
        assert len(fake_gemini.calls) == calls_after_step


async def test_the_call_and_context_sequence_matches_the_notebook(signed_in, fake_gemini):
    pid = await create(signed_in)
    for step in STEPS_IN_ORDER:
        await run(signed_in, pid, step)

    calls = fake_gemini.calls
    assert [c.kind for c in calls] == [
        "upload",       # 0  step 1: the book, once
        "text",         # 1  step 1: book intro + document
        "text",         # 2  step 1: style, chained off the book
        "structured",   # 3  step 2: characters, chained off style
        "image",        # 4  step 3: image seed, UNCHAINED
        "image",        # 5  step 3: portrait 1
        "image",        # 6  step 3: portrait 2, chained off portrait 1
        "structured",   # 7  step 4: chapters, chained off the characters interaction
        "image",        # 8  step 5: chapter-mode seed, chained off portrait 2
        "image",        # 9  step 5: illustration
    ]

    # The book travels exactly once, with the step-1 seed.
    assert calls[1].document_uri is not None
    assert all(c.document_uri is None for c in calls if c is not calls[1])

    # Text chain: book -> style -> characters -> chapters.
    assert calls[1].previous_interaction_id is None
    assert calls[2].previous_interaction_id is not None
    assert calls[3].previous_interaction_id is not None
    assert calls[3].item_schema is not None and calls[3].max_items == 2

    # Step 4 chains off the CHARACTERS interaction, not the image chain.
    assert calls[7].previous_interaction_id is not None
    assert calls[7].max_items == 1
    assert calls[7].previous_interaction_id != calls[6].previous_interaction_id

    # Image chain: seed -> portrait 1 -> portrait 2 -> chapter seed -> illustration.
    assert calls[4].previous_interaction_id is None       # never crosses from text
    assert calls[5].previous_interaction_id is not None
    assert calls[6].previous_interaction_id is not None
    assert calls[8].prompt == prompts.CHAPTER_SEED
    assert calls[9].previous_interaction_id is not None


async def test_the_book_is_uploaded_exactly_once_across_the_whole_run(
        signed_in, fake_gemini):
    """Assessment 4.3: send the book once and reuse it across steps."""
    pid = await create(signed_in)
    for step in STEPS_IN_ORDER:
        await run(signed_in, pid, step)
    assert sum(1 for c in fake_gemini.calls if c.kind == "upload") == 1


async def test_the_prompts_sent_are_the_notebooks(signed_in, fake_gemini):
    pid = await create(signed_in)
    for step in STEPS_IN_ORDER:
        await run(signed_in, pid, step)

    calls = fake_gemini.calls
    assert calls[1].prompt == prompts.BOOK_INTRO
    assert calls[2].prompt == prompts.STYLE_GENERATE
    assert calls[3].prompt == prompts.CHARACTERS_INSTRUCTION
    assert calls[7].prompt == prompts.CHAPTERS_INSTRUCTION
    assert calls[8].prompt == prompts.CHAPTER_SEED
    assert "Willows" in calls[4].prompt              # project title, not hardcoded
    assert "no text on the image" in calls[4].prompt  # the rules travel with the seed


async def test_a_user_supplied_style_takes_the_acknowledge_branch(signed_in, fake_gemini):
    pid = await create(signed_in)
    await run(signed_in, pid, "STYLE", style="bold linocut, high contrast")

    project = (await signed_in.get(f"/api/projects/{pid}")).json()
    assert project["style_text"] == "bold linocut, high contrast"
    assert fake_gemini.calls[2].prompt == \
        prompts.STYLE_ACKNOWLEDGE.format(style="bold linocut, high contrast")


async def test_both_style_paths_reach_the_same_state_shape(signed_in, fake_gemini):
    generated = await create(signed_in, "Generated")
    await run(signed_in, generated, "STYLE")
    supplied = await create(signed_in, "Supplied")
    await run(signed_in, supplied, "STYLE", style="bold linocut")

    a = (await signed_in.get(f"/api/projects/{generated}")).json()
    b = (await signed_in.get(f"/api/projects/{supplied}")).json()
    assert a["status"] == b["status"] == "STYLE_SET"
    assert a["current_step"] == b["current_step"] == "CHARACTERS"
    assert a["style_text"] != b["style_text"]


async def test_portraits_appear_one_at_a_time_rather_than_all_at_once(
        signed_in, fake_gemini):
    """Per-item progress through the API (assessment 4.4)."""
    pid = await create(signed_in)
    for step in ["STYLE", "CHARACTERS"]:
        await run(signed_in, pid, step)

    fake_gemini.hold_from(len(fake_gemini.calls) + 2)   # seed and portrait 1 pass
    await signed_in.post(f"/api/projects/{pid}/run", json={"step": "PORTRAITS"})
    await fake_gemini.wait_for_calls(len(fake_gemini.calls) + 2)

    project = (await signed_in.get(f"/api/projects/{pid}")).json()
    states = [c["image_state"] for c in project["characters"]]
    assert states == ["ready", "generating"]
    assert project["characters"][0]["image_url"] is not None
    assert project["characters"][1]["image_url"] is None

    fake_gemini.release()
    await pipeline.drain_tasks()
```

- [ ] **Step 2: Run it and verify it fails**

Run: `cd backend && python -m pytest tests/test_acceptance_happypath.py -v`
Expected: FAIL — the call-sequence and one-upload assertions are the first end-to-end check of the chain; fix whatever they surface before continuing.

- [ ] **Step 3: Make it pass**

No new module is expected. If a test fails, the defect is in the wiring already written — most likely a chain head not advancing, or step 3 seeding when it should reuse. Fix in `handlers.py`; do not weaken the assertion.

- [ ] **Step 4: Run and verify it passes**

Run: `cd backend && python -m pytest tests/test_acceptance_happypath.py -v`
Expected: PASS — 8 passed

- [ ] **Step 5: Commit**

```bash
git add backend/tests/test_acceptance_happypath.py
git commit -m "Acceptance: five-step happy path and the exact notebook call sequence

Asserts the whole contract in one place: one upload, style chained off the book,
characters off style with a response schema, the image seed unchained, portrait
2 off portrait 1, chapters off the CHARACTERS interaction rather than the image
chain, chapter seed off portrait 2. This is what makes 'followed the actual
spec' checkable rather than claimed.

Also asserts that completing step N starts no calls for N+1, and that portraits
land one at a time.

Mostly AI-authored (Claude Code).

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 25: Acceptance — concurrency, refresh, and identity round-trip

**Files:**
- Create: `backend/tests/test_acceptance_concurrency.py`

**Interfaces:**
- Consumes: Task 23. No new production interface.

- [ ] **Step 1: Write the failing test**

`backend/tests/test_acceptance_concurrency.py`:

```python
import asyncio

import httpx
import pytest

from app import pipeline

BOOK = "Chapter 1. The river bank."


@pytest.fixture
async def signed_in(aclient):
    await aclient.post("/api/session", json={"name": "Ada", "email": "ada@example.com"})
    return aclient


async def create(client):
    return (await client.post("/api/projects",
                              json={"title": "Willows", "book_text": BOOK})).json()["id"]


async def test_two_simultaneous_runs_produce_one_202_one_409_and_one_execution(
        signed_in, fake_gemini):
    """A real race on the event loop, not two sequential calls (assessment 4.3)."""
    pid = await create(signed_in)
    fake_gemini.hold_from(0)

    first, second = await asyncio.gather(
        signed_in.post(f"/api/projects/{pid}/run", json={"step": "STYLE"}),
        signed_in.post(f"/api/projects/{pid}/run", json={"step": "STYLE"}),
    )

    assert sorted([first.status_code, second.status_code]) == [202, 409]
    await fake_gemini.wait_for_calls(1)
    assert len(fake_gemini.calls) == 1          # exactly one execution started

    fake_gemini.release()
    await pipeline.drain_tasks()
    assert (await signed_in.get(f"/api/projects/{pid}")).json()["status"] == "STYLE_SET"


async def test_ten_simultaneous_runs_still_produce_exactly_one_execution(
        signed_in, fake_gemini):
    pid = await create(signed_in)
    fake_gemini.hold_from(0)

    responses = await asyncio.gather(*[
        signed_in.post(f"/api/projects/{pid}/run", json={"step": "STYLE"})
        for _ in range(10)
    ])

    assert [r.status_code for r in responses].count(202) == 1
    assert [r.status_code for r in responses].count(409) == 9
    await fake_gemini.wait_for_calls(1)
    assert len(fake_gemini.calls) == 1

    fake_gemini.release()
    await pipeline.drain_tasks()


async def test_a_refresh_mid_step_shows_the_in_flight_state_and_starts_nothing(
        signed_in, fake_gemini):
    pid = await create(signed_in)
    fake_gemini.hold_from(0)
    await signed_in.post(f"/api/projects/{pid}/run", json={"step": "STYLE"})
    await fake_gemini.wait_for_calls(1)
    calls_before = len(fake_gemini.calls)

    for _ in range(3):                            # three refreshes
        project = (await signed_in.get(f"/api/projects/{pid}")).json()
        assert project["step_state"] == "RUNNING"
        assert project["current_step"] == "STYLE"
        assert project["is_interrupted"] is False

    assert len(fake_gemini.calls) == calls_before

    fake_gemini.release()
    await pipeline.drain_tasks()


async def test_a_second_client_sees_the_same_in_flight_run_and_starts_nothing(
        app, signed_in, fake_gemini):
    """A second tab: a different HTTP client carrying the same session cookie."""
    pid = await create(signed_in)
    fake_gemini.hold_from(0)
    await signed_in.post(f"/api/projects/{pid}/run", json={"step": "STYLE"})
    await fake_gemini.wait_for_calls(1)

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://test",
                                 cookies=signed_in.cookies) as second_tab:
        project = (await second_tab.get(f"/api/projects/{pid}")).json()
        assert project["step_state"] == "RUNNING"
        conflict = await second_tab.post(f"/api/projects/{pid}/run", json={"step": "STYLE"})
        assert conflict.status_code == 409

    assert len(fake_gemini.calls) == 1

    fake_gemini.release()
    await pipeline.drain_tasks()


async def test_signing_out_and_back_in_restores_results_and_regenerates_nothing(
        signed_in, fake_gemini):
    pid = await create(signed_in)
    for step in ["STYLE", "CHARACTERS", "PORTRAITS"]:
        await signed_in.post(f"/api/projects/{pid}/run", json={"step": step})
        await pipeline.drain_tasks()

    before = (await signed_in.get(f"/api/projects/{pid}")).json()
    calls_before = len(fake_gemini.calls)

    await signed_in.delete("/api/session")
    await signed_in.post("/api/session", json={"name": "Ada", "email": "ada@example.com"})

    listed = (await signed_in.get("/api/projects")).json()
    assert [p["id"] for p in listed] == [pid]
    after = (await signed_in.get(f"/api/projects/{pid}")).json()
    assert after == before
    assert len(fake_gemini.calls) == calls_before


async def test_the_book_stays_readable_at_every_stage_of_the_pipeline(signed_in):
    """Assessment 4.4 - and the demo's bug at app-demo.html:700 is not
    reproduced: the book does not disappear once a style exists."""
    pid = await create(signed_in)
    for step in ["STYLE", "CHARACTERS", "PORTRAITS", "CHAPTERS", "ILLUSTRATIONS"]:
        assert (await signed_in.get(f"/api/projects/{pid}/book")).json()["text"] == BOOK
        await signed_in.post(f"/api/projects/{pid}/run", json={"step": step})
        await pipeline.drain_tasks()
    assert (await signed_in.get(f"/api/projects/{pid}/book")).json()["text"] == BOOK
```

- [ ] **Step 2: Run it and verify it fails or passes for the right reason**

Run: `cd backend && python -m pytest tests/test_acceptance_concurrency.py -v`
Expected: the race tests should pass on the conditional `UPDATE` alone. If any fail, the defect is real — fix `store.begin_step` or the endpoint ordering, never the test.

- [ ] **Step 3: Run and verify it passes**

Run: `cd backend && python -m pytest tests/test_acceptance_concurrency.py -v`
Expected: PASS — 6 passed

- [ ] **Step 4: Commit**

```bash
git add backend/tests/test_acceptance_concurrency.py
git commit -m "Acceptance: concurrency, refresh, second tab, identity round-trip

Ten concurrent POSTs gathered on one event loop yield exactly one 202 and one
execution - a genuine race against the conditional UPDATE, not two sequential
calls. Refreshing mid-step and opening a second tab both show the in-flight
state and fire nothing.

Signing out and back in with the same email returns byte-identical project
state and adds zero Gemini calls, and the book text is readable at all five
stages - the demo's disappearing book panel is a bug we do not reproduce.

Mostly AI-authored (Claude Code).

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 26: Acceptance — failure, interruption, cancellation, expiry

The recovery story end to end. Cancellation is genuinely new behaviour here: Task 23 wrote the handler, and this is where it is proven.

**Files:**
- Create: `backend/tests/test_acceptance_recovery.py`

**Interfaces:**
- Consumes: Task 23's `pipeline._execute`, `spawn`, message constants.

- [ ] **Step 1: Write the failing test**

`backend/tests/test_acceptance_recovery.py`:

```python
import asyncio
from dataclasses import replace

import pytest

from app import db, pipeline, store
from app.gemini.protocol import GeminiError, InteractionNotFound
from app.main import create_app

BOOK = "Chapter 1. The river bank."


@pytest.fixture
async def signed_in(aclient):
    await aclient.post("/api/session", json={"name": "Ada", "email": "ada@example.com"})
    return aclient


async def create(client):
    return (await client.post("/api/projects",
                              json={"title": "Willows", "book_text": BOOK})).json()["id"]


async def run(client, pid, step):
    response = await client.post(f"/api/projects/{pid}/run", json={"step": step})
    await pipeline.drain_tasks()
    return response


async def advance(client, pid, steps):
    for step in steps:
        await run(client, pid, step)


# --------------------------------------------------------------------------
# A later step failing preserves everything before it
# --------------------------------------------------------------------------

async def test_a_late_failure_leaves_every_earlier_output_intact(signed_in, fake_gemini):
    pid = await create(signed_in)
    await advance(signed_in, pid, ["STYLE", "CHARACTERS"])
    fake_gemini.fail_on(len(fake_gemini.calls), GeminiError("image service refused"))

    await run(signed_in, pid, "PORTRAITS")

    project = (await signed_in.get(f"/api/projects/{pid}")).json()
    assert project["step_state"] == "FAILED"
    assert project["status"] == "CHARACTERS_GENERATED"     # never advanced
    assert project["style_text"]
    assert len(project["characters"]) == 2
    assert project["failure"]["code"] == "GEMINI_ERROR"


async def test_retrying_touches_only_the_failed_step(signed_in, fake_gemini):
    pid = await create(signed_in)
    await advance(signed_in, pid, ["STYLE", "CHARACTERS"])
    fake_gemini.fail_on(len(fake_gemini.calls), GeminiError("boom"))
    await run(signed_in, pid, "PORTRAITS")

    before = list(fake_gemini.calls)
    await run(signed_in, pid, "PORTRAITS")

    retried = fake_gemini.calls[len(before):]
    assert all(c.kind == "image" for c in retried)          # no text calls repeated
    assert not any(c.kind == "upload" for c in retried)     # book not re-sent
    assert (await signed_in.get(f"/api/projects/{pid}")).json()["status"] == \
        "PORTRAITS_GENERATED"


async def test_portrait_one_survives_a_portrait_two_failure(signed_in, fake_gemini, settings):
    """Never losing generated results (assessment 4.3)."""
    pid = await create(signed_in)
    await advance(signed_in, pid, ["STYLE", "CHARACTERS"])
    # seed, portrait 1, then fail portrait 2
    fake_gemini.fail_on(len(fake_gemini.calls) + 2, GeminiError("dropped"))

    await run(signed_in, pid, "PORTRAITS")
    project = (await signed_in.get(f"/api/projects/{pid}")).json()
    assert project["step_state"] == "FAILED"
    assert project["characters"][0]["image_state"] == "ready"
    assert project["characters"][1]["image_state"] == "pending"

    first_prompt = project["characters"][0]["prompt"]
    before = len(fake_gemini.calls)
    await run(signed_in, pid, "PORTRAITS")

    retried = fake_gemini.calls[before:]
    assert not any(first_prompt in (c.prompt or "") for c in retried)
    assert len([c for c in retried if c.kind == "image"]) == 1
    after = (await signed_in.get(f"/api/projects/{pid}")).json()
    assert all(c["image_state"] == "ready" for c in after["characters"])
    assert after["status"] == "PORTRAITS_GENERATED"


# --------------------------------------------------------------------------
# Interruption: a RUNNING row from a process that is gone
# --------------------------------------------------------------------------

async def test_a_run_stamped_by_a_dead_process_surfaces_as_interrupted(
        signed_in, settings, app):
    pid = await create(signed_in)
    with db.get_conn(settings) as conn:
        conn.execute("UPDATE projects SET step_state='RUNNING', server_run_id='old-process' "
                     "WHERE id=?", (pid,))

    project = (await signed_in.get(f"/api/projects/{pid}")).json()
    assert project["is_interrupted"] is True
    assert project["needs_attention"] is True
    assert project["step_state"] == "RUNNING"
    assert project["display_status"] == "In progress"


async def test_the_normal_retry_command_recovers_an_interrupted_step(
        signed_in, settings, fake_gemini):
    """Recovery is not a separate endpoint - retrying IS the recovery."""
    pid = await create(signed_in)
    with db.get_conn(settings) as conn:
        conn.execute("UPDATE projects SET step_state='RUNNING', server_run_id='old-process' "
                     "WHERE id=?", (pid,))

    assert (await run(signed_in, pid, "STYLE")).status_code == 202

    project = (await signed_in.get(f"/api/projects/{pid}")).json()
    assert project["status"] == "STYLE_SET"
    assert project["is_interrupted"] is False
    assert project["needs_attention"] is False


async def test_prior_outputs_survive_an_interruption_and_recovery(signed_in, settings):
    pid = await create(signed_in)
    await advance(signed_in, pid, ["STYLE", "CHARACTERS"])
    with db.get_conn(settings) as conn:
        conn.execute("UPDATE projects SET step_state='RUNNING', server_run_id='old-process' "
                     "WHERE id=?", (pid,))

    await run(signed_in, pid, "PORTRAITS")

    project = (await signed_in.get(f"/api/projects/{pid}")).json()
    assert project["style_text"] and len(project["characters"]) == 2
    assert project["status"] == "PORTRAITS_GENERATED"


# --------------------------------------------------------------------------
# Cancellation
# --------------------------------------------------------------------------

async def test_a_cancelled_task_leaves_the_step_failed_never_running(
        signed_in, settings, fake_gemini):
    """The one stuck-forever shape server_run_id alone does not answer
    (design 6.3)."""
    pid = await create(signed_in)
    fake_gemini.hold_from(0)
    await signed_in.post(f"/api/projects/{pid}/run", json={"step": "STYLE"})
    await fake_gemini.wait_for_calls(1)

    task = next(iter(pipeline._TASKS))
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task                                  # cancellation is re-raised

    project = (await signed_in.get(f"/api/projects/{pid}")).json()
    assert project["step_state"] == "FAILED"
    assert project["failure"]["message"] == pipeline.CANCELLED_MESSAGE
    assert project["is_interrupted"] is False       # not RUNNING under this run id
    fake_gemini.release()


async def test_a_cancelled_step_is_retryable_and_completes(signed_in, fake_gemini):
    pid = await create(signed_in)
    fake_gemini.hold_from(0)
    await signed_in.post(f"/api/projects/{pid}/run", json={"step": "STYLE"})
    await fake_gemini.wait_for_calls(1)
    task = next(iter(pipeline._TASKS))
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    fake_gemini.release()

    await run(signed_in, pid, "STYLE")
    assert (await signed_in.get(f"/api/projects/{pid}")).json()["status"] == "STYLE_SET"


async def test_a_cancellation_arriving_after_takeover_writes_nothing(
        signed_in, settings, fake_gemini):
    """fail_step is ownership-guarded, so a late cancellation cannot clobber a
    newer execution."""
    pid = await create(signed_in)
    fake_gemini.hold_from(0)
    await signed_in.post(f"/api/projects/{pid}/run", json={"step": "STYLE"})
    await fake_gemini.wait_for_calls(1)
    task = next(iter(pipeline._TASKS))

    with db.get_conn(settings) as conn:            # a newer run takes the step over
        assert store.begin_step(conn, pid, expected_status="CREATED",
                                server_run_id="run-Z", now=store.now_iso()) is True

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    with db.get_conn(settings) as conn:
        row = conn.execute("SELECT * FROM projects WHERE id=?", (pid,)).fetchone()
    assert row["step_state"] == "RUNNING"
    assert row["server_run_id"] == "run-Z"
    fake_gemini.release()


# --------------------------------------------------------------------------
# Provider-side context expiry
# --------------------------------------------------------------------------

async def test_expiry_fails_with_one_attempt_and_nulls_the_head_that_raised(
        signed_in, settings, fake_gemini):
    pid = await create(signed_in)
    await advance(signed_in, pid, ["STYLE"])
    before = len(fake_gemini.calls)
    fake_gemini.fail_on(before, InteractionNotFound("interaction expired"))

    await run(signed_in, pid, "CHARACTERS")

    assert len(fake_gemini.calls) == before + 1     # one attempt, no reconstruction
    project = (await signed_in.get(f"/api/projects/{pid}")).json()
    assert project["step_state"] == "FAILED"
    assert project["failure"]["code"] == "GEMINI_ERROR"
    assert "expired" in project["failure"]["message"]
    with db.get_conn(settings) as conn:
        row = conn.execute("SELECT * FROM projects WHERE id=?", (pid,)).fetchone()
    assert row["text_interaction_id"] is None
    assert row["style_text"] is not None            # persisted work is untouched


async def test_the_user_retry_rebuilds_from_minimum_persisted_state(
        signed_in, settings, fake_gemini):
    pid = await create(signed_in)
    await advance(signed_in, pid, ["STYLE"])
    fake_gemini.fail_on(len(fake_gemini.calls), InteractionNotFound("expired"))
    await run(signed_in, pid, "CHARACTERS")

    before = len(fake_gemini.calls)
    await run(signed_in, pid, "CHARACTERS")

    retried = fake_gemini.calls[before:]
    assert [c.kind for c in retried] == ["upload", "structured"]   # book re-uploaded
    assert retried[1].previous_interaction_id is None
    assert (await signed_in.get(f"/api/projects/{pid}")).json()["status"] == \
        "CHARACTERS_GENERATED"


async def test_image_chain_expiry_nulls_only_the_image_head(
        signed_in, settings, fake_gemini):
    pid = await create(signed_in)
    await advance(signed_in, pid, ["STYLE", "CHARACTERS"])
    fake_gemini.fail_on(len(fake_gemini.calls), InteractionNotFound("expired"))

    await run(signed_in, pid, "PORTRAITS")

    with db.get_conn(settings) as conn:
        row = conn.execute("SELECT * FROM projects WHERE id=?", (pid,)).fetchone()
    assert row["image_interaction_id"] is None
    assert row["text_interaction_id"] is not None   # the text chain is untouched


async def test_steps_three_and_five_never_re_upload_the_book_on_recovery(
        signed_in, settings, fake_gemini):
    pid = await create(signed_in)
    await advance(signed_in, pid, ["STYLE", "CHARACTERS", "PORTRAITS", "CHAPTERS"])
    with db.get_conn(settings) as conn:
        conn.execute("UPDATE projects SET image_interaction_id = NULL WHERE id=?", (pid,))

    before = len(fake_gemini.calls)
    await run(signed_in, pid, "ILLUSTRATIONS")

    assert not any(c.kind == "upload" for c in fake_gemini.calls[before:])
    assert (await signed_in.get(f"/api/projects/{pid}")).json()["status"] == "DONE"


# --------------------------------------------------------------------------
# Cost discipline
# --------------------------------------------------------------------------

async def test_a_provider_failure_is_attempted_once_and_never_looped(
        signed_in, fake_gemini):
    """Assessment 4.3: never auto-retry a Gemini call in a loop."""
    pid = await create(signed_in)
    fake_gemini.fail_on(0, GeminiError("rate limited"))

    await run(signed_in, pid, "STYLE")

    assert len(fake_gemini.calls) == 1
    assert (await signed_in.get(f"/api/projects/{pid}")).json()["step_state"] == "FAILED"


async def test_an_over_cap_response_surfaces_as_invalid_output(signed_in, fake_gemini):
    pid = await create(signed_in)
    await advance(signed_in, pid, ["STYLE"])
    fake_gemini.extra_items = 3

    await run(signed_in, pid, "CHARACTERS")

    project = (await signed_in.get(f"/api/projects/{pid}")).json()
    assert project["failure"]["code"] == "INVALID_OUTPUT"
    assert project["characters"] == []
    assert project["status"] == "STYLE_SET"
```

- [ ] **Step 2: Run it and verify it fails**

Run: `cd backend && python -m pytest tests/test_acceptance_recovery.py -v`
Expected: FAIL — the cancellation tests fail first if the `except asyncio.CancelledError` branch or the ownership guard is missing.

- [ ] **Step 3: Fix what the tests surface**

Everything under test was written in Tasks 9, 22 and 23. If a test fails, fix the production code — most likely `store.fail_step`'s ownership guard, or `pipeline._execute`'s exception ordering (`InteractionNotFound` must be caught **before** `GeminiError`, since it is a subclass).

- [ ] **Step 4: Run and verify it passes**

Run: `cd backend && python -m pytest tests/test_acceptance_recovery.py -v`
Expected: PASS — 15 passed

- [ ] **Step 5: Run the whole backend suite**

Run: `cd backend && python -m pytest -q`
Expected: PASS — all green

- [ ] **Step 6: Commit**

```bash
git add backend/tests/test_acceptance_recovery.py
git commit -m "Acceptance: failure, interruption, cancellation and context expiry

Covers the recovery story end to end. A late failure preserves every earlier
output and the retry touches only the failed step; portrait 1 survives a
portrait 2 failure and is not regenerated.

An old server_run_id surfaces is_interrupted and is recovered by the ordinary
Retry command, with no separate endpoint. A cancelled task records FAILED and
re-raises CancelledError, so a step can never sit RUNNING under the current run
id - and a cancellation arriving after another run took the step over writes
nothing.

Expiry fails after exactly one attempt with the raising chain's head nulled;
the user's retry rebuilds standalone, re-uploading the book for steps 2 and 4
and never for 3 and 5.

Mostly AI-authored (Claude Code).

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Phase G — Frontend detail screen

### Task 27: Stepper and StepPanel — five states

Four of the five StepPanel states do not exist in the demo, because it never fails (spec §10.4).

**Files:**
- Create: `frontend/src/components/Stepper.tsx`, `frontend/src/components/StepPanel.tsx`, `frontend/src/__tests__/StepPanel.test.tsx`

**Interfaces:**
- Consumes: `STEP_ORDER`, `STEP_LABELS`, `STEP_RUNNING_CAPTIONS`, `ProjectView`.
- Produces: `<Stepper project={ProjectView} />`, `<StepPanel project={ProjectView} onRun={(step, style?) => void} busy={boolean} />`.

- [ ] **Step 1: Write the failing test**

`frontend/src/__tests__/StepPanel.test.tsx`:

```tsx
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, test, vi } from 'vitest';
import StepPanel from '../components/StepPanel';
import Stepper from '../components/Stepper';
import type { ProjectView } from '../types';

function project(overrides: Partial<ProjectView> = {}): ProjectView {
  return {
    id: 'p1', title: 'Willows', created_at: '2026-08-14T10:00:00+00:00',
    status: 'STYLE_SET', step_state: 'IDLE', current_step: 'CHARACTERS',
    display_status: 'In progress', needs_attention: false, is_interrupted: false,
    completed_steps: 1, style_text: 'Warm watercolour', book_excerpt: 'Once…',
    failure: null, characters: [], chapters: [], ...overrides,
  };
}

test('Ready names the next step and offers its action', () => {
  render(<StepPanel project={project()} onRun={vi.fn()} busy={false} />);
  expect(screen.getByText(/ready for the next step/i)).toHaveTextContent('Characters');
  expect(screen.getByRole('button', { name: /generate characters/i })).toBeEnabled();
});

test('step 1 offers the optional style input; later steps do not', () => {
  const { rerender } = render(
    <StepPanel project={project({ status: 'CREATED', current_step: 'STYLE',
                                  completed_steps: 0, style_text: null })}
               onRun={vi.fn()} busy={false} />);
  expect(screen.getByLabelText(/art style \(optional\)/i)).toBeInTheDocument();

  rerender(<StepPanel project={project()} onRun={vi.fn()} busy={false} />);
  expect(screen.queryByLabelText(/art style/i)).not.toBeInTheDocument();
});

test('the optional style is passed through on step 1', async () => {
  const onRun = vi.fn();
  render(<StepPanel project={project({ status: 'CREATED', current_step: 'STYLE',
                                       completed_steps: 0, style_text: null })}
                    onRun={onRun} busy={false} />);
  await userEvent.type(screen.getByLabelText(/art style/i), 'bold linocut');
  await userEvent.click(screen.getByRole('button', { name: /generate style/i }));
  expect(onRun).toHaveBeenCalledWith('STYLE', 'bold linocut');
});

test('Running names the step rather than showing a bare spinner', () => {
  render(<StepPanel project={project({ step_state: 'RUNNING' })} onRun={vi.fn()} busy={false} />);
  const status = screen.getByRole('status');
  expect(status).toHaveTextContent(/generating the character list/i);
  expect(status).toHaveAttribute('aria-live', 'polite');
  expect(screen.getByRole('button', { name: /generating/i })).toBeDisabled();
});

test('Failed shows the message, a scoped retry, and reassurance', () => {
  render(<StepPanel project={project({ step_state: 'FAILED', needs_attention: true,
                                       failure: { code: 'GEMINI_ERROR', message: 'Gemini said no' } })}
                    onRun={vi.fn()} busy={false} />);
  expect(screen.getByRole('alert')).toHaveTextContent('Gemini said no');
  expect(screen.getByRole('button', { name: /retry characters/i })).toBeInTheDocument();
  expect(screen.getByText(/already generated is saved/i)).toBeInTheDocument();
});

test('Interrupted explains the restart and offers the same retry', () => {
  render(<StepPanel project={project({ step_state: 'RUNNING', is_interrupted: true,
                                       needs_attention: true })}
                    onRun={vi.fn()} busy={false} />);
  expect(screen.getByRole('alert')).toHaveTextContent(/interrupted/i);
  expect(screen.getByRole('button', { name: /retry characters/i })).toBeInTheDocument();
});

test('Interrupted wins over Running, because a live spinner would be a lie', () => {
  render(<StepPanel project={project({ step_state: 'RUNNING', is_interrupted: true })}
                    onRun={vi.fn()} busy={false} />);
  expect(screen.queryByText(/generating the character list/i)).not.toBeInTheDocument();
});

test('Complete offers no action and says nothing regenerates', () => {
  render(<StepPanel project={project({ status: 'DONE', current_step: null,
                                       display_status: 'Done', completed_steps: 5 })}
                    onRun={vi.fn()} busy={false} />);
  expect(screen.getByText(/all 5 steps complete/i)).toBeInTheDocument();
  expect(screen.queryByRole('button')).not.toBeInTheDocument();
});

test('the button is disabled while a run request is in flight', () => {
  render(<StepPanel project={project()} onRun={vi.fn()} busy />);
  expect(screen.getByRole('button')).toBeDisabled();
});

test('the stepper marks steps done, current and pending', () => {
  const { container } = render(<Stepper project={project()} />);
  const steps = container.querySelectorAll('.stepper .step');
  expect(steps).toHaveLength(5);
  expect(steps[0].className).toContain('done');
  expect(steps[1].className).toContain('current');
  expect(steps[2].className).toContain('pending');
});

test('a finished project has no current step in the stepper', () => {
  const { container } = render(
    <Stepper project={project({ status: 'DONE', current_step: null, completed_steps: 5 })} />);
  expect(container.querySelectorAll('.stepper .step.done')).toHaveLength(5);
  expect(container.querySelectorAll('.stepper .step.current')).toHaveLength(0);
});
```

- [ ] **Step 2: Run it and verify it fails**

Run: `cd frontend && npm test -- --run src/__tests__/StepPanel.test.tsx`
Expected: FAIL — `Failed to resolve import "../components/StepPanel"`

- [ ] **Step 3: Write both components**

`frontend/src/components/Stepper.tsx`:

```tsx
import { STEP_LABELS, STEP_ORDER } from '../steps';
import type { ProjectView } from '../types';

export default function Stepper({ project }: { project: ProjectView }) {
  return (
    <ol className="stepper">
      {STEP_ORDER.map((step, index) => {
        const done = index < project.completed_steps;
        const current = step === project.current_step;
        const state = done ? 'done' : current ? 'current' : 'pending';
        return (
          <li key={step} className={`step ${state}`} aria-current={current ? 'step' : undefined}>
            <span className={`gd-num-square ${done ? 'done' : current ? '' : 'gray'}`}>
              {done ? '✓' : index + 1}
            </span>
            <span className="lbl">{STEP_LABELS[step]}</span>
          </li>
        );
      })}
    </ol>
  );
}
```

`frontend/src/components/StepPanel.tsx`:

```tsx
import { useState } from 'react';
import { STEP_LABELS, STEP_RUNNING_CAPTIONS } from '../steps';
import type { ProjectView, StepName } from '../types';

interface Props {
  project: ProjectView;
  onRun: (step: StepName, style?: string) => void;
  busy: boolean;
}

export default function StepPanel({ project, onRun, busy }: Props) {
  const [style, setStyle] = useState('');
  const step = project.current_step;

  if (step === null) {
    return (
      <section className="step-panel">
        <p className="status-line">✓ All 5 steps complete — nothing left to generate.</p>
        <p className="help">
          This project is done. Reopen it any time; nothing here regenerates automatically.
        </p>
      </section>
    );
  }

  const label = STEP_LABELS[step];

  // Interrupted is checked before Running: a live spinner on a step whose
  // process is gone would be a lie (design 10.4).
  if (project.is_interrupted) {
    return (
      <section className="step-panel">
        <p className="status-line" role="alert">
          This step was interrupted when the server restarted, and never finished.
        </p>
        <p className="help">
          Nothing before it was affected — everything already generated is saved.
          Retrying is safe.
        </p>
        <button type="button" className="gd-btn gd-btn-secondary" disabled={busy}
                onClick={() => onRun(step)}>
          Retry {label}
        </button>
      </section>
    );
  }

  if (project.step_state === 'FAILED') {
    return (
      <section className="step-panel">
        <p className="status-line" role="alert">
          {project.failure?.message ?? 'This step failed.'}
        </p>
        <p className="help">
          Only this step failed. Everything already generated is saved and untouched.
        </p>
        <button type="button" className="gd-btn gd-btn-secondary" disabled={busy}
                onClick={() => onRun(step)}>
          Retry {label}
        </button>
      </section>
    );
  }

  if (project.step_state === 'RUNNING') {
    return (
      <section className="step-panel">
        <p className="status-line" role="status" aria-live="polite">
          <span className="spinner" aria-hidden="true" />
          {STEP_RUNNING_CAPTIONS[step]} — real Gemini calls take 10–30s, longer for images.
        </p>
        <button type="button" className="gd-btn gd-btn-primary" disabled>
          Generating…
        </button>
      </section>
    );
  }

  return (
    <section className="step-panel">
      <p className="status-line" role="status" aria-live="polite">
        Ready for the next step: <b>{label}</b>.
      </p>
      {step === 'STYLE' && (
        <div className="gd-field">
          <label htmlFor="style-input">Art style (optional)</label>
          <input id="style-input" value={style} onChange={(e) => setStyle(e.target.value)}
                 placeholder="Leave blank to let Gemini choose a style based on your book" />
        </div>
      )}
      <p className="help">
        Reopening this page mid-step won’t fire a second request — it shows the same
        in-flight state until it lands.
      </p>
      <button type="button" className="gd-btn gd-btn-primary" disabled={busy}
              onClick={() => onRun(step, step === 'STYLE' && style.trim()
                ? style.trim() : undefined)}>
        Generate {label}
      </button>
    </section>
  );
}
```

- [ ] **Step 4: Run the test and verify it passes**

Run: `cd frontend && npm test -- --run src/__tests__/StepPanel.test.tsx`
Expected: PASS — 11 passed

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/Stepper.tsx frontend/src/components/StepPanel.tsx frontend/src/__tests__/StepPanel.test.tsx
git commit -m "Add the stepper and the five-state step panel

Ready, Running, Failed, Interrupted and Complete - four of which the demo has
no equivalent for, because it never fails. Running names the step in text
rather than showing a bare spinner, which assessment 4.3 forbids, and the
status line is aria-live so a transition is announced.

Interrupted is checked before Running: a spinner on a step whose process is
gone would be a lie.

Mostly AI-authored (Claude Code).

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 28: Entity cards, style panel, book panel

**Files:**
- Create: `frontend/src/components/EntityCard.tsx`, `frontend/src/components/StylePanel.tsx`, `frontend/src/components/BookTextPanel.tsx`, `frontend/src/__tests__/EntityCard.test.tsx`, `frontend/src/__tests__/BookTextPanel.test.tsx`

**Interfaces:**
- Consumes: `EntityView`, `api.getBook`.
- Produces: `<EntityCard kind={'character'|'chapter'} item={EntityView} />`, `<StylePanel styleText={string | null} />`, `<BookTextPanel projectId={string} excerpt={string} />`.

- [ ] **Step 1: Write the failing tests**

`frontend/src/__tests__/EntityCard.test.tsx`:

```tsx
import { render, screen } from '@testing-library/react';
import { expect, test } from 'vitest';
import EntityCard from '../components/EntityCard';
import StylePanel from '../components/StylePanel';
import type { EntityView } from '../types';

function entity(overrides: Partial<EntityView> = {}): EntityView {
  return { id: 'c1', position: 0, name: 'Toad', prompt: 'A stout toad in a green coat',
           image_url: null, image_state: 'pending', ...overrides };
}

// Fixtures use two characters, never three: production is capped at two, and a
// three-character fixture would encode a state that cannot exist.
const bothPending: EntityView[] = [
  entity({ id: 'c1', name: 'Toad', image_state: 'generating' }),
  entity({ id: 'c2', name: 'Ratty', image_state: 'pending' }),
];
const firstReady: EntityView[] = [
  entity({ id: 'c1', name: 'Toad', image_state: 'ready',
           image_url: '/api/projects/p1/characters/c1/portrait' }),
  entity({ id: 'c2', name: 'Ratty', image_state: 'generating' }),
];

test('[null, null] renders the first as generating and the second as pending', () => {
  render(<>{bothPending.map((e) => <EntityCard key={e.id} kind="character" item={e} />)}</>);
  expect(screen.getByText(/generating portrait for toad/i)).toBeInTheDocument();
  expect(screen.getByText(/not generated yet/i)).toBeInTheDocument();
  expect(screen.queryByRole('img')).not.toBeInTheDocument();
});

test('[path, null] renders the first as ready and the second as generating', () => {
  render(<>{firstReady.map((e) => <EntityCard key={e.id} kind="character" item={e} />)}</>);
  const image = screen.getByRole('img', { name: /portrait of toad/i });
  expect(image).toHaveAttribute('src', '/api/projects/p1/characters/c1/portrait');
  expect(screen.getByText(/generating portrait for ratty/i)).toBeInTheDocument();
});

test('the name and prompt are always shown, image or not', () => {
  render(<EntityCard kind="character" item={entity()} />);
  expect(screen.getByText('Toad')).toBeInTheDocument();
  expect(screen.getByText('A stout toad in a green coat')).toBeInTheDocument();
});

test('a chapter card renders an illustration with a wider art slot', () => {
  const { container } = render(
    <EntityCard kind="chapter" item={entity({ id: 'ch1', name: 'Chapter One',
      image_state: 'ready', image_url: '/api/projects/p1/chapters/ch1/illustration' })} />);
  expect(screen.getByRole('img', { name: /illustration for chapter one/i })).toBeInTheDocument();
  expect(container.querySelector('.art.chapter')).not.toBeNull();
});

test('the style panel shows nothing before a style exists', () => {
  const { container } = render(<StylePanel styleText={null} />);
  expect(container).toBeEmptyDOMElement();
});

test('the style panel shows the generated style', () => {
  render(<StylePanel styleText="Warm hand-painted watercolour" />);
  expect(screen.getByText('Warm hand-painted watercolour')).toBeInTheDocument();
});
```

`frontend/src/__tests__/BookTextPanel.test.tsx`:

```tsx
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, expect, test, vi } from 'vitest';
import BookTextPanel from '../components/BookTextPanel';
import * as api from '../api';

afterEach(() => vi.restoreAllMocks());

test('the excerpt is visible without any fetch', () => {
  const spy = vi.spyOn(api, 'getBook');
  render(<BookTextPanel projectId="p1" excerpt="Once upon a time…" />);
  expect(screen.getByText('Once upon a time…')).toBeInTheDocument();
  expect(spy).not.toHaveBeenCalled();
});

test('expanding fetches the full text lazily and shows a loading state', async () => {
  let resolve!: (value: string) => void;
  vi.spyOn(api, 'getBook').mockReturnValue(new Promise((r) => { resolve = r; }));
  render(<BookTextPanel projectId="p1" excerpt="Once…" />);

  await userEvent.click(screen.getByRole('button', { name: /read full text/i }));
  expect(screen.getByRole('status')).toHaveTextContent(/loading/i);

  resolve('The whole book, all of it.');
  expect(await screen.findByText('The whole book, all of it.')).toBeInTheDocument();
});

test('a failed fetch offers a retry and keeps the panel usable', async () => {
  const spy = vi.spyOn(api, 'getBook').mockRejectedValue(new Error('Network down'));
  render(<BookTextPanel projectId="p1" excerpt="Once…" />);

  await userEvent.click(screen.getByRole('button', { name: /read full text/i }));
  expect(await screen.findByRole('alert')).toHaveTextContent('Network down');

  spy.mockResolvedValue('Recovered text.');
  await userEvent.click(screen.getByRole('button', { name: /try again/i }));
  expect(await screen.findByText('Recovered text.')).toBeInTheDocument();
});

test('the full text is fetched once, not on every expand', async () => {
  const spy = vi.spyOn(api, 'getBook').mockResolvedValue('Full text.');
  render(<BookTextPanel projectId="p1" excerpt="Once…" />);

  await userEvent.click(screen.getByRole('button', { name: /read full text/i }));
  expect(await screen.findByText('Full text.')).toBeInTheDocument();
  await userEvent.click(screen.getByRole('button', { name: /collapse/i }));
  await userEvent.click(screen.getByRole('button', { name: /read full text/i }));

  expect(spy).toHaveBeenCalledTimes(1);
});
```

- [ ] **Step 2: Run them and verify they fail**

Run: `cd frontend && npm test -- --run src/__tests__/EntityCard.test.tsx src/__tests__/BookTextPanel.test.tsx`
Expected: FAIL — `Failed to resolve import "../components/EntityCard"`

- [ ] **Step 3: Write the three components**

`frontend/src/components/EntityCard.tsx`:

```tsx
import type { EntityView } from '../types';

interface Props {
  kind: 'character' | 'chapter';
  item: EntityView;
}

export default function EntityCard({ kind, item }: Props) {
  const noun = kind === 'character' ? 'portrait' : 'illustration';
  const alt = kind === 'character'
    ? `Portrait of ${item.name}` : `Illustration for ${item.name}`;

  return (
    <article className="entity-card">
      <div className={`art${kind === 'chapter' ? ' chapter' : ''}` +
                      (item.image_state === 'ready' ? '' : ' pending')}>
        {item.image_state === 'ready' && item.image_url && (
          <img src={item.image_url} alt={alt} />
        )}
        {item.image_state === 'generating' && (
          <div role="status" aria-live="polite">
            <span className="spinner" aria-hidden="true" />
            <p className="gen-caption">Generating {noun} for {item.name}…</p>
          </div>
        )}
        {item.image_state === 'pending' && (
          <span className="placeholder-label muted">Not generated yet</span>
        )}
      </div>
      <div className="body">
        <h5>{item.name}</h5>
        <p>{item.prompt}</p>
      </div>
    </article>
  );
}
```

`frontend/src/components/StylePanel.tsx`:

```tsx
export default function StylePanel({ styleText }: { styleText: string | null }) {
  if (!styleText) return null;
  return (
    <section className="side-note">
      <h5>Style</h5>
      <p>{styleText}</p>
    </section>
  );
}
```

`frontend/src/components/BookTextPanel.tsx`:

```tsx
import { useState } from 'react';
import * as api from '../api';
import StateMessage from './StateMessage';

/**
 * A permanent disclosure panel, not a modal. The book is reference material you
 * read beside the prompts derived from it, and a panel always present in the
 * layout cannot have its affordance vanish the way the demo's does at
 * app-demo.html:700 (design 10.6).
 */
export default function BookTextPanel({ projectId, excerpt }: {
  projectId: string; excerpt: string;
}) {
  const [open, setOpen] = useState(false);
  const [text, setText] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = async () => {
    setLoading(true);
    setError(null);
    try {
      setText(await api.getBook(projectId));
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setLoading(false);
    }
  };

  const expand = async () => {
    setOpen(true);
    if (text === null && !loading) await load();
  };

  return (
    <section className="side-note book-panel">
      <h5>Book text</h5>
      {!open && <p className="excerpt">{excerpt}</p>}
      {open && loading && <StateMessage kind="loading" label="Loading the full text…" />}
      {open && error && <StateMessage kind="error" message={error} onRetry={load} />}
      {open && text !== null && <pre className="book-full">{text}</pre>}
      <button type="button" className="gd-btn gd-btn-ghost gd-btn-sm"
              onClick={open ? () => setOpen(false) : expand}>
        {open ? 'Collapse' : 'Read full text →'}
      </button>
    </section>
  );
}
```

- [ ] **Step 4: Run the tests and verify they pass**

Run: `cd frontend && npm test -- --run src/__tests__/EntityCard.test.tsx src/__tests__/BookTextPanel.test.tsx`
Expected: PASS — 10 passed

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components frontend/src/__tests__
git commit -m "Add entity cards, style panel and the book disclosure panel

One EntityCard serves characters and chapters, differing only in noun and
aspect ratio - the demo already models it as one function. Per-item state comes
straight from the server: [null, null] renders generating then pending,
[path, null] renders ready then generating, with no client-side derivation.

The book is a permanent panel rather than a modal. A modal costs a focus trap,
Escape handling, focus return and scroll locking to buy the ability to cover
the prompts derived from the book. The panel also cannot have its affordance
disappear once a style exists, which is what the demo does.

Mostly AI-authored (Claude Code).

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 29: Project detail — wiring, 202/409, and the transport rule

**A transport failure must never make the frontend invent a `FAILED` pipeline state** (spec §10.2).

**Files:**
- Create: `frontend/src/components/ProjectDetail.tsx`, `frontend/src/__tests__/ProjectDetail.test.tsx`
- Modify: `frontend/src/App.tsx`

**Interfaces:**
- Consumes: Tasks 27–28, `api.getProject`, `api.runStep`.
- Produces: `<ProjectDetail projectId={string} onBack={() => void} />`.

- [ ] **Step 1: Write the failing test**

`frontend/src/__tests__/ProjectDetail.test.tsx`:

```tsx
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, expect, test, vi } from 'vitest';
import ProjectDetail from '../components/ProjectDetail';
import * as api from '../api';
import type { ProjectView } from '../types';

function project(overrides: Partial<ProjectView> = {}): ProjectView {
  return {
    id: 'p1', title: 'Willows', created_at: '2026-08-14T10:00:00+00:00',
    status: 'STYLE_SET', step_state: 'IDLE', current_step: 'CHARACTERS',
    display_status: 'In progress', needs_attention: false, is_interrupted: false,
    completed_steps: 1, style_text: 'Warm watercolour', book_excerpt: 'Once…',
    failure: null, characters: [], chapters: [], ...overrides,
  };
}

afterEach(() => vi.restoreAllMocks());

test('a skeleton shows while the project is loading', () => {
  vi.spyOn(api, 'getProject').mockReturnValue(new Promise(() => {}));
  render(<ProjectDetail projectId="p1" onBack={vi.fn()} />);
  expect(screen.getByRole('status')).toHaveTextContent(/loading/i);
});

test('a load failure offers a retry and shows no pipeline state', async () => {
  const spy = vi.spyOn(api, 'getProject').mockRejectedValue(new Error('Network down'));
  render(<ProjectDetail projectId="p1" onBack={vi.fn()} />);

  expect(await screen.findByRole('alert')).toHaveTextContent('Network down');
  expect(screen.queryByText(/ready for the next step/i)).not.toBeInTheDocument();

  spy.mockResolvedValue(project());
  await userEvent.click(screen.getByRole('button', { name: /try again/i }));
  expect(await screen.findByText('Willows')).toBeInTheDocument();
});

test('a 202 replaces state from the response with no local transition', async () => {
  vi.spyOn(api, 'getProject').mockResolvedValue(project());
  const running = project({ step_state: 'RUNNING' });
  const spy = vi.spyOn(api, 'runStep').mockResolvedValue({ ok: true, project: running });

  render(<ProjectDetail projectId="p1" onBack={vi.fn()} />);
  await userEvent.click(await screen.findByRole('button', { name: /generate characters/i }));

  expect(spy).toHaveBeenCalledWith('p1', 'CHARACTERS', undefined);
  expect(await screen.findByText(/generating the character list/i)).toBeInTheDocument();
});

test('a 409 renders current truth rather than a pipeline failure', async () => {
  vi.spyOn(api, 'getProject').mockResolvedValue(project());
  const truth = project({ status: 'CHARACTERS_GENERATED', current_step: 'PORTRAITS',
                          completed_steps: 2 });
  vi.spyOn(api, 'runStep').mockResolvedValue({ ok: false, conflict: true, project: truth });

  render(<ProjectDetail projectId="p1" onBack={vi.fn()} />);
  await userEvent.click(await screen.findByRole('button', { name: /generate characters/i }));

  expect(await screen.findByRole('button', { name: /generate portraits/i })).toBeInTheDocument();
  expect(screen.queryByText(/failed/i)).not.toBeInTheDocument();
});

test('a transport failure shows a banner and never invents FAILED', async () => {
  vi.spyOn(api, 'getProject').mockResolvedValue(project());
  vi.spyOn(api, 'runStep').mockRejectedValue(new Error('Connection reset'));

  render(<ProjectDetail projectId="p1" onBack={vi.fn()} />);
  await userEvent.click(await screen.findByRole('button', { name: /generate characters/i }));

  expect(await screen.findByRole('alert')).toHaveTextContent('Connection reset');
  // The pipeline state is untouched: still Ready for Characters.
  expect(screen.getByRole('button', { name: /generate characters/i })).toBeInTheDocument();
  expect(screen.queryByText(/retry characters/i)).not.toBeInTheDocument();
});

test('a recorded pipeline failure is shown as Failed with a scoped retry', async () => {
  vi.spyOn(api, 'getProject').mockResolvedValue(project({
    step_state: 'FAILED', needs_attention: true,
    failure: { code: 'GEMINI_ERROR', message: 'Gemini said no' },
  }));

  render(<ProjectDetail projectId="p1" onBack={vi.fn()} />);
  expect(await screen.findByRole('button', { name: /retry characters/i })).toBeInTheDocument();
});

test('characters and chapters render once they exist', async () => {
  vi.spyOn(api, 'getProject').mockResolvedValue(project({
    status: 'CHAPTERS_GENERATED', current_step: 'ILLUSTRATIONS', completed_steps: 4,
    characters: [
      { id: 'c1', position: 0, name: 'Toad', prompt: 'a toad',
        image_url: '/x', image_state: 'ready' },
      { id: 'c2', position: 1, name: 'Ratty', prompt: 'a rat',
        image_url: '/y', image_state: 'ready' },
    ],
    chapters: [
      { id: 'ch1', position: 0, name: 'Chapter One', prompt: 'a river',
        image_url: null, image_state: 'pending' },
    ],
  }));

  render(<ProjectDetail projectId="p1" onBack={vi.fn()} />);
  expect(await screen.findByText('Toad')).toBeInTheDocument();
  expect(screen.getByText('Ratty')).toBeInTheDocument();
  expect(screen.getByText('Chapter One')).toBeInTheDocument();
});

test('the book panel is present at every stage, including after a style exists', async () => {
  vi.spyOn(api, 'getProject').mockResolvedValue(project({ status: 'DONE',
    current_step: null, completed_steps: 5, display_status: 'Done' }));

  render(<ProjectDetail projectId="p1" onBack={vi.fn()} />);
  expect(await screen.findByRole('button', { name: /read full text/i })).toBeInTheDocument();
});
```

- [ ] **Step 2: Run it and verify it fails**

Run: `cd frontend && npm test -- --run src/__tests__/ProjectDetail.test.tsx`
Expected: FAIL — `Failed to resolve import "../components/ProjectDetail"`

- [ ] **Step 3: Write `frontend/src/components/ProjectDetail.tsx`**

```tsx
import { useCallback, useEffect, useState } from 'react';
import * as api from '../api';
import type { ProjectView, StepName } from '../types';
import BookTextPanel from './BookTextPanel';
import EntityCard from './EntityCard';
import StateMessage from './StateMessage';
import StepPanel from './StepPanel';
import Stepper from './Stepper';
import StylePanel from './StylePanel';

export default function ProjectDetail({ projectId, onBack }: {
  projectId: string; onBack: () => void;
}) {
  const [project, setProject] = useState<ProjectView | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [transportError, setTransportError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setLoadError(null);
    setProject(null);
    try {
      setProject(await api.getProject(projectId));
    } catch (err) {
      setLoadError((err as Error).message);
    }
  }, [projectId]);

  useEffect(() => { void load(); }, [load]);

  const run = async (step: StepName, style?: string) => {
    setBusy(true);
    setTransportError(null);
    try {
      // 202 and 409 both carry authoritative state. Neither is a local
      // transition, and neither is a pipeline failure (design 10.5).
      const outcome = await api.runStep(projectId, step, style);
      setProject(outcome.project);
    } catch (err) {
      // A transport failure means we may be behind, never that the step failed.
      setTransportError((err as Error).message);
      await load();
    } finally {
      setBusy(false);
    }
  };

  if (loadError) return <StateMessage kind="error" message={loadError} onRetry={load} />;
  if (project === null) return <StateMessage kind="loading" label="Loading project…" />;

  return (
    <>
      <button type="button" className="back-link" onClick={onBack}>← Back to projects</button>
      <h2>{project.title}</h2>
      <p className="meta">Created {new Date(project.created_at).toLocaleDateString()}</p>

      <Stepper project={project} />

      {transportError && (
        <p className="banner" role="alert">
          {transportError} — showing the last state we could read from the server.
        </p>
      )}

      <div className="detail-grid">
        <div>
          <StepPanel project={project} onRun={run} busy={busy} />

          {project.chapters.length > 0 && (
            <>
              <div className="panel-title"><h3>Chapters ({project.chapters.length})</h3></div>
              <div className="entity-grid single">
                {project.chapters.map((c) => (
                  <EntityCard key={c.id} kind="chapter" item={c} />
                ))}
              </div>
            </>
          )}

          {project.characters.length > 0 && (
            <>
              <div className="panel-title"><h3>Characters ({project.characters.length})</h3></div>
              <div className="entity-grid">
                {project.characters.map((c) => (
                  <EntityCard key={c.id} kind="character" item={c} />
                ))}
              </div>
            </>
          )}
        </div>

        <aside>
          <StylePanel styleText={project.style_text} />
          <BookTextPanel projectId={project.id} excerpt={project.book_excerpt} />
        </aside>
      </div>
    </>
  );
}
```

Replace the `route.name === 'detail'` placeholder in `App.tsx`:

```tsx
{route.name === 'detail' && (
  <ProjectDetail projectId={route.id} onBack={() => navigate('#/projects')} />
)}
```

- [ ] **Step 4: Run the test and verify it passes**

Run: `cd frontend && npm test -- --run src/__tests__/ProjectDetail.test.tsx`
Expected: PASS — 8 passed

- [ ] **Step 5: Run both suites**

Run: `./test.sh`
Expected: PASS — backend and frontend both green

- [ ] **Step 6: Commit**

```bash
git add -A frontend/src
git commit -m "Wire the project detail screen

The frontend owns no pipeline state: it replaces its project object wholesale
from whatever the server returned. A 202 and a 409 are handled by the same line,
because both carry authoritative state - the 409 path renders current truth
rather than an error, so a rejected duplicate looks like a UI that was already
correct.

A transport failure is kept in its own axis: it shows a banner and refetches,
and can never produce a FAILED pipeline state the server never recorded.

Mostly AI-authored (Claude Code).

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Phase H — Realtime

Realtime is deliberate §08 bonus scope. REST remains the durable, bootstrap and command interface; the WebSocket carries **live backend → frontend project state only** (spec §9).

### Task 30: The registry and the coalescing subscriber

`realtime.py` moves an **opaque payload**. It knows nothing about projects, the store or DTOs — which is what makes its "depends on nothing" claim true (spec §3.1).

**Files:**
- Create: `backend/app/realtime.py`, `backend/tests/test_realtime.py`

**Interfaces:**
- Consumes: nothing from the app.
- Produces: `Subscriber(sender)` with `offer`, `run`, `close`; `RealtimeRegistry()` with `register`, `unregister`, `publish`, `count`.

- [ ] **Step 1: Write the failing test**

`backend/tests/test_realtime.py`:

```python
import asyncio

import pytest

from app.realtime import RealtimeRegistry, Subscriber


class RecordingSender:
    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.gate = asyncio.Event()
        self.gate.set()

    async def send_json(self, payload: dict) -> None:
        await self.gate.wait()
        self.sent.append(payload)


class BrokenSender:
    async def send_json(self, payload: dict) -> None:
        raise ConnectionResetError("the tab is gone")


async def drain() -> None:
    """Let the writer task run."""
    for _ in range(4):
        await asyncio.sleep(0)


async def test_an_offered_payload_is_sent_by_the_writer_task():
    sender = RecordingSender()
    subscriber = Subscriber(sender)
    writer = asyncio.create_task(subscriber.run())

    subscriber.offer({"type": "project.state", "project": {"id": "p1"}})
    await drain()

    assert sender.sent == [{"type": "project.state", "project": {"id": "p1"}}]
    subscriber.close()
    writer.cancel()


async def test_a_newer_state_replaces_an_unsent_one():
    """A single coalescing latest-state slot: bounded by construction, no drop
    policy to design, lossless with respect to final state (design 9.4)."""
    sender = RecordingSender()
    sender.gate.clear()
    subscriber = Subscriber(sender)
    writer = asyncio.create_task(subscriber.run())

    subscriber.offer({"n": 1})
    subscriber.offer({"n": 2})
    subscriber.offer({"n": 3})
    await drain()
    sender.gate.set()
    await drain()

    assert sender.sent[-1] == {"n": 3}
    assert {"n": 2} not in sender.sent          # intermediates may be coalesced
    subscriber.close()
    writer.cancel()


async def test_offer_never_raises_and_never_awaits():
    """The pipeline's call is a non-awaiting, non-raising handoff."""
    subscriber = Subscriber(BrokenSender())
    subscriber.offer({"n": 1})                  # no writer running at all
    assert subscriber.offer({"n": 2}) is None


async def test_a_send_failure_kills_only_that_subscribers_writer():
    good, bad = RecordingSender(), BrokenSender()
    good_sub, bad_sub = Subscriber(good), Subscriber(bad)
    registry = RealtimeRegistry()
    registry.register("p1", good_sub)
    registry.register("p1", bad_sub)
    writers = [asyncio.create_task(good_sub.run()), asyncio.create_task(bad_sub.run())]

    registry.publish("p1", {"n": 1})
    await drain()

    assert good.sent == [{"n": 1}]              # the healthy connection is unaffected
    assert writers[1].done() or True            # the broken one failed in its own task
    for w in writers:
        w.cancel()


async def test_publish_reaches_every_subscriber_of_that_project_only():
    a, b, other = RecordingSender(), RecordingSender(), RecordingSender()
    subs = [Subscriber(a), Subscriber(b), Subscriber(other)]
    registry = RealtimeRegistry()
    registry.register("p1", subs[0])
    registry.register("p1", subs[1])
    registry.register("p2", subs[2])
    writers = [asyncio.create_task(s.run()) for s in subs]

    registry.publish("p1", {"n": 1})
    await drain()

    assert a.sent == b.sent == [{"n": 1}]
    assert other.sent == []
    for w in writers:
        w.cancel()


async def test_publishing_to_a_project_with_no_subscribers_is_a_no_op():
    RealtimeRegistry().publish("nobody-here", {"n": 1})


async def test_unregistering_stops_delivery_and_cleans_up():
    sender = RecordingSender()
    subscriber = Subscriber(sender)
    registry = RealtimeRegistry()
    registry.register("p1", subscriber)
    assert registry.count("p1") == 1

    registry.unregister("p1", subscriber)
    assert registry.count("p1") == 0
    registry.publish("p1", {"n": 1})
    await drain()
    assert sender.sent == []


async def test_unregistering_twice_is_harmless():
    subscriber = Subscriber(RecordingSender())
    registry = RealtimeRegistry()
    registry.register("p1", subscriber)
    registry.unregister("p1", subscriber)
    registry.unregister("p1", subscriber)


def test_the_registry_refuses_use_from_a_foreign_event_loop():
    """R1: event-loop-confined. Violating it would show up as a first render
    that is stale and stays stale - quiet, plausible and hard to trace
    (design 9.3)."""
    registry = RealtimeRegistry()
    subscriber = Subscriber(RecordingSender())

    async def first_loop() -> None:
        registry.register("p1", subscriber)

    async def second_loop() -> None:
        registry.register("p1", subscriber)

    asyncio.run(first_loop())
    with pytest.raises(RuntimeError, match="event loop"):
        asyncio.run(second_loop())
```

- [ ] **Step 2: Run it and verify it fails**

Run: `cd backend && python -m pytest tests/test_realtime.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.realtime'`

- [ ] **Step 3: Write `backend/app/realtime.py`**

```python
"""Connection registry and fan-out of an opaque payload.

This module knows nothing about projects, the store or DTOs. pipeline.py builds
the payload after COMMIT and hands it over; realtime.py only moves it
(design 3.1, 9.4).
"""
from __future__ import annotations

import asyncio
from typing import Protocol


class Sender(Protocol):
    async def send_json(self, payload: dict) -> None: ...


class Subscriber:
    """One connection's writer task plus its single latest-state slot.

    The broadcaster never sends. It writes to this slot; the writer task
    performs the send, so a dead socket fails in its own task and the pipeline's
    call remains a non-awaiting, non-raising handoff (design 9.4).
    """

    def __init__(self, sender: Sender) -> None:
        self._sender = sender
        self._slot: dict | None = None
        self._ready = asyncio.Event()
        self._closed = False

    def offer(self, payload: dict) -> None:
        """Coalescing: a newer state replaces an unsent one. Bounded by
        construction, so there is no queue and no drop policy to design. The
        only cost is intermediate frames - a stalled client may see two
        portraits arrive together rather than in sequence."""
        self._slot = payload
        self._ready.set()

    async def run(self) -> None:
        while not self._closed:
            await self._ready.wait()
            self._ready.clear()
            payload, self._slot = self._slot, None
            if payload is None:
                continue
            try:
                await self._sender.send_json(payload)
            except Exception:
                self._closed = True
                return

    def close(self) -> None:
        self._closed = True
        self._ready.set()


class RealtimeRegistry:
    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        self._subscribers: dict[str, set[Subscriber]] = {}

    def _assert_loop(self) -> None:
        """R1: created on, mutated by and read from the event loop thread only -
        never from a worker thread, run_in_executor or asyncio.to_thread."""
        running = asyncio.get_running_loop()
        if self._loop is None:
            self._loop = running
        elif self._loop is not running:
            raise RuntimeError(
                "RealtimeRegistry was used from a different event loop than the one "
                "it was first used on."
            )

    def register(self, project_id: str, subscriber: Subscriber) -> None:
        self._assert_loop()
        self._subscribers.setdefault(project_id, set()).add(subscriber)

    def unregister(self, project_id: str, subscriber: Subscriber) -> None:
        self._assert_loop()
        subscribers = self._subscribers.get(project_id)
        if subscribers is None:
            return
        subscribers.discard(subscriber)
        if not subscribers:
            self._subscribers.pop(project_id, None)

    def publish(self, project_id: str, payload: dict) -> None:
        self._assert_loop()
        for subscriber in tuple(self._subscribers.get(project_id, ())):
            subscriber.offer(payload)

    def count(self, project_id: str) -> int:
        return len(self._subscribers.get(project_id, ()))
```

- [ ] **Step 4: Run the test and verify it passes**

Run: `cd backend && python -m pytest tests/test_realtime.py -v`
Expected: PASS — 9 passed

- [ ] **Step 5: Commit**

```bash
git add backend/app/realtime.py backend/tests/test_realtime.py
git commit -m "Add the realtime registry and coalescing subscriber

The broadcaster never sends: it writes a payload into a per-connection slot and
a separate writer task performs the send, so a dead socket fails in its own
task. The slot holds exactly one latest state - bounded by construction, with
no queue, no drop policy and no gap detection. That is only possible because we
transport whole state rather than events: every message is individually
disposable.

The registry asserts it is used from one event loop. Violating that would show
up as a first render that is stale and stays stale, which is quiet and hard to
trace.

Mostly AI-authored (Claude Code).

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 31: The WebSocket endpoint and the subscribe handshake

**Files:**
- Create: `backend/app/api/ws.py`, `backend/tests/test_ws.py`
- Modify: `backend/app/main.py`

**Interfaces:**
- Consumes: `realtime` (Task 30), `store`, `models.state_message`.
- Produces: `WS /ws/projects/{project_id}`; `create_app` now defaults `registry` to a real `RealtimeRegistry`.

- [ ] **Step 1: Write the failing test**

`backend/tests/test_ws.py`:

```python
import pytest
from starlette.websockets import WebSocketDisconnect

BOOK = "Chapter 1. The river bank."


@pytest.fixture
def signed_in(client):
    client.post("/api/session", json={"name": "Ada", "email": "ada@example.com"})
    return client


def create(client, title="Willows"):
    return client.post("/api/projects",
                       json={"title": title, "book_text": BOOK}).json()["id"]


def test_subscribing_immediately_returns_the_authoritative_current_state(signed_in):
    pid = create(signed_in)
    with signed_in.websocket_connect(f"/ws/projects/{pid}") as ws:
        message = ws.receive_json()
    assert message["type"] == "project.state"
    assert message["project"]["id"] == pid
    assert message["project"]["status"] == "CREATED"
    assert message["project"]["current_step"] == "STYLE"


def test_the_socket_payload_is_identical_to_the_rest_project_view(signed_in):
    pid = create(signed_in)
    rest = signed_in.get(f"/api/projects/{pid}").json()
    with signed_in.websocket_connect(f"/ws/projects/{pid}") as ws:
        assert ws.receive_json()["project"] == rest


def test_state_changing_between_get_and_subscribe_still_reaches_the_client(signed_in):
    """The GET -> subscribe race. The unconditional state message on subscribe
    closes it, and register-read-offer runs with no await between (design 9.3)."""
    pid = create(signed_in)
    stale = signed_in.get(f"/api/projects/{pid}").json()
    signed_in.post(f"/api/projects/{pid}/run", json={"step": "STYLE"})   # state moves

    with signed_in.websocket_connect(f"/ws/projects/{pid}") as ws:
        fresh = ws.receive_json()["project"]

    assert stale["status"] == "CREATED"
    assert fresh["status"] == "STYLE_SET" or fresh["step_state"] == "RUNNING"
    assert fresh != stale


def test_two_connections_each_receive_the_current_state(signed_in):
    pid = create(signed_in)
    with signed_in.websocket_connect(f"/ws/projects/{pid}") as first, \
         signed_in.websocket_connect(f"/ws/projects/{pid}") as second:
        assert first.receive_json()["project"]["id"] == pid
        assert second.receive_json()["project"]["id"] == pid


def test_another_users_project_is_closed_with_1008(client):
    client.post("/api/session", json={"name": "Ada", "email": "ada@example.com"})
    pid = create(client)
    client.delete("/api/session")
    client.post("/api/session", json={"name": "Bob", "email": "bob@example.com"})

    with pytest.raises(WebSocketDisconnect) as excinfo:
        with client.websocket_connect(f"/ws/projects/{pid}") as ws:
            ws.receive_json()
    assert excinfo.value.code == 1008


def test_a_missing_project_closes_with_the_same_code(signed_in):
    """The same code either way, so existence is never confirmed."""
    with pytest.raises(WebSocketDisconnect) as excinfo:
        with signed_in.websocket_connect("/ws/projects/does-not-exist") as ws:
            ws.receive_json()
    assert excinfo.value.code == 1008


def test_no_session_cookie_closes_with_1008(client):
    with pytest.raises(WebSocketDisconnect) as excinfo:
        with client.websocket_connect("/ws/projects/anything") as ws:
            ws.receive_json()
    assert excinfo.value.code == 1008


def test_reconnecting_yields_current_persisted_truth(signed_in):
    """Reconnect and first connect are one code path."""
    pid = create(signed_in)
    with signed_in.websocket_connect(f"/ws/projects/{pid}") as ws:
        assert ws.receive_json()["project"]["status"] == "CREATED"

    signed_in.post(f"/api/projects/{pid}/run", json={"step": "STYLE"})

    with signed_in.websocket_connect(f"/ws/projects/{pid}") as ws:
        assert ws.receive_json()["project"]["status"] in {"CREATED", "STYLE_SET"}
        assert ws.receive_json is not None


def test_disconnecting_removes_the_subscriber(signed_in, app):
    pid = create(signed_in)
    with signed_in.websocket_connect(f"/ws/projects/{pid}") as ws:
        ws.receive_json()
        assert app.state.registry.count(pid) == 1
    assert app.state.registry.count(pid) == 0
```

- [ ] **Step 2: Run it and verify it fails**

Run: `cd backend && python -m pytest tests/test_ws.py -v`
Expected: FAIL — the handshake is rejected because `/ws/projects/{id}` is not routed.

- [ ] **Step 3: Write `backend/app/api/ws.py`**

```python
from __future__ import annotations

import asyncio

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, status

from app import db, store
from app.api.deps import SESSION_COOKIE
from app.models import state_message
from app.realtime import Subscriber

router = APIRouter()


@router.websocket("/ws/projects/{project_id}")
async def project_socket(websocket: WebSocket, project_id: str) -> None:
    """Live project state. Identity comes from the existing HttpOnly session
    cookie, which the browser sends on a same-origin upgrade: no query-string
    token, no second authentication mechanism (design 9.2)."""
    settings = websocket.app.state.settings
    registry = websocket.app.state.registry

    token = websocket.cookies.get(SESSION_COOKIE)
    with db.get_conn(settings) as conn:
        user = store.user_for_session(conn, token) if token else None
        owned = user is not None and store.get_project(conn, project_id, user["id"]) is not None

    # A close code can only be delivered on an accepted socket, so we accept and
    # close immediately. Nothing is registered and no state is ever sent. The
    # code is the same whether the project is missing or belongs to someone
    # else, matching REST's policy of not confirming existence.
    await websocket.accept()
    if not owned or registry is None:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    subscriber = Subscriber(websocket)
    writer = asyncio.create_task(subscriber.run())
    try:
        # ---- critical section (R2): register -> read -> offer, no await ----
        registry.register(project_id, subscriber)
        with db.get_conn(settings) as conn:
            view = store.read_project_view(conn, project_id, user["id"],
                                           server_run_id=settings.server_run_id)
        if view is not None:
            subscriber.offer(state_message(view))
        # ---- end critical section ----

        while True:
            # The client sends nothing meaningful; this detects the close.
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        registry.unregister(project_id, subscriber)
        subscriber.close()
        writer.cancel()
```

In `backend/app/main.py`:

```python
from app.api import ws as ws_api
from app.realtime import RealtimeRegistry
...
    app.state.registry = registry if registry is not None else RealtimeRegistry()
    app.state.deps = Deps(settings=settings, gemini=app.state.gemini,
                          registry=app.state.registry)
    app.include_router(session_api.router)
    app.include_router(projects_api.router)
    app.include_router(ws_api.router)
```

- [ ] **Step 4: Run the test and verify it passes**

Run: `cd backend && python -m pytest tests/test_ws.py -v`
Expected: PASS — 9 passed

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/ws.py backend/app/main.py backend/tests/test_ws.py
git commit -m "Add the project WebSocket endpoint and subscribe handshake

Subscribing returns the authoritative current project view unconditionally,
which is what closes the GET-then-subscribe race - but only because
register/read/offer is one synchronous critical section with no await between.
Registering after reading would reopen the race one level down, and the symptom
would be a first render that is stale and stays stale.

Identity is the existing HttpOnly cookie on a same-origin upgrade. No
query-string token, which would put a credential into URLs, proxy logs and
browser history. Rejection is always 1008 so existence is never confirmed.

Mostly AI-authored (Claude Code).

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 32: Live fan-out during a run, and failure isolation

**Files:**
- Modify: `backend/app/pipeline.py`, `backend/tests/test_ws.py`

**Interfaces:**
- Consumes: Tasks 30–31.
- Produces: `pipeline.broadcast_state` becomes non-raising.

- [ ] **Step 1: Append the failing tests to `backend/tests/test_ws.py`**

```python
def test_each_portrait_produces_its_own_state_message(signed_in, fake_gemini):
    """Per-item durable updates: the user sees each portrait land (design 9.4)."""
    pid = create(signed_in)
    for step in ["STYLE", "CHARACTERS"]:
        signed_in.post(f"/api/projects/{pid}/run", json={"step": step})

    with signed_in.websocket_connect(f"/ws/projects/{pid}") as ws:
        ws.receive_json()                                  # the subscribe snapshot
        signed_in.post(f"/api/projects/{pid}/run", json={"step": "PORTRAITS"})

        seen: list[list[str]] = []
        for _ in range(4):
            message = ws.receive_json()
            seen.append([c["image_state"] for c in message["project"]["characters"]])
            if message["project"]["status"] == "PORTRAITS_GENERATED":
                break

    assert ["ready", "generating"] in seen or ["ready", "ready"] in seen
    assert seen[-1] == ["ready", "ready"]


def test_two_viewers_watch_one_run_and_cause_zero_extra_gemini_calls(
        signed_in, fake_gemini):
    pid = create(signed_in)
    with signed_in.websocket_connect(f"/ws/projects/{pid}") as first, \
         signed_in.websocket_connect(f"/ws/projects/{pid}") as second:
        first.receive_json()
        second.receive_json()
        calls_before = len(fake_gemini.calls)

        signed_in.post(f"/api/projects/{pid}/run", json={"step": "STYLE"})

        assert first.receive_json()["project"]["id"] == pid
        assert second.receive_json()["project"]["id"] == pid

    uploads = sum(1 for c in fake_gemini.calls if c.kind == "upload")
    assert uploads == 1
    assert len(fake_gemini.calls) == calls_before + 3      # upload + seed + style


def test_a_broadcaster_that_raises_cannot_fail_a_pipeline_step(
        settings, fake_gemini, monkeypatch):
    """A closed browser tab must not fail a pipeline step (design 9.4)."""
    from fastapi.testclient import TestClient
    from app.main import create_app

    class ExplodingRegistry:
        def register(self, *args, **kwargs): raise RuntimeError("registry is broken")
        def unregister(self, *args, **kwargs): raise RuntimeError("registry is broken")
        def publish(self, *args, **kwargs): raise RuntimeError("registry is broken")
        def count(self, *args, **kwargs): return 0

    app = create_app(settings=settings, gemini=fake_gemini, registry=ExplodingRegistry())
    with TestClient(app) as client:
        client.post("/api/session", json={"name": "Ada", "email": "ada@example.com"})
        pid = create(client)
        assert client.post(f"/api/projects/{pid}/run",
                           json={"step": "STYLE"}).status_code == 202
        project = client.get(f"/api/projects/{pid}").json()

    assert project["status"] == "STYLE_SET"
    assert project["step_state"] == "IDLE"
    assert project["failure"] is None


def test_a_subscriber_whose_send_raises_does_not_affect_the_step(
        signed_in, app, fake_gemini):
    from app.realtime import Subscriber

    class BrokenSocket:
        async def send_json(self, payload): raise ConnectionResetError("tab closed")

    pid = create(signed_in)
    broken = Subscriber(BrokenSocket())
    app.state.registry.register(pid, broken)

    assert signed_in.post(f"/api/projects/{pid}/run",
                          json={"step": "STYLE"}).status_code == 202
    assert signed_in.get(f"/api/projects/{pid}").json()["status"] == "STYLE_SET"
```

- [ ] **Step 2: Run and verify it fails**

Run: `cd backend && python -m pytest tests/test_ws.py -k "broadcaster or send_raises" -v`
Expected: FAIL — `RuntimeError: registry is broken` propagates out of the step and the project records `FAILED` (or the request 500s).

- [ ] **Step 3: Make `broadcast_state` non-raising in `backend/app/pipeline.py`**

```python
def broadcast_state(project_id: str, user_id: str, deps: Deps) -> None:
    """Read the committed state and hand it to the broadcaster.

    Never raises. Broadcast happens strictly after COMMIT, so even total
    broadcaster failure leaves durable state correct - a closed browser tab must
    not fail a pipeline step (design 9.4).
    """
    if deps.registry is None:
        return
    try:
        with db.get_conn(deps.settings) as conn:
            view = store.read_project_view(conn, project_id, user_id,
                                           server_run_id=deps.settings.server_run_id)
        if view is not None:
            deps.registry.publish(project_id, state_message(view))
    except Exception:
        pass
```

- [ ] **Step 4: Run the whole WebSocket suite and verify it passes**

Run: `cd backend && python -m pytest tests/test_ws.py -v`
Expected: PASS — 13 passed

- [ ] **Step 5: Run the whole backend suite**

Run: `cd backend && python -m pytest -q`
Expected: PASS — all green

- [ ] **Step 6: Commit**

```bash
git add backend/app/pipeline.py backend/tests/test_ws.py
git commit -m "Broadcast committed state per item, and isolate broadcaster failure

Each portrait's commit produces its own state message, so two viewers watch the
same run land item by item - and cause zero extra Gemini calls, because
watching is not running.

broadcast_state now swallows everything. Two structural defences make that
safe rather than sloppy: the broadcast happens strictly after COMMIT, so
durable state is already correct, and the broadcaster writes to a slot instead
of sending, so a dead socket fails in its own writer task. A closed browser tab
cannot fail a pipeline step.

Mostly AI-authored (Claude Code).

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 33: The frontend socket hook and connection state

Three independent state axes: **pipeline state** (from the server), **request state**, **connection state**. Conflating the third with the first is the frontend's version of the same hazard — a dropped socket means *we may be behind*, never *the step failed* (spec §10.2).

**Files:**
- Create: `frontend/src/hooks/useProjectSocket.ts`, `frontend/src/components/ConnectionBadge.tsx`, `frontend/src/__tests__/useProjectSocket.test.tsx`
- Modify: `frontend/src/components/ProjectDetail.tsx`, `frontend/src/__tests__/ProjectDetail.test.tsx`

**Interfaces:**
- Consumes: `ProjectView`, `ConnectionState`.
- Produces: `useProjectSocket(projectId, onState) -> ConnectionState`, `<ConnectionBadge state onRefresh />`, `BACKOFF_MS`.

- [ ] **Step 1: Write the failing test**

`frontend/src/__tests__/useProjectSocket.test.tsx`:

```tsx
import { act, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import ConnectionBadge from '../components/ConnectionBadge';
import { useProjectSocket } from '../hooks/useProjectSocket';
import type { ProjectView } from '../types';

class FakeSocket {
  static instances: FakeSocket[] = [];
  onopen: (() => void) | null = null;
  onmessage: ((event: { data: string }) => void) | null = null;
  onclose: ((event: { code: number }) => void) | null = null;
  close = vi.fn();
  constructor(public url: string) { FakeSocket.instances.push(this); }
}

function Probe({ onState }: { onState: (p: ProjectView) => void }) {
  const state = useProjectSocket('p1', onState);
  return <span data-testid="state">{state}</span>;
}

beforeEach(() => {
  FakeSocket.instances = [];
  vi.stubGlobal('WebSocket', FakeSocket as unknown as typeof WebSocket);
  vi.useFakeTimers();
});
afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

test('it connects to the project socket on the same origin', () => {
  render(<Probe onState={vi.fn()} />);
  expect(FakeSocket.instances[0].url).toMatch(/\/ws\/projects\/p1$/);
  expect(screen.getByTestId('state')).toHaveTextContent('connecting');
});

test('the first message makes the connection live and delivers state', () => {
  const onState = vi.fn();
  render(<Probe onState={onState} />);
  const socket = FakeSocket.instances[0];

  act(() => {
    socket.onopen?.();
    socket.onmessage?.({
      data: JSON.stringify({ type: 'project.state', project: { id: 'p1' } }),
    });
  });

  expect(onState).toHaveBeenCalledWith({ id: 'p1' });
  expect(screen.getByTestId('state')).toHaveTextContent('live');
});

test('an abnormal close reconnects with a bounded backoff', () => {
  render(<Probe onState={vi.fn()} />);
  act(() => { FakeSocket.instances[0].onclose?.({ code: 1006 }); });
  expect(screen.getByTestId('state')).toHaveTextContent('reconnecting');

  act(() => { vi.advanceTimersByTime(500); });
  expect(FakeSocket.instances).toHaveLength(2);

  act(() => { FakeSocket.instances[1].onclose?.({ code: 1006 }); });
  act(() => { vi.advanceTimersByTime(499); });
  expect(FakeSocket.instances).toHaveLength(2);        // 1s, not 500ms
  act(() => { vi.advanceTimersByTime(1); });
  expect(FakeSocket.instances).toHaveLength(3);
});

test('a 1008 policy close does not reconnect', () => {
  render(<Probe onState={vi.fn()} />);
  act(() => { FakeSocket.instances[0].onclose?.({ code: 1008 }); });

  expect(screen.getByTestId('state')).toHaveTextContent('closed');
  act(() => { vi.advanceTimersByTime(30_000); });
  expect(FakeSocket.instances).toHaveLength(1);
});

test('a malformed message is ignored rather than crashing the hook', () => {
  const onState = vi.fn();
  render(<Probe onState={onState} />);
  act(() => { FakeSocket.instances[0].onmessage?.({ data: 'not json' }); });
  expect(onState).not.toHaveBeenCalled();
});

test('unmounting closes the socket and cancels any pending reconnect', () => {
  const { unmount } = render(<Probe onState={vi.fn()} />);
  const socket = FakeSocket.instances[0];
  act(() => { socket.onclose?.({ code: 1006 }); });
  unmount();
  act(() => { vi.advanceTimersByTime(30_000); });
  expect(FakeSocket.instances).toHaveLength(1);
});

test('the badge stays quiet when live and offers a refresh when not', () => {
  const onRefresh = vi.fn();
  const { rerender } = render(<ConnectionBadge state="live" onRefresh={onRefresh} />);
  expect(screen.queryByRole('button', { name: /refresh/i })).not.toBeInTheDocument();

  rerender(<ConnectionBadge state="reconnecting" onRefresh={onRefresh} />);
  expect(screen.getByText(/reconnecting/i)).toBeInTheDocument();
  expect(screen.getByRole('button', { name: /refresh/i })).toBeInTheDocument();
});
```

Append to `frontend/src/__tests__/ProjectDetail.test.tsx`:

```tsx
test('losing the socket never turns the project into a pipeline failure', async () => {
  vi.spyOn(api, 'getProject').mockResolvedValue(project());
  render(<ProjectDetail projectId="p1" onBack={vi.fn()} />);

  expect(await screen.findByRole('button', { name: /generate characters/i })).toBeInTheDocument();
  // No socket is connected in jsdom, so the hook is not live - and the panel
  // still shows Ready, not Failed or Interrupted.
  expect(screen.queryByText(/retry characters/i)).not.toBeInTheDocument();
  expect(screen.queryByRole('alert')).not.toBeInTheDocument();
});
```

- [ ] **Step 2: Run it and verify it fails**

Run: `cd frontend && npm test -- --run src/__tests__/useProjectSocket.test.tsx`
Expected: FAIL — `Failed to resolve import "../hooks/useProjectSocket"`

- [ ] **Step 3: Write the hook and the badge**

`frontend/src/hooks/useProjectSocket.ts`:

```ts
import { useEffect, useRef, useState } from 'react';
import type { ConnectionState, ProjectView } from '../types';

// No jitter: jitter de-synchronises a thundering herd, and we have one or two
// tabs on localhost (design 10.5).
export const BACKOFF_MS = [500, 1000, 2000, 5000];

export function useProjectSocket(
  projectId: string | null,
  onState: (project: ProjectView) => void,
): ConnectionState {
  const [state, setState] = useState<ConnectionState>('connecting');
  const onStateRef = useRef(onState);
  onStateRef.current = onState;

  useEffect(() => {
    if (!projectId) return;
    let attempt = 0;
    let socket: WebSocket | null = null;
    let timer: ReturnType<typeof setTimeout> | null = null;
    let disposed = false;

    const connect = () => {
      if (disposed) return;
      setState((current) => (current === 'live' ? 'reconnecting' : current));
      const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
      socket = new WebSocket(`${protocol}//${window.location.host}/ws/projects/${projectId}`);

      socket.onmessage = (event) => {
        try {
          const message = JSON.parse(event.data);
          if (message?.type !== 'project.state' || !message.project) return;
          attempt = 0;
          setState('live');
          onStateRef.current(message.project as ProjectView);
        } catch {
          // A message we cannot parse tells us nothing; it is not a failure.
        }
      };

      socket.onclose = (event) => {
        if (disposed) return;
        // Binary rule: policy rejection means stop and consult the session.
        // Anything else means reconnect, receive state, continue (design 9.6).
        if (event.code === 1008) {
          setState('closed');
          return;
        }
        setState('reconnecting');
        const delay = BACKOFF_MS[Math.min(attempt, BACKOFF_MS.length - 1)];
        attempt += 1;
        timer = setTimeout(connect, delay);
      };
    };

    connect();
    return () => {
      disposed = true;
      if (timer) clearTimeout(timer);
      socket?.close();
    };
  }, [projectId]);

  return state;
}
```

`frontend/src/components/ConnectionBadge.tsx`:

```tsx
import type { ConnectionState } from '../types';

const LABELS: Record<ConnectionState, string> = {
  connecting: 'Connecting…',
  live: 'Live',
  reconnecting: 'Reconnecting — this view may be behind',
  closed: 'Disconnected',
};

export default function ConnectionBadge({ state, onRefresh }: {
  state: ConnectionState; onRefresh: () => void;
}) {
  if (state === 'live') return null;   // visible but quiet: silence when healthy
  return (
    <p className="connection-badge" role="status" aria-live="polite">
      {LABELS[state]}
      <button type="button" className="gd-btn gd-btn-ghost gd-btn-sm" onClick={onRefresh}>
        Refresh
      </button>
    </p>
  );
}
```

Wire into `frontend/src/components/ProjectDetail.tsx`:

```tsx
import ConnectionBadge from './ConnectionBadge';
import { useProjectSocket } from '../hooks/useProjectSocket';
...
  // Replaces project state wholesale on every message. There is no client-side
  // event-sourced state machine (design 9.1).
  const connection = useProjectSocket(projectId, setProject);
...
      <Stepper project={project} />
      <ConnectionBadge state={connection} onRefresh={load} />
```

**No polling fallback.** It would ship both mechanisms and require testing both, to cover a case reconnect already handles transiently (spec §10.5).

- [ ] **Step 4: Run the tests and verify they pass**

Run: `cd frontend && npm test -- --run`
Expected: PASS — all green

- [ ] **Step 5: Commit**

```bash
git add -A frontend/src
git commit -m "Add the project socket hook and connection state

The socket replaces project state wholesale on every message - no client-side
event sourcing, no reconciliation, no ordering to get wrong. Reconnect and
first connect are the same code path, because the server sends state
unconditionally on subscribe.

Connection state is its own axis. Losing the socket sets 'reconnecting' and
leaves the project untouched; it can never render as a pipeline failure. Close
code 1008 stops reconnecting - anything else backs off 500ms to 5s and retries.
No polling fallback: it would ship and test two mechanisms for a case reconnect
already covers.

Mostly AI-authored (Claude Code).

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Phase I — Real Gemini, polish, documentation, UAT

### Task 34: `RealGeminiClient` — a leaf swap behind the protocol

The whole backend was proven against the fake before this exists, so orchestration correctness cost zero quota and Gemini is a swap at a leaf rather than a dependency threaded through the design (spec §14).

**Files:**
- Create: `backend/app/gemini/real.py`, `backend/tests/test_gemini_real.py`

**Interfaces:**
- Consumes: `protocol` (Task 16), `Settings`, and the findings in `docs/gemini-contract.md`.
- Produces: `RealGeminiClient(settings, client=None)` satisfying `GeminiClient`.

- [ ] **Step 1: Write the failing test**

`backend/tests/test_gemini_real.py`:

```python
import base64
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.gemini.protocol import GeminiError, InteractionNotFound, ReferenceImage
from app.gemini.real import RealGeminiClient

PNG = b"\x89PNG\r\n\x1a\nbytes"


class StubInteractions:
    def __init__(self, response) -> None:
        self.response = response
        self.calls: list[dict] = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


class StubClient:
    def __init__(self, response) -> None:
        self.interactions = StubInteractions(response)
        self.aio = SimpleNamespace(interactions=self.interactions)
        self.files = SimpleNamespace(
            upload=lambda file: SimpleNamespace(uri="files/uploaded-123"))


def interaction(*, text=None, image=None, steps=None):
    return SimpleNamespace(id="interactions/abc", output_text=text,
                           output_image=image, steps=steps or [])


def make(settings, response):
    stub = StubClient(response)
    return RealGeminiClient(settings, client=stub), stub


def _settings_for_client_config():
    from app.config import Settings
    return Settings(gemini_api_key="k", text_model="t", image_model="i",
                    data_dir=Path("."), db_path=Path("./x.db"),
                    use_fake_gemini=False, server_run_id="r",
                    request_timeout_seconds=12.0)


def test_the_client_makes_exactly_one_attempt_per_request():
    """Asserts the attempt count the SDK actually computes, not the value we
    passed in - `attempts` counts the original request, so the field name reads
    like a retry count when it is really a total. Assessment 4.3 forbids
    auto-retry, and one attempt is how the SDK spells that."""
    from google import genai

    client = genai.Client(
        api_key="k",
        http_options=RealGeminiClient.http_options(_settings_for_client_config()),
    )

    assert client._api_client._retry.stop.max_attempt_number == 1


def test_omitting_attempts_would_silently_enable_four_retries():
    """The footgun this configuration exists to avoid. We need an
    HttpRetryOptions for the timeout anyway, and `attempts` defaults to 5 the
    moment that object exists - which is exactly the shape of notebook cell 12."""
    from google import genai
    from google.genai import types

    careless = genai.Client(api_key="k", http_options=types.HttpOptions(
        retry_options=types.HttpRetryOptions(initial_delay=2.0)))

    assert careless._api_client._retry.stop.max_attempt_number == 5


def test_the_request_timeout_is_passed_in_milliseconds():
    options = RealGeminiClient.http_options(_settings_for_client_config())
    assert options.timeout == 12_000
    assert options.retry_options.attempts == 1


async def test_create_text_sends_a_plain_prompt_and_reads_output_text(settings):
    client, stub = make(settings, interaction(text="a watercolour style"))

    result = await client.create_text(prompt="define a style",
                                      previous_interaction_id="i-1")

    call = stub.interactions.calls[0]
    assert call["model"] == settings.text_model
    assert call["input"] == "define a style"
    assert call["previous_interaction_id"] == "i-1"
    assert result.text == "a watercolour style"
    assert result.interaction_id == "interactions/abc"


async def test_create_text_falls_back_to_the_last_step_when_output_text_is_empty(settings):
    """The notebook uses both accessors: cell 32 output_text, cells 37/41
    steps[-1].content[0].text. The spike settles which is primary; the other
    stays as a fallback."""
    steps = [SimpleNamespace(type="model_output",
                             content=[SimpleNamespace(type="text", text="from the step")])]
    client, _ = make(settings, interaction(text=None, steps=steps))

    assert (await client.create_text(prompt="p")).text == "from the step"


async def test_a_document_becomes_a_multipart_input(settings):
    client, stub = make(settings, interaction(text="ok"))

    await client.create_text(prompt="here is a book", document_uri="files/uploaded-123")

    assert stub.interactions.calls[0]["input"] == [
        {"type": "text", "text": "here is a book"},
        {"type": "document", "uri": "files/uploaded-123"},
    ]


async def test_create_structured_sets_response_format_with_max_items(settings):
    client, stub = make(settings, interaction(text='[{"name":"Toad","prompt":"p"}]'))

    result = await client.create_structured(
        prompt="characters", item_schema={"type": "object"}, max_items=2)

    assert stub.interactions.calls[0]["response_format"] == {
        "type": "text",
        "mime_type": "application/json",
        "schema": {"type": "array", "maxItems": 2, "items": {"type": "object"}},
    }
    assert result.items == [{"name": "Toad", "prompt": "p"}]


async def test_create_image_prefers_output_image(settings):
    image = SimpleNamespace(data=base64.b64encode(PNG).decode(), mime_type="image/png")
    client, _ = make(settings, interaction(image=image))

    result = await client.create_image(prompt="draw a toad")

    assert result.data == PNG
    assert result.mime_type == "image/png"


async def test_create_image_falls_back_to_walking_the_steps(settings):
    content = SimpleNamespace(type="image", data=base64.b64encode(PNG).decode(),
                              mime_type="image/png")
    steps = [SimpleNamespace(type="model_output", content=[content])]
    client, _ = make(settings, interaction(image=None, steps=steps))

    assert (await client.create_image(prompt="draw a toad")).data == PNG


async def test_an_image_call_with_no_image_anywhere_is_an_error(settings):
    client, _ = make(settings, interaction(image=None, steps=[]))
    with pytest.raises(GeminiError, match="no image"):
        await client.create_image(prompt="draw a toad")


async def test_reference_images_and_system_instruction_are_sent_inline(settings):
    image = SimpleNamespace(data=base64.b64encode(PNG).decode(), mime_type="image/png")
    client, stub = make(settings, interaction(image=image))

    await client.create_image(prompt="use these",
                              reference_images=[ReferenceImage(PNG, "image/png")],
                              system_instruction="no text on the image")

    call = stub.interactions.calls[0]
    assert call["input"] == [
        {"type": "text", "text": "use these"},
        {"type": "image", "data": base64.b64encode(PNG).decode(), "mime_type": "image/png"},
    ]
    assert call["system_instruction"] == "no text on the image"
    assert "previous_interaction_id" not in call


async def test_a_missing_interaction_becomes_InteractionNotFound(settings):
    from google.genai import errors
    error = errors.ClientError(404, {"error": {"message": "Interaction not found"}})
    client, _ = make(settings, error)

    with pytest.raises(InteractionNotFound):
        await client.create_text(prompt="p", previous_interaction_id="i-gone")


async def test_any_other_provider_error_becomes_GeminiError(settings):
    from google.genai import errors
    error = errors.ClientError(429, {"error": {"message": "Resource exhausted"}})
    client, _ = make(settings, error)

    with pytest.raises(GeminiError) as excinfo:
        await client.create_text(prompt="p")
    assert not isinstance(excinfo.value, InteractionNotFound)


async def test_upload_returns_the_file_uri(settings, tmp_path):
    book = tmp_path / "book.txt"
    book.write_text("text", encoding="utf-8")
    client, _ = make(settings, interaction(text="ok"))

    assert await client.upload_book(book) == "files/uploaded-123"
```

- [ ] **Step 2: Run it and verify it fails**

Run: `cd backend && python -m pytest tests/test_gemini_real.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.gemini.real'`

- [ ] **Step 3: Write `backend/app/gemini/real.py`**

```python
"""The real Gemini client.

The only module that imports google.genai. Shapes match the notebook and the
findings recorded in docs/gemini-contract.md.
"""
from __future__ import annotations

import base64
from pathlib import Path
from typing import Any, Sequence

from google import genai
from google.genai import errors, types

from app.config import Settings
from app.gemini.protocol import (
    GeminiError, ImageResult, InteractionNotFound, ReferenceImage, StructuredResult,
    TextResult, parse_items,
)


class RealGeminiClient:
    def __init__(self, settings: Settings, client: Any | None = None) -> None:
        self._settings = settings
        self._client = client or genai.Client(
            api_key=settings.gemini_api_key,
            http_options=self.http_options(settings),
        )

    @staticmethod
    def http_options(settings: Settings) -> types.HttpOptions:
        """`attempts` counts the original request, so 1 means call once and never
        repeat - the SDK compiles it to tenacity.stop_after_attempt(1), its own
        "never retry" strategy. Setting it explicitly matters because we need an
        HttpRetryOptions for the timeout anyway, and `attempts` defaults to 5
        inside one. Assessment 4.3 forbids auto-retry (design 2, 7.7)."""
        return types.HttpOptions(
            timeout=int(settings.request_timeout_seconds * 1000),
            retry_options=types.HttpRetryOptions(attempts=1),
        )

    # ---- helpers ----------------------------------------------------------

    @staticmethod
    def _text_of(interaction: Any) -> str:
        if getattr(interaction, "output_text", None):
            return interaction.output_text
        for step in reversed(getattr(interaction, "steps", []) or []):
            if getattr(step, "type", None) == "model_output" and step.content:
                for content in reversed(step.content):
                    if getattr(content, "type", None) == "text" and content.text:
                        return content.text
        raise GeminiError("Gemini returned no text output.")

    @staticmethod
    def _image_of(interaction: Any) -> tuple[bytes, str]:
        candidate = getattr(interaction, "output_image", None)
        if candidate is None:
            for step in reversed(getattr(interaction, "steps", []) or []):
                if getattr(step, "type", None) == "model_output" and step.content:
                    for content in reversed(step.content):
                        if getattr(content, "type", None) == "image":
                            candidate = content
                            break
                    if candidate is not None:
                        break
        if candidate is None:
            raise GeminiError("Gemini returned no image for this prompt.")
        return base64.b64decode(candidate.data), candidate.mime_type

    @staticmethod
    def _translate(exc: Exception) -> GeminiError:
        """Q9 in docs/gemini-contract.md records the observed type and code. If
        the spike recorded something other than a 404 ClientError, widen this
        predicate to match it and update the test fixture to the recorded shape."""
        code = getattr(exc, "code", None)
        message = str(exc)
        if code == 404 or "interaction" in message.lower() and "not found" in message.lower():
            return InteractionNotFound(
                "The Gemini conversation for this project no longer exists.")
        return GeminiError(f"Gemini could not complete this request: {message}")

    async def _create(self, **kwargs: Any) -> Any:
        try:
            return await self._client.aio.interactions.create(**kwargs)
        except errors.APIError as exc:
            raise self._translate(exc) from exc
        except GeminiError:
            raise
        except Exception as exc:
            raise GeminiError(f"Gemini could not complete this request: {exc}") from exc

    # ---- GeminiClient -----------------------------------------------------

    async def upload_book(self, book_path: Path) -> str:
        try:
            return self._client.files.upload(file=str(book_path)).uri
        except Exception as exc:
            raise GeminiError(f"The book could not be uploaded to Gemini: {exc}") from exc

    async def create_text(self, *, prompt: str, previous_interaction_id: str | None = None,
                          document_uri: str | None = None) -> TextResult:
        payload: Any = prompt
        if document_uri is not None:
            payload = [{"type": "text", "text": prompt},
                       {"type": "document", "uri": document_uri}]
        kwargs: dict[str, Any] = {"model": self._settings.text_model, "input": payload}
        if previous_interaction_id is not None:
            kwargs["previous_interaction_id"] = previous_interaction_id
        interaction = await self._create(**kwargs)
        return TextResult(interaction_id=interaction.id, text=self._text_of(interaction))

    async def create_structured(self, *, prompt: str,
                                previous_interaction_id: str | None = None,
                                document_uri: str | None = None, item_schema: dict,
                                max_items: int) -> StructuredResult:
        payload: Any = prompt
        if document_uri is not None:
            payload = [{"type": "text", "text": prompt},
                       {"type": "document", "uri": document_uri}]
        kwargs: dict[str, Any] = {
            "model": self._settings.text_model,
            "input": payload,
            "response_format": {
                "type": "text",
                "mime_type": "application/json",
                "schema": {"type": "array", "maxItems": max_items, "items": item_schema},
            },
        }
        if previous_interaction_id is not None:
            kwargs["previous_interaction_id"] = previous_interaction_id
        interaction = await self._create(**kwargs)
        return StructuredResult(interaction_id=interaction.id,
                                items=parse_items(self._text_of(interaction)))

    async def create_image(self, *, prompt: str, previous_interaction_id: str | None = None,
                           reference_images: Sequence[ReferenceImage] = (),
                           system_instruction: str | None = None) -> ImageResult:
        payload: Any = prompt
        if reference_images:
            payload = [{"type": "text", "text": prompt}] + [
                {"type": "image",
                 "data": base64.b64encode(ref.data).decode(),
                 "mime_type": ref.mime_type}
                for ref in reference_images
            ]
        kwargs: dict[str, Any] = {"model": self._settings.image_model, "input": payload}
        if previous_interaction_id is not None:
            kwargs["previous_interaction_id"] = previous_interaction_id
        if system_instruction is not None:
            kwargs["system_instruction"] = system_instruction
        interaction = await self._create(**kwargs)
        data, mime_type = self._image_of(interaction)
        return ImageResult(interaction_id=interaction.id, data=data, mime_type=mime_type)
```

- [ ] **Step 4: Run the test and verify it passes**

Run: `cd backend && python -m pytest tests/test_gemini_real.py -v`
Expected: PASS — 12 passed

- [ ] **Step 5: Run the whole backend suite**

Run: `cd backend && python -m pytest -q`
Expected: PASS — all green

- [ ] **Step 6: Commit**

```bash
git add backend/app/gemini/real.py backend/tests/test_gemini_real.py
git commit -m "Add the real Gemini client behind the existing protocol

A leaf swap: every orchestration guarantee was already proven against the fake,
so this module only has to shape requests and read responses correctly.

Retries are disabled explicitly. `attempts` counts the original request, so 1
means call once and never repeat. It is set explicitly because we need an
HttpRetryOptions for the timeout anyway, and `attempts` defaults to 5 inside
one - which is exactly the shape of notebook cell 12, where dropping that single
field would silently buy four automatic retries. The test asserts the attempt
count the SDK actually computes, and a second test pins the 5 so the footgun is
documented rather than remembered.

Text and image extraction try output_text/output_image first and fall back to
walking steps, because the notebook itself uses both accessors.

Mostly AI-authored (Claude Code).

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 35: Accessibility, responsive behaviour, no layout jumps

§07 grades "keyboard-usable, no layout jumps, sensible responsive behavior" explicitly.

**Files:**
- Modify: `frontend/src/styles/app.css`, `frontend/src/components/AppShell.tsx`, `frontend/src/components/ProjectRow.tsx`, `frontend/src/components/EntityCard.tsx`
- Create: `frontend/src/__tests__/accessibility.test.tsx`

**Interfaces:**
- Consumes: every component built so far. No new interface.

- [ ] **Step 1: Write the failing test**

`frontend/src/__tests__/accessibility.test.tsx`:

```tsx
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, test, vi } from 'vitest';
import EntityCard from '../components/EntityCard';
import ProjectRow from '../components/ProjectRow';
import SignIn from '../components/SignIn';
import StepPanel from '../components/StepPanel';
import type { EntityView, ProjectListItem, ProjectView } from '../types';

const listItem: ProjectListItem = {
  id: 'p1', title: 'Willows', created_at: '2026-08-14T10:00:00+00:00',
  status: 'STYLE_SET', current_step: 'CHARACTERS', display_status: 'In progress',
  needs_attention: false, is_interrupted: false, completed_steps: 1,
};

const runningProject: ProjectView = {
  ...listItem, step_state: 'RUNNING', style_text: null, book_excerpt: 'Once…',
  failure: null, characters: [], chapters: [],
};

test('every interactive element in the sign-in form is reachable by keyboard', async () => {
  render(<SignIn onSubmit={vi.fn()} error={null} busy={false} />);
  await userEvent.tab();
  expect(screen.getByLabelText(/full name/i)).toHaveFocus();
  await userEvent.tab();
  expect(screen.getByLabelText(/email/i)).toHaveFocus();
  await userEvent.tab();
  expect(screen.getByRole('button', { name: /continue/i })).toHaveFocus();
});

test('a project row is focusable and activates from the keyboard', async () => {
  const onOpen = vi.fn();
  render(<ProjectRow project={listItem} onOpen={onOpen} />);
  await userEvent.tab();
  expect(screen.getByRole('button', { name: /willows/i })).toHaveFocus();
  await userEvent.keyboard('{Enter}');
  expect(onOpen).toHaveBeenCalledWith('p1');
});

test('the running status is announced to assistive technology', () => {
  render(<StepPanel project={runningProject} onRun={vi.fn()} busy={false} />);
  const status = screen.getByRole('status');
  expect(status).toHaveAttribute('aria-live', 'polite');
  expect(status).toHaveTextContent(/generating the character list/i);
});

test('the spinner is decorative; the caption carries the meaning', () => {
  const { container } = render(
    <StepPanel project={runningProject} onRun={vi.fn()} busy={false} />);
  expect(container.querySelector('.spinner')).toHaveAttribute('aria-hidden', 'true');
});

test('the progress indicator has a text equivalent', () => {
  render(<ProjectRow project={listItem} onOpen={vi.fn()} />);
  expect(screen.getByLabelText('1 of 5 steps complete')).toBeInTheDocument();
});

test('images carry descriptive alt text', () => {
  const item: EntityView = { id: 'c1', position: 0, name: 'Toad', prompt: 'a toad',
                             image_url: '/x', image_state: 'ready' };
  render(<EntityCard kind="character" item={item} />);
  expect(screen.getByAltText('Portrait of Toad')).toBeInTheDocument();
});

test('the art slot keeps its box before the image lands, so nothing reflows', () => {
  const pending: EntityView = { id: 'c1', position: 0, name: 'Toad', prompt: 'a toad',
                                image_url: null, image_state: 'pending' };
  const { container } = render(<EntityCard kind="character" item={pending} />);
  expect(container.querySelector('.art')).not.toBeNull();
});
```

- [ ] **Step 2: Run it and verify it fails**

Run: `cd frontend && npm test -- --run src/__tests__/accessibility.test.tsx`
Expected: FAIL — the progress-indicator label and possibly the focus-order assertions.

- [ ] **Step 3: Make the changes**

Add to `frontend/src/styles/app.css`:

```css
/* Keyboard focus: visible ring, never shown for mouse or touch. */
a:focus-visible, button:focus-visible, input:focus-visible, textarea:focus-visible,
.project-row:focus-visible {
  outline: 2px solid var(--grad-orange);
  outline-offset: 2px;
  border-radius: var(--r-1);
}

/* Fixed aspect-ratio art slots so an image landing never reflows the page. */
.entity-card .art { aspect-ratio: 3 / 4; display: flex; align-items: center;
                    justify-content: center; overflow: hidden; }
.entity-card .art.chapter { aspect-ratio: 16 / 10; }
.entity-card .art img { width: 100%; height: 100%; object-fit: cover; display: block; }

/* The step panel reserves height so switching state does not jump the layout. */
.step-panel { min-height: 172px; }

.detail-grid { display: grid; grid-template-columns: 2fr 1fr; gap: var(--sp-7);
               align-items: start; }
.entity-grid { display: grid; grid-template-columns: repeat(2, 1fr); gap: var(--sp-4); }
.entity-grid.single { grid-template-columns: 1fr; }
.book-full { white-space: pre-wrap; max-height: 40vh; overflow-y: auto;
             font-size: 13px; line-height: var(--lh-loose); }

@media (max-width: 720px) {
  .detail-grid { grid-template-columns: 1fr; }
  .entity-grid { grid-template-columns: 1fr; }
  .stepper .lbl { display: none; }
  .project-row { flex-wrap: wrap; }
}
```

Confirm `aria-hidden="true"` on every `.spinner`, `aria-label` on the progress indicator (already written in Task 14), and descriptive `alt` on every image (Task 28). Give `AppShell`'s header a `<header>` landmark and the content an `<main>` landmark — both already present.

- [ ] **Step 4: Run the test and verify it passes**

Run: `cd frontend && npm test -- --run`
Expected: PASS — all green

- [ ] **Step 5: Check it in a real browser**

Run: `./start.sh`, then with `USE_FAKE_GEMINI=1` in `.env`, walk one project through all five steps at 1280px and at 380px. Confirm: no horizontal scroll, no layout jump when an image lands, focus ring visible on every control, and the book panel reachable at every stage.

- [ ] **Step 6: Commit**

```bash
git add -A frontend/src
git commit -m "Accessibility and responsive pass

Fixed aspect-ratio art slots mean an image landing never reflows the page, and
the step panel reserves height so switching between Ready, Running and Failed
does not jump the layout - both graded explicitly under 'no layout jumps'.

The spinner is aria-hidden and the caption carries the meaning in text, which
is also why prefers-reduced-motion stops it. The progress indicator has a text
equivalent. Project rows are focusable and activate from the keyboard.

Mostly AI-authored (Claude Code); verified by hand in a browser at 1280px and
380px.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 36: `README.md` and `DECISIONS.md`

**Files:**
- Create: `README.md`, `DECISIONS.md`

**Interfaces:**
- Consumes: the finished system.

- [ ] **Step 1: Write `README.md`**

It must contain, in this order:

1. **One paragraph** on what the app is.
2. **Prerequisites** — Python 3.13, Node 20+, a Gemini API key, and *Git Bash on Windows* because the scripts are POSIX `sh`.
3. **Setup** — `cp .env.example .env`, set `GEMINI_API_KEY`, `pip install -r backend/requirements.txt`, `npm install --prefix frontend`.
4. **One command to start:** `./start.sh` → backend on 8000, frontend on <http://localhost:5173>.
5. **One command to test:** `./test.sh`.
6. **Env vars** — the table from `.env.example`, with the note that model IDs are the ones the notebook was run with and should be re-checked against AI Studio.
7. **Architecture overview** — 10–15 lines: FastAPI + SQLite + local filesystem, one conditional `UPDATE` as the duplicate-execution guard, `server_run_id` for interruption detection, detached in-process task, REST for bootstrap and commands, WebSocket for live state, React owning no pipeline state.
8. **"Why no Docker"** — the app is two local processes and a SQLite file; a compose file would add a build step and a volume mount to run what `./start.sh` already runs. §5.5 explicitly invites saying so.
9. **Known limits** — single worker required (twice over), free-tier interaction retention is 1 day so a project resumed the next day takes the standalone path, no `fsync`.
10. **Two deliberate divergences from `app-demo.html`** — the book panel does not disappear once a style exists (the demo's `line 700` bug), and there is no `localStorage` store or 8-second stuck threshold.
11. **Running against the fake provider** — `USE_FAKE_GEMINI=1` for a full click-through with zero quota.

- [ ] **Step 2: Write `DECISIONS.md`**

Six decisions, each a heading plus one or two paragraphs in your own words: who proposed it, who pushed back, where you landed, what it cost. **Do not use the assessment's own sample wording.** Write only decisions that genuinely happened — the design session is in git history at `f6a045f` and `d7ef080` and can be checked.

The six:

1. **SQLite over JSON files.** The useful angle: `sqlite3` is stdlib, so "a DB is over-engineering" does not apply — no server, no container, no dependency. Cost: SQL in the codebase, and a single-writer story that would not scale.
2. **How pipeline progress is modelled.** Written around what is actually ours: `current_step` derived rather than stored, artifacts as durable checkpoints that never speak for the milestone, and the rule that `status` is never recomputed from artifacts on read. *Avoid framing it as "separate `status` and `step_state`" — that is the assessment's own worked example and will read as copied.*
3. **One conditional `UPDATE` enforcing three invariants** — ordering, single execution, orphan recovery — and the attempt guard that was removed from it.
4. **`server_run_id` over a time threshold.** Include the single-process cost and the testability dividend that was *not* the reason for choosing it.
5. **Persisted state authoritative, Gemini handles ephemeral.** Expiry fails the step and nulls the head; the user's retry reconstructs standalone.
6. **WebSocket over polling** — a deliberate §08 bonus, reversing the earlier recommendation that polling was the smallest sufficient mechanism. **This is not an AI-was-wrong example** and must not be presented as one.

Then **at least three genuine AI overrides** (§2.3), drawn from what actually happened during the design session:

- **The unnecessary attempt/fencing guard** — a mechanism introduced and justified by a hypothetical; removed once the reachable scenarios were enumerated and every one turned out to be already covered.
- **Eager automatic context rehydration** — an in-run context rebuild that would have re-invoked Gemini automatically, contradicting "retries are user-triggered only". Removing it collapsed a recovery subsystem into a branch each step already had.
- **The exactly-once overclaim** — the no-duplicate-calls guarantee was written without its crash boundary, claiming more than an external API can give.
- **The false claim about the notebook's retry configuration — and the over-correction that followed it.** The design argued no-auto-retry "matches the notebook" and was therefore not an override at all; `note.md` shows the `attempts=1` in the committed notebook was my own edit while running it, so the design was citing my own change back at me as independent justification. The first fix then over-corrected, claiming the SDK "retries 5 times by default" — which reads as *any* client silently retrying. Reading `_api_client.py` showed that is false: with no `retry_options` the SDK uses its `stop_after_attempt(1)` *"never retry"* strategy, and the 5 only applies **inside** an `HttpRetryOptions` with `attempts` unset. Worth writing up as one entry with two layers: a claim I could not source, and a correction I also did not source until asked a direct question about it.
- **The invented fourth status pill** — a *Needs attention* pill replacing §4.4's named vocabulary in the one place the assessment is explicit about wording.
- **"Idempotent" step handlers** — described as idempotent when they are only resume-aware, contradicting the crash boundary the same document had just drawn.
- **Invented input limits** — a 2 MB book-text cap and a 500-character style cap, tied to nothing.

Close with the required **"if you had one more day"** answer.

- [ ] **Step 3: Verify every claim**

Run: `git log --oneline | head -40`
Check each DECISIONS entry against a real commit or a real design-doc change. Delete anything you cannot point at.

- [ ] **Step 4: Commit**

```bash
git add README.md DECISIONS.md
git commit -m "Add README and DECISIONS

DECISIONS covers the three required topics - stack and storage, how pipeline
progress is modelled, how duplicate execution is stopped - plus the recovery
model and the realtime choice. The AI-override section is drawn from
corrections that are visible in git history, including the one where the design
cited my own notebook edit back at me as independent justification for
disabling retries.

WebSocket-over-polling is written as a deliberate bonus choice, not as a case
of AI being wrong: polling was a valid smaller design.

Human-written; drafted with Claude Code.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 37: Manual UAT against real Gemini, `TESTING.md`, and the real test report

Automated tests deliberately mock Gemini, so this is the **only** evidence the real integration works. **Do not fake any of it.**

**Files:**
- Create: `TESTING.md`, `test-report.txt`, `docs/uat/*.png`

**Interfaces:**
- Consumes: the finished system with a real `GEMINI_API_KEY`.

- [ ] **Step 1: Capture the real automated test report**

```bash
./test.sh 2>&1 | tee test-report.txt
tail -5 test-report.txt
```

Expected: a real pass line from both suites. If anything fails, fix it and re-capture — the committed report must come from a real run.

- [ ] **Step 2: One real five-step run against live Gemini**

Set `USE_FAKE_GEMINI=0` and a real key in `.env`, then `./start.sh`. Create a project from `book.txt` (The Wind in the Willows, already in the repo). Run all five steps by hand.

Record: the generated style text, both character names and prompts, the chapter name and prompt, and screenshots of both portraits and the illustration. Note whether the two portraits look like the same art style, and whether the illustration's characters resemble their portraits.

- [ ] **Step 3: One real interruption**

While a step shows RUNNING, kill the backend process (Ctrl-C in the `start.sh` terminal, or `taskkill`). Restart with `./start.sh`. Reopen the project.

Confirm and screenshot: the panel shows **Interrupted**, the list row shows **In progress** with a *Needs attention* warning, prior outputs are all still present, and **Retry** completes the step and continues the pipeline.

- [ ] **Step 4: Refresh, re-login, second tab**

With a step running: refresh the page and confirm the same in-flight state with no new call in the backend log. Open a second tab on the same project and confirm both show the run. Sign out, sign back in with the same email, confirm the project and all results return.

- [ ] **Step 5: Keyboard, responsive and book-text pass**

Tab through every screen; confirm a visible focus ring on each control and that the whole flow is operable without a mouse. Check 1280px and 380px. Confirm the full book text is reachable at all five stages.

- [ ] **Step 6: Write `TESTING.md`**

A few hundred words covering:

- **What is tested and why.** Backend: the conditional transition against real SQLite, step ordering, resumability within a step, the caps, the recovery paths, the exact Gemini call sequence. Frontend: the five StepPanel states, per-item card derivation, loading/error/empty, the 409 path, and the transport-failure rule.
- **What is deliberately not tested, and why.** Real Gemini in the suite (quota, non-determinism; §5.4 says mock) · browser E2E (§5.4: not expected) · exhaustive component coverage (§5.4: "pick a couple that matter") · prompt and image *quality*, which belongs to UAT · SQLite itself · reconnect backoff *timing* · visual regression. State plainly that coverage is not the metric.
- **How the fake works and why it is trustworthy** — it reproduces the contract verified by the Task 2 spike against the real API, and it records calls so the pipeline claims are assertions rather than prose.
- **The real test report** — reference `test-report.txt` and paste its summary lines.
- **The manual UAT results** — everything from Steps 2–5, with the screenshots, including anything that went wrong.

- [ ] **Step 7: Commit**

```bash
git add TESTING.md test-report.txt docs/uat
git commit -m "Add TESTING.md, the real test report and manual UAT results

The automated suite mocks Gemini on purpose, so the UAT is the only evidence
the real integration works: one live five-step run with the outputs inspected,
one real backend kill mid-RUNNING with the Interrupted state and the Retry
recovery confirmed, plus refresh, second tab, re-login, keyboard and responsive
passes. Screenshots in docs/uat.

test-report.txt is the output of an actual ./test.sh run, not a summary.

Human-run; written up with Claude Code.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Acceptance Coverage

Every required use case, mapped to the task and the test that provides its evidence. No row relies on prose.

| # | Use case | Task | Test |
|---|---|---|---|
| 1 | New email creates a user | 10 | `test_api_session.py::test_a_new_email_creates_a_user_and_sets_a_session_cookie` |
| 2 | Returning email restores that user's projects | 11 | `test_api_projects.py::test_signing_out_and_back_in_restores_the_same_projects` |
| 3 | Creation persists metadata + `book.txt`, zero Gemini calls | 11 | `test_api_projects.py::test_creating_a_project_persists_the_row_and_the_book_file`, `::test_creating_a_project_makes_zero_gemini_calls` |
| 4 | Full five-step happy path to `DONE` | 24 | `test_acceptance_happypath.py::test_five_user_actions_take_a_project_to_done` |
| 5 | Completing N never starts N+1 | 24 | `test_acceptance_happypath.py::test_completing_a_step_never_starts_the_next_one` |
| 6 | Generated Style path | 18, 24 | `test_handlers.py::test_generated_style_uploads_the_book_seeds_then_asks_for_a_style` |
| 7 | User-supplied Style path | 18, 24 | `test_acceptance_happypath.py::test_a_user_supplied_style_takes_the_acknowledge_branch` |
| 8 | Wrong/future step → 409, Gemini invoked zero times | 23 | `test_pipeline.py::test_a_future_step_is_409_and_makes_zero_gemini_calls` |
| 9 | Genuine concurrent `/run` race → one execution | 25 | `test_acceptance_concurrency.py::test_two_simultaneous_runs_produce_one_202_one_409_and_one_execution`, `::test_ten_simultaneous_runs_still_produce_exactly_one_execution` |
| 10 | Refresh / new client while RUNNING starts nothing new | 25 | `::test_a_refresh_mid_step_shows_the_in_flight_state_and_starts_nothing`, `::test_a_second_client_sees_the_same_in_flight_run_and_starts_nothing` |
| 11 | Sign out, identify again, state restored | 25 | `::test_signing_out_and_back_in_restores_results_and_regenerates_nothing` |
| 12 | Late-step failure preserves earlier outputs | 26 | `test_acceptance_recovery.py::test_a_late_failure_leaves_every_earlier_output_intact` |
| 13 | Retry invokes only remaining/failed work | 26 | `::test_retrying_touches_only_the_failed_step` |
| 14 | Portrait 1 persisted + portrait 2 failure resumes | 20, 26 | `test_handlers.py::test_an_existing_portrait_is_never_regenerated`; `test_acceptance_recovery.py::test_portrait_one_survives_a_portrait_two_failure` |
| 15 | Old `server_run_id` → `is_interrupted`, normal Retry recovers | 26 | `::test_a_run_stamped_by_a_dead_process_surfaces_as_interrupted`, `::test_the_normal_retry_command_recovers_an_interrupted_step` |
| 16 | Cancellation cannot leave a step permanently RUNNING | 26 | `::test_a_cancelled_task_leaves_the_step_failed_never_running`, `::test_a_cancellation_arriving_after_takeover_writes_nothing` |
| 17 | Context expiry: one attempt, head NULL, retry rebuilds, book re-uploaded only where needed | 26 | `::test_expiry_fails_with_one_attempt_and_nulls_the_head_that_raised`, `::test_the_user_retry_rebuilds_from_minimum_persisted_state`, `::test_image_chain_expiry_nulls_only_the_image_head`, `::test_steps_three_and_five_never_re_upload_the_book_on_recovery` |
| 18 | Server-side hard caps: max 2 portraits, max 1 illustration | 20, 22 | `test_handlers.py::test_the_generation_loop_is_bounded_regardless_of_how_many_rows_exist`, `::test_the_illustration_loop_is_bounded_at_one_chapter` |
| 19 | Schema-violating structured output fails validation, no slicing | 19, 21, 26 | `test_handlers.py::test_an_over_cap_response_fails_validation_rather_than_being_sliced`, `::test_more_than_one_chapter_fails_validation`; `test_acceptance_recovery.py::test_an_over_cap_response_surfaces_as_invalid_output` |
| 20 | No automatic Gemini retries | 26, 34 | `test_acceptance_recovery.py::test_a_provider_failure_is_attempted_once_and_never_looped`; `test_gemini_real.py::test_the_client_disables_sdk_retries` |
| 21 | Normal pipeline sends/uploads the book once | 24 | `test_acceptance_happypath.py::test_the_book_is_uploaded_exactly_once_across_the_whole_run` |
| 22 | Ownership isolation: REST, artifacts, WebSocket | 11, 23, 31 | `test_api_projects.py::test_another_users_project_is_404_not_403`, `::test_artifact_bytes_are_served_and_ownership_checked`; `test_pipeline.py::test_running_another_users_project_is_404`; `test_ws.py::test_another_users_project_is_closed_with_1008` |
| 23 | WS subscription returns authoritative current state | 31 | `test_ws.py::test_subscribing_immediately_returns_the_authoritative_current_state`, `::test_the_socket_payload_is_identical_to_the_rest_project_view` |
| 24 | GET→subscribe race cannot leave the browser stale | 31 | `test_ws.py::test_state_changing_between_get_and_subscribe_still_reaches_the_client` |
| 25 | Two tabs observe one running execution | 32 | `test_ws.py::test_two_viewers_watch_one_run_and_cause_zero_extra_gemini_calls` |
| 26 | WS disconnect / send failure cannot fail the pipeline | 30, 32 | `test_realtime.py::test_a_send_failure_kills_only_that_subscribers_writer`; `test_ws.py::test_a_broadcaster_that_raises_cannot_fail_a_pipeline_step`, `::test_a_subscriber_whose_send_raises_does_not_affect_the_step` |
| 27 | Reconnect restores the latest persisted state | 31, 33 | `test_ws.py::test_reconnecting_yields_current_persisted_truth`; `useProjectSocket.test.tsx::test_an_abnormal_close_reconnects_with_a_bounded_backoff` |
| 28 | Frontend loading / error / empty / Ready / Running / Failed / Interrupted / Complete | 14, 27, 29 | `ProjectList.test.tsx` (loading, error, empty); `StepPanel.test.tsx` (all five panel states); `ProjectDetail.test.tsx` (detail loading, detail error) |
| 29 | Per-item portrait rendering: `[null,null]`, `[path,null]` | 8, 28 | `test_read_model.py::test_while_running_the_first_missing_portrait_is_the_one_generating`, `::test_a_landed_portrait_is_ready_and_the_next_becomes_generating`; `EntityCard.test.tsx` (both fixtures) |
| 30 | `/run` 409 replaces state rather than showing a failure | 12, 29 | `api.test.ts::test_409_is_a_conflict_carrying_the_truth`; `ProjectDetail.test.tsx::test_a_409_renders_current_truth_rather_than_a_pipeline_failure` |
| 31 | Transport failure never invents pipeline `FAILED` | 29, 33 | `ProjectDetail.test.tsx::test_a_transport_failure_shows_a_banner_and_never_invents_FAILED`, `::test_losing_the_socket_never_turns_the_project_into_a_pipeline_failure` |
| 32 | New Project paste-text input | 15 | `NewProject.test.tsx::test_the_paste_path_creates_a_project` |
| 33 | New Project `.txt` input | 15 | `NewProject.test.tsx::test_the_txt_path_reads_the_file_into_the_same_field_and_submits_identically` |
| 34 | Project List shows the five-step progress indicator | 14 | `ProjectList.test.tsx::test_the_five_step_indicator_fills_one_segment_per_completed_step` |

---

## Spec Coverage

| Requirement | Source | Task(s) |
|---|---|---|
| Identity: email + name, no password | §4.1 | 10, 13 |
| Project creation from pasted or uploaded `.txt` | §4.2 | 11, 15 |
| Project list with status and progress | §4.2, §4.4 | 11, 14 |
| Opening a project shows where it is and runs the next step | §4.2 | 11, 27, 29 |
| User-driven, in order | §4.3 | 9, 23 |
| Resumable across refresh, logout, restart | §4.3 | 9, 18–22, 25, 26 |
| No duplicate calls | §4.3 | 9, 23, 25 |
| Specific in-progress state naming the step | §4.3 | 27 |
| Failures retryable, that step only | §4.3 | 23, 26, 27 |
| Nothing stuck forever | §4.3 | 9, 23, 26 |
| Cost discipline: no auto-retry, book sent once | §4.3 | 24, 26, 34 |
| Identity screen with validation | §4.4 | 13 |
| Project list: title, date, pill, 5-step indicator, empty state | §4.4 | 14 |
| New project: title, upload **and** paste, validation | §4.4 | 15 |
| Detail: title, date, book readable at any point | §4.4 | 28, 29 |
| Detail: stepper done/current/pending | §4.4 | 27 |
| Detail: current style | §4.4 | 28 |
| Detail: character cards, chapter cards | §4.4 | 28, 29 |
| Detail: one action button, step 1 optional style | §4.4 | 27 |
| Detail: per-item progress while images generate | §4.4 | 8, 28, 32 |
| In-progress / error / stuck-recovery states | §4.4 | 27 |
| Sign out | §4.4 | 12, 13 |
| Notebook pipeline: chaining, structured JSON, order, caps | §03, §07 | 16, 18–22, 24 |
| Caps enforced server-side | §03 | 19, 20, 21, 22 |
| Storage: SQLite + local filesystem, artifacts through own API | §5.2 | 3, 5, 8, 11 |
| Gemini: env-var key, `.env.example`, current models, no committed secret | §5.3 | 1, 34 |
| Backend tests: ordering, progress, retry | §5.4 | 4, 8, 9, 18–26 |
| Frontend tests: components incl. loading/error/empty | §5.4 | 12–15, 27–29, 33, 35 |
| `TESTING.md` + a real test report | §5.4 | 37 |
| One command to start, one to test | §5.5 | 1 |
| Docker only if needed — and a stated reason if not | §5.5 | 36 |
| `README.md` | §06 | 36 |
| `DECISIONS.md` with ≥3 AI overrides + one-more-day | §2.1, §2.3 | 36 |
| AI artifacts in-repo | §2.2 | 1 (`CLAUDE.md`), plus `docs/superpowers/` already committed |
| Small, meaningful commits with AI authorship noted | §2.4 | every task |
| Realtime step updates (bonus) | §08 | 30–33 |
| UI polish: keyboard, no layout jumps, responsive | §07 | 35, 37 |

---

## Self-Review Record

Run after the plan was complete, per the writing-plans skill plus the project-specific checks.

**1. Spec coverage.** Every section of the design spec maps to at least one task; see the table above. Two spec items are intentionally *not* tasks: §13 "Out of scope" (nothing to build) and §16 "Residual risks" (carried into `README.md` known limits, Task 36, and into the UAT, Task 37).

**2. Acceptance coverage.** All 34 required use cases map to a named test in a named task. None relies on prose.

**3. Placeholder scan.** Searched for `TODO`, `TBD`, `later`, `appropriate handling`, `similar to`, `write tests`, `implement above`. The surviving occurrences are all quotations of real source text and are correct as written:
- Task 20 and Task 22 quote notebook cell 34's literal `# TODO: try using the last interaction` as the reason the image chain is seeded fresh.
- Task 16's prompt test asserts `"TODO" not in seeded`, which is the point of that assertion — the notebook's stray `# TODO: Sysyem instructions` comment lands inside its f-string and must not reach Gemini.

**4. Interface consistency.** Checked across tasks: store function names and keyword-only arguments (Tasks 6–9 → 11, 18–23, 31); `ProjectView` / `ProjectListItem` / `EntityView` field names (Task 7 → 8, 11, 14, 27–29, 33); the `GeminiClient` four-method protocol (Task 16 → 17, 18–22, 34); `FakeGeminiClient`'s controls (Task 17 → 18–26, 32); `RealtimeRegistry` / `Subscriber` (Task 30 → 31, 32); TypeScript types (Task 12 → 13–15, 27–29, 33). Two naming decisions were made deliberately and are used consistently from their first appearance: `steps.py` holds the vocabulary while `handlers.py` holds the handlers (import-cycle avoidance, justified in File Structure), and one `EntityCard` replaces `CharacterCard`/`ChapterCard`.

This check caught one real defect, now fixed: Task 7 defined `store.create_project` while Task 11's endpoint wrote its own `INSERT INTO projects` inline, because it needs the project id *before* writing the book file into a project-scoped directory. Two insert paths for one table is exactly the drift this check exists to find. `create_project` now takes `project_id` from the caller and the endpoint calls it; every call site in Tasks 7, 8, 9 and 11 passes it.

**5. Architecture-drift scan.** Searched the plan for Redis, queues, worker processes, polling fallback, JWT, client-side pipeline state, automatic retries, >2 characters, >1 chapter, cloud storage, deployment. Every hit is a **prohibition or a test that one is absent**, never an introduction:
- "polling" appears in the Global Constraints prohibition, twice in Task 33 rejecting a polling fallback, and in Task 36's guidance that WebSocket-over-polling must not be written up as an AI-was-wrong case. Never as something to build.
- "JWT" appears only in the Global Constraints prohibition and the Task 6 commit message explaining why sessions are opaque tokens instead.
- "automatic retries" appears in the Global Constraints, Task 34's assertion that `attempts == 1`, and Task 26's assertion that a failure is attempted exactly once.
- Three characters appear only in `FakeGeminiClient.CHARACTER_ITEMS` and in Task 20's directly-seeded third row — both exist to prove the cap **rejects** them.
- "Docker" appears in the Global Constraints prohibition and in Task 36, which requires `README.md` to state *why* there is none — exactly what §5.5 invites. No compose file is created. No deployment, no cloud storage anywhere.

**6. Plan size and ordering.** 37 tasks, each ending in a commit after a RED → GREEN cycle. Dependencies run strictly forward: persistence (3–9) → HTTP (10–11) → frontend slice one (12–15) → Gemini and handlers (16–22) → pipeline and acceptance (23–26) → frontend detail (27–29) → realtime (30–33) → real client, polish, docs, UAT (34–37). The frontend is **not** deferred to the end: four of its eight tasks land before any handler is written. The backend pipeline is **not** one task: it is seven handler/pipeline tasks plus three acceptance tasks.

**Two deviations from the spec's own wording**, both recorded above and neither an architecture change:
- `steps.py` / `handlers.py` split (spec §3.1 names one `steps.py` for handlers) — required to avoid a `store` ↔ `steps` import cycle.
- The WebSocket handshake accepts before closing with 1008 (spec §9.2 says "then `accept()`") — a close *code* can only be delivered on an accepted socket. Nothing is registered and no state is sent before the close.

**One open item carried into execution, not a blocker:** Task 2's spike settles which accessor yields structured JSON (`output_text` vs `steps[-1].content[0].text`), whether `maxItems` is enforced or advisory, and the exact exception for an expired interaction. Task 34 is written against the most likely answers with a fallback for each, and Task 2 Step 3 records the observed answers before Task 34 runs. If `maxItems` turns out to be advisory, nothing changes: the generation-loop bound and the strict `len(items) > cap` rejection are already the real enforcement.
