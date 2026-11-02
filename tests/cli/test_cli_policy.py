# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Francesco Campanile
"""Gate policy files and the flags over them: what a file says, every mistake in
one named, and which flags replace or add to it."""
import pytest

from assay_cli.arguments import build_parser
from assay_cli.gate import RUN_FAIL_ON, STRICT, Limits, PolicyError
from assay_cli.policy import check_scope, load, with_flags

FULL = """\
# assay-gate.yml
test_plan: "Release checks"
fail_on: [Red, NotRan]          # Amber tolerated
max_failures: 3
test_sets:
  "Smoke":  { max_failures: 0 }
  "Tone":   { min_pass_rate: 0.80 }
blocking_checks: ["Toxicity", "Valid JSON"]
"""


def _file(tmp_path, text):
    path = tmp_path / "assay-gate.yml"
    path.write_text(text, encoding="utf-8")
    return path


def _error(tmp_path, text):
    with pytest.raises(PolicyError) as raised:
        load(_file(tmp_path, text))
    return str(raised.value)


def test_a_policy_file_says_everything_the_gate_judges_by(tmp_path):
    policy = load(_file(tmp_path, FULL))

    assert policy.scope == ("test-plan", "Release checks")
    assert policy.fail_on == ("Red", "NotRan")
    assert policy.limits == Limits(max_failures=3)
    assert dict(policy.test_sets) == {"Smoke": Limits(max_failures=0),
                                      "Tone": Limits(min_pass_rate=0.8)}
    assert policy.blocking_checks == ("Toxicity", "Valid JSON")
    assert policy.tolerant and policy.source.endswith("assay-gate.yml")


def test_an_empty_file_is_the_strict_default(tmp_path):
    policy = load(_file(tmp_path, "# nothing loosened yet\n"))

    assert policy.fail_on == RUN_FAIL_ON and not policy.tolerant


def test_fail_on_may_be_a_list_or_comma_separated_in_any_case(tmp_path):
    assert load(_file(tmp_path, "fail_on: red, notran\n")).fail_on == ("Red", "NotRan")


@pytest.mark.parametrize(("text", "says"), [
    ("max_failure: 3\n", "unknown key 'max_failure'"),
    ("max_failures: 3\nmax_failures: 9\n", "line 2: 'max_failures' is given twice"),
    ("test_sets:\n  Smoke: {max_failures: 0}\n  Smoke: {max_failures: 5}\n",
     "'Smoke' is given twice"),
    ("test_sets:\n  No: {max_failures: 0}\n", "a name YAML reads as False, not as text: quote"),
    ("test_sets:\n  Smoke: {}\n", "test_sets: Smoke: expected max_failures, min_pass_rate"),
    ("test_sets:\n  Smoke: {max_fails: 0}\n", "test_sets: Smoke: unknown key 'max_fails'"),
    ("test_sets: [Smoke]\n", "test_sets: expected each test set's name"),
    ("max_failures: -1\n", "max_failures: expected a whole number, 0 or more (got -1)"),
    ("max_failures: 1.5\n", "expected a whole number"),
    ("max_failures: true\n", "expected a whole number"),
    ('max_failures: "²"\n', "expected a whole number"),  # a digit int() can't read
    ("min_pass_rate: 80\n", "min_pass_rate: expected a share from 0 to 1, e.g. 0.8 for 80%"),
    ("min_pass_rate: -0.1\n", "expected a share from 0 to 1"),
    ("min_pass_rate: .nan\n", "expected a share from 0 to 1"),
    ("fail_on: [Red, Purple]\n", "fail_on: choose from Green, Amber, Red, NotRan"),
    ("blocking_checks: [Toxicity, 3]\n", "blocking_checks: expected a name, as text (got 3)"),
    ("test_set: Smoke\ntest_plan: Release\n", "give one of test_set, test_plan"),
    ("- max_failures: 3\n", "expected keys such as fail_on"),
    ("max_failures: [3\n", "not valid YAML"),
])
def test_each_mistake_in_a_file_is_named(tmp_path, text, says):
    message = _error(tmp_path, text)

    assert says in message and message.startswith(str(tmp_path / "assay-gate.yml"))


