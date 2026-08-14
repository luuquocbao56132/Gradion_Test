# Gemini `interactions` contract — spike findings

Spike run: 2026-08-14 (fix round 1 — see history for the aborted first run),
`backend/spike_gemini.py` (deleted after this document was written), against the real
Gemini API using the key in the worktree's `.env`. `google-genai` 2.18.1, models
`gemini-3.1-flash-lite` (text) and `gemini-2.5-flash-image` (image) — both confirmed
present via `client.models.list()`.

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

**Observed live — and it is NOT what the first run's static-analysis inference
predicted.** Calling with `previous_interaction_id="interactions/does-not-exist"`
(the brief's literal string) raised, on the first attempt (no 403, so no retry
triggered):

`Q9 raises: google.genai._gaos.lib.compat_errors.BadRequestError`
`Q9 message: Error code: 400 - {'error': {'message': 'Request contains an invalid argument.', 'code': 'invalid_request'}}`
`Q9 code attrs: None 400`
`Q9 body: {'error': {'message': 'Request contains an invalid argument.', 'code': 'invalid_request'}}`

Facts confirmed directly from this exception object:
- **Class:** `BadRequestError`, **module:** `google.genai._gaos.lib.compat_errors`
- **`.status_code`: `400`** (an int)
- **`.code` attribute does not exist** — `getattr(exc, "code", None)` is `None`.
  There is no `code` field on the exception itself.
- **`.body`** is a parsed dict: `{"error": {"message": "Request contains an invalid
  argument.", "code": "invalid_request"}}`. The provider's `code` string
  (`invalid_request`) lives inside `.body["error"]["code"]`, not as a top-level
  attribute.

**Important caveat, stated plainly:** this is a `400 invalid_request`, not the `404
NotFoundError` the first run's source-code inference predicted. The likely reason:
real interaction IDs observed elsewhere in this same run look like
`v1_Chd3Q05fYXNQSU44RG1vc1VQMmJ5TC1RNBIXd0NOX2FzUElOOERtb3NVUDJieUwtUTQ` — an opaque
token in a specific format — while the brief's test string, `"interactions/does-not-exist"`,
does not match that shape at all. The API appears to validate the *format* of
`previous_interaction_id` before attempting a lookup, and rejects a malformed id with
400 `invalid_request` rather than ever reaching "not found" logic. **This spike did not
test a well-formed-but-nonexistent id** (e.g. a syntactically valid `v1_...` token that
was never issued, or one that has genuinely expired) — that would require either
minting a plausible-but-fake token of the right shape, or waiting for a real one to
expire, neither of which this run attempted. The `_STATUS_MAP` in
`compat_errors.py` (400→`BadRequestError`, 404→`NotFoundError`, confirmed accurate
against this run's 400 and the prior run's 403) means a well-formed-but-missing id
would plausibly still raise `NotFoundError` (404) rather than `BadRequestError` — but
that is now flagged explicitly as **unverified inference**, not fact, precisely because
this run proved the naive "just try a nonsense string" approach doesn't exercise that
path.

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
- **`InteractionNotFound` detection (Q9):** for a **malformed** `previous_interaction_id`
  (wrong shape, e.g. not the opaque `v1_...` token format the API issues), expect
  `google.genai._gaos.lib.compat_errors.BadRequestError`
  (module `google.genai._gaos.lib.compat_errors`), `.status_code == 400`, no `.code`
  attribute (always `None` — do not gate on it), `.body ==
  {"error": {"message": "Request contains an invalid argument.", "code":
  "invalid_request"}}`. **This spike could not exercise the well-formed-but-nonexistent
  case** (a syntactically valid token that was never issued or has expired) — for that
  case, fall back to the source-confirmed dispatch table in `compat_errors.py`
  (`_STATUS_MAP`) and expect `NotFoundError`, `.status_code == 404`, same "no `.code`
  attribute, `.body` is the parsed error dict" shape, **but treat that specific
  prediction as unverified** until confirmed against a real expired-interaction call.
  A defensible `RealGeminiClient` predicate for `InteractionNotFound` that covers both
  observed and predicted cases:
  `getattr(exc, "status_code", None) in (400, 404)` combined with a body-content check
  (`exc.body.get("error", {}).get("code") in ("invalid_request", "not_found")`) rather
  than relying on exception class alone, since the 400 case is now confirmed real and
  the 404 case is still only inferred.
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
