# Gemini `interactions` contract — spike findings

Spike run: 2026-08-14 (fix round 2 — see history for the aborted first run and the
fix-round-1 re-run), `backend/spike_gemini.py` (deleted after this document was
written), against the real Gemini API using the key in the worktree's `.env`.
`google-genai` 2.18.1, models `gemini-3.1-flash-lite` (text) and
`gemini-2.5-flash-image` (image) — both confirmed present via `client.models.list()`.
Q9's remaining gap (well-formed-but-nonexistent `previous_interaction_id`) was closed
by a coordinator-run diagnostic against the same key, recorded below with the rest.

**Headline: the `interactions` endpoint works.** The first run (superseded) aborted on
its very first call and wrongly concluded the endpoint might be access-gated. It was
not. This run wraps every probe (Q1–Q9) in its own try/except with up to 3 attempts,
retrying only on a 403, and got a clean, complete answer to all nine questions. The 403s
turned out to be a known intermittent provider flake, not a contract question — see
below.

**This per-probe retry is a spike-only diagnostic, used to characterise a known flake.
It is not, and must not become, a pattern in production code. Assessment §4.3 forbids
automatic retries absolutely; a transient `403` in production surfaces as
`GEMINI_ERROR` → step `FAILED` → user-triggered Retry, which is the recovery path the
design already specifies.**

## Intermittent-403 finding (first-class result)

Across the 11 API calls this run made (file upload excluded — it uses a different,
non-`interactions` code path and never 403'd), **4 raised `PermissionDeniedError` (403,
`permission_denied`) — a 36.4% rate** — and every single one succeeded when the
*identical* request was retried immediately after:

- `Q5 seed (text+document)`: attempt 1 → 403, attempt 2 → 403, attempt 3 → OK
- `Q1/Q2/Q6 style (chained)`: attempt 1 → 403, attempt 2 → OK
- `Q3/Q7 structured`: attempt 1 → 403, attempt 2 → OK
- `Q4 image`, `Q8 standalone image`, `Q9`: all OK/answered on attempt 1, no 403 seen for
  these three in this run (small sample — not evidence the flake is text-only)

This matches the coordinator's independent diagnostic (six identical bare-document
calls → `403, 403, OK, OK, OK, OK`). The failure is **non-deterministic, has no
warm-up pattern, and is unrelated to payload shape or content type** — it hit a
document-input call, a plain chained call, and a structured-output call alike, and
resolved on retry every time with no other change. Nothing about the first run's
specific failure was special; it was simply the unlucky first sample of this same
flake.

## Raw output

