# Next move: implementing dev_notes.txt notes 4 & 5

Implementation plan for **test plan execution attribution** (note 4) and
**replay vs. live re-execution** (note 5). Grounded in the current state of
the codebase as of this writing:

- `TestRunModel` exists but nothing creates rows in it yet — there is no
  execution service or API endpoint anywhere in `src/assay/services/` or
  `src/assay/api/`. This is genuinely new functionality, not a change to an
  existing path.
- `TestPlanEntryModel` (the plan ↔ test set junction) is fully built:
  create/list/get/rename a plan, list a plan's linked test sets, link new
  test sets to a plan. No freeze guard on it, correctly, per note 4's
  correction.
- The entry-level freeze (`_check_test_set_entry_has_no_runs_or_409` and
  friends in `services/test_sets/_common.py`) is unaffected by any of this
  and needs no changes.

This plan builds the execution layer notes 4 & 5 assume exists, in
dependency order: schema → services → API → tests → docs.

### Terminology — two different things are called "entry"

This document (and the codebase) uses "entry" for two unrelated concepts.
Keep them separate — the replay/freeze reasoning below only makes sense if
they aren't conflated:

- **Test set entry** (`TestSetEntryModel`) — lives inside a *test set*.
  It's a **frozen snapshot of a test**: `input`, `expected_output`,
  `model_output`, `test_type_names`, copied once from the live `TestModel`
  at the moment it was added. This is the thing that can freeze
  permanently (see "Why replay doesn't need new freezing logic" under
  Phase 3.2).
- **Test plan entry** (`TestPlanEntryModel`) — lives inside a *test plan*.
  It's just a **live link pointing at a test set** — no snapshot, no
  content of its own. Per note 4, this one deliberately never freezes; a
  plan's linked test sets can keep changing forever.

Every "entry" below is a test set entry unless explicitly written as
"test plan entry" / `TestPlanEntryModel`.

---

## Phase 1 — Data model

### 1.1 `TestPlanExecutionModel` (new)

One row per trigger event ("I executed this plan just now"), so past
executions can be listed and individually replayed (note 5). Add to
`src/assay/models/test.py`, near `TestPlanModel`:

```python
class TestPlanExecutionModel(Base):
    """
    One trigger event of a test plan — either a fresh fan-out over the
    plan's currently linked test sets ("live"), or a replay of a specific
    prior execution's exact entries ("replay").

    Grouping runs under an execution (rather than relying on test_plan_id
    + created_at clustering) is what makes "list past executions, replay
    one of them" a reliable query instead of an inferred one.
    """

    __tablename__ = "test_plan_executions"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    test_plan_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("test_plans.id"), nullable=False, index=True
    )
    # Null for a live execution. Set for a replay — points at the
    # execution whose entry set this one re-ran.
    replayed_execution_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("test_plan_executions.id"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now().astimezone()
    )

    test_plan: Mapped["TestPlanModel"] = relationship()
    replayed_execution: Mapped["TestPlanExecutionModel | None"] = relationship(
        remote_side=[id]
    )
    runs: Mapped[list["TestRunModel"]] = relationship(back_populates="test_plan_execution")
```

Self-referential FK on `replayed_execution_id` keeps a chain: replaying a
replay still points at *which* execution's entries were reused, without
needing to chase back through `TestRunModel`.

#### Why this model exists at all

Note 5 established that a plan can be executed multiple times, and each
execution might cover a different scope (test sets keep evolving). To let
a user "replay execution #2" specifically, you need to be able to answer:
*which `TestRunModel` rows belong to execution #2, as opposed to #1 or
#3?* `test_plan_id` alone can't answer that — every run from every
execution of the same plan shares the same `test_plan_id`. You'd be stuck
bucketing runs by `created_at` timestamp ranges, which breaks the moment
two executions' timestamps overlap (a slow execution still finishing while
a new one gets triggered) or a batch straddles a clock boundary.
`TestPlanExecutionModel` exists purely to give each *trigger event* its
own durable identity that runs can point back to.

#### Field by field

```python
id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
```
Its own identity — the ID a client passes as `replay_execution_id` to say
"run it again exactly like this one did."

```python
test_plan_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("test_plans.id"), nullable=False, index=True)
```
Which plan this execution belongs to. Not strictly required to reach the
plan (you could join through `runs` → `test_plan_execution` → back up),
but having it directly here makes "list all past executions of plan X" a
single indexed `WHERE test_plan_id = X` query on this table, instead of
one that has to go through `TestRunModel` to find it.

