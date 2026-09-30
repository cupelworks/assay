"""give BLEU the sacreBLEU settings and texts, and seed BLEU (case-insensitive)

Revision ID: ad28dd68006e
Revises: 1d1fddd1a247
Create Date: 2026-09-30 23:59:00.000000

The BLEU engine now scores with sacreBLEU, whose settings the row names as
the library does: `smooth_method` (`exp`, its default) and `lowercase`
(off: capitals count as different words). Its texts say what BLEU measures
— the answer's wording in runs of up to four words, found in the expected
text, with a much lower score for an answer shorter than the expected one —
and its threshold gets a placeholder and a hint with the scores to expect,
measured on sample answers.

BLEU (case-insensitive) is the same engine with `lowercase` on: a second
row, no code. The downgrade deletes it by name (it fails, as it should,
while a test still has it assigned) and restores BLEU as it was.

"""
import uuid
from collections.abc import Sequence
from datetime import datetime

import sqlalchemy as sa

from alembic import op

revision: str = 'ad28dd68006e'
down_revision: str | None = '1d1fddd1a247'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_REFERENCE = {"key": "reference", "label": "Reference text", "kind": "reference",
              "required": True}
_THRESHOLD = {"key": "threshold", "label": "Minimum score to pass", "kind": "numeric",
              "required": True, "min": 0.0, "max": 100.0}
_THRESHOLD_WITH_HINT = {
    **_THRESHOLD,
    "placeholder": "20",
    "hint": "Shared wording in runs of up to four words. A close paraphrase scores about 25, "
            "an unrelated answer under 5.",
}

_BLEU_BEFORE = {
    "description": "Measures n-gram precision between output and reference text.",
    "best_for": "Machine translation tasks.",
    "limitations": "Poorly suited for short texts; doesn't account for recall or semantics.",
    "engine_settings": {"smoothing": True},
    "config_fields": [_REFERENCE, _THRESHOLD],
}
_BLEU_AFTER = {
    "description": "Measures how much of the output's wording, in runs of up to four words, "
                   "appears in the expected text; an output shorter than the expected text "
                   "scores much lower.",
    "best_for": "Fixed wording: translations, templated replies, answers that should reuse "
                "the expected phrasing.",
    "limitations": "Rewording scores low and a short correct answer scores near 0; capitals "
                   "count as different words.",
    "engine_settings": {"smooth_method": "exp", "lowercase": False},
    "config_fields": [_REFERENCE, _THRESHOLD_WITH_HINT],
}
_CASE_INSENSITIVE = {
    "name": "BLEU (case-insensitive)",
    "description": "Measures how much of the output's wording, in runs of up to four words, "
                   "appears in the expected text, ignoring capitals; an output shorter than "
                   "the expected text scores much lower.",
    "best_for": "Fixed wording where capitalisation doesn't matter.",
    "limitations": "Rewording scores low and a short correct answer scores near 0.",
    "engine_settings": {"smooth_method": "exp", "lowercase": True},
    "config_fields": [_REFERENCE, _THRESHOLD_WITH_HINT],
}

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
    op.execute(_test_types.update().where(_test_types.c.name == "BLEU").values(**_BLEU_AFTER))
    op.bulk_insert(_test_types, [{
        "id": uuid.uuid4(),
        "category": "nlp_metric",
        "cost": "fast",
        "engine": "bleu",
        "comparison": "gte",
        "is_active": True,
        "created_at": datetime.now().astimezone(),
        **_CASE_INSENSITIVE,
    }])


def downgrade() -> None:
    op.execute(_test_types.delete().where(_test_types.c.name == _CASE_INSENSITIVE["name"]))
    op.execute(_test_types.update().where(_test_types.c.name == "BLEU").values(**_BLEU_BEFORE))
