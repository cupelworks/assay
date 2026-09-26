import logging
import uuid
from unittest.mock import patch

import pytest
from kombu.exceptions import EncodeError, OperationalError

from assay.services.runs._common import _dispatch_runs

# --- _dispatch_runs() ---


def test_dispatch_runs_sends_task_by_name_for_each_run_id():
    run_ids = [uuid.uuid4(), uuid.uuid4(), uuid.uuid4()]

    with patch("assay.services.runs._common._celery_app") as mock_celery_app:
        _dispatch_runs(run_ids)

    assert mock_celery_app.send_task.call_count == len(run_ids)
    for run_id, call in zip(run_ids, mock_celery_app.send_task.call_args_list, strict=True):
        assert call.args[0] == "assay.worker.tasks.execute_run.execute_run"
        assert call.kwargs["args"] == [run_id]
        # send_task ignores the app-wide task_ignore_result — must be explicit here
        assert call.kwargs["ignore_result"] is True


def test_dispatch_runs_empty_list_sends_nothing():
    with patch("assay.services.runs._common._celery_app") as mock_celery_app:
        _dispatch_runs([])

    mock_celery_app.send_task.assert_not_called()


def test_dispatch_runs_one_failure_does_not_stop_the_rest():
    run_ids = [uuid.uuid4(), uuid.uuid4(), uuid.uuid4()]

    with patch("assay.services.runs._common._celery_app") as mock_celery_app:
        mock_celery_app.send_task.side_effect = [
            None, OperationalError("broker unreachable"), None,
        ]

        _dispatch_runs(run_ids)  # must not raise

    assert mock_celery_app.send_task.call_count == len(run_ids)


def test_dispatch_runs_every_kombu_error_is_swallowed():
    run_ids = [uuid.uuid4(), uuid.uuid4()]

    with patch("assay.services.runs._common._celery_app") as mock_celery_app:
        mock_celery_app.send_task.side_effect = [
            OperationalError("broker unreachable"), EncodeError("bad args"),
        ]

        _dispatch_runs(run_ids)  # must not raise

    assert mock_celery_app.send_task.call_count == len(run_ids)


def test_dispatch_runs_non_kombu_error_propagates():
    with patch("assay.services.runs._common._celery_app") as mock_celery_app:
        mock_celery_app.send_task.side_effect = TypeError("a bug, not a publish failure")

        with pytest.raises(TypeError):
            _dispatch_runs([uuid.uuid4(), uuid.uuid4()])

    mock_celery_app.send_task.assert_called_once()


def test_dispatch_runs_logs_one_summary_line_per_batch(caplog):
    run_ids = [uuid.uuid4(), uuid.uuid4(), uuid.uuid4()]

    with (
        patch("assay.services.runs._common._celery_app") as mock_celery_app,
        caplog.at_level(logging.INFO, logger="assay.services.runs"),
    ):
        mock_celery_app.send_task.side_effect = [
            None, OperationalError("broker unreachable"), None,
        ]
        _dispatch_runs(run_ids)

    summaries = [r for r in caplog.records if r.getMessage().startswith("Dispatched")]
    assert len(summaries) == 1
    assert summaries[0].getMessage() == "Dispatched 2 of 3 runs to the worker"
    assert (summaries[0].dispatched, summaries[0].run_count) == (2, 3)
