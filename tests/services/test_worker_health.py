from unittest.mock import MagicMock, patch

from kombu.exceptions import OperationalError

from assay.code_fingerprint import CODE_FINGERPRINT
from assay.schemas import WorkerHealthStatus
from assay.services import worker_health
from assay.services.worker_health import expected_tasks, get_worker_health

ALL_TASKS = [
    "assay.worker.tasks.check_judge.check_judge",
    "assay.worker.tasks.check_target.check_target",
    "assay.worker.tasks.execute_run.execute_run",
    "assay.worker.tasks.reconcile_runs.reconcile_runs",
]


def _app(replies=None, connect_error=None, transport_options=None, codes=None):
    """A stand-in Celery app: a broker connection that connects (or raises),
    workers that answer `registered` with `replies`, and the code report
    with `codes` — by default every worker that answered, on the API's code."""
    app = MagicMock()
    app.conf.broker_transport_options = transport_options or {}
    app.conf.beat_schedule = worker_health._celery_app.conf.beat_schedule
    connection = app.connection_for_write.return_value.__enter__.return_value
    if connect_error is not None:
        connection.ensure_connection.side_effect = connect_error
    app.control.inspect.return_value.registered.return_value = replies
    if codes is None:
        codes = {name: {"fingerprint": CODE_FINGERPRINT, "version": "1.0.0"}
                 for name in (replies or {})}
    app.control.broadcast.return_value = [{name: code} for name, code in codes.items()]
    return app


def _health(app):
    with patch.object(worker_health, "_celery_app", app):
        return get_worker_health()


def test_the_expected_tasks_are_every_task_the_api_and_beat_send():
    assert expected_tasks() == set(ALL_TASKS)


def test_ready_when_every_worker_that_answers_has_every_task():
    health = _health(_app({"celery@b": ALL_TASKS, "celery@a": ALL_TASKS + ["celery.chord"]}))

    assert health.status == WorkerHealthStatus.ready
    assert (health.broker.reachable, health.broker.error) == (True, None)
    assert [(w.name, w.missing_tasks) for w in health.workers] == [
        ("celery@a", []), ("celery@b", []),
    ]


def test_no_worker_when_nobody_answers():
    health = _health(_app(None))

    assert (health.status, health.workers, health.broker.reachable) == (
        WorkerHealthStatus.no_worker, [], True)


def test_outdated_when_any_worker_lacks_a_task_and_it_names_them():
    health = _health(_app({
        "celery@current": ALL_TASKS,
        "celery@old": ALL_TASKS[1:3],
    }))

    assert health.status == WorkerHealthStatus.outdated
    old = next(w for w in health.workers if w.name == "celery@old")
    assert old.missing_tasks == [
        "assay.worker.tasks.check_judge.check_judge",
        "assay.worker.tasks.reconcile_runs.reconcile_runs",
    ]


def test_broker_unreachable_says_why_and_asks_no_worker():
    app = _app(connect_error=OperationalError("error 111 connecting to redis:6379. "
                                              "Connection refused."))

    health = _health(app)

    assert health.status == WorkerHealthStatus.broker_unreachable
    assert health.broker.reachable is False
    assert health.broker.error == "Error 111 connecting to redis:6379. Connection refused."
    assert health.workers == []
    app.control.inspect.assert_not_called()


def test_a_failure_with_no_message_is_named_by_its_type():
    health = _health(_app(connect_error=TimeoutError()))

    assert health.broker.error == "TimeoutError"


def test_the_connect_and_the_ping_are_bounded_and_keep_the_brokers_own_options():
    app = _app({"celery@a": ALL_TASKS}, transport_options={"visibility_timeout": 3600})

    _health(app)

    options = app.connection_for_write.call_args.kwargs["transport_options"]
    assert options == {"visibility_timeout": 3600, "socket_connect_timeout": 1.0,
                       "socket_timeout": 1.0}
    connection = app.connection_for_write.return_value.__enter__.return_value
    connection.ensure_connection.assert_called_once_with(max_retries=0)
    app.control.inspect.assert_called_once_with(timeout=1.0, connection=connection)



# --- the code each worker runs ---


def test_a_worker_on_the_apis_code_is_current_with_its_version():
    health = _health(_app({"celery@a": ALL_TASKS}))

    (worker,) = health.workers
    assert (worker.current_code, worker.code_fingerprint, worker.version, worker.problem) == (
        True, CODE_FINGERPRINT, "1.0.0", None)
    assert (health.code_fingerprint, health.version) == (CODE_FINGERPRINT, "1.0.0")


def test_a_worker_on_older_code_is_outdated_even_with_every_task():
    health = _health(_app(
        {"celery@new": ALL_TASKS, "celery@old": ALL_TASKS},
        codes={"celery@new": {"fingerprint": CODE_FINGERPRINT, "version": "1.0.0"},
               "celery@old": {"fingerprint": "0123456789ab", "version": "1.0.0"}},
    ))

    assert health.status == WorkerHealthStatus.outdated
    old = next(w for w in health.workers if w.name == "celery@old")
    assert (old.current_code, old.missing_tasks) == (False, [])
    assert old.problem == "It runs older code than the API: restart it on the current code."


def test_a_worker_too_old_to_report_its_code_is_outdated():
    # a worker without the command answers a Celery error, or not at all
    health = _health(_app(
        {"celery@a": ALL_TASKS, "celery@b": ALL_TASKS},
        codes={"celery@a": {"error": "No such inspect command: 'assay_code'"}},
    ))

    assert health.status == WorkerHealthStatus.outdated
    for worker in health.workers:
        assert (worker.version, worker.code_fingerprint, worker.current_code) == (
            None, None, False)
        assert worker.problem.startswith("It runs code from before this check could see")


def test_a_worker_missing_a_task_says_how_many():
    health = _health(_app({"celery@a": ALL_TASKS[:2]}))

    assert health.workers[0].problem == (
        "It doesn't know 2 kind(s) of job: restart it on the current code.")


def test_the_code_report_waits_only_for_the_workers_that_answered():
    app = _app({"celery@a": ALL_TASKS, "celery@b": ALL_TASKS})

    _health(app)

    connection = app.connection_for_write.return_value.__enter__.return_value
    app.control.broadcast.assert_called_once_with(
        "assay_code", reply=True, timeout=1.0, connection=connection, limit=2)


def test_with_no_worker_the_code_isnt_asked_for():
    app = _app(None)

    _health(app)

    app.control.broadcast.assert_not_called()
