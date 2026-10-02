"""the statistical tests as rows: statistical_tests, and the engine on batches

Revision ID: 7a3c5e9f1b2d
Revises: 5d8a1f3c9e27
Create Date: 2026-10-02 10:00:00.000000

The catalogue of statistical tests moves from code to a table, like the
check types: a `statistical_tests` row names the engine in code that does
its arithmetic and carries everything a user may want different — the texts,
each parameter's label, default, range and hint, the floor's explanation and
worked examples, the engine's settings. The eight rows seeded here are the
eight tests of 0.11.0, with their texts as they were, under the same ids
(`binomial_gate`, …), so stored batches and the FE keep working.

`statistical_batches` and `statistical_comparisons` gain `engine`: the
engine their row named when they were created, backfilled from
`statistical_test` (the seeded ids are the engine names). A batch is
finished and a comparison read with the recorded engine, never the row.

The downgrade drops the two columns and the table.

"""
from collections.abc import Sequence
from datetime import datetime, timedelta

import sqlalchemy as sa

from alembic import op

revision: str = '7a3c5e9f1b2d'
down_revision: str | None = '5d8a1f3c9e27'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CONFIDENCE = {
    "key": "confidence", "label": "Confidence level", "kind": "level", "default": 0.95,
    "min": 0.80, "max": 0.999,
    "hint": "How sure a verdict must be: at 0.95 a proven claim is wrong at most 1 time in "
            "20. Higher needs more runs.",
}

