"""Planning a batch from what the checks have already shown
(docs/statistics/dev_notes.md notes 30–32): each check's chance of an answer
at each size, the batch's chance that every check gets one, the size worth
running, how each check looks against its target, and what would make the
answer more likely or the batch cheaper.

Every chance is averaged over what the check's true rate could be, given its
history (stats_math.gate_answer_chance), and the checks are taken as
independent — checks of one run aren't quite, an approximation the API says
so. The goal is a 90% chance that every check gets an answer; when no size
within one batch reaches it, the default is the smallest size within 5
points of the best chance, and the dialog says so.
"""
import math
import statistics
from dataclasses import dataclass, field
from functools import lru_cache

from assay import stats_math
from assay.schemas.statistics import (
    CheaperKind,
    CheaperOption,
    CheckHistoryOut,
    CheckRef,
    OddsPoint,
    OutcomeSplit,
    Outlook,
    StatisticalEngine,
    Suggestion,
    SuggestionKind,
)
from assay.services.statistics.catalogue import MAX_TIMES, often
from assay.services.statistics.history import CheckHistory

GOAL = 0.9
# when the goal is out of reach: the smallest size within this of the best
WITHIN = 0.05
# an outlook is "likely" when the history makes it at least this likely
LIKELY = 0.9
# the t-test's sizes, between its floor and what a batch can run
_SCORE_SIZES = (10, 15, 20, 25, 30, 40, 50, 60, 75, 100, 125, 150, 200, 250, 300, 400, 500,
                600, 750, 1000)
# confidence levels one step apart, for "less sure"
_CONFIDENCE_STEPS = (0.999, 0.99, 0.95, 0.9, 0.85, 0.8)
_TARGET_STEP = 0.05


@lru_cache(maxsize=65536)
def _gate_chance(n: int, target: float, confidence: float, passed: int,
                 failed: int) -> stats_math.AnswerChance:
    return stats_math.gate_answer_chance(n, target, confidence, passed, failed)


@lru_cache(maxsize=4096)
def _peaks(target: float, confidence: float, limit: int) -> tuple[int, ...]:
    """The sizes where one more failure becomes allowed — where the chance of
    an answer peaks before dipping — up to `limit`: stats_math's
    binomial_runs_allowing for 0, 1, 2… failures, each search going on from
    the previous peak (a size allowing m + 1 failures is beyond the one
    allowing m) rather than from the floor again."""
    alpha = 1 - confidence
    sizes, misses = [], 0
    n = stats_math.binomial_floor(target, confidence)
    while n <= limit:
        while stats_math.binomial_sf(n - misses, n, target) > alpha:
            n += 1
        if n > limit:
            break
        sizes.append(n)
        misses += 1
        n += 1
    return tuple(sizes)


