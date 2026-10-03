"""What a batch would need and cost, before anything is created
(docs/statistics/dev_notes.md note 6): the floor, the sizes worth
suggesting, the gate's rule at the chosen size, the calls it will pay for,
which checks get a verdict, and what to know before confirming."""
import uuid

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from assay import stats_math
from assay.judge_settings import resolve_judge_settings
from assay.models import SettingsModel, SettingsSection
from assay.schemas import TestTypeAssignment
from assay.schemas.statistics import (
    CallCount,
    Calls,
    CheaperKind,
    CheckPlan,
    EntryPlan,
    Estimate,
    EstimateRequest,
    GateRuleSchema,
    StatisticalEngine,
    StatisticalTestKind,
    TrialOffer,
    Warning_,
)
from assay.services.statistics import compute, odds
from assay.services.statistics._scope import ResolvedScope, resolve_scope
from assay.services.statistics.catalogue import (
    MAX_RUNS,
    CatalogueEntry,
    _invalid,
    check_times,
    find_trial,
    load_entry,
    often,
    percent,
    resolve_parameters,
    sizing,
)
from assay.services.statistics.history import CheckHistory, load_history
from assay.target_settings import resolve_target_settings


def applies(engine: StatisticalEngine, scope: ResolvedScope, entry: compute.BatchEntry,
            assignment: TestTypeAssignment) -> tuple[bool, str | None]:
    """Whether a batch test gives this check a verdict, and why not: the
    result's own rule (compute.applies), so the estimate never promises a
    verdict the result won't give."""
    return compute.applies(engine, scope.types.get(assignment.name), entry.recorded_answer)


# a check of the scope: its entry (None for a standalone test) and its label
CheckKey = tuple[uuid.UUID | None, str]

_NOTHING_APPLIES = {
    StatisticalEngine.one_sample_t:
        "None of these checks gives a score: an average needs ROUGE, BLEU, METEOR, BERTScore "
        "or Cosine Similarity",
    StatisticalEngine.judge_stability:
        "None of these checks is an LLM judge on a recorded answer: this test needs one "
        "(Correctness, Relevance, Bias, Toxicity or Hallucination, on a test with a recorded "
        "answer)",
}


async def estimate_batch(request: EstimateRequest, session: AsyncSession) -> Estimate:
    """The estimate for a scope and a batch test.

    Raises:
        HTTPException: 404 for an unknown test, set or plan; 409 for an empty
            set or plan, or an entry with no checks.
        RequestValidationError: 422 for an unknown statistical test or a
            comparison test, parameters out of range, times below the floor or
            above the limits, or a scope none of whose checks this test
            applies to.
    """
    chosen = await load_entry(request.statistical_test, StatisticalTestKind.batch, session)
    parameters = resolve_parameters(chosen, request.parameters)
    scope = await resolve_scope(request, session)
    targets, leave_out = resolve_overrides(request, chosen, scope)
    return await build_estimate(chosen, parameters, request.times, scope, session,
                                targets=targets, leave_out=leave_out)


def resolve_overrides(request: EstimateRequest, chosen: CatalogueEntry, scope: ResolvedScope
                      ) -> tuple[dict[CheckKey, float], set[CheckKey]]:
    """The request's per-check targets and left-out checks, each checked
    against the scope: every problem reported together as a 422, in
    FastAPI's list shape."""
    checks = {(entry.entry_id, a.label): entry for entry in scope.entries
              for a in entry.assignments}
    target = next((p for p in chosen.parameters if p.key == "target"), None)
    problems: list[tuple[tuple, str]] = []
    targets: dict[CheckKey, float] = {}
    for index, item in enumerate(request.targets):
        key = (item.entry_id, item.label)
        if target is None:
            problems.append((("targets", index),
                             f"{chosen.name} has no target to set per check"))
        elif key not in checks:
            problems.append((("targets", index, "label"), _no_such_check(item, scope)))
        elif key in targets:
            problems.append((("targets", index), f"'{item.label}' has its target set twice"))
        elif not target.min <= item.target <= target.max:
            problems.append((("targets", index, "target"),
                             f"Must be between {target.min:g} and {target.max:g}"))
        else:
            targets[key] = item.target
    leave_out: set[CheckKey] = set()
    for index, item in enumerate(request.leave_out):
        key = (item.entry_id, item.label)
        if key not in checks:
            problems.append((("leave_out", index, "label"), _no_such_check(item, scope)))
        else:
            leave_out.add(key)
    for entry in scope.entries:
        if entry.assignments and all((entry.entry_id, a.label) in leave_out
                                     for a in entry.assignments):
            problems.append((("leave_out",),
                             f"Every check of '{entry.name}' is left out: keep at least one"))
    if problems:
        raise _invalid(problems)
    return targets, leave_out


