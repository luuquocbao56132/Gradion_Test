# Book Illustration Studio — Design

**Date:** 2026-08-14
**Status:** Approved design. Implementation has not started.
**Assessment:** Gradion Intern Fullstack Developer take-home (~16h)

---

## 1. Purpose and scope

A local web app that turns a book's text into character portraits and a chapter
illustration using the Gemini API, following the pipeline in Google's
*Illustrate a book: The Wind in the Willows* notebook, steps 1–5.

Five steps, each run by an explicit user action, in order:

```
Style → Characters → Portraits → Chapters → Illustrations
```

The product is deliberately small. The engineering difficulty is not in the
business logic; it is in persistent pipeline state, step ordering, preventing
duplicate Gemini calls, retryability, crash recovery, preserving generated
results, and Gemini context continuity — while keeping the solution
right-sized.

**Stack:** FastAPI (Python) + React (Vite), SQLite via stdlib `sqlite3`, local
filesystem for artifacts, WebSocket for live project state.

---

## 2. Source hierarchy and resolved contradictions

Authority order: **the assessment brief wins**, then the notebook for Gemini
mechanics, then `app-demo.html` for product scope and UX.

`app-demo.html` is authoritative for screens, visible states, expected UX and
the visual floor. It is *not* authoritative for persistence, concurrency,
`localStorage`, fake timings, stuck thresholds, error handling, or any backend
behaviour.

Contradictions found while reading the three sources, and how each is resolved:

| # | Contradiction | Resolution |
|---|---|---|
| 1 | The notebook does not cap characters at the prompt. Cell 32's real output returns **four** (Mole, Ratty, Toad, Badger), sliced later by `characters[:max_character_images]` at image time. The assessment moves the cap onto the list itself (§03 step 2). | Cap at the source: prompt asks for at most 2, schema sets `maxItems: 2`. This also keeps Gemini's own context free of characters we would discard — otherwise step 4 can write a chapter prompt referencing a character with no portrait. |
| 2 | `app-demo.html:700` renders the book-text panel **only when `p.style` is falsy**, so the book becomes unreachable once step 1 completes — the sole call site of `openBookModal` disappears with it. §4.4 requires the book "readable in full, at any point in the pipeline". | The book gets its own permanent panel, independent of style. The demo's behaviour is a bug and is not reproduced. |
| 3 | Notebook section 5 contains two variants: cells 37–38 (image-chain chaining, the required path) and cells 39–44, labelled *"Bonus: going further with more granular control"* (explicit reference images, `system_instruction`, no chaining). | Chaining is the normal path. The reference-image variant is the standalone-reconstruction path used when a chain is unusable (§7.5). |
| 4 | Assessment §03 calls the 2-character / 1-chapter caps "hard requirements"; §08 offers "more characters or chapters — still bounded, and document the changed caps" as a bonus. | §03 is binding for this submission. Changed caps are intentionally out of scope, not forbidden. |
| 5 | Assessment §4.3 forbids auto-retrying a Gemini call. **`attempts` counts the original request**, so `attempts=1` *is* "no retries" — `google-genai` implements it as `tenacity.stop_after_attempt(1)`, which its own source calls the *"never retry"* strategy. The trap is one level in: a client constructed with no `retry_options` does not retry, but **the moment an `HttpRetryOptions` object exists for any reason — a delay, a status-code list — `attempts` silently defaults to 5** (`_RETRY_ATTEMPTS = 5  # including the initial call`). Notebook cell 12 is exactly that shape: it sets `initial_delay`, `max_delay` and `http_status_codes`, so its `attempts=1` is load-bearing — drop that one field and four automatic retries appear. And that `attempts=1` is **our own adaptation made while running the notebook** (`note.md`: *"Chỉnh config khi integrate notebook: … disable automatic retry"*), not the reference pipeline's native configuration. | **A deliberate override, stated as one.** Follow the notebook for pipeline mechanics; own the retry configuration ourselves. The production client is constructed with `HttpRetryOptions(attempts=1)` because §4.3 requires it, and a test asserts the resulting attempt count rather than trusting the field name. The notebook is explicitly *not* cited as justification: it cannot justify a setting we put there ourselves. |

**Provider facts the design depends on** (verified against the Gemini docs):

- Interactions API retention: **1 day on the free tier**, 55 days paid
  (configurable 7/14/28/55).
- File API uploads: **deleted after 48 hours**.
- Structured Outputs documents `maxItems`.
- Image-model free-tier limits are account-specific; the published rate-limit
  page no longer carries a table and directs you to AI Studio.

The 1-day free-tier retention is shorter than the assessment's own 3-day
deadline. A project started one day and resumed the next has a dead chain.
This drives §7.5.

---

## 3. Architecture

```
React (Vite)
 ├── REST ────▶ FastAPI ──▶ SQLite (data/app.db)
 │                    ├──▶ data/projects/<id>/  (book.txt, PNGs)
 └── WS   ◀──── realtime.py                └──▶ Gemini API (retries disabled)
```

Two processes in development: uvicorn (**single worker**) and vite. `./start.sh`
runs both. No Docker; `README.md` says so explicitly, as §5.5 invites.

### 3.1 Backend modules

| Module | Owns | Does not own |
|---|---|---|
| `api/` | Routing, request validation, session cookie, ownership checks, HTTP serialization | Pipeline logic |
| `api/ws.py` | The WebSocket endpoint: session identity, ownership, subscribe | Fan-out mechanics |
| `pipeline.py` | Step ordering, the atomic transition, background execution, recording results and failures, **recovery policy** — when a Gemini handle is unusable and which persisted state to reconstruct from | Gemini's API shape |
| `steps.py` | The five step handlers, one function each | State transitions |
| `store.py` | All SQL. Conditional updates. `read_project_view()` — the single read model | Transport |
| `gemini.py` | *How* to construct text and image contexts; parsing structured output; surfacing `InteractionNotFound` as a typed error | The database, what is persisted, when to rebuild |
| `files.py` | Artifact writes, path derivation | Transport, SQL |
| `realtime.py` | Connection registry and fan-out of an **opaque payload** | Any knowledge of projects, the store, or DTOs |

Two boundaries carry weight:

- **`gemini.py` knows how; `pipeline.py` knows when and from what.** The client
  can build a context from whatever it is given; only the pipeline knows what
  is actually on disk.
- **`realtime.py` never constructs a payload.** It moves one. `pipeline.py`
  builds the view after commit and hands it over. This is what makes its
  "depends on nothing" claim true.

Adding a sixth step means: one handler in `steps.py`, one entry in the ordered
`STEPS` list, its own output storage, and its own UI representation. The
*orchestration* is step-agnostic — ordering, transitions, retry and recovery
only ask "what is the current step" and "what does it depend on". That is what
does not need rewriting; the step still needs building.

### 3.2 Filesystem layout

```
data/
  app.db
  projects/<project_id>/
    book.txt
    portraits/<character_id>.png
    illustrations/<chapter_id>.png
```

Project-scoped directories mean no filename can collide across users or
projects, and one project's entire output is inspectable in one directory.

### 3.3 The database ↔ filesystem contract

Filesystem bytes cannot participate in a SQLite transaction. Only the reference
is transactional:

```
1. write bytes → <final>.tmp   (same directory)
2. os.replace(<final>.tmp, <final>)      # atomic on POSIX, outside any txn
3. BEGIN;  UPDATE … SET <path>, <chain head>  … ;  COMMIT;
4. broadcast(project_id, view)           # after COMMIT, never inside
```

