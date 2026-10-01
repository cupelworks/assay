"""Swagger examples for the second-wave statistical tests, computed by the very
functions that compute real results, on fixed plain data — so an example can
never drift from what the API returns. Series are cut to three points: a real
result has one per time."""
import uuid

from assay.models import Comparison, TestStatus, TestTypesModel
from assay.schemas import TestTypeAssignment
from assay.schemas.statistics import StatisticalTestName
from assay.services.statistics import compare, compute
from assay.services.statistics.compute import BatchEntry, BatchRun

_TOXICITY = TestTypesModel(name="Toxicity", engine="llm_judge", comparison=None,
                           config_fields=[])
_ROUGE = TestTypesModel(name="ROUGE", engine="rouge", comparison=Comparison.gte,
                        config_fields=[{"key": "threshold", "min": 0.0, "max": 1.0}])
_CONTAINS = TestTypesModel(name="Contains", engine="contains", comparison=None,
                           config_fields=[])


def _id(n: int) -> uuid.UUID:
    return uuid.UUID(int=0x9B2F7C1E0F4A4D3B8A512C7E5D9F0000 + n)


def _runs(label: str, outcomes: list, offset: int = 0) -> list[BatchRun]:
    """outcomes: a bool (passed) or a score (passed when at least 0.5)."""
    runs = []
    for index, outcome in enumerate(outcomes, start=1):
        score = None if isinstance(outcome, bool) else outcome
        passed = outcome if isinstance(outcome, bool) else outcome >= 0.5
        runs.append(BatchRun(
            id=_id(offset + index), index=index, execution_id=None, status=TestStatus.green,
            results={label: {"passed": passed, "score": score, "detail": None}}, error=None))
    return runs


def _entry(label: str, type_name: str, outcomes: list, entry_id: int | None = None,
           name: str = "Reset a password", offset: int = 0) -> BatchEntry:
    return BatchEntry(
        entry_id=_id(1000 + entry_id) if entry_id is not None else None, test_id=None,
        test_set_id=None, test_set_name="Support answers" if entry_id is not None else None,
        name=name, recorded_answer=True,
        assignments=[TestTypeAssignment(name=type_name, label=label)],
        runs=_runs(label, outcomes, offset))


def _cut(data: dict) -> dict:
    """Shorten every series and strip to three points."""
    for key in ("series", "strip", "pairs"):
        if isinstance(data.get(key), list):
            data[key] = data[key][:3]
    for value in data.values():
        if isinstance(value, dict):
            _cut(value)
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    _cut(item)
    return data


def _dump(model) -> dict:
    return _cut(model.model_dump(mode="json"))


def judge_stability_check() -> dict:
    """A Toxicity judge asked 29 times about one recorded answer, every time
    saying pass: proven to agree with itself at least 90% of the time."""
    entry = _entry("Toxicity", "Toxicity", [True] * 29)
    check = compute.check_result(StatisticalTestName.judge_stability,
                                 {"target": 0.9, "confidence": 0.95}, 29, False,
                                 entry.assignments[0], _TOXICITY, entry.runs, True)
    return _dump(check)


def failures_by_entry() -> dict:
    entries = [
        _entry("Contains", "Contains", [True] * 29, 1, "Opening hours"),
        _entry("Contains", "Contains", [True] * 15 + [False] * 14, 2, "Refund policy", 100),
        _entry("Contains", "Contains", [True] * 28 + [False], 3, "Reset a password", 200),
    ]
    return _dump(compute.failures_by_entry(entries, 0.95))


_SCORES_A = [0.61, 0.58, 0.66, 0.55, 0.63, 0.6, 0.57, 0.64, 0.59, 0.62]
_SCORES_B = [0.7, 0.65, 0.72, 0.61, 0.69, 0.74, 0.66, 0.7]


def score_comparison(name: StatisticalTestName) -> dict:
    """ROUGE under prompt v2 (A) and v3 (B)."""
    check = compare.compare_check(
        name, {"confidence": 0.95}, "ROUGE", "ROUGE", _ROUGE,
        _entry("ROUGE", "ROUGE", _SCORES_A), _entry("ROUGE", "ROUGE", _SCORES_B, offset=100))
    return _dump(check)


def no_worse_comparison() -> dict:
    """27 of 29 against 29 of 29 with a 10-point margin."""
    check = compare.compare_check(
        StatisticalTestName.no_worse, {"confidence": 0.95, "margin": 0.1}, "Contains",
        "Contains", _CONTAINS, _entry("Contains", "Contains", [True] * 29),
        _entry("Contains", "Contains", [True] * 27 + [False] * 2, offset=100))
    return _dump(check)


def paired_comparison() -> dict:
    """Seven entries of a set: B passes more often on every one."""
    entries_a = [_entry("Contains", "Contains", [True] * (20 + i) + [False] * (9 - i), i,
                        f"Entry {i + 1}", 100 * i) for i in range(7)]
    entries_b = [_entry("Contains", "Contains", [True] * 29, i, f"Entry {i + 1}",
                        1000 + 100 * i) for i in range(7)]
    result = compare.compare(entries_a, entries_b, {"confidence": 0.95},
                             StatisticalTestName.paired_entries, {"Contains": _CONTAINS})
    data = result.model_dump(mode="json")
    data["entries"] = data["entries"][:1]
    return _cut(data)
