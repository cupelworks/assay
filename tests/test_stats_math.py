"""The statistics maths against published values. Every reference value
below was produced by scipy 1.x / statsmodels (exact binomial, Clopper–Pearson,
Wilson, Student's t, chi-square, Fisher, Newcombe) or is quoted in
docs/statistics/dev_notes.md; neither library is a dependency of Assay."""
import ast
from pathlib import Path

import pytest

from assay import stats_math as m

APPROX = {"abs": 1e-9}


# --- the binomial distribution and its bounds ---


@pytest.mark.parametrize("k,n,p,sf,cdf", [
    (29, 29, 0.9, 0.04710128697246249, 1.0),
    (27, 29, 0.9, 0.43496003278274, 0.8011278994496028),
    (5, 20, 0.3, 0.7624922211223987, 0.4163708294474809),
    (0, 10, 0.4, 1.0, 0.0060466176),
    (500, 1000, 0.5, 0.5126125090891802, 0.5126125090891802),
])
def test_binomial_tails(k, n, p, sf, cdf):
    assert m.binomial_sf(k, n, p) == pytest.approx(sf, abs=1e-12)
    assert m.binomial_cdf(k, n, p) == pytest.approx(cdf, abs=1e-12)


@pytest.mark.parametrize("passes,n,lower,upper", [
    (18, 20, 0.7173814751141391, 0.9819347969145814),
    (20, 20, 0.8608916593317348, 1.0),
    (0, 15, 0.0, 0.18103627252208468),
    (27, 29, 0.7984394120784961, 0.9876064686738686),
    (3, 7, 0.1287563928042427, 0.7746784159675523),
])
def test_exact_one_sided_bounds_are_clopper_pearson(passes, n, lower, upper):
    assert m.exact_lower_bound(passes, n, 0.95) == pytest.approx(lower, **APPROX)
    assert m.exact_upper_bound(passes, n, 0.95) == pytest.approx(upper, **APPROX)


def test_29_of_29_proves_at_least_90_percent():
    assert m.exact_lower_bound(29, 29, 0.95) == pytest.approx(0.05 ** (1 / 29))
    assert m.exact_lower_bound(29, 29, 0.95) > 0.9 > m.exact_lower_bound(28, 28, 0.95)


@pytest.mark.parametrize("passes,n,lower,upper", [
    (18, 20, 0.6989663547715127, 0.9721335187862318),
    (20, 20, 0.8388748419471804, 1.0),
    (0, 15, 0.0, 0.20388330103584867),
    (3, 7, 0.15821985525146964, 0.7495416354723428),
])
def test_wilson_interval(passes, n, lower, upper):
    assert m.wilson_interval(passes, n, 0.95) == pytest.approx((lower, upper), abs=1e-12)


@pytest.mark.parametrize("passes,n", [(0, 0), (-1, 5), (6, 5)])
def test_a_rate_needs_sensible_counts(passes, n):
    with pytest.raises(ValueError):
        m.wilson_interval(passes, n, 0.95)


# --- the binomial gate ---


@pytest.mark.parametrize("target,floor", [(0.8, 14), (0.9, 29), (0.95, 59), (0.99, 299)])
def test_the_gates_floor_is_ln_alpha_over_ln_target(target, floor):
    assert m.binomial_floor(target, 0.95) == floor


def test_the_floor_is_exact_at_a_boundary():
    # 0.5 ** 2 == 0.25 exactly: two runs suffice at 75% confidence
    assert m.binomial_floor(0.5, 0.75) == 2


def test_46_runs_allow_one_miss_at_90_percent():
    assert m.binomial_runs_allowing(0, 0.9, 0.95) == 29
    assert m.binomial_runs_allowing(1, 0.9, 0.95) == 46


@pytest.mark.parametrize("n,pass_at_least,fail_at_most", [
    (29, 29, 22), (46, 45, 37), (20, None, 15), (1, None, None),
])
def test_the_gates_rule_at_n(n, pass_at_least, fail_at_most):
    rule = m.binomial_gate_rule(n, 0.9, 0.95)
    assert (rule.pass_at_least, rule.fail_at_most) == (pass_at_least, fail_at_most)


@pytest.mark.parametrize("passes,n,verdict", [
    (29, 29, m.Verdict.passed),
    (28, 29, m.Verdict.inconclusive),
    (23, 29, m.Verdict.inconclusive),
    (22, 29, m.Verdict.failed),
    (18, 20, m.Verdict.inconclusive),
    (20, 20, m.Verdict.inconclusive),  # below the floor nothing passes
    (0, 3, m.Verdict.failed),
])
def test_the_gates_three_way_verdict(passes, n, verdict):
    assert m.binomial_gate(passes, n, 0.9, 0.95).verdict == verdict


