from datetime import datetime

from assay.messages import sentence
from assay.schemas import BrokerStatus, WorkerHealth, WorkerHealthStatus, WorkerStatus
from assay.services.runs._common import EXECUTE_RUN_TASK
from assay.services.settings.create_judge_check import CHECK_TASK as CHECK_JUDGE_TASK
from assay.services.settings.create_target_check import CHECK_TASK as CHECK_TARGET_TASK
from assay.worker import app as _celery_app

# Bounds on each step, so a page polling this never waits long: connecting
# to the broker, then waiting for the workers to answer the ping.
BROKER_TIMEOUT_SECONDS = 1.0
PING_TIMEOUT_SECONDS = 1.0


def expected_tasks() -> set[str]:
    """Every task the API or Beat sends a worker, by name."""
    return {
        EXECUTE_RUN_TASK,
        CHECK_TARGET_TASK,
        CHECK_JUDGE_TASK,
        _celery_app.conf.beat_schedule["reconcile-runs"]["task"],
    }


def get_worker_health() -> WorkerHealth:
    """Whether runs and checks sent now will be executed: is the broker
    reachable, which workers answer, and does each have every task.

    Blocking (it talks to the broker), so the route is a plain `def` that
    FastAPI runs in its thread pool. At most about two seconds: one to
    connect, one to wait for the workers' answers. Beat isn't a worker and
    doesn't answer a ping, so it isn't covered.

    Workers are asked for their registered tasks, which doubles as the ping:
    a worker that answers is alive, and its list shows whether it runs the
    current code.
    """
    checked_at = datetime.now().astimezone()
    try:
        with _celery_app.connection_for_write(transport_options={
            **_celery_app.conf.broker_transport_options,
            # Redis's own socket timeouts: a broker that drops packets would
            # otherwise hang the connect for minutes
            "socket_connect_timeout": BROKER_TIMEOUT_SECONDS,
            "socket_timeout": BROKER_TIMEOUT_SECONDS,
        }) as connection:
            connection.ensure_connection(max_retries=0)
            replies = _celery_app.control.inspect(
                timeout=PING_TIMEOUT_SECONDS, connection=connection,
            ).registered() or {}
    except Exception as exc:  # any failure to reach the broker is the answer itself
        return WorkerHealth(
            status=WorkerHealthStatus.broker_unreachable,
            broker=BrokerStatus(reachable=False, error=sentence(str(exc) or type(exc).__name__)),
            workers=[],
            checked_at=checked_at,
        )

    expected = expected_tasks()
    workers = [
        WorkerStatus(name=name, missing_tasks=sorted(expected - set(tasks or [])))
        for name, tasks in sorted(replies.items())
    ]
    if not workers:
        status = WorkerHealthStatus.no_worker
    elif any(worker.missing_tasks for worker in workers):
        status = WorkerHealthStatus.outdated
    else:
        status = WorkerHealthStatus.ready
    return WorkerHealth(status=status, broker=BrokerStatus(reachable=True), workers=workers,
                        checked_at=checked_at)
