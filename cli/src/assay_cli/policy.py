# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Francesco Campanile
"""A gate policy read from a YAML file kept in the application's repository, so
loosening the gate is reviewed like any change, then the flags given for one run.
The file is read strictly: an unknown or repeated key, a name YAML would read as
something other than text, a limit out of range, each is an error naming it."""
import contextlib
import math
from collections.abc import Iterable, Mapping
from pathlib import Path
from types import MappingProxyType

import yaml

from assay_cli.client import SCOPE_NOUNS
from assay_cli.gate import RUN_STATUSES, STRICT, Limits, Policy, PolicyError, spelled

_SCOPE_KEYS = {"test": "test", "test_set": "test-set", "test_plan": "test-plan"}
"""The key naming the scope a file is for, and that scope as the command line says it."""
_LIMIT_KEYS = ("max_failures", "min_pass_rate")
_KEYS = (*_SCOPE_KEYS, "fail_on", *_LIMIT_KEYS, "test_sets", "blocking_checks")


class _Loader(yaml.SafeLoader):
    """Safe, and a key given twice is an error: the second would win unseen."""


def _mapping(loader: _Loader, node: yaml.MappingNode, deep: bool = False) -> dict:
    seen = set()
    for key_node, _ in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in seen:
            raise PolicyError(f"line {key_node.start_mark.line + 1}: {key!r} is given twice")
        seen.add(key)
    return loader.construct_mapping(node, deep=deep)


_Loader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _mapping)


def load(path: Path) -> Policy:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise PolicyError(f"{path}: {exc.strerror or exc}") from exc
    try:
        data = yaml.load(text, Loader=_Loader)  # noqa: S506 - a SafeLoader
    except PolicyError as exc:
        raise PolicyError(f"{path}: {exc}") from None
    except yaml.YAMLError as exc:
        raise PolicyError(f"{path}: not valid YAML: {exc}") from exc
    try:
        return _policy(data if data is not None else {}, str(path))
    except PolicyError as exc:
        raise PolicyError(f"{path}: {exc}") from None


def _policy(data: object, source: str) -> Policy:
    if not isinstance(data, Mapping):
        raise PolicyError("expected keys such as fail_on, max_failures and test_sets")
    unknown = [str(key) for key in data if key not in _KEYS]
    if unknown:
        raise PolicyError(f"unknown key {', '.join(map(repr, unknown))} "
                          f"(the keys are: {', '.join(_KEYS)})")
    scopes = [key for key in _SCOPE_KEYS if key in data]
    if len(scopes) > 1:
        raise PolicyError(f"give one of {', '.join(scopes)}: a policy is for one scope")
    scope = None
    if scopes:
        scope = (_SCOPE_KEYS[scopes[0]], _text(data[scopes[0]], scopes[0]))
    fail_on = STRICT.fail_on
    if "fail_on" in data:
        try:
            fail_on = spelled(RUN_STATUSES, _list(data["fail_on"], "fail_on", split=True))
        except ValueError as exc:
            raise PolicyError(f"fail_on: {exc}") from None
    test_sets = data.get("test_sets") or {}
    if not isinstance(test_sets, Mapping):
        raise PolicyError("test_sets: expected each test set's name, then its limits")
    by_set = {}
    for name, limits in test_sets.items():
        if not isinstance(name, str):
            raise PolicyError(f"test_sets: a name YAML reads as {name!r}, not as text: quote "
                              "it, as YAML reads words such as No, Yes, On and Off, and "
                              "numbers, as something else")
        where = f"test_sets: {name}"
        if not isinstance(limits, Mapping):
            raise PolicyError(f"{where}: expected max_failures, min_pass_rate or both")
        extra = [str(key) for key in limits if key not in _LIMIT_KEYS]
        if extra:
            raise PolicyError(f"{where}: unknown key {', '.join(map(repr, extra))} "
                              f"(a test set takes {' and '.join(_LIMIT_KEYS)})")
        by_set[name] = _limits(limits, where)
        if not by_set[name].given:
            raise PolicyError(f"{where}: expected max_failures, min_pass_rate or both")
    blocking = tuple(dict.fromkeys(
        _text(label, "blocking_checks")
        for label in _list(data.get("blocking_checks") or [], "blocking_checks")))
    return Policy(fail_on, _limits(data, ""), MappingProxyType(by_set), blocking, scope, source)


def _limits(data: Mapping, where: str) -> Limits:
    prefix = f"{where}: " if where else ""
    max_failures = data.get("max_failures")
    if max_failures is not None:
        max_failures = max_failures_of(max_failures, f"{prefix}max_failures")
    min_pass_rate = data.get("min_pass_rate")
    if min_pass_rate is not None:
        min_pass_rate = pass_rate_of(min_pass_rate, f"{prefix}min_pass_rate")
    return Limits(max_failures, min_pass_rate)


def max_failures_of(value: object, what: str) -> int:
    """A whole number, 0 or more: bool isn't one, though Python counts it as an int."""
    if isinstance(value, str) and value.strip().isdigit():
        value = int(value)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise PolicyError(f"{what}: expected a whole number, 0 or more (got {value!r})")
    return value


def pass_rate_of(value: object, what: str) -> float:
    """A share from 0 to 1, e.g. 0.8 for 80%."""
    if isinstance(value, str):
        with contextlib.suppress(ValueError):
            value = float(value)
    if (isinstance(value, bool) or not isinstance(value, int | float)
            or not math.isfinite(value) or not 0 <= value <= 1):
        raise PolicyError(f"{what}: expected a share from 0 to 1, e.g. 0.8 for 80% "
                          f"(got {value!r})")
    return float(value)


def _list(value: object, what: str, split: bool = False) -> list:
    if isinstance(value, str):
        return value.split(",") if split else [value]
    if not isinstance(value, list):
        raise PolicyError(f"{what}: expected a list")
    return value


def _text(value: object, what: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PolicyError(f"{what}: expected a name, as text (got {value!r}): quote it")
    return value


def with_flags(policy: Policy, fail_on: tuple[str, ...] | None,
               max_failures: Iterable[tuple[str | None, int]],
               min_pass_rates: Iterable[tuple[str | None, float]],
               blocking_checks: Iterable[str]) -> Policy:
    """The flags given for one run, over the file's policy: each limit and `fail_on`
    given replaces the file's, for the whole or for its test set. A blocking check
    is only ever added: dropping one is a change to the file, reviewed as one."""
    whole = policy.limits
    by_set = dict(policy.test_sets)
    for test_set, limits in ([(s, Limits(max_failures=n)) for s, n in max_failures]
                             + [(s, Limits(min_pass_rate=r)) for s, r in min_pass_rates]):
        if test_set is None:
            whole = whole.updated(limits)
        else:
            by_set[test_set] = by_set.get(test_set, Limits()).updated(limits)
    return Policy(fail_on or policy.fail_on, whole, MappingProxyType(by_set),
                  tuple(dict.fromkeys((*policy.blocking_checks, *blocking_checks))),
                  policy.scope, policy.source)


def check_scope(policy: Policy, kind: str, target: dict) -> None:
    """A file naming its scope applies to that scope alone: never, by mistake, to
    another plan's gate."""
    if policy.scope is None:
        return
    wanted_kind, wanted = policy.scope
    if wanted_kind != kind or wanted not in (target["name"], target["id"]):
        raise PolicyError(f"{policy.source} is for the {SCOPE_NOUNS[wanted_kind]} {wanted!r}, "
                          f"not the {SCOPE_NOUNS[kind]} {target['name']!r}")
