# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Francesco Campanile
import asyncio
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from assay.schemas import TestCaseID
from assay.services import delete_test_by_id
from assay.services.tests.delete_test import _raise_if_referenced

# --- _raise_if_referenced ---

def test_nothing_referenced_raises_nothing():
    _raise_if_referenced([uuid.uuid4()], set(), "test sets")


def test_a_referenced_test_is_a_409_naming_it_and_the_reason():
    asked, referenced = uuid.uuid4(), uuid.uuid4()

    with pytest.raises(HTTPException) as e:
        _raise_if_referenced([asked, referenced], {referenced}, "test runs")

    assert e.value.status_code == 409
    assert str(referenced) in str(e.value.detail) and str(asked) not in str(e.value.detail)
    assert "test runs" in str(e.value.detail)


def test_several_referenced_tests_are_named_once_each_in_the_order_asked():
    first, second = uuid.uuid4(), uuid.uuid4()

    with pytest.raises(HTTPException) as e:
        _raise_if_referenced([second, first], {first, second}, "test sets")

    assert str(e.value.detail).index(str(second)) < str(e.value.detail).index(str(first))


def _get_two_ids():
    first_found_id = uuid.uuid4()
    second_found_id = uuid.uuid4()
    return first_found_id, second_found_id




# --- delete_test_by_id ---

def test_delete_raises_404_if_any_test_not_found():
    ids = list(_get_two_ids())
    test_case_ids = [TestCaseID(id=_id) for _id in ids]

    session = AsyncMock()
    session.scalars.return_value = MagicMock(all=MagicMock(return_value=[ids[0]]))

    with pytest.raises(HTTPException) as e:
        asyncio.run(delete_test_by_id(test_case_ids, session))

    assert e.value.status_code == 404
    assert str(ids[1]) in str(e.value.detail)
    session.execute.assert_not_called()
    session.commit.assert_not_called()


def test_delete_raises_409_if_linked_to_test_set():
    ids = list(_get_two_ids())
    test_case_ids = [TestCaseID(id=_id) for _id in ids]

    session = AsyncMock()
    # side_effect as a list makes the mock return a different value on each successive call.
    # session.scalars is called twice before the 409 is raised:
    #   1st call — _find_all_tests_or_404: returns both ids so the 404 check passes
    #   2nd call — the test sets holding a copy: returns ids[0] as referenced, triggering 409
    session.scalars.side_effect = [
        MagicMock(all=MagicMock(return_value=ids)),
        MagicMock(all=MagicMock(return_value=[ids[0]])),
    ]

    with pytest.raises(HTTPException) as e:
        asyncio.run(delete_test_by_id(test_case_ids, session))

    assert e.value.status_code == 409
    assert str(ids[0]) in str(e.value.detail)
    assert "test sets" in str(e.value.detail)
    session.execute.assert_not_called()
    session.commit.assert_not_called()


def test_delete_raises_409_if_linked_to_test_run():
    ids = list(_get_two_ids())
    test_case_ids = [TestCaseID(id=_id) for _id in ids]

    session = AsyncMock()
    session.scalars.side_effect = [
        MagicMock(all=MagicMock(return_value=ids)),
        MagicMock(all=MagicMock(return_value=[])),
        MagicMock(all=MagicMock(return_value=[ids[0]])),
    ]

    with pytest.raises(HTTPException) as e:
        asyncio.run(delete_test_by_id(test_case_ids, session))

    assert e.value.status_code == 409
    assert str(ids[0]) in str(e.value.detail)
    assert "test runs" in str(e.value.detail)
    session.execute.assert_not_called()
    session.commit.assert_not_called()


def test_delete_happy_path():
    ids = list(_get_two_ids())
    test_case_ids = [TestCaseID(id=_id) for _id in ids]

    session = AsyncMock()
    session.scalars.side_effect = [
        MagicMock(all=MagicMock(return_value=ids)),
        MagicMock(all=MagicMock(return_value=[])),
        MagicMock(all=MagicMock(return_value=[])),
    ]

    asyncio.run(delete_test_by_id(test_case_ids, session))

    session.execute.assert_called_once()
    session.commit.assert_called_once()