```python
replayed_execution_id: Mapped[uuid.UUID | None] = mapped_column(
    ForeignKey("test_plan_executions.id"), nullable=True, index=True
)
```
This is the field that actually encodes "live vs. replay" as data, not
just as a request parameter that gets discarded after use. If it's
`None`, this execution was a live fan-out from whatever test sets were
linked at the time. If it's set, this execution is a replay, and the
value tells you *of what*. Critically, this is a **self-referential**
FK — `test_plan_executions.replayed_execution_id` points back at another
row in the same table. That's what lets you chain: "execution #3 replayed
execution #1" is a fact you can query later, not something you have to
infer.

```python
created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now().astimezone())
```
When the trigger happened — same pattern as every other model in this
codebase (`TestPlanModel.created_at`, `TestSetModel.created_at`, etc.).

```python
runs: Mapped[list["TestRunModel"]] = relationship(back_populates="test_plan_execution")
```
The actual payoff: every `TestRunModel` created by this execution links
back here via its new `test_plan_execution_id` FK (added to `TestRunModel`
in section 1.2). This relationship is how you answer "what did execution
#2 actually test" — just walk `execution.runs`, each of which points at a
frozen `TestSetEntryModel`.

#### Worked example

1. **Day 1** — you link Regression Suite (5 entries) and Smoke Tests (3
   entries) to "Campaign Q3", then trigger a live execution. This creates
   `TestPlanExecutionModel(id=E1, test_plan_id=Campaign, replayed_execution_id=None)`,
   plus 8 `TestRunModel` rows, each with `test_plan_execution_id=E1`.
2. **Day 15** — someone adds 2 new entries to Regression Suite. You want
   to know "did the model regress against exactly what I tested on Day
   1?" — not the new entries. You call replay with
   `replay_execution_id=E1`. This creates
   `TestPlanExecutionModel(id=E2, test_plan_id=Campaign, replayed_execution_id=E1)`,
   and the service queries `TestRunModel` where `test_plan_execution_id=E1`
   to get the same 8 `test_set_entry_id`s, creating 8 fresh runs (new
   `TestRunModel` rows, `test_plan_execution_id=E2`) against those same
   frozen entries — the 2 new entries are correctly excluded, because E1
   never ran them.
3. **Day 30** — now you *do* want the new entries included, so you trigger
   live again:
   `TestPlanExecutionModel(id=E3, test_plan_id=Campaign, replayed_execution_id=None)`,
   fanning out fresh from whatever's linked right now (10 entries, since
   the 2 new ones plus whatever else has changed).

Later, `GET /test-plans/{id}/executions` would show all three (E1, E2,
E3), and for E2 specifically you could show "replayed E1" by reading its
`replayed_execution_id` — exactly the audit-trail behavior note 4's
closing bullet wanted, and exactly what a flat `test_plan_id`-only design
(no execution model) couldn't give you.

### 1.2 `TestRunModel` — add plan/execution attribution

Add two nullable, indexed FKs to the existing `TestRunModel`:

```python
test_plan_id: Mapped[uuid.UUID | None] = mapped_column(
    ForeignKey("test_plans.id"), nullable=True, index=True
)
test_plan_execution_id: Mapped[uuid.UUID | None] = mapped_column(
    ForeignKey("test_plan_executions.id"), nullable=True, index=True
)

test_plan: Mapped["TestPlanModel | None"] = relationship()
test_plan_execution: Mapped["TestPlanExecutionModel | None"] = relationship(
    back_populates="runs"
)
```

Both null for standalone runs and for a run created via direct test-set
execution (out of scope here — only plan-triggered runs populate them).
`test_plan_id` is redundant with `test_plan_execution.test_plan_id` once
the execution exists, but keeping it directly on `TestRunModel` is worth
it: it lets guards and the "has this plan ever run" existence check filter
on `TestRunModel.test_plan_id` directly with a plain `WHERE`, no join
through `test_plan_executions` needed. Update the existing docstring's
"Invariant" section to note `test_plan_id`/`test_plan_execution_id` are
populated together or not at all, and only for set-based runs.

### 1.3 Migration

`uv run alembic revision --autogenerate -m "add test plan execution tracking"`,
then hand-verify the generated `op.create_table('test_plan_executions', ...)`
and the two `batch_alter_table('test_runs', ...)` column adds follow the
existing style (see `a32c5cc3f97a_updating_models_for_test_management.py`
for the FK/index pattern already used for `test_runs`). No data migration
needed — no `TestRunModel` rows exist yet in any real deployment of this
feature, since nothing creates them today.

---

## Phase 2 — Schemas

New file `src/assay/schemas/test_plan_executions.py`:

