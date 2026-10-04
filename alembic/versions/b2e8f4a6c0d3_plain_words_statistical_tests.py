"""say the statistical tests in plain words: names, questions, labels, hints

Revision ID: b2e8f4a6c0d3
Revises: 9c4d2e7f0a1b
Create Date: 2026-10-02 23:00:00.000000

The catalogue's texts were written for statisticians: "Binomial gate",
"One-sample t-test", "Confidence level", floors explained with 0.9^29 and
p-values. A user deciding whether a prompt is good enough can't use them.
Every name, question, parameter label and hint, and every "why at least N"
explanation is reworded in everyday terms ("Passes reliably", "How sure",
"A few passes in a row can be luck…"). Ids and engines don't change, and
`method` keeps the technical description for whoever wants it. A text an
operator has already reworded is left alone, both ways.

"""
import json
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = 'b2e8f4a6c0d3'
down_revision: str | None = '9c4d2e7f0a1b'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# what 9c4d2e7f0a1b left: the texts this migration rewords
_BEFORE = {'binomial_gate': {'name': 'Binomial gate',
                   'question': 'Does each check pass at least a target share of the time? For '
                               'example: "95% confident it passes at least 90% of the time".',
                   'floor_explanation': 'If a check really passed exactly the target share of '
                                        'the time, how likely would it be to pass every run? '
                                        "At 90% that's 0.9^29 = 4.7% for 29 runs — rarer than "
                                        '1 in 20 — so 29 straight passes prove "above 90%" '
                                        "with 95% confidence. At 28 runs it's 5.2%: no result "
                                        'could prove it. The floor allows no miss; one miss '
                                        'takes 46 runs at 90%.',
                   'parameters': {'target': {'label': 'Target pass rate',
                                             'hint': 'The share of runs each check must pass — '
                                                     '"at least 90% of the time", say. Higher '
                                                     'targets need many more runs.'},
                                  'confidence': {'label': 'Confidence level',
                                                 'hint': 'How sure a verdict must be: at 95% a '
                                                         'proven claim is wrong at most 1 time '
                                                         'in 20. Higher needs more runs.'}}},
 'one_sample_t': {'name': 'One-sample t-test',
                  'question': "Is each scored check's average score on the passing side of its "
                              'threshold? For example: "95% confident the average ROUGE is '
                              'above 0.6".',
                  'floor_explanation': 'Below 10 scores the spread is too poorly known for the '
                                       "t-test to mean much; 30 is where it's comfortable. The "
                                       'suggestion in between sizes the batch to see the gap '
                                       'you set: with scores spreading ±0.1, a mean 0.05 from '
                                       'the threshold needs 27 runs to be seen 80% of the '
                                       'time.',
                  'parameters': {'confidence': {'label': 'Confidence level',
                                                'hint': 'How sure a verdict must be: at 95% a '
                                                        'proven claim is wrong at most 1 time '
                                                        'in 20. Higher needs more runs.'},
                                 'difference': {'label': 'Smallest gap worth detecting',
                                                'hint': 'How far from the threshold an average '
                                                        'must be for you to care, as a share '
                                                        "of the check's score range (5% of the "
                                                        'range is 0.05 on ROUGE, 5 points on '
                                                        'BLEU). Only sizes the suggestion.'},
                                 'spread': {'label': 'Expected spread of scores',
                                            'hint': 'How much scores usually vary between runs '
                                                    '(their standard deviation), as a share of '
                                                    'the score range. Only sizes the '
                                                    'suggestion; the verdict uses the spread '
                                                    'the batch observes.'}}},
 'judge_stability': {'name': 'Judge stability',
                     'question': 'Is the judge itself consistent? The same recorded answer '
                                 'judged again and again: does each judge check give the same '
                                 'verdict at least a target share of the time?',
                     'floor_explanation': "The binomial gate's floor, on agreement instead of "
                                          'passing: 29 runs that all agree prove "agrees at '
                                          'least 90% of the time" with 95% confidence. Only '
                                          'judge checks of entries with a recorded answer are '
                                          'tested: when the answer itself varies, a changed '
                                          "verdict can't be pinned on the judge.",
                     'parameters': {'target': {'label': 'Target agreement',
                                               'hint': 'The share of runs that must agree with '
                                                       "the judge's usual verdict (pass or "
                                                       'fail, whichever it gives more often): '
                                                       'at 90% agreement it changes its mind '
                                                       'at most 1 time in 10.'},
                                    'confidence': {'label': 'Confidence level',
                                                   'hint': 'How sure a verdict must be: at 95% '
                                                           'a proven claim is wrong at most 1 '
                                                           'time in 20. Higher needs more '
                                                           'runs.'}}},
 'pass_rates': {'name': 'Pass rates, A against B',
                'question': "Did a check's pass rate change between two batches of the same "
                            'scope? For example: "did my change to the prompt help?".',
                'floor_explanation': "No minimum: with few runs Fisher's exact test stands in "
                                     'for chi-square. But small batches only see big '
                                     'differences — 20 runs each sees about 35 points, 60 each '
                                     'about 20 points, and 80% against 90% takes about 200 '
                                     'each.',
                'parameters': {'confidence': {'label': 'Confidence level',
                                              'hint': 'How sure a verdict must be: at 95% a '
                                                      'proven claim is wrong at most 1 time in '
                                                      '20. Higher needs more runs.'}}},
 'no_worse': {'name': 'No worse than A',
              'question': 'Is B no worse than A by more than a margin? The release-gate '
                          'question: a difference test can\'t prove "no difference", only fail '
                          'to find one; this proves "at most 5 points worse".',
              'floor_explanation': 'No minimum, but proving a small margin takes large '
                                   'batches: two checks both passing 90% of the time need '
                                   'about 446 times each to show a 5-point margin, about 112 '
                                   'for 10 points.',
              'parameters': {'margin': {'label': 'Margin',
                                        'hint': 'How much worse B may be and still count as no '
                                                'worse — 5 points of pass rate, say. Smaller '
                                                'margins need many more runs.'},
                             'confidence': {'label': 'Confidence level',
                                            'hint': 'How sure a verdict must be: at 95% a '
                                                    'proven claim is wrong at most 1 time in '
                                                    '20. Higher needs more runs.'}}},
 'mean_scores': {'name': 'Mean scores, A against B',
                 'question': "Did a scored check's average change between two batches? Welch's "
                             't-test on the scores themselves, not only on pass or fail.',
                 'floor_explanation': 'About 20 scores per batch: with fewer the spread is '
                                      'poorly known and only big differences show. Two scores '
                                      'per batch is the least it can compute at. When scores '
                                      'bunch against 0 or 1, prefer score ranks.',
                 'parameters': {'confidence': {'label': 'Confidence level',
                                               'hint': 'How sure a verdict must be: at 95% a '
                                                       'proven claim is wrong at most 1 time '
                                                       'in 20. Higher needs more runs.'}}},
 'score_ranks': {'name': 'Score ranks, A against B',
                 'question': "Do a scored check's scores tend to be higher in B than in A? "
                             'Mann–Whitney on the ranks — for scores that pile up against a '
                             'bound (0 or 1), where an average misleads.',
                 'floor_explanation': 'At least 4 scores per batch: with 3 against 3, even a '
                                      "perfect separation has p = 0.1 and can't reach 95%. 10 "
                                      'per batch is the practical minimum, 20 recommended.',
                 'parameters': {'confidence': {'label': 'Confidence level',
                                               'hint': 'How sure a verdict must be: at 95% a '
                                                       'proven claim is wrong at most 1 time '
                                                       'in 20. Higher needs more runs.'}}},
 'paired_entries': {'name': 'Paired by entry',
                    'question': "On the same entries, did B do better than A? Each entry's "
                                'check is paired with itself, so the differences between '
                                'entries cancel out — the strongest form of "did my change '
                                'help?" for a test set.',
                    'floor_explanation': 'At least 6 pairs (entries × checks) that changed: '
                                         'six differences of the same sign have p = 0.031 '
                                         "under Wilcoxon's test, five 0.0625. 10 pairs is the "
                                         'practical minimum.',
                    'parameters': {'confidence': {'label': 'Confidence level',
                                                  'hint': 'How sure a verdict must be: at 95% '
                                                          'a proven claim is wrong at most 1 '
                                                          'time in 20. Higher needs more '
                                                          'runs.'}}}}

