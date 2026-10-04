from fastapi import APIRouter

from assay.schemas import WorkerHealth
from assay.services import get_worker_health

router = APIRouter(tags=["meta"])

# Sample values for the Swagger examples of GET /health/worker below — none of
# this runs when the endpoint is called; the real report is built by
# services/worker_health.py from code_fingerprint.py's hash of the running code.
_EXAMPLE_CHECKED_AT = "2026-10-01T10:55:00+02:00"
_EXAMPLE_FINGERPRINT = "e64e0c986336"
_EXAMPLE_BROKER_OK = {"reachable": True, "error": None}


def _example_worker(name: str, fingerprint: str | None = _EXAMPLE_FINGERPRINT,
                    version: str | None = "1.2.0", missing: list[str] | None = None,
                    problem: str | None = None) -> dict:
    return {"name": name, "version": version, "code_fingerprint": fingerprint,
            "current_code": fingerprint == _EXAMPLE_FINGERPRINT, "missing_tasks": missing or [],
            "problem": problem}


def _example_health(status: str, workers: list[dict],
                    broker: dict = _EXAMPLE_BROKER_OK) -> dict:
    return {"status": status, "broker": broker, "workers": workers, "version": "1.2.0",
            "code_fingerprint": _EXAMPLE_FINGERPRINT, "checked_at": _EXAMPLE_CHECKED_AT}


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get(
    path="/health/worker",
    summary="Can runs and checks be executed right now?",
    responses={
        200: {
            "description": (
                "Always 200 — the API itself is up; this reports on what runs outside it. "
                "Read `status` first; when it isn't `ready`, each worker's `problem` says "
                "what's wrong in a sentence you can show as it is."
            ),
            "content": {"application/json": {"examples": {
                "ready": {
                    "summary": "ready — every worker runs the current code",
                    "value": _example_health("ready", [_example_worker("celery@worker1"),
                                                       _example_worker("celery@worker2")]),
                },
                "outdated_older_code": {
                    "summary": "outdated — a worker started before the latest change",
                    "value": _example_health("outdated", [
                        _example_worker("celery@worker1"),
                        _example_worker("celery@worker2", fingerprint="7f3a19c02bd4",
                                problem="It runs older code than the API: restart it on "
                                        "the current code."),
                    ]),
                },
                "outdated_no_report": {
                    "summary": "outdated — a worker too old to report its code",
                    "value": _example_health("outdated", [
                        _example_worker("celery@worker1", fingerprint=None, version=None,
                                problem="It runs code from before this check could see "
                                        "its version: restart it on the current code."),
                    ]),
                },
                "outdated_missing_task": {
                    "summary": "outdated — a worker that doesn't know a kind of job",
                    "value": _example_health("outdated", [
                        _example_worker("celery@worker1", fingerprint="7f3a19c02bd4",
                                missing=["assay.worker.tasks.check_judge.check_judge"],
                                problem="It runs older code than the API: restart it on "
                                        "the current code."),
                    ]),
                },
                "no_worker": {
                    "summary": "no_worker — nothing is running the work",
                    "value": _example_health("no_worker", []),
                },
                "broker_unreachable": {
                    "summary": "broker_unreachable — the queue can't be reached",
                    "value": _example_health("broker_unreachable", [], broker={
                        "reachable": False,
                        "error": "Error 111 connecting to redis:6379. Connection refused.",
                    }),
                },
            }}},
        },
    },
)
def read_worker_health() -> WorkerHealth:  # pragma: no cover
    """Whether a run or a check sent now will actually be executed — the
    question to ask before trusting that work will happen, e.g. for a status
    indicator on the Settings page.

    **How work gets done.** The API doesn't execute runs and checks itself:
    it puts each one on a queue (the *broker*, Redis), and separate *worker*
    processes take them off the queue and do them. So three things must hold:
    the broker is reachable, at least one worker is running, and every worker
    runs the **current code** — a worker keeps the code it started with until
    it's restarted, while the API reloads on every change.

    **`status`, and what to do:**

    - `ready` — the broker is reachable and every worker that answered runs
      the current code. Nothing to do.
    - `outdated` — at least one worker runs older code than the API; work it
      picks up can fail, or wait forever. Restart the workers in `workers`
      whose `problem` isn't null — it says why, in a sentence to show as is.
    - `no_worker` — the broker is reachable, but no worker answered: runs and
      checks wait in the queue. Start a worker; the waiting work is then done.
    - `broker_unreachable` — the queue can't be reached: runs stay Pending
      (the reconciliation scan re-sends them later) and checks complete at
      once as not sent. Start or fix Redis; `broker.error` says what failed.

    **How "current code" is decided.** Every process takes a *fingerprint* of
    its code when it starts — a short hash of Assay's source, leaving out what
    only the API runs. A worker whose `code_fingerprint` differs from the
    response's started before the latest code change. A worker with no
    fingerprint at all started on code from before this check existed. Either
    is `outdated`, and so is one missing a kind of job (`missing_tasks`) — a
    job of a kind it doesn't know is dropped. The fingerprints are opaque:
    compare, don't parse; `version` is the readable one, but it only changes
    with a release, so two workers on the same version can still differ.

    **Timing and limits.** Usually about a second, at most about three (one
    to reach the broker, one waiting for the workers' answers, the code
    report returning as soon as every worker has answered) — poll it every
    few seconds at most, don't call it per request. Beat, which schedules the
    reconciliation scan, isn't a worker and isn't covered. `GET /health` stays
    the API's own liveness check.
    """
    return get_worker_health()
