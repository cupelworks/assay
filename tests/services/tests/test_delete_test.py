import asyncio
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from assay.schemas import TestCaseID
from assay.services import delete_test_by_id
from assay.services.tests.delete_test import _assert_not_referenced

# --- _assert_not_referenced ---

def test_assert_not_referenced_passes_when_no_ids_found():
    session = AsyncMock()
    session.scalars.return_value = MagicMock(all=MagicMock(return_value=[]))

    with patch("assay.services.tests.delete_test.select"):
        asyncio.run(_assert_not_referenced(session, MagicMock(), [], ""))


def test_assert_is_referenced_raises_409_for_single_id():
    found_id = uuid.uuid4()

    session = AsyncMock()
    session.scalars.return_value = MagicMock(all=MagicMock(return_value=[found_id]))

    with patch("assay.services.tests.delete_test.select"), \
            pytest.raises(HTTPException) as e:
        asyncio.run(_assert_not_referenced(session, MagicMock(), [found_id], ""))

    assert e.value.status_code == 409
    assert str(e.value.detail).__contains__(str(found_id))


def _get_two_ids():
    first_found_id = uuid.uuid4()
    second_found_id = uuid.uuid4()
    return first_found_id, second_found_id


def test_assert_not_referenced_raises_409_for_multiple_ids():
    ids = list(_get_two_ids())

    session = AsyncMock()
    session.scalars.return_value = MagicMock(all=MagicMock(return_value=ids))

    with patch("assay.services.tests.delete_test.select"), \
        pytest.raises(HTTPException) as e:
        asyncio.run(_assert_not_referenced(session, MagicMock(), ids, ""))

    assert e.value.status_code == 409
    assert all(str(_id) in str(e.value.detail) for _id in ids)


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
    #   2nd call — _assert_not_referenced (test sets): returns ids[0] as referenced, triggering 409
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