_HOW_SURE = {"label": "How sure",
         "hint": "95% sure means the answer is wrong at most 1 time in 20. Surer takes more runs."}
_AFTER = {
    "binomial_gate": {
        "name": "Passes reliably",
        "question": "Does each check pass almost every time? Run it many times and find out "
                    "whether it passes at least, say, 9 times in 10.",
        "floor_explanation":
            "A few passes in a row can be luck. A check that really passed only 9 times in 10 "
            "would pass 29 times in a row less than 1 time in 20, so 29 clean passes are "
            "enough to be 95% sure. With fewer, even a perfect record could be luck. Allowing "
            "one failure takes 46 runs.",
        "parameters": {
            "target": {"label": "How often it must pass",
                       "hint": "The share of runs each check must pass — \"at least 9 times in "
                               "10\", say. The higher, the more runs it takes."},
            "confidence": _HOW_SURE,
        },
    },
    "one_sample_t": {
        "name": "Scores high enough on average",
        "question": "Is each scored check's average score on the right side of its threshold — "
                    "is the average ROUGE above 0.6, say, and not just in one lucky run?",
        "floor_explanation":
            "An average of fewer than 10 scores is too unreliable to judge, and 30 is "
            "comfortable. The suggestion in between is what it takes to spot the gap you care "
            "about: if scores vary by about 10% of the range, an average 5% from the threshold "
            "takes about 27 runs.",
        "parameters": {
            "confidence": _HOW_SURE,
            "difference": {"label": "How close to the threshold matters",
                           "hint": "How far from the threshold an average has to be before you "
                                   "care, as a share of the score range — 5% is 0.05 on ROUGE, "
                                   "5 points on BLEU. It only sizes the suggested number of "
                                   "runs."},
            "spread": {"label": "How much scores usually vary",
                       "hint": "How much a check's score changes from run to run, as a share of "
                               "the score range. A guess is fine: it only sizes the suggested "
                               "number of runs; the answer uses how much they actually vary."},
        },
    },
    "judge_stability": {
        "name": "The judge is consistent",
        "question": "Does the LLM judge give the same verdict when it sees the same answer "
                    "again? Judge one recorded answer many times and find out whether it agrees "
                    "with itself at least, say, 9 times in 10.",
        "floor_explanation":
            "A judge can agree with itself a few times by luck. One that really agreed only 9 "
            "times in 10 would agree 29 times in a row less than 1 time in 20, so 29 agreeing "
            "runs are enough to be 95% sure. Only judge checks on a recorded answer count: if "
            "the answer itself changes, a changed verdict could be the answer's doing.",
        "parameters": {
            "target": {"label": "How often it must agree",
                       "hint": "The share of runs that must give the judge's usual verdict "
                               "(pass or fail, whichever it gives more often) — \"at least 9 "
                               "times in 10\", say."},
            "confidence": _HOW_SURE,
        },
    },
    "pass_rates": {
        "name": "Did it get better?",
        "question": "Compare two batches of the same tests — before and after a change to your "
                    "prompt, say — and find out whether each check passes more often, less "
                    "often, or no differently.",
        "floor_explanation":
            "Any size works, but small batches only notice big changes: 20 runs each notices a "
            "jump of about 35 points, 60 runs each about 20 points; telling 80% from 90% takes "
            "about 200 runs each.",
        "parameters": {"confidence": _HOW_SURE},
    },
    "no_worse": {
        "name": "Is it still as good?",
        "question": "Did a change keep things at least as good — no more than a few points "
                    "worse? The question to ask before a release: \"no clear difference\" isn't "
                    "proof that nothing got worse, this is.",
        "floor_explanation":
            "Any size works, but proving a small margin takes big batches: for checks passing "
            "about 9 times in 10, a 5-point margin takes about 446 runs each, a 10-point margin "
            "about 112.",
        "parameters": {
            "margin": {"label": "How much worse is acceptable",
                       "hint": "How many points of pass rate B may lose and still count as just "
                               "as good — 5 points, say. The smaller, the more runs it takes."},
            "confidence": _HOW_SURE,
        },
    },
    "mean_scores": {
        "name": "Did scores go up?",
        "question": "Compare two batches on a scored check (ROUGE, BLEU, …): did the average "
                    "score go up, down, or stay the same?",
        "floor_explanation":
            "About 20 scores per batch: with fewer, only big changes show. When scores bunch up "
            "near 0 or 1, use the version for scores near 0 or 1 instead.",
        "parameters": {"confidence": _HOW_SURE},
    },
    "score_ranks": {
        "name": "Did scores go up? (scores near 0 or 1)",
        "question": "Like \"Did scores go up?\", for scores that pile up near 0 or 1, where an "
                    "average misleads: does B tend to score higher than A?",
        "floor_explanation":
            "At least 4 scores per batch can give an answer; 10 is the practical minimum and 20 "
            "is comfortable.",
        "parameters": {"confidence": _HOW_SURE},
    },
    "paired_entries": {
        "name": "Did each entry get better?",
        "question": "Compare two batches of the same test set entry by entry: with each entry "
                    "measured against itself, did B do better than A overall? The sharpest way "
                    "to tell whether a change helped a test set.",
        "floor_explanation":
            "It needs at least 6 checks across the entries that both batches ran, and 10 for a "
            "reliable answer.",
        "parameters": {"confidence": _HOW_SURE},
    },
}