def test_a_file_that_cant_be_read_is_named(tmp_path):
    with pytest.raises(PolicyError, match="assay-gate.yml: No such file"):
        load(tmp_path / "assay-gate.yml")


def test_flags_replace_the_files_limits_and_fail_on_one_by_one(tmp_path):
    policy = with_flags(load(_file(tmp_path, FULL)), ("Amber", "Red"),
                        [(None, 5), ("Smoke", 1)], [("Tone", 0.5), ("Extra", 0.9)], [])

    assert policy.fail_on == ("Amber", "Red")
    assert policy.limits == Limits(max_failures=5)
    assert dict(policy.test_sets) == {"Smoke": Limits(max_failures=1),
                                      "Tone": Limits(min_pass_rate=0.5),
                                      "Extra": Limits(min_pass_rate=0.9)}


def test_a_flag_adds_a_blocking_check_but_never_drops_one(tmp_path):
    policy = with_flags(load(_file(tmp_path, FULL)), None, [], [],
                        ["toxicity", "Bias", "BIAS"])

    assert policy.blocking_checks == ("Toxicity", "Valid JSON", "Bias")  # each once, any case
    assert policy.fail_on == ("Red", "NotRan")  # the file's, with no --fail-on


def test_without_a_file_flags_alone_make_the_policy():
    policy = with_flags(STRICT, None, [(None, 2)], [], [])

    assert policy.limits == Limits(max_failures=2) and policy.fail_on == RUN_FAIL_ON


def test_a_file_naming_its_scope_applies_to_that_scope_alone(tmp_path):
    policy = load(_file(tmp_path, FULL))

    check_scope(policy, "test-plan", {"id": "p1", "name": "Release checks"})
    with pytest.raises(PolicyError, match="is for the test plan 'Release checks', not the "
                                          "test plan 'Nightly'"):
        check_scope(policy, "test-plan", {"id": "p2", "name": "Nightly"})
    with pytest.raises(PolicyError, match="not the test set 'Release checks'"):
        check_scope(policy, "test-set", {"id": "s1", "name": "Release checks"})


def test_a_scope_may_be_named_by_its_id(tmp_path):
    policy = load(_file(tmp_path, "test_set: 4e86003a-9e28-4c93-a08e-f99c6acbaab6\n"))

    check_scope(policy, "test-set", {"id": "4e86003a-9e28-4c93-a08e-f99c6acbaab6",
                                     "name": "Smoke"})


@pytest.mark.parametrize(("option", "value", "parsed"), [
    ("--max-failures", "3", (None, 3)),
    ("--max-failures", "Smoke=0", ("Smoke", 0)),
    ("--max-failures", "a=b=1", ("a=b", 1)),  # a set's name may hold an =
    ("--min-pass-rate", "Tone=0.8", ("Tone", 0.8)),
    ("--min-pass-rate", "1", (None, 1.0)),
])
def test_a_limit_flag_is_for_the_whole_or_for_one_test_set(option, value, parsed):
    args = build_parser().parse_args(["run", "test-plan", "x", option, value])
    assert getattr(args, option[2:].replace("-", "_")) == [parsed]


@pytest.mark.parametrize(("option", "value"), [
    ("--max-failures", "-1"), ("--max-failures", "Smoke="), ("--max-failures", "=3"),
    ("--max-failures", "2.5"), ("--min-pass-rate", "80"), ("--min-pass-rate", "Tone=nan"),
])
def test_a_limit_flag_out_of_range_is_refused(option, value):
    with pytest.raises(SystemExit):
        build_parser().parse_args(["run", "test-plan", "x", option, value])
