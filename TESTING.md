# Testing

## Strategy

The engineering risk in this project is not the business logic — it is persistent
pipeline state: step ordering, preventing duplicate Gemini calls, resuming after a
crash, never losing generated work, and keeping Gemini's conversation context
coherent. The tests are aimed there.

Run everything with one command:

```
./test.sh
```

`test-report.txt` in this repository is the verbatim output of a real run of that
command, not a summary.

### What is tested, and why

**The conditional `UPDATE` (`backend/tests/test_store_transitions.py`).** One SQL
statement enforces three invariants at once — step ordering, at-most-one-execution,
and reclaiming a run left behind by a dead process. Everything else about
concurrency rests on it, so it is tested against **real SQLite**, never a mock: the
guarantee lives in the statement's atomicity, and a mocked database would prove
nothing.

**Resumability inside a step (`test_handlers.py`, `test_acceptance_recovery.py`).**
Each handler does only the work not already persisted. The tests cover a crash
between two portraits, a failure late in the pipeline, and a retry that must touch
only the failed step. Handlers are *resume-aware*, not idempotent — the distinction
matters and is documented in `DECISIONS.md`.

**The exact Gemini call and context sequence (`test_acceptance_happypath.py`).**
This is the test that proves we implemented the notebook's pipeline rather than a
plausible-looking simplification: one upload, style chained off the book,
characters off style with a response schema, the image chain seeded fresh rather
than crossed with the text chain, portrait 2 chained off portrait 1, chapters
chained off the *characters* interaction, chapter-mode seeded off portrait 2.

**Prompt fidelity.** The prompt constants were additionally verified against
`Book_illustration.ipynb` itself, not only against the unit tests — the tests
compare `prompts.py` to strings typed from the same source, so they could not catch
a transcription error made in both places. All nine constants appear verbatim in
their notebook cells; the characters and chapters instructions differ only by the
documented cap sentence.

**Concurrency for real (`test_acceptance_concurrency.py`).** Ten simultaneous
`POST /run` requests gathered on one event loop yield exactly one 202 and exactly
one execution — a genuine race against the conditional `UPDATE`, not two sequential
calls dressed up as one.

**Frontend component states.** Loading, error, empty, and the five `StepPanel`
states (Ready / Running / Failed / Interrupted / Complete). Four of those five have
no equivalent in `app-demo.html`, because the demo never fails. Also covered: the
409 path renders current truth rather than an error, and a transport failure never
invents a `FAILED` pipeline state.

**Accessibility (`frontend/src/__tests__/accessibility.test.tsx`).** Keyboard
reachability, focus activation on project rows, `aria-live` on the running status,
the spinner being decorative rather than the only signal, a text equivalent for the
progress indicator, descriptive alt text, and a reserved art slot so an image
landing cannot reflow the page.

### What is deliberately not tested, and why

- **Real Gemini in the automated suite.** It burns quota and is non-deterministic;
  the assessment explicitly says to mock it. Real-provider evidence comes from the
  manual UAT below instead.
- **Browser end-to-end.** Not expected, and the component tests plus the HTTP-level
  UAT cover the same ground more cheaply.
- **Exhaustive component coverage.** The brief asks for a couple that matter, not a
  coverage number. Coverage is not the metric here and no coverage gate is enforced.
- **Prompt and image *quality*.** No automated test can judge whether a portrait
  looks like Ratty. That belongs to UAT, below.
- **SQLite itself**, and **reconnect backoff timing** — testing a dependency's own
  behaviour, or wall-clock delays, buys nothing.
- **Visual regression.** Out of proportion at this scope.

### Why the fake is trustworthy

`FakeGeminiClient` reproduces a contract that was **verified against the real API**
before it was written (`docs/gemini-contract.md`), rather than an imagined one. It
records every call — model, prompt, `previous_interaction_id`, document URI,
schema, reference-image count — which is what turns "no duplicate calls" and "the
book is sent once" into assertions instead of prose. It can hold a call open so a
test can observe `RUNNING`, inject a provider failure, raise `InteractionNotFound`,
and return schema-violating output.

It is not perfect, and UAT proved it: see the seed-call defect below.

---

## Automated test report

Full output: [`test-report.txt`](test-report.txt) — captured from a real
`./test.sh` run, exit code 0.

```
=== backend (pytest) ===
239 passed

=== frontend (vitest) ===
Test Files  12 passed (12)
     Tests  87 passed (87)
```

Warnings are promoted to errors in `backend/pytest.ini`, with one narrowly-matched
ignore for a third-party Starlette deprecation. "Test output is pristine" is
therefore enforced by the suite rather than asserted by hand.

---

## Manual UAT

### 1. Live five-step run against real Gemini

Real key, `USE_FAKE_GEMINI=0`, the full 344,109-character *Wind in the Willows*
from Project Gutenberg.

| Step | Result | Time | Attempts |
|---|---|---|---|
| Style | ✅ generated | 19.8s | 1 |
| Characters | ✅ 2 adults: *Ratty (The Water Rat)*, *Mr. Badger* | 8.0s | 1 |
| Portraits | ✅ 2 real PNGs (2,439,361 and 2,074,638 bytes) | 66.3s | 4 |
| Chapters | ✅ 1 chapter: *Chapter I: The River Bank* | 7.0s | 1 |
| Illustrations | ⚠️ not obtained — see below | — | 6 failed |

The style Gemini invented for itself was *"Biological Steampunk Pastoralism"*, and
the character prompts were drawn from the book's own descriptions. The caps held:
exactly 2 characters and 1 chapter, enforced server-side.