A crash between 2 and 3 leaves an orphaned PNG and an unchanged database — the
retry regenerates and commits. Because **artifact paths derive from the row id,
not randomness**, the retry overwrites its own orphan; no cleanup pass is
needed. The reverse ordering would leave a row pointing at a missing file,
which is a broken project.

`fsync` is not used. That is a durability gap against power loss, recorded as
future work rather than implemented.

Paths in the database are **relative**, so `data/` stays relocatable.

Artifacts are served through an authenticated, ownership-checked endpoint
rather than a static mount — §5.2 requires artifacts be "served through your
own API".

---

## 4. Persistent data model

```sql
users      (id, email UNIQUE, name, created_at)
sessions   (token PK, user_id, created_at)
projects   (id, user_id, title, created_at, book_path,
            status, step_state, step_started_at, server_run_id,
            error_code, error_message,
            style_text, text_interaction_id, image_interaction_id)
characters (id, project_id, position, name, prompt, portrait_path)
chapters   (id, project_id, position, name, prompt, illustration_path)
```

`status ∈ {CREATED, STYLE_SET, CHARACTERS_GENERATED, PORTRAITS_GENERATED,
CHAPTERS_GENERATED, DONE}` — deliberately the demo's vocabulary, since the demo
is the behavioural reference.

`step_state ∈ {IDLE, RUNNING, FAILED}`.

### 4.1 Stored versus derived

> **`status` is the authoritative persisted pipeline milestone. It is never
> recomputed from artifacts on read.**
>
> **Artifacts are durable checkpoints.** They are consulted only *inside* a
> retry, to decide what work remains within that step.

A project whose two portraits are both on disk but whose `status` is still
`CHARACTERS_GENERATED` reads as *not yet advanced* — correct, because the step
did not finish. The retry finds no images left to make and advances.

Derived at read time, never stored: `current_step` (from `status` via the
ordered `STEPS` list), `display_status`, `needs_attention`, `is_interrupted`,
and per-item progress.

### 4.2 Display status

Computed once, server-side, by one function shared by the list serializer, the
detail serializer, the socket payload and 409 bodies — so the pill can never
disagree between screens:

```
status == DONE                            → Done
status == CREATED and step_state == IDLE  → Draft
otherwise                                 → In progress
```

**The pill vocabulary is exactly the three values §4.4 names.** An earlier draft
added a fourth, *Needs attention*, for failed and interrupted projects. That
invents a status the assessment does not name, in the one place the assessment
is explicit about wording, so it is removed.

The concern behind it was real — a failed project rendering as *In progress*
sends the user looking for a spinner — and is answered without touching the
vocabulary. The row carries a **separate boolean**, computed by the same shared
function:

```
needs_attention = (step_state == FAILED) or is_interrupted
```

rendered as a warning affordance **beside** the pill, never in place of it.
`display_status` and `needs_attention` are independent DTO fields, so the pill
cannot be corrupted by the warning and both are consistent across the list
serializer, the detail serializer, the socket payload and 409 bodies.

### 4.3 Errors

`error_code ∈ {GEMINI_ERROR, INVALID_OUTPUT, INTERNAL}` plus a user-safe
`error_message`. Both cleared on the next transition into `RUNNING`.

Context expiry is **not** in this enum. It is an internal recovery condition,
not an outcome (§7.5). When it surfaces it does so as an ordinary
`GEMINI_ERROR` whose `error_message` explains that the conversation context
expired and that retrying will rebuild it from saved work. The code drives UI
treatment; the message carries the specifics.

### 4.4 Required SQLite pragmas

Load-bearing rather than ceremony, given a background writer and concurrent
readers:

- **`journal_mode=WAL`** — the default mode locks the whole database for a
  writer, so a read concurrent with the background task's commit would
  intermittently fail.
- **`busy_timeout`** — concurrent writers wait instead of raising
  `database is locked`.
- **`foreign_keys=ON`.**

Database access is synchronous `sqlite3` on the event loop, with a short-lived
connection per operation. At one user and microsecond writes the blocking is
invisible; it would be unacceptable at scale.

### 4.5 Per-item progress

`portrait_path` distinguishes **completed** from **incomplete** — it says
nothing about which incomplete item is in flight. The in-flight item is
derived:

```
if step_state == RUNNING and current step is PORTRAITS:
    generating = first character in position order with portrait_path IS NULL
    pending    = every later character with portrait_path IS NULL
otherwise:
    every NULL is simply pending
```

This works because the handler iterates in position order. Identical rule for
chapters. **No per-item DB state, no progress column, no `generating` flag.**

---

## 5. State machine and concurrency

### 5.1 One statement, three invariants

```sql
UPDATE projects
   SET step_state='RUNNING', server_run_id=:this_run, step_started_at=:now,
       error_code=NULL, error_message=NULL
 WHERE id=:pid
   AND status=:expected_status_for_requested_step
   AND ( step_state IN ('IDLE','FAILED')
         OR (step_state='RUNNING' AND server_run_id IS NOT :this_run) );
```

`rowcount == 1` → this caller owns the attempt; start the work.
`rowcount == 0` → 409 with the current project state.

- `status = …` enforces **step ordering**.
- `IDLE/FAILED` enforces **at most one execution**.
- The trailing clause performs **orphan recovery** — so recovery is not a
  separate endpoint or transition. Retrying an interrupted step *is* the
  recovery, permitted only when the owning process is provably gone.

### 5.2 Transitions

| From | Event | To |
|---|---|---|
| `(S, IDLE)` | user runs step `next(S)` | `(S, RUNNING)` |
| `(S, FAILED)` | user retries step `next(S)` | `(S, RUNNING)` |
| `(S, RUNNING)` with stale `server_run_id` | user retries | `(S, RUNNING)` under this run |
| `(S, RUNNING)` with current `server_run_id` | user runs anything | rejected, 409 |
| `(S, RUNNING)` | handler succeeds | `(next(S), IDLE)` — one UPDATE |
| `(S, RUNNING)` | handler raises | `(S, FAILED)` + error fields |
| `(S, RUNNING)` | task cancelled | `(S, FAILED)` + error fields, ownership-guarded, `CancelledError` re-raised (§6.3) |
| any | user runs a non-current step | rejected, 409 |

### 5.3 Interrupted steps: `server_run_id`, not a timeout

The demo uses `STALE_RUNNING_MS = 8000`. At real latencies a threshold would
need to be minutes, and any threshold is wrong in both directions — too low and
a live image call is declared dead, too high and the user watches a corpse.

**Each `RUNNING` row is stamped with a `server_run_id` minted once at process
start.** A `RUNNING` row carrying a different id is *provably* orphaned: the
process that owned it no longer exists. Correct at second zero.

`is_interrupted` is a read-time comparison, never a stored field.

§4.3 scopes this requirement precisely — *"stranded in in-progress (server died
mid-call)"* — which is exactly what this mechanism answers.

**Constraint created:** the design assumes **one server process**. With
multiple workers each would mint a different id and see the others' live rows
as orphaned. `start.sh` pins a single worker. This is an accepted, documented
limit.

### 5.4 Why there is no attempt guard

An `attempt_id`-guarded write was designed and then removed. Overlapping stale
work is unreachable here:

