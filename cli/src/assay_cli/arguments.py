# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Francesco Campanile
"""The command line: its options, and every rule about them checked before anything
is sent to Assay. A mistake exits 2 with argparse's usage line."""
import argparse
import json
import math
import re
from collections.abc import Callable, Mapping
from pathlib import Path

import httpx

from assay_cli import __version__
from assay_cli.client import SCOPES, canonical_id
from assay_cli.gate import (
    BATCH_FAIL_ON,
    BATCH_FAIL_ON_CHOICES,
    RUN_FAIL_ON,
    RUN_STATUSES,
    PolicyError,
    spelled,
)
from assay_cli.policy import max_failures_of, pass_rate_of

URL_ENV = "ASSAY_API_URL"
HEADERS_ENV = "ASSAY_API_HEADERS"
INTERVAL = 5
"""Seconds between two checks on progress, and before trying a failed read again."""
_HEADER_NAME = re.compile(r"[!#$%&'*+\-.^_`|~0-9A-Za-z]+")
"""A header's name, as HTTP defines it: a token."""


class ArgumentError(Exception):
    """A setting outside the command line itself is wrong: its environment."""


def parse(argv: list[str] | None) -> argparse.Namespace:
    parser = build_parser()
    args = parser.parse_args(argv)
    if getattr(args, "replay", None) is not None and args.scope == "test":
        parser.error("--replay: a standalone test has no executions to replay")
    return args


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="assay",
        description="Run Assay's tests from a pipeline and fail the step when they fail.",
        epilog="Exit codes: 0 passed, 1 failed the gate, 2 error, 3 timed out, "
               "4 couldn't be judged (it failed only on runs that Not Ran), "
               "130 interrupted, 143 cancelled.")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    _connection_arguments(parser, defaults=True)
    commands = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")
    # the connection's options are accepted after the command too
    connection = argparse.ArgumentParser(add_help=False)
    _connection_arguments(connection, defaults=False)

    commands.add_parser("health", parents=[connection],
                        help="Check the API is reachable and a worker is ready.")

    run = commands.add_parser(
        "run", parents=[connection],
        help="Run a test, test set or plan once and gate on its runs' outcomes.",
        description="Start a run (a live execution, or a replay of a past one), wait for "
                    "every run to finish, and fail when a run's outcome is in --fail-on, or, "
                    "with limits, when more runs fail than they allow.")
    _scope_arguments(run)
    # an id, or nothing is replayed: "" (an unset variable) would start a live run,
    # and a path such as ../.. a run of something else
    run.add_argument("--replay", type=_execution_id, metavar="EXECUTION_ID",
                     help="Replay this past execution of the set or plan: exactly its entries.")
    run.add_argument("--fail-on", type=_choices(RUN_STATUSES), metavar="STATUSES",
                     help="Comma-separated run outcomes that fail a run "
                          f"(default {','.join(RUN_FAIL_ON)}, or the policy's).")
    tolerance = run.add_argument_group(
        "tolerance", "By default any failing run fails the step. These tolerate some, or "
                     "block on a check. Over a policy file, a limit replaces the file's for "
                     "one run; a blocking check is added to the file's.")
    tolerance.add_argument("--policy", type=Path, metavar="FILE",
                           help="A YAML gate policy, e.g. assay-gate.yml in your repository.")
    tolerance.add_argument("--max-failures", type=_limit(max_failures_of), action="append",
                           default=[], metavar="[TEST_SET=]N",
                           help="At most N failing runs: over the whole execution, or in "
                                "that test set; repeatable.")
    tolerance.add_argument("--min-pass-rate", type=_limit(pass_rate_of), action="append",
                           default=[], metavar="[TEST_SET=]RATE",
                           help="At least RATE (0 to 1) of the runs passing: over the whole "
                                "execution, or in that test set; repeatable.")
    tolerance.add_argument("--blocking-check", type=_label, action="append", default=[],
                           metavar="LABEL",
                           help="A check label that fails the step whenever it fails, "
                                "whatever the counts; repeatable, added to the policy's.")
    _wait_arguments(run)
    _output_arguments(run)

    batch = commands.add_parser(
        "batch", parents=[connection],
        help="Run a scope N times as a statistics batch and gate on its verdicts.",
        description="Start a statistics batch, wait for its result, and fail when a check's "
                    "verdict is in --fail-on, or when the batch couldn't decide anything.")
    _scope_arguments(batch)
    batch.add_argument("--statistical-test", "-s", required=True, metavar="ID",
                       help="A batch test's id from GET /statistics/tests.")
    batch.add_argument("--times", "-n", type=_positive_int, metavar="N",
                       help="Times to run the scope (default: the catalogue's suggestion).")
    batch.add_argument("--param", "-p", action="append", default=[], type=_parameter,
                       metavar="KEY=VALUE",
                       help="A parameter of the statistical test, e.g. target=0.9; "
                            "repeatable. VALUE is read as JSON when it parses.")
    batch.add_argument("--note", help="Kept with the batch, e.g. the commit or PR it tests.")
    batch.add_argument("--fail-on", type=_choices(BATCH_FAIL_ON_CHOICES), default=BATCH_FAIL_ON,
                       metavar="VERDICTS",
                       help="Comma-separated verdicts that fail the step: fail, inconclusive "
                            f"(default {','.join(BATCH_FAIL_ON)}). A check proven to fail, and "
                            "a batch that was stopped or couldn't decide anything, always "
                            "fail it.")
    batch.add_argument("--stop-on-timeout", action="store_true",
                       help="Stop the batch when the step ends without its result "
                            "(--timeout reached, the job cancelled, Ctrl-C, an error), so "
                            "its remaining runs aren't paid for.")
    _wait_arguments(batch)
    _output_arguments(batch)
    return parser


