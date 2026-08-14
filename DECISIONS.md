# DECISIONS

Decisions only, per the brief. The design session that produced most of these
is in git history (`f6a045f` design spec, `d7ef080` seven pre-implementation
corrections, `8acf492` the retry-claim fix), so every entry here can be checked
against a real commit or a real design-doc change.

## SQLite, because it is stdlib — not because "a real DB"

Claude's first framing was the classic trade-off: a database versus JSON files,
with a DB carrying setup weight. I rejected the framing rather than either
option: `sqlite3` ships in Python's standard library, so the usual
"over-engineering" argument against a DB simply doesn't apply — no server, no
container, no dependency, one file next to the images. That gave me real
transactions for the concurrency rules instead of hand-built file locking,
which is where JSON files quietly get hard. What it cost: SQL statements
spread through a codebase this small, a schema to maintain, and a
single-writer story that would not survive a second server process — which the
design then leaned into rather than fought (see `server_run_id` below).

## How pipeline progress is modelled

Progress is one milestone column that only moves when a step *completes*,
plus per-item artifacts (character rows, image files) that land durably as
they are produced. Two rules keep those honest, and both were argued out
explicitly. First, `current_step` is derived from the milestone, never stored
— storing it would create two writers for one fact. Second, artifacts never
speak for the milestone: portrait one existing on disk does not make step 3
"further along", because a crashed step must resume from what actually
completed, not from what happens to be lying on disk. On read, nothing is
recomputed from artifacts; the milestone is the only authority the UI sees.
Cost: a resumed step has to re-derive its remaining work from artifacts
server-side (each handler carries that logic), and the read model does more
assembly than a single denormalised row would.

## Duplicate execution dies in one conditional UPDATE

Refresh mid-step, double-click, second tab, two racing requests — my rule was
that all of them must collapse into one mechanism, not one defence per
symptom. Starting a step is a single conditional `UPDATE` that succeeds only
from the right predecessor milestone with nothing running; the loser of any
race gets a 409 that carries current state. The same statement also enforces
step order and performs orphan takeover, so three invariants live in one
place. Claude had additionally designed an `attempt_id` fencing guard on
result writes; I made it enumerate the scenarios that guard could fire in, and
every reachable one was already covered (the table survives in the design
spec, §5.4). The guard was removed — with a written note of the two conditions
under which it must come back (a second process, or manual reset of a
running step). Cost: the protection is only as strong as that enumeration,
and the single-process assumption is now load-bearing in two places.

## `server_run_id` instead of a time threshold

For "stranded in progress after a server death", the obvious mechanism is the
demo's: a timer that declares a step stuck after N seconds (`app-demo.html`
uses 8). I chose process identity instead: each server start mints an id,
running steps are stamped with it, and a running row stamped by a dead
process *is* interrupted — no guessing, no threshold to tune against Gemini
image calls that legitimately run longer than any sensible timeout. The cost
is the single-process constraint (a second worker would see the first one's
live rows as orphans), which `start.sh` pins and the README states twice. It
also happened to make interruption trivially testable — the suite just opens
the same database under a different run id — but I want to be honest that
this was a dividend discovered later, not the reason for the choice.

## Persisted state is authoritative; Gemini's context is treated as ephemeral

Free-tier interaction retention is one day, shorter than this assessment's own
deadline, so expired conversation chains are a normal condition here, not an
edge case. The rule we landed on: everything needed to continue lives in our
database and files; the provider-side chain is an optimisation. When a chain
head turns out to be dead, the step fails once with a clear message, the dead
head is nulled, and the user's ordinary Retry rebuilds context from persisted
state — for the two steps that need the book, that means one extra upload.
Claude's first design recovered more eagerly (see the overrides below); the
final shape is deliberately duller. Model choice, recorded per §5.3:
`gemini-3.1-flash-lite` for text and `gemini-2.5-flash-image` for images —
the IDs the notebook was actually run with, re-checkable against AI Studio.

## WebSocket over polling — a reversal, and not an AI-was-wrong story