# the catalogue of 0.11.0, in its order: batch tests, then comparisons
_ROWS = [
    {
        "id": "binomial_gate",
        "name": "Binomial gate",
        "engine": "binomial_gate",
        "question": "Does each check pass at least a target share of the time? For example: "
                    "\"95% confident it passes at least 90% of the time\".",
        "parameters": [
            {"key": "target", "label": "Target pass rate", "kind": "rate", "default": 0.9,
             "min": 0.5, "max": 0.999,
             "hint": "The share of runs each check must pass: 0.9 means \"at least 90% of "
                     "the time\". Higher targets need many more runs."},
            _CONFIDENCE,
        ],
        "engine_settings": {},
        "floor_explanation":
            "If a check really passed exactly the target share of the time, how likely would "
            "it be to pass every run? At 90% that's 0.9^29 = 4.7% for 29 runs — rarer than 1 "
            "in 20 — so 29 straight passes prove \"above 90%\" with 95% confidence. At 28 "
            "runs it's 5.2%: no result could prove it. The floor allows no miss; one miss "
            "takes 46 runs at 90%.",
        "floor_examples": [
            {"target": 0.8, "confidence": 0.95, "times": 14},
            {"target": 0.9, "confidence": 0.95, "times": 29},
            {"target": 0.95, "confidence": 0.95, "times": 59},
            {"target": 0.99, "confidence": 0.95, "times": 299},
        ],
        "method": "An exact binomial test each way at the confidence level: pass when the "
                  "exact (Clopper–Pearson) one-sided lower bound of the pass rate is at least "
                  "the target, fail when the upper bound is below it, inconclusive otherwise.",
    },
    {
        "id": "one_sample_t",
        "name": "One-sample t-test",
        "engine": "one_sample_t",
        "question": "Is each scored check's average score on the passing side of its "
                    "threshold? For example: \"95% confident the average ROUGE is above "
                    "0.6\".",
        "parameters": [
            _CONFIDENCE,
            {"key": "difference", "label": "Smallest gap worth detecting", "kind": "share",
             "default": 0.05, "min": 0.005, "max": 1.0,
             "hint": "How far from the threshold an average must be for you to care, as a "
                     "share of the check's score range (0.05 = 0.05 on ROUGE, 5 points on "
                     "BLEU). Only sizes the suggestion."},
            {"key": "spread", "label": "Expected spread of scores", "kind": "share",
             "default": 0.1, "min": 0.001, "max": 1.0,
             "hint": "How much scores usually vary between runs (their standard deviation), "
                     "as a share of the score range. Only sizes the suggestion; the verdict "
                     "uses the spread the batch observes."},
        ],
        "engine_settings": {"floor": 10, "recommended_times": 30},
        "floor_explanation":
            "Below 10 scores the spread is too poorly known for the t-test to mean much; 30 "
            "is where it's comfortable. The suggestion in between sizes the batch to see the "
            "gap you set: with scores spreading ±0.1, a mean 0.05 from the threshold needs "
            "27 runs to be seen 80% of the time.",
        "floor_examples": [
            {"difference": 0.05, "spread": 0.1, "confidence": 0.95, "times": 27},
            {"difference": 0.025, "spread": 0.1, "confidence": 0.95, "times": 101},
        ],
        "method": "Student's one-sample t-test each way, in the direction of the check type's "
                  "comparison: pass when the one-sided t bound of the mean score is on the "
                  "passing side of the threshold, fail when the other bound is on the failing "
                  "side, inconclusive otherwise. Scores that never vary (a recorded answer) "
                  "are compared to the threshold directly.",
    },
    {
        "id": "judge_stability",
        "name": "Judge stability",
        "engine": "judge_stability",
        "question": "Is the judge itself consistent? The same recorded answer judged again "
                    "and again: does each judge check give the same verdict at least a target "
                    "share of the time?",
        "parameters": [
            {"key": "target", "label": "Target agreement", "kind": "rate", "default": 0.9,
             "min": 0.6, "max": 0.999,
             "hint": "The share of runs that must agree with the judge's usual verdict (pass "
                     "or fail, whichever it gives more often): 0.9 means it changes its mind "
                     "at most 1 time in 10."},
            _CONFIDENCE,
        ],
        "engine_settings": {},
        "floor_explanation":
            "The binomial gate's floor, on agreement instead of passing: 29 runs that all "
            "agree prove \"agrees at least 90% of the time\" with 95% confidence. Only judge "
            "checks of entries with a recorded answer are tested: when the answer itself "
            "varies, a changed verdict can't be pinned on the judge.",
        "floor_examples": [
            {"target": 0.8, "confidence": 0.95, "times": 14},
            {"target": 0.9, "confidence": 0.95, "times": 29},
            {"target": 0.95, "confidence": 0.95, "times": 59},
        ],
        "method": "An exact binomial test each way on the agreement count — the runs that "
                  "gave the judge's majority verdict: pass when the exact one-sided lower "
                  "bound of the agreement rate is at least the target, fail when the upper "
                  "bound is below it, inconclusive otherwise.",
    },
    {
        "id": "pass_rates",
        "name": "Pass rates, A against B",
        "engine": "pass_rates",
        "question": "Did a check's pass rate change between two batches of the same scope? "
                    "For example: \"did my change to the prompt help?\".",
        "parameters": [_CONFIDENCE],
        "engine_settings": {},
        "floor_explanation":
            "No minimum: with few runs Fisher's exact test stands in for chi-square. But "
            "small batches only see big differences — 20 runs each sees about 35 points, 60 "
            "each about 20 points, and 80% against 90% takes about 200 each.",
        "floor_examples": [{"rate_a": 0.8, "rate_b": 0.9, "confidence": 0.95, "times": 199}],
        "method": "Newcombe's score interval of B − A decides: better when it lies above 0, "
                  "worse when below, no real difference otherwise. Beside it, chi-square's "
                  "p-value when every expected count is at least 5, Fisher's exact otherwise.",
    },
    {
        "id": "no_worse",
        "name": "No worse than A",
        "engine": "no_worse",
        "question": "Is B no worse than A by more than a margin? The release-gate question: a "
                    "difference test can't prove \"no difference\", only fail to find one; "
                    "this proves \"at most 5 points worse\".",
        "parameters": [
            {"key": "margin", "label": "Margin", "kind": "share", "default": 0.05,
             "min": 0.01, "max": 0.5,
             "hint": "How much worse B may be and still count as no worse: 0.05 is 5 points "
                     "of pass rate. Smaller margins need many more runs."},
            _CONFIDENCE,
        ],
        "engine_settings": {},
        "floor_explanation":
            "No minimum, but proving a small margin takes large batches: two checks both "
            "passing 90% of the time need about 446 times each to show a 5-point margin, "
            "about 112 for 10 points.",
        "floor_examples": [
            {"rate_a": 0.9, "rate_b": 0.9, "margin": 0.05, "confidence": 0.95, "times": 446},
            {"rate_a": 0.9, "rate_b": 0.9, "margin": 0.1, "confidence": 0.95, "times": 112},
        ],
        "method": "Newcombe's interval of B − A with one-sided bounds at the confidence level "
                  "(the two-sided interval at 2 × confidence − 1): no worse when the lower "
                  "bound is above −margin, worse when the upper bound is below it, "
                  "inconclusive otherwise.",
    },
    {
        "id": "mean_scores",
        "name": "Mean scores, A against B",
        "engine": "mean_scores",
        "question": "Did a scored check's average change between two batches? Welch's t-test "
                    "on the scores themselves, not only on pass or fail.",
        "parameters": [_CONFIDENCE],
        "engine_settings": {"recommended_times": 20},
        "floor_explanation":
            "About 20 scores per batch: with fewer the spread is poorly known and only big "
            "differences show. Two scores per batch is the least it can compute at. When "
            "scores bunch against 0 or 1, prefer score ranks.",
        "floor_examples": [],
        "method": "Welch's two-sample t-test (unequal variances) of B − A, two-sided: better "
                  "when its interval lies above 0 in the type's direction (higher is better "
                  "for every metric today), worse when on the other side, no real difference "
                  "otherwise.",
    },
    {
        "id": "score_ranks",
        "name": "Score ranks, A against B",
        "engine": "score_ranks",
        "question": "Do a scored check's scores tend to be higher in B than in A? "
                    "Mann–Whitney on the ranks — for scores that pile up against a bound (0 "
                    "or 1), where an average misleads.",
        "parameters": [_CONFIDENCE],
        "engine_settings": {"recommended_times": 20},
        "floor_explanation":
            "At least 4 scores per batch: with 3 against 3, even a perfect separation has "
            "p = 0.1 and can't reach 95%. 10 per batch is the practical minimum, 20 "
            "recommended.",
        "floor_examples": [{"confidence": 0.95, "times": 4}],
        "method": "The Mann–Whitney U test, two-sided (exact for small samples without ties, "
                  "the normal approximation otherwise): better when B's scores are "
                  "significantly higher — the probability that a score of B beats one of A, "
                  "`effect`, above 0.5 — worse when lower, no real difference otherwise.",
    },
    {
        "id": "paired_entries",
        "name": "Paired by entry",
        "engine": "paired_entries",
        "question": "On the same entries, did B do better than A? Each entry's check is "
                    "paired with itself, so the differences between entries cancel out — the "
                    "strongest form of \"did my change help?\" for a test set.",
        "parameters": [_CONFIDENCE],
        "engine_settings": {},
        "floor_explanation":
            "At least 6 pairs (entries × checks) that changed: six differences of the same "
            "sign have p = 0.031 under Wilcoxon's test, five 0.0625. 10 pairs is the "
            "practical minimum.",
        "floor_examples": [{"confidence": 0.95, "pairs": 6}],
        "method": "The paired t-test of the per-pair difference in pass rate (B − A), "
                  "two-sided, decides from its interval: better above 0, worse below, no real "
                  "difference otherwise. Wilcoxon's signed-rank p-value is shown beside it, "
                  "as a check that doesn't assume the differences are bell-shaped.",
    },
]

