from fastapi import APIRouter

from assay.schemas import WorkerHealth
from assay.services import get_worker_health

router = APIRouter(tags=["meta"])

_CHECKED_AT = "2026-10-01T10:55:00+02:00"


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get(
    path="/health/worker",
    summary="Check that runs and checks will be executed",
    responses={
        200: {
            "description": (
                "Always 200: the API itself is up, and this reports on the parts outside "
                "it — the broker and the workers — in `status`. A 503 would read as the "
                "API failing."
            ),
            "content": {"application/json": {"examples": {
                "ready": {"summary": "A worker is running the current code", "value": {
                    "status": "ready",
                    "broker": {"reachable": True, "error": None},
                    "workers": [{"name": "celery@worker-1", "missing_tasks": []}],
                    "checked_at": _CHECKED_AT,
                }},
                "no_worker": {"summary": "No worker answered", "value": {
                    "status": "no_worker",
                    "broker": {"reachable": True, "error": None},
                    "workers": [],
                    "checked_at": _CHECKED_AT,
                }},
                "outdated": {"summary": "A worker runs older code", "value": {
                    "status": "outdated",
                    "broker": {"reachable": True, "error": None},
                    "workers": [
                        {"name": "celery@worker-1", "missing_tasks": []},
                        {"name": "celery@worker-2",
                         "missing_tasks": ["assay.worker.tasks.check_judge.check_judge"]},
                    ],
                    "checked_at": _CHECKED_AT,
                }},
                "broker_unreachable": {"summary": "The broker can't be reached", "value": {
                    "status": "broker_unreachable",
                    "broker": {"reachable": False,
                               "error": "Error 111 connecting to redis:6379. Connection "
                                        "refused."},
                    "workers": [],
                    "checked_at": _CHECKED_AT,
                }},
            }}},
        },
    },
)
def read_worker_health() -> WorkerHealth:  # pragma: no cover
    """Whether runs and checks sent now will be executed: the message broker
    is reachable, at least one worker answers, and every worker that answers
    has every task the API sends — a worker running older code is flagged
    with the tasks it lacks.

    Takes up to about two seconds (one to reach the broker, one to wait for
    the workers' answers), so poll it, don't call it per request. Beat, which
    schedules the reconciliation scan, doesn't answer a ping and isn't
    covered. `/health` stays the API's own liveness check.
    """
    return get_worker_health()
