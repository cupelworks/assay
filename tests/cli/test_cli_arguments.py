# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Francesco Campanile
"""The command line's options, and every rule about them checked before any
request is sent."""
import io
import re
import tomllib
from pathlib import Path

import pytest

from assay_cli.arguments import ArgumentError, api_url, build_parser, headers
from assay_cli.cli import EXIT_ERROR, main


def test_headers_from_the_environment_and_flags():
    args = build_parser().parse_args(["--header", "X-A: 1", "health", "--header", "X-B:2"])
    chosen = headers(args, {"ASSAY_API_HEADERS": '{"X-C": "3", "X-A": "0"}'})
    assert chosen == {"X-A": "1", "X-B": "2", "X-C": "3"}
    with pytest.raises(ArgumentError):
        headers(build_parser().parse_args(["health"]), {"ASSAY_API_HEADERS": "[1]"})


def test_connection_options_before_the_command_survive_the_subcommand():
    args = build_parser().parse_args(["--url", "http://a", "run", "test", "x"])
    assert args.url == "http://a" and args.header == [] and args.http_timeout == 30.0


def test_fail_on_is_checked_and_normalised():
    args = build_parser().parse_args(["run", "test", "x", "--fail-on", "red, notran,Red"])
    assert args.fail_on == ("Red", "NotRan")
    with pytest.raises(SystemExit):
        build_parser().parse_args(["run", "test", "x", "--fail-on", "Blue"])


def test_without_a_url_it_says_how_to_give_one():
    err = io.StringIO()
    assert main(["health"], env={}, out=io.StringIO(), err=err) == EXIT_ERROR
    assert "--url or $ASSAY_API_URL" in err.getvalue()


@pytest.mark.parametrize("url", ["https://assay.internal/api", "http://localhost:8000"])
def test_an_http_or_https_url_is_the_apis(url):
    assert api_url(url) == url


@pytest.mark.parametrize(("url", "says"), [
    ("assay.internal/api", "must start with http:// or https://"),
    ("ftp://assay.internal", "must start with http:// or https://"),
    ("https://", "must start with http:// or https:// and name a host"),
    ("http://[::1", "isn't valid"),
])
def test_a_url_that_could_never_be_reached_is_refused(url, says):
    with pytest.raises(ArgumentError, match=re.escape(says)):
        api_url(url)


def test_a_bad_url_is_refused_before_any_request():
    err = io.StringIO()
    code = main(["health"], env={"ASSAY_API_URL": "assay.internal/api"}, out=io.StringIO(),
                err=err)
    assert code == EXIT_ERROR
    assert "the API's URL must start with http:// or https://" in err.getvalue()


@pytest.mark.parametrize(("header", "env", "says"), [
    ("X A: 1", None, "--header 'X A: 1': 'X A' isn't a header name"),
    ("X-A: café", None, "header X-A's value may hold only printable ASCII"),
    (None, '{"X-A": "a\\nb"}', "$ASSAY_API_HEADERS: header X-A's value"),
    (None, '{"X:A": "1"}', "$ASSAY_API_HEADERS: 'X:A' isn't a header name"),
])
def test_a_header_http_doesnt_allow_is_refused(header, env, says):
    args = build_parser().parse_args(["health", *(["--header", header] if header else [])])
    with pytest.raises(ArgumentError) as raised:
        headers(args, {"ASSAY_API_HEADERS": env} if env else {})
    assert says in str(raised.value)


def test_a_header_value_keeps_its_inner_spaces_and_may_be_empty():
    args = build_parser().parse_args(["health", "--header", "Authorization:  Bearer a b ",
                                      "--header", "X-Empty:"])
    assert headers(args, {}) == {"Authorization": "Bearer a b", "X-Empty": ""}


@pytest.mark.parametrize("option", ["--interval", "--timeout", "--http-timeout"])


def test_waiting_options_refuse_impossible_values(option):
    for value in ("-1", "inf", "nan"):
        with pytest.raises(SystemExit):
            build_parser().parse_args(["run", "test", "x", option, value])
    if option != "--timeout":  # 0 waits forever
        with pytest.raises(SystemExit):
            build_parser().parse_args(["run", "test", "x", option, "0"])
    assert build_parser().parse_args(["run", "test", "x", "--timeout", "0"]).timeout == 0


def test_a_header_replaces_one_of_the_same_name_whatever_its_case():
    args = build_parser().parse_args(["health", "--header", "authorization: Bearer new"])
    chosen = headers(args, {"ASSAY_API_HEADERS": '{"Authorization": "Bearer old"}'})
    assert chosen == {"authorization": "Bearer new"}


def test_command_line_mistakes_are_caught_before_any_request():
    class Untouched:
        def __getattr__(self, name):
            raise AssertionError(f"the API was called: {name}")

    for argv in (["run", "test", "x", "--replay", "e"],
                 ["batch", "test", "x", "-s", "binomial_gate", "-p", "target"]):
        with pytest.raises(SystemExit) as raised:
            main(argv, client=Untouched(), env={}, out=io.StringIO(), err=io.StringIO())
        assert raised.value.code == EXIT_ERROR


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity", "1e400", "[NaN]"])


def test_a_parameter_json_cant_send_is_refused(value):
    with pytest.raises(SystemExit):
        build_parser().parse_args(["batch", "test", "x", "-s", "t", "-p", f"target={value}"])


def test_parameters_are_read_as_json_when_they_parse():
    args = build_parser().parse_args(["batch", "test", "x", "-s", "t", "-p", "target=0.9",
                                      "-p", "label=a=b", "-p", "flag=true"])
    assert dict(args.param) == {"target": 0.9, "label": "a=b", "flag": True}


def test_the_version_is_written_once():
    project = tomllib.loads((Path(__file__).parents[2] / "cli" / "pyproject.toml").read_text())
    assert "version" not in project["project"] and "version" in project["project"]["dynamic"]
    assert project["tool"]["hatch"]["version"]["path"] == "src/assay_cli/__init__.py"