_TEXTS = ("name", "question", "floor_explanation")

_statistical_tests = sa.table(
    "statistical_tests",
    sa.column("id", sa.Text()),
    sa.column("name", sa.Text()),
    sa.column("question", sa.Text()),
    sa.column("floor_explanation", sa.Text()),
    sa.column("parameters", sa.JSON()),
)


def _reword(old: dict, new: dict) -> None:
    connection = op.get_bind()
    rows = connection.execute(sa.select(
        _statistical_tests.c.id, _statistical_tests.c.name, _statistical_tests.c.question,
        _statistical_tests.c.floor_explanation, _statistical_tests.c.parameters)).all()
    for row in rows:
        if row.id not in old:
            continue
        values = {field: new[row.id][field] for field in _TEXTS
                  if getattr(row, field) == old[row.id][field]}
        parameters = json.loads(row.parameters) if isinstance(row.parameters, str) else (
            row.parameters)
        changed = False
        for parameter in parameters or []:
            was = old[row.id]["parameters"].get(parameter.get("key"))
            if was is None:
                continue
            for field in ("label", "hint"):
                if parameter.get(field) == was[field]:
                    parameter[field] = new[row.id]["parameters"][parameter["key"]][field]
                    changed = True
        if changed:
            values["parameters"] = parameters
        if values:
            connection.execute(sa.update(_statistical_tests)
                               .where(_statistical_tests.c.id == row.id).values(**values))


def upgrade() -> None:
    _reword(_BEFORE, _AFTER)


def downgrade() -> None:
    _reword(_AFTER, _BEFORE)