@dataclass
class CheckOdds:
    """One applicable check, as the plan reads it."""
    entry_id: object
    label: str
    engine: StatisticalEngine
    confidence: float
    target: float | None = None
    history: CheckHistory | None = None
    certain: bool = False
    is_judge: bool = False
    # the t-test's frame
    threshold: float | None = None
    higher_is_better: bool = True
    floor: int = 1

    @property
    def ref(self) -> CheckRef:
        return CheckRef(entry_id=self.entry_id, label=self.label)

    @property
    def known(self) -> bool:
        """Whether there's enough to plan from: a certain check always is."""
        if self.certain:
            return True
        if self.history is None or self.history.runs == 0:
            return False
        if self.engine == StatisticalEngine.one_sample_t:
            return len(self.history.scores) >= 2
        return True

    def with_(self, **changes) -> "CheckOdds":
        return CheckOdds(**{**self.__dict__, **changes})

    def chance(self, n: int) -> stats_math.AnswerChance:
        if self.certain:
            if n < self.floor:
                return stats_math.AnswerChance(n=n, passes=0.0, fails=0.0)
            result = self.certain_result
            # a certain check whose result hasn't been seen: answered, either way
            return stats_math.AnswerChance(
                n=n, passes=1.0 if result != "fail" else 0.0, fails=1.0 if result == "fail"
                else 0.0)
        history = self.history
        if self.engine == StatisticalEngine.one_sample_t:
            scores = history.scores
            return stats_math.mean_answer_chance(
                n, statistics.fmean(scores), statistics.stdev(scores), len(scores),
                self.threshold, self.higher_is_better, self.confidence)
        passed, failed = history.passed, history.failed
        if self.engine == StatisticalEngine.judge_stability:
            passed, failed = max(passed, failed), min(passed, failed)
        return _gate_chance(n, self.target, self.confidence, passed, failed)

    @property
    def certain_result(self) -> str | None:
        if not self.certain or self.history is None or self.history.runs == 0:
            return None
        return "pass" if self.history.passed * 2 >= self.history.runs else "fail"

    def sizes(self, limit: int) -> tuple[int, ...]:
        if self.engine == StatisticalEngine.one_sample_t:
            return tuple(n for n in (self.floor, *_SCORE_SIZES) if self.floor <= n <= limit)
        return _peaks(self.target, self.confidence, limit)

    def failures_allowed(self, n: int) -> int | None:
        if self.target is None:
            return None
        rule = stats_math.binomial_gate_rule(n, self.target, self.confidence)
        return None if rule.pass_at_least is None else n - rule.pass_at_least


@dataclass
class Plan:
    """The odds of one set of checks: the curve, the goal, the default."""
    checks: list[CheckOdds]
    limit: int
    sizes: list[int] = field(default_factory=list)
    chances: dict[int, float] = field(default_factory=dict)
    reaches: int | None = None
    best: int | None = None
    worth: int | None = None

    @property
    def default(self) -> int | None:
        return self.reaches or self.worth

    @property
    def best_chance(self) -> float:
        return self.chances[self.best] if self.best is not None else 0.0


def plan(checks: list[CheckOdds], limit: int) -> Plan:
    """The curve over every size worth considering, and its sizes: the first
    that reaches the goal, the best, and the smallest within 5 points of the
    best."""
    found = Plan(checks=checks, limit=limit)
    found.sizes = sorted({n for check in checks for n in check.sizes(limit)})
    for n in found.sizes:
        chance = math.prod(check.chance(n).answer for check in checks)
        found.chances[n] = chance
        if found.reaches is None and chance >= GOAL:
            found.reaches = n
            break
    if found.chances:
        found.best = max(found.chances, key=lambda n: (found.chances[n], -n))
    if found.reaches is None and found.best is not None:
        found.worth = next(n for n in found.sizes
                           if found.chances[n] >= found.best_chance - WITHIN)
    return found


def curve(found: Plan) -> list[OddsPoint]:
    """The chart's points: every size up to the limit (`plan` stops early
    once the goal is reached; the chart doesn't)."""
    return [OddsPoint(times=n, chance=round(math.prod(c.chance(n).answer
                                                      for c in found.checks), 4))
            for n in sorted({n for check in found.checks for n in check.sizes(found.limit)})]


def outcome(checks: list[CheckOdds], n: int) -> tuple[OutcomeSplit | None, CheckRef | None]:
    """How a batch of n would most likely end, and the check most likely to
    stay undecided (none when every check is almost sure to be told). No
    split while a certain check's result is unseen: it will be answered, but
    nobody knows which way."""
    chances = [check.chance(n) for check in checks]
    if any(check.certain and check.certain_result is None for check in checks):
        undecided = max(zip(chances, checks, strict=True), key=lambda pair: pair[0].undecided)
        return None, undecided[1].ref if undecided[0].undecided >= 0.05 else None
    passed = math.prod(c.passes for c in chances)
    failed = 1 - math.prod(1 - c.fails for c in chances)
    undecided = max(zip(chances, checks, strict=True), key=lambda pair: pair[0].undecided)
    likely = undecided[1].ref if undecided[0].undecided >= 0.05 else None
    return OutcomeSplit(passed=round(passed, 4), failed=round(failed, 4),
                        inconclusive=round(max(0.0, 1 - passed - failed), 4)), likely