def _no_such_check(item, scope: ResolvedScope) -> str:
    if item.entry_id is not None and not any(e.entry_id == item.entry_id
                                             for e in scope.entries):
        return f"No entry {item.entry_id} in this scope"
    return f"No check '{item.label}' in this scope"


async def build_estimate(chosen: CatalogueEntry, parameters: dict[str, float],
                         times: int | None, scope: ResolvedScope, session: AsyncSession,
                         targets: dict[CheckKey, float] | None = None,
                         leave_out: set[CheckKey] | None = None) -> Estimate:
    """The estimate. `targets` sets a check's own target, `leave_out` drops
    checks from the batch — both keyed by (entry id, label), the entry id
    None for a standalone test."""
    targets, leave_out = targets or {}, leave_out or set()
    engine = chosen.engine.id
    size = sizing(chosen, parameters)
    confidence = parameters.get("confidence", 0.95)
    batch_target = parameters.get("target")
    histories = await load_history(scope.entries, session)
    limit = odds.limit_for(scope.runs_per_time, MAX_RUNS)

    entries, total, applicable, checks = [], 0, 0, {}
    for entry in scope.entries:
        plans = []
        for assignment in entry.assignments:
            key = (entry.entry_id, assignment.label)
            does_apply, reason = applies(engine, scope, entry, assignment)
            left_out = key in leave_out
            if left_out:
                does_apply, reason = False, "Left out of this batch"
            target = targets.get(key, batch_target) if batch_target is not None else None
            plans.append(CheckPlan(label=assignment.label, test_type=assignment.name,
                                   applies=does_apply, reason=reason, target=target,
                                   left_out=left_out))
            total += 1
            applicable += does_apply
            if does_apply:
                checks[key] = _odds_of(engine, scope, entry, assignment, target, confidence,
                                       size.floor, histories.get(key))
        entries.append(EntryPlan(
            entry_id=entry.entry_id, test_id=entry.test_id, test_set_id=entry.test_set_id,
            test_set_name=entry.test_set_name, name=entry.name,
            recorded_answer=entry.recorded_answer, checks=plans,
        ))
    if applicable == 0 and engine != StatisticalEngine.trial:
        raise _invalid([(("statistical_test",), _NOTHING_APPLIES.get(
            engine, "Every check is left out: keep at least one"))])

    floor = size.floor
    if batch_target is not None:
        # the targets in play decide: a check's own target has its own floor
        floor = max(stats_math.binomial_floor(check.target, confidence)
                    for check in checks.values())
    found = (odds.plan(list(checks.values()), limit)
             if all(check.known for check in checks.values()) else None)
    capped_from = None
    if times is None:
        times = found.default if found is not None and found.default else size.default
        most = MAX_RUNS // scope.runs_per_time
        if times > most:
            # a default the scope can't run: the most it can, if that still
            # reaches the floor (said in a warning), else the scope is too big
            if most < floor:
                field = {"test": "test_id", "test_set": "test_set_id",
                         "test_plan": "test_plan_id"}[scope.scope.kind.value]
                raise _invalid([((field,),
                                 f"This scope runs {scope.runs_per_time} entries each time: "
                                 f"the {floor} times this test needs would be "
                                 f"{floor * scope.runs_per_time} runs, more than the "
                                 f"{MAX_RUNS} a batch can create. Run it on a smaller set")])
            capped_from, times = times, most
    check_times(times, scope.runs_per_time, floor, chosen, parameters)

    application_per_time = sum(not entry.recorded_answer for entry in scope.entries)
    judge_per_time = sum(scope.is_judge(a) for entry in scope.entries for a in entry.assignments
                         if (entry.entry_id, a.label) not in leave_out)
    rule = None
    if engine in (StatisticalEngine.binomial_gate, StatisticalEngine.judge_stability):
        gate_rule = stats_math.binomial_gate_rule(times, batch_target, confidence)
        rule = GateRuleSchema(times=times, pass_at_least=gate_rule.pass_at_least,
                              fail_at_most=gate_rule.fail_at_most)

    estimate = Estimate(
        scope=scope.scope, statistical_test=chosen.id, engine=engine, parameters=parameters,
        floor=floor, floor_explanation=size.explanation, suggestions=size.suggestions,
        times=times, rule=rule, runs_per_time=scope.runs_per_time,
        runs_total=times * scope.runs_per_time,
        calls=Calls(
            application=CallCount(per_time=application_per_time,
                                  total=application_per_time * times),
            judge=CallCount(per_time=judge_per_time, total=judge_per_time * times),
        ),
        checks_total=total, checks_applicable=applicable, entries=entries,
        warnings=await _warnings(
            scope, 0 if engine == StatisticalEngine.trial else total - applicable - len(leave_out),
            session, times, capped_from),
        goal=odds.GOAL,
    )
    _with_odds(estimate, chosen, checks, found, limit, floor)
    if found is None and engine != StatisticalEngine.trial:
        estimate.trial = await _trial_offer(checks, application_per_time, judge_per_time,
                                            session)
    return estimate


