# Gemini `interactions` contract — spike findings

Spike run: 2026-08-14, single execution, `backend/spike_gemini.py` (deleted after this
document was written), against the real Gemini API using the key in the worktree's
`.env`. `google-genai` 2.18.1, models `gemini-3.1-flash-lite` (text) and
`gemini-2.5-flash-image` (image) — both confirmed present via `client.models.list()`
before this spike ran.

**Headline result: the spike did not complete.** The very first `interactions.create`
call raised `PermissionDeniedError` (HTTP 403) and the script has no try/except around
that call, so execution stopped there. Only Q5 has any live data (partial — the file
upload half succeeded, the interaction-create half failed). Q1–Q4 and Q6–Q9 were never
attempted. Per the task's cost-discipline rule, the spike was **not** retried, no
alternate model was substituted, and no second live call was made to narrow down the
cause. What follows records exactly what was observed, and — where source inspection of
the installed SDK (no API calls involved) gives useful, clearly-labelled context — what
that inspection suggests for later verification.

## Raw output

```
Q5 upload uri: https://generativelanguage.googleapis.com/v1beta/files/w31srpotqz4p
Traceback (most recent call last):
  File ".../google/genai/_gaos/interactions.py", line 2111, in create
    return await _speakeasy_parse_response(http_res)
  File ".../google/genai/_gaos/interactions.py", line 1887, in _speakeasy_parse_response
    raise errors.CreateInteractionClientError(
        response_data, http_res, http_res_text
    )
google.genai._gaos.errors.createinteraction.CreateInteractionClientError: The caller does not have permission

The above exception was the direct cause of the following exception:

Traceback (most recent call last):
  File "backend/spike_gemini.py", line 103, in <module>
    asyncio.run(main())
  ...
  File "backend/spike_gemini.py", line 25, in main
    seed = await client.aio.interactions.create(
  File ".../google/genai/_gaos/google_genai.py", line 427, in create
    response = await async_wrap_sdk_call(...)
  File ".../google/genai/_gaos/lib/compat_errors.py", line 378, in async_wrap_sdk_call
    return await fn(*args, **kwargs)
  File ".../google/genai/_gaos/interactions.py", line 2113, in create
    await response_helpers.raise_parse_error_async(...)
  File ".../google/genai/_gaos/utils/response_helpers.py", line 110, in raise_parse_error_async
    raise parse_error from exc
google.genai._gaos.lib.compat_errors.PermissionDeniedError: Error code: 403 - {'error': {'message': 'The caller does not have permission', 'code': 'permission_denied'}}
```

Exit code: 1. No other stdout lines were produced.

## Q1 — Is `interaction.output_text` populated for a plain text call?

**NOT ANSWERED.** The script never reached this call (line 48, the `style` interaction);
execution stopped at line 25. The dispatcher's pre-flight note said this was already
known to be `True` from an earlier manual check, but that check is not reproduced in
this document because it isn't something this spike itself observed — no pasted output
line exists for it here.

## Q2 — Does `steps[-1].content[0].text` return the same string as `output_text`?

**NOT ANSWERED.** Blocked for the same reason as Q1.

## Q3 — Is `output_text` populated when `response_format` is set (structured output)?

**NOT ANSWERED.** The `structured` call (line 56) was never reached.

## Q4 — Does `output_image` carry base64 `data` + `mime_type`, and does the steps-walk find the same image?

**NOT ANSWERED.** The image call (line 79) was never reached.

## Q5 — Is `[text, document]` input with a `files.upload` URI accepted?

**PARTIALLY ANSWERED — the upload succeeded, the interaction using it did not.**

- `client.files.upload(file="spike_book.txt")` succeeded synchronously and returned a
  usable URI:
  `Q5 upload uri: https://generativelanguage.googleapis.com/v1beta/files/w31srpotqz4p`
- The very next call, `client.aio.interactions.create(model=TEXT, input=[{"type":
  "text", ...}, {"type": "document", "uri": book.uri}])`, raised before returning:
  `google.genai._gaos.lib.compat_errors.PermissionDeniedError: Error code: 403 -
  {'error': {'message': 'The caller does not have permission', 'code':
  'permission_denied'}}`