def suggestions(found: Plan, floor: int) -> list[Suggestion]:
    """The sizes to offer: the floor, then the goal's size — or, when no size
    reaches the goal, the one worth its cost and the best one."""
    picks = [(floor, SuggestionKind.floor)]
    if found.reaches is not None:
        picks.append((found.reaches, SuggestionKind.reaches_goal))
    else:
        picks.append((found.worth, SuggestionKind.worth_its_cost))
        picks.append((found.best, SuggestionKind.best_chance))
    by_times: dict[int, SuggestionKind] = {}
    for times, kind in picks:
        if times is not None and times >= floor:
            by_times[times] = kind  # a later, more telling kind wins a shared size
    default = found.default
    checks = found.checks
    out = []
    for times in sorted(by_times):
        chance = math.prod(c.chance(times).answer for c in checks)
        split, likely = outcome(checks, times)
        allowed = checks[0].failures_allowed(times) if checks and checks[0].target else None
        out.append(Suggestion(
            times=times, kind=by_times[times], label=_label(times, by_times[times], chance),
            default=times == default, chance=round(chance, 4), failures_allowed=allowed,
            outcome=split, likely_undecided=likely))
    if not any(s.default for s in out) and out:
        out[0].default = True
    return out


def _label(times: int, kind: SuggestionKind, chance: float) -> str:
    said = {
        SuggestionKind.floor: "the least that can answer",
        SuggestionKind.reaches_goal: f"{_odds_words(chance)} chance of an answer",
        SuggestionKind.worth_its_cost: f"{_odds_words(chance)} chance, worth its cost",
        SuggestionKind.best_chance: f"{_odds_words(chance)} chance, the best",
    }[kind]
    return f"{times} times · {said}"


def _odds_words(chance: float) -> str:
    return f"{round(chance * 100)}%"


def summary(found: Plan) -> str:
    if found.reaches is not None:
        return (f"{found.reaches} times give every check an answer "
                f"{_odds_words(found.chances[found.reaches])} of the time.")
    best, worth = found.best, found.worth
    if worth is None or worth == best:
        return (f"Even {best} times give every check an answer only "
                f"{_odds_words(found.best_chance)} of the time.")
    fewer = round((1 - worth / best) * 100)
    return (f"Even {best} times give every check an answer only "
            f"{_odds_words(found.best_chance)} of the time; {worth} times give "
            f"{_odds_words(found.chances[worth])} for {fewer}% fewer runs.")


# ── one check ────────────────────────────────────────────────────────────────


def history_out(check: CheckOdds) -> CheckHistoryOut | None:
    history = check.history
    if history is None or history.runs == 0:
        return None
    scores = history.scores
    return CheckHistoryOut(
        runs=history.runs, passed=history.passed, rate=round(history.passed / history.runs, 4),
        mean=round(statistics.fmean(scores), 4) if scores else None,
        sd=round(statistics.stdev(scores), 4) if len(scores) >= 2 else None)


