# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Francesco Campanile
"""The client against a mock API: how each failure is classed, and how long
each request may wait."""
import math

import httpx
import pytest

from assay_cli.client import ApiError, AssayClient


def _client(reply):
    return AssayClient(httpx.Client(base_url="http://assay", transport=httpx.MockTransport(reply)))


@pytest.mark.parametrize(("status", "retries"), [
    (502, math.inf), (503, math.inf), (504, math.inf), (429, math.inf),
    (500, 3),  # a database busy for a moment passes; a bug doesn't
    (501, 0), (505, 0), (401, 0), (404, 0)])


def test_how_often_a_failed_read_is_worth_trying_again(status, retries):
    with pytest.raises(ApiError) as raised:
        _client(lambda request: httpx.Response(status)).health()
    assert raised.value.retries == retries


@pytest.mark.parametrize("status", [500, 502, 503, 504])


def test_a_failure_to_start_may_have_started(status):
    with pytest.raises(ApiError, match="it may have been carried out") as raised:
        _client(lambda request: httpx.Response(status)).start_batch({})
    assert raised.value.retries == 0


def test_an_unreachable_api_is_transient():
    def unreachable(request):
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(ApiError) as raised:
        _client(unreachable).health()
    assert raised.value.retries == math.inf


def test_a_redirect_is_named_never_followed():
    reply = httpx.Response(301, headers={"location": "https://assay/api/health/worker"})
    with pytest.raises(ApiError, match="301 redirected to https://assay/api/health/worker"):
        _client(lambda request: reply).health()


def test_an_empty_reply_is_an_error():
    with pytest.raises(ApiError, match="an empty reply"):
        _client(lambda request: httpx.Response(200)).health()


def _sent_but_unanswered(request):
    raise httpx.ReadTimeout("timed out", request=request)


def test_a_start_with_no_reply_may_have_started_and_is_never_retried():
    with pytest.raises(ApiError, match="it may have been carried out") as raised:
        _client(_sent_but_unanswered).start_batch({})
    assert raised.value.retries == 0


def test_a_read_with_no_reply_is_tried_again():
    with pytest.raises(ApiError, match="no reply from Assay") as raised:
        _client(_sent_but_unanswered).health()
    assert raised.value.retries == math.inf


def test_a_start_that_never_left_is_unreachable():
    def unreachable(request):
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(ApiError, match="couldn't reach Assay"):
        _client(unreachable).start_batch({})


def test_the_time_left_caps_every_step_of_a_request():
    seen = []

    def reply(request):
        seen.append(request.extensions["timeout"])
        return httpx.Response(200, json={"id": "b"})

    client = _client(reply)
    client.batch("b", time_left=lambda: 2)
    client.batch("b")

    assert seen[0] == {"connect": 2, "read": 2, "write": 2, "pool": 2}
    assert seen[1]["read"] == 5  # httpx's own default, untouched without


def test_each_page_asks_the_time_left_afresh():
    clock = {"now": 0}

    def page(request):
        clock["now"] += 3  # each page takes 3s
        return httpx.Response(200, json={"items": [{"id": "r"}] * 100, "total": 500})

    with pytest.raises(ApiError, match="out of time, not sent") as raised:
        _client(page).execution_runs("test-set", "s", "e", lambda: 7 - clock["now"])
    assert clock["now"] == 9  # pages at 0s, 3s and 6s; none at 9s
    assert raised.value.retries == math.inf


def test_a_finished_executions_runs_are_read_500_at_a_time():
    limits = []

    def page(request):
        limits.append(request.url.params["limit"])
        return httpx.Response(200, json={"items": [], "total": 0})

    _client(page).execution_runs("test-set", "s", "e")
    assert limits == ["500"]


def _named(item_id, name="Smoke"):
    return {"id": item_id, "name": name}


def test_a_name_found_twice_across_shifting_pages_is_one_item():
    def pages(request):
        # the list shifted between the two reads: page 2 starts with page 1's last item
        if request.url.params["offset"] == "0":
            return httpx.Response(200, json={"items": [_named("a")], "total": 2})
        return httpx.Response(200, json={"items": [_named("a"), _named("b", "Other")],
                                         "total": 3})

    assert _client(pages).resolve("test-set", "Smoke") == {"id": "a", "name": "Smoke"}


def test_a_name_lookup_stops_at_its_second_match():
    asked = []

    def pages(request):
        offset = int(request.url.params["offset"])
        asked.append(offset)
        return httpx.Response(200, json={"items": [_named(f"id{offset}")], "total": 1000})

    with pytest.raises(ApiError, match="At least 2 test sets are named 'Smoke' \\(id0, id1\\)"):
        _client(pages).resolve("test-set", "Smoke")
    assert asked == [0, 1]


def test_an_id_no_item_has_is_looked_up_as_a_name():
    ref = "3f0c2b1e-7d4a-4c1b-9e2f-0a1b2c3d4e5f"

    def reply(request):
        if request.url.path.endswith(ref):
            return httpx.Response(404, json={"detail": "Test not found"})
        return httpx.Response(200, json={"test_cases": [_named("t1", ref)], "total": 1})

    assert _client(reply).resolve("test", ref) == {"id": "t1", "name": ref}


def test_a_failure_other_than_404_looking_up_an_id_isnt_hidden():
    with pytest.raises(ApiError, match="503") as raised:
        _client(lambda request: httpx.Response(503)).resolve(
            "test", "3f0c2b1e-7d4a-4c1b-9e2f-0a1b2c3d4e5f")
    assert raised.value.retries == math.inf
