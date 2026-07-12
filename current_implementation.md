# Current implementation

A behavioral reference for every HTTP endpoint that actually exists in this
codebase today — what it does, what it guards against, and what it
returns. For a one-line-per-endpoint index see `README.md`'s API surface
table; this file goes one level deeper into *why* each guard exists and how
the pieces fit together. For what's planned but not yet built, see
`dev_notes.txt` and `next_move.md`.

All response bodies are JSON. All error bodies are `{"detail": "..."}`
(or, for FastAPI/Pydantic validation failures, `{"detail": [...]}` — a list
of structured field errors) unless noted otherwise.

---

## Cross-cutting conventions

These patterns repeat across almost every domain below — noted once here
instead of on every endpoint.

- **Pagination.** Every list endpoint takes `offset` (default `0`) and
  `limit` (default `100`, except dataset rows which requires both
  explicitly) as query params, and returns `{total, offset, limit, items}`
  (field name varies: `items` for most, `test_cases` for test cases).
  `total` is the full row count regardless of `offset`/`limit`, so clients
  can compute page count.
- **Rename-is-a-no-op-on-self-name.** Every `PATCH .../name`-style rename
  endpoint (datasets, test sets, test plans) treats resubmitting the
  entity's own current name as success, not a 409 — the uniqueness check
  only runs when the requested name differs from the current one. This
  was a real bug fixed during development (test sets, test plans): without
  the `name != current_name` guard, the uniqueness query matches the
  entity's own row and false-positives a 409 on every unchanged resubmit.
- **Bulk operations are all-or-nothing.** Every endpoint that accepts a
  list of IDs (bulk delete tests, bulk delete test set entries, bulk
  snapshot tests into a set, bulk link test sets to a plan) validates the
  *entire* list before writing anything. If any ID is invalid or any guard
  fails, nothing is written — there is no partial/best-effort mode
  anywhere in this API.
- **Duplicate IDs in a request body are silently deduplicated.** Any
  endpoint whose guard does an existence check via SQL `IN (...)` gets
  deduplication for free — the query returns each matching row once no
  matter how many times its ID appears in the input list, so callers don't
  need to dedupe client-side and won't get duplicate output rows for a
  duplicated input ID.
- **`extra="forbid"` on `PATCH`/modify bodies.** Every "modify" schema
  (`ModifyTestCaseRequest`, `ModifyTestSetMetadataRequest`,
  `ModifyTestPlanRequest`) rejects unknown fields with a 422, specifically
  to catch a misplaced `id` in the body rather than silently ignoring it.
- **The freeze-on-execution philosophy.** Anything that has ever been
  referenced by a `TestRunModel` — a test set entry, or (transitively) the
  test set containing it — becomes permanently immutable: no more edits,
  no more deletes. The freeze triggers on run *existence*, not run
  *completion* (a `pending` run already locks its target), because a
  queued run's input must not change out from under it before it executes.
  This is the single reproducibility guarantee the whole domain model is
  built around. It does **not** extend to test *plans* — a plan's linked
  test sets stay editable forever, even after the plan has executed (see
  `dev_notes.txt` note 4 for the full reasoning, and note that this
  reasoning currently exists only as a design note — the execution layer
  itself isn't built yet, so nothing in the shipped code enforces or even
  exercises this distinction today).

---

## Health

| | |
|---|---|
| `GET /health` | Returns `{"status": "ok"}`. No guards, no dependencies. |

---

## Datasets

A dataset is a named collection of `(prompt, expected_output, model_output)`
rows, typically imported from a local `.jsonl` file, used as the source
material for bulk test-case creation.

| Endpoint | Behavior & guards |
|---|---|
| `GET /datasets` | Paginated list of dataset metadata (`id`, `name`, `created_at`). No guards. |
| `GET /datasets/{dataset_id}` | Single dataset metadata. 404 if the ID doesn't exist. |
| `GET /datasets/{dataset_id}/rows` | Paginated list of a dataset's rows. 404 if the dataset doesn't exist. `offset`/`limit` have no defaults here (required query params) — the only pagination endpoint in the API where that's true. |
| `POST /datasets/path` | Creates a dataset from a local `.jsonl` file. Every line is validated against the row schema (`prompt`, `expected_output`, `model_output`) **before any write** — one invalid line fails the whole import (422, listing the bad line number and content). Dataset name must be unique (409). Only reads from the server's local filesystem — no remote URLs. |
| `POST /datasets/rows` | Appends rows to an existing dataset. 404 if the dataset doesn't exist. |
| `PUT /datasets/rows` | Replaces **all** rows of a dataset in one transaction — old rows deleted, new ones inserted, atomically. 404 if the dataset doesn't exist. |
| `PATCH /datasets/name` | Renames a dataset (`id` + `name` in body). 404 if not found, 409 if the new name is taken by a different dataset (self-name no-op per the cross-cutting note above). |
| `PATCH /datasets/rows` | Updates existing rows by ID (list of `{id, row_info}`). 404 if **any** row ID is missing — all-or-nothing. |
| `DELETE /datasets` | Deletes a dataset and all its rows. 404 if not found. No guard against tests already created from this dataset's rows — deleting the dataset does not touch or block on any `TestModel` created via `POST /tests/from-dataset`. |
| `DELETE /datasets/rows` | Deletes specific rows by ID. 404 if any ID is missing — all-or-nothing. |