```
Q5 upload uri: https://generativelanguage.googleapis.com/v1beta/files/9a8xaiwsdv7v
Q5 seed (text+document): attempt 1/3 raised google.genai._gaos.lib.compat_errors.PermissionDeniedError (status_code=403): Error code: 403 - {'error': {'message': 'The caller does not have permission', 'code': 'permission_denied'}}
Q5 seed (text+document): attempt 2/3 raised google.genai._gaos.lib.compat_errors.PermissionDeniedError (status_code=403): Error code: 403 - {'error': {'message': 'The caller does not have permission', 'code': 'permission_denied'}}
Q5 seed (text+document): attempt 3/3 OK
Q5 multipart+document accepted. id: v1_Chd3Q05fYXNQSU44RG1vc1VQMmJ5TC1RNBIXd0NOX2FzUElOOERtb3NVUDJieUwtUTQ
Q5 name-form uri: attempt 1/3 raised google.genai._gaos.lib.compat_errors.BadRequestError (status_code=400): Error code: 400 - {'error': {'message': 'Unsupported file URI type: files/9a8xaiwsdv7v. File URI must be a File API (e.g. https://generativelanguage.googleapis.com/files/<id>), Youtube (e.g. https://w
Q5 name-form uri REJECTED: google.genai._gaos.lib.compat_errors.BadRequestError | Error code: 400 - {'error': {'message': 'Unsupported file URI type: files/9a8xaiwsdv7v. File URI must be a File API (e.g. https://generativelanguage.googleapis.com/files/<id>), Youtube (e.g. https://w
Q1/Q2/Q6 style (chained): attempt 1/3 raised google.genai._gaos.lib.compat_errors.PermissionDeniedError (status_code=403): Error code: 403 - {'error': {'message': 'The caller does not have permission', 'code': 'permission_denied'}}
Q1/Q2/Q6 style (chained): attempt 2/3 OK
Q1 output_text populated: True
Q1 output_text: 'Oil painting in the style of Beatrix Potter.'
Q2 steps[-1].content[0].text: 'Oil painting in the style of Beatrix Potter.'
Q6 chaining accepted, id: v1_Chd3Q05fYXNQSU44RG1vc1VQMmJ5TC1RNBIXeHlOX2F2emxGWmFSdnIwUHk2YlAtUTQ
Q3/Q7 structured: attempt 1/3 raised google.genai._gaos.lib.compat_errors.PermissionDeniedError (status_code=403): Error code: 403 - {'error': {'message': 'The caller does not have permission', 'code': 'permission_denied'}}
Q3/Q7 structured: attempt 2/3 OK
Q3 structured output_text populated: True
Q7 asked for <= 2, returned 2 items
Q7 raw items: [{"name": "Toad", "prompt": "An anthropomorphic, eccentric toad wearing a jaunty tweed driving cap and a silk scarf, sitting behind the wheel of a vintage brass-trimmed 1900s motor-car, expressive and grandiose face, oil painting style in the vein of Beatrix Potter."}, {"name": "Rat", "prompt": "An ...
Q4 image: attempt 1/3 OK
Q4a output_image is not None: True
Q4a mime_type: image/png | decoded bytes: 2014478
Q4b steps-walk found an image: True
Q8 standalone image: attempt 1/3 OK
Q8 standalone image+system_instruction accepted: v1_Chc3aU5fYXB6X0JfSy12cjBQamJ1ZDZRcxIXN2lOX2Fwel9CX0stdnIwUGpidWQ2UXM
Q9 unknown previous_interaction_id: attempt 1/3 raised google.genai._gaos.lib.compat_errors.BadRequestError (status_code=400): Error code: 400 - {'error': {'message': 'Request contains an invalid argument.', 'code': 'invalid_request'}}
Q9 raises: google.genai._gaos.lib.compat_errors.BadRequestError
Q9 message: Error code: 400 - {'error': {'message': 'Request contains an invalid argument.', 'code': 'invalid_request'}}
Q9 code attrs: None 400
Q9 body: {'error': {'message': 'Request contains an invalid argument.', 'code': 'invalid_request'}}
SUMMARY: 11 total attempts across all probes, 4 were 403 permission_denied (36.4%)
```

Exit code: 0. All nine questions answered.

## Q1 — Is `interaction.output_text` populated for a plain text call?

**Yes, confirmed.** `style.output_text` (a chained call, `previous_interaction_id`
set) was truthy and held the actual model response:

`Q1 output_text populated: True`
`Q1 output_text: 'Oil painting in the style of Beatrix Potter.'`

## Q2 — Does `steps[-1].content[0].text` return the same string as `output_text`?

**Yes, confirmed — identical string.**

`Q2 steps[-1].content[0].text: 'Oil painting in the style of Beatrix Potter.'`

## Q3 — Is `output_text` populated when `response_format` is set (structured output)?

**Yes, confirmed.**

`Q3 structured output_text populated: True`

`structured.output_text` held the raw JSON string; `json.loads()` on it succeeded
directly (see Q7 for the parsed content).

## Q4 — Does `output_image` carry base64 `data` + `mime_type`, and does the steps-walk find the same image?

**Yes to both, confirmed.**

`Q4a output_image is not None: True`
`Q4a mime_type: image/png | decoded bytes: 2014478`
`Q4b steps-walk found an image: True`

`img.output_image.mime_type` is `image/png`; `img.output_image.data` is a base64
string that decodes to ~2 MB of real PNG bytes. The `steps`-walk fallback
(`for step in reversed(img.steps): if step.type == "model_output": ...`) independently
locates a `content.type == "image"` entry too — both accessors agree an image is
present.

## Q5 — Is `[text, document]` input with a `files.upload` URI accepted?

**Yes, confirmed — the notebook's mechanic works.** `files.upload()` → the full
`https://generativelanguage.googleapis.com/v1beta/files/<id>` URI → a
`{"type": "document", "uri": <that URI>}` content part in `interactions.create` is
accepted and read correctly (the model's later structured-output answer, Q7, correctly
named "Toad" and "Rat" — characters from the uploaded book text, confirming the
document was actually read, not just accepted-and-ignored).

`Q5 upload uri: https://generativelanguage.googleapis.com/v1beta/files/9a8xaiwsdv7v`
`Q5 multipart+document accepted. id: v1_Chd3Q05fYXNQSU44RG1vc1VQMmJ5TC1RNBIXd0NOX2FzUElOOERtb3NVUDJieUwtUTQ`

