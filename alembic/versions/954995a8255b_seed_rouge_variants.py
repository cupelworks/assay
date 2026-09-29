"""seed the ROUGE variants and give every ROUGE threshold a placeholder and hint

Revision ID: 954995a8255b
Revises: 812acbc349ad
Create Date: 2026-09-30 18:00:00.000000

Four catalogue rows on the existing `rouge` engine, each a different
`variant`/`measure`: ROUGE-1 (shared words), ROUGE-2 (shared word pairs),
ROUGE-L Recall (how much of the expected text the answer covers) and
ROUGE-L Precision (how much of the answer comes from the expected text).
Recall and precision separate the two ways an answer can miss that F1
blurs together: leaving things out, and adding things.

Typical scores differ a lot between them, so every ROUGE row's threshold —
the existing ROUGE's included — gets a placeholder and a hint with the
range to expect, measured on sample answers with the reference
implementation. The downgrade deletes the four rows by name (it fails, as
it should, while a test still has one assigned) and restores ROUGE's
threshold as it was.

"""
import uuid
from collections.abc import Sequence
from datetime import datetime

import sqlalchemy as sa

from alembic import op

revision: str = '954995a8255b'
down_revision: str | None = '812acbc349ad'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_REFERENCE = {"key": "reference", "label": "Reference text", "kind": "reference",
              "required": True}


def _threshold(placeholder: str | None = None, hint: str | None = None) -> dict:
    field = {"key": "threshold", "label": "Minimum score to pass", "kind": "numeric",
             "required": True, "min": 0.0, "max": 1.0}
    if placeholder is not None:
        field |= {"placeholder": placeholder, "hint": hint}
    return field


_ROUGE_BEFORE = [_REFERENCE, _threshold()]
_ROUGE_AFTER = [_REFERENCE, _threshold(
    "0.5",
    "0 to 1: shared wording, in order. A close paraphrase scores about 0.6, an unrelated "
    "answer about 0.2.",
)]

_ROWS = [
    {
        "name": "ROUGE-1",
        "description": "Measures how many words the output shares with the expected text, in "
                       "any order (ROUGE-1 F1).",
        "best_for": "Checking the answer uses the right vocabulary or key terms, however it's "
                    "phrased.",
        "limitations": "Ignores word order and meaning — only the words themselves, after "
                       "stemming; English only.",
        "engine_settings": {"variant": "rouge1", "measure": "f1", "stemmer": True},
        "config_fields": [_REFERENCE, _threshold(
            "0.5",
            "0 to 1: shared words, in any order. A close paraphrase scores about 0.6, an "
            "unrelated answer about 0.2.",
        )],
    },
    {
        "name": "ROUGE-2",
        "description": "Measures how many consecutive word pairs the output shares with the "
                       "expected text (ROUGE-2 F1).",
        "best_for": "Checking close phrasing: fixed wording, templates, terminology used the "
                    "same way.",
        "limitations": "Drops fast with any rewording, so thresholds sit lower than for "
                       "ROUGE-1; English only.",
        "engine_settings": {"variant": "rouge2", "measure": "f1", "stemmer": True},
        "config_fields": [_REFERENCE, _threshold(
            "0.3",
            "0 to 1: shared word pairs, so lower than ROUGE-1. A close paraphrase scores about "
            "0.3, an unrelated answer 0.",
        )],
    },
    {
        "name": "ROUGE-L Recall",
        "description": "Measures how much of the expected text the output covers, as the "
                       "longest in-order run of shared words (ROUGE-L recall).",
        "best_for": "Answers that must include the key information, even when they say "
                    "more.",
        "limitations": "Doesn't penalise extra or wrong content — pair it with ROUGE-L "
                       "Precision or a judge; English only.",
        "engine_settings": {"variant": "rougeL", "measure": "recall", "stemmer": True},
        "config_fields": [_REFERENCE, _threshold(
            "0.6",
            "0 to 1: how much of the expected text the answer covers. A longer answer that "
            "covers it all can score 0.7 or more.",
        )],
    },
    {
        "name": "ROUGE-L Precision",
        "description": "Measures how much of the output comes from the expected text, as the "
                       "longest in-order run of shared words (ROUGE-L precision).",
        "best_for": "Concise, on-topic answers without padding or invented extras.",
        "limitations": "A short answer that leaves most of the expected content out can "
                       "still score high — pair it with ROUGE-L Recall; English only.",
        "engine_settings": {"variant": "rougeL", "measure": "precision", "stemmer": True},
        "config_fields": [_REFERENCE, _threshold(
            "0.6",
            "0 to 1: how much of the answer comes from the expected text. Extra content "
            "lowers it; a short, on-point answer can score 0.8 or more.",
        )],
    },
]

_test_types = sa.table(
    "test_types",
    sa.column("id", sa.Uuid()),
    sa.column("name", sa.Text()),
    sa.column("category", sa.Text()),
    sa.column("description", sa.Text()),
    sa.column("best_for", sa.Text()),
    sa.column("cost", sa.Text()),
    sa.column("limitations", sa.Text()),
    sa.column("config_fields", sa.JSON()),
    sa.column("engine", sa.Text()),
    sa.column("engine_settings", sa.JSON()),
    sa.column("comparison", sa.Text()),
    sa.column("is_active", sa.Boolean()),
    sa.column("created_at", sa.DateTime()),
)


def upgrade() -> None:
    now = datetime.now().astimezone()
    op.bulk_insert(_test_types, [
        {
            "id": uuid.uuid4(),
            "category": "nlp_metric",
            "cost": "fast",
            "engine": "rouge",
            "comparison": "gte",
            "is_active": True,
            "created_at": now,
            **row,
        }
        for row in _ROWS
    ])
    op.execute(_test_types.update().where(_test_types.c.name == "ROUGE")
               .values(config_fields=_ROUGE_AFTER))


def downgrade() -> None:
    op.execute(_test_types.update().where(_test_types.c.name == "ROUGE")
               .values(config_fields=_ROUGE_BEFORE))
    names = [row["name"] for row in _ROWS]
    op.execute(_test_types.delete().where(_test_types.c.name.in_(names)))