| Scenario | Two tasks at once? |
|---|---|
| Double-click / double-submit | No — the conditional update admits one caller |
| Two tabs click Run simultaneously | No — same |
| Refresh mid-step | No — the task never belonged to the request |
| Server restart, then retry | No — the old task's *process* is gone |
| A live task hangs forever | Prevented by a per-request timeout on the Gemini client, which converts a hang into a recorded failure |

Overlap only becomes reachable with a second process, or a manual reset of a
step running in the *current* process. We ship neither. The guard would be a
mechanism justified by a hypothetical.

**It comes back the moment either condition changes.** That is written down so
the omission is visible rather than forgotten.

### 5.5 The honest boundary of "no duplicate calls"

> **Guaranteed:** no duplicate Gemini call from ordinary concurrency —
> double-click, refresh, second tab, overlapping requests.
>
> **Not guaranteed:** exactly-once execution across a process crash. If the
> process dies after a call is dispatched but before its response is recorded,
> that call's cost is spent and unrecoverable, and a subsequent
> **user-triggered** retry may repeat it.

Exactly-once against an external API is unachievable without provider-side
idempotency keys, which Gemini does not offer here. §4.3's rule concerns
auto-retry loops and UI-triggered duplicates; that is what is satisfied.

---

## 6. Execution model

### 6.1 Detached in-process task, not a blocking handler

**Polling-or-push is forced by the requirements independently of this
decision.** §4.3 requires a refresh mid-step to show in-flight state — that tab
has no response to wait on. §4.4 requires each portrait to appear as it lands.
Both need project state to be readable live.

Given that, a blocking handler would deliver the result by a *second* mechanism
duplicating what the client already receives, and its correctness would depend
on FastAPI's client-disconnect semantics, which differ between `def` and
`async def` handlers and vary by version. If a refresh cancels the handler,
every refresh strands a step — converting a rare failure into the common one,
in exactly the scenario §4.3 grades.

So: `POST /api/projects/{id}/run` performs the transition, starts an in-process
task, and returns **202 immediately**. Costs: a module-level set holding task
references (Python garbage-collects a task nobody holds) and
`try/except/finally` so a raised exception becomes a recorded `FAILED` rather
than a silent one. `google-genai` exposes `client.aio`, so calls await
natively.

No queue, no worker process, no broker. The detached task is the *smaller*
option, not the fancier one.

### 6.2 Resume-aware handlers, and crash behaviour

Each step handler does **only the work not already persisted**.

**They are resume-aware, not idempotent.** Skipping persisted work makes a retry
cheap and lossless within a step. It does not make the handler idempotent in the
strict sense: the Gemini call is an external side effect, and a call whose
response is lost to process death leaves nothing on disk, so a later
user-triggered retry genuinely repeats it. Calling these handlers "idempotent"
would overclaim the exact boundary §5.5 draws. Throughout this document and the
implementation plan they are **resume-aware** / **checkpoint-aware** /
**resumable**.

| Process dies… | On disk | On retry after restart |
|---|---|---|
| after the UPDATE, before any call | nothing | clean rerun |
| mid-Gemini-call | nothing | clean rerun; **one call's cost lost — unavoidable** |
| after portrait 1, before portrait 2 | portrait 1 saved | handler iterates only `portrait_path IS NULL` — portrait 1 is never regenerated |
| after all artifacts, before advancing `status` | everything saved | handler finds no work, advances `status` |
| between `status` and `step_state` | — | impossible; single UPDATE |

Row 3 delivers "never losing generated results". Row 4 makes a retry after a
near-complete step cheap. Both fall out of resumability-within-a-step rather
than being special-cased.

Process death is covered by `server_run_id`. In-process cancellation is covered
by §6.3. Neither leaves a permanently `RUNNING` step.

### 6.3 Cancellation

A detached task can be cancelled — most realistically at process shutdown, when
the event loop cancels outstanding tasks. Cancellation must not leave
`step_state='RUNNING'` stamped with the **current** `server_run_id`, because
`is_interrupted` is precisely a comparison against the current id. Such a row is
neither running nor recoverable, and no user action clears it. It is the one
shape of stuck-forever that `server_run_id` alone does not answer.

The rule, in the task wrapper:

```python
try:
    await handler(...)
    store.complete_step(project_id, run_id, next_status)
except asyncio.CancelledError:
    store.fail_step(project_id, run_id, "INTERNAL",
                    "The step was cancelled before it finished. Retry to run it again.")
    raise                       # re-raised, never swallowed
except Exception as exc:
    store.fail_step(project_id, run_id, code_for(exc), message_for(exc))
```

Three properties make this correct rather than hopeful:

- **`fail_step` is ownership-guarded.** Its `WHERE` clause is
  `id=:pid AND step_state='RUNNING' AND server_run_id=:run_id`. A task that no
  longer owns the run writes nothing, so a late cancellation cannot clobber a
  newer execution that legitimately took the step over.
- **The recovery write cannot itself be cancelled.** `store.*` is synchronous
  `sqlite3` (§4.4) containing no `await`, so it runs to completion inside the
  cancelled task. An `await`ing cleanup would be re-cancelled at its first
  suspension point and write nothing.
- **`CancelledError` is re-raised**, so `asyncio` still observes the task as
  cancelled and shutdown is not stalled by a task that swallowed its own
  cancellation.

The result is `(S, FAILED)` — an ordinary retryable state, reached through the
ordinary Retry command. No durable worker, no reaper task, no timeout sweep.

---

## 7. Gemini interaction design

### 7.1 Two chains, never crossed

```
TEXT chain  (text model)
  book+document ──▶ style ──▶ characters ──────────────▶ chapters
     step 1          step 1     step 2                    step 4
                                     (step 3 does not touch this chain)

IMAGE chain (image model)
  seed(style+rules) ──▶ portrait₁ ──▶ portrait₂ ──▶ chapter-seed ──▶ illustration₁
        step 3            step 3        step 3         step 5           step 5
```

Two columns hold the heads. The text head advances on steps 1, 2 and 4 only —
step 4 chains off the *characters* interaction, which is exactly what the head
is after step 2, so no history table is needed.

Notebook cell 34 is a bare `# TODO: try using the last interaction` — Google has
not validated chaining an image call off a text interaction. Neither do we.

The operative meaning of a head column: **`NULL` means "no usable chain — build
this step's call standalone."**

`book_file_uri` is deliberately **not** stored. It would have no reader: steps
2–5 reach the book through the chain, and any reconstruction re-uploads to get a
fresh URI.

### 7.2 Per step

