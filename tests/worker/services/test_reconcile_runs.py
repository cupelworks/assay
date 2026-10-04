import logging
import uuid
from datetime import datetime, timedelta
from unittest.mock import MagicMock

import pytest
from kombu.exceptions import EncodeError, OperationalError

from assay.worker.services.reconcile_runs import reconcile_pending_runs


def _session_returning(run_ids: list[uuid.UUID]) -> MagicMock:
    session = MagicMock()
    session.scalars.return_value.all.return_value = run_ids
    return session


def test_no_stale_runs_publishes_nothing():
    session = _session_returning([])
    publish = MagicMock()

    reconcile_pending_runs(session, timedelta(minutes=15), publish)

    publish.assert_not_called()


def test_every_stale_run_is_republished():
    run_ids = [uuid.uuid4(), uuid.uuid4()]
    session = _session_returning(run_ids)
    publish = MagicMock()

    reconcile_pending_runs(session, timedelta(minutes=15), publish)

    assert [call.args[0] for call in publish.call_args_list] == run_ids


def test_one_publish_failure_does_not_stop_the_rest():
    run_ids = [uuid.uuid4(), uuid.uuid4(), uuid.uuid4()]
    session = _session_returning(run_ids)
    publish = MagicMock(side_effect=[None, OperationalError("broker unreachable"), None])

    reconcile_pending_runs(session, timedelta(minutes=15), publish)  # must not raise

    assert [call.args[0] for call in publish.call_args_list] == run_ids


def test_every_kombu_error_is_swallowed():
    run_ids = [uuid.uuid4(), uuid.uuid4()]
    session = _session_returning(run_ids)
    publish = MagicMock(side_effect=[OperationalError("broker down"), EncodeError("bad args")])

    reconcile_pending_runs(session, timedelta(minutes=15), publish)  # must not raise

    assert publish.call_count == 2


def test_non_kombu_error_propagates():
    session = _session_returning([uuid.uuid4(), uuid.uuid4()])
    publish = MagicMock(side_effect=TypeError("a bug, not a publish failure"))

    with pytest.raises(TypeError):
        reconcile_pending_runs(session, timedelta(minutes=15), publish)

    publish.assert_called_once()


def test_no_stale_runs_is_logged_at_debug_only(caplog):
    session = _session_returning([])

    with caplog.at_level(logging.DEBUG, logger="assay.worker"):
        reconcile_pending_runs(session, timedelta(minutes=15), MagicMock())

    records = [r for r in caplog.records if r.name == "assay.worker.services.reconcile_runs"]
    assert [(r.levelno, r.getMessage()) for r in records] == [
        (logging.DEBUG, "Reconciliation scan: no Pending runs older than 15 min"),
    ]


def test_republish_summary_counts_the_failures(caplog):
    run_ids = [uuid.uuid4(), uuid.uuid4(), uuid.uuid4()]
    session = _session_returning(run_ids)
    publish = MagicMock(side_effect=[None, OperationalError("broker unreachable"), None])

    with caplog.at_level(logging.INFO, logger="assay.worker"):
        reconcile_pending_runs(session, timedelta(minutes=15), publish)

    summary = next(r for r in caplog.records if r.getMessage().startswith("Reconciliation scan"))
    assert summary.getMessage() == (
        "Reconciliation scan: re-published 2 of 3 Pending runs older than 15 min"
    )
    assert (summary.republished, summary.stale_count, summary.threshold_minutes) == (2, 3, 15)


def test_query_filters_pending_runs_older_than_threshold():
    session = _session_returning([])
    threshold = timedelta(minutes=15)
    before = datetime.now().astimezone() - threshold

    reconcile_pending_runs(session, threshold, MagicMock())

    after = datetime.now().astimezone() - threshold
    compiled = session.scalars.call_args.args[0].compile()
    sql = str(compiled)
    assert "test_runs.status = :status_1" in sql
    assert "test_runs.created_at < :created_at_1" in sql
    assert compiled.params["status_1"] == "Pending"
    assert before <= compiled.params["created_at_1"] <= after
