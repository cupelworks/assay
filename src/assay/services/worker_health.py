
from assay.code_fingerprint import CODE_FINGERPRINT, CODE_VERSION
from assay.messages import sentence
from assay.schemas import BrokerStatus, WorkerHealth, WorkerHealthStatus, WorkerStatus
from assay.services.runs._common import EXECUTE_RUN_TASK
from assay.services.settings.create_judge_check import CHECK_TASK as CHECK_JUDGE_TASK
from assay.services.settings.create_target_check import CHECK_TASK as CHECK_TARGET_TASK
from assay.timestamps import utc_now
from assay.worker import app as _celery_app
from assay.worker.celery_app import CODE_COMMAND

# Bounds on each step, so a page polling this never waits long: connecting
# to the broker, then waiting for the workers to answer.
BROKER_TIMEOUT_SECONDS = 1.0
PING_TIMEOUT_SECONDS = 1.0

RESTART = "restart it on the current code"


# workers send it to one another after each wave of a batch that runs until
# there's an answer; a worker without it would leave such a batch stalled
ADVANCE_BATCH_TASK = "assay.worker.tasks.advance_batch.advance_batch"


def expected_tasks() -> set[str]:
    """Every task the API, Beat or another worker sends a worker, by name."""
    return {
        EXECUTE_RUN_TASK,
        CHECK_TARGET_TASK,
        CHECK_JUDGE_TASK,
        ADVANCE_BATCH_TASK,
        _celery_app.conf.beat_schedule["reconcile-runs"]["task"],
    }


def get_worker_health() -> WorkerHealth:
    """Whether runs and checks sent now will be executed: is the broker
    reachable, which workers answer, and does each run the current code.

    Blocking (it talks to the broker), so the route is a plain `def` that
    FastAPI runs in its thread pool. About a second, at most about three:
    one to connect, one to wait for the workers' task lists, then their code
    fingerprints, which return as soon as every worker that answered has.
    Beat isn't a worker and doesn't answer, so it isn't covered.

    Two questions to every worker: its registered tasks (which doubles as
    the ping — a worker that answers is alive) and its code fingerprint. A
    worker whose fingerprint differs from the API's started before the
    latest code change; one that answers the first but not the second
    started on code from before fingerprints. Either is out of date, as is
    one missing a task.
    """
    checked_at = utc_now()
    try:
        with _celery_app.connection_for_write(transport_options={
            **_celery_app.conf.broker_transport_options,
            # Redis's own socket timeouts: a broker that drops packets would
            # otherwise hang the connect for minutes
            "socket_connect_timeout": BROKER_TIMEOUT_SECONDS,
            "socket_timeout": BROKER_TIMEOUT_SECONDS,
        }) as connection:
            connection.ensure_connection(max_retries=0)
            registered = _celery_app.control.inspect(
                timeout=PING_TIMEOUT_SECONDS, connection=connection,
            ).registered() or {}
            codes = _codes(connection, len(registered)) if registered else {}
    except Exception as exc:  # any failure to reach the broker is the answer itself
        return WorkerHealth(
            status=WorkerHealthStatus.broker_unreachable,
            broker=BrokerStatus(reachable=False, error=sentence(str(exc) or type(exc).__name__)),
            workers=[],
            version=CODE_VERSION,
            code_fingerprint=CODE_FINGERPRINT,
            checked_at=checked_at,
        )

    expected = expected_tasks()
    workers = [_worker(name, tasks, codes.get(name), expected)
               for name, tasks in sorted(registered.items())]
    if not workers:
        status = WorkerHealthStatus.no_worker
    elif any(worker.problem for worker in workers):
        status = WorkerHealthStatus.outdated
    else:
        status = WorkerHealthStatus.ready
    return WorkerHealth(status=status, broker=BrokerStatus(reachable=True), workers=workers,
                        version=CODE_VERSION, code_fingerprint=CODE_FINGERPRINT,
                        checked_at=checked_at)


def _codes(connection, expected_replies: int) -> dict[str, dict]:
    """Each answering worker's {"fingerprint", "version"}, by name. Waits only
    until every worker that answered the ping has replied, or the timeout."""
    replies = _celery_app.control.broadcast(
        CODE_COMMAND, reply=True, timeout=PING_TIMEOUT_SECONDS, connection=connection,
        limit=expected_replies,
    ) or []
    return {name: reply for answer in replies for name, reply in answer.items()
            if isinstance(reply, dict)}


def _worker(name: str, tasks: list[str] | None, code: dict | None,
            expected: set[str]) -> WorkerStatus:
    fingerprint = code.get("fingerprint") if code else None
    missing = sorted(expected - set(tasks or []))
    current = fingerprint == CODE_FINGERPRINT
    if fingerprint is None:
        problem = f"It runs code from before this check could see its version: {RESTART}."
    elif not current:
        problem = f"It runs older code than the API: {RESTART}."
    elif missing:
        problem = f"It doesn't know {len(missing)} kind(s) of job: {RESTART}."
    else:
        problem = None
    return WorkerStatus(
        name=name, version=code.get("version") if code else None,
        code_fingerprint=fingerprint, current_code=current, missing_tasks=missing,
        problem=problem,
    )