Because this was the **first and only** `interactions.create` call this spike managed to
issue, the failure cannot be attributed specifically to the `document` content part —
it is equally consistent with the entire `interactions.create` endpoint being
unavailable to this API key/project regardless of payload shape. Distinguishing those
two explanations (endpoint-wide gate vs. document-part-specific rejection) requires one
more live call (e.g. a text-only `interactions.create`), which this spike does not make
per the "run once, do not retry" rule.

**Working hypothesis (unverified):** `interactions`/GAOS (the SDK module is literally
named `_gaos`) appears to be a distinct, newer surface from the classic
`generateContent`/`models.list` surface this key was already confirmed to work against.
A 403 with `code: permission_denied` on the first possible call to that surface is
consistent with the interactions API not being enabled/allowlisted for this key's
project, independent of model choice — `gemini-3.1-flash-lite` was already confirmed to
exist in this account's `models.list()` output, so this is not a wrong-model-ID problem.

## Q6 — Is `previous_interaction_id` accepted, and does it chain?

**NOT ANSWERED.** The `style` call that would have exercised chaining (line 48) was
never reached; `seed.id` was never obtained.

## Q7 — Is `maxItems` honoured, or advisory?

**NOT ANSWERED.** The structured-output call (line 56) was never reached, so no item
count was ever observed. Do not read the Decisions entry below as an empirical finding —
it is a defensive default carried over unverified from spec §7.4's existing assumption,
not something this spike measured.

## Q8 — Is a standalone image call with inline `image` parts + `system_instruction` accepted?

**NOT ANSWERED.** The `img` call it depends on (line 79) was never reached.

## Q9 — What exception type/message does an unknown `previous_interaction_id` raise?

**NOT ANSWERED live** — the try/except block at the end of the script (line 107) was
never reached because the script raised, unhandled, at line 25.

However, the Q5 failure is directly useful here because it exercises the exact same
error-translation path `InteractionNotFound` detection would rely on, and confirms that
path is real and active at runtime. Static inspection of the installed package (no
further API calls — this is reading local `.venv` source, not hitting the network)
shows `google/genai/_gaos/lib/compat_errors.py` maps HTTP status codes to exception
classes via a fixed table:

```python
_STATUS_MAP = {
    400: BadRequestError,
    401: AuthenticationError,
    403: PermissionDeniedError,
    404: NotFoundError,
    409: ConflictError,
    422: UnprocessableEntityError,
    429: RateLimitError,
}
```

The Q5 failure **observed live** — `PermissionDeniedError` for a 403 — matches this
table exactly, which gives reasonable (but not certain) confidence that a 404 from an
unknown `previous_interaction_id` would raise
`google.genai._gaos.lib.compat_errors.NotFoundError`, in module
`google.genai._gaos.lib.compat_errors`. From the same source:

- `NotFoundError` (like `PermissionDeniedError`) is an `APIStatusError`, which sets
  `self.status_code = response.status_code` in `__init__` — so `status_code` would be
  `404`, populated from a real int, not a string.
- There is **no `.code` attribute** anywhere in this exception hierarchy. The
  `getattr(exc, "code", None)` the brief's script probes would return `None` on any of
  these exceptions. The provider's `code: "..."` string (e.g. `permission_denied`,
  presumably `not_found` for a 404) lives one level down, inside `exc.body["error"]["code"]`
  — `.body` is set in `APIError.__init__` to the parsed JSON dict (confirmed live: the
  Q5 exception's rendered message shows `body` was parsed into
  `{'error': {'message': ..., 'code': 'permission_denied'}}`, not left as a raw string).
- `str(exc)` renders as `Error code: {status_code} - {body}` (from
  `_compose_message`), which is exactly the message format observed live for the Q5
  403. A 404 would render as `Error code: 404 - {'error': {'message': ..., 'code':
  'not_found'}}` or similar, by the same code path.

This is **inference from source, not an observed Q9 result**, and must be confirmed
with a real unknown-`previous_interaction_id` call once the Q5 access blocker is
resolved, before `InteractionNotFound` detection ships.

