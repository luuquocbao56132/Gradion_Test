# Engineering Decisions

This file records the decisions that materially changed how I built the project. It is intentionally not a work log; the git history, design spec and implementation plan show how the work progressed.

## 1. The notebook defines Gemini mechanics; the assessment defines application behaviour

One of my first questions was how literally I should integrate Google's notebook into the application. I needed to call Gemini for real, but I did not want to copy notebook cells into the backend without understanding what part of them was actually the contract.

I ended up treating the notebook as the source of truth for Gemini mechanics: uploading the book, interaction chaining, structured output, image generation and reuse of image context. The assessment remains the higher authority for application behaviour. Every pipeline step still requires an explicit user action, characters are restricted to adults with a maximum of two, chapters are capped at one, and the application must not automatically retry Gemini calls.

The cost of this decision is that the Gemini adapter is not a blind copy of the notebook. The differences have to be understood, documented and tested.

For the final implementation I use `gemini-3.1-flash-lite` for text and `gemini-2.5-flash-image` for image generation. The IDs remain configurable through environment variables because model availability changes over time.

## 2. Gemini context is a continuation handle, not durable application state

Another early question was whether a Gemini "session" had to stay alive for the lifetime of a project. The notebook chains interactions so the full book does not need to be sent again for every step, which is useful, but I did not want project correctness to depend on how long Google retains an interaction or uploaded file.

The final rule is that SQLite state and local artifacts are the durable application truth. Gemini interaction IDs are only continuation handles. In the normal flow the book is uploaded once and later text steps reuse the interaction chain. Completed style, characters, portraits, chapters and illustrations remain valid even if the provider-side context eventually disappears.

Claude initially proposed rebuilding expired context automatically inside the same execution. I pushed back on that because rebuilding context would itself call Gemini again, while the assessment says retries must be user-triggered. The final behaviour is simpler: the current step fails once, the user sees the failure, and only an explicit Retry reconstructs the minimum context needed for that step from persisted data.

The cost is that some recovery paths may need to upload the book again, and a retry after a process crash can repeat a provider request whose result was lost before persistence. I prefer stating that boundary explicitly rather than claiming exactly-once behaviour that the external API cannot guarantee.

## 3. The backend owns the pipeline; a separate worker system is unnecessary here

From the beginning I expected pipeline state to live on the backend rather than in browser state. The part I was less certain about was execution: Gemini calls can take tens of seconds, so I considered whether the backend needed a worker service or queue to keep those calls alive.

We eventually kept execution inside the FastAPI process as a detached asynchronous task. Before a task starts, the backend persists that the step is running. During image steps it persists each completed artifact incrementally, and when the task finishes it persists success or failure before notifying the frontend.

A separate Celery-style worker, Redis queue or durable job system would give stronger process isolation, but I could not find a requirement in this local single-user topology that justified that cost. If the backend process dies, the task dies too; `server_run_id` lets the restarted process recognise that the old `RUNNING` state is interrupted and expose the normal Retry action.

The backend is therefore the only pipeline authority. The frontend may own forms, loading state and connection state, but it never decides that a pipeline step has advanced.

I chose FastAPI and React/Vite mainly because they are familiar tools and let me spend the limited assessment time on the pipeline rather than on the framework. SQLite fits the same principle: it gives me atomic state transitions without introducing another service. Book text and generated images remain on the local filesystem as required by the brief.

The main cost is an intentional single-process constraint. This is not the architecture I would use unchanged for a horizontally scaled service, but it is the smallest one I found that satisfies the actual assessment.

## 4. One guarded SQLite transition protects each expensive user action

The duplicate-call requirement changed how I thought about the state model. A double-click, refresh or second tab must not cause two Gemini executions, so protecting the frontend button is not enough. Admission has to happen at the backend storage boundary.

Claude initially introduced an additional attempt/fencing mechanism while reasoning about overlapping executions. I pushed back and asked which reachable case in this application it solved. Once the scenarios were enumerated, the extra mechanism did not buy anything that the project row and a conditional SQLite update did not already provide.

The final design uses one guarded transition to admit a step only when the expected pipeline milestone and execution state match. This same mechanism enforces step ordering and rejects a second caller that arrives after the first one has already acquired the step. `server_run_id` additionally distinguishes a live execution from a `RUNNING` row left by an older backend process.