```python
class TestPlanExecutionID(BaseModel):
    id: uuid.UUID = Field(..., description="The unique identifier of the test plan execution")

class ExecuteTestPlanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    replay_execution_id: uuid.UUID | None = Field(
        default=None,
        description=(
            "ID of a prior execution of this plan to replay — re-runs the exact "
            "same test set entries that execution used. Omit or set to null to "
            "run the plan's current live scope instead (whatever test sets are "
            "linked to it right now)."
        ),
    )

class TestPlanExecutionMetadata(TestPlanExecutionID):
    test_plan_id: uuid.UUID
    replayed_execution_id: uuid.UUID | None
    created_at: datetime
    run_count: int = Field(..., description="Number of TestRunModel rows created by this execution")

class PaginatedTestPlanExecutionsResponse(Pagination):
    items: list[TestPlanExecutionMetadata]
```

Re-export from `schemas/__init__.py` following the existing alphabetical
placement (watch the import-order-vs-circular-import trap already hit once
with `test_plan_entries.py` — `test_plan_executions.py` will need
`TestSetEntryModel`/entry schemas, so double check where it lands relative
to its dependencies in `__init__.py`'s import sequence before assuming
plain alphabetical works).

---

## Phase 3 — Service layer

New file `src/assay/services/test_plans/execute_test_plan.py`.

### 3.1 Guard: existence check for note 4

Add to `services/test_plans/_common.py`:

```python
async def _check_test_plan_has_no_runs_or_409(test_plan_id, session) -> None:
```

Wait — **note 4 was retracted.** Do not add this guard anywhere in the
write path for `TestPlanEntryModel` (`add_test_sets_to_test_plan_by_id`,
and whatever future "remove test set from plan" endpoint). This bullet is
here only as a explicit checklist reminder for whoever implements this:
the temptation to "finish the freeze" will resurface once `test_plan_id`
exists on `TestRunModel` and the existence check becomes trivial to write.
Don't. See dev_notes.txt note 4 for the full reasoning.

### 3.2 `execute_test_plan_by_id`

```python
async def execute_test_plan_by_id(
        test_plan_id: uuid.UUID,
        request: ExecuteTestPlanRequest,
        session: AsyncSession,
) -> TestPlanExecutionMetadata:
```

Guards, in order:
1. `_find_test_plan_by_id_or_404` — plan must exist.
2. If `request.replay_execution_id` is set: fetch that
   `TestPlanExecutionModel` and 404 if missing *or* if its `test_plan_id`
   doesn't match `test_plan_id` (don't let a client replay execution X of
   plan A by hitting plan B's endpoint — this is the same "scoped lookup"
   pattern already used by `_find_test_set_entries_in_specific_test_set_or_404`
   for entries scoped to a set).

Branch on mode:

- **Replay** (`replay_execution_id` set): query distinct
  `test_set_entry_id` from `TestRunModel` where
  `test_plan_execution_id == replay_execution_id`. Create one new
  `TestRunModel` per entry, `status=pending`, with the new execution's id
  and `test_plan_id`. If the replayed execution somehow has zero runs
  (shouldn't happen, but a stale/corrupt row is cheap to defend against),
  this needs a decision: 409 "nothing to replay" vs. silently succeed with
  zero runs — lean 409, since a silent no-op response would look like
  success to a caller who forgot the request wouldn't do anything.
- **Live** (`replay_execution_id` is `None`): query
  `TestPlanEntryModel.test_set_id` for this plan, join to
  `TestSetEntryModel` for all entries across all linked test sets, create
  one new `TestRunModel` per entry. If the plan has zero linked test sets,
  same open question as above — 409 "test plan has no linked test sets"
  is the safer default; a plan that fans out to zero runs is a
  no-op that likely indicates the caller meant to link sets first.

Both branches: create the `TestPlanExecutionModel` row first (so
newly-created `TestRunModel`s can reference its id), `session.add_all` the
runs, single `session.commit()`.

Return `TestPlanExecutionMetadata` with `run_count = len(created_runs)`.

#### Why replay doesn't need new freezing logic

Replay works by re-pointing new `TestRunModel` rows at the *same* test set
entries an earlier execution used — no copying of content, no
reconstruction. That's safe only because those test set entries are
already permanently frozen by guards that exist today, completely
independent of this feature:

1. **A test set entry can't be edited once it has a run.**
   `_check_test_set_entry_has_no_runs_or_409` blocks `PATCH` the moment any
   run references it.
2. **A test set entry can't be deleted once it has a run.** The bulk-delete
   guard (`_check_given_test_set_entries_have_no_runs_or_409`) blocks
   removing one that has runs.
3. **The test set containing it can't be deleted while any of its test set
   entries has a run.** `_check_test_set_entries_have_no_runs_or_409`
   blocks whole-set deletion under the same condition.

So the moment a test set entry gets its first run (i.e., the moment the
*original* execution ran it), its content, existence, and containment are
all locked forever — no unfreeze mechanism exists anywhere. When a later
replay re-points a new run at that same test set entry, it's guaranteed
byte-for-byte identical to what the original execution saw, purely as a
side effect of guards 1–3, none of which this feature needs to add or
modify.

This is exactly why replay (anchored to permanently frozen test set
entries) and a plan's ever-changing live scope (test plan entries, which
never freeze — note 4) can coexist without conflict: the freeze lives
entirely at the test-set-entry layer; the test-plan-entry layer was never
meant to freeze at all.