## Decisions

Because 8 of 9 questions were never exercised, most of these are **not** verified
contract decisions — they are the best available defaults, explicitly flagged as
unverified, to unblock later tasks' design without pretending this spike confirmed them.
None of Q1–Q4, Q6–Q8 should be treated as settled; re-run the spike (past the Q5
blocker) before `RealGeminiClient` ships.

- **Q1/Q2 (`create_text`)** — UNVERIFIED. Provisionally follow the dispatcher's
  pre-verified note (`output_text` is populated for a plain text call) and use
  `interaction.output_text` as the primary accessor, with `interaction.steps[-1]
  .content[0].text` as the documented fallback — but re-confirm both live before
  `RealGeminiClient.create_text` ships.
- **Q3 (`create_structured`)** — UNVERIFIED. Provisionally mirror Q1: try
  `interaction.output_text` first, fall back to `interaction.steps[-1].content[0].text`,
  same as the brief's script does (`raw = structured.output_text or
  structured.steps[-1].content[0].text`). Not confirmed.
- **Q4 (`create_image`)** — UNVERIFIED. Provisionally use `interaction.output_image`
  (expected shape: `.data` base64 string, `.mime_type` string) as primary, with the
  brief's steps-walk (`for step in reversed(interaction.steps): if step.type ==
  "model_output": for content in reversed(step.content): if content.type == "image"`)
  as the fallback. Not confirmed live.
- **Q5 (`upload_book`)** — **File upload itself is confirmed working**:
  `client.files.upload(file=...)` returns a real, usable `.uri`. Whether that URI can
  actually be consumed by `interactions.create` as a `{"type": "document", "uri":
  ...}` input part is **not confirmed** — the one attempt failed with a 403 that may or
  may not be document-specific (see Q5 section above). `RealGeminiClient.upload_book`
  should not assume the document-input path works until a text-only
  `interactions.create` call is separately confirmed to succeed against this
  key/project, and then the document-input call is retried in isolation.
- **Q6 (chaining via `previous_interaction_id`)** — UNVERIFIED. No chaining call ever
  executed; `seed.id` was never obtained.
- **Q7 (`maxItems`)** — UNVERIFIED, no live data. Per spec §7.4's existing assumption,
  and consistent with how JSON-Schema `maxItems` is generally treated as advisory by
  LLM structured-output implementations rather than as a hard server-side cap: *the
  generation-loop bound and strict `len(items) > cap` validation are the only real
  enforcement.* This is carried forward as a defensive design default, not as something
  this spike measured — confirm with a live structured-output call before relying on it.
- **Q8 (standalone image + `system_instruction`)** — UNVERIFIED. No such call executed.
- **Q9 (`InteractionNotFound` detection)** — UNVERIFIED live, but source-inferred with
  moderate confidence (see Q9 section): expect
  `google.genai._gaos.lib.compat_errors.NotFoundError` (module
  `google.genai._gaos.lib.compat_errors`), `.status_code == 404`, no usable `.code`
  attribute (always `None` on this hierarchy — do not gate on it), and `.body` as a
  parsed dict shaped `{"error": {"message": str, "code": str}}`. A reliable predicate,
  pending live confirmation:
  `isinstance(exc, google.genai._gaos.lib.compat_errors.NotFoundError)` (or, more
  defensively, `getattr(exc, "status_code", None) == 404`). Do not wire this into
  `InteractionNotFound` without first confirming it against a real unknown-id call.

## Overall blocker for Tasks 16, 17, 34

The account/key that passed `models.list()` and basic-model verification does **not**
appear to have working access to the `interactions` (`_gaos`) surface that this whole
contract — and therefore `FakeGeminiClient`/`RealGeminiClient`'s entire design — is
built on: the very first call to it returned `403 permission_denied`. This needs to be
resolved (correct project/API enablement, correct key, or a different SDK entry point)
and this spike re-run to completion before Tasks 16, 17, or 34 can rely on anything
beyond the Q5 file-upload result and the UNVERIFIED defaults above.