async def _trial_offer(checks: dict[CheckKey, odds.CheckOdds], application_per_time: int,
                       judge_per_time: int, session: AsyncSession) -> TrialOffer | None:
    """A trial to run first, when some checks have never run: what it costs
    and why. None when the catalogue has no trial."""
    trial = await find_trial(session)
    if trial is None:
        return None
    times = trial.settings["default_times"]
    unknown = sum(not check.known for check in checks.values())
    which = "1 check has" if unknown == 1 else f"{unknown} checks have"
    return TrialOffer(
        statistical_test=trial.id, times=times,
        application_calls=application_per_time * times, judge_calls=judge_per_time * times,
        reason=f"{which} never run: a trial of {times} times shows how they behave, so the "
               "batch can be planned from it.")


def _odds_of(engine: StatisticalEngine, scope: ResolvedScope, entry: compute.BatchEntry,
             assignment: TestTypeAssignment, target: float | None, confidence: float,
             floor: int, history: CheckHistory | None) -> odds.CheckOdds:
    """A check as the plan reads it. A recorded answer read by anything but a
    judge gives the same result every run: certain."""
    row = scope.types.get(assignment.name)
    is_judge = scope.is_judge(assignment)
    check = odds.CheckOdds(
        entry_id=entry.entry_id, label=assignment.label, engine=engine,
        confidence=confidence, target=target, history=history, is_judge=is_judge,
        certain=entry.recorded_answer and not is_judge
        and engine != StatisticalEngine.judge_stability, floor=floor)
    if target is not None:
        check.floor = stats_math.binomial_floor(target, confidence)
    if engine == StatisticalEngine.one_sample_t:
        check.threshold = compute.threshold_of(assignment)
        check.higher_is_better = row is None or row.comparison is None or (
            row.comparison.value == "gte")
    return check


def _with_odds(estimate: Estimate, chosen: CatalogueEntry,
               checks: dict[CheckKey, odds.CheckOdds], found: odds.Plan | None, limit: int,
               floor: int) -> None:
    """Fill in what the history tells: per check its history, outlook and
    odds alone; for the batch the curve, the sizes worth offering, and what
    would make it cheaper. Without a history for every check, only the
    per-check parts that exist."""
    for entry in estimate.entries:
        for plan in entry.checks:
            check = checks.get((entry.entry_id, plan.label))
            if check is None:
                continue
            plan.history = odds.history_out(check)
            plan.outlook, plan.outlook_reason = odds.outlook(check)
            plan.certain_result = check.certain_result
            if check.known:
                alone = odds.plan([check], limit)
                plan.size_needed = alone.reaches
                points = odds.curve(alone)
                plan.best_chance = max((p.chance for p in points), default=None)
    if found is None or not found.sizes:
        return

    estimate.goal_reachable = found.reaches is not None
    points = odds.curve(found)
    best = max(points, key=lambda p: (p.chance, -p.times))
    estimate.best_chance, estimate.best_times = best.chance, best.times
    estimate.odds = points
    estimate.odds_summary = odds.summary(found)
    estimate.suggestions = odds.suggestions(found, floor)
    at = found.default
    driving = min(checks.values(), key=lambda c: c.chance(at).answer) if at else None
    if driving is not None and driving.chance(at).answer >= 0.999:
        driving = None  # every check is as good as answered: nothing drives the size
    estimate.driving_check = driving.ref if driving else None

    minimum = {p.key: p.min for p in chosen.parameters}
    everyone = list(checks.values())
    if all(check.certain for check in everyone):
        return  # nothing can vary: run it once, nothing is cheaper
    for entry in estimate.entries:
        for plan in entry.checks:
            check = checks.get((entry.entry_id, plan.label))
            if check is None or check.certain:
                continue
            worth_it = check is driving or plan.outlook in (odds.Outlook.too_close,
                                                            odds.Outlook.likely_fail)
            if not worth_it:
                continue
            others = [c for c in everyone if c is not check]
            for target in odds.lower_targets(check, minimum.get("target", 0.0)):
                lowered = check.with_(target=target, floor=stats_math.binomial_floor(
                    target, check.confidence))
                changed = odds.plan(others + [lowered], limit)
                if not odds.helps(changed, found):
                    continue
                plan.cheaper.append(odds.option(
                    CheaperKind.lower_target,
                    f"{plan.label} at \"at least {often(target)}\": "
                    f"{odds.option_words(changed)}", changed, check=check.ref, target=target))
            without = odds.plan(others, limit) if others else None
            if without is not None and odds.helps(without, found):
                plan.cheaper.append(odds.option(
                    CheaperKind.leave_out, f"Leave {plan.label} out: "
                    f"{odds.option_words(without)}", without, check=check.ref,
                    judge_calls_saved_per_time=int(check.is_judge)))
    single = [o for e in estimate.entries for c in e.checks for o in c.cheaper]
    if not any(o.reaches_goal for o in single) and found.reaches is None:
        combination = odds.fewest_left_out(everyone, limit)
        if combination is not None and len(combination[0]) > 1:
            left, without = combination
            names = " and ".join(c.label for c in left)
            estimate.cheaper.append(odds.option(
                CheaperKind.leave_out, f"Leave {names} out: {odds.option_words(without)}",
                without, checks=[c.ref for c in left],
                judge_calls_saved_per_time=sum(c.is_judge for c in left)))
    lower = odds.less_sure(everyone[0].confidence, minimum.get("confidence", 0.0))
    if lower is not None:
        relaxed = [c.with_(confidence=lower, floor=stats_math.binomial_floor(c.target, lower)
                           if c.target is not None else c.floor) for c in everyone]
        changed = odds.plan(relaxed, limit)
        if odds.helps(changed, found):
            estimate.cheaper.append(odds.option(
                CheaperKind.less_sure, f"{percent(lower)} sure: {odds.option_words(changed)}",
                changed, confidence=lower))