### 3.3 Supporting reads

- `get_test_plan_executions_metadata(test_plan_id, session, offset, limit)`
  — paginated list of a plan's past executions (mirrors
  `get_test_sets_linked_tests`'s pagination shape). This is what a client
  calls before choosing a `replay_execution_id`.
- `get_test_plan_execution_runs(test_plan_id, execution_id, session, ...)`
  — the actual "what did this campaign test" audit read from note 4's
  closing bullet. Returns run-level detail (scores/status/entry snapshot)
  for one execution. Scope this 404 the same way as the replay lookup
  above (execution must belong to the given plan).

---

## Phase 4 — API endpoints

Add to `src/assay/api/test_plan.py`, following this file's existing
`responses=`/docstring depth:

| Method | Path | Purpose |
|--------|------|---------|
| `POST` | `/test-plans/{test_plan_id}/executions` | Trigger an execution — `ExecuteTestPlanRequest` body selects live vs. replay |
| `GET` | `/test-plans/{test_plan_id}/executions` | Paginated list of past executions |
| `GET` | `/test-plans/{test_plan_id}/executions/{execution_id}/runs` | Audit-trail read: what that execution actually ran, with results |

`responses` for `POST .../executions` needs at minimum:
- 200 — execution created, `TestPlanExecutionMetadata` with `run_count`.
- 404 — plan not found, *or* `replay_execution_id` doesn't resolve to an
  execution of this plan (two distinct example blocks, same style as the
  existing 404 handling on `POST /test-sets/{id}/entries`).
- 409 — nothing to execute (empty live scope, or an empty replay target —
  see the open question in 3.2, resolve it before writing this).

---

## Phase 5 — Tests

New test files under `tests/services/test_plans/`, matching the mocking
conventions already used in this directory (`AsyncMock` session,
`MagicMock(all=MagicMock(return_value=[...]))` for `scalars`, `patch(...)`
on the specific guard functions being bypassed per test, `pytest.raises`
for the error paths):

- `test_execute_test_plan.py` — plan-not-found (404), replay execution not
  found / belongs to a different plan (404), live-with-zero-linked-sets
  (409 or whatever Phase 3 decided), replay happy path (asserts new runs
  reference the same `test_set_entry_id`s as the replayed execution, new
  `test_plan_execution_id`, not the old one), live happy path (asserts one
  run per entry across all currently-linked sets).
- `test_get_test_plan_executions_metadata.py` — pagination shape, matches
  the existing `test_get_test_plan_metadata.py` pattern.
- `test_get_test_plan_execution_runs.py` — 404 scoping, correct run detail
  returned.

Also worth a **regression test proving note 4's correction holds**: link a
test set to a plan, execute it, then unlink that test set — assert this
does not raise, and assert the already-created `TestRunModel` rows are
untouched (still exist, still reference the same frozen entry). This is
the concrete behavioral claim note 4 makes; nothing currently in the test
suite asserts it because the feature doesn't exist yet.

---

## Phase 6 — README

Once built, `PATCH`/`POST` additions follow the same four-place update
this project already does for every endpoint (see recent commit history):
Status blurb, project layout tree (`services/test_plans/execute_test_plan.py`,
new `models/test.py` entry for `TestPlanExecutionModel`), API surface table
(new "Test plan executions" subsection or extend "Test plans"), and TODOs
(remove "test runs ... no service or API layer" once this lands — check
whether that TODO should stay partially, since *standalone* test execution
would still be unbuilt even after this).

---

## Open questions to resolve before writing code

1. **Empty live scope / empty replay target** — 409 vs. silent zero-run
   success. Leaning 409 (see Phase 3.2) but not decided.
2. **Async execution** — this plan only covers *creating* `TestRunModel`
   rows (`status=pending`). Actually running them (calling the model,
   scoring, writing back `scores`/`error`/`executed_at`) is a separate,
   larger piece of work with its own concurrency/background-job design,
   deliberately out of scope for this plan.
3. **Should `POST .../executions` be synchronous or just enqueue?** Tied
   to (2) — if execution is async, this endpoint only creates `pending`
   rows and returns immediately; a later mechanism promotes them to
   `running`/`completed`/`failed`. Not decided here.
