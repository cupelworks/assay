"""What a batch would need and cost, before anything is created
(docs/statistics/dev_notes.md note 6): the floor, the sizes worth
suggesting, the gate's rule at the chosen size, the calls it will pay for,
which checks get a verdict, and what to know before confirming."""
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from assay import stats_math
from assay.judge_settings import resolve_judge_settings
from assay.models import SettingsModel, SettingsSection
from assay.schemas import TestTypeAssignment
from assay.schemas.statistics import (
    CallCount,
    Calls,
    CheckPlan,
    EntryPlan,
    Estimate,
    EstimateRequest,
    GateRuleSchema,
    StatisticalTestKind,
    StatisticalTestName,
    Warning_,
)
from assay.services.statistics import compute
from assay.services.statistics._scope import ResolvedScope, ScopeEntry, resolve_scope
from assay.services.statistics.catalogue import (
    _invalid,
    check_times,
    resolve_parameters,
    sizing,
)
from assay.target_settings import resolve_target_settings


def applies(name: StatisticalTestName, scope: ResolvedScope, entry: ScopeEntry,
            assignment: TestTypeAssignment) -> tuple[bool, str | None]:
    """Whether a batch test gives this check a verdict, and why not: the
    result's own rule (compute.applies), so the estimate never promises a
    verdict the result won't give."""
    return compute.applies(name, scope.types.get(assignment.name), entry.recorded_answer)


_NOTHING_APPLIES = {
    StatisticalTestName.one_sample_t:
        "None of this scope's checks is scored on a scale: a t-test needs ROUGE, BLEU, "
        "METEOR, BERTScore or Cosine Similarity",
    StatisticalTestName.judge_stability:
        "None of this scope's checks is an LLM judge on a recorded answer: judge stability "
        "needs one (Correctness, Relevance, Bias, Toxicity or Hallucination, on an entry "
        "with a recorded answer)",
}


async def estimate_batch(request: EstimateRequest, session: AsyncSession) -> Estimate:
    """The estimate for a scope and a batch test.

    Raises:
        HTTPException: 404 for an unknown test, set or plan; 409 for an empty
            set or plan, or an entry with no checks.
        RequestValidationError: 422 for a comparison test, parameters out of
            range, times below the floor or above the limits, or a scope none
            of whose checks this test applies to.
    """
    parameters = resolve_parameters(request.statistical_test, request.parameters,
                                    StatisticalTestKind.batch)
    scope = await resolve_scope(request, session)
    return await build_estimate(request.statistical_test, parameters, request.times,
                                scope, session)


async def build_estimate(name: StatisticalTestName, parameters: dict[str, float],
                         times: int | None, scope: ResolvedScope,
                         session: AsyncSession) -> Estimate:
    size = sizing(name, parameters)
    times = times or size.default

    entries, total, applicable = [], 0, 0
    for entry in scope.entries:
        checks = []
        for assignment in entry.assignments:
            does_apply, reason = applies(name, scope, entry, assignment)
            checks.append(CheckPlan(label=assignment.label, test_type=assignment.name,
                                    applies=does_apply, reason=reason))
            total += 1
            applicable += does_apply
        entries.append(EntryPlan(
            entry_id=entry.entry_id, test_id=entry.test_id, test_set_id=entry.test_set_id,
            test_set_name=entry.test_set_name, name=entry.name,
            recorded_answer=entry.recorded_answer, checks=checks,
        ))
    if applicable == 0:
        raise _invalid([(("statistical_test",), _NOTHING_APPLIES[name])])
    check_times(times, scope.runs_per_time, size.floor, name, parameters)

    application_per_time = sum(not entry.recorded_answer for entry in scope.entries)
    judge_per_time = sum(scope.is_judge(a) for entry in scope.entries for a in entry.assignments)
    rule = None
    if name == StatisticalTestName.binomial_gate:
        gate_rule = stats_math.binomial_gate_rule(
            times, parameters["target"], parameters["confidence"])
        rule = GateRuleSchema(times=times, pass_at_least=gate_rule.pass_at_least,
                              fail_at_most=gate_rule.fail_at_most)

    return Estimate(
        scope=scope.scope, statistical_test=name, parameters=parameters,
        floor=size.floor, floor_explanation=size.explanation, suggestions=size.suggestions,
        times=times, rule=rule, runs_per_time=scope.runs_per_time,
        runs_total=times * scope.runs_per_time,
        calls=Calls(
            application=CallCount(per_time=application_per_time,
                                  total=application_per_time * times),
            judge=CallCount(per_time=judge_per_time, total=judge_per_time * times),
        ),
        checks_total=total, checks_applicable=applicable, entries=entries,
        warnings=await _warnings(scope, total - applicable, session),
    )


async def _warnings(scope: ResolvedScope, not_applicable: int,
                    session: AsyncSession) -> list[Warning_]:
    warnings = []
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
                        f"result, so {'its' if one else 'their'} statistics only repeat one "
                        "run's outcome.",
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
            message=f"{not_applicable} {'check is' if not_applicable == 1 else 'checks are'} "
                    "not covered by this statistical test: "
                    f"{'its' if not_applicable == 1 else 'their'} pass rate is shown "
                    "without a verdict.",
        ))
    return warnings