async def _warnings(scope: ResolvedScope, not_applicable: int, session: AsyncSession,
                    times: int, capped_from: int | None) -> list[Warning_]:
    warnings = []
    if capped_from is not None:
        warnings.append(Warning_(
            code="times_capped",
            message=f"The suggested {capped_from} times would create "
                    f"{capped_from * scope.runs_per_time} runs, more than the {MAX_RUNS} a "
                    f"batch can create: this estimate is for {times} times, the most for "
                    "this scope.",
        ))
    recorded = [e for e in scope.entries if e.recorded_answer]
    if recorded:
        static = [e for e in recorded if not any(scope.is_judge(a) for a in e.assignments)]
        if len(scope.entries) == 1:
            subject = "The test has" if scope.entries[0].entry_id is None else "The entry has"
            message = f"{subject} a recorded answer: only its judge checks can vary between runs."
        else:
            have = "entry has" if len(recorded) == 1 else "entries have"
            its = "its" if len(recorded) == 1 else "their"
            message = (f"{len(recorded)} of {len(scope.entries)} {have} a recorded answer: "
                       f"only {its} judge checks can vary between runs.")
        warnings.append(Warning_(code="recorded_answers", message=message))
        if static:
            one = len(static) == 1
            warnings.append(Warning_(
                code="nothing_can_vary",
                message=f"{len(static)} recorded {'entry has' if one else 'entries have'} no "
                        f"judge check: every run of {'it' if one else 'them'} gives the same "
                        "result, so running it many times only repeats that one result.",
            ))
    if any(scope.is_judge(a) for e in scope.entries for a in e.assignments):
        try:
            judge = resolve_judge_settings(await session.get(SettingsModel,
                                                             SettingsSection.judge))
            if judge.provider is None:
                warnings.append(Warning_(
                    code="no_judge_configured",
                    message="Judge checks are assigned but no judge model is chosen: they'll "
                            "fail as errors in every run and get no verdict. Choose one "
                            "under Settings first.",
                ))
        except ValidationError:
            warnings.append(Warning_(
                code="judge_settings_invalid",
                message="The saved judge settings no longer validate: judge checks will "
                        "fail as errors until they're fixed under Settings.",
            ))
    if len(recorded) < len(scope.entries):
        try:
            target = resolve_target_settings(await session.get(SettingsModel,
                                                               SettingsSection.target))
            if target.url is None:
                warnings.append(Warning_(
                    code="no_application_configured",
                    message="Some entries have no recorded answer and no application URL "
                            "is set: their runs will be Not Ran. Set it under Settings "
                            "first.",
                ))
        except ValidationError:
            warnings.append(Warning_(
                code="target_settings_invalid",
                message="The saved application settings no longer validate: runs that "
                        "need the application will be Not Ran until they're fixed.",
            ))
    if not_applicable:
        warnings.append(Warning_(
            code="checks_not_applicable",
            message=f"{not_applicable} {'check' if not_applicable == 1 else 'checks'} "
                    f"{'isn' if not_applicable == 1 else 'aren'}'t something this test can "
                    "judge: you'll see how often "
                    f"{'it' if not_applicable == 1 else 'they'} passed, without an answer.",
        ))
    return warnings