---

## Test cases

A test case (`TestModel`) is the atomic, live, directly-editable unit: an
input plus optional expected/actual output, with zero or more assigned
test types (evaluation strategies). Tests can be run standalone, or
snapshotted into a test set for reproducible batch execution.

| Endpoint | Behavior & guards |
|---|---|
| `GET /tests` | Paginated list of all test cases, response field is `test_cases` not `items` (this endpoint predates the `items` convention used everywhere else). No guards. |
| `GET /tests/{test_case_id}` | Single test case. 404 if not found. |
| `POST /tests` | Creates a test case manually. `name` defaults to a fresh UUID if omitted. `test_type_names` (optional) must all exist in the test types catalogue — 422 listing any unrecognized names. |
| `POST /tests/from-dataset` | Bulk-creates one test case per row of an existing dataset, all sharing the same `test_type_names`. 404 if the dataset doesn't exist *or* has zero rows (two distinct 404 examples). 422 for unknown test type names. |
| `PATCH /tests/{test_case_id}` | Partial update — only fields present in the body change; omitted fields are untouched. `test_type_names: null` leaves assignments untouched; `test_type_names: []` clears them (this null-vs-empty-list distinction is deliberate and is the main subtlety of this endpoint). 404 if not found. 422 for an unknown field (extra="forbid") or an unrecognized test type name. |
| `DELETE /tests` | Bulk delete by ID list. 404 if any ID is missing. 409 if any test is still referenced — either by a `TestSetEntryModel` (must unlink from the set first) or a `TestRunModel` (past run records must be dealt with first) — two distinct 409 examples, checked via `_assert_not_referenced` against each FK column in turn. |

---

## Test sets

A test set (`TestSetModel`) is a named, reusable collection of test **entries**
— frozen snapshots of tests, not live references to them. The same
underlying test can be snapshotted into many different sets independently.

| Endpoint | Behavior & guards |
|---|---|
| `GET /test-sets` | Paginated list of test set metadata (`id`, `name`, `created_at`). No guards. |
| `GET /test-sets/{test_set_id}` | Single test set metadata. 404 if not found. |
| `POST /test-sets` | Creates a test set. Name must be unique — 409 otherwise. |
| `PATCH /test-sets/{test_set_id}` | Renames a test set. 404 if not found, 409 if another set has the name (self-name no-op), 422 for extra fields. |
| `DELETE /test-sets/{test_set_id}` | Deletes the set **and cascades to all of its entries** in one operation (DB-level `ON DELETE CASCADE`, `passive_deletes=True` on the ORM relationship — see "Database" section of `README.md`). Blocked with 409 if **any** entry in the set has ever been run — the whole set is protected, not just the entries with runs, since a partial cascade would be worse than an all-or-nothing refusal. Does not affect the live tests the entries were snapshotted from. |

---

## Test set entries

The entry (`TestSetEntryModel`) is what actually gets executed. It's a
one-time copy of a test's `name`/`input`/`expected_output`/`model_output`/
`test_type_names`, taken at the moment it's added to a set — it never
re-syncs with the live test afterward, by design.

