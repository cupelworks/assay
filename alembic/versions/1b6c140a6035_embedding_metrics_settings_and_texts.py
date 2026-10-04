"""give BERTScore and Cosine Similarity their real settings and plain texts,
and seed Cosine Similarity (multilingual)

Revision ID: 1b6c140a6035
Revises: 6774a3279590
Create Date: 2026-10-01 03:00:00.000000

BERTScore now scores with the engine's own implementation of the reference
algorithm, whose row names the model, the hidden layer read (5 for
distilbert-base-uncased) and the reference implementation's rescaling
baseline for that model and layer — in place of `"rescale": false`. Raw
scores bunch up (an unrelated sentence scored 0.68); rescaled, an unrelated
answer is near 0 and an identical one 1.

Both rows' texts now say, in plain words, what they check and what they
can't — closeness of meaning is not correctness — and their thresholds get
a placeholder and a hint with the scores to expect, measured on sample
answers.

Cosine Similarity (multilingual) is the same engine with a multilingual
model: German, French and Italian paraphrases scored 0.94–0.96, and an
Italian answer to an English expected text 0.88 (the English model: 0.29).
The downgrade deletes it by name (it fails, as it should, while a test
still has it assigned) and restores both rows as they were.

"""
import uuid
from collections.abc import Sequence
from datetime import datetime

import sqlalchemy as sa

from alembic import op

revision: str = '1b6c140a6035'
down_revision: str | None = '6774a3279590'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_REFERENCE = {"key": "reference", "label": "Reference text", "kind": "reference",
              "required": True}
_THRESHOLD_0_1 = {"key": "threshold", "label": "Minimum score to pass", "kind": "numeric",
                  "required": True, "min": 0.0, "max": 1.0}
_THRESHOLD_MINUS_1_1 = {**_THRESHOLD_0_1, "min": -1.0}
_NOT_CORRECTNESS = ("Measures closeness of meaning, not correctness: an answer saying the "
                    "opposite about the same thing can still score high.")

_BEFORE = {
    "BERTScore": {
        "description": "Measures semantic similarity using BERT embeddings.",
        "best_for": "Q&A and tasks where paraphrasing is acceptable.",
        "limitations": "Requires a BERT model; scores can be hard to interpret without a "
                       "baseline.",
        "engine_settings": {"model": "distilbert-base-uncased", "measure": "f1",
                            "rescale": False},
        "config_fields": [_REFERENCE, _THRESHOLD_0_1],
    },
    "Cosine Similarity": {
        "description": "Measures semantic similarity between output and reference using "
                       "vector embeddings.",
        "best_for": "RAG evaluation and semantic search tasks.",
        "limitations": "Requires an embedding model; sensitive to the quality of embeddings "
                       "used.",
        "engine_settings": {"model": "all-MiniLM-L6-v2"},
        "config_fields": [_REFERENCE, _THRESHOLD_MINUS_1_1],
    },
}
_AFTER = {
    "BERTScore": {
        "description": "Checks how closely the answer's words match the expected text's in "
                       "meaning, word by word, so synonyms and rephrasings count as matches.",
        "best_for": "Answers that should make the same points as the expected text in "
                    "similar, not identical, words.",
        "limitations": "Rewards similar wording on the same topic, not correctness: an answer "
                       "saying the opposite can still score high. English only. A very short "
                       "or unrelated answer can score slightly below 0.",
        "engine_settings": {
            "model": "distilbert-base-uncased",
            "layer": 5,
            "measure": "f1",
            "baseline": {"precision": 0.6666033864021301, "recall": 0.6666046380996704,
                         "f1": 0.6662048697471619},
        },
        "config_fields": [_REFERENCE, {
            **_THRESHOLD_0_1,
            "placeholder": "0.6",
            "hint": "Word-by-word closeness of meaning. A close paraphrase scores about 0.75, "
                    "an unrelated answer about 0.",
        }],
    },
    "Cosine Similarity": {
        "description": "Checks whether the answer means the same as the expected text, "
                       "comparing the meaning of the two texts as a whole, however "
                       "differently they're worded.",
        "best_for": "Answers in English that may be worded very differently but must say the "
                    "same thing.",
        "limitations": f"{_NOT_CORRECTNESS} English only: use Cosine Similarity "
                       "(multilingual) for other languages. Only about the first 200 words of "
                       "each text count.",
        "engine_settings": {"model": "all-MiniLM-L6-v2"},
        "config_fields": [_REFERENCE, {
            **_THRESHOLD_MINUS_1_1,
            "placeholder": "0.7",
            "hint": "Closeness of meaning. A close paraphrase scores about 0.95, an unrelated "
                    "answer under 0.1.",
        }],
    },
}
_MULTILINGUAL = {
    "name": "Cosine Similarity (multilingual)",
    "description": "Checks whether the answer means the same as the expected text, as a "
                   "whole, in about 50 languages including German, French and Italian — even "
                   "when the answer and the expected text are in different languages.",
    "best_for": "Answers in languages other than English, or in a different language from "
                "the expected text.",
    "limitations": f"{_NOT_CORRECTNESS} Only about the first 100 words of each text count. "
                   "Its model is larger, so the first check on a worker takes longer.",
    "engine_settings": {"model": "paraphrase-multilingual-MiniLM-L12-v2"},
    "config_fields": [_REFERENCE, {
        **_THRESHOLD_MINUS_1_1,
        "placeholder": "0.7",
        "hint": "Closeness of meaning, in any of its languages. A close paraphrase scores "
                "about 0.95, an unrelated answer about 0.",
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


def _rewrite(rows: dict[str, dict]) -> None:
    for name, values in rows.items():
        op.execute(_test_types.update().where(_test_types.c.name == name).values(**values))


def upgrade() -> None:
    _rewrite(_AFTER)
    op.bulk_insert(_test_types, [{
        "id": uuid.uuid4(),
        "category": "nlp_metric",
        "cost": "fast",
        "engine": "embedding_cosine",
        "comparison": "gte",
        "is_active": True,
        "created_at": datetime.now().astimezone(),
        **_MULTILINGUAL,
    }])


def downgrade() -> None:
    op.execute(_test_types.delete().where(_test_types.c.name == _MULTILINGUAL["name"]))
    _rewrite(_BEFORE)