| Step | Calls | Persisted |
|---|---|---|
| 1 Style | upload `book.txt` → seed interaction `[intro text, document]` → style interaction (generate one, or acknowledge the user's) | `style_text`, text head, `status` |
| 2 Characters | one structured call off the text head | ≤2 character rows, new text head, `status` |
| 3 Portraits | seed the image chain **if the head is NULL**; then one call per character with `portrait_path IS NULL` | **per character: file, then path + new image head in one transaction** |
| 4 Chapters | one structured call off the text head | ≤1 chapter row, new text head, `status` |
| 5 Illustrations | chapter-mode seed; then one call per chapter with `illustration_path IS NULL` | **per chapter: file, then path + new image head in one transaction** |

**The image chain head must advance in the same transaction as the artifact it
produced.** Save the file without the head and a retry re-seeds a chain that has
diverged from the images on disk; save the head without the file and the handler
skips a portrait it does not have. Coupling them makes step 3 resumable
mid-flight — which is simultaneously §4.4's per-item progress requirement and
§4.3's never-lose-results requirement, satisfied by one decision.

The book upload and the book interaction happen **lazily inside step 1**, not at
project creation. Otherwise a project left unopened for a day begins life with a
dead file URI, and project creation could fail on a Gemini error. Creation is a
pure local write that cannot fail.

Style text is stored raw. The notebook's `Follow this style: "…"` wrapper is
applied when building an image prompt — formatting belongs at the point of use.

### 7.3 Seed interactions are never persisted alone

The chain head is written only alongside an artifact. Consequences:

- Crash after step 3's seed, before portrait 1 → head still `NULL`; the retry
  re-seeds. One cheap call lost.
- Crash after step 5's chapter-mode seed → head still points at portrait 2; the
  retry re-seeds.

Re-seeding is always safe because a seed's content derives entirely from
persisted data (style text plus the static rules string), so it reconstructs
identically. The trade is explicit: one wasted seed call on an unlucky crash,
versus a persisted phase and a column existing only for that window.

### 7.4 The caps — where each layer acts

Three layers, guarding two distinct failures:

| Layer | Acts on | Purpose |
|---|---|---|
| Prompt: "at most 2 adult characters" | what Gemini is asked for | Keeps Gemini's own context free of characters we would discard — **this is what stops step 4 referencing a character with no portrait** |
| Schema `maxItems: 2` + Pydantic validation | what Gemini returns | Structural contract on the response |
| Generation-loop bound | what we persisted | The application's **cost invariant** |

**Strict validation wins. There is no silent slicing anywhere in the parse
path.** A structured response violating the schema is `INVALID_OUTPUT`: step
`FAILED`, nothing persisted, user retries.

The generation-loop bound is a *different* mechanism guarding a *different*
failure: it iterates at most 2 characters / 1 chapter **regardless of how many
rows exist**, so persisted state exceeding the cap cannot produce extra image
calls. It is not input validation — no request supplies character data; the only
user inputs anywhere are the book text at creation and the optional style string
at step 1. It is where the server discharges §03's obligation, and it is tested
by seeding rows directly rather than through Gemini output.

### 7.5 Provider-side expiry: fail, then reconstruct on retry

Free-tier interaction retention is 1 day; File API uploads last 48 hours. The
application never assumes either. It assumes only its own disk.

> **Persisted application state and artifacts are authoritative. Gemini
> interaction and file handles are ephemeral context handles, not pipeline
> truth.**

Detecting `InteractionNotFound` does exactly two things, in one write:

1. sets the step `FAILED` with a message saying the conversation context
   expired, and
2. **nulls the head of the chain that raised.**

Nothing else happens in that run. No second call, no automatic reconstruction —
so *"retries are user-triggered only"* holds without qualification.

The user then clicks Retry. The step finds a `NULL` head, which already means
"build standalone". **One call**, constructed from minimum persisted context,
and its interaction becomes the new head:

| Step | Standalone call | Book |
|---|---|---|
| 2 | `[intro + style_text + instruction, document]` + schema | re-uploaded |
| 3 | seed carrying style + rules, plus any existing portraits as references | none |
| 4 | `[intro + style_text + character prompts + instruction, document]` + schema | re-uploaded |
| 5 | portrait files as reference images + chapter prompt + rules as `system_instruction` — notebook cells 39–44 | none |

**Which portraits step 5 sends as references.** Notebook cell 44 selects
reference images by `chapter["characters"]`, which requires the chapter schema
from the *bonus* cells 40–41 (`name`, `prompt`, `characters`). The required path
— cell 37 — returns `Prompt` (`name`, `prompt`) only. Rather than carry a second
schema and a `characters` column to serve one recovery branch, step 5's
standalone call sends **every persisted portrait for the project** as a
reference. At a hard cap of 2 characters those two sets are the same set in
practice, and the chapter prompt already names its characters — cell 37 asks it
to. Cell 44's selection logic exists to narrow a larger cast; we do not have one.

There is no recovery code path. Step 3's `NULL` head is *already* its first-run
branch, and the other three reuse it. Steps 2 and 4 genuinely require the book —
their prompts read it ("use the descriptions from the book", "for each chapters
of the book") — so no persisted artifact substitutes. Steps 3 and 5 never
re-upload it under any circumstance.

### 7.6 Cost invariant, restated

> **Normal operation: the book reaches Gemini exactly once, during step 1.**
> Steps 2–5 reference it through the interaction chain and never re-send it.
>
> **Exception: provider-side context expiry.** If the chain is gone when step 2
> or 4 runs, that step re-uploads the book once, as part of an explicitly
> user-triggered run.

### 7.7 Structured output and retries

`response_format={"type":"text","mime_type":"application/json","schema": …}`,
validated against a Pydantic model before anything is persisted.

**Where the JSON is read from is settled by the spike, not asserted here.** The
notebook is internally inconsistent: cell 32 reads `interaction.output_text`;
cells 37 and 41 read `interaction.steps[-1].content[0].text`. Both surfaces
exist on `Interaction` in the installed SDK (`google-genai` 2.18.0 declares
`output_text`, `output_image`, `output_audio`, `output_video` as fields). The
spike (§14 step 2) determines which is populated for a structured response; the
client then uses one accessor everywhere, with the other as a documented
fallback.

**Retries are disabled deliberately, as an override.** `attempts` counts the
original request, so `attempts=1` means the call is made once and never
repeated. We need a `HttpRetryOptions` object anyway — and within one,
`attempts` defaults to **5** (one call plus four retries) when left unset. The
client is therefore constructed with `HttpRetryOptions(attempts=1)` plus a
per-request timeout, because §4.3 forbids auto-retry loops (§2, contradiction
5). There is no retry loop anywhere in our code. **The only path that re-invokes
Gemini is a user `POST`.**

Because the field name reads like a retry count when it is really a total, the
test asserts the **resulting attempt count** the SDK computes, not the value we
passed in.

`service_tier` is a notebook parameter serving its paid sections; the app omits
it and takes the SDK default. The spike confirms this on a free-tier key.

Model IDs come from environment variables and are recorded in `DECISIONS.md`.
The notebook as run carries `IMAGE_MODEL_ID = "gemini-2.5-flash-image"` and
`GEMINI_MODEL_ID = "gemini-3.1-flash-lite"`; those are the `.env.example`
defaults, re-checked against AI Studio at implementation time since the
notebook's own list has already turned over once.

### 7.8 Prompt constants, taken from the notebook

Prompt wording is not invented. Every instruction below is the notebook's, used
verbatim except where the assessment requires a change, and each lives as a
named constant rather than an inline string:

| Constant | Source | Change from the notebook |
|---|---|---|
| `BOOK_INTRO` | cell 27 — *"Here's a book, to illustrate using Nano Banana. Don't say anything for now, instructions will follow."* | none |
| `RULES` | cell 23 — the no-text / no-cover-page / family-friendly / single-image instructions | none. Referred to throughout as "the rules string"; passed as a message in the chained path and as `system_instruction` in the standalone path, following the notebook |
| `STYLE_GENERATE` / `STYLE_ACKNOWLEDGE` | cell 30, both branches | none |
| `CHARACTERS_INSTRUCTION` | cell 32 | **adds "at most 2"** — §03 moves the cap onto the list (§2, contradiction 1) |
| `IMAGE_SEED` | cell 35 | book title comes from the project rather than being hardcoded |
| `PORTRAIT_INSTRUCTION` | cell 35 | none |
| `CHAPTERS_INSTRUCTION` | cell 37 | **adds "at most 1"** |
| `CHAPTER_SEED` | cell 38 | none |
| `ILLUSTRATION_INSTRUCTION` | cell 38 | none |
| `STYLE_WRAPPER` | cell 30 — `Follow this style: "…"` | applied when building an image prompt, not when persisting (§7.2) |

Keeping these as named constants is what makes acceptance row 4 — asserting the
exact call and context sequence — a test of *the notebook's pipeline* rather
than of our paraphrase of it.

---

## 8. API surface

Ten REST endpoints plus one WebSocket endpoint.

| Method | Path | Notes |
|---|---|---|
| `POST` | `/api/session` | `{name, email}` → normalize (trim, lowercase), validate, upsert user, set cookie |
| `GET` | `/api/session` | current user or 401 — restores identity on app boot |
| `DELETE` | `/api/session` | sign out |
| `POST` | `/api/projects` | `{title, book_text}` → writes `book.txt`, inserts `CREATED/IDLE`. **No Gemini call** |
| `GET` | `/api/projects` | list rows: `id, title, created_at, status, current_step, display_status, needs_attention, is_interrupted` |
| `GET` | `/api/projects/{id}` | full project view — the REST bootstrap read |
| `GET` | `/api/projects/{id}/book` | book text, from disk |
| `POST` | `/api/projects/{id}/run` | `{step, style?}` → **202** + project view, or **409** + project view |
| `GET` | `/api/projects/{id}/characters/{cid}/portrait` | artifact bytes, ownership-checked |
| `GET` | `/api/projects/{id}/chapters/{cid}/illustration` | artifact bytes, ownership-checked |
| `WS` | `/ws/projects/{id}` | live project state (§9) |

**`.txt` upload is a frontend input mode, not an endpoint.** The browser reads
the file with `FileReader.readAsText` and posts its contents through the same
`{title, book_text}` call as pasted text — what the demo does at line 355. §4.4
requires both input *modes*, not two transports.

**Why the book has its own endpoint.** It can be 230 KB and it never changes;
keeping it out of the project view keeps every state payload small. It is
fetched lazily on first expand.

**Why `run` carries the step name.** A tab left open on "Run Characters" while
another tab already advanced would otherwise silently run Portraits. The server
compares the asserted step against the derived current step and returns 409 if
they disagree.

**Why 409 returns the full project view.** The losing caller renders the truth
immediately with no follow-up fetch and no error screen. A rejected duplicate
should look like a UI that was already correct.

### 8.1 Session identity

An opaque 256-bit random token in an `HttpOnly`, `SameSite=Lax` cookie; the row
lives in SQLite so a restart does not sign anyone out. There is no
authentication mechanism in this system — §4.1 specifies name and email only.

A JWT would be *more* code here, not less: a signing library and key management
added to avoid a table in a database we are already running. The opaque token is
also revocable.

`SameSite=Lax` is sufficient **for this threat model** — a local-only app,
explicitly not deployed per §08, with no cross-origin surface. It is not a
general CSRF guarantee and would need revisiting if hosted.

Signing in with an existing email **updates the stored name** to what was typed,
matching the demo (line 347). Silently keeping the old name would show something
different from what the user just entered.

### 8.2 Status codes, and two kinds of error

`200` read · `201` create · `202` run accepted · `400` validation · `401` no
session · **`404` for another user's project** (not 403 — do not confirm
existence) · `409` wrong step or already running · `500` unexpected.

| | Describes | Where it appears |
|---|---|---|
| HTTP status + `{error: {code, message}}` | *the request* — accepted, valid, permitted | the response to that request |
| `project.failure: {code, message} \| null` | *the pipeline* — a step's outcome | inside a **200** project view |

`POST /run` returns 202 and is finished; a Gemini failure thirty seconds later
cannot retroactively become a 500. It surfaces as a later project view with
`step_state: "FAILED"`. The DTO field is named `failure`, not `error`, so the
two cannot be conflated in the client.

---

## 9. Realtime

REST remains the durable, bootstrap and command interface. WebSocket carries
**live backend → frontend project state only**. It is not a second state store,
not a command bus, and not a second pipeline state machine.

Realtime is §08 bonus scope, chosen deliberately. A disconnected client may
display **stale** state; WebSocket failure never corrupts durable pipeline
state, and truth is restored by reconnect or a manual REST refresh.

### 9.1 Protocol

One message type, one shape:

```json
{ "type": "project.state", "project": <the same project view REST returns> }
```

We transport authoritative state, not events. Snapshot/update labels and FIFO
history add no correctness value, so there are none. The client replaces its
project state wholesale on every message. There is no client-side event-sourced
state machine.

### 9.2 Connection lifecycle

| Phase | Behaviour |
|---|---|
| connect | resolve the existing session cookie → `user_id`; verify the project belongs to that user; then `accept()` |
| reject | close with **`WS_1008_POLICY_VIOLATION`** — the same code whether the project is missing or belongs to someone else, matching REST's policy of not confirming existence |
| subscribe | register, then read, then offer the current view — atomically (§9.3) |
| updates | `project.state` after every committed pipeline mutation |
| disconnect | remove from the registry; nothing else happens anywhere |

The handshake reuses the existing `HttpOnly` cookie, which the browser sends on
a same-origin upgrade. **No query-string token** — that would put a session
credential into URLs, proxy logs and browser history, and would be a second
authentication mechanism where there is none.

### 9.3 The subscription race, and the invariants that close it

A client reads `GET /api/projects/{id}`, state changes, *then* the socket
connects. The change would be lost. The unconditional state message on subscribe
closes this — but only with the right ordering. The naive implementation
reopens the race one level down:

```
read S1 → [state → S2, fan-out; this connection is not registered yet]
        → register(conn) → send S1        # client believes S1 forever
```

Two invariants, stated as ours rather than inferred from a dependency:

> **R1 — `RealtimeRegistry` is event-loop-confined.** Created on, mutated by and
> read from the event loop thread only; never from a worker thread,
> `run_in_executor`, or `asyncio.to_thread`. Enforced by asserting
> `asyncio.get_running_loop()` matches the loop captured at construction.
>
> **R2 — subscribe is one synchronous critical section.**
> `register → build view → offer` contains no `await`, and is marked as a
> critical section in code.

An earlier version of this argument rested on SQLite's driver *happening* to be
synchronous. That is an incidental property of a dependency, not a guarantee we
own. If R2 is ever violated the symptom is a first render that is stale and
*stays* stale until the next update — quiet, plausible, and hard to trace.

### 9.4 Fan-out, backpressure, and the failure that must not propagate

**A closed browser tab must not fail a pipeline step.** Two structural defences,
both required:

- **Broadcast happens strictly after `COMMIT`**, outside the transaction, so
  even total broadcaster failure leaves durable state correct.
- **The broadcaster never sends.** It writes to a per-connection slot; a
  separate writer task per connection performs the send. A dead socket fails in
  *its own* task. The pipeline's call is a non-awaiting, non-raising handoff.

The per-connection buffer is a **single coalescing latest-state slot**: a newer
state replaces an unsent one. Bounded by construction, no drop policy to design,
lossless with respect to final state. The only cost is intermediate frames — a
stalled client may see two portraits arrive together rather than in sequence.
Degraded granularity, never wrong state.

This is a concrete argument for state-transport over event-transport: with
`portrait_finished`-style events every message is essential, requiring a real
queue, a drop policy and gap detection. Whole-state messages are individually
disposable.

### 9.5 Why no broker

Redis Pub/Sub, RabbitMQ and Kafka exist to fan out *across processes*. We ship
one. An in-memory registry is not a simplification of a broker — at this
topology it is the complete correct implementation. A broker would add a network
hop, a serialization boundary and a failure mode to move a message between two
objects in the same interpreter.

**The single-process constraint now binds for two independent reasons** —
`server_run_id` required it, and the registry requires it. Written down so it
cannot be relaxed by satisfying only one.

### 9.6 Interaction with `server_run_id`

Heartbeat-based stuck detection is rejected. A heartbeat proves a *socket* is
alive; it says nothing about whether the *task* is. `server_run_id` answers the
actual question exactly.

WebSocket composes pleasantly with it: on server death the socket closes
immediately, so the client reconnects at once instead of waiting for a poll —
but `is_interrupted` is still derived server-side from `server_run_id` and
delivered in the reconnect state. The close is a prompt; the state is the truth.

The client never inspects close codes to recognise a restart. The rule is
binary: **policy rejection (1008) → do not reconnect, consult
`GET /api/session`. Any other disconnect → reconnect, receive state, continue.**

### 9.7 Development proxy

Vite proxies both `/api` and `/ws` to FastAPI, so the browser is same-origin and
the `HttpOnly` cookie works with no CORS middleware, no `credentials: 'include'`
and no preflight:

```js
server: { proxy: {
  '/api': { target: 'http://127.0.0.1:8000' },
  '/ws':  { target: 'ws://127.0.0.1:8000', ws: true },
}}
```

`ws: true` is easy to omit, and its absence fails the handshake in a way that
reads like an application bug. Noted in `README.md`.

### 9.8 Project list stays REST

Realtime's value is watching a long-running step, which happens on the detail
screen. A list subscription would need a user-scoped channel with a different
lifecycle and auth scope, for a screen you must navigate away from to act.
Staleness there is an explicit non-goal.

---

## 10. Frontend

### 10.1 The governing rule

> **No client-owned mirror or advancement of pipeline state.** Pipeline state
> comes exclusively from the server project view.

The frontend legitimately owns form inputs, the disclosure open flag, request
status for every fetch, connection status, and the separately-fetched book text.
It owns no pipeline state, performs no optimistic advance, and implements no
step ordering. This is the sharpest divergence from the demo, which owns
pipeline state in `localStorage` and drives progress with `setTimeout`.

### 10.2 Three independent state axes

**Pipeline state** (from the server) · **request state** (loading / error per
fetch) · **connection state** (connecting / live / reconnecting).

Conflating the third with the first is the frontend's version of §9.4's hazard:
a dropped socket means *we may be behind*, never *the step failed*. **A transport
failure must never make the frontend invent a `FAILED` pipeline state.** The
response is always to refetch backend truth.

### 10.3 Components

`App` (routing, session bootstrap) · `SignIn` · `ProjectList` / `ProjectRow` /
`EmptyState` · `NewProject` · `ProjectDetail` · `Stepper` · `StepPanel` ·
`BookTextPanel` · `StylePanel` · `CharacterCard` / `ChapterCard` ·
`useProjectSocket`.

Three §4.4 requirements are named explicitly here so they cannot be lost between
the design and the plan:

- **`NewProject` implements both input paths.** A textarea for pasted text, and a
  `.txt` file input read with `FileReader.readAsText` whose contents populate
  that same textarea (`app-demo.html:355`). Both submit through the one
  `{title, book_text}` call (§8). Validation requires a non-empty title and
  non-empty book text whichever path produced it.
- **`ProjectRow` renders the five-step progress indicator.** Five segments,
  filled for each completed step, derived from `status` via the ordered `STEPS`
  list exactly as the stepper is (`app-demo.html:556`). It sits alongside the
  status pill and the `needs_attention` warning affordance (§4.2).
- **Loading, error and empty states exist on every screen.** `ProjectList`:
  skeleton while the fetch is in flight, `EmptyState` at zero projects, error
  state with a retry affordance. `ProjectDetail`: skeleton while `GET` is in
  flight, error state with a retry affordance, and `BookTextPanel`'s own loading
  state on first expand.

Sign out lives in the app shell header, present on every authenticated screen.

### 10.4 StepPanel states

| State | Condition | Shows |
|---|---|---|
| **Ready** | `IDLE`, not done | "Ready for the next step: *Characters*" + `Generate Characters`. Step 1 also renders the optional style input |
| **Running** | `RUNNING`, not interrupted | spinner + a caption **naming the step** + disabled button. §4.3 forbids a bare spinner |
| **Failed** | `FAILED` | `failure.message`, `Retry Characters`, and explicit reassurance that completed steps are untouched |
| **Interrupted** | `is_interrupted` | "This step was interrupted when the server restarted" + `Retry Characters` |
| **Complete** | `status == DONE` | all five done; nothing regenerates |

Four of the five do not exist in the demo.

### 10.5 Frontend flow

| Event | Behaviour |
|---|---|
| open project | `GET /api/projects/{id}` → render |
| connect socket | connection → `connecting` |
| **first message** | replace project state; connection → `live` |
| `project.state` | replace project state wholesale |
| portrait appears | falls out of the above — no special handling |
| `POST /run` → **202** | replace view with returned state; **no local transition** |
| `POST /run` → **409** | replace view with returned state; render current truth, not an error |
| `POST /run` → other | transient banner, refetch, **no pipeline transition** |
| socket closes abnormally | connection → `reconnecting`; **project state untouched** |
| reconnect | server sends state unconditionally — **reconnect and first connect are one code path** |
| close 1008 | **do not reconnect**; consult `GET /api/session` |
| interrupted project | arrives as ordinary state carrying `is_interrupted` |

Reconnect uses a bounded backoff — 500 ms → 1 s → 2 s → 5 s cap. No jitter:
jitter de-synchronizes a thundering herd, and we have one or two tabs on
localhost.

Connection status is visible but quiet — a small indicator and a manual
`Refresh` when not live.

**Rejected: polling as a socket fallback.** It would ship both mechanisms and
require testing both, to cover a case that reconnect already handles transiently
and that a misconfigured proxy exposes on the first run in development.

### 10.6 Book text

A permanent disclosure panel, not a modal. A modal costs a focus trap, Escape
handling, focus return, `aria-modal` and scroll locking, and buys the ability to
*cover* the pipeline UI — the opposite of useful, since the book is reference
material you read *beside* the prompts derived from it. A panel always present
in the layout also cannot have its affordance vanish the way the demo's does.

The project view carries a short `book_excerpt` so the collapsed panel has
something to show; the full text loads lazily on first expand.

### 10.7 Validation, accessibility, styling

**Validation** — frontend for UX, backend as the trust boundary, both:
non-empty name and valid email syntax; non-empty title and book text. **No
invented size limits** — the File API's documented ceiling is 2 GB, which we
come nowhere near, so there is no constraint to encode.

**Accessibility** — `aria-live` on the status line so a step transition is
announced; visible focus rings; `prefers-reduced-motion` respected **including
the spinner**, which becomes a static indicator because the caption already
carries the meaning in text (the demo exempts it at line 241). Fixed
aspect-ratio art slots so an image landing never reflows the page — §07 grades
"no layout jumps".

**Styling** — the `:root` token block from `app-demo.html` (lines 13–56) is the
**assessment's visual baseline**; the file's claim that it comes from a Gradion
design system is unverifiable in a mock. Reusing it is justified because it is
the shipped floor we are measured against.

---

## 11. Testing strategy

### 11.1 Acceptance matrix

Every row is executable evidence against `FakeGeminiClient`.

| # | Use case | Brief | Key assertion |
|---|---|---|---|
| 1 | New email creates a user; returning email loads their projects | §4.1 | second sign-in returns existing projects; stored name updated |
| 2 | Project creation persists | §4.2 | row + `book.txt` on disk; **zero Gemini calls** |
| 3 | Five user actions → `DONE` | §4.3, §5.4 | happy path completes; each step needs its own `POST` |
| 4 | **The exact call and context sequence** | §07 | one upload; style chained off book; characters off style *with* `response_format`; image seed **unchained**; portrait₂ off portrait₁; chapters off the *characters* interaction; chapter-seed off portrait₂ |
| 5 | A completed step never starts the next | §4.3 | after step *N* commits, no further calls until the next `POST` |
| 6 | Both step-1 style paths | §03, §4.4 | generated-style and user-supplied-style produce different first calls, same resulting state shape |
| 7 | Wrong / future step rejected | §4.3 | 409 + **zero Gemini calls** |
| 8 | **Genuine concurrent `/run` race** | §4.3 | two requests via `asyncio.gather` on an ASGI transport → exactly one 202, one 409, **one execution** |
| 9 | Refresh / new client mid-`RUNNING` | §4.3 | sees existing in-flight state; **zero new calls** |
| 10 | Sign out, sign in again, same email | §4.1, §4.3 | same project, same results, nothing regenerated |
| 11 | Late-step failure preserves earlier outputs | §4.3 | style, characters, portraits intact; retry calls **only** the failed step |
| 12 | Partial portrait failure | §4.3, §4.4 | portrait 1 persisted; retry calls character 1 **zero** times, character 2 once |
| 13 | Stale `server_run_id` | §4.3 | `is_interrupted` surfaces through the API; recovery is the **normal Retry command** |
| 14 | Task cancellation | §4.3, §6.3 | a cancelled task leaves the step `FAILED` and re-raises `CancelledError` — never `RUNNING` under the current `server_run_id`; a cancellation arriving after the run was taken over writes nothing |
| 15 | Context expiry → Retry | §4.3 | expiry run records **one** call then `FAILED` with the head nulled; retry makes a standalone call, **re-uploading the book for steps 2/4 and not for 3/5** |
| 16 | Ownership isolation | §4.1 | user B blocked from the project view, the artifact bytes, **and** the socket (1008) |
| 17 | Reconnect and the GET→subscribe gap | §9.3 | state changing between `GET` and subscribe still reaches the client; reconnect yields current persisted truth |

Row 4 is what proves §07's "you implemented *its* pipeline, not an imagined
simplification". Row 8 is a real race on the event loop, not two sequential
calls.

### 11.2 `FakeGeminiClient`

Same protocol as the real client, selected by environment variable at app
construction:

- **Deterministic outputs** — fixed style text, 2 characters, 1 chapter, a tiny
  valid PNG.
- **A gate** — holds a call open so a test can observe `RUNNING`. Released
  explicitly, never slept through.
- **Injectable failure** — fail on call *N*; raise `InteractionNotFound`; return
  a schema-violating response.
- **A call recorder** — model, input shape, `previous_interaction_id`,
  `response_format`.

The recorder turns rows 4, 5, 7, 8, 9, 11, 12 and 15 into assertions rather than
claims. `server_run_id` is injectable, making row 13 three lines with no clock
and no killed process — a second dividend from choosing a run id over a
threshold.

### 11.3 Focused coverage underneath

**Backend units** — the transition function against real SQLite (mocking the
database would test nothing, since the guarantee lives in its atomicity); step
handlers against the fake (within-step resumability, the `NULL`-head standalone
branch, structured-output validation); **the generation-loop bound tested by
seeding 3 character rows directly** and asserting exactly 2 portrait calls,
never through Gemini output.

**Frontend components**

| Test | Rule it protects |
|---|---|
| StepPanel × 5 | Ready / Running *naming the step* / Failed / Interrupted / Complete |
| Card derivation `[null, null]` | first → *generating*, second → *pending* |
| Card derivation `[path, null]` | first → *ready*, second → *generating* |
| **Detail loading** | skeleton while `GET` is in flight — §5.4 names loading |
| Book panel loading | lazy fetch on first expand shows a loading state |
| ProjectList empty + populated | §5.4 names empty |
| **ProjectList progress indicator** | five segments, filled count equals completed steps — §4.4 |
| **NewProject paste path** | pasted text submits `{title, book_text}` — §4.4 |
| **NewProject `.txt` path** | a chosen file's contents populate the text and submit identically — §4.4 |
| Socket disconnected | staleness indicator, **not** a pipeline failure |
| `/run` → 409 | renders current state, not an error |
| Fetch failure | retry affordance; **no invented `FAILED`** |

Card fixtures use two characters, never three — production is capped at two and
a three-character fixture would encode a state that cannot exist.

**WebSocket** — FastAPI's `TestClient` provides an in-process WebSocket client,
so subscribe, per-item update, two-client fan-out, ownership rejection,
disconnect/reconnect, **"a connection whose send raises does not affect the
step"**, and **"two viewers on one running project cause zero extra Gemini
calls"** are all ordinary integration tests. No network harness, no E2E.

### 11.4 Deliberately not tested

Real Gemini in the suite (quota, non-determinism; §5.4 says mock) · browser E2E
(§5.4: not expected) · exhaustive component coverage (§5.4: "pick a couple that
matter") · prompt and image **quality**, which belongs to UAT · SQLite itself ·
reconnect backoff *timing* · visual regression.

Every test maps to a named invariant. None exists to raise a number — §5.4 is
explicit that coverage is not the metric.

### 11.5 Manual UAT

Automated tests never touch Gemini, so these are the only evidence the real
integration works:

1. **One real five-step run** end to end against live Gemini, with the resulting
   portraits and illustration inspected — also the only check on prompt and
   image quality.
2. **One real interruption** — kill the backend mid-`RUNNING`, restart, confirm
   the project surfaces *Interrupted* and recovers through the normal Retry
   command with prior results intact.
3. **Visual and keyboard pass** — tab-through on every screen, focus visibility,
   reduced motion, responsive behaviour, and the **full book text reachable at
   every stage of the pipeline**.

Findings and screenshots go in `TESTING.md` alongside the automated report.

### 11.6 Commands

`./start.sh` runs uvicorn (**single worker**) and vite.
`./test.sh` runs pytest and vitest and prints to stdout — **it does not write the
committed report**, so ordinary runs never clobber evidence. The committed report
is captured once from a real final run (`./test.sh | tee test-report.txt`) and
referenced from `TESTING.md`.

---

## 12. Trade-offs accepted

| Trade-off | Cost accepted |
|---|---|
| Single process | No horizontal scale. **Required twice over** — by `server_run_id` and independently by the in-memory registry |
| Exactly-once not claimed across a crash | A user-triggered retry may repeat a call whose response was lost |
| Persisted state + artifacts authoritative; Gemini handles ephemeral | One extra book upload on the steps 2/4 recovery path |
| Coalescing latest-state slot | A stalled client may see two portraits arrive together |
| Project list not live | Its pill is stale until navigation |
| No `fsync` | Power-loss durability; future work |
| `SameSite=Lax` | Sufficient local-only; would need revisiting if hosted |
| Synchronous SQLite on the event loop | Microsecond blocking — invisible at one user (§4.4) |
| WebSocket disconnect | Screen may be stale until reconnect or manual refresh; durable state never affected |

---

## 13. Out of scope

Intentionally excluded from this submission:

- **Project deletion** — not a requirement.
- **Notebook sections 6–9** (Veo, Lyria, TTS, media mixing) — §03 excludes them
  from the required pipeline; §08 lists one later section as a bonus option.
- **Increased caps** — §03 makes 2/1 binding here; §08 offers changed caps as a
  bonus option.
- **Deployment** — §08 explicitly forbids hosting.
- **Passwords, OAuth, email verification** — §4.1.
- **Rate-limiting infrastructure** — §5.3 says none is required.
- **Retry/attempt history UI, sample public-domain books, CI** — §08 bonus, only
  if time remains after everything required.
- **Multi-worker / horizontal scale.**
- **Docker** — not needed; `README.md` says so per §5.5.

---

## 14. Implementation order

| # | Step |
|---|---|
| **0** | **Run notebook steps 1–5 in Colab** — mandatory per §03 and §09.1, before any app code |
| 1 | Repo skeleton, `start.sh`, `test.sh`, `.env.example`, `CLAUDE.md` — harness first, per §09.3 |
| **2** | **Gemini contract spike (throwaway)** — verify `output_text` vs `steps[-1].content[0].text`, image extraction (`output_image` vs walking `steps`), `maxItems` enforcement, multi-part input with `response_format`, `document` parts from a `files.upload` URI, `previous_interaction_id` chaining, `system_instruction` on a standalone image call, the one-attempt retry configuration, and omitting `service_tier`. `FakeGeminiClient` then models a **verified** contract |
| 3 | Schema, `store.py`, `read_project_view` |
| 4 | Transition function + tests — the core invariant, early |
| 5 | Identity and session REST |
| 6 | Projects REST — create, list, detail, book |
| 7 | `FakeGeminiClient` + step handlers |
| 8 | Pipeline, background execution, `/run` — **acceptance rows 1–15 green** |
| 9 | `realtime.py` + WebSocket endpoint + rows 16–17 |
| 10 | Real Gemini client — a leaf swap behind the protocol |
| 11 | Frontend shell, sign-in, list, new project |
| 12 | Frontend detail: stepper, step panel, cards |
| 13 | Frontend socket hook + connection state |
| 14 | Frontend component tests |
| 15 | Real Gemini run, manual UAT, `README` / `TESTING` / `DECISIONS` |

The whole backend is proven against the fake (step 8) before the real client
exists (step 10) — orchestration correctness costs zero quota, and Gemini becomes
a swap at a leaf rather than a dependency threaded through the design.

---

## 15. `DECISIONS.md` candidates

Not yet written, and not to be written as though already decided. These are
genuine moments from the design session. §2.1 asks for 4–6 entries; §2.3
requires **at least 3** places where AI output was wrong, unsafe or
overcomplicated.

Recommended six:

1. **SQLite over JSON files** — required storage topic. The useful angle:
   `sqlite3` is stdlib, so "a DB is over-engineering" does not apply — no server,
   container or dependency.
2. **Pipeline progress model** — required progress topic. *Note:* the
   assessment's own sample entry is "Separate `status` and `step_state`", so
   writing it in those terms will read as copied from the prompt. Write it around
   what is actually ours: `current_step` derived rather than stored, artifacts as
   checkpoints that never speak for the milestone, and the rule that `status` is
   never recomputed from artifacts on read.
3. **One conditional `UPDATE` enforcing three invariants** — required
   duplicate-execution topic. Includes the removed attempt guard.
4. **`server_run_id` over a time threshold** — with the single-process cost, and
   the testability dividend that was not the reason for choosing it.
5. **Persisted state authoritative; Gemini handles ephemeral** — expiry fails the
   step, retry reconstructs standalone.
6. **WebSocket over polling** — a deliberate §08 bonus, reversing the earlier
   recommendation that polling was the smallest sufficient mechanism.

**The ≥3 AI overrides** are drawn from actual corrections, not from entry 6 —
that was a deliberate product choice, not a case of AI being wrong, and counting
it would be the manufactured evidence §2.3 penalizes. The real ones:

- **The unnecessary attempt guard** — a mechanism introduced and justified by a
  hypothetical; removed once the reachable scenarios were enumerated.
- **Eager automatic rehydration** — an in-run context rebuild that would have
  re-invoked Gemini automatically, muddying "retries are user-triggered only". Its
  removal collapsed a recovery subsystem into a branch each step already had.
- **The exactly-once overclaim** — the no-duplicate-calls guarantee was written
  without its crash boundary.
- **Invented input limits** — 2 MB book text and 500-character style, tied to
  nothing.
- **The realtime DTO dependency contradiction** — `realtime.py` was claimed
  dependency-free while building a project DTO.
- **The false claim about the notebook's retry configuration** — the design
  argued that no-auto-retry "matches the notebook" and was therefore not an
  override at all. `note.md` shows the `attempts=1` in our notebook copy is our
  own edit made while running it, so the design was citing our own change back
  at us as independent justification. Rewritten as what it is: a deliberate
  override required by §4.3. **This entry has a second layer worth writing up.**
  The first correction over-corrected — it claimed the SDK "retries 5 times by
  default", which reads as *any* `google-genai` client silently retrying. Going
  to the source showed that is not true: with no `retry_options` the SDK uses
  its `tenacity.stop_after_attempt(1)` *"never retry"* strategy. The 5 only
  applies **inside** an `HttpRetryOptions` object with `attempts` unset — which
  is precisely the notebook's shape, since it sets delays and status codes. Both
  the original claim and its first fix were plausible-sounding and unverified;
  only reading `_api_client.py` settled it.
- **The invented fourth status pill** — a *Needs attention* pill was added for
  failed and interrupted projects, replacing §4.4's named vocabulary in the one
  place the assessment is explicit about wording. The real concern was kept as a
  separate `needs_attention` flag rendered beside the pill.
- **"Idempotent" step handlers** — the handlers were described as idempotent
  when they are only resume-aware; the word contradicted the crash boundary the
  same document had just drawn in §5.5.

That is more override material than §2.3's minimum of three. Pick the strongest
and write them in full rather than listing all of them thinly.

Close the file with the required one-more-day answer.

---

## 16. Residual risks

- **Image-model free-tier limits are account-specific.** Check AI Studio before
  the demo run; the published rate-limit page no longer carries a table.
- **Free-tier interaction retention is 1 day.** A project resumed the next day
  takes the standalone path. That is designed for, and worth demonstrating
  deliberately rather than discovering live.
- **Model IDs turn over.** The notebook's own list has already changed once; pin
  from AI Studio at implementation time.
- **The `ws: true` Vite proxy flag** is easy to omit and fails in a way that
  looks like an application bug.
- **`app-demo.html`'s book-text bug is fixed, not reproduced** — a visible,
  deliberate divergence from the reference, worth a sentence in `README.md`.
