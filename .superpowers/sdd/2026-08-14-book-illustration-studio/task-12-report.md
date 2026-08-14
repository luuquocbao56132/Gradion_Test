# Historical report — Task 12

Retrospective review target: commit `5793f70` over base `44bece7`.

Implemented the frontend DTO mirrors, step vocabulary, HTTP API client,
design tokens/styles, AppShell, StateMessage, and API tests. The execution
ledger records backend 102 passing and frontend 7 passing at this commit, and
records a field-by-field cross-check of `types.ts` against backend models.

The work was originally written by an interrupted subagent and verified and
committed by the controller. A contemporaneous strict RED transcript is not
available. This report is intentionally historical and makes no stronger TDD
claim.

Files and exact requirements are in `task-12-brief.md`; the review package is
the source of truth for what the commit changed.

## Fix Round 1 — `ConnectionState` frontend contract

### Files

- `frontend/src/types.ts` — exported the required `ConnectionState` union:
  `'connecting' | 'live' | 'reconnecting' | 'closed'`.
- `frontend/src/types.contract.ts` — compile-time contract fixture covering all
  four allowed values and rejecting `offline` with `@ts-expect-error`.

### RED

Command: `cd frontend && npm run build`

Result: failed as expected with `TS2305: Module "../types" has no exported
member 'ConnectionState'` (and the dependent `@ts-expect-error` was unused).
The failure demonstrated that the fixture could not compile while the
canonical export was absent.

### GREEN

After adding only the required union, `cd frontend && npm run build` passed:
TypeScript compiled and Vite reported `✓ built in 399ms`.

`cd frontend && npm test -- --run` passed: 4 test files and 23 tests passed.

### Self-review

The change is limited to the missing public type and one compile-only contract
fixture. The fixture is outside Vitest's `*.test.*` pattern, so it participates
in the TypeScript build without becoming an empty runtime test suite.

### Concerns

None. The untracked `book.txt` and `note.md` files were preserved and not
included in the commit.
