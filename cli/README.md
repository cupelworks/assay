# Assay CLI

Run Assay's tests from a CI/CD pipeline, and fail the step when they fail.

`assay` starts a run of a test, a test set or a test plan, or a statistics batch, waits
for it to finish, prints what happened, and exits with a code the pipeline understands.
It writes JUnit XML for your CI system's test results and a summary for GitHub's job page.

It's a thin client of [Assay's API](../README.md#api). It needs only `httpx`, not the
backend, its database, its worker or the NLP models, so it installs in seconds on any
runner.

```console
$ assay run test-plan "Release checks" --junit assay.xml
Started execution 3f1c… of test plan 'Release checks': 24 runs.
  0/24 runs finished
  17/24 runs finished (16 Green, 1 Red)
  24/24 runs finished (22 Green, 1 Amber, 1 Red)
FAILED: test plan 'Release checks', 24 runs: 22 Green, 1 Amber, 1 Red, 0 NotRan
  execution id: 3f1c…
  ✗ Refund policy: Red: not met: Correctness, Contains
  ✗ Greeting: Amber: not met: BLEU
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
  which verdicts, fail it.
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

**About the network.** The [Azure deployment](../infra/azure/README.md) gives the API no
address outside its private network, so runners hosted by GitHub, Microsoft or GitLab
can't reach it. Use a self-hosted runner or agent inside that network, or in a network
peered with it. Locally, the API is at `http://127.0.0.1:8000`.

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
| Timeout of each request | `--http-timeout SECONDS` | | `30` |

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
  same name, `--header` wins. Keep tokens in your CI system's secrets, never in the
  pipeline file.

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
- **Two tests with the same name** are an error that lists their ids: use an id. Test set
  and test plan names are unique in Assay, so they can't clash.
- **Ids** are shown in the UI and returned by the API, e.g. `GET /test-sets?q=smoke`.

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
| API unreachable | Wrong URL, network or proxy | 2 |

Run it as a first step so a broken setup fails quickly and clearly, rather than as a
timeout an hour later.

## `assay run`

Runs a scope once, waits for every run to finish, and fails the step if any run's outcome
is one you fail on.

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

### Choosing what fails the step: `--fail-on`

By default **anything short of Green fails the step**: `--fail-on Amber,Red,NotRan`.
Give the outcomes you want to fail on, separated by commas, in any case:

```bash
assay run test-set "Smoke" --fail-on Red,NotRan   # tolerate Amber
assay run test-set "Smoke" --fail-on red          # tolerate Amber and NotRan too
```

Runs you tolerate still appear in the JUnit report, with their outcome in the test case's
output.

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
- A standalone test has no executions, so `--replay` with `test` is an error.

### All options

```text
assay run {test,test-set,test-plan} ID_OR_NAME
          [--replay EXECUTION_ID] [--fail-on STATUSES]
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
  Parameters you leave out take their defaults. A value out of range is refused (exit 2,
  with the API's reason).
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
| `Failed` | At least one check proven to fail | Yes, by default |
| `Inconclusive` | Finished, but not every check could be decided | Only with `--fail-on fail,inconclusive` |
| `Incomplete` | Stopped before every run ran | **Always** |
| `NotRan` | Nothing could be decided: every run Not Ran, or every check errored in every run. Fix the cause; a bigger batch wouldn't help | **Always** |
| `Done` | A trial finished | No |

### Choosing what fails the step: `--fail-on`

```bash
assay batch test-set "Smoke" -s binomial_gate                          # fails on fail
assay batch test-set "Smoke" -s binomial_gate --fail-on fail,inconclusive
```

- **`fail` (the default)** fails the step only when a check is *proven* to fail. A
  borderline check doesn't block you by chance.
- **`fail,inconclusive`** also fails the step when there isn't enough evidence that every
  check passes. This is stricter: use it for release gates.
- A batch that was stopped or couldn't decide anything always fails, whatever you choose.

### Stopping a batch that's taking too long: `--stop-on-timeout`

If the step gives up waiting (`--timeout`), the batch normally carries on in Assay and you
keep paying for its runs. With `--stop-on-timeout`, the CLI stops it first: runs not yet
started are cancelled, and the batch ends `Incomplete`. The step still exits 3 (timed out).

Cancelling the pipeline or pressing Ctrl-C doesn't stop a batch. Stop it in the UI or with
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
| `--interval SECONDS` | `5` | Time between two checks on its progress |
| `--no-wait` | | Start it, print its id, and exit 0 straight away |

- **Progress** is printed to stderr each time it changes: runs finished by outcome for a
  run, runs and times done (and waves) for a batch.
- **Set the CLI's `--timeout` below your CI job's own time limit,** so the CLI reports the
  timeout (and can stop a batch) before the CI system kills the step.
- **Timing out stops waiting, not the work.** The runs carry on in Assay and their results
  are kept there. Only a batch with `--stop-on-timeout` is stopped.
- **`--no-wait`** prints the new execution's, run's or batch's id on stdout (with `--json`:
  `{"kind", "id", "scope"}`). Nothing is judged, so it always exits 0 once the run has
  started. Use it to start something and look at it in the UI later.

## Output

The outcome is printed on **stdout**. Progress, "Started …" lines and errors go to
**stderr**, so you can capture or pipe the outcome on its own.

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
`!` a run that couldn't run. The first 50 are listed; the JUnit report has them all.

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
| `failures` | What failed the step: `outcome` is `failed` or `error` (a run that couldn't run) |

For example, `assay run test-set Smoke --json | jq -r '.failures[].name'`.

### JUnit XML: `--junit FILE`

Writes a JUnit XML report, which most CI systems display as test results: GitLab, Azure
Pipelines, Jenkins, CircleCI, TeamCity, and GitHub through a test-report action. Missing
folders in the path are created.

| In the report | A run or execution | A batch |
|---|---|---|
| `<testsuite>` | The test set or plan (or test) | Each entry: `test set / test` |
| `<testcase>` | Each run, named after its test | Each check, named by its label |
| `<failure>` | A run whose outcome fails the step (`Amber`, `Red`) | A verdict that fails the step |
| `<error>` | A run that couldn't run (`NotRan`) | |
| `<skipped>` | | A check with no verdict, or a verdict you don't fail on (e.g. `inconclusive` by default) |
| `<system-out>` | The outcome of a run you tolerated | |

Each suite has a property `assay.execution_id` (or `assay.run_id`, `assay.batch_id`), so
you can find the run in Assay from the CI system.

### Markdown: `--markdown FILE`, and GitHub's job summary

The CLI appends a short Markdown summary to `FILE`: ✅ or ❌, the outcome, the id, and a
table of what failed. On **GitHub Actions** it also appends it to `$GITHUB_STEP_SUMMARY` by
itself, so the summary shows on the run's page without any option. Use `--markdown` on
other systems, e.g. to post it as a pull request comment.

## Exit codes

| Code | Meaning | Typical causes |
|---|---|---|
| `0` | Passed | Every run, or every verdict, met your rule. Also: `--no-wait` started it; `health` is ready |
| `1` | Failed the gate | A run you fail on, a verdict you fail on, an `Incomplete` or `NotRan` batch; `health` not ready |
| `2` | Error: nothing was judged | No URL; bad arguments; the API unreachable; a name with no match; the API refused the request (the reason is shown) |
| `3` | Timed out | Not finished within `--timeout`, usually because no worker is running |
| `130` | Interrupted | Ctrl-C |

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
| `couldn't reach Assay (…)` | Wrong URL, or the runner can't reach the network the API is in: see [Before you start](#before-you-start). Check it with `curl $ASSAY_API_URL/health` from the runner |
| `the reply isn't JSON — is the URL Assay's API?` | The URL points at something else, e.g. the web interface, or a proxy's sign-in page. Point it at the API, including its path (`/api`) |
| `404 …` on every command | The URL is missing the path the API is served under |
| `No test set is named 'X'` | Names must match exactly, including case. Check spelling and quotes, or use the id |
| `2 tests are named 'X' (…)` | Use one of the ids listed |
| `409 No Test Set Entries found …` | The set is empty: add tests to it |
| `409 No test types assigned …` | An entry has no checks: assign at least one |
| `422 …` on `batch` | A parameter is out of range, `--times` is below the test's floor, or the statistical test id is unknown. The message says which |
| Stuck at `0/N runs finished`, then exit 3 | No worker is running, or it can't reach the broker: `assay health` |
| Every run `NotRan` | Usually the application under test: no target set, or it can't be reached from Assay's workers. Check it with `POST /settings/target/checks` |
| Judge checks fail with a reason | No judge chosen, or its key is missing: `POST /settings/judge/checks` |
| Batch ends `NotRan` | Nothing could be decided: fix the cause above; more runs won't help |
| The step passes but you expected it to fail | Check `--fail-on`: e.g. `inconclusive` doesn't fail a batch by default |

`--http-timeout` raises the limit on each request if the API is slow to answer, e.g.
behind a busy proxy.

## What it doesn't do (yet)

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
| `--http-timeout SECONDS` | all | `30` | Timeout of each request |
| `--version` | | | Print the CLI's version |
| `-h`, `--help` | all | | Help for the CLI or a command |
| `--replay EXECUTION_ID` | `run` | | Replay a past execution of the set or plan |
| `--fail-on STATUSES` | `run` | `Amber,Red,NotRan` | Run outcomes that fail the step |
| `--statistical-test ID`, `-s` | `batch` | required | The statistical test to run |
| `--times N`, `-n` | `batch` | the catalogue's suggestion | Times to run the scope (the most, for tests that run in waves) |
| `--param KEY=VALUE`, `-p` | `batch` | the test's defaults | A parameter; repeatable |
| `--note TEXT` | `batch` | | Kept with the batch |
| `--fail-on VERDICTS` | `batch` | `fail` | `fail`, `inconclusive`, or both |
| `--stop-on-timeout` | `batch` | off | Stop the batch when `--timeout` is reached |
| `--no-wait` | `run`, `batch` | off | Start, print the id, exit 0 |
| `--timeout SECONDS` | `run`, `batch` | `3600` | Give up waiting; `0` waits forever |
| `--interval SECONDS` | `run`, `batch` | `5` | Time between progress checks |
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
`tests/cli/` and run with the backend's suite, end to end against the real API in-process.

```bash
pip install -e ".[dev]" -e ./cli
pytest tests/cli
ruff check cli/ tests/cli
```

| Module | What it holds |
|---|---|
| `cli.py` | The commands, their options, waiting, and the exit codes |
| `client.py` | The API calls: finding by name, starting, reading |
| `gate.py` | Whether an outcome passes the step: pure functions of the API's replies |
| `report.py` | The text, Markdown and JUnit outputs |

## License

[AGPL-3.0-only](LICENSE), like Assay. Copyright (C) 2026 Francesco Campanile.
