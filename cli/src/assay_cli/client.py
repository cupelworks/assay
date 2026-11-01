# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Francesco Campanile
"""A thin, synchronous client for the parts of Assay's API a pipeline needs:
start a run or a statistics batch, read how it stands, list its runs."""
import uuid
from collections.abc import Iterator
from typing import Any

import httpx

SCOPES = ("test", "test-set", "test-plan")
"""What can be run, as the command line spells it."""

_LIST_PATHS = {"test": "/tests", "test-set": "/test-sets", "test-plan": "/test-plans"}
_LIST_KEYS = {"test": "test_cases"}
"""Where a list's items are, when not under `items`."""
_SCOPE_NOUNS = {"test": "test", "test-set": "test set", "test-plan": "test plan"}


class ApiError(Exception):
    """The API refused a request, or couldn't be reached."""

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


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

    def _request(self, method: str, path: str, **kwargs) -> Any:
        try:
            response = self.http.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise ApiError(f"{method} {path}: couldn't reach Assay ({exc})") from exc
        if response.status_code >= 400:
            raise ApiError(f"{method} {path}: {response.status_code} {_detail(response)}",
                           response.status_code)
        if not response.content:
            return None
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
        noun = _SCOPE_NOUNS[scope]
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
                raise ApiError("A standalone test has no executions to replay.")
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