Artifact bytes were served through the app's own API, ownership-checked:

```
GET /api/projects/{id}/characters/{cid}/portrait -> 200 image/png 2,439,361 bytes
GET /api/projects/{id}/characters/{cid}/portrait -> 200 image/png 2,074,638 bytes
GET /api/projects/{id}/book                      -> 200, 344,109 characters
```

### 2. A real defect that only UAT could find

Step 3 failed **deterministically** on the first live run: *"Gemini returned no
image for this prompt."*

The cause was the image-chain **seed** call. The pipeline seeds the image chain
with the style and the rules before drawing anything, and our client demanded an
image back from every image-model call. Probing the live API showed the seed
answers in prose:

```
SEED call     -> output_text: "Great! I understand the style and rules you're
                 looking for. Let's start with the first character..."
                 output_image: None
PORTRAIT call -> output_image: present
```

The notebook agrees: cell 35 keeps only the seed's `.id` and never extracts an
image from it. **The fake had hidden this** by returning a PNG for every image call
regardless of prompt, so all 239 backend tests passed against a contract the real
provider does not honour.

Fixed by marking seeding calls `expect_image=False`, teaching the fake to return no
image for them so it models reality, and adding four regression tests — two on the
real client (a seed tolerates a missing image; a *generating* call still requires
one) and two on the handlers (both seed sites are marked as seeds). After the fix,
portraits succeeded and produced the two real PNGs above.

### 3. Remaining provider flakiness (not a defect in this app)

Two independent intermittent behaviours were measured against this Gemini account:

- **403 `permission_denied` on file-backed calls**, ~30% of attempts. Identical
  request retried immediately succeeds. Measured during the contract spike at 4/11
  (36.4%), and again by a standalone probe: `403, 403, OK, OK, OK, OK`.
- **The image model answering in prose instead of drawing.** Intermittent and
  variable: portraits needed 4 attempts; illustrations failed 6/6 in one window
  while a standalone probe of the *same chained request* succeeded 3/3 minutes
  earlier. Forcing `response_modalities=["image"]` made no measurable difference
  (4/4 and 3/3 either way), so it is provider mood, not a request-shape problem.

Neither is auto-retried, deliberately: §4.3 forbids retry loops. Both surface as
`GEMINI_ERROR` → step `FAILED` → the user clicks **Retry**, with every earlier
result preserved. Step 5 is therefore *reachable but not yet observed end-to-end*
against the live provider — an honest gap, recorded rather than papered over. The
same step completes reliably against the fake, so the orchestration is proven; what
is unproven is this account's image model finishing a deep chain on demand.

### 4. Flow verification (fake provider, deterministic)

The remaining flows exercise **our** state machine, where the provider is
irrelevant and determinism is worth more than realism. Driven over HTTP against a
running server with `USE_FAKE_GEMINI=1`: **32 checks, 32 passed.**

| Flow | Verified |
|---|---|
| Five-step happy path | reaches `DONE`, pill `Done`, 5/5 steps, all images `ready` |
| Server-side caps | exactly 2 characters, 1 chapter |
| Book availability | readable at every stage **and** after `DONE` (344,109 chars) |
| Artifacts | served through our own API with `image/png` |
| Step ordering | a future step returns **409** and changes nothing |
| 409 semantics | carries the full current project state, not just an error |
| Duplicate suppression | two rapid runs → one 202, one 409 |
| Second tab | sees identical state |
| Sign out / sign in | project state **byte-identical** after re-login |
| Ownership | another user gets 404 on project, book **and** artifact bytes; empty list |
| Interrupted step | a `RUNNING` row from a dead process surfaces `is_interrupted`, flags needs-attention, keeps the pill vocabulary, and is reclaimed by the ordinary **Retry** |
| Failure recovery | style and characters preserved; retry advances past the failed step |
| WebSocket | subscribe returns `project.state` immediately; payload identical to REST; pushes completion; another user is rejected |

The mid-run refresh check could not observe `RUNNING` with the fake because steps
complete in ~20 ms. It is covered two other ways: the automated suite gates the
fake open in-process to observe `RUNNING`, and the live run above showed in-flight
state across 20–66 second steps.

### 5. Accessibility and keyboard

Covered by automated tests (see above): keyboard reachability and activation,
`aria-live` announcements, decorative spinner, text equivalent for the progress
indicator, alt text, and a reserved art slot preventing reflow.

### 6. Not yet performed — requires a human at a browser

This environment has no browser automation, so the following were **not** done and
are not claimed:

- Visual pass at 1280px and 380px
- Confirming no layout jump when an image lands, in a real browser
- Visible focus rings under real focus-visible behaviour
- `prefers-reduced-motion` with the OS setting enabled
- Watching portraits appear one at a time in the UI during a live run

To perform them:

1. Set `USE_FAKE_GEMINI=1` in `.env` (instant steps, no quota), then `./start.sh`
   and open <http://localhost:5173>.
2. Sign in, create a project by pasting text **and** again with a `.txt` file, and
   run all five steps. Watch the two portrait cards: the first should flip to
   *generating* while the second stays *pending*, then swap — with no page jump as
   each image lands.
3. Resize to 380px wide. Check nothing scrolls horizontally and the stepper labels
   collapse rather than overflow.
4. Tab through every screen without touching the mouse. Confirm a visible focus
   ring on each control and that the whole flow is operable from the keyboard.
5. Enable the OS "reduce motion" setting and reload. The spinner should stop
   animating while the caption still names the running step.
6. Set `USE_FAKE_GEMINI=0` and repeat step 2 once against the real provider to see
   genuine artwork, allowing for the retries described above.
