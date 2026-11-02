# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Francesco Campanile
"""The client against a mock API: how each failure is classed, and how long
each request may wait."""
import math
import threading
import time

import httpx
import pytest

from assay_cli.client import GIVEN_UP_READS, ApiError, AssayClient


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


def _held(release):
    def reply(request):
        release.wait(5)
        return httpx.Response(200, json={"id": "b"})
    return reply


@pytest.mark.parametrize(("send", "says", "retries"), [
    (lambda client, left: client.batch("b", time_left=left), "no reply from Assay", math.inf),
    (lambda client, left: client.stop_batch("b", left), "it may have been carried out", 0),
])
def test_the_time_left_bounds_a_request_in_all(send, says, retries):
    release = threading.Event()
    started = time.monotonic()
    try:
        with pytest.raises(ApiError) as raised:
            send(_client(_held(release)), lambda: 0.2)
    finally:
        release.set()  # the reply given up on ends its thread

    assert time.monotonic() - started < 2
    assert "gave up after 0.2s" in str(raised.value) and says in str(raised.value)
    assert raised.value.retries == retries


def test_http_timeout_bounds_a_request_in_all_without_a_time_left():
    release = threading.Event()
    client = AssayClient(httpx.Client(base_url="http://assay", timeout=0.2,
                                      transport=httpx.MockTransport(_held(release))))
    started = time.monotonic()
    try:
        with pytest.raises(ApiError, match="gave up after 0.2s.*it may have been carried out"):
            client.start_batch({})  # a start has no time left: --http-timeout bounds it
    finally:
        release.set()
    assert time.monotonic() - started < 2


def test_reads_given_up_on_and_still_answered_hold_back_other_reads_not_writes():
    release = threading.Event()
    client = _client(_held(release))
    try:
        for _ in range(GIVEN_UP_READS):
            with pytest.raises(ApiError, match="gave up after"):
                client.batch("b", time_left=lambda: 0.05)
        with pytest.raises(ApiError, match="not sent: 3 requests given up on are still "
                                           "being answered") as raised:
            client.batch("b", time_left=lambda: 0.05)
        assert raised.value.retries == math.inf  # the wait goes on: one may end
        with pytest.raises(ApiError, match="gave up after"):
            client.stop_batch("b", lambda: 0.05)  # sent: a stop never waits on reads
    finally:
        release.set()
    deadline = time.monotonic() + 2
    while client._given_up and any(t.is_alive() for t in client._given_up):
        assert time.monotonic() < deadline
        time.sleep(0.01)
    assert client.batch("b", time_left=lambda: 1) == {"id": "b"}  # they ended: sent again


@pytest.mark.parametrize("error", [
    httpx.UnsupportedProtocol("Request URL is missing an 'http://' or 'https://' protocol."),
    httpx.LocalProtocolError("Illegal header value b' x'"),
])
def test_a_request_no_server_takes_is_never_tried_again(error):
    def refused(request):
        raise error

    with pytest.raises(ApiError) as raised:
        _client(refused).health()
    assert str(raised.value) == f"GET /health/worker: {error}"
    assert raised.value.retries == 0


def test_a_url_without_a_scheme_is_named_at_once():
    client = AssayClient(httpx.Client(base_url="assay.internal/api"))
    with pytest.raises(ApiError, match="missing an 'http://'") as raised:
        client.health()
    assert raised.value.retries == 0


@pytest.mark.parametrize("given", [
    "3F2504E0-4F89-11D3-9A0C-0305E82C3301", "{3f2504e0-4f89-11d3-9a0c-0305e82c3301}",
    "urn:uuid:3f2504e0-4f89-11d3-9a0c-0305e82c3301"])
def test_an_id_in_any_form_is_sent_as_assay_spells_it(given):
    paths = []

    def reply(request):
        paths.append(request.url.path)
        return httpx.Response(200, json={"id": "3f2504e0-4f89-11d3-9a0c-0305e82c3301",
                                         "name": "Smoke"})

    found = _client(reply).resolve("test-set", given)

    assert paths == ["/test-sets/3f2504e0-4f89-11d3-9a0c-0305e82c3301"]
    assert found == {"id": "3f2504e0-4f89-11d3-9a0c-0305e82c3301", "name": "Smoke"}


@pytest.mark.parametrize("page", [
    {"runs": [], "total": 3},  # its items under another key
    {"items": [{"id": "r"}]},  # no total: a long list would end after one page
    {"items": None, "total": 0},
    [{"id": "r"}],
])
def test_a_reply_that_isnt_a_page_is_an_error_never_the_end_of_the_list(page):
    with pytest.raises(ApiError, match="the reply isn't a page of a list") as raised:
        _client(lambda request: httpx.Response(200, json=page)).execution_runs(
            "test-set", "s", "e")
    assert raised.value.retries == 0


def test_check_labels_are_read_from_a_test_and_from_a_sets_entries():
    def reply(request):
        if request.url.path == "/tests/t1":
            return httpx.Response(200, json={"id": "t1", "test_type_assignments": [
                {"name": "Contains", "label": "Mentions it"}, {"name": "Toxicity"}]})
        return httpx.Response(200, json={"total": 2, "items": [
            {"id": "e1", "test_type_assignments": [{"name": "Bias", "label": "Bias"}]},
            {"id": "e2", "test_type_assignments": []}]})

    client = _client(reply)
    assert client.test_labels("t1") == ["Mentions it", "Toxicity"]
    assert client.entry_labels("s1") == {"e1": ["Bias"], "e2": []}


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
