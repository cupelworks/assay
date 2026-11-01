# Assay CLI

Run Assay's tests from a CI/CD pipeline, and fail the step when they fail.

`assay` starts a run of a test, a test set or a test plan, or a statistics batch, waits
for it to finish, prints what happened, and exits with a code the pipeline understands.
It writes JUnit XML for your CI system's test results and a summary for GitHub's job page.

It's a thin client of [Assay's API](../README.md#api). It needs only `httpx` and
`PyYAML`, not the backend, its database, its worker or the NLP models, so it installs in
seconds on any runner.

```console
$ assay run test-plan "Release checks" --junit assay.xml
Started execution 3f1c… of test plan 'Release checks': 24 runs.
  0/24 runs finished
  17/24 runs finished (16 Green, 1 Red)
  24/24 runs finished (22 Green, 1 Amber, 1 Red)
FAILED: test plan 'Release checks', 24 runs: 22 Green, 1 Amber, 1 Red, 0 NotRan
  execution id: 3f1c…
  ✗ Support › Refund policy: Red: not met: Correctness, Contains
  ✗ Smoke › Greeting: Amber: not met: BLEU
  fails on: Amber, Red, NotRan
JUnit report: assay.xml
$ echo $?
1
```

## Contents

- [What it can do](#what-it-can-do)
- [Before you start](#before-you-start)
- [Install](#install)
- [Connect to Assay](#connect-to-assay)
- [Name what to run](#name-what-to-run)
- [`assay health`](#assay-health): is Assay ready?
- [`assay run`](#assay-run): run once and gate on the outcomes
  - [Tolerating some failures](#tolerating-some-failures): limits, blocking checks, a policy file
- [`assay batch`](#assay-batch): run N times and gate on statistical verdicts
- [Waiting](#waiting)
- [Output](#output): text, JSON, JUnit XML, Markdown
- [Exit codes](#exit-codes)
- [In a pipeline](#in-a-pipeline): GitHub Actions, Azure Pipelines, GitLab CI, Jenkins, any other
- [Recipes](#recipes)
- [Troubleshooting](#troubleshooting)
- [What it doesn't do (yet)](#what-it-doesnt-do-yet)
- [Reference](#reference): every command and option
- [Development](#development)

## What it can do

- **Run a test, a test set or a test plan once.** This is a live execution of what the set
  or plan holds right now. It can also **replay** a past execution: exactly the entries
  that execution ran, even if the set has changed since.
- **Run a scope N times as a statistics batch.** Each check gets a verdict, `pass`, `fail`
  or `inconclusive`, at a stated confidence, instead of an outcome from one lucky or
  unlucky run.
- **Wait for the result** and show progress while it waits, with a time limit.
- **Decide pass or fail for the step** with a rule you choose: which run outcomes, or
  which verdicts, fail it; how many failing runs an execution may have, over all of it or
  per test set; which checks may never fail. The rules can live in a file in your
  repository, so loosening the gate is reviewed like any change.
- **Report it** as text for the log, JSON for scripts, JUnit XML for CI test-result views,
  and Markdown for GitHub's job summary (or any file).
- **Check Assay is ready** before a pipeline depends on it.
- **Find what to run by name**, so a pipeline says `"Release checks"` rather than an id.

## Before you start

The CLI only asks Assay to run things. For a run to finish, Assay itself has to be set up:

| You need | Why | How to check |
|---|---|---|
| **Python 3.11 or later** on the runner | The CLI is a Python package | `python --version` |
| **The runner can reach Assay's API** | The CLI calls it over HTTP(S) | `assay health` |
| **A worker running** | The API only queues runs; a worker executes them. Without one, runs stay `Pending` and the step times out | `assay health` says `ready` |
| **The application under test configured** in Assay's settings | Tests without a recorded answer ask your application; without a target they end `NotRan` | `POST /settings/target/checks`, or the Settings page |
| **A judge configured**, if you use LLM checks | Correctness, Relevance and the other judge checks need one | `POST /settings/judge/checks`, or the Settings page |
| **The test, test set or test plan exists in Assay** | The CLI runs what's in Assay; it doesn't create tests | The UI, or `GET /test-sets` |

**About the network.** The runner must reach the API. Locally, the API is at
`http://127.0.0.1:8000`. On the [Azure deployment](../infra/azure/README.md) it can't yet:
the API's ingress is internal to its Container Apps environment, so only the other apps
there (the web interface, the worker) reach it, and no pipeline runner does, wherever it
runs. See [What it doesn't do (yet)](#what-it-doesnt-do-yet).

## Install

From GitHub, at a tag or commit that contains `cli/`:

```bash
pip install "assay-cli @ git+https://github.com/cupelworks/assay@<tag-or-commit>#subdirectory=cli"
```

`@main` works too, but pin a tag or a commit in a pipeline so an update can't change your
gate without you noticing.

From a clone of this repository:

```bash
pip install ./cli          # or: pip install -e ./cli to work on it
```

As a tool of its own, kept apart from your project's packages:

```bash
pipx install "assay-cli @ git+https://github.com/cupelworks/assay@<tag-or-commit>#subdirectory=cli"
```

Check it's installed:

```bash
assay --version            # assay 0.1.0
assay --help
```

`python -m assay_cli` does the same as `assay`, for when scripts aren't on the `PATH`.

## Connect to Assay

Tell the CLI where the API is, with a flag or an environment variable:

```bash
export ASSAY_API_URL=https://assay.internal.example.com
assay health
# or
assay --url https://assay.internal.example.com health
```

| Setting | Flag | Environment variable | Default |
|---|---|---|---|
| The API's URL | `--url URL` | `ASSAY_API_URL` | none: required |
| Headers sent with every request | `--header 'Name: value'`, repeatable | `ASSAY_API_HEADERS`, a JSON object | none |
| Timeout of each step of a request | `--http-timeout SECONDS` | | `30` |

- **The URL includes the path the API is served under.** If a proxy serves it under `/api`
  (Assay's `ASSAY_ROOT_PATH`), give `https://host/api`. A trailing slash doesn't matter.
- **The flag wins** over the environment variable.
- **These options go before or after the command:** `assay --url X run …` and
  `assay run … --url X` both work.
- **Credentials.** Assay has no users of its own, so the CLI sends none by default. If you
  put a gateway or proxy in front of the API that wants a token, send it as a header:

  ```bash
  export ASSAY_API_HEADERS='{"Authorization": "Bearer '"$TOKEN"'"}'
  assay run test-set "Smoke" --header "X-Pipeline: nightly"
  ```

  Headers from `--header` are added to those in `ASSAY_API_HEADERS`; when both give the
  same name, in any case (`authorization` and `Authorization`), `--header` wins. Keep
  tokens in your CI system's secrets, never in the pipeline file.

## Name what to run

Every `run` and `batch` names a **scope** and **which one**:

| Scope | What one run of it is |
|---|---|
| `test` | One standalone run of that test |
| `test-set` | One execution of the set: a run per entry |
| `test-plan` | One execution of the plan: a run per entry of every test set linked to it |

Give either its **id** or its **exact name**:

```bash
assay run test-set 4e86003a-9e28-4c93-a08e-f99c6acbaab6
assay run test-set "Smoke"
```

- **A name must match exactly**, including upper and lower case. Quote it if it has
  spaces.
- **No match** is an error (exit 2) that says so.
- **Two tests with the same name** are an error that lists two of their ids: use an id. Test set
  and test plan names are unique in Assay, so they can't clash.
- **Ids** are shown in the UI and returned by the API, e.g. `GET /test-sets?q=smoke`.
- **A name shaped like an id** (a test created without a name is named with a random
  one) is looked up as an id first, then as a name.

## `assay health`

Checks that the API answers and that a worker is ready to execute runs.

```console
$ assay health
Assay 2.4.3: ready (2 workers)
```

| Status | Meaning | Exit |
|---|---|---|
| `ready` | The broker is reachable and every worker runs the current code | 0 |
| `no_worker` | No worker answered: runs would wait in the queue | 1 |
| `outdated` | A worker runs older code: restart it | 1 |
| `broker_unreachable` | Nothing can be sent to workers: runs stay `Pending` | 1 |
| API unreachable | Wrong URL, network or proxy; tried for a minute first | 2 |

Run it as a first step so a broken setup fails quickly and clearly, rather than as a
timeout an hour later.

## `assay run`

Runs a scope once, waits for every run to finish, and fails the step if any run's outcome
is one you fail on, or, with [limits](#tolerating-some-failures), if more runs fail than
you allow.

```bash
assay run test "Greeting"
assay run test-set "Smoke"
assay run test-plan "Release checks"
```

### What a run's outcome means

Each run scores the test's checks and ends with one outcome:

| Outcome | Meaning |
|---|---|
| `Green` | Every check passed |
| `Amber` | Some checks passed, some didn't |
| `Red` | No check passed |
| `NotRan` | It couldn't run, e.g. the application couldn't be reached or no target is set. The reason is shown |

A test that carries a recorded answer is scored as it is. A test without one makes Assay
call your application, with the settings in effect now, and score its reply.

### Choosing what fails a run: `--fail-on`

By default **anything short of Green fails a run, and any failing run fails the step**:
`--fail-on Amber,Red,NotRan`. Give the outcomes you want to fail on, separated by commas,
in any case:

```bash
assay run test-set "Smoke" --fail-on Red,NotRan   # tolerate Amber
assay run test-set "Smoke" --fail-on red          # tolerate Amber and NotRan too
```

Runs you tolerate still appear in the JUnit report, with their outcome in the test case's
output.

A run that couldn't run (`NotRan`) says nothing about quality: the application or the
judge didn't answer. When the step fails only because of such runs, it exits **4**, not 1,
so "the application is down" and "the application got worse" look different in the
pipeline (see [Exit codes](#exit-codes)).

### Tolerating some failures

One execution is **one sample per entry**. A GenAI application that passes each of 40
entries 95% of the time still fails at least one of them 87% of the time, so "any failure
fails" blocks a healthy application most days. Allowing up to 4 failures lets that
application through 95% of the time, but it also lets through one degraded to 85% about
a quarter of the time. Tolerance is a heuristic: it trades missed regressions for fewer
false alarms. For a decision you can put a confidence on, run a
[statistics batch](#assay-batch).

Tolerance is always your explicit choice; without it, the strict default holds.

| Option | What it allows |
|---|---|
| `--max-failures N` | At most N failing runs in the whole execution |
| `--max-failures "TEST_SET=N"` | At most N in that test set |
| `--min-pass-rate RATE` | At least RATE (0 to 1) of the runs passing, over the whole execution |
| `--min-pass-rate "TEST_SET=RATE"` | The same, in that test set |
| `--blocking-check LABEL` | Nothing: any run in which that check fails fails the step, whatever the counts |
| `--policy FILE` | The rules above, read from a file (below) |

Each is repeatable: give a limit over the whole and others per set, or several blocking
checks.

```bash
assay run test-plan "Release checks" --fail-on Red,NotRan --max-failures 3
assay run test-plan "Release checks" --max-failures "Smoke=0" --min-pass-rate "Tone=0.8" \
  --blocking-check Toxicity --blocking-check "Valid JSON"
```

**How the step is judged:**

1. A run fails when its outcome is in `--fail-on`.
2. A blocking check that failed in any run fails the step, whatever the counts. It counts
   even in a run whose outcome you tolerate (an `Amber` run with `--fail-on Red`).
3. A test set with limits of its own is judged by them: its failing runs against
   `max_failures`, its passing share against `min_pass_rate`.
4. Limits over the whole execution count every run, those sets' included.
5. **Without limits over the whole,** every run outside the sets with their own limits
   must pass. So `--min-pass-rate "Tone=0.8"` alone tolerates failures in Tone only.

A test set is named as Assay names it, exactly. A plan's runs each carry the test set of
their entry, so per-set limits work on a test plan; on a test set, its own name works too;
a standalone test has no sets. A limit naming a set the scope doesn't run is refused
before anything starts: it's a typo, or a set renamed since, and would never apply.

#### A policy file: `--policy`

Keep the rules in the application's repository, so loosening the gate is reviewed in a
pull request:

```yaml
# assay-gate.yml
test_plan: "Release checks"     # optional: the scope this file is for
fail_on: [Red, NotRan]          # Amber tolerated
max_failures: 3                 # over the whole plan
test_sets:
  "Smoke": { max_failures: 0 }
  "Tone": { min_pass_rate: 0.80 }
blocking_checks: ["Toxicity", "Valid JSON"]
```

```bash
assay run test-plan "Release checks" --policy assay-gate.yml
```

| Key | Value |
|---|---|
| `test_plan`, `test_set` or `test` | Optional, one of them: the scope's name or id. The file then refuses any other scope, so it can't gate the wrong plan by mistake |
| `fail_on` | A list (or comma-separated) of `Green`, `Amber`, `Red`, `NotRan`; default `[Amber, Red, NotRan]` |
| `max_failures` | A whole number, 0 or more, over the whole execution |
| `min_pass_rate` | A share from 0 to 1 (`0.8` for 80%), over the whole execution |
| `test_sets` | Each set's name, then its `max_failures`, `min_pass_rate` or both |
| `blocking_checks` | A list of check labels, exactly as Assay shows them |

- **The file is read strictly.** An unknown key, a key given twice, a limit out of range,
  or a name YAML reads as something else (an unquoted `No`, `On`, or a number) is an
  error that names it, before anything is sent to Assay. Quote names to be safe.
- **Flags change it for one run.** `--fail-on` and each limit given as a flag replace the
  file's, for the whole or for that set. `--blocking-check` only adds: dropping a
  blocking check is a change to the file, reviewed as one.
- An empty file is the strict default.

#### What the output says

When you've set any limit or blocking check, every output names the rules that decided:
the ones broken when the step fails, the ones that tolerated failures when it passes.

```text
FAILED: test plan 'Release checks', 40 runs: 35 Green, 3 Amber, 1 Red, 1 NotRan
  execution id: 3f1c…
  gate: Smoke: 1 failure > max 0; blocking check 'Toxicity' failed in 1 run
  ✗ Smoke › Login: Red: not met: Contains
  ✗ Tone › Refund policy: Amber: not met: Toxicity (blocking: Toxicity)
  fails on: Red, NotRan
```

The same rules are in the JSON's `gate` (every rule, `broken` or not), the Markdown
summary's **Gate:** line, and the JUnit report's `Assay gate` suite: one test case per
rule, named after the rule alone (`Smoke: at most 0 failures`), so a CI system's history
shows which rule broke and since when.

### Replaying a past execution: `--replay`

```bash
assay run test-set "Smoke" --replay <execution-id>
assay run test-plan "Release checks" --replay <execution-id>
```

A replay runs **exactly the entries that execution ran**, even if entries were added to or
removed from the set since. Use it to compare like with like: "did this change regress on
exactly what we tested at the last release?". Entries without a recorded answer ask your
application again, so their answers can differ from the original run's.

- The execution must belong to the set or plan you name.
- Find execution ids with `GET /runs/test-sets/{id}/executions` (or `/runs/test-plans/…`),
  in the UI, or in the CLI's output.
- A standalone test has no executions, so `--replay` with `test` is an error, caught
  before anything is sent to Assay.

### All options

```text
assay run {test,test-set,test-plan} ID_OR_NAME
          [--replay EXECUTION_ID] [--fail-on STATUSES]
          [--policy FILE] [--max-failures [TEST_SET=]N]... [--min-pass-rate [TEST_SET=]RATE]...
          [--blocking-check LABEL]...
          [--no-wait] [--timeout SECONDS] [--interval SECONDS]
          [--json] [--junit FILE] [--markdown FILE]
```

The [waiting](#waiting) and [output](#output) options are described below.

## `assay batch`

A GenAI application answers differently every time, so one run can pass or fail by luck.
A **batch** runs the scope N times and lets a statistical test decide, for each check,
whether it passes, at a confidence you choose, or say there's not enough evidence yet.

```bash
assay batch test-set "Smoke" --statistical-test binomial_gate
assay batch test-plan "Release checks" -s binomial_gate --times 50 \
  --param target=0.95 --param confidence=0.99 --note "PR #123 at $GIT_SHA"
```

**Every run of a batch is a full, paid run:** application calls for tests without a
recorded answer, and judge calls for LLM checks. A batch of 30 times over a 20-entry plan
is 600 runs. See the cost first (see [Recipes](#see-a-batchs-cost-before-running-it)).

### Choosing the statistical test: `--statistical-test` / `-s`

The catalogue is `GET /statistics/tests`; those with `kind: batch` can be run here. A
standard installation has:

| Id | The question | Parameters (default) |
|---|---|---|
| `binomial_gate` | Does each check pass reliably, at least `target` of the time? | `target` (0.9), `confidence` (0.95) |
| `sequential_gate` | The same, run in waves until there's an answer | `target` (0.9), `confidence` (0.95) |
| `one_sample_t` | Does each scored check score high enough on average? | `confidence` (0.95), `difference` (0.05), `spread` (0.1) |
| `sequential_t` | The same, run in waves until there's an answer | `confidence` (0.95) |
| `judge_stability` | Does the judge give the same verdict consistently? | `target` (0.9), `confidence` (0.95) |
| `sequential_judge_stability` | The same, run in waves until there's an answer | `target` (0.9), `confidence` (0.95) |
| `trial` | No question: see how each check behaves | none |

The catalogue is data, so your installation may have more or different tests: check
`GET /statistics/tests` for the ids, each parameter's range, and the fewest times each
test can conclude at (`floor`).

- **"Until there's an answer"** tests run in waves and stop as soon as every check is
  decided, so they often cost less. `--times` is then the most they can run.
- **A trial** asks no question and ends `Done`, which never fails the step. Use it to
  learn how a new test set behaves before you gate on it.

### Parameters: `--param` / `-p`, `--times` / `-n`, `--note`

- **`--param KEY=VALUE`**, repeatable, sets one of the test's parameters. VALUE is read as
  JSON when it can be (`0.9` is a number, `true` a boolean); otherwise it's text.
  Parameters you leave out take their defaults. One without `=`, or a number JSON can't
  send (`NaN`, `Infinity`), is refused before anything is sent (exit 2); a value out of
  range is refused by Assay (exit 2, with the API's reason).
- **`--times N`** is how many times to run the scope. Left out, Assay uses the catalogue's
  suggestion for that test and its parameters. Fewer than the test's floor is refused:
  with that few runs it could never conclude anything.
- **`--note TEXT`** is kept with the batch and shown in Assay's lists and comparisons. Put
  what's being tested in it, e.g. the commit or pull request, so you can compare batches
  later. At most 500 characters.

### What the verdicts mean

Each check that the test applies to gets a verdict:

| Verdict | Meaning |
|---|---|
| `pass` | Proven at the confidence level, e.g. "95% confident it passes at least 90% of the time" |
| `fail` | The opposite is proven |
| `inconclusive` | Neither, at this size: more runs would decide it |
| no verdict | Nothing could be drawn, e.g. no run of it was evaluated |

The batch ends with one status:

| Status | Meaning | Fails the step? |
|---|---|---|
| `Passed` | Every applicable check proven to pass | No |
| `Failed` | At least one check proven to fail | **Always** |
| `Inconclusive` | Finished, but not every check could be decided | Only with `--fail-on fail,inconclusive` |
| `Incomplete` | Stopped before every run ran | **Always** |
| `NotRan` | Nothing could be decided: every run Not Ran, or every check errored in every run. Fix the cause; a bigger batch wouldn't help | **Always**, with exit 4 |
| `Done` | A trial finished | No |

A status this version of the CLI doesn't know fails the step, saying so: the gate never
passes on something it can't read. The same goes for a run's status. Update the CLI then.

### Choosing what fails the step: `--fail-on`

```bash
assay batch test-set "Smoke" -s binomial_gate                          # fails on fail
assay batch test-set "Smoke" -s binomial_gate --fail-on fail,inconclusive
```

- **`fail` (the default)** fails the step only when a check is *proven* to fail. A
  borderline check doesn't block you by chance.
- **`fail,inconclusive`** also fails the step when there isn't enough evidence that every
  check passes: a check that's inconclusive, or that got no verdict in an `Inconclusive`
  batch. This is stricter: use it for release gates. `inconclusive` alone means the same:
  `fail` is always included.
- The status and every check's verdict count: a `Failed` batch with inconclusive checks
  fails a strict gate for both reasons.
- A check proven to fail, and a batch that was stopped or couldn't decide anything, always
  fail the step, whatever you choose.

### Stopping a batch that's taking too long: `--stop-on-timeout`

If the step stops waiting before the batch's result, the batch normally carries on in Assay
and you keep paying for its runs. With `--stop-on-timeout`, the CLI stops it first: runs not
yet started are cancelled, and the batch ends `Incomplete`. It does so however the step
ends early, from the moment the batch is started:

- `--timeout` is reached: the step still exits 3 (timed out);
- the CI system cancels the job (it sends SIGTERM): exit 143;
- Ctrl-C: exit 130;
- an error, such as an expired token: exit 2.

Stopping gets 5 seconds, since the CI system kills the step soon after it cancels it
(GitHub Actions sends SIGINT, then SIGTERM 7.5 seconds later, then kills it). A SIGTERM
while the stop is being sent waits for it, so it isn't cut short, and the step then exits
143 (cancelled). If Assay refuses the stop, it takes longer, or it's interrupted (a second
Ctrl-C), the CLI says so and exits as it would have: stop the batch in Assay then.

If the job is cancelled while the request starting a run or batch is on its way, the CLI
can't know whether it started: it says so. Look in Assay before running it again.

Without `--stop-on-timeout`, nothing stops a batch but you: in the UI, or with
`POST /statistics/batches/{id}/stop`.

### All options

```text
assay batch {test,test-set,test-plan} ID_OR_NAME
            --statistical-test ID [--times N] [--param KEY=VALUE]... [--note TEXT]
            [--fail-on VERDICTS] [--stop-on-timeout]
            [--no-wait] [--timeout SECONDS] [--interval SECONDS]
            [--json] [--junit FILE] [--markdown FILE]
```

Per-check targets and leaving checks out of a batch are possible through the API
(`targets`, `leave_out` on `POST /statistics/batches`) but not yet through the CLI.

## Waiting

`run` and `batch` wait until everything has finished, then report.

| Option | Default | What it does |
|---|---|---|
| `--timeout SECONDS` | `3600` (an hour) | Give up waiting after this long and exit 3. `0` waits forever |
| `--interval SECONDS` | `5` | Time between two checks on its progress; a number more than 0 |
| `--no-wait` | | Start it, print its id, and exit 0 straight away |

- **Progress** is printed to stderr each time it changes: runs finished by outcome for a
  run, runs and times done (and waves) for a batch.
- **Set the CLI's `--timeout` below your CI job's own time limit,** so the CLI reports the
  timeout (and can stop a batch) before the CI system kills the step.
- **A passing failure doesn't end the wait.** When a check on its progress can't reach the
  API, gets no reply, or gets a `429`, `502`, `503` or `504` (a gateway, or a busy API), the
  CLI says so and tries again after `--interval`, until `--timeout`. A `500` (the API's own
  failure: its database busy for a moment, or a bug) is tried again 3 times, then ends it
  with exit 2; a gateway's failures before or between them don't count. Any other refusal,
  such as a `401`, ends it with exit 2 at once.
- **Reads outside the wait get a minute.** Looking up what to run, `assay health`, and
  reading an execution's runs once it has finished are tried again the same way for up to
  a minute, whatever `--timeout` is. Starting a run or a batch is never tried again: it may
  have started.
- **The last check comes on time.** The CLI never sleeps past `--timeout`, and no request,
  each page of a list included, is sent after the time left (or 5 seconds, for the check at
  the deadline). Each step of a request (connecting, sending, each read of the reply) waits
  no longer than the time left either, whatever `--http-timeout` is. So the step reports
  the timeout, and stops a batch, a few seconds after `--timeout`. A reply that arrives
  very slowly, a little at a time, can take longer: each step is capped, not the request
  as a whole.
- **Timing out stops waiting, not the work.** The runs carry on in Assay and their results
  are kept there. Only a batch with `--stop-on-timeout` is stopped.
- **`--no-wait`** prints the new execution's, run's or batch's id on stdout (with `--json`:
  `{"kind", "id", "scope"}`). Nothing is judged, so it always exits 0 once the run has
  started. Use it to start something and look at it in the UI later.

## Output

The outcome is printed on **stdout**. Progress, "Started …" lines and errors go to
**stderr**, so you can capture or pipe the outcome on its own. Both are UTF-8 on every
runner, Windows included. The report files are written before the outcome is printed. If
one can't be written (a read-only folder, a path that's a folder), the CLI says which, still
writes the others and prints the outcome, and exits 2.

### Text (the default)

```text
FAILED: test set 'Smoke', 3 runs: 1 Green, 1 Amber, 0 Red, 1 NotRan
  execution id: 0b7e…
  ✗ Refund policy: Amber: not met: Correctness
  ! Live lookup: Not Ran: No application configured: no URL is set
  fails on: Amber, Red, NotRan
```

The first line says `PASSED` or `FAILED`, then what ran and the outcomes. Each line after
the id is something that failed the step: `✗` a check not met, or a verdict of `fail`;
`!` a run that couldn't run. A plan's runs are named within their test set
(`Smoke › Greeting`). The first 50 are listed; the JUnit report has them all.

For a batch, the first line carries Assay's one-sentence summary, and each failure names
its check within its test (`Smoke / Refund policy › Correctness: fail: …`).

### JSON: `--json`

Prints one JSON object instead of the text, for scripts:

```json
{
  "kind": "execution",
  "id": "0b7e…",
  "scope": {"kind": "test-set", "id": "4e86…", "name": "Smoke"},
  "status": "NotRan",
  "passed": false,
  "summary": "FAILED: test set 'Smoke', 3 runs: 1 Green, 1 Amber, 0 Red, 1 NotRan",
  "counts": {"Green": 1, "Amber": 1, "Red": 0, "NotRan": 1},
  "fail_on": ["Amber", "Red", "NotRan"],
  "reason": null,
  "judged": true,
  "gate": [
    {"rule": "none_allowed", "test_set": null, "limit": 0, "actual": 2, "broken": true,
     "text": "2 failures, none allowed"}
  ],
  "failures": [
    {"name": "Refund policy", "group": "Smoke", "outcome": "failed",
     "message": "Amber: not met: Correctness"},
    {"name": "Live lookup", "group": "Smoke", "outcome": "error",
     "message": "Not Ran: No application configured: no URL is set"}
  ]
}
```

| Field | Meaning |
|---|---|
| `kind` | `run` (a standalone test), `execution` (a set or plan) or `batch` |
| `id` | The run's, execution's or batch's id in Assay |
| `scope` | What ran: `kind`, `id`, `name` |
| `status` | A run's outcome; an execution's worst outcome; a batch's status |
| `passed` | Whether the step passed: the same as exit code 0 |
| `counts` | Runs by outcome; for a batch, checks by verdict (`pass`, `fail`, `inconclusive`, `none`) |
| `fail_on` | The rule it was judged by |
| `reason` | Why it failed whatever `fail_on` says: a batch stopped or undecided, a status the CLI doesn't know; `null` otherwise |
| `judged` | `false` when it failed only because runs Not Ran, or a batch couldn't decide anything: exit 4 |
| `gate` | A run's or execution's rules as they held: `rule` (`max_failures`, `min_pass_rate`, `none_allowed`, `blocking_check`), `test_set` (or `null` for the whole), `limit`, `actual`, `broken`, `text`. Empty for a batch |
| `failures` | What failed the step: `outcome` is `failed` or `error` (a run that couldn't run, a batch that ended `Incomplete` or `NotRan`, a status the CLI doesn't know) |

For example, `assay run test-set Smoke --json | jq -r '.failures[].name'`.

### JUnit XML: `--junit FILE`

Writes a JUnit XML report, which most CI systems display as test results: GitLab, Azure
Pipelines, Jenkins, CircleCI, TeamCity, and GitHub through a test-report action. Missing
folders in the path are created.

| In the report | A run or execution | A batch |
|---|---|---|
| `<testsuite>` | The test set (or test); for a plan, each of its test sets | Each entry: `test set / test` |
| `<testcase>` | Each run, named after its test | Each check, named by its label |
| `<failure>` | A run whose outcome fails the step (`Amber`, `Red`) | A verdict that fails the step |
| `<error>` | A run that couldn't run (`NotRan`), or with a status the CLI doesn't know | A batch that ended `Incomplete` or `NotRan`, or with a status the CLI doesn't know: one case named after the statistical test, with the reason |
| `<skipped>` | | A check with no verdict, or a verdict you don't fail on (e.g. `inconclusive` by default) |
| `<system-out>` | The outcome of a run you tolerated | |

When two cases would share a suite and a name, for example the same test in two sets of a
plan, each name ends with its entry's id in brackets: CI systems merge cases with the same
names.

Characters XML can't hold, such as colour codes in an application's error, are left out, so
CI systems can always read the report.

Each suite has a property `assay.execution_id` (or `assay.run_id`, `assay.batch_id`), so
you can find the run in Assay from the CI system.

With [limits or blocking checks](#tolerating-some-failures), a suite named `Assay gate`
holds one test case per rule, failed when the rule broke.

### Markdown: `--markdown FILE`, and GitHub's job summary

The CLI appends a short Markdown summary to `FILE`: ✅ or ❌, the outcome, the id, and a
table of what failed. On **GitHub Actions** it also appends it to `$GITHUB_STEP_SUMMARY` by
itself, so the summary shows on the run's page without any option. Use `--markdown` on
other systems, e.g. to post it as a pull request comment. Missing folders in the path are
created, and a file given both ways gets the summary once.

## Exit codes

| Code | Meaning | Typical causes |
|---|---|---|
| `0` | Passed | Every run, or every verdict, met your rule. Also: `--no-wait` started it; `health` is ready |
| `1` | Failed the gate | A run you fail on, a limit or blocking check broken, a verdict you fail on, an `Incomplete` batch, a status the CLI doesn't know; `health` not ready |
| `2` | Error: nothing was judged | No URL; bad arguments or policy file; the API unreachable; a name with no match; the API refused the request (the reason is shown); a report file that can't be written; anything unexpected |
| `3` | Timed out | Not finished within `--timeout`, usually because no worker is running |
| `4` | Couldn't be judged | It failed only because runs Not Ran: the application under test or the judge didn't answer. With the runs that Not Ran left out, it would have passed. Also a `NotRan` batch |
| `130` | Interrupted | Ctrl-C |
| `143` | Cancelled | The CI system cancelled the job (SIGTERM) |

A pipeline step fails on any code other than 0. To tell the reasons apart in a script:

```bash
set +e
assay run test-set "Smoke"
code=$?
set -e
case $code in
  0) echo "passed" ;;
  1) echo "quality gate failed"; exit 1 ;;
  3) echo "Assay didn't finish: is a worker running?"; exit 1 ;;
  4) echo "the application or the judge didn't answer"; exit 1 ;;
  *) echo "couldn't run Assay"; exit 1 ;;
esac
```

## In a pipeline

Each example installs the CLI, checks Assay is ready, runs a plan, and publishes the
JUnit report even when the gate fails. Replace `<tag-or-commit>` and the names with yours.

### GitHub Actions

```yaml
name: Assay
on:
  pull_request:
  workflow_dispatch:

jobs:
  assay:
    # a runner that can reach Assay's API (see "Before you start")
    runs-on: [self-hosted, assay]
    timeout-minutes: 70
    env:
      ASSAY_API_URL: ${{ secrets.ASSAY_API_URL }}
    steps:
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      - name: Install the Assay CLI
        run: pip install "assay-cli @ git+https://github.com/cupelworks/assay@<tag-or-commit>#subdirectory=cli"

      - name: Assay is ready
        run: assay health

      - name: Release checks
        run: assay run test-plan "Release checks" --junit assay-results.xml --timeout 3600

      - name: Keep the report
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: assay-results
          path: assay-results.xml
```

The outcome appears in the run's job summary by itself.

### Azure Pipelines

```yaml
pool: assay-agents   # an agent pool inside Assay's network

steps:
  - task: UsePythonVersion@0
    inputs:
      versionSpec: "3.12"

  - script: pip install "assay-cli @ git+https://github.com/cupelworks/assay@<tag-or-commit>#subdirectory=cli"
    displayName: Install the Assay CLI

  - script: assay health
    displayName: Assay is ready
    env:
      ASSAY_API_URL: $(AssayApiUrl)

  - script: assay run test-plan "Release checks" --junit $(Common.TestResultsDirectory)/assay.xml
    displayName: Release checks
    env:
      ASSAY_API_URL: $(AssayApiUrl)

  - task: PublishTestResults@2
    condition: always()
    inputs:
      testResultsFormat: JUnit
      testResultsFiles: $(Common.TestResultsDirectory)/assay.xml
      testRunTitle: Assay
```

### GitLab CI

```yaml
assay:
  image: python:3.12-slim
  tags: [assay]          # a runner inside Assay's network
  variables:
    ASSAY_API_URL: $ASSAY_API_URL   # a masked CI/CD variable
  script:
    - pip install "assay-cli @ git+https://github.com/cupelworks/assay@<tag-or-commit>#subdirectory=cli"
    - assay health
    - assay run test-plan "Release checks" --junit assay.xml
  artifacts:
    when: always
    reports:
      junit: assay.xml
```

GitLab shows the results in the pipeline's **Tests** tab and on merge requests.

### Jenkins

```groovy
stage('Assay') {
  environment { ASSAY_API_URL = credentials('assay-api-url') }
  steps {
    sh 'pip install "assay-cli @ git+https://github.com/cupelworks/assay@<tag-or-commit>#subdirectory=cli"'
    sh 'assay health'
    sh 'assay run test-plan "Release checks" --junit assay.xml'
  }
  post { always { junit 'assay.xml' } }
}
```

### Any other CI system

Anything that runs a shell command and reads its exit code works: install the package,
set `ASSAY_API_URL`, run `assay …`. If the system reads JUnit XML, pass `--junit`.

## Recipes

### A quick gate on every pull request

Run a small set of recorded and cheap checks once:

```bash
assay run test-set "Smoke" --timeout 900
```

### A statistical gate before a release

Prove each check passes at least 9 times in 10, and fail if that can't be shown:

```bash
assay batch test-plan "Release checks" -s sequential_gate \
  --param target=0.9 --fail-on fail,inconclusive \
  --note "release $VERSION ($GIT_SHA)" \
  --timeout 7200 --stop-on-timeout --junit assay.xml
```

`sequential_gate` stops as soon as every check is decided, so it usually needs fewer runs
than `binomial_gate`.

### A nightly check that the judge is consistent

```bash
assay batch test-set "Judge calibration" -s judge_stability --note "nightly $(date -I)"
```

### Tolerate partial passes, but never errors

```bash
assay run test-plan "Release checks" --fail-on Red,NotRan
```

### A few failures in the long tail, none in the smoke tests

```yaml
# assay-gate.yml, reviewed with the code
test_plan: "Release checks"
max_failures: 2
test_sets:
  "Smoke": { max_failures: 0 }
blocking_checks: ["Toxicity"]
```

```bash
assay run test-plan "Release checks" --policy assay-gate.yml --junit assay.xml
```

### Compare against exactly what you tested last time

```bash
assay run test-set "Regression" --replay "$LAST_RELEASE_EXECUTION_ID"
```

### Start now, look later

```bash
id=$(assay run test-plan "Exploratory" --no-wait)
echo "See execution $id in Assay"
```

### See a batch's cost before running it

The CLI has no estimate command yet. Send the same body to the API's estimate, which
creates nothing:

```bash
curl -s -X POST "$ASSAY_API_URL/statistics/estimate" \
  -H 'Content-Type: application/json' \
  -d '{"test_set_id": "<id>", "statistical_test": "binomial_gate", "times": 30}'
```

It returns the fewest runs that can conclude anything, suggested sizes, and the
application and judge calls they'd make.

### Use the outcome in a script

```bash
assay run test-set "Smoke" --json > outcome.json || true
jq -r '.failures[] | "\(.name): \(.message)"' outcome.json
```

## Troubleshooting

| You see | What it means, and what to do |
|---|---|
| `give the API's URL with --url or $ASSAY_API_URL` | Set one of them |
| `POST …: no reply from Assay (…); it may have been carried out …`, or `POST …: 504 …; it may have been carried out …` (or `500`, `502`, `503`) | Assay, or a gateway in front of it, got the request but gave no answer in time, or Assay failed after saving it: the run or batch may have started, and the CLI has no id for it. Look in Assay's lists before running it again (and stop a batch there if needed). Raise `--http-timeout` if the API is slow |
| `couldn't reach Assay (…)` | Wrong URL, or the runner can't reach the network the API is in: see [Before you start](#before-you-start). Check it with `curl $ASSAY_API_URL/health` from the runner |
| `301 redirected to …` (or `302`, `307`, `308`) | Give the address it names with `--url`: the CLI never follows a redirect, so a run is never started twice or not at all |
| `an empty reply — is the URL Assay's API?` | As below |
| `the reply isn't JSON — is the URL Assay's API?` | The URL points at something else, e.g. the web interface, or a proxy's sign-in page. Point it at the API, including its path (`/api`) |
| `404 …` on every command | The URL is missing the path the API is served under |
| `No test set is named 'X'` | Names must match exactly, including case. Check spelling and quotes, or use the id |
| `At least 2 tests are named 'X' (…)` | Use one of the ids listed |
| `409 No Test Set Entries found …` | The set is empty: add tests to it |
| `409 No test types assigned …` | An entry has no checks: assign at least one |
| `422 …` on `batch` | A parameter is out of range, `--times` is below the test's floor, or the statistical test id is unknown. The message says which |
| Stuck at `0/N runs finished`, then exit 3 | No worker is running, or it can't reach the broker: `assay health` |
| Every run `NotRan` | Usually the application under test: no target set, or it can't be reached from Assay's workers. Check it with `POST /settings/target/checks` |
| Judge checks fail with a reason | No judge chosen, or its key is missing: `POST /settings/judge/checks` |
| Batch ends `NotRan` | Nothing could be decided: fix the cause above; more runs won't help |
| The step passes but you expected it to fail | Check `--fail-on`: e.g. `inconclusive` doesn't fail a batch by default. With limits, the gate line says which rule tolerated the failures |
| `assay-gate.yml: …` | The policy file has a mistake, named in the message: nothing was sent to Assay. Fix the file |
| `the policy names test set 'X', which this test plan doesn't run` | A typo, or a set renamed or unlinked since: use the names listed |
| `assay-gate.yml is for the test plan 'X', not …` | The file names the scope it's for (`test_plan:`): give it to that scope only |
| Exit `4` | The application under test or the judge didn't answer: check them (`POST /settings/target/checks`, `POST /settings/judge/checks`) |

`--http-timeout` raises the limit on each step of a request (connecting, each read) if the
API is slow to answer, e.g. behind a busy proxy.

## What it doesn't do (yet)

- **Names with accented capitals on SQLite.** A name is found through the API's search,
  which on SQLite ignores case for ASCII letters only: `École smoke` may not be found.
  Give the id instead. PostgreSQL, as on Azure, finds it.
- **Reaching the API on the Azure deployment.** Its ingress is internal to its Container
  Apps environment, so a pipeline runner can't call it yet.
- **A different application per run.** A run calls the application set in Assay's
  settings (the Settings page, or `ASSAY_TARGET_*`). Point that at the environment the
  pipeline deploys to, such as staging. Testing each pull request's own preview URL needs
  a per-run target, which Assay doesn't have yet.
- **Creating or changing tests, sets, plans or settings.** Do that in the UI or the API;
  the CLI runs what's there.
- **Estimating a batch's cost, comparing two batches, per-check targets or leaving checks
  out.** These are in the API (`/statistics/estimate`, `/statistics/comparisons`,
  `targets` and `leave_out`), not yet in the CLI.
- **Waiting for something already started.** `--no-wait` starts a run and leaves it; there
  is no command to pick it up again later.
- **A GitHub Action.** Use the CLI in a `run:` step, as above.

## Reference

```text
assay [--version] [--url URL] [--header 'NAME: VALUE']... [--http-timeout SECONDS]
      COMMAND ...

assay health

assay run {test,test-set,test-plan} ID_OR_NAME
          [--replay EXECUTION_ID] [--fail-on STATUSES]
          [--no-wait] [--timeout SECONDS] [--interval SECONDS]
          [--json] [--junit FILE] [--markdown FILE]

assay batch {test,test-set,test-plan} ID_OR_NAME
            --statistical-test ID [--times N] [--param KEY=VALUE]... [--note TEXT]
            [--fail-on VERDICTS] [--stop-on-timeout]
            [--no-wait] [--timeout SECONDS] [--interval SECONDS]
            [--json] [--junit FILE] [--markdown FILE]
```

| Option | Commands | Default | Description |
|---|---|---|---|
| `--url URL` | all | `$ASSAY_API_URL` | Assay's API, including its path |
| `--header 'NAME: VALUE'` | all | `$ASSAY_API_HEADERS` | A header for every request; repeatable |
| `--http-timeout SECONDS` | all | `30` | Timeout of each step of a request |
| `--version` | | | Print the CLI's version |
| `-h`, `--help` | all | | Help for the CLI or a command |
| `--replay EXECUTION_ID` | `run` | | Replay a past execution of the set or plan |
| `--fail-on STATUSES` | `run` | `Amber,Red,NotRan`, or the policy's | Run outcomes that fail a run |
| `--policy FILE` | `run` | | A YAML gate policy |
| `--max-failures [TEST_SET=]N` | `run` | none: no failure allowed | At most N failing runs, over the whole or in a set; repeatable |
| `--min-pass-rate [TEST_SET=]RATE` | `run` | none | At least RATE (0 to 1) passing, over the whole or in a set; repeatable |
| `--blocking-check LABEL` | `run` | none | A check whose failure always fails the step; repeatable |
| `--statistical-test ID`, `-s` | `batch` | required | The statistical test to run |
| `--times N`, `-n` | `batch` | the catalogue's suggestion | Times to run the scope (the most, for tests that run in waves) |
| `--param KEY=VALUE`, `-p` | `batch` | the test's defaults | A parameter; repeatable |
| `--note TEXT` | `batch` | | Kept with the batch |
| `--fail-on VERDICTS` | `batch` | `fail` | `fail` always counts; add `inconclusive` for a stricter gate |
| `--stop-on-timeout` | `batch` | off | Stop the batch when the wait ends early: `--timeout`, a cancelled job, Ctrl-C, an error |
| `--no-wait` | `run`, `batch` | off | Start, print the id, exit 0 |
| `--timeout SECONDS` | `run`, `batch` | `3600` | Give up waiting; `0` waits forever |
| `--interval SECONDS` | `run`, `batch` | `5` | Time between progress checks; more than 0 |
| `--json` | `run`, `batch` | off | The outcome as JSON |
| `--junit FILE` | `run`, `batch` | | Write JUnit XML |
| `--markdown FILE` | `run`, `batch` | | Append a Markdown summary |

| Environment variable | Used for |
|---|---|
| `ASSAY_API_URL` | The API's URL, when `--url` isn't given |
| `ASSAY_API_HEADERS` | A JSON object of headers for every request |
| `GITHUB_STEP_SUMMARY` | Set by GitHub Actions: the Markdown summary is appended to it |

## Development

The CLI lives in `cli/` as a package of its own (`assay-cli`); its tests are in
`tests/cli/` and run with the backend's suite: one file per module, and one end to end
against the real API in-process, with a stand-in worker.

```bash
pip install -e ".[dev]" -e ./cli
pytest tests/cli
ruff check cli/ tests/cli
```

| Module | What it holds |
|---|---|
| `cli.py` | The commands and the exit codes; cancels, and stopping a batch |
| `arguments.py` | The options, and every rule about them checked before a request is sent |
| `client.py` | The API calls: finding by name, starting, reading; how each failure is classed |
| `waiting.py` | Waiting and trying again: what's retried, how often, and the deadlines |
| `gate.py` | Whether an outcome passes the step, by which rules: pure functions of the API's replies |
| `policy.py` | A gate policy read from a YAML file, and the flags over it |
| `report.py` | The text, Markdown and JUnit outputs |

## License

[AGPL-3.0-only](LICENSE), like Assay. Copyright (C) 2026 Francesco Campanile.