| Endpoint | Behavior & guards |
|---|---|
| `POST /test-sets/{test_set_id}/entries` | Snapshots one or more tests into the set — one entry per test ID in the body. Three sequential guards: (1) set exists (404), (2) all test IDs exist (404, listing missing ones), (3) none of the tests are already snapshotted in *this* set (409, listing conflicts). Duplicate test IDs in the body produce exactly one entry each. |
| `GET /test-sets/{test_set_id}/entries` | Paginated list of a set's entries, ordered by `name` with `id` as a tiebreaker (stable pagination even with duplicate names). 404 if the set doesn't exist. |
| `GET /test-sets/{test_set_id}/entries/{entry_id}` | Single entry, scoped to its parent set. 404 if the set doesn't exist, or if the entry doesn't exist *within that specific set* (an entry ID valid in a different set still 404s here). |
| `PATCH /test-sets/{test_set_id}/entries/{entry_id}` | Partial update, same null-vs-omitted semantics as test case `PATCH`. **Only allowed until the entry has its first run** — 409 afterward ("can't be modified because it has runs"), permanently, with no unfreeze path. 404 if set or entry not found (two distinct examples). 422 for unrecognized test type names. Editing an entry does **not** re-link it to the live test's current state — `test_case_id` still traces back to the origin test, but the entry's content is now independent, edited data. |
| `DELETE /test-sets/{test_set_id}/entries` | Bulk-delete specific entries by ID, all-or-nothing. Guards: set exists (404), every requested entry ID resolves to an entry *in this set* (404 — an entry belonging to a different set is treated the same as one that doesn't exist), none of the requested entries have runs (409, listing which ones). Nothing else in the set is touched. |

---

## Test plans

A test plan (`TestPlanModel`) is a named campaign — a collection of test
sets meant to be executed together as one unit. Note that **execution
itself does not exist yet** (no `TestRunModel`-creating endpoint anywhere
in this API) — everything below is CRUD over the plan and its links to
test sets, not execution.

| Endpoint | Behavior & guards |
|---|---|
| `GET /test-plans` | Paginated list of plan metadata. No guards. |
| `GET /test-plans/{test_plan_id}` | Single plan metadata. 404 if not found. |
| `POST /test-plans` | Creates a plan. Name must be unique — 409 otherwise. |
| `PATCH /test-plans/{test_plan_id}` | Renames a plan. Same shape as test set rename: 404, 409 (self-name no-op), 422. |

---

## Test plan entries

A test plan entry (`TestPlanEntryModel`) is the junction linking a plan to
one of its test sets. **This is a live pointer, not a snapshot** — no
content of its own beyond the FK pair (`test_plan_id`, `test_set_id`).
Unlike test set entries, this layer never freezes: a plan's linked test
sets can be added or (once that endpoint exists) removed at any time, even
after the plan has been executed. See `dev_notes.txt` note 4 for the full
reasoning on why that's intentional, not an oversight.

| Endpoint | Behavior & guards |
|---|---|
| `POST /test-plans/{test_plan_id}/entries` | Links one or more test sets to the plan — one entry per test set ID in the body. Three sequential guards, same shape as adding tests to a set: (1) plan exists (404), (2) all test set IDs exist (404, listing missing ones — this existence check is what makes the dedup guarantee possible, since its `IN (...)` query naturally collapses duplicate IDs to one row each), (3) none of the test sets are already linked to *this* plan (409, listing conflicts). |
| `GET /test-plans/{test_plan_id}/entries` | Paginated list of the plan's linked test sets, ordered by the **linked test set's** `name` with its `id` as a tiebreaker. Each item embeds the linked test set's `id`/`name`/`created_at` under `test_set`, alongside the entry's own `id` (the link's identity, not the test set's — a common point of confusion). 404 if the plan doesn't exist. Implementation note: built via an explicit SQL join + `contains_eager` (not `selectinload`) specifically so the join can also drive the `ORDER BY` on the joined test set's columns in a single query. |

---

## Statistical tests

Standalone statistical analysis, not tied to the test/test-set/test-plan
domain model — takes raw scores directly in the request.

| Endpoint | Behavior & guards |
|---|---|
| `POST /statistical-tests/z-test` | One-sample z-test: is the population mean of `scores` significantly different from `threshold`? Requires `scores` to have at least 30 values (enforced at the schema level, `min_length=30`) — the response documents this as "reliable for n ≥ 30; smaller samples should use a t-distribution instead," but nothing currently *routes* to a t-test alternative. No 404/409 guards — purely computational, no persistence lookup. |

---

## Observability

| | |
|---|---|
| `GET /metrics` | Prometheus-compatible scrape endpoint (`http_requests_total`, `http_request_duration_seconds`), labelled by method/status/handler. Deliberately excluded from the OpenAPI docs (`/docs`) since it returns plain text, not JSON — but it's live on every running instance regardless. |

---

## Not yet implemented (pointers, not a full list)

- **Test execution** — no endpoint anywhere creates a `TestRunModel`.
  Standalone runs, test-set-based runs, and test-plan-based runs (fan-out,
  replay, live re-execution) are all unbuilt. See `next_move.md` for the
  detailed implementation plan already drafted for test-plan execution
  specifically.
- **Removing a test set from a plan** — `TestPlanEntryModel` deletion has
  no endpoint yet (linking exists; unlinking doesn't).
- **Deleting a test plan** — no `DELETE /test-plans/{id}` yet.
- **LLM-as-judge evaluators and the broader statistics engine** — stubbed,
  per `README.md`'s Status section.

For the authoritative, currently-accurate version of this list, check
`README.md`'s TODOs section directly rather than trusting this copy to
stay in sync — this file is a behavioral reference for what exists, not
a live tracker for what doesn't.
