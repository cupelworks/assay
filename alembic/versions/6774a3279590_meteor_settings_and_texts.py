# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Francesco Campanile
"""give METEOR NLTK's parameters and plain texts, and seed METEOR (balanced)

Revision ID: 6774a3279590
Revises: ad28dd68006e
Create Date: 2026-10-01 00:30:00.000000

The METEOR engine now scores with NLTK's METEOR, whose parameters the row
records as the library names them — `alpha` 0.9, `beta` 3.0, `gamma` 0.5 —
in place of the empty `{}`. Its texts say, in plain words, what METEOR
checks — how much of the expected text the answer covers, accepting
synonyms and other forms of a word, with extra content barely counted —
and what it can't do, and its threshold gets a placeholder and a hint with
the scores to expect, measured on sample answers.

METEOR (balanced) is the same engine with `alpha` 0.5: missing information
and extra content lower the score equally, so a padded answer scores lower
(0.85 → 0.57 for one that covers everything and adds two sentences). A
second row, no code; the two descriptions are written to be read side by
side. The downgrade deletes it by name (it fails, as it should, while a
test still has it assigned) and restores METEOR as it was.

"""
import uuid
from collections.abc import Sequence
from datetime import datetime

import sqlalchemy as sa

from alembic import op

revision: str = '6774a3279590'
down_revision: str | None = 'ad28dd68006e'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_REFERENCE = {"key": "reference", "label": "Reference text", "kind": "reference",
              "required": True}
_THRESHOLD = {"key": "threshold", "label": "Minimum score to pass", "kind": "numeric",
              "required": True, "min": 0.0, "max": 1.0}

_BEFORE = {
    "description": "Measures alignment between output and reference, accounting for synonyms "
                   "and stemming.",
    "best_for": "Tasks where paraphrasing and word variations are common.",
    "limitations": "More complex to compute than BLEU/ROUGE; language support varies.",
    "engine_settings": {},
    "config_fields": [_REFERENCE, _THRESHOLD],
}
_AFTER = {
    "description": "Checks how much of the expected text the answer covers, accepting "
                   "synonyms and other forms of a word (\"select\" for \"choose\", "
                   "\"arrives\" for \"arrive\"). Extra content in the answer barely lowers "
                   "the score.",
    "best_for": "Answers that must include the expected information, even when they reword "
                "it or say more.",
    "limitations": "Synonyms and word forms are English only: in other languages mostly exact "
                   "words match. Words in a different order lower the score a little. An "
                   "identical answer scores 0.9999, not 1, so a threshold of 1 never passes.",
    "engine_settings": {"alpha": 0.9, "beta": 3.0, "gamma": 0.5},
    "config_fields": [_REFERENCE, {
        **_THRESHOLD,
        "placeholder": "0.5",
        "hint": "Shared words, counting other word forms and synonyms. A close paraphrase "
                "scores about 0.6, an unrelated answer about 0.1.",
    }],
}
_BALANCED = {
    "name": "METEOR (balanced)",
    "description": "Checks both that the answer covers the expected text and that it doesn't "
                   "add much else, accepting synonyms and other forms of a word. Missing "
                   "information and extra content lower the score equally.",
    "best_for": "Concise answers: all the expected information, reworded if needed, without "
                "padding.",
    "limitations": "A correct answer with a helpful extra sentence also scores lower. Synonyms "
                   "and word forms are English only. An identical answer scores 0.9999, not 1, "
                   "so a threshold of 1 never passes.",
    "engine_settings": {"alpha": 0.5, "beta": 3.0, "gamma": 0.5},
    "config_fields": [_REFERENCE, {
        **_THRESHOLD,
        "placeholder": "0.5",
        "hint": "Shared words, counting other word forms and synonyms; extra content lowers it "
                "too. A close paraphrase scores about 0.6, an unrelated answer about 0.1.",
    }],
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
    op.execute(_test_types.update().where(_test_types.c.name == "METEOR").values(**_AFTER))
    op.bulk_insert(_test_types, [{
        "id": uuid.uuid4(),
        "category": "nlp_metric",
        "cost": "fast",
        "engine": "meteor",
        "comparison": "gte",
        "is_active": True,
        "created_at": datetime.now().astimezone(),
        **_BALANCED,
    }])


def downgrade() -> None:
    op.execute(_test_types.delete().where(_test_types.c.name == _BALANCED["name"]))
    op.execute(_test_types.update().where(_test_types.c.name == "METEOR").values(**_BEFORE))