This was a direct AI override: the additional fencing layer was reasonable for a more distributed architecture, but it was complexity for a topology I was not building.

I also corrected an earlier claim that this gave exactly-once Gemini execution. It does not. It prevents the duplicate cases the assessment names while the application is operating normally. A process can still die after Gemini receives a request but before the result is persisted, and an explicit retry may repeat that provider call.

The cost of the simpler approach is the single-process assumption. In return, the core concurrency guarantee is small enough to understand and test directly against real SQLite rather than a mock.

## 5. Mock only the Gemini boundary; keep the integration test real

I decided early that automated tests should not call Gemini directly, but I did not want an "integration test" that mocked the pipeline itself.

The boundary is therefore:

```text
HTTP API
    ↓
real pipeline/orchestration
    ↓
real SQLite + filesystem
    ↓
FakeGeminiClient
```

Production uses the same interface with `RealGeminiClient`.

This lets the tests exercise the code I actually care about: step ordering, persistence, duplicate prevention, retries and partial progress. The fake can return deterministic structured data and tiny image bytes, record every provider call, fail a chosen call, simulate an expired interaction and hold a request open while a test observes the `RUNNING` state.

I originally considered giving the fake a configurable delay similar to a real Gemini request so the integration test would feel more realistic. Claude pushed back on that, and I agreed after thinking through what the delay would prove. Sleeping for 10–30 seconds does not make orchestration more realistic; it just makes the tests slower and less deterministic. The final fake uses an explicit gate when a test needs to keep a call in flight.

This is an example where the AI corrected my initial approach rather than the other way around.

The cost is that automated tests cannot validate live API compatibility or image quality. I cover those separately with a real Gemini contract check and a manual five-step run before submission.

That cost turned out to be real, and it is worth recording rather than hiding. The manual run failed at step 3 with "Gemini returned no image for this prompt", while all 235 backend tests were green. The image chain is seeded with the style and rules before anything is drawn, and our client demanded an image back from every call to the image model. The live API answers a seeding instruction in prose — "Great! I understand the style and rules you're looking for..." with no image attached — and only the next chained call returns a picture. The notebook does the same thing and never looks for an image in the seed; I had not noticed that the distinction mattered until the real provider enforced it.

The fake had hidden the defect by returning image bytes for every image call regardless of prompt. It was modelling what was convenient rather than what the provider actually does. I fixed the client, then fixed the fake so it returns no image for a seeding call, and added regression tests on both sides so the seed contract cannot silently drift back. This is the clearest argument I have for why the manual run was worth doing: a fake that is never checked against reality will eventually encode a contract that does not exist.

## 6. The demo defines UX and scope, not architecture or failure semantics

I separated what I trusted in `app-demo.html` from what I did not.

I use it as the reference for user flow, required screens, visible states, general interaction patterns and the visual floor. I do not use it as the source of truth for persistence, concurrency, timing or failure recovery. Those parts are deliberately mocked in the demo.

For example, its duplicate-click protection only exists in one browser tab, while the assessment explicitly requires protection across refreshes and multiple tabs. Its generated-step timing is only a few seconds and its stuck threshold is artificial, while real Gemini calls can take much longer. Those behaviours therefore moved to backend state rather than being copied from JavaScript timers or `localStorage`.

The demo also exposed a direct conflict with the written brief: after a style exists, its only affordance for reading the book disappears. The brief says the full book must remain readable at any point in the pipeline, so I followed the brief and kept book access available throughout the project.

I later chose WebSocket updates instead of polling because I wanted each completed portrait to appear immediately. That was a deliberate product choice rather than an AI mistake: polling would have satisfied the core requirement. I kept the socket intentionally narrow — REST still loads durable state and sends commands; WebSocket only pushes the same committed project view. It is not a second state system.

The cost is extra connection and reconnect logic. I kept that bounded with an in-process broadcaster rather than adding Redis or a messaging system.

# If I had one more day

I would add visible per-step attempt history.

The recovery model already distinguishes failures, interrupted executions and explicit retries, but the UI only exposes the current state. A small history such as "first attempt failed because the provider context expired; second attempt succeeded after user retry" would make that behaviour easier to understand and debug without changing the pipeline itself.

I left it out because doing it properly would require another persisted model, API representation and UI surface for a bonus feature. For this submission I preferred to spend that time making the required five-step flow, duplicate prevention, restart recovery, tests and real Gemini integration reliable.
