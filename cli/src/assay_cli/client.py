# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Francesco Campanile
"""A thin, synchronous client for the parts of Assay's API a pipeline needs:
start a run or a statistics batch, read how it stands, list its runs."""
import math
import uuid
from collections.abc import Callable, Iterator
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


_GATEWAY_STATUSES = frozenset({429, 502, 503, 504})
"""Replies a gateway or a busy API gives in passing: worth asking again until the
deadline. To a write, they leave open whether the API carried it out."""
SERVER_ERROR_RETRIES = 3
"""A 500 is the API's own failure: a database busy for a moment passes, a bug doesn't."""
_MAYBE_DONE = "; it may have been carried out, so look in Assay before trying again"
TimeLeft = Callable[[], float]
"""Seconds a request may still wait, asked just before it's sent."""


def _retries(status: int) -> float:
    if status in _GATEWAY_STATUSES:
        return math.inf
    return SERVER_ERROR_RETRIES if status == 500 else 0


class ApiError(Exception):
    """The API refused a request, or couldn't be reached. `retries`: how many times
    in a row the same read is worth trying again (`transient`: at all), until the
    deadline; `inf` for the network or a gateway failing in passing."""

    def __init__(self, message: str, status_code: int | None = None,
                 transient: bool = False, retries: float | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.retries = (math.inf if transient else 0) if retries is None else retries
        self.transient = self.retries > 0


class AssayClient:
    def __init__(self, http: httpx.Client):
        self.http = http

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
        """`time_left`: each step of the request (connecting, each read) waits no
        longer than it says, however long --http-timeout is."""
        if time_left is not None:
            left = time_left()
            if left <= 0:
                raise ApiError(f"{method} {path}: out of time, not sent", transient=True)
            kwargs["timeout"] = httpx.Timeout(**{
                step: left if limit is None else min(limit, left)
                for step, limit in self.http.timeout.as_dict().items()})
        try:
            response = self.http.request(method, path, **kwargs)
        except _NOT_SENT as exc:
            raise ApiError(f"{method} {path}: couldn't reach Assay ({exc})",
                           transient=True) from exc
        except httpx.HTTPError as exc:
            if method == "GET":
                raise ApiError(f"{method} {path}: no reply from Assay ({exc})",
                               transient=True) from exc
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
            if method != "GET" and status in _GATEWAY_STATUSES - {429}:
                # a gateway gave up waiting: the API behind it may have carried it out
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

    def _pages(self, path: str, params: dict | None = None, limit: int = 100,
               key: str = "items", time_left: TimeLeft | None = None) -> Iterator[dict]:
        """Every page, each asking `time_left` afresh."""
        offset = 0
        while True:
            page = self._request("GET", path, time_left=time_left,
                                 params={**(params or {}), "offset": offset, "limit": limit})
            items = page.get(key, [])
            yield from items
            offset += len(items)
            if not items or offset >= page.get("total", 0):
                return

    # -- reading --

    def health(self) -> dict:
        return self._request("GET", "/health/worker")

    def resolve(self, scope: str, ref: str) -> dict:
        """A test, test set or plan by id, or by its exact name: `{id, name}`."""
        noun = SCOPE_NOUNS[scope]
        if _is_uuid(ref):
            item = self._request("GET", f"{_LIST_PATHS[scope]}/{ref}")
            return {"id": str(item.get("id", ref)), "name": item.get("name", ref)}
        listed = self._pages(_LIST_PATHS[scope], {"q": ref}, limit=500,
                             key=_LIST_KEYS.get(scope, "items"))
        found = [item for item in listed if item.get("name") == ref]
        if not found:
            raise ApiError(f"No {noun} is named {ref!r}. Give its exact name or its id.")
        if len(found) > 1:
            ids = ", ".join(str(item["id"]) for item in found)
            raise ApiError(f"{len(found)} {noun}s are named {ref!r} ({ids}); give the id.")
        return {"id": str(found[0]["id"]), "name": found[0]["name"]}

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
        return list(self._pages(path, {"sort": "worst_first"}, time_left=time_left))

    # -- statistics batches --

    def start_batch(self, body: dict) -> dict:
        return self._request("POST", "/statistics/batches", json=body)

    def batch(self, batch_id: str, series: bool = False,
              time_left: TimeLeft | None = None) -> dict:
        return self._request("GET", f"/statistics/batches/{batch_id}", time_left=time_left,
                             params={"series": str(series).lower()})

    def stop_batch(self, batch_id: str, time_left: TimeLeft | None = None) -> dict:
        return self._request("POST", f"/statistics/batches/{batch_id}/stop", time_left=time_left)


def _is_uuid(value: str) -> bool:
    try:
        uuid.UUID(value)
    except ValueError:
        return False
    return True


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