The first attempt at this exact call 403'd twice before succeeding on attempt 3/3 —
see the intermittent-403 finding above; this is the flake, not a contract answer.

**Sub-finding — URI form matters.** The **name-form** URI (`files/9a8xaiwsdv7v`, what
`file.name` returns) is **rejected with 400**, not accepted:

`Q5 name-form uri: attempt 1/3 raised ... BadRequestError (status_code=400): Error code: 400 - {'error': {'message': 'Unsupported file URI type: files/9a8xaiwsdv7v. File URI must be a File API (e.g. https://generativelanguage.googleapis.com/files/<id>), ...`

The **full `https://...` URI** (`file.uri`, not `file.name`) is required. `mime_type`
on the document part makes no observable difference either way (per the coordinator's
independent diagnostic — not separately re-tested in this run to conserve calls).

## Q6 — Is `previous_interaction_id` accepted, and does it chain?

**Yes, confirmed.** The `style` call passed `previous_interaction_id=seed.id` and
succeeded, returning its own new `id`:

`Q6 chaining accepted, id: v1_Chd3Q05fYXNQSU44RG1vc1VQMmJ5TC1RNBIXeHlOX2F2emxGWmFSdnIwUHk2YlAtUTQ`

That the style answer ("Oil painting in the style of Beatrix Potter") is a sensible
continuation of the seed turn ("here's a book, don't say anything yet") is consistent
with real context-chaining, not just an accepted-but-ignored parameter.

## Q7 — Is `maxItems` honoured, or advisory?

**Observed: the model returned exactly 2 items for `maxItems: 2` — it did not exceed
the cap in this run.**

`Q7 asked for <= 2, returned 2 items`

This single non-violating observation does **not** prove `maxItems` is a hard,
server-enforced constraint — it's equally consistent with the model simply choosing to
stop at 2 on its own (there were plausibly more than 2 "adult characters" available in
the seeded snippet, but the snippet was short and repetitive, so this isn't a strong
stress test of the cap). No overflow was observed, but none was forced either.
**Decision unchanged from spec §7.4's existing assumption: the generation-loop bound
and strict `len(items) > cap` validation are the only real enforcement** — this run
gives no reason to relax that, and every reason to keep it, since a single
non-violating sample cannot establish server-side guarantees either way.

## Q8 — Is a standalone image call with inline `image` parts + `system_instruction` accepted?

**Yes, confirmed.**

`Q8 standalone image+system_instruction accepted: v1_Chc3aU5fYXB6X0JfSy12cjBQamJ1ZDZRcxIXN2lOX2Fwel9CX0stdnIwUGpidWQ2UXM`

Inline `{"type": "image", "data": <base64>, "mime_type": "image/png"}` plus a top-level
`system_instruction` string, with no `previous_interaction_id`, is accepted by the
image model on the first attempt (no 403 seen for this probe in this run).

## Q9 — What exception type/message does an unknown `previous_interaction_id` raise?

**Confirmed, both cases tested — and the answer is that the provider does not
distinguish them.**

Real interaction ids observed in this spike are opaque `v1_...` tokens, e.g.
`v1_Chc4Q1JfYXRQTkY4RG1vc1VQMmJ5TC1RNBIXOENSX2F0UE5GOERtb3NVUDJieUwtUTQ` — nothing like
the brief's literal test string `"interactions/does-not-exist"`. A follow-up
diagnostic took a real id of that shape, mutated its tail to produce a **well-formed
but nonexistent** id (the case an actually-expired chain head would produce in
production), and called with it. Result: **byte-identical to the malformed-string
case.** Both raise:

`class       : google.genai._gaos.lib.compat_errors.BadRequestError`
`status_code : 400`
`.code attr  : <none>`
`body        : {'error': {'message': 'Request contains an invalid argument.', 'code': 'invalid_request'}}`
`error.code  : 'invalid_request'`

(The malformed-string variant's raw transcript, from the second spike run, for
reference: `Q9 raises: google.genai._gaos.lib.compat_errors.BadRequestError` /
`Q9 message: Error code: 400 - {'error': {'message': 'Request contains an invalid
argument.', 'code': 'invalid_request'}}` / `Q9 code attrs: None 400` / `Q9 body:
{'error': {'message': 'Request contains an invalid argument.', 'code':
'invalid_request'}}`.)

