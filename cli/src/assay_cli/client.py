# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Francesco Campanile
"""A thin, synchronous client for the parts of Assay's API a pipeline needs:
start a run or a statistics batch, read how it stands, list its runs."""
import math
import threading
import uuid
from collections.abc import Callable, Iterator
from concurrent.futures import Future, wait
from typing import Any

import httpx

SCOPES = ("test", "test-set", "test-plan")
"""What can be run, as the command line spells it."""

_LIST_PATHS = {"test": "/tests", "test-set": "/test-sets", "test-plan": "/test-plans"}
_LIST_KEYS = {"test": "test_cases"}
"""Where a list's items are, when not under `items`."""
SCOPE_NOUNS = {"test": "test", "test-set": "test set", "test-plan": "test plan"}
"""Each scope as a sentence spells it."""
_NOT_SENT = (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout)
"""Failures before the request left: Assay never saw it."""
_NEVER_PASSES = (httpx.UnsupportedProtocol, httpx.LocalProtocolError)
"""Failures of the request itself, before it left: a URL or a header no server
takes, so asking again gets the same."""


UNTIL_DEADLINE = math.inf
"""Retries of a failure that passes: the network, a gateway, a busy API."""
_GATEWAY_STATUSES = frozenset({429, 502, 503, 504})
"""Replies a gateway or a busy API gives in passing: worth asking again until the
deadline."""
SERVER_ERROR_RETRIES = 3
"""A 500 is the API's own failure: a database busy for a moment passes, a bug doesn't."""
_UNSURE_STATUSES = frozenset({500, 502, 503, 504})
"""Replies to a write that leave open whether it was carried out: a gateway that gave
up waiting, or the API failing after it committed."""
_MAYBE_DONE = "; it may have been carried out, so look in Assay before trying again"
TimeLeft = Callable[[], float]
"""Seconds a request may still wait, asked just before it's sent."""
GIVEN_UP_READS = 3
"""Reads given up on, yet still being answered, past which no other read is sent
until one ends: each holds a connection, and a reply trickling in, a little at a
time, keeps it held. A write is sent whatever: stopping a batch mustn't wait."""


def _retries(status: int) -> float:
    if status in _GATEWAY_STATUSES:
        return UNTIL_DEADLINE
    return SERVER_ERROR_RETRIES if status == 500 else 0


class ApiError(Exception):
    """The API refused a request, or couldn't be reached. `retries`: how many times
    in a row the same read is worth trying again, until the deadline."""

    def __init__(self, message: str, status_code: int | None = None, retries: float = 0):
        super().__init__(message)
        self.status_code = status_code
        self.retries = retries


