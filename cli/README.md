# Assay CLI

Run Assay's tests from a CI/CD pipeline, and fail the step when they fail.

`assay` starts a run of a test, test set or test plan (or a statistics batch), waits for
it to finish, prints what happened and exits with a code the pipeline understands. It's a
thin client of [Assay's API](../README.md#api): it needs only `httpx`, not the backend, its
database or the NLP models.

```console
$ assay run test-plan "Release checks"
Started execution 3f1c… of test plan 'Release checks': 24 runs.
  0/24 runs finished
  17/24 runs finished (16 Green, 1 Red)
  24/24 runs finished (22 Green, 1 Amber, 1 Red)
FAILED: test plan 'Release checks', 24 runs: 22 Green, 1 Amber, 1 Red, 0 NotRan
  execution id: 3f1c…
  ✗ Refund policy: Red: not met: Correctness, Contains
  ✗ Greeting: Amber: not met: BLEU
  fails on: Amber, Red, NotRan
$ echo $?
1
```

## Install

```bash
pip install "assay-cli @ git+https://github.com/cupelworks/assay@main#subdirectory=cli"
```

Pin a tag or commit instead of `main` in a pipeline. From a clone: `pip install -e cli/`.

## Connect

| Setting | Flag | Environment variable |
|---|---|---|
| The API's URL, including any path it's served under | `--url` | `ASSAY_API_URL` |
| Extra headers, e.g. for a gateway in front of the API | `--header 'Name: value'` (repeatable) | `ASSAY_API_HEADERS` (a JSON object) |
| Timeout of each request | `--http-timeout` (default 30 s) | |

Assay has no users of its own, so the CLI sends no credentials unless you add headers.
`assay health` checks the API answers and a worker is ready (exit 0) before you rely on it.

## Run once and gate on the outcomes

```bash
assay run test-set "Smoke"                  # by exact name …
assay run test-plan 4e86003a-9e28-…         # … or by id
assay run test 9b2d…                        # a standalone test
assay run test-set "Smoke" --replay <execution-id>   # exactly the entries of a past execution
```

Each run ends `Green`, `Amber`, `Red` or `NotRan`. By default anything short of `Green`
fails the step; `--fail-on Red,NotRan` tolerates `Amber`.

## Run with statistics and gate on the verdicts

A GenAI application answers differently every time, so one run can pass by luck. A batch
runs the scope N times and gives each check a verdict at a stated confidence:

```bash
assay batch test-plan "Release checks" \
  --statistical-test <id from GET /statistics/tests> \
  --times 30 --param target=0.9 \
  --note "PR #123 at $GIT_SHA" \
  --fail-on fail,inconclusive --stop-on-timeout
```

- `--fail-on fail` (the default) fails the step only when a check is **proven** to fail.
  Add `inconclusive` to also fail when there isn't enough evidence to say it passes.
- A batch that was stopped (`Incomplete`) or couldn't decide anything (`NotRan`) always
  fails.
- See the cost first with `POST /statistics/estimate`: every batch run is a full, paid
  run. `--stop-on-timeout` stops the batch if the step gives up waiting, so its remaining
  runs aren't paid for.

## Options for both

| Option | Default | |
|---|---|---|
| `--timeout SECONDS` | 3600 | Give up waiting (exit 3); `0` waits forever |
| `--interval SECONDS` | 5 | Time between progress checks |
| `--no-wait` | | Start it, print its id, exit 0 |
| `--json` | | Print the outcome as JSON on stdout |
| `--junit FILE` | | Write JUnit XML: one test case per run, or per check of a batch |
| `--markdown FILE` | | Append a Markdown summary; on GitHub Actions it's added to the job summary on its own |

Progress goes to stderr, the outcome to stdout.

**Exit codes:** `0` passed · `1` failed the gate · `2` error (bad arguments, the API
unreachable or refusing the request) · `3` timed out, nothing concluded.

## In a pipeline

### GitHub Actions

```yaml
- name: Assay release checks
  env:
    ASSAY_API_URL: ${{ secrets.ASSAY_API_URL }}
  run: |
    pip install "assay-cli @ git+https://github.com/cupelworks/assay@v2.4.3#subdirectory=cli"
    assay run test-plan "Release checks" --junit assay-results.xml
```

The outcome appears in the job summary on its own.

### Azure Pipelines

```yaml
- script: |
    pip install "assay-cli @ git+https://github.com/cupelworks/assay@v2.4.3#subdirectory=cli"
    assay run test-plan "Release checks" --junit $(Build.ArtifactStagingDirectory)/assay.xml
  env:
    ASSAY_API_URL: $(AssayApiUrl)
- task: PublishTestResults@2
  condition: always()
  inputs:
    testResultsFormat: JUnit
    testResultsFiles: $(Build.ArtifactStagingDirectory)/assay.xml
```

### GitLab CI

```yaml
assay:
  script:
    - pip install "assay-cli @ git+https://github.com/cupelworks/assay@v2.4.3#subdirectory=cli"
    - assay run test-plan "Release checks" --junit assay.xml
  artifacts:
    when: always
    reports:
      junit: assay.xml
```

## Before you rely on it

- **The runner must reach the API.** The [Azure deployment](../infra/azure/README.md) gives
  the API no address outside its private network, so a hosted runner can't reach it. Use a
  self-hosted runner or agent inside that network (or peered with it).
- **The application under test is the one in Assay's settings.** A run calls the target
  set on the Settings page or with `ASSAY_TARGET_*`: point it at the environment the
  pipeline deploys to, such as staging. A target per run, e.g. a pull request's preview,
  isn't supported yet.
- **Runs are queued, not executed, by the API.** Without a worker they wait as `Pending`
  and the step times out: `assay health` tells you first.

## Development

From the repository root, with the backend's dev dependencies installed:

```bash
pip install -e cli/
pytest tests/cli          # unit tests, and end-to-end against the real API in-process
ruff check cli/ tests/cli
```
