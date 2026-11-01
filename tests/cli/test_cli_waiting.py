# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Francesco Campanile
"""Waiting, on a clock that only moves when the waiter sleeps: what is tried again,
how often, and that no wait outlasts its deadline."""
import pytest

from assay_cli.client import SERVER_ERROR_RETRIES, UNTIL_DEADLINE, ApiError
from assay_cli.waiting import LAST_CHECK_SECONDS, TimedOut, Waiter


class Clock:
    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


@pytest.fixture
def clock():
    return Clock()


def _waiter(clock, said=None):
    return Waiter(clock, clock.sleep, 5, (said if said is not None else []).append)


def _reading(*replies):
    """Each read gives the next reply: raised when it's an ApiError, else returned;
    the last one again once they run out. Records each read's time left."""
    replies, limits = list(replies), []

    def read(time_left):
        limits.append(time_left and time_left())
        reply = replies.pop(0) if len(replies) > 1 else replies[0]
        if isinstance(reply, ApiError):
            raise reply
        return reply

    read.limits = limits
    return read


def _gateway():
    return ApiError("GET …: 502 Bad Gateway", 502, retries=UNTIL_DEADLINE)


def _server_error():
    return ApiError("GET …: 500 Internal Server Error", 500, retries=SERVER_ERROR_RETRIES)


def _out_of_time(exc):
    return TimedOut(f"out of time: {exc}")


def test_a_refusal_ends_the_read_at_once(clock):
    refused = ApiError("GET …: 401 Expired", 401)

    with pytest.raises(ApiError) as raised:
        _waiter(clock).read(_reading(refused), None, _out_of_time)
    assert raised.value is refused and clock.sleeps == []


def test_a_500_gets_its_retries_whatever_gateway_failures_came_first(clock):
    read = _reading(*[_gateway() for _ in range(4)],
                    *[_server_error() for _ in range(SERVER_ERROR_RETRIES)], "done")

    assert _waiter(clock).read(read, None, _out_of_time) == "done"


def test_a_steady_500_ends_the_read_whatever_gateway_failures_come_between(clock):
    errors = []
    for _ in range(SERVER_ERROR_RETRIES + 1):
        errors += [_server_error(), _gateway()]

    with pytest.raises(ApiError) as raised:
        _waiter(clock).read(_reading(*errors), None, _out_of_time)  # never times out
    assert raised.value.status_code == 500
    assert len(clock.sleeps) == 2 * SERVER_ERROR_RETRIES  # the 4th 500 isn't waited on


def test_a_failure_that_passes_is_tried_again_until_the_deadline(clock):
    said = []

    with pytest.raises(TimedOut, match="out of time: GET …: 502"):
        _waiter(clock, said).read(_reading(_gateway()), 12, _out_of_time)
    assert clock.sleeps == [5, 5, 2] and clock.now == 12
    assert said.count("  GET …: 502 Bad Gateway; trying again") == 3


def test_each_try_ends_by_the_deadline_and_one_at_it_gets_a_last_check(clock):
    read = _reading(_gateway(), _gateway(), "done")

    assert _waiter(clock).read(read, 7, _out_of_time) == "done"
    # at 0s, 7s left; at 5s, 2s, raised to the last check's 5; at 7s, the last check
    assert read.limits == [7, LAST_CHECK_SECONDS, LAST_CHECK_SECONDS]


def test_without_a_deadline_a_read_has_no_time_limit(clock):
    read = _reading("done")

    _waiter(clock).read(read, None, _out_of_time)
    assert read.limits == [None]


def test_until_says_each_new_line_once_and_times_out_on_time(clock):
    said = []

    with pytest.raises(TimedOut, match="Still not finished after 7s: 1 of 3"):
        _waiter(clock, said).until(_reading("0 of 3", "1 of 3"), lambda state: False,
                                   str, timeout=7)
    assert said == ["  0 of 3", "  1 of 3"]
    assert clock.now == 7  # the last check comes at 7s, not at 10s


def test_until_returns_as_soon_as_it_has_finished(clock):
    read = _reading("waiting", "waiting", "finished")

    assert _waiter(clock).until(read, lambda state: state == "finished", str,
                                timeout=0) == "finished"
    assert clock.sleeps == [5, 5]


def test_a_check_that_keeps_failing_names_its_last_failure_when_time_is_up(clock):
    with pytest.raises(TimedOut, match="the last check failed \\(GET …: 502 Bad Gateway\\)"):
        _waiter(clock).until(_reading(_gateway()), lambda state: True, str, timeout=6)