class AssayClient:
    def __init__(self, http: httpx.Client):
        self.http = http
        # httpx's timeout bounds each step of a request; the longest of them is
        # this client's bound on a request in all
        self.request_seconds = max(
            (limit for limit in http.timeout.as_dict().values() if limit is not None),
            default=None)
        self._given_up: list[threading.Thread] = []

    @classmethod
    def connect(cls, base_url: str, headers: dict[str, str] | None = None,
                timeout: float = 30.0) -> "AssayClient":
        return cls(httpx.Client(base_url=base_url.rstrip("/"), headers=headers or {},
                                timeout=timeout))

    def close(self) -> None:
        self.http.close()

    # -- plumbing --

    def _request(self, method: str, path: str, time_left: TimeLeft | None = None,
                 **kwargs) -> Any:
        """A request, taking --http-timeout at most in all, and no longer than
        `time_left` says when given; past it, it's given up on, unanswered."""
        seconds = self.request_seconds
        if time_left is not None:
            left = time_left()
            if left <= 0:
                raise ApiError(f"{method} {path}: out of time, not sent", retries=UNTIL_DEADLINE)
            # each step capped too, so a request given up on ends soon after
            kwargs["timeout"] = httpx.Timeout(**{
                step: left if limit is None else min(limit, left)
                for step, limit in self.http.timeout.as_dict().items()})
            seconds = left if seconds is None else min(seconds, left)
        try:
            response = self._send(method, path, seconds, **kwargs)
        except _NOT_SENT as exc:
            raise ApiError(f"{method} {path}: couldn't reach Assay ({exc})",
                           retries=UNTIL_DEADLINE) from exc
        except _NEVER_PASSES as exc:
            raise ApiError(f"{method} {path}: {exc}") from exc
        except httpx.HTTPError as exc:
            if method == "GET":
                raise ApiError(f"{method} {path}: no reply from Assay ({exc})",
                               retries=UNTIL_DEADLINE) from exc
            # sent, so Assay may have acted on it: never assume it didn't
            raise ApiError(f"{method} {path}: no reply from Assay ({exc}){_MAYBE_DONE}") from exc
        if response.is_redirect:
            # never followed: a redirected POST could start nothing, or start it twice
            where = response.headers.get("location", "elsewhere")
            raise ApiError(f"{method} {path}: {response.status_code} redirected to {where}; "
                           "give the API's final address with --url", response.status_code)
        status = response.status_code
        if status >= 400:
            message = f"{method} {path}: {status} {_detail(response)}"
            if method != "GET" and status in _UNSURE_STATUSES:
                raise ApiError(message + _MAYBE_DONE, status)
            raise ApiError(message, status, retries=_retries(status))
        if not response.content:
            raise ApiError(f"{method} {path}: an empty reply — is the URL Assay's API?",
                           response.status_code)
        try:
            return response.json()
        except ValueError as exc:
            raise ApiError(f"{method} {path}: the reply isn't JSON — is the URL Assay's API?",
                           response.status_code) from exc

    def _send(self, method: str, path: str, seconds: float | None,
              **kwargs) -> httpx.Response:
        """The request, waited for no longer than `seconds` in all (None: as long as
        httpx's timeouts allow). Those bound each step, and a reply read in chunks
        waits that long for each: so it's sent from a thread of its own, and one
        given up on is left to end by them."""
        if seconds is None:
            return self.http.request(method, path, **kwargs)
        self._given_up = [thread for thread in self._given_up if thread.is_alive()]
        if method == "GET" and len(self._given_up) >= GIVEN_UP_READS:
            raise ApiError(f"{method} {path}: not sent: {len(self._given_up)} requests given "
                           "up on are still being answered, so Assay, or the network to "
                           "it, is answering very slowly", retries=UNTIL_DEADLINE)
        # an httpx.Client is safe to share between threads: a request given up on
        # may still be in flight while the next one is sent
        reply: Future = Future()

        def send() -> None:
            try:
                reply.set_result(self.http.request(method, path, **kwargs))
            except BaseException as exc:  # handed to the caller, whatever it is
                reply.set_exception(exc)

        thread = threading.Thread(target=send, daemon=True)
        thread.start()
        if not wait([reply], timeout=seconds).done:
            self._given_up.append(thread)
            raise httpx.TimeoutException(f"gave up after {seconds:.1f}s")
        return reply.result()

    def _pages(self, path: str, params: dict | None = None, limit: int = 100,
               key: str = "items", time_left: TimeLeft | None = None) -> Iterator[dict]:
        """Every page, each asking `time_left` afresh. A reply without the items or
        their `total` isn't a page: an error, never taken as the end of the list."""
        offset = 0
        while True:
            page = self._request("GET", path, time_left=time_left,
                                 params={**(params or {}), "offset": offset, "limit": limit})
            if not (isinstance(page, dict) and isinstance(page.get(key), list)
                    and isinstance(page.get("total"), int)):
                raise ApiError(f"GET {path}: the reply isn't a page of a list, with {key!r} "
                               "and 'total' — is the URL Assay's API?")
            items = page[key]
            yield from items
            offset += len(items)
            if not items or offset >= page["total"]:
                return

    # -- reading --

    def health(self, time_left: TimeLeft | None = None) -> dict:
        return self._request("GET", "/health/worker", time_left=time_left)

    def resolve(self, scope: str, ref: str, time_left: TimeLeft | None = None) -> dict:
        """A test, test set or plan by id, or by its exact name: `{id, name}`. A ref
        shaped like an id that no item has is looked for as a name: a test created
        without a name is named with a random UUID."""
        noun = SCOPE_NOUNS[scope]
        item_id = canonical_id(ref)
        if item_id:
            try:
                item = self._request("GET", f"{_LIST_PATHS[scope]}/{item_id}",
                                     time_left=time_left)
            except ApiError as exc:
                if exc.status_code != 404:
                    raise
            else:
                return {"id": str(item.get("id", item_id)), "name": item.get("name", ref)}
        found: dict[str, dict] = {}  # by id: a list shifting between pages repeats items
        for item in self._pages(_LIST_PATHS[scope], {"q": ref}, limit=500,
                                key=_LIST_KEYS.get(scope, "items"), time_left=time_left):
            if item.get("name") == ref:
                found[str(item["id"])] = item
                if len(found) > 1:  # enough to know it's ambiguous
                    raise ApiError(f"At least 2 {noun}s are named {ref!r} "
                                   f"({', '.join(found)}); give the id.")
        if not found:
            has = "has the id or the name" if item_id else "is named"
            raise ApiError(f"No {noun} {has} {ref!r}. Give its exact name or its id.")
        (item_id, item), = found.items()
        return {"id": item_id, "name": item["name"]}

    def plan_test_sets(self, plan_id: str, time_left: TimeLeft | None = None) -> list[dict]:
        """The test sets a plan links now: `{id, name}`."""
        return [{"id": str(entry["test_set"]["id"]), "name": entry["test_set"]["name"]}
                for entry in self._pages(f"/test-plans/{plan_id}/entries", limit=500,
                                         time_left=time_left)]

    def test_labels(self, test_id: str, time_left: TimeLeft | None = None) -> list[str]:
        """The labels of a test's checks."""
        test = self._request("GET", f"/tests/{test_id}", time_left=time_left)
        return _labels(test)

    def entry_labels(self, test_set_id: str,
                     time_left: TimeLeft | None = None) -> dict[str, list[str]]:
        """The labels of the checks of each entry of a test set, by the entry's id."""
        return {str(entry["id"]): _labels(entry) for entry in self._pages(
            f"/test-sets/{test_set_id}/entries", limit=500, time_left=time_left)}

    # -- runs --

    def start_run(self, scope: str, scope_id: str, replay: str | None = None) -> dict:
        """`replay`: a past execution of the set or plan."""
        if scope == "test":
            return self._request("POST", f"/runs/standalone/{scope_id}")
        path = f"/runs/{scope}s/{scope_id}"
        if replay:
            path += f"/executions/{replay}"
        return self._request("POST", path)

    def standalone_run(self, test_id: str, run_id: str,
                       time_left: TimeLeft | None = None) -> dict:
        return self._request("GET", f"/runs/standalone/{test_id}/test-runs/{run_id}",
                             time_left=time_left)

    def execution(self, scope: str, scope_id: str, execution_id: str,
                  time_left: TimeLeft | None = None) -> dict:
        return self._request("GET", f"/runs/{scope}s/{scope_id}/executions/{execution_id}",
                             time_left=time_left)

    def execution_runs(self, scope: str, scope_id: str, execution_id: str,
                       time_left: TimeLeft | None = None) -> list[dict]:
        path = f"/runs/{scope}s/{scope_id}/executions/{execution_id}/test-runs"
        return list(self._pages(path, {"sort": "worst_first"}, limit=500,
                                time_left=time_left))

    # -- statistics batches --

    def start_batch(self, body: dict) -> dict:
        return self._request("POST", "/statistics/batches", json=body)

    def batch(self, batch_id: str, series: bool = False,
              time_left: TimeLeft | None = None) -> dict:
        return self._request("GET", f"/statistics/batches/{batch_id}", time_left=time_left,
                             params={"series": str(series).lower()})

    def stop_batch(self, batch_id: str, time_left: TimeLeft | None = None) -> dict:
        return self._request("POST", f"/statistics/batches/{batch_id}/stop", time_left=time_left)


def _labels(test: dict) -> list[str]:
    return [assignment.get("label") or assignment["name"]
            for assignment in test.get("test_type_assignments") or []]


def canonical_id(value: str) -> str | None:
    """`value` as Assay spells an id (lowercase, hyphenated), when it's one in any
    form (capitals, braces, `urn:uuid:`); else None."""
    try:
        return str(uuid.UUID(value))
    except ValueError:
        return None


def _detail(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return response.text[:300] or response.reason_phrase
    detail = body.get("detail", body) if isinstance(body, dict) else body
    if isinstance(detail, list):  # FastAPI's validation errors
        return "; ".join(f"{'.'.join(map(str, d.get('loc', [])))}: {d.get('msg')}"
                         if isinstance(d, dict) else str(d) for d in detail)
    return str(detail)