_statistical_tests = sa.table(
    "statistical_tests",
    sa.column("id", sa.Text()),
    sa.column("name", sa.Text()),
    sa.column("engine", sa.Text()),
    sa.column("question", sa.Text()),
    sa.column("parameters", sa.JSON()),
    sa.column("engine_settings", sa.JSON()),
    sa.column("floor_explanation", sa.Text()),
    sa.column("floor_examples", sa.JSON()),
    sa.column("method", sa.Text()),
    sa.column("created_at", sa.DateTime()),
)


def upgrade() -> None:
    op.create_table(
        "statistical_tests",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("engine", sa.Text(), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("parameters", sa.JSON(), nullable=False),
        sa.Column("engine_settings", sa.JSON(), nullable=False),
        sa.Column("floor_explanation", sa.Text(), nullable=False),
        sa.Column("floor_examples", sa.JSON(), nullable=False),
        sa.Column("method", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )
    # seeded a second apart in the catalogue's order, all in the past, so a
    # row added right after the migration still lists last (the API lists by
    # created_at)
    first = datetime.now().astimezone() - timedelta(seconds=len(_ROWS))
    op.bulk_insert(_statistical_tests, [
        {**row, "created_at": first + timedelta(seconds=position)}
        for position, row in enumerate(_ROWS)
    ])

    for table in ("statistical_batches", "statistical_comparisons"):
        with op.batch_alter_table(table) as batch:
            batch.add_column(sa.Column("engine", sa.Text(), nullable=True))
        op.execute(f"UPDATE {table} SET engine = statistical_test")
        with op.batch_alter_table(table) as batch:
            batch.alter_column("engine", existing_type=sa.Text(), nullable=False)


def downgrade() -> None:
    for table in ("statistical_batches", "statistical_comparisons"):
        with op.batch_alter_table(table) as batch:
            batch.drop_column("engine")
    op.drop_table("statistical_tests")
