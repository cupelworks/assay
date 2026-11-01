# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Francesco Campanile
"""The outcome written for people (text, a GitHub job summary in Markdown) and
for tools (JUnit XML, which GitLab, Azure Pipelines, Jenkins and most CI
systems show as test results)."""
from xml.etree import ElementTree as ET

from assay_cli.client import SCOPE_NOUNS
from assay_cli.gate import Outcome

_MARK = {"passed": "✓", "failed": "✗", "error": "!", "skipped": "–"}
MAX_LISTED = 50
"""Failures listed in the text summary; the JUnit report has every one."""


def text(outcome: Outcome) -> str:
    lines = [outcome.summary, f"  {outcome.kind} id: {outcome.id}"]
    failures = outcome.failures
    for case in failures[:MAX_LISTED]:
        lines.append(f"  {_MARK[case.outcome]} {_label(outcome, case)}: {case.message}")
    if len(failures) > MAX_LISTED:
        lines.append(f"  … and {len(failures) - MAX_LISTED} more (see the JUnit report)")
    if not outcome.passed and outcome.fail_on and not outcome.reason:
        lines.append(f"  fails on: {', '.join(outcome.fail_on)}")
    return "\n".join(lines)


def markdown(outcome: Outcome) -> str:
    icon = "✅" if outcome.passed else "❌"
    lines = [f"### {icon} Assay: {SCOPE_NOUNS[outcome.scope['kind']]} "
             f"`{outcome.scope['name']}`", "",
             _escape(outcome.summary), "",
             f"{outcome.kind.capitalize()} `{outcome.id}`"]
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
        suite = ET.SubElement(suites, "testsuite", name=group, tests=str(len(cases)),
                              failures=str(sum(c.outcome == "failed" for c in cases)),
                              errors=str(sum(c.outcome == "error" for c in cases)),
                              skipped=str(sum(c.outcome == "skipped" for c in cases)))
        ET.SubElement(ET.SubElement(suite, "properties"), "property",
                      name=f"assay.{outcome.kind}_id", value=outcome.id)
        for case in cases:
            element = ET.SubElement(suite, "testcase", name=case.name, classname=group)
            if case.outcome in ("failed", "error", "skipped"):
                tag = {"failed": "failure", "error": "error", "skipped": "skipped"}[case.outcome]
                ET.SubElement(element, tag, message=case.message).text = case.detail or None
            elif case.message:
                ET.SubElement(element, "system-out").text = case.message
    ET.indent(suites)
    return ET.tostring(suites, encoding="utf-8", xml_declaration=True)


def _label(outcome: Outcome, case) -> str:
    """A batch's check is named within its entry; a run by its test's name."""
    if outcome.kind != "batch" or case.group in ("", case.name):
        return case.name
    return f"{case.group} › {case.name}"


def _escape(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")