def _connection_arguments(parser: argparse.ArgumentParser, defaults: bool) -> None:
    """On the subcommands, an option left out mustn't overwrite one given before them."""
    def default(value):
        return value if defaults else argparse.SUPPRESS
    parser.add_argument("--url", default=default(None),
                        help=f"Assay's API, e.g. https://assay.internal/api (or ${URL_ENV}).")
    parser.add_argument("--header", action="append", default=[] if defaults else None,
                        dest="header" if defaults else "header_after", metavar="'NAME: VALUE'",
                        help="A header sent with every request, e.g. for a gateway in front "
                             f"of the API; repeatable. ${HEADERS_ENV} takes a JSON object.")
    parser.add_argument("--http-timeout", type=_positive_float, default=default(30.0),
                        metavar="SECONDS",
                        help="The longest a request to the API may take, in all "
                             "(default 30).")


def _scope_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("scope", choices=SCOPES, help="What to run.")
    parser.add_argument("ref", metavar="ID_OR_NAME", help="Its id, or its exact name.")


def _wait_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--no-wait", action="store_true",
                        help="Start it, print its id and exit 0 without waiting.")
    parser.add_argument("--timeout", type=_non_negative_float, default=3600, metavar="SECONDS",
                        help="Give up waiting after this long (default 3600; 0 waits forever).")
    parser.add_argument("--interval", type=_positive_float, default=INTERVAL, metavar="SECONDS",
                        help=f"Time between checks on its progress (default {INTERVAL}).")


def _output_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--json", action="store_true",
                        help="Print the outcome as JSON instead of text.")
    parser.add_argument("--junit", type=Path, metavar="FILE",
                        help="Write a JUnit XML report, for the CI system's test results.")
    parser.add_argument("--markdown", type=Path, metavar="FILE",
                        help="Append a Markdown summary to FILE. On GitHub Actions it's "
                             "appended to $GITHUB_STEP_SUMMARY without this.")


def _choices(allowed: tuple[str, ...]) -> Callable[[str], tuple[str, ...]]:
    """Comma-separated, in any case, each once: spelled as `allowed` spells them."""
    def parse(value: str) -> tuple[str, ...]:
        try:
            return spelled(allowed, value.split(","))
        except ValueError as exc:
            raise argparse.ArgumentTypeError(str(exc)) from None
    return parse


def _limit(parse: Callable[[object, str], object]) -> Callable[[str], tuple]:
    """`[TEST_SET=]VALUE`: the test set is everything before the last `=`."""
    def limit(text: str) -> tuple:
        test_set, sep, value = text.rpartition("=")
        if sep and not test_set.strip():
            raise argparse.ArgumentTypeError(f"expected [TEST_SET=]VALUE (got {text!r})")
        try:
            return (test_set.strip() or None), parse(value.strip(), "value")
        except PolicyError as exc:
            raise argparse.ArgumentTypeError(str(exc).removeprefix("value: ")) from None
    return limit


def _execution_id(value: str) -> str:
    """An execution's id, in any form, as Assay spells it."""
    execution_id = canonical_id(value.strip())
    if execution_id is None:
        raise argparse.ArgumentTypeError(f"expected an execution's id (got {value!r})")
    return execution_id


