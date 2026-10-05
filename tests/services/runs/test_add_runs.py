# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Francesco Campanile
import asyncio
import uuid
from unittest.mock import AsyncMock, MagicMock

from assay.models import StandaloneRunModel, TestRunModel, TestSetExecutionModel
from assay.services.runs._common import _add_runs

CONTAINS = {"name": "Contains", "label": "Contains"}
ROUGE = {"name": "ROUGE", "label": "ROUGE"}


def _session(entry_assignments: list[tuple]) -> AsyncMock:
    session = AsyncMock()
    session.add_all = MagicMock()
    result = MagicMock()
    result.tuples.return_value.all.return_value = entry_assignments
    session.execute.return_value = result
    return session


def test_entry_runs_get_their_entrys_check_types_and_are_added_with_their_execution():
    first, second = uuid.uuid4(), uuid.uuid4()
    execution = TestSetExecutionModel(id=uuid.uuid4())
    runs = [TestRunModel(id=uuid.uuid4(), test_set_entry_id=first),
            TestRunModel(id=uuid.uuid4(), test_set_entry_id=second)]
    session = _session([(first, [CONTAINS]), (second, [CONTAINS, ROUGE])])

    asyncio.run(_add_runs(session, runs, [execution]))

    session.execute.assert_awaited_once()  # one query for every entry
    assert [[c.test_type_name for c in run.check_types] for run in runs] == [
        ["Contains"], ["Contains", "ROUGE"]]
    session.add_all.assert_called_once_with([execution, *runs])


def test_standalone_runs_need_no_query():
    run = TestRunModel(id=uuid.uuid4(), test_id=uuid.uuid4())
    run.standalone_run = StandaloneRunModel(id=run.id, name="t", input="q",
                                            test_type_assignments=[ROUGE])
    session = _session([])

    asyncio.run(_add_runs(session, [run]))

    session.execute.assert_not_awaited()
    assert [c.test_type_name for c in run.check_types] == ["ROUGE"]
    session.add_all.assert_called_once_with([run])