def test_the_gates_verdict_and_its_bounds_agree():
    for n in (20, 29, 46, 60):
        for passes in range(n + 1):
            gate = m.binomial_gate(passes, n, 0.9, 0.95)
            assert (gate.verdict == m.Verdict.passed) == (gate.lower >= 0.9 - 1e-12)
            assert (gate.verdict == m.Verdict.failed) == (gate.upper <= 0.9 + 1e-12)


@pytest.mark.parametrize("passes,n,runs", [(27, 29, 239), (28, 29, 61), (28, 28, 29)])
def test_more_runs_that_would_likely_decide_it(passes, n, runs):
    assert m.binomial_gate_runs_to_decide(passes, n, 0.9, 0.95) == runs


def test_more_runs_toward_a_failure_and_never_at_the_target():
    assert m.binomial_gate_runs_to_decide(24, 29, 0.9, 0.95) is not None
    assert m.binomial_gate_runs_to_decide(9, 10, 0.9, 0.95) is None
    assert m.binomial_gate_runs_to_decide(0, 0, 0.9, 0.95) is None


@pytest.mark.parametrize("target", [0.0, 1.0, 1.2, -0.1])
def test_a_target_must_be_strictly_between_0_and_1(target):
    with pytest.raises(ValueError):
        m.binomial_floor(target, 0.95)


# --- the t-distribution ---


@pytest.mark.parametrize("t,df,cdf", [
    (2.093, 19, 0.9749988105285861),
    (12.706, 1, 0.9749995988209335),
    (1.962, 1000, 0.9749802378137714),
    (-0.5, 3, 0.3257239824240755),
    (0.0, 7, 0.5),
    (4.0, 2.5, 0.9804935120793409),
])
def test_t_cdf(t, df, cdf):
    assert m.t_cdf(t, df) == pytest.approx(cdf, abs=1e-10)
    assert m.t_sf(t, df) == pytest.approx(1 - cdf, abs=1e-10)


@pytest.mark.parametrize("probability,df,t", [
    (0.975, 19, 2.0930240544083087),
    (0.975, 1, 12.706204736174694),
    (0.975, 1000, 1.9623390808264083),
    (0.95, 9, 1.833112932656237),
    (0.999, 3, 10.214531852407383),
    (0.05, 5, -2.015048373333024),
])
def test_t_quantile(probability, df, t):
    assert m.t_quantile(probability, df) == pytest.approx(t, abs=1e-8)


# --- scores ---


def test_quantiles_interpolate_linearly_like_numpy():
    values = [0.2, 0.4, 0.5, 0.9]
    assert m.quantile(values, 0.5) == pytest.approx(0.45)
    assert m.quantile(values, 0.1) == pytest.approx(0.26)
    assert m.quantile([3.0], 0.9) == 3.0


def test_a_score_summary_has_everything_a_box_plot_needs():
    summary = m.summarize_scores([0.5, 0.9, 0.2, 0.4])
    assert (summary.n, summary.minimum, summary.maximum) == (4, 0.2, 0.9)
    assert summary.mean == pytest.approx(0.5)
    assert summary.sd == pytest.approx(0.2943920288775949)
    assert summary.median == pytest.approx(0.45)
    assert m.summarize_scores([0.7]).sd is None


SCORES = [0.61, 0.72, 0.55, 0.8, 0.67, 0.7, 0.64, 0.59, 0.77, 0.69]


def test_one_sample_t_matches_scipy():
    result = m.one_sample_t(SCORES, 0.6, higher_is_better=True, confidence=0.95)
    assert result.t == pytest.approx(2.970846888025653, abs=1e-10)
    assert result.p_value_pass == pytest.approx(0.007840424318587036, abs=1e-10)
    assert (result.lower, result.upper) == pytest.approx(
        (0.6283394990959255, 0.7196605009040741), abs=1e-10)
    assert result.verdict == m.Verdict.passed


def test_one_sample_t_in_each_direction():
    assert m.one_sample_t(SCORES, 0.75, True, 0.95).verdict == m.Verdict.failed
    assert m.one_sample_t(SCORES, 0.68, True, 0.95).verdict == m.Verdict.inconclusive
    # lower is better: a mean well under the threshold passes
    assert m.one_sample_t(SCORES, 0.75, False, 0.95).verdict == m.Verdict.passed
    assert m.one_sample_t(SCORES, 0.6, False, 0.95).verdict == m.Verdict.failed


