# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Francesco Campanile
"""The outcome written for people (text, a GitHub job summary in Markdown) and
for tools (JUnit XML, which GitLab, Azure Pipelines, Jenkins and most CI
systems show as test results)."""
import re
from xml.etree import ElementTree as ET

from assay_cli.client import SCOPE_NOUNS
from assay_cli.gate import Case, Outcome

_MARK = {"passed": "✓", "failed": "✗", "error": "!", "skipped": "–"}
MAX_LISTED = 50
"""Failures listed in the text summary; the JUnit report has every one."""
_NOT_IN_XML = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffe\uffff]")
"""Characters XML 1.0 can't hold: a CI system would refuse the whole report."""
GATE_SUITE = "Assay gate"
"""The JUnit suite holding a tolerant policy's rules, one case each."""
_UNJUDGED = ("it failed only on runs that Not Ran: the application or the judge didn't "
             "answer, so nothing about quality was decided")


def _gate_line(outcome: Outcome) -> str:
    """The rules that decided, when the policy had more than the default's one."""
    if not outcome.tolerant or not outcome.deciding:
        return ""
    return "; ".join(rule.text for rule in outcome.deciding)


def text(outcome: Outcome) -> str:
    lines = [_one_line(outcome.summary), f"  {outcome.kind} id: {outcome.id}"]
    if _gate_line(outcome):
        lines.append(f"  gate: {_gate_line(outcome)}")
    if not outcome.passed and not outcome.judged:
        lines.append(f"  couldn't be judged: {_UNJUDGED}")
    failures = outcome.failures
    for case in failures[:MAX_LISTED]:
        lines.append(f"  {_MARK[case.outcome]} {_one_line(_label(outcome, case))}: "
                     f"{_one_line(case.message)}")
    if len(failures) > MAX_LISTED:
        lines.append(f"  … and {len(failures) - MAX_LISTED} more (see the JUnit report)")
    if not outcome.passed and outcome.fail_on and not outcome.reason:
        lines.append(f"  fails on: {', '.join(outcome.fail_on)}")
    return "\n".join(lines)


def markdown(outcome: Outcome) -> str:
    icon = "✅" if outcome.passed else "❌"
    lines = [f"### {icon} Assay: {SCOPE_NOUNS[outcome.scope['kind']]} "
             f"{_code(outcome.scope['name'])}", "",
             _escape(outcome.summary), "",
             f"{outcome.kind.capitalize()} `{outcome.id}`"]
    if _gate_line(outcome):
        lines += ["", f"**Gate:** {_escape(_gate_line(outcome))}"]
    if not outcome.passed and not outcome.judged:
        lines += ["", f"**Couldn't be judged:** {_UNJUDGED}"]
    failures = outcome.failures
    if failures:
        lines += ["", "| | Where | What |", "|---|---|---|"]
        for case in failures[:MAX_LISTED]:
            lines.append(f"| {_MARK[case.outcome]} | {_escape(_label(outcome, case))} | "
                         f"{_escape(case.message)} |")
        if len(failures) > MAX_LISTED:
            lines.append(f"| | … and {len(failures) - MAX_LISTED} more | |")
    return "\n".join(lines) + "\n"


def junit(outcome: Outcome) -> bytes:
    suites = ET.Element("testsuites", name="assay")
    by_group: dict[str, list] = {}
    for case in outcome.cases:
        by_group.setdefault(case.group, []).append(case)
    for group, cases in (by_group or {outcome.scope["name"]: []}).items():
        suite = ET.SubElement(suites, "testsuite", name=_xml(group), tests=str(len(cases)),
                              failures=str(sum(c.outcome == "failed" for c in cases)),
                              errors=str(sum(c.outcome == "error" for c in cases)),
                              skipped=str(sum(c.outcome == "skipped" for c in cases)))
        ET.SubElement(ET.SubElement(suite, "properties"), "property",
                      name=f"assay.{outcome.kind}_id", value=outcome.id)
        for case in cases:
            element = ET.SubElement(suite, "testcase", name=_xml(case.name),
                                    classname=_xml(group))
            if case.outcome in ("failed", "error", "skipped"):
                tag = {"failed": "failure", "error": "error", "skipped": "skipped"}[case.outcome]
                ET.SubElement(element, tag, message=_xml(case.message))
            elif case.message:
                ET.SubElement(element, "system-out").text = _xml(case.message)
    if outcome.tolerant:
        _gate_suite(suites, outcome)
    ET.indent(suites)
    return ET.tostring(suites, encoding="utf-8", xml_declaration=True)


def _gate_suite(suites: ET.Element, outcome: Outcome) -> None:
    """Each rule a case, named the same from one build to the next, so a CI
    system's history shows which rule broke, and since when."""
    rules = outcome.rules
    suite = ET.SubElement(suites, "testsuite", name=GATE_SUITE, tests=str(len(rules)),
                          failures=str(sum(rule.broken for rule in rules)), errors="0",
                          skipped="0")
    for rule in rules:
        element = ET.SubElement(suite, "testcase", name=_xml(rule.name), classname=GATE_SUITE)
        if rule.broken:
            ET.SubElement(element, "failure", message=_xml(rule.text))
        else:
            ET.SubElement(element, "system-out").text = _xml(rule.text)


def _label(outcome: Outcome, case: Case) -> str:
    """A batch's check is named within its entry, a plan's run within its test set;
    any other run by its test's name."""
    grouped = outcome.kind == "batch" or (outcome.kind == "execution"
                                          and outcome.scope["kind"] == "test-plan")
    if not grouped or case.group in ("", case.name):
        return case.name
    return f"{case.group} › {case.name}"


def _xml(value: str) -> str:
    return _NOT_IN_XML.sub("", value)


def _one_line(value: str) -> str:
    """Line breaks, which would end a line of a report early, as spaces."""
    return value.replace("\r\n", " ").replace("\r", " ").replace("\n", " ")


def _escape(value: str) -> str:
    """On one line, and no `|` read as a Markdown table's column."""
    return _one_line(value).replace("|", "\\|")


def _code(value: str) -> str:
    """`value` as Markdown code, whatever backticks it holds: fenced by one more
    than its longest run of them, spaced when it starts or ends with one."""
    value = _one_line(value)
    fence = "`" * (max(map(len, re.findall("`+", value)), default=0) + 1)
    padded = f" {value} " if value.startswith("`") or value.endswith("`") else value
    return f"{fence}{padded}{fence}"