def outlook(check: CheckOdds) -> tuple[Outlook, str]:
    if check.certain:
        result = check.certain_result
        seen = {"pass": " It passes every time.", "fail": " It fails every time."}.get(
            result, "")
        return Outlook.certain, ("It can't vary — a recorded answer read by a fixed check — "
                                 "so one run tells its result." + seen)
    if not check.known:
        return Outlook.unknown, "No runs yet: nothing to plan from."
    history = check.history
    runs = history.runs
    if check.engine == StatisticalEngine.one_sample_t:
        scores = history.scores
        mean, sd = statistics.fmean(scores), statistics.stdev(scores)
        sign = 1 if check.higher_is_better else -1
        gap = sign * (mean - check.threshold)
        likely = (0.5 if gap == 0 else 1.0) if sd == 0 else 1 - stats_math.normal_sf(
            gap / (sd / math.sqrt(len(scores))))
        side = "above" if check.higher_is_better else "below"
        seen = f"Averaged {mean:.3g} over its last {len(scores)} runs"
        if likely >= LIKELY:
            return Outlook.likely_pass, f"{seen}: likely {side} the {check.threshold:g} needed."
        if likely <= 1 - LIKELY:
            return Outlook.likely_fail, (f"{seen}: likely on the wrong side of the "
                                         f"{check.threshold:g} needed.")
        return Outlook.too_close, (f"{seen}: too close to the {check.threshold:g} needed to "
                                   "tell cheaply.")
    goal = often(check.target)
    if check.engine == StatisticalEngine.judge_stability:
        agreeing = max(history.passed, history.failed)
        seen = f"Gave its usual verdict {agreeing} of its last {runs} times"
        likely = stats_math.rate_at_least(check.target, agreeing, runs - agreeing)
        verb = "agree with itself"
    else:
        seen = f"Passed {history.passed} of its last {runs} runs"
        likely = stats_math.rate_at_least(check.target, history.passed, history.failed)
        verb = "pass"
    if likely >= LIKELY:
        return Outlook.likely_pass, f"{seen}: likely to {verb} at least {goal}."
    if likely <= 1 - LIKELY:
        return Outlook.likely_fail, f"{seen}: likely to {verb} less than {goal}."
    return Outlook.too_close, f"{seen}: too close to {goal} to tell cheaply."


def lower_targets(check: CheckOdds, minimum: float) -> list[float]:
    """Up to three lower targets for a target check, 5 points apart."""
    if check.target is None or check.certain:
        return []
    found, step = [], 1
    while len(found) < 3:
        target = round(check.target - step * _TARGET_STEP, 4)
        if target < minimum - 1e-9:
            break
        found.append(target)
        step += 1
    return found


def less_sure(confidence: float, minimum: float) -> float | None:
    lower = [c for c in _CONFIDENCE_STEPS if c < confidence - 1e-9 and c >= minimum - 1e-9]
    return lower[0] if lower else None


def helps(changed: Plan, found: Plan) -> bool:
    """Whether a change is worth offering: it reaches the goal with fewer
    runs, reaches it where nothing did, or raises the best chance."""
    if changed.reaches is not None:
        return found.reaches is None or changed.reaches < found.reaches
    return found.reaches is None and changed.best_chance > found.best_chance + 0.005


def option(kind: CheaperKind, label: str, found: Plan, **fields) -> CheaperOption:
    times = found.default
    chance = found.chances.get(times, 0.0) if times is not None else 0.0
    points = curve(found)
    best = max(points, key=lambda p: (p.chance, -p.times)) if points else None
    return CheaperOption(kind=kind, label=label, times=times or 0, chance=round(chance, 4),
                         reaches_goal=found.reaches is not None,
                         best_chance=best.chance if best else 0.0,
                         best_times=best.times if best else 0, **fields)


def fewest_left_out(checks: list[CheckOdds], limit: int) -> tuple[list[CheckOdds], Plan] | None:
    """The fewest checks to leave out together for the rest to reach the
    goal: the hardest first (lowest chance alone), until it's reached, always
    keeping one. None when even that doesn't reach it."""
    varying = sorted((c for c in checks if not c.certain),
                     key=lambda c: max((p.chance for p in curve(plan([c], limit))), default=0))
    left: list[CheckOdds] = []
    for check in varying[:-1] if len(varying) == len(checks) else varying:
        left.append(check)
        rest = [c for c in checks if c not in left]
        found = plan(rest, limit)
        if found.reaches is not None:
            return left, found
    return None


def option_words(found: Plan) -> str:
    if found.reaches is not None:
        return f"{_odds_words(found.chances[found.reaches])} at {found.reaches} times"
    return f"at most {_odds_words(found.best_chance)}, {found.worth} times for " \
           f"{_odds_words(found.chances[found.worth])}"


def limit_for(runs_per_time: int, max_runs: int) -> int:
    return max(1, min(MAX_TIMES, max_runs // max(runs_per_time, 1)))