Facts confirmed directly from these exception objects, for **either** a malformed or a
well-formed-but-nonexistent `previous_interaction_id`:
- **Class:** `BadRequestError`, **module:** `google.genai._gaos.lib.compat_errors`
- **`.status_code`: `400`** (an int)
- **`.code` attribute does not exist on this hierarchy at all** —
  `getattr(exc, "code", None)` is always `None`. This is not a quirk of this one call;
  it kills any predicate shape that reads `exc.code`, including the one the plan
  currently assumes for Task 34 (see the flagged warning below).
- **`.body`** is a parsed dict: `{"error": {"message": "Request contains an invalid
  argument.", "code": "invalid_request"}}`. The provider's `code` string
  (`invalid_request`) lives inside `.body["error"]["code"]`, not as a top-level
  attribute.

**The finding: the provider gives no way to distinguish an expired/nonexistent
interaction from a malformed request.** Both a real-but-expired-shaped id and a
garbage string produce the exact same `400 invalid_request`. There is no server-side
signal — no distinct status code, no distinct `code` string, no distinguishing message
— that separates "this id was never valid" from "this id used to be valid and no
longer is." This is now a confirmed result, not an inference from source code.

### The discriminator has to be ours, not the provider's

Because the provider collapses both cases into the same error, `RealGeminiClient`
cannot detect expiry from the exception alone. It must use context the client already
has — whether the failing request carried a `previous_interaction_id` at all — as part
of the predicate:

```python
status = getattr(exc, "status_code", None)
body   = getattr(exc, "body", None)
code   = body.get("error", {}).get("code") if isinstance(body, dict) else None
if had_previous_interaction and (status == 404 or (status == 400 and code == "invalid_request")):
    raise InteractionNotFound(...)
```

Two points make this correct:

1. **`had_previous_interaction` gates the whole check.** The client only treats a `400
   invalid_request` as an expiry when the failing request actually carried a
   `previous_interaction_id`. A `400` on a call with no `previous_interaction_id`
   cannot be an expired-chain-head error by definition — it's some other bad-request
   condition — and stays a plain `GeminiError`. Without this gate, `400
   invalid_request` is far too generic a signal to key off of alone (it's the same
   error a genuinely malformed request of any kind would produce, as confirmed above).
2. **The `404` branch is kept even though it was never observed.** `compat_errors.py`'s
   `_STATUS_MAP` defines `NotFoundError` for status 404, and the provider may start
   using it for this exact case in the future (either by changing behaviour, or simply
   because a single confirmed sample of the well-formed-nonexistent case is not proof
   no code path ever returns 404). Keeping the branch costs nothing and avoids silently
   breaking `InteractionNotFound` detection if the provider's behaviour shifts.

### Asymmetry — why the predicate leans toward detecting expiry