The earlier recommendation in the design conversation — polling is the
smallest mechanism that satisfies refresh-mid-step — was sound, and I want to
record clearly that reversing it was a product decision, not a correction.
The brief lists realtime step updates as bonus scope, and per-portrait
progress is exactly the experience that rewards push. The shape that made it
cheap enough to say yes: the socket carries whole project state (the same
payload REST serves), sent after every commit, so reconnect and first connect
are one code path and the client does no event sourcing. Cost: an in-memory
connection registry with an event-loop-confinement invariant, a second
transport to test, and one more system that assumes a single process.

---

# Where I overrode the AI

## The attempt guard that guarded nothing

Claude introduced an `attempt_id` fencing mechanism for step-result writes and
justified it with "a stale task could write over a newer attempt". Instead of
accepting or deleting it on instinct, I had it enumerate every scenario where
two tasks could overlap in this design. There were five candidates; each was
already prevented by the conditional transition, the detached-task model, or
process death itself. The mechanism was defending against a state the system
cannot reach — so it went, replaced by a paragraph in the spec saying exactly
when it must return. Overcomplication, caught by demanding the failure case
be named.

## Eager context rehydration

The first recovery design rebuilt Gemini context *inside* a running step: hit
an expired chain, re-upload, re-seed, continue — automatically. That is a
second Gemini invocation the user never asked for, in a submission whose cost
rules say retries are user-triggered only; it also duplicated, as a
subsystem, reconstruction logic each step handler already needed for its own
resume path. I cut it: expiry fails the step and nulls the dead head, full
stop. Recovery is the user's Retry, which walks into a branch the handler
already has. A recovery subsystem collapsed into an if-statement.

## The exactly-once overclaim

An early draft guaranteed "no duplicate Gemini calls", unqualified. That
claim is not achievable: if the process dies after a call is dispatched but
before its response is recorded, that money is spent, and a user-triggered
retry will repeat the call — no client can fix that without provider-side
idempotency keys. I made the design state its own boundary (spec §5.5):
guaranteed against ordinary concurrency, explicitly not guaranteed across a
crash window. Unsafe-by-overstatement, fixed by writing down what is *not*
promised.

## The retry claim I had to correct twice

The spec argued that disabling automatic retries "matches the notebook" and
was therefore not an override at all. It was citing me back at myself: the
`attempts=1` in the committed notebook is an edit I made while running it (the
correction is recorded in `d7ef080`). Rewritten as what it is — a deliberate
override required by the brief's cost rules. Then the correction
over-corrected: it claimed the SDK "retries 5 times by default", which reads
as every bare client silently retrying. Asked a direct question about the
attempt count, I finally read `_api_client.py` (`8acf492`): with no
`retry_options` at all, the SDK compiles its "never retry" strategy; the 5
appears only *inside* an `HttpRetryOptions` whose `attempts` is unset — which
is precisely the notebook's shape, making its one field load-bearing. Both
the claim and its first fix were plausible and unsourced; the test suite now
asserts the attempt count the SDK actually computes, plus a second test
pinning the default-5 footgun so it stays documented.

## The invented fourth status pill

For failed and interrupted projects, Claude added a *Needs attention* pill —
in the one place the brief is explicit about wording: exactly Draft,
In progress, Done. The concern was real, the vocabulary change was not the
answer. The pill set went back to the brief's three, and the concern became a
separate `needs_attention` flag rendered *beside* the pill (`d7ef080`).

## "Idempotent" step handlers

The design described the handlers as idempotent, three paragraphs after
drawing the crash boundary that makes the word false — a handler that died
after dispatching a Gemini call cannot replay for free. The handlers are
resume-aware: they skip work whose results are durably present and redo the
rest. The distinction matters because "idempotent" licenses careless re-runs
and the actual guarantee does not. Terminology fixed everywhere (`d7ef080`);
the plan's constraints section still carries the ban.

---

# If I had one more day

I would make the project list live. It is the one screen left that can show
stale truth — its status pill freezes until you navigate — and the machinery
to fix it already exists: the registry fans out whole-state messages
per commit, so a list subscription is one more consumer of an existing
stream, not a new system. I would take that over new features because this
design's core promise is that the UI never lies about pipeline state, and
the list is the last place the promise has a visible gap. Runner-up:
`fsync` on artifact and DB writes, closing the recorded power-loss gap.