def test_one_sample_t_without_spread_is_the_threshold_comparison():
    same = [0.7] * 5
    assert m.one_sample_t(same, 0.7, True, 0.95).verdict == m.Verdict.passed
    assert m.one_sample_t(same, 0.71, True, 0.95).verdict == m.Verdict.failed
    result = m.one_sample_t(same, 0.6, True, 0.95)
    assert (result.lower, result.upper, result.sd) == (0.7, 0.7, 0.0)


def test_one_score_proves_nothing():
    result = m.one_sample_t([0.9], 0.5, True, 0.95)
    assert (result.verdict, result.lower, result.sd) == (m.Verdict.inconclusive, None, None)


def test_runs_needed_for_a_mean_matches_gpower():
    # effect size 0.5, one-sided 5%, power 80%: G*Power gives 27
    assert m.runs_needed_for_mean(0.1, 0.05, 0.95) == 27
    assert m.one_sample_t_runs_to_decide(0.65, 0.1, 0.6, 0.95) == 27
    assert m.one_sample_t_runs_to_decide(0.6, 0.1, 0.6, 0.95) is None


# --- two pass rates ---


@pytest.mark.parametrize("table,chi,p", [
    ((10, 2, 3, 15), 13.031674208144796, 0.00030626663018354963),
    ((8, 2, 1, 5), 6.112169312169312, 0.013425424881491248),
    ((45, 5, 38, 12), 3.472714386959603, 0.06238885487025272),
    ((29, 0, 25, 4), 4.296296296296296, 0.038195468008596384),
])
def test_chi_square_2x2(table, chi, p):
    statistic, p_value, _ = m.chi_square_2x2(*table)
    assert (statistic, p_value) == pytest.approx((chi, p), abs=1e-10)


def test_chi_square_with_an_empty_margin_has_nothing_to_test():
    assert m.chi_square_2x2(0, 5, 0, 7)[:2] == (0.0, 1.0)


@pytest.mark.parametrize("table,p", [
    ((10, 2, 3, 15), 0.0005367241191434357),
    ((8, 2, 1, 5), 0.034965034965034975),
    ((45, 5, 38, 12), 0.10836958408822048),
    ((29, 0, 25, 4), 0.1119617224880383),
    ((0, 5, 0, 7), 1.0),
])
def test_fisher_exact(table, p):
    assert m.fisher_exact(*table) == pytest.approx(p, abs=1e-10)


@pytest.mark.parametrize("a,b,interval", [
    ((40, 50), (45, 50), (-0.04343178591209204, 0.24209715781408725)),
    ((25, 29), (29, 29), (-0.0054695827590056645, 0.30558993720352023)),
    ((10, 20), (18, 20), (0.1159299100097686, 0.6132710342397404)),
])
def test_newcombe_interval_of_b_minus_a(a, b, interval):
    lower, difference, upper = m.newcombe_interval(*a, *b, 0.95)
    assert (lower, upper) == pytest.approx(interval, abs=1e-9)
    assert difference == pytest.approx(b[0] / b[1] - a[0] / a[1])


def test_a_comparisons_verdict_comes_from_the_interval_and_shows_the_right_p_value():
    better = m.compare_proportions(10, 20, 18, 20, 0.95)
    assert better.verdict == m.ComparisonVerdict.better
    assert better.p_value_method == "chi_square"  # expected counts 14 and 6
    small = m.compare_proportions(3, 10, 8, 10, 0.95)
    assert small.p_value_method == "fisher_exact"  # an expected count of 4.5
    assert small.p_value == pytest.approx(m.fisher_exact(3, 7, 8, 2))
    same = m.compare_proportions(40, 50, 45, 50, 0.95)
    assert same.verdict == m.ComparisonVerdict.no_difference
    assert same.p_value_method == "chi_square"
    assert m.compare_proportions(18, 20, 10, 20, 0.95).verdict == m.ComparisonVerdict.worse


def test_runs_needed_for_two_pass_rates():
    assert m.runs_needed_for_proportions(0.8, 0.9, 0.95) == 199
    assert m.runs_needed_for_proportions(0.9, 0.9, 0.95) is None


def test_the_module_imports_nothing_outside_the_standard_library():
    tree = ast.parse(Path(m.__file__).read_text())
    imported = {alias.name.split(".")[0] for node in ast.walk(tree)
                if isinstance(node, ast.Import) for alias in node.names}
    imported |= {node.module.split(".")[0] for node in ast.walk(tree)
                 if isinstance(node, ast.ImportFrom) and node.module}
    assert imported <= {"math", "dataclasses", "enum", "statistics"}