A **false positive** (treating a non-expiry `400` as `InteractionNotFound` when it
wasn't) is gated almost entirely away by the `had_previous_interaction` check above;
in the residual case where it still happens, the cost is bounded and cheap — the
recovery path re-uploads the book and starts a fresh chain, i.e. one extra book upload
on a step-2/4 retry. A **false negative** (failing to recognise a real expiry, treating
it as a generic `GeminiError` instead) is much worse: the user is stranded in a
failure loop with `Retry` re-sending the same dead `previous_interaction_id` forever,
with no path back to a working chain, because nothing ever triggers the
reconstruction (re-upload-and-restart) logic that `InteractionNotFound` is meant to
kick off. That asymmetry — cheap, self-correcting false positive vs. permanent,
unrecoverable false negative — is why the predicate above is written to lean toward
detecting expiry rather than toward precision.

### Flagged warning for Task 34

**The plan's current design for `_translate` keys on `code == 404`, and its test
fixture constructs `google.genai.errors.ClientError(404, ...)`.** Both of those are
wrong against what this spike actually observed:

- `code == 404` doesn't exist as a checkable attribute — confirmed above, `.code` is
  `None` on every exception in this hierarchy. The real signal is `.status_code`, and
  even that was never observed to be `404` for this case — it was `400` both times
  tested.
- `google.genai.errors.ClientError` is a **different class entirely** from
  `google.genai._gaos.lib.compat_errors.BadRequestError`, which is what the real
  `client.aio.interactions.create()` call in this spike actually raised, twice,
  confirmed live. A test fixture built from `google.genai.errors.ClientError(404, ...)`
  would never construct an object `isinstance`-compatible with, or attribute-shaped
  like, what production actually throws.

**Net effect if this ships as currently planned: `InteractionNotFound` would never
fire against the real API, while a test suite built around the wrong fixture class and
the wrong status code would keep passing anyway** — a false-green test suite backing a
recovery path that silently doesn't work. Task 34 must use the predicate given above
(`status_code`/`body["error"]["code"]`/`had_previous_interaction`, not `.code`) and
build its test fixture from `google.genai._gaos.lib.compat_errors.BadRequestError` (and
ideally `NotFoundError` too, for the unexercised-but-kept 404 branch), not
`google.genai.errors.ClientError`.

## Decisions

- **`create_text` (Q1/Q2):** use `interaction.output_text` as the primary accessor
  (confirmed populated and correct). Fall back to `interaction.steps[-1].content[0].text`
  only defensively — confirmed to return an identical string in this run, but treat
  `output_text` as authoritative.
- **`create_structured` (Q3):** use `interaction.output_text`, confirmed populated for a
  `response_format`-bearing call; `json.loads()` directly on it. Same fallback as above.
- **`create_image` (Q4):** use `interaction.output_image.data` (base64) +
  `interaction.output_image.mime_type`, confirmed populated (`image/png`, ~2 MB
  decoded). Fall back to the steps-walk
  (`step.type == "model_output"` → `content.type == "image"`) only if `output_image`
  is `None` — confirmed both accessors agree when `output_image` is present.
- **`upload_book` (Q5):** `client.files.upload(file=...)` then pass **`file.uri`** (the
  full `https://generativelanguage.googleapis.com/...` form) as
  `{"type": "document", "uri": file.uri}` — this is the notebook's mechanic and it
  **works**, confirmed end-to-end including that the model actually reads the content.
  **Do not use `file.name`** (the short `files/<id>` form) — confirmed rejected with
  `400 BadRequestError`, message `"Unsupported file URI type: ..."`.
- **Chaining (Q6):** pass `previous_interaction_id=<prior interaction>.id` on every
  follow-up call in a project's turn sequence — confirmed accepted and confirmed to
  actually carry context forward (the chained answer was contextually appropriate, not
  just accepted-and-ignored).
- **`maxItems` cap (Q7):** treat as advisory / not provably server-enforced. Per spec
  §7.4: **the generation-loop bound and strict `len(items) > cap` validation are the
  only real enforcement.** This run observed exactly-at-cap, not over-cap, so it neither
  confirms nor refutes hard enforcement — the defensive validation stays regardless.
- **Standalone image + system_instruction (Q8):** confirmed accepted — inline
  `{"type": "image", "data": <base64>, "mime_type": ...}` content parts plus a
  top-level `system_instruction` string, no `previous_interaction_id` required. This is
  the shape Step 5's recovery path (redraw-from-reference) should use.
- **`InteractionNotFound` detection (Q9):** confirmed for both a **malformed**
  `previous_interaction_id` and a **well-formed-but-nonexistent** one (a real `v1_...`
  id with a mutated tail) — both raise byte-identical
  `google.genai._gaos.lib.compat_errors.BadRequestError`
  (module `google.genai._gaos.lib.compat_errors`), `.status_code == 400`, no `.code`
  attribute (always `None` — do not gate on it, and do not use
  `google.genai.errors.ClientError` as the fixture class, see the Q9 flagged warning),
  `.body == {"error": {"message": "Request contains an invalid argument.", "code":
  "invalid_request"}}`. **The provider gives no way to distinguish an expired
  interaction from a malformed request** — this is a confirmed finding, not an
  inference. Because of that, the client must supply the missing discriminator itself:
  only treat a `400 invalid_request` as `InteractionNotFound` when the failing request
  actually carried a `previous_interaction_id`; keep a `404`/`NotFoundError` branch
  too, even though never observed, since `compat_errors.py`'s `_STATUS_MAP` defines it
  and the provider may use it later. See the Q9 section above for the exact predicate,
  the two correctness points, and the false-positive/false-negative asymmetry that
  motivates leaning toward detecting expiry.
- **Intermittent 403 (new, cross-cutting):** `PermissionDeniedError` (403,
  `permission_denied`) is a transient provider flake on the `interactions` endpoint,
  observed at 36.4% (4/11) in this run across text and document-input calls,
  unrelated to payload shape, resolving on a bare retry of the identical request every
  time observed. **No automatic retry is added to `RealGeminiClient` or anywhere in
  production code** — assessment §4.3 forbids it absolutely. In production, a 403 here
  surfaces as `GEMINI_ERROR`, the affected step moves to `FAILED`, and recovery is the
  existing user-triggered Retry action — exactly the path the design already
  specifies. The per-probe retry used in this throwaway spike exists solely to
  characterise the flake's rate and confirm it's not a real contract answer; it must
  not be copied into `FakeGeminiClient` or `RealGeminiClient`.
