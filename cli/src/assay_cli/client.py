# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Francesco Campanile
"""A thin, synchronous client for the parts of Assay's API a pipeline needs:
start a run or a statistics batch, read how it stands, list its runs."""
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
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


def _transient(status: int) -> bool:
    """Replies a gateway or a busy API gives in passing (a database busy for a
    moment): worth asking again."""
    return status == 429 or status >= 500


class ApiError(Exception):
    """The API refused a request, or couldn't be reached. `transient`: the network,
    a gateway or the API failed in passing (unreachable, 429, any 5xx), so the same
    read may succeed later."""

    def __init__(self, message: str, status_code: int | None = None,
                 transient: bool = False):
        super().__init__(message)
        self.status_code = status_code
        self.transient = transient


class AssayClient:
    def __init__(self, http: httpx.Client):
        self.http = http
        self._limit: float | None = None

    @classmethod
    def connect(cls, base_url: str, headers: dict[str, str] | None = None,
                timeout: float = 30.0) -> "AssayClient":
        return cls(httpx.Client(base_url=base_url.rstrip("/"), headers=headers or {},
                                timeout=timeout))

    def close(self) -> None:
        self.http.close()

    @contextmanager
    def within(self, seconds: float | None):
        """Requests made inside wait at most `seconds` for each step (connecting,
        each read), however long --http-timeout is; None: no limit of its own."""
        self._limit = seconds
        try:
            yield self
        finally:
            self._limit = None

    # -- plumbing --

    def _request(self, method: str, path: str, **kwargs) -> Any:
        if self._limit is not None:
            kwargs["timeout"] = httpx.Timeout(**{
                step: self._limit if limit is None else min(limit, self._limit)
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
            raise ApiError(f"{method} {path}: no reply from Assay ({exc}); it may have "
                           "been carried out, so look in Assay before trying again") from exc
        if response.is_redirect:
            # never followed: a redirected POST could start nothing, or start it twice
            where = response.headers.get("location", "elsewhere")
            raise ApiError(f"{method} {path}: {response.status_code} redirected to {where}; "
                           "give the API's final address with --url", response.status_code)
        if response.status_code >= 400:
            raise ApiError(f"{method} {path}: {response.status_code} {_detail(response)}",
                           response.status_code, transient=_transient(response.status_code))
        if not response.content:
            raise ApiError(f"{method} {path}: an empty reply — is the URL Assay's API?",
                           response.status_code)
        try:
            return response.json()
        except ValueError as exc:
            raise ApiError(f"{method} {path}: the reply isn't JSON — is the URL Assay's API?",
                           response.status_code) from exc

    def _pages(self, path: str, params: dict | None = None, limit: int = 100,
               key: str = "items") -> Iterator[dict]:
        offset = 0
        while True:
            page = self._request("GET", path, params={**(params or {}),
                                                      "offset": offset, "limit": limit})
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
        if scope == "test":
            if replay:
                raise ValueError("a standalone test has no executions to replay")
            return self._request("POST", f"/runs/standalone/{scope_id}")
        path = f"/runs/{scope}s/{scope_id}"
        if replay:
            path += f"/executions/{replay}"
        return self._request("POST", path)

    def standalone_run(self, test_id: str, run_id: str) -> dict:
        return self._request("GET", f"/runs/standalone/{test_id}/test-runs/{run_id}")

    def execution(self, scope: str, scope_id: str, execution_id: str) -> dict:
        return self._request("GET", f"/runs/{scope}s/{scope_id}/executions/{execution_id}")

    def execution_runs(self, scope: str, scope_id: str, execution_id: str) -> list[dict]:
        path = f"/runs/{scope}s/{scope_id}/executions/{execution_id}/test-runs"
        return list(self._pages(path, {"sort": "worst_first"}))

    # -- statistics batches --

    def start_batch(self, body: dict) -> dict:
        return self._request("POST", "/statistics/batches", json=body)

    def batch(self, batch_id: str, series: bool = False) -> dict:
        return self._request("GET", f"/statistics/batches/{batch_id}",
                             params={"series": str(series).lower()})

    def stop_batch(self, batch_id: str) -> dict:
        return self._request("POST", f"/statistics/batches/{batch_id}/stop")


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