def _label(value: str) -> str:
    """A check's label, without the spaces around it: they'd match no check."""
    if not value.strip():
        raise argparse.ArgumentTypeError("expected a check's label")
    return value.strip()


def _positive_int(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return number


def _positive_float(value: str) -> float:
    number = float(value)
    if not (number > 0 and math.isfinite(number)):  # NaN too
        raise argparse.ArgumentTypeError("must be a number more than 0")
    return number


def _non_negative_float(value: str) -> float:
    number = float(value)
    if not (number >= 0 and math.isfinite(number)):
        raise argparse.ArgumentTypeError("must be a number, 0 or more")
    return number


def _parameter(pair: str) -> tuple[str, object]:
    """VALUE as JSON when it parses (`0.9`, `true`), else as text."""
    key, sep, value = pair.partition("=")
    if not sep or not key.strip():
        raise argparse.ArgumentTypeError(f"expected KEY=VALUE (got {pair!r})")
    try:
        parsed = json.loads(value)
    except ValueError:
        return key.strip(), value
    try:
        json.dumps(parsed, allow_nan=False)
    except ValueError:  # NaN, Infinity, 1e400: JSON can't send them
        raise argparse.ArgumentTypeError(f"{pair!r}: not a finite number") from None
    return key.strip(), parsed


def api_url(url: str) -> str:
    """An http:// or https:// address with a host, and no query or fragment, which
    every request's path would be added after; the spaces around it, as a secret
    pasted with them would keep, left out."""
    url = url.strip()
    try:
        parsed = httpx.URL(url)
    except httpx.InvalidURL as exc:
        # httpx's reason may quote part of a password: kept out when there may be one
        why = "" if "@" in url else f": {exc}"
        raise ArgumentError(f"the API's URL {_shown(url)} isn't valid{why}") from None
    if parsed.scheme not in ("http", "https") or not parsed.host:
        raise ArgumentError(f"the API's URL must start with http:// or https:// and name "
                            f"a host, e.g. https://assay.internal/api (got {_shown(url)})")
    if parsed.query or parsed.fragment or url.endswith(("?", "#")):
        raise ArgumentError(f"the API's URL can't have a query (?) or a fragment (#), as "
                            f"each request's path follows it (got {_shown(url)}); send a "
                            "gateway's key as a header with --header")
    return url


def _shown(url: str) -> str:
    """The URL for an error, unless it holds an @: a password may be before it, and
    may hold any character, so no part of it is shown then."""
    return "(not shown, as it may hold a password)" if "@" in url else repr(url)


def headers(args: argparse.Namespace, env: Mapping[str, str]) -> dict[str, str]:
    """$ASSAY_API_HEADERS, then each --header: a later one replaces an earlier one
    of the same name, whatever its case, as HTTP names are. A name or a value HTTP
    doesn't allow is an error here, not on every request; no error shows a value,
    nor a --header that isn't one, as either may hold a secret."""
    chosen: dict[str, str] = {}

    def put(name: str, value: str, where: str) -> None:
        name, value = name.strip(), value.strip()
        if not _HEADER_NAME.fullmatch(name):
            raise ArgumentError(f"{where}: a header's name holds only letters, digits and "
                                "punctuation such as -, with no spaces")
        if not all(char == "\t" or " " <= char <= "~" for char in value):
            raise ArgumentError(f"{where}: header {name}'s value may hold only printable "
                                "ASCII characters")
        for given in [given for given in chosen if given.lower() == name.lower()]:
            del chosen[given]
        chosen[name] = value

    if env.get(HEADERS_ENV):
        try:
            loaded = json.loads(env[HEADERS_ENV])
        except ValueError as exc:
            raise ArgumentError(f"${HEADERS_ENV} isn't a JSON object: {exc.msg}, at "
                                f"character {exc.pos}") from None
        if not isinstance(loaded, dict):
            raise ArgumentError(f"${HEADERS_ENV} must be a JSON object of header names "
                                "to values")
        for number, (name, value) in enumerate(loaded.items(), 1):
            where = f"${HEADERS_ENV}, header {number}"
            if not isinstance(value, str):
                raise ArgumentError(f"{where}: a value must be text, in quotes")
            put(name, value, where)
    given = [*args.header, *(getattr(args, "header_after", None) or [])]
    for number, header in enumerate(given, 1):
        where = f"--header {number}" if len(given) > 1 else "--header"
        name, sep, value = header.partition(":")
        if not sep or not name.strip():
            raise ArgumentError(f"{where}: expected 'NAME: VALUE', a colon after the name "
                                "(the header isn't shown, as it may hold a secret)")
        put(name, value, where)
    return chosen
