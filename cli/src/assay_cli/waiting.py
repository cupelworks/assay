# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Francesco Campanile
"""Waiting on Assay: a read tried again while its failure may pass, and every wait
ended by its deadline. Time is only what the given clock says, and passes only
through the given sleep."""
from collections import Counter
from collections.abc import Callable
from typing import Any

from assay_cli.client import ApiError, TimeLeft

LAST_CHECK_SECONDS = 5
"""A check made at the deadline still gets this long to be answered."""

Read = Callable[[TimeLeft | None], Any]
"""A request that changes nothing, given how long it may still wait."""


class TimedOut(Exception):
    """It didn't finish by the deadline; nothing was concluded."""


class Waiter:
    def __init__(self, clock: Callable[[], float], sleep: Callable[[float], None],
                 interval: float, say: Callable[[str], None]):
        self.clock, self.sleep, self.interval, self.say = clock, sleep, interval, say

    def until(self, read: Read, finished: Callable[[Any], bool],
              describe: Callable[[Any], str], timeout: float) -> Any:
        """Read every interval until `finished`, saying each new progress line;
        `timeout` 0 waits forever. TimedOut when it isn't finished by then."""
        deadline = self.clock() + timeout if timeout > 0 else None
        still = f"Still not finished after {timeout:g}s"
        last = None
        while True:
            current = self.read(read, deadline,
                                lambda exc: TimedOut(f"{still}: the last check failed ({exc})"))
            line = describe(current)
            if line != last:
                self.say(f"  {line}")
                last = line
            if finished(current):
                return current
            if self.past(deadline):
                raise TimedOut(f"{still}: {line}")
            self.pause(deadline)

    def read(self, read: Read, deadline: float | None,
             out_of_time: Callable[[ApiError], Exception]) -> Any:
        """`read`, tried again after a failure that may pass, until the deadline:
        `out_of_time(the last failure)` then. Each kind of failure has its own count
        and ends it past its `retries` (a 500's 3, whatever gateway failures come
        between them); any other refusal ends it at once. Each try, every page of it,
        ends by the deadline, or LAST_CHECK_SECONDS after it for a try made then."""
        failures: Counter[float] = Counter()
        while True:
            ends = None if deadline is None else max(deadline,
                                                     self.clock() + LAST_CHECK_SECONDS)
            try:
                return read(self.time_left(ends))
            except ApiError as exc:
                failures[exc.retries] += 1
                if failures[exc.retries] > exc.retries:
                    raise
                if self.past(deadline):
                    raise out_of_time(exc) from exc
                self.say(f"  {exc}; trying again")
                self.pause(deadline)

    def time_left(self, ends: float | None) -> TimeLeft | None:
        return None if ends is None else lambda: ends - self.clock()

    def past(self, deadline: float | None) -> bool:
        return deadline is not None and self.clock() >= deadline

    def pause(self, deadline: float | None) -> None:
        """The interval, but never past the deadline: the last check comes on time."""
        wait = self.interval
        if deadline is not None:
            wait = min(wait, max(deadline - self.clock(), 0.0))
        self.sleep(wait)
