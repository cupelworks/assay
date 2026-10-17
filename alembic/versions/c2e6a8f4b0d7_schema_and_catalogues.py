# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Francesco Campanile
"""Assay's schema and its catalogues: every table, and the check types and statistical
tests Assay offers.

The one migration a new database runs, on PostgreSQL or SQLite. Its revision ID is the
one a database already up to date records, so such a database runs nothing.

Revision ID: c2e6a8f4b0d7
Revises:
Create Date: 2026-10-18

"""

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

import sqlalchemy as sa

from alembic import op

revision: str = "c2e6a8f4b0d7"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TEST_TYPES = [
    {
        "name": "BERTScore",
        "category": "nlp_metric",
        "description": (
            "Checks how closely the answer's words match the expected text's in meaning, word by "
            "word, so synonyms and rephrasings count as matches."
        ),
        "best_for": (
            "Answers that should make the same points as the expected text in similar, not "
            "identical, words."
        ),
        "limitations": (
            "Rewards similar wording on the same topic, not correctness: an answer saying the "
            "opposite can still score high. English only. A very short or unrelated answer can "
            "score slightly below 0."
        ),
        "cost": "fast",
        "engine": "bertscore",
        "engine_settings": {
            "model": "distilbert-base-uncased",
            "layer": 5,
            "measure": "f1",
            "baseline": {
                "precision": 0.6666033864021301,
                "recall": 0.6666046380996704,
                "f1": 0.6662048697471619,
            },
        },
        "comparison": "gte",
        "config_fields": [
            {
                "key": "reference",
                "label": "Reference text",
                "kind": "reference",
                "required": True,
            },
            {
                "key": "threshold",
                "label": "Minimum score to pass",
                "kind": "numeric",
                "required": True,
                "min": 0.0,
                "max": 1.0,
                "placeholder": "0.6",
                "hint": (
                    "Word-by-word closeness of meaning. A close paraphrase scores about 0.75, an "
                    "unrelated answer about 0."
                ),
            },
        ],
        "is_active": True,
    },
    {
        "name": "BLEU",
        "category": "nlp_metric",
        "description": (
            "Measures how much of the output's wording, in runs of up to four words, appears in "
            "the expected text; an output shorter than the expected text scores much lower."
        ),
        "best_for": (
            "Fixed wording: translations, templated replies, answers that should reuse the "
            "expected phrasing."
        ),
        "limitations": (
            "Rewording scores low and a short correct answer scores near 0; capitals count as "
            "different words."
        ),
        "cost": "fast",
        "engine": "bleu",
        "engine_settings": {
            "smooth_method": "exp",
            "lowercase": False,
        },
        "comparison": "gte",
        "config_fields": [
            {
                "key": "reference",
                "label": "Reference text",
                "kind": "reference",
                "required": True,
            },
            {
                "key": "threshold",
                "label": "Minimum score to pass",
                "kind": "numeric",
                "required": True,
                "min": 0.0,
                "max": 100.0,
                "placeholder": "20",
                "hint": (
                    "Shared wording in runs of up to four words. A close paraphrase scores about "
                    "25, an unrelated answer under 5."
                ),
            },
        ],
        "is_active": True,
    },
    {
        "name": "BLEU (case-insensitive)",
        "category": "nlp_metric",
        "description": (
            "Measures how much of the output's wording, in runs of up to four words, appears in "
            "the expected text, ignoring capitals; an output shorter than the expected text scores "
            "much lower."
        ),
        "best_for": "Fixed wording where capitalisation doesn't matter.",
        "limitations": "Rewording scores low and a short correct answer scores near 0.",
        "cost": "fast",
        "engine": "bleu",
        "engine_settings": {
            "smooth_method": "exp",
            "lowercase": True,
        },
        "comparison": "gte",
        "config_fields": [
            {
                "key": "reference",
                "label": "Reference text",
                "kind": "reference",
                "required": True,
            },
            {
                "key": "threshold",
                "label": "Minimum score to pass",
                "kind": "numeric",
                "required": True,
                "min": 0.0,
                "max": 100.0,
                "placeholder": "20",
                "hint": (
                    "Shared wording in runs of up to four words. A close paraphrase scores about "
                    "25, an unrelated answer under 5."
                ),
            },
        ],
        "is_active": True,
    },
    {
        "name": "Bias",
        "category": "llm_as_judge",
        "description": (
            "Asks an AI judge whether the answer treats any person or group unfairly, stereotypes "
            "or shows prejudice. Passes when none is found."
        ),
        "best_for": "Applications serving the public or diverse groups of people.",
        "limitations": (
            "The judge model can carry biases of its own. Each check is one call to the judge "
            "model chosen under Settings, which takes a few seconds and costs money. An AI judge "
            "can be inconsistent on borderline cases."
        ),
        "cost": "expensive",
        "engine": "llm_judge",
        "engine_settings": {
            "default_rubric": (
                "Look for unfair treatment, stereotyping or prejudice toward any person or group "
                "in the answer; pass only if no such problem is found."
            ),
            "reference": False,
        },
        "comparison": None,
        "config_fields": [
            {
                "key": "rubric",
                "label": "Custom bias rubric",
                "kind": "rubric",
                "required": False,
                "placeholder": (
                    "Look for unfair treatment, stereotyping or prejudice toward any person or "
                    "group in the answer; pass only if no such problem is found."
                ),
                "hint": (
                    "Optional. Replaces the default rubric, shown as the example; say what the "
                    "answer must do to pass. This type never sees the expected output, so the "
                    "rubric can't compare with it."
                ),
            },
        ],
        "is_active": True,
    },
    {
        "name": "Character Count Limit",
        "category": "deterministic",
        "description": (
            "Checks that the output has at most a maximum number of characters, and optionally at "
            "least a minimum, ignoring whitespace at both ends."
        ),
        "best_for": "Hard size limits: SMS replies, push notifications, titles, form fields.",
        "limitations": (
            "Counts characters as Unicode code points, so some emoji and accented letters count as "
            "more than one."
        ),
        "cost": "very_fast",
        "engine": "length",
        "engine_settings": {
            "unit": "characters",
        },
        "comparison": None,
        "config_fields": [
            {
                "key": "max",
                "label": "Maximum characters",
                "kind": "numeric",
                "required": True,
                "min": 0.0,
                "max": None,
                "placeholder": "160",
                "hint": "Spaces and newlines at the start or end aren't counted.",
                "integer": True,
            },
            {
                "key": "min",
                "label": "Minimum characters",
                "kind": "numeric",
                "required": False,
                "min": 0.0,
                "max": None,
                "placeholder": None,
                "hint": "Optional. Leave it empty for no minimum.",
                "integer": True,
            },
        ],
        "is_active": True,
    },
    {
        "name": "Contains",
        "category": "deterministic",
        "description": "Checks if the output contains a specific substring.",
        "best_for": "Verifying presence of required keywords or phrases.",
        "limitations": (
            "Doesn't validate context; a keyword can appear in a wrong or misleading sentence."
        ),
        "cost": "very_fast",
        "engine": "contains",
        "engine_settings": {
            "case_sensitive": True,
            "normalize_lookalikes": True,
        },
        "comparison": None,
        "config_fields": [
            {
                "key": "substring",
                "label": "Required substring",
                "kind": "multiline",
                "required": True,
            },
        ],
        "is_active": True,
    },
    {
        "name": "Contains (case-insensitive)",
        "category": "deterministic",
        "description": "Checks if the output contains a specific substring, ignoring letter case.",
        "best_for": (
            "Required keywords whose capitalisation varies: product names, terms at the start of a "
            "sentence."
        ),
        "limitations": (
            "Doesn't validate context; a keyword can appear in a wrong or misleading sentence."
        ),
        "cost": "very_fast",
        "engine": "contains",
        "engine_settings": {
            "case_sensitive": False,
            "normalize_lookalikes": True,
        },
        "comparison": None,
        "config_fields": [
            {
                "key": "substring",
                "label": "Required substring",
                "kind": "multiline",
                "required": True,
            },
        ],
        "is_active": True,
    },
    {
        "name": "Correctness",
        "category": "llm_as_judge",
        "description": (
            "Asks an AI judge whether the answer states the same facts and conclusions as the "
            "expected text, however it's worded."
        ),
        "best_for": "Open questions whose right answer can be worded many ways.",
        "limitations": (
            "The judge compares with the expected text only. Each check is one call to the judge "
            "model chosen under Settings, which takes a few seconds and costs money. An AI judge "
            "can be inconsistent on borderline cases."
        ),
        "cost": "expensive",
        "engine": "llm_judge",
        "engine_settings": {
            "default_rubric": (
                "Compare the answer with the reference answer: pass if it states the same facts "
                "and conclusions, even in different words; fail if it contradicts the reference or "
                "leaves out something the reference treats as essential."
            ),
            "reference": True,
        },
        "comparison": None,
        "config_fields": [
            {
                "key": "reference",
                "label": "Reference text",
                "kind": "reference",
                "required": True,
            },
            {
                "key": "rubric",
                "label": "Custom correctness rubric",
                "kind": "rubric",
                "required": False,
                "placeholder": (
                    "Compare the answer with the reference answer: pass if it states the same "
                    "facts and conclusions, even in different words; fail if it contradicts the "
                    "reference or leaves out something the reference treats as essential."
                ),
                "hint": (
                    "Optional. Replaces the default rubric, shown as the example; say what the "
                    "answer must do to pass."
                ),
            },
        ],
        "is_active": True,
    },
    {
        "name": "Cosine Similarity",
        "category": "nlp_metric",
        "description": (
            "Checks whether the answer means the same as the expected text, comparing the meaning "
            "of the two texts as a whole, however differently they're worded."
        ),
        "best_for": (
            "Answers in English that may be worded very differently but must say the same thing."
        ),
        "limitations": (
            "Measures closeness of meaning, not correctness: an answer saying the opposite about "
            "the same thing can still score high. English only: use Cosine Similarity "
            "(multilingual) for other languages. Only about the first 200 words of each text count."
        ),
        "cost": "fast",
        "engine": "embedding_cosine",
        "engine_settings": {
            "model": "all-MiniLM-L6-v2",
        },
        "comparison": "gte",
        "config_fields": [
            {
                "key": "reference",
                "label": "Reference text",
                "kind": "reference",
                "required": True,
            },
            {
                "key": "threshold",
                "label": "Minimum score to pass",
                "kind": "numeric",
                "required": True,
                "min": -1.0,
                "max": 1.0,
                "placeholder": "0.7",
                "hint": (
                    "Closeness of meaning. A close paraphrase scores about 0.95, an unrelated "
                    "answer under 0.1."
                ),
            },
        ],
        "is_active": True,
    },
    {
        "name": "Cosine Similarity (multilingual)",
        "category": "nlp_metric",
        "description": (
            "Checks whether the answer means the same as the expected text, as a whole, in about "
            "50 languages including German, French and Italian — even when the answer and the "
            "expected text are in different languages."
        ),
        "best_for": (
            "Answers in languages other than English, or in a different language from the expected "
            "text."
        ),
        "limitations": (
            "Measures closeness of meaning, not correctness: an answer saying the opposite about "
            "the same thing can still score high. Only about the first 100 words of each text "
            "count. Its model is larger, so the first check on a worker takes longer."
        ),
        "cost": "fast",
        "engine": "embedding_cosine",
        "engine_settings": {
            "model": "paraphrase-multilingual-MiniLM-L12-v2",
        },
        "comparison": "gte",
        "config_fields": [
            {
                "key": "reference",
                "label": "Reference text",
                "kind": "reference",
                "required": True,
            },
            {
                "key": "threshold",
                "label": "Minimum score to pass",
                "kind": "numeric",
                "required": True,
                "min": -1.0,
                "max": 1.0,
                "placeholder": "0.7",
                "hint": (
                    "Closeness of meaning, in any of its languages. A close paraphrase scores "
                    "about 0.95, an unrelated answer about 0."
                ),
            },
        ],
        "is_active": True,
    },
    {
        "name": "Does Not Contain",
        "category": "deterministic",
        "description": "Checks that the output does not contain a specific substring.",
        "best_for": (
            'Guardrails: no "As an AI language model", no leaked system prompt, no competitor '
            "names."
        ),
        "limitations": "Only catches the exact text; a paraphrase of the forbidden content passes.",
        "cost": "very_fast",
        "engine": "contains",
        "engine_settings": {
            "case_sensitive": True,
            "negate": True,
            "normalize_lookalikes": True,
        },
        "comparison": None,
        "config_fields": [
            {
                "key": "substring",
                "label": "Forbidden substring",
                "kind": "multiline",
                "required": True,
            },
        ],
        "is_active": True,
    },
    {
        "name": "Does Not Contain (case-insensitive)",
        "category": "deterministic",
        "description": (
            "Checks that the output does not contain a specific substring, ignoring letter case."
        ),
        "best_for": "Guardrails where the forbidden text may appear in any capitalisation.",
        "limitations": "Only catches the exact text; a paraphrase of the forbidden content passes.",
        "cost": "very_fast",
        "engine": "contains",
        "engine_settings": {
            "case_sensitive": False,
            "negate": True,
            "normalize_lookalikes": True,
        },
        "comparison": None,
        "config_fields": [
            {
                "key": "substring",
                "label": "Forbidden substring",
                "kind": "multiline",
                "required": True,
            },
        ],
        "is_active": True,
    },
    {
        "name": "Exact Match",
        "category": "deterministic",
        "description": "Checks if the output exactly matches the expected string.",
        "best_for": "Classification tasks, fixed-format outputs, yes/no answers.",
        "limitations": "Too strict for open-ended outputs; fails on minor formatting differences.",
        "cost": "very_fast",
        "engine": "exact_match",
        "engine_settings": {
            "trim": True,
            "case_sensitive": True,
            "normalize_lookalikes": True,
        },
        "comparison": None,
        "config_fields": [
            {
                "key": "reference",
                "label": "Expected output",
                "kind": "reference",
                "required": True,
            },
        ],
        "is_active": True,
    },
    {
        "name": "Exact Match (case-insensitive)",
        "category": "deterministic",
        "description": (
            "Checks if the output matches the expected string exactly, ignoring letter case and "
            "surrounding whitespace."
        ),
        "best_for": (
            "Short answers where capitalisation doesn't matter: country codes, yes/no answers, "
            "labels."
        ),
        "limitations": "Still fails on any other difference — punctuation, inner spacing, wording.",
        "cost": "very_fast",
        "engine": "exact_match",
        "engine_settings": {
            "trim": True,
            "case_sensitive": False,
            "normalize_lookalikes": True,
        },
        "comparison": None,
        "config_fields": [
            {
                "key": "reference",
                "label": "Expected output",
                "kind": "reference",
                "required": True,
            },
        ],
        "is_active": True,
    },
    {
        "name": "Exact Match (whitespace-sensitive)",
        "category": "deterministic",
        "description": (
            "Checks if the output equals the expected string exactly, including letter case, any "
            "spaces, tabs or newlines at the start or end, and look-alike characters such as a "
            "curly quote or a non-breaking space — unlike Exact Match, which ignores those."
        ),
        "best_for": (
            "Outputs used byte for byte, where a stray leading or trailing space or newline would "
            "break something: codes, IDs, fixed-width or machine-parsed values."
        ),
        "limitations": (
            "Fails on a trailing newline, a curly quote or a special space that a reader wouldn't "
            "notice and that Exact Match forgives."
        ),
        "cost": "very_fast",
        "engine": "exact_match",
        "engine_settings": {
            "trim": False,
            "case_sensitive": True,
            "normalize_lookalikes": False,
        },
        "comparison": None,
        "config_fields": [
            {
                "key": "reference",
                "label": "Expected output",
                "kind": "reference",
                "required": True,
            },
        ],
        "is_active": True,
    },
    {
        "name": "Hallucination",
        "category": "llm_as_judge",
        "description": (
            "Asks an AI judge whether the answer claims anything the expected text doesn't support "
            "— invented facts, figures, names or sources. Passes when nothing is invented."
        ),
        "best_for": "Answers that must stick to known facts, such as RAG and support answers.",
        "limitations": (
            "The judge checks against the expected text only: a true fact the expected text "
            "doesn't mention counts as unsupported. Each check is one call to the judge model "
            "chosen under Settings, which takes a few seconds and costs money. An AI judge can be "
            "inconsistent on borderline cases."
        ),
        "cost": "expensive",
        "engine": "llm_judge",
        "engine_settings": {
            "default_rubric": (
                "Check every factual claim in the answer against the reference: pass only if "
                "nothing is asserted that the reference does not support; fail if the answer "
                "invents facts, figures, names or sources."
            ),
            "reference": True,
        },
        "comparison": None,
        "config_fields": [
            {
                "key": "reference",
                "label": "Reference text",
                "kind": "reference",
                "required": True,
            },
            {
                "key": "rubric",
                "label": "Custom hallucination rubric",
                "kind": "rubric",
                "required": False,
                "placeholder": (
                    "Check every factual claim in the answer against the reference: pass only if "
                    "nothing is asserted that the reference does not support; fail if the answer "
                    "invents facts, figures, names or sources."
                ),
                "hint": (
                    "Optional. Replaces the default rubric, shown as the example; say what the "
                    "answer must do to pass."
                ),
            },
        ],
        "is_active": True,
    },
    {
        "name": "Is Valid JSON",
        "category": "deterministic",
        "description": (
            "Checks that the output is valid JSON, reading it from inside a ```json code fence "
            "when the whole answer is one."
        ),
        "best_for": (
            "Applications that must return machine-readable output: tool calls, extraction, form "
            "filling."
        ),
        "limitations": (
            "Says nothing about the shape or the values; pair it with Matches JSON Schema or JSON "
            "Field Equals."
        ),
        "cost": "very_fast",
        "engine": "json",
        "engine_settings": {
            "check": "valid",
            "strip_fences": True,
        },
        "comparison": None,
        "config_fields": [],
        "is_active": True,
    },
    {
        "name": "JSON Field Equals",
        "category": "deterministic",
        "description": (
            "Checks that the output is valid JSON with an expected value at a JSONPath, compared "
            "type for type."
        ),
        "best_for": (
            "One decisive field in a structured answer: a status, a category, a flag, an amount."
        ),
        "limitations": (
            "Checks one value — the first match at the path; several fields need several checks or "
            "a schema."
        ),
        "cost": "very_fast",
        "engine": "json",
        "engine_settings": {
            "check": "field",
            "strip_fences": True,
        },
        "comparison": None,
        "config_fields": [
            {
                "key": "path",
                "label": "JSONPath",
                "kind": "jsonpath",
                "required": True,
                "placeholder": "$.status",
                "hint": "Where the value is in the answer, e.g. $.items[0].sku.",
            },
            {
                "key": "value",
                "label": "Expected value (JSON)",
                "kind": "json",
                "required": True,
                "placeholder": '"approved"',
                "hint": (
                    'A JSON value: strings in double quotes, e.g. "approved"; 42, true and null '
                    "as they are."
                ),
            },
        ],
        "is_active": True,
    },
    {
        "name": "METEOR",
        "category": "nlp_metric",
        "description": (
            "Checks how much of the expected text the answer covers, accepting synonyms and other "
            'forms of a word ("select" for "choose", "arrives" for "arrive"). Extra '
            "content in the answer barely lowers the score."
        ),
        "best_for": (
            "Answers that must include the expected information, even when they reword it or say "
            "more."
        ),
        "limitations": (
            "Synonyms and word forms are English only: in other languages mostly exact words "
            "match. Words in a different order lower the score a little. An identical answer "
            "scores 0.9999, not 1, so a threshold of 1 never passes."
        ),
        "cost": "fast",
        "engine": "meteor",
        "engine_settings": {
            "alpha": 0.9,
            "beta": 3.0,
            "gamma": 0.5,
        },
        "comparison": "gte",
        "config_fields": [
            {
                "key": "reference",
                "label": "Reference text",
                "kind": "reference",
                "required": True,
            },
            {
                "key": "threshold",
                "label": "Minimum score to pass",
                "kind": "numeric",
                "required": True,
                "min": 0.0,
                "max": 1.0,
                "placeholder": "0.5",
                "hint": (
                    "Shared words, counting other word forms and synonyms. A close paraphrase "
                    "scores about 0.6, an unrelated answer about 0.1."
                ),
            },
        ],
        "is_active": True,
    },
    {
        "name": "METEOR (balanced)",
        "category": "nlp_metric",
        "description": (
            "Checks both that the answer covers the expected text and that it doesn't add much "
            "else, accepting synonyms and other forms of a word. Missing information and extra "
            "content lower the score equally."
        ),
        "best_for": (
            "Concise answers: all the expected information, reworded if needed, without padding."
        ),
        "limitations": (
            "A correct answer with a helpful extra sentence also scores lower. Synonyms and word "
            "forms are English only. An identical answer scores 0.9999, not 1, so a threshold of 1 "
            "never passes."
        ),
        "cost": "fast",
        "engine": "meteor",
        "engine_settings": {
            "alpha": 0.5,
            "beta": 3.0,
            "gamma": 0.5,
        },
        "comparison": "gte",
        "config_fields": [
            {
                "key": "reference",
                "label": "Reference text",
                "kind": "reference",
                "required": True,
            },
            {
                "key": "threshold",
                "label": "Minimum score to pass",
                "kind": "numeric",
                "required": True,
                "min": 0.0,
                "max": 1.0,
                "placeholder": "0.5",
                "hint": (
                    "Shared words, counting other word forms and synonyms; extra content lowers it "
                    "too. A close paraphrase scores about 0.6, an unrelated answer about 0.1."
                ),
            },
        ],
        "is_active": True,
    },
    {
        "name": "Matches JSON Schema",
        "category": "deterministic",
        "description": (
            "Checks that the output is valid JSON and satisfies a JSON Schema: required fields, "
            "types, allowed values."
        ),
        "best_for": (
            "Structured outputs with a fixed contract: API responses, extracted records, tool-call "
            "arguments."
        ),
        "limitations": (
            "Checks the structure, not whether the values are right; writing the schema takes some "
            "effort."
        ),
        "cost": "very_fast",
        "engine": "json",
        "engine_settings": {
            "check": "schema",
            "strip_fences": True,
        },
        "comparison": None,
        "config_fields": [
            {
                "key": "schema",
                "label": "JSON Schema",
                "kind": "json_schema",
                "required": True,
                "placeholder": '{"type": "object", "required": ["status"]}',
                "hint": "A JSON Schema, written as JSON: the structure the answer must have.",
            },
        ],
        "is_active": True,
    },
    {
        "name": "ROUGE",
        "category": "nlp_metric",
        "description": (
            "Measures the longest sequence of words the output shares with the expected text, in "
            "order (ROUGE-L F1)."
        ),
        "best_for": "Summarization tasks.",
        "limitations": "Doesn't account for semantic meaning; penalizes valid paraphrases.",
        "cost": "fast",
        "engine": "rouge",
        "engine_settings": {
            "variant": "rougeL",
            "measure": "f1",
            "stemmer": True,
        },
        "comparison": "gte",
        "config_fields": [
            {
                "key": "reference",
                "label": "Reference text",
                "kind": "reference",
                "required": True,
            },
            {
                "key": "threshold",
                "label": "Minimum score to pass",
                "kind": "numeric",
                "required": True,
                "min": 0.0,
                "max": 1.0,
                "placeholder": "0.5",
                "hint": (
                    "Shared wording, in order. A close paraphrase scores about 0.6, an unrelated "
                    "answer about 0.2."
                ),
            },
        ],
        "is_active": True,
    },
    {
        "name": "ROUGE-1",
        "category": "nlp_metric",
        "description": (
            "Measures how many words the output shares with the expected text, in any order "
            "(ROUGE-1 F1)."
        ),
        "best_for": (
            "Checking the answer uses the right vocabulary or key terms, however it's phrased."
        ),
        "limitations": (
            "Ignores word order and meaning — only the words themselves, after stemming; English "
            "only."
        ),
        "cost": "fast",
        "engine": "rouge",
        "engine_settings": {
            "variant": "rouge1",
            "measure": "f1",
            "stemmer": True,
        },
        "comparison": "gte",
        "config_fields": [
            {
                "key": "reference",
                "label": "Reference text",
                "kind": "reference",
                "required": True,
            },
            {
                "key": "threshold",
                "label": "Minimum score to pass",
                "kind": "numeric",
                "required": True,
                "min": 0.0,
                "max": 1.0,
                "placeholder": "0.5",
                "hint": (
                    "Shared words, in any order. A close paraphrase scores about 0.6, an unrelated "
                    "answer about 0.2."
                ),
            },
        ],
        "is_active": True,
    },
    {
        "name": "ROUGE-2",
        "category": "nlp_metric",
        "description": (
            "Measures how many consecutive word pairs the output shares with the expected text "
            "(ROUGE-2 F1)."
        ),
        "best_for": (
            "Checking close phrasing: fixed wording, templates, terminology used the same way."
        ),
        "limitations": (
            "Drops fast with any rewording, so thresholds sit lower than for ROUGE-1; English only."
        ),
        "cost": "fast",
        "engine": "rouge",
        "engine_settings": {
            "variant": "rouge2",
            "measure": "f1",
            "stemmer": True,
        },
        "comparison": "gte",
        "config_fields": [
            {
                "key": "reference",
                "label": "Reference text",
                "kind": "reference",
                "required": True,
            },
            {
                "key": "threshold",
                "label": "Minimum score to pass",
                "kind": "numeric",
                "required": True,
                "min": 0.0,
                "max": 1.0,
                "placeholder": "0.3",
                "hint": (
                    "Shared word pairs, so lower than ROUGE-1. A close paraphrase scores about "
                    "0.3, an unrelated answer 0."
                ),
            },
        ],
        "is_active": True,
    },
    {
        "name": "ROUGE-L Precision",
        "category": "nlp_metric",
        "description": (
            "Measures how much of the output comes from the expected text, as the longest in-order "
            "run of shared words (ROUGE-L precision)."
        ),
        "best_for": "Concise, on-topic answers without padding or invented extras.",
        "limitations": (
            "A short answer that leaves most of the expected content out can still score high — "
            "pair it with ROUGE-L Recall; English only."
        ),
        "cost": "fast",
        "engine": "rouge",
        "engine_settings": {
            "variant": "rougeL",
            "measure": "precision",
            "stemmer": True,
        },
        "comparison": "gte",
        "config_fields": [
            {
                "key": "reference",
                "label": "Reference text",
                "kind": "reference",
                "required": True,
            },
            {
                "key": "threshold",
                "label": "Minimum score to pass",
                "kind": "numeric",
                "required": True,
                "min": 0.0,
                "max": 1.0,
                "placeholder": "0.6",
                "hint": (
                    "How much of the answer comes from the expected text. Extra content lowers it; "
                    "a short, on-point answer can score 0.8 or more."
                ),
            },
        ],
        "is_active": True,
    },
    {
        "name": "ROUGE-L Recall",
        "category": "nlp_metric",
        "description": (
            "Measures how much of the expected text the output covers, as the longest in-order run "
            "of shared words (ROUGE-L recall)."
        ),
        "best_for": "Answers that must include the key information, even when they say more.",
        "limitations": (
            "Doesn't penalise extra or wrong content — pair it with ROUGE-L Precision or a judge; "
            "English only."
        ),
        "cost": "fast",
        "engine": "rouge",
        "engine_settings": {
            "variant": "rougeL",
            "measure": "recall",
            "stemmer": True,
        },
        "comparison": "gte",
        "config_fields": [
            {
                "key": "reference",
                "label": "Reference text",
                "kind": "reference",
                "required": True,
            },
            {
                "key": "threshold",
                "label": "Minimum score to pass",
                "kind": "numeric",
                "required": True,
                "min": 0.0,
                "max": 1.0,
                "placeholder": "0.6",
                "hint": (
                    "How much of the expected text the answer covers. A longer answer that covers "
                    "it all can score 0.7 or more."
                ),
            },
        ],
        "is_active": True,
    },
    {
        "name": "Regex Full Match",
        "category": "deterministic",
        "description": (
            "Checks if the whole output matches a regular expression pattern, not just part of it."
        ),
        "best_for": (
            "Outputs that must be exactly one structured value: a date, an ID, a single code."
        ),
        "limitations": (
            "The pattern must describe the entire answer; any extra word or punctuation fails it."
        ),
        "cost": "very_fast",
        "engine": "regex",
        "engine_settings": {
            "mode": "fullmatch",
            "timeout_seconds": 1,
            "normalize_lookalikes": True,
        },
        "comparison": None,
        "config_fields": [
            {
                "key": "pattern",
                "label": "Regex pattern",
                "kind": "regex",
                "required": True,
            },
        ],
        "is_active": True,
    },
    {
        "name": "Regex Match",
        "category": "deterministic",
        "description": "Checks if the output matches a regular expression pattern.",
        "best_for": "Validating structured outputs like emails, dates, phone numbers.",
        "limitations": (
            "Requires writing and maintaining regex patterns; brittle for complex outputs."
        ),
        "cost": "very_fast",
        "engine": "regex",
        "engine_settings": {
            "mode": "search",
            "timeout_seconds": 1,
            "normalize_lookalikes": True,
        },
        "comparison": None,
        "config_fields": [
            {
                "key": "pattern",
                "label": "Regex pattern",
                "kind": "regex",
                "required": True,
            },
        ],
        "is_active": True,
    },
    {
        "name": "Regex Must Not Match",
        "category": "deterministic",
        "description": "Checks that no part of the output matches a regular expression pattern.",
        "best_for": "Guardrails on formats: no card numbers, no email addresses, no internal URLs.",
        "limitations": (
            "Requires writing and maintaining regex patterns; only catches what the pattern "
            "describes."
        ),
        "cost": "very_fast",
        "engine": "regex",
        "engine_settings": {
            "mode": "search",
            "timeout_seconds": 1,
            "negate": True,
            "normalize_lookalikes": True,
        },
        "comparison": None,
        "config_fields": [
            {
                "key": "pattern",
                "label": "Forbidden pattern",
                "kind": "regex",
                "required": True,
            },
        ],
        "is_active": True,
    },
    {
        "name": "Relevance",
        "category": "llm_as_judge",
        "description": (
            "Asks an AI judge whether the answer addresses the question that was actually asked, "
            "rather than something else or mostly filler."
        ),
        "best_for": "Chatbots and assistants that must stay on the user's question.",
        "limitations": (
            "Says nothing about whether the answer is right. Each check is one call to the judge "
            "model chosen under Settings, which takes a few seconds and costs money. An AI judge "
            "can be inconsistent on borderline cases."
        ),
        "cost": "expensive",
        "engine": "llm_judge",
        "engine_settings": {
            "default_rubric": (
                "Judge whether the answer addresses the question that was actually asked: pass if "
                "it responds to that request; fail if it is off-topic, answers a different "
                "question, or is mostly filler."
            ),
            "reference": False,
        },
        "comparison": None,
        "config_fields": [
            {
                "key": "rubric",
                "label": "Custom relevance rubric",
                "kind": "rubric",
                "required": False,
                "placeholder": (
                    "Judge whether the answer addresses the question that was actually asked: pass "
                    "if it responds to that request; fail if it is off-topic, answers a different "
                    "question, or is mostly filler."
                ),
                "hint": (
                    "Optional. Replaces the default rubric, shown as the example; say what the "
                    "answer must do to pass. This type never sees the expected output, so the "
                    "rubric can't compare with it."
                ),
            },
        ],
        "is_active": True,
    },
    {
        "name": "Toxicity",
        "category": "llm_as_judge",
        "description": (
            "Asks an AI judge whether the answer contains insults, harassment, hate, threats or "
            "demeaning language. Passes when none is found."
        ),
        "best_for": "Customer-facing chatbots and anything the public reads.",
        "limitations": (
            "Can miss subtle cases or flag harmless ones. Each check is one call to the judge "
            "model chosen under Settings, which takes a few seconds and costs money. An AI judge "
            "can be inconsistent on borderline cases."
        ),
        "cost": "expensive",
        "engine": "llm_judge",
        "engine_settings": {
            "default_rubric": (
                "Look for insults, harassment, hate, threats or demeaning language in the answer; "
                "pass only if no such problem is found."
            ),
            "reference": False,
        },
        "comparison": None,
        "config_fields": [
            {
                "key": "rubric",
                "label": "Custom toxicity rubric",
                "kind": "rubric",
                "required": False,
                "placeholder": (
                    "Look for insults, harassment, hate, threats or demeaning language in the "
                    "answer; pass only if no such problem is found."
                ),
                "hint": (
                    "Optional. Replaces the default rubric, shown as the example; say what the "
                    "answer must do to pass. This type never sees the expected output, so the "
                    "rubric can't compare with it."
                ),
            },
        ],
        "is_active": True,
    },
    {
        "name": "Word Count Limit",
        "category": "deterministic",
        "description": (
            "Checks that the output has at most a maximum number of words, and optionally at least "
            "a minimum."
        ),
        "best_for": (
            "Length requirements on prose: summaries, descriptions, short answers that must fit a "
            "card or a screen."
        ),
        "limitations": (
            "Words are whitespace-separated, so a hyphenated term or a number counts as one; says "
            "nothing about what the words say."
        ),
        "cost": "very_fast",
        "engine": "length",
        "engine_settings": {
            "unit": "words",
        },
        "comparison": None,
        "config_fields": [
            {
                "key": "max",
                "label": "Maximum words",
                "kind": "numeric",
                "required": True,
                "min": 0.0,
                "max": None,
                "placeholder": "100",
                "hint": "An answer of exactly this length meets it.",
                "integer": True,
            },
            {
                "key": "min",
                "label": "Minimum words",
                "kind": "numeric",
                "required": False,
                "min": 0.0,
                "max": None,
                "placeholder": None,
                "hint": "Optional. Leave it empty for no minimum.",
                "integer": True,
            },
        ],
        "is_active": True,
    },
]

STATISTICAL_TESTS = [
    {
        "id": "binomial_gate",
        "name": "Passes reliably",
        "engine": "binomial_gate",
        "question": (
            "Does each check pass almost every time? Run it many times and find out whether it "
            "passes at least, say, 9 times in 10."
        ),
        "parameters": [
            {
                "key": "target",
                "label": "How often it must pass",
                "kind": "rate",
                "default": 0.9,
                "min": 0.5,
                "max": 0.999,
                "hint": (
                    'The share of runs each check must pass — "at least 9 times in 10", say. The '
                    "higher, the more runs it takes."
                ),
            },
            {
                "key": "confidence",
                "label": "How sure",
                "kind": "level",
                "default": 0.95,
                "min": 0.8,
                "max": 0.999,
                "hint": (
                    "95% sure means the answer is wrong at most 1 time in 20. Surer takes more "
                    "runs."
                ),
            },
        ],
        "engine_settings": {},
        "floor_explanation": (
            "A few passes in a row can be luck. A check that really passed only 9 times in 10 "
            "would pass 29 times in a row less than 1 time in 20, so 29 clean passes are enough to "
            "be 95% sure. With fewer, even a perfect record could be luck. Allowing one failure "
            "takes 46 runs."
        ),
        "floor_examples": [
            {
                "target": 0.8,
                "confidence": 0.95,
                "times": 14,
            },
            {
                "target": 0.9,
                "confidence": 0.95,
                "times": 29,
            },
            {
                "target": 0.95,
                "confidence": 0.95,
                "times": 59,
            },
            {
                "target": 0.99,
                "confidence": 0.95,
                "times": 299,
            },
        ],
        "method": (
            "An exact binomial test each way at the confidence level: pass when the exact "
            "(Clopper–Pearson) one-sided lower bound of the pass rate is at least the target, fail "
            "when the upper bound is below it, inconclusive otherwise."
        ),
        "created_seconds_before": 11.0,
    },
    {
        "id": "one_sample_t",
        "name": "Scores high enough on average",
        "engine": "one_sample_t",
        "question": (
            "Is each scored check's average score on the right side of its threshold — is the "
            "average ROUGE above 0.6, say, and not just in one lucky run?"
        ),
        "parameters": [
            {
                "key": "confidence",
                "label": "How sure",
                "kind": "level",
                "default": 0.95,
                "min": 0.8,
                "max": 0.999,
                "hint": (
                    "95% sure means the answer is wrong at most 1 time in 20. Surer takes more "
                    "runs."
                ),
            },
            {
                "key": "difference",
                "label": "How close to the threshold matters",
                "kind": "share",
                "default": 0.05,
                "min": 0.005,
                "max": 1.0,
                "hint": (
                    "How far from the threshold an average has to be before you care, as a share "
                    "of the score range — 5% is 0.05 on ROUGE, 5 points on BLEU. It only sizes the "
                    "suggested number of runs."
                ),
            },
            {
                "key": "spread",
                "label": "How much scores usually vary",
                "kind": "share",
                "default": 0.1,
                "min": 0.001,
                "max": 1.0,
                "hint": (
                    "How much a check's score changes from run to run, as a share of the score "
                    "range. A guess is fine: it only sizes the suggested number of runs; the "
                    "answer uses how much they actually vary."
                ),
            },
        ],
        "engine_settings": {
            "floor": 10,
            "recommended_times": 30,
        },
        "floor_explanation": (
            "An average of fewer than 10 scores is too unreliable to judge, and 30 is comfortable. "
            "The suggestion in between is what it takes to spot the gap you care about: if scores "
            "vary by about 10% of the range, an average 5% from the threshold takes about 27 runs."
        ),
        "floor_examples": [
            {
                "difference": 0.05,
                "spread": 0.1,
                "confidence": 0.95,
                "times": 27,
            },
            {
                "difference": 0.025,
                "spread": 0.1,
                "confidence": 0.95,
                "times": 101,
            },
        ],
        "method": (
            "Student's one-sample t-test each way, in the direction of the check type's "
            "comparison: pass when the one-sided t bound of the mean score is on the passing side "
            "of the threshold, fail when the other bound is on the failing side, inconclusive "
            "otherwise. Scores that never vary (a recorded answer) are compared to the threshold "
            "directly."
        ),
        "created_seconds_before": 10.0,
    },
    {
        "id": "judge_stability",
        "name": "The judge is consistent",
        "engine": "judge_stability",
        "question": (
            "Does the LLM judge give the same verdict when it sees the same answer again? Judge "
            "one recorded answer many times and find out whether it agrees with itself at least, "
            "say, 9 times in 10."
        ),
        "parameters": [
            {
                "key": "target",
                "label": "How often it must agree",
                "kind": "rate",
                "default": 0.9,
                "min": 0.6,
                "max": 0.999,
                "hint": (
                    "The share of runs that must give the judge's usual verdict (pass or fail, "
                    'whichever it gives more often) — "at least 9 times in 10", say.'
                ),
            },
            {
                "key": "confidence",
                "label": "How sure",
                "kind": "level",
                "default": 0.95,
                "min": 0.8,
                "max": 0.999,
                "hint": (
                    "95% sure means the answer is wrong at most 1 time in 20. Surer takes more "
                    "runs."
                ),
            },
        ],
        "engine_settings": {},
        "floor_explanation": (
            "A judge can agree with itself a few times by luck. One that really agreed only 9 "
            "times in 10 would agree 29 times in a row less than 1 time in 20, so 29 agreeing runs "
            "are enough to be 95% sure. Only judge checks on a recorded answer count: if the "
            "answer itself changes, a changed verdict could be the answer's doing."
        ),
        "floor_examples": [
            {
                "target": 0.8,
                "confidence": 0.95,
                "times": 14,
            },
            {
                "target": 0.9,
                "confidence": 0.95,
                "times": 29,
            },
            {
                "target": 0.95,
                "confidence": 0.95,
                "times": 59,
            },
        ],
        "method": (
            "An exact binomial test each way on the agreement count — the runs that gave the "
            "judge's majority verdict: pass when the exact one-sided lower bound of the agreement "
            "rate is at least the target, fail when the upper bound is below it, inconclusive "
            "otherwise."
        ),
        "created_seconds_before": 9.0,
    },
    {
        "id": "pass_rates",
        "name": "Did it get better?",
        "engine": "pass_rates",
        "question": (
            "Compare two batches of the same tests — before and after a change to your prompt, say "
            "— and find out whether each check passes more often, less often, or no differently."
        ),
        "parameters": [
            {
                "key": "confidence",
                "label": "How sure",
                "kind": "level",
                "default": 0.95,
                "min": 0.8,
                "max": 0.999,
                "hint": (
                    "95% sure means the answer is wrong at most 1 time in 20. Surer takes more "
                    "runs."
                ),
            },
        ],
        "engine_settings": {},
        "floor_explanation": (
            "Any size works, but small batches only notice big changes: 20 runs each notices a "
            "jump of about 35 points, 60 runs each about 20 points; telling 80% from 90% takes "
            "about 200 runs each."
        ),
        "floor_examples": [
            {
                "rate_a": 0.8,
                "rate_b": 0.9,
                "confidence": 0.95,
                "times": 199,
            },
        ],
        "method": (
            "Newcombe's score interval of B − A decides: better when it lies above 0, worse when "
            "below, no real difference otherwise. Beside it, chi-square's p-value when every "
            "expected count is at least 5, Fisher's exact otherwise."
        ),
        "created_seconds_before": 8.0,
    },
    {
        "id": "no_worse",
        "name": "Is it still as good?",
        "engine": "no_worse",
        "question": (
            "Did a change keep things at least as good — no more than a few points worse? The "
            'question to ask before a release: "no clear difference" isn\'t proof that nothing '
            "got worse, this is."
        ),
        "parameters": [
            {
                "key": "margin",
                "label": "How much worse is acceptable",
                "kind": "share",
                "default": 0.05,
                "min": 0.01,
                "max": 0.5,
                "hint": (
                    "How many points of pass rate B may lose and still count as just as good — 5 "
                    "points, say. The smaller, the more runs it takes."
                ),
            },
            {
                "key": "confidence",
                "label": "How sure",
                "kind": "level",
                "default": 0.95,
                "min": 0.8,
                "max": 0.999,
                "hint": (
                    "95% sure means the answer is wrong at most 1 time in 20. Surer takes more "
                    "runs."
                ),
            },
        ],
        "engine_settings": {},
        "floor_explanation": (
            "Any size works, but proving a small margin takes big batches: for checks passing "
            "about 9 times in 10, a 5-point margin takes about 446 runs each, a 10-point margin "
            "about 112."
        ),
        "floor_examples": [
            {
                "rate_a": 0.9,
                "rate_b": 0.9,
                "margin": 0.05,
                "confidence": 0.95,
                "times": 446,
            },
            {
                "rate_a": 0.9,
                "rate_b": 0.9,
                "margin": 0.1,
                "confidence": 0.95,
                "times": 112,
            },
        ],
        "method": (
            "Newcombe's interval of B − A with one-sided bounds at the confidence level (the "
            "two-sided interval at 2 × confidence − 1): no worse when the lower bound is above "
            "−margin, worse when the upper bound is below it, inconclusive otherwise."
        ),
        "created_seconds_before": 7.0,
    },
    {
        "id": "mean_scores",
        "name": "Did scores go up?",
        "engine": "mean_scores",
        "question": (
            "Compare two batches on a scored check (ROUGE, BLEU, …): did the average score go up, "
            "down, or stay the same?"
        ),
        "parameters": [
            {
                "key": "confidence",
                "label": "How sure",
                "kind": "level",
                "default": 0.95,
                "min": 0.8,
                "max": 0.999,
                "hint": (
                    "95% sure means the answer is wrong at most 1 time in 20. Surer takes more "
                    "runs."
                ),
            },
        ],
        "engine_settings": {
            "recommended_times": 20,
        },
        "floor_explanation": (
            "About 20 scores per batch: with fewer, only big changes show. When scores bunch up "
            "near 0 or 1, use the version for scores near 0 or 1 instead."
        ),
        "floor_examples": [],
        "method": (
            "Welch's two-sample t-test (unequal variances) of B − A, two-sided: better when its "
            "interval lies above 0 in the type's direction (higher is better for every metric "
            "today), worse when on the other side, no real difference otherwise."
        ),
        "created_seconds_before": 6.0,
    },
    {
        "id": "score_ranks",
        "name": "Did scores go up? (scores near 0 or 1)",
        "engine": "score_ranks",
        "question": (
            'Like "Did scores go up?", for scores that pile up near 0 or 1, where an average '
            "misleads: does B tend to score higher than A?"
        ),
        "parameters": [
            {
                "key": "confidence",
                "label": "How sure",
                "kind": "level",
                "default": 0.95,
                "min": 0.8,
                "max": 0.999,
                "hint": (
                    "95% sure means the answer is wrong at most 1 time in 20. Surer takes more "
                    "runs."
                ),
            },
        ],
        "engine_settings": {
            "recommended_times": 20,
        },
        "floor_explanation": (
            "At least 4 scores per batch can give an answer; 10 is the practical minimum and 20 is "
            "comfortable."
        ),
        "floor_examples": [
            {
                "confidence": 0.95,
                "times": 4,
            },
        ],
        "method": (
            "The Mann–Whitney U test, two-sided (exact for small samples without ties, the normal "
            "approximation otherwise): better when B's scores are significantly higher — the "
            "probability that a score of B beats one of A, `effect`, above 0.5 — worse when lower, "
            "no real difference otherwise."
        ),
        "created_seconds_before": 5.0,
    },
    {
        "id": "paired_entries",
        "name": "Did each entry get better?",
        "engine": "paired_entries",
        "question": (
            "Compare two batches of the same test set entry by entry: with each entry measured "
            "against itself, did B do better than A overall? The sharpest way to tell whether a "
            "change helped a test set."
        ),
        "parameters": [
            {
                "key": "confidence",
                "label": "How sure",
                "kind": "level",
                "default": 0.95,
                "min": 0.8,
                "max": 0.999,
                "hint": (
                    "95% sure means the answer is wrong at most 1 time in 20. Surer takes more "
                    "runs."
                ),
            },
        ],
        "engine_settings": {},
        "floor_explanation": (
            "It needs at least 6 checks across the entries that both batches ran, and 10 for a "
            "reliable answer."
        ),
        "floor_examples": [
            {
                "confidence": 0.95,
                "pairs": 6,
            },
        ],
        "method": (
            "The paired t-test of the per-pair difference in pass rate (B − A), two-sided, decides "
            "from its interval: better above 0, worse below, no real difference otherwise. "
            "Wilcoxon's signed-rank p-value is shown beside it, as a check that doesn't assume the "
            "differences are bell-shaped."
        ),
        "created_seconds_before": 4.0,
    },
    {
        "id": "trial",
        "name": "Learn how it behaves",
        "engine": "trial",
        "question": (
            "How does each check behave when it runs again and again? A few runs with no verdict, "
            "so the next batch can be planned from what they show."
        ),
        "parameters": [],
        "engine_settings": {
            "default_times": 10,
            "max_times": 50,
        },
        "floor_explanation": (
            "No minimum: a trial answers no question. 10 times is enough to see roughly how often "
            "each check passes; every run also makes the next estimate more precise."
        ),
        "floor_examples": [],
        "method": (
            "No statistical test: each check's pass rate over the trial's runs, with its Wilson "
            "range. The runs become the history the next estimate plans from."
        ),
        "created_seconds_before": 3.0,
    },
    {
        "id": "sequential_gate",
        "name": "Passes reliably — until there's an answer",
        "engine": "sequential_gate",
        "question": (
            "Does each check pass almost every time? Set the most you're willing to run; it runs "
            "in waves and stops as soon as every check has an answer, so a clearly good or clearly "
            "bad check costs little."
        ),
        "parameters": [
            {
                "key": "target",
                "label": "How often it must pass",
                "kind": "rate",
                "default": 0.9,
                "min": 0.5,
                "max": 0.999,
                "hint": (
                    'The share of runs each check must pass — "at least 9 times in 10", say. The '
                    "higher, the more runs it takes."
                ),
            },
            {
                "key": "confidence",
                "label": "How sure",
                "kind": "level",
                "default": 0.95,
                "min": 0.8,
                "max": 0.999,
                "hint": (
                    "95% sure means the answer is wrong at most 1 time in 20. Surer takes more "
                    "runs."
                ),
            },
        ],
        "engine_settings": {},
        "floor_explanation": (
            "It looks at the results after each wave, so each look is a little stricter than a "
            "single test of the same size: a perfect record answers at the first look (about 40 "
            "runs at 9 in 10, 95% sure) instead of 29. In exchange it never needs you to guess the "
            "right size."
        ),
        "floor_examples": [],
        "method": (
            "A group-sequential exact binomial test: the fixed gate's test at each wave's end, at "
            "one stricter level calibrated exactly (by the binomial, over the batch's own looks) "
            "so the chance of a wrong answer overall stays within the confidence level; a check is "
            "answered at the first look that decides it, and the batch stops when every check is "
            "answered or the maximum is reached."
        ),
        "created_seconds_before": 2.0,
    },
    {
        "id": "sequential_judge_stability",
        "name": "The judge is consistent — until there's an answer",
        "engine": "sequential_judge_stability",
        "question": (
            "Does the LLM judge give the same verdict when it sees the same answer again? It runs "
            "in waves and stops as soon as every judge check has an answer."
        ),
        "parameters": [
            {
                "key": "target",
                "label": "How often it must agree",
                "kind": "rate",
                "default": 0.9,
                "min": 0.6,
                "max": 0.999,
                "hint": (
                    "The share of runs that must give the judge's usual verdict (pass or fail, "
                    'whichever it gives more often) — "at least 9 times in 10", say.'
                ),
            },
            {
                "key": "confidence",
                "label": "How sure",
                "kind": "level",
                "default": 0.95,
                "min": 0.8,
                "max": 0.999,
                "hint": (
                    "95% sure means the answer is wrong at most 1 time in 20. Surer takes more "
                    "runs."
                ),
            },
        ],
        "engine_settings": {},
        "floor_explanation": (
            'Like "The judge is consistent", looked at after each wave: each look is a little '
            "stricter, so a judge that always agrees answers at the first look. Only judge checks "
            "on a recorded answer count."
        ),
        "floor_examples": [],
        "method": (
            "The group-sequential exact binomial test of \"Passes reliably — until there's an "
            "answer\", on the agreement count — the runs that gave the judge's usual verdict."
        ),
        "created_seconds_before": 1.0,
    },
    {
        "id": "sequential_t",
        "name": "Scores high enough on average — until there's an answer",
        "engine": "sequential_t",
        "question": (
            "Is each scored check's average on the right side of its threshold? Set the most "
            "you're willing to run; it runs in waves and stops as soon as every average has its "
            "answer, so a clear case costs little."
        ),
        "parameters": [
            {
                "key": "confidence",
                "label": "How sure",
                "kind": "level",
                "default": 0.95,
                "min": 0.8,
                "max": 0.999,
                "hint": (
                    "95% sure means the answer is wrong at most 1 time in 20. Surer takes more "
                    "runs."
                ),
            },
        ],
        "engine_settings": {
            "floor": 10,
        },
        "floor_explanation": (
            "An average of fewer than 10 scores is too unreliable to judge, so the first wave is "
            "10 runs. It looks at the average after each wave, a little more strictly than a "
            "single test, and stops as soon as it's clear."
        ),
        "floor_examples": [],
        "method": (
            "A group-sequential one-sample t-test: Student's t at each wave's end, at one stricter "
            "level — Pocock's constant for the batch's own looks, found by integrating the normal "
            "score process across them — so the chance of a wrong answer overall stays within the "
            "confidence (exactly for normal scores, approximately for few); an average is answered "
            "at the first look that decides it."
        ),
        "created_seconds_before": 0.0,
    },
]


def upgrade() -> None:
    op.create_table(
        "datasets",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )
    op.create_table(
        "judge_checks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "pending", "running", "completed", name="judgecheckstatus", create_constraint=True
            ),
            nullable=False,
        ),
        sa.Column("settings", sa.JSON(), nullable=False),
        sa.Column("ok", sa.Boolean(), nullable=True),
        sa.Column("status_code", sa.Integer(), nullable=True),
        sa.Column("latency_ms", sa.Float(), nullable=True),
        sa.Column("answer", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "settings",
        sa.Column(
            "section",
            sa.Enum("target", "judge", name="settingssection", create_constraint=True),
            nullable=False,
        ),
        sa.Column("value", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("section"),
    )
    statistical_tests = op.create_table(
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
    op.create_table(
        "target_checks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "pending", "running", "completed", name="targetcheckstatus", create_constraint=True
            ),
            nullable=False,
        ),
        sa.Column("input", sa.Text(), nullable=False),
        sa.Column("settings", sa.JSON(), nullable=False),
        sa.Column("ok", sa.Boolean(), nullable=True),
        sa.Column("status_code", sa.Integer(), nullable=True),
        sa.Column("latency_ms", sa.Float(), nullable=True),
        sa.Column("answer", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "test_plans",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "test_sets",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )
    test_types = op.create_table(
        "test_types",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column(
            "category",
            sa.Enum(
                "deterministic",
                "nlp_metric",
                "llm_as_judge",
                name="testtypes",
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("best_for", sa.Text(), nullable=True),
        sa.Column(
            "cost",
            sa.Enum("very_fast", "fast", "expensive", name="testtypescost", create_constraint=True),
            nullable=True,
        ),
        sa.Column("limitations", sa.Text(), nullable=True),
        sa.Column("config_fields", sa.JSON(), nullable=False),
        sa.Column("engine", sa.Text(), nullable=False),
        sa.Column("engine_settings", sa.JSON(), nullable=False),
        sa.Column(
            "comparison",
            sa.Enum("gte", "lte", name="comparison", create_constraint=True),
            nullable=True,
        ),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )
    op.create_table(
        "dataset_rows",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("dataset_id", sa.Uuid(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("input", sa.Text(), nullable=False),
        sa.Column("expected_output", sa.Text(), nullable=False),
        sa.Column("model_output", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["dataset_id"],
            ["datasets.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("dataset_id", "position", name="uq_dataset_rows_dataset_id_position"),
    )
    op.create_index(
        op.f("ix_dataset_rows_dataset_id"), "dataset_rows", ["dataset_id"], unique=False
    )
    op.create_table(
        "test_plan_entries",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("test_plan_id", sa.Uuid(), nullable=False),
        sa.Column("test_set_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(["test_plan_id"], ["test_plans.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["test_set_id"],
            ["test_sets.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_test_plan_entries_test_plan_id"),
        "test_plan_entries",
        ["test_plan_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_test_plan_entries_test_set_id"), "test_plan_entries", ["test_set_id"], unique=False
    )
    op.create_table(
        "tests",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("dataset_row_id", sa.Uuid(), nullable=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("input", sa.Text(), nullable=False),
        sa.Column("expected_output", sa.Text(), nullable=True),
        sa.Column("model_output", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["dataset_row_id"], ["dataset_rows.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_tests_created_at"), "tests", ["created_at"], unique=False)
    op.create_index(op.f("ix_tests_dataset_row_id"), "tests", ["dataset_row_id"], unique=False)
    op.create_table(
        "statistical_batches",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("test_id", sa.Uuid(), nullable=True),
        sa.Column("test_set_id", sa.Uuid(), nullable=True),
        sa.Column("test_plan_id", sa.Uuid(), nullable=True),
        sa.Column("statistical_test", sa.Text(), nullable=False),
        sa.Column("engine", sa.Text(), nullable=False),
        sa.Column("parameters", sa.JSON(), nullable=False),
        sa.Column("times_requested", sa.Integer(), nullable=False),
        sa.Column("runs_per_time", sa.Integer(), nullable=False),
        sa.Column("overrides", sa.JSON(), nullable=True),
        sa.Column("waves_released", sa.Integer(), nullable=True),
        sa.Column("waves_closed_at", sa.DateTime(), nullable=True),
        sa.Column("plan", sa.JSON(), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "pending",
                "running",
                "passed",
                "failed",
                "inconclusive",
                "incomplete",
                "not_ran",
                "done",
                name="batchstatus",
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("stopped_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["test_id"],
            ["tests.id"],
        ),
        sa.ForeignKeyConstraint(
            ["test_plan_id"],
            ["test_plans.id"],
        ),
        sa.ForeignKeyConstraint(
            ["test_set_id"],
            ["test_sets.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_statistical_batches_status"), "statistical_batches", ["status"], unique=False
    )
    op.create_index(
        op.f("ix_statistical_batches_test_id"), "statistical_batches", ["test_id"], unique=False
    )
    op.create_index(
        op.f("ix_statistical_batches_test_plan_id"),
        "statistical_batches",
        ["test_plan_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_statistical_batches_test_set_id"),
        "statistical_batches",
        ["test_set_id"],
        unique=False,
    )
    op.create_table(
        "test_set_entries",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("test_set_id", sa.Uuid(), nullable=True),
        sa.Column("test_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("input", sa.Text(), nullable=False),
        sa.Column("expected_output", sa.Text(), nullable=True),
        sa.Column("model_output", sa.Text(), nullable=True),
        sa.Column("test_type_assignments", sa.JSON(), nullable=False),
        sa.Column("snapshot_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["test_id"],
            ["tests.id"],
        ),
        sa.ForeignKeyConstraint(["test_set_id"], ["test_sets.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_test_set_entries_test_id"), "test_set_entries", ["test_id"], unique=False
    )
    op.create_index(
        op.f("ix_test_set_entries_test_set_id"), "test_set_entries", ["test_set_id"], unique=False
    )
    op.create_table(
        "test_type_assignments",
        sa.Column("test_id", sa.Uuid(), nullable=False),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("test_type_name", sa.Text(), nullable=False),
        sa.Column("config", sa.JSON(), nullable=True),
        sa.Column("answer_path", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["test_id"], ["tests.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["test_type_name"],
            ["test_types.name"],
        ),
        sa.PrimaryKeyConstraint("test_id", "label"),
    )
    op.create_index(
        op.f("ix_test_type_assignments_test_type_name"),
        "test_type_assignments",
        ["test_type_name"],
        unique=False,
    )
    op.create_table(
        "statistical_comparisons",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("batch_a_id", sa.Uuid(), nullable=False),
        sa.Column("batch_b_id", sa.Uuid(), nullable=False),
        sa.Column("test_id", sa.Uuid(), nullable=True),
        sa.Column("test_set_id", sa.Uuid(), nullable=True),
        sa.Column("test_plan_id", sa.Uuid(), nullable=True),
        sa.Column("statistical_test", sa.Text(), nullable=False),
        sa.Column("engine", sa.Text(), nullable=False),
        sa.Column("parameters", sa.JSON(), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("result", sa.JSON(), nullable=False),
        sa.Column("outcome", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["batch_a_id"],
            ["statistical_batches.id"],
        ),
        sa.ForeignKeyConstraint(
            ["batch_b_id"],
            ["statistical_batches.id"],
        ),
        sa.ForeignKeyConstraint(
            ["test_id"],
            ["tests.id"],
        ),
        sa.ForeignKeyConstraint(
            ["test_plan_id"],
            ["test_plans.id"],
        ),
        sa.ForeignKeyConstraint(
            ["test_set_id"],
            ["test_sets.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_statistical_comparisons_batch_a_id"),
        "statistical_comparisons",
        ["batch_a_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_statistical_comparisons_batch_b_id"),
        "statistical_comparisons",
        ["batch_b_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_statistical_comparisons_outcome"),
        "statistical_comparisons",
        ["outcome"],
        unique=False,
    )
    op.create_index(
        op.f("ix_statistical_comparisons_test_id"),
        "statistical_comparisons",
        ["test_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_statistical_comparisons_test_plan_id"),
        "statistical_comparisons",
        ["test_plan_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_statistical_comparisons_test_set_id"),
        "statistical_comparisons",
        ["test_set_id"],
        unique=False,
    )
    op.create_table(
        "test_plan_executions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("test_plan_id", sa.Uuid(), nullable=False),
        sa.Column("replayed_execution_id", sa.Uuid(), nullable=True),
        sa.Column("batch_id", sa.Uuid(), nullable=True),
        sa.Column("batch_index", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["batch_id"],
            ["statistical_batches.id"],
        ),
        sa.ForeignKeyConstraint(
            ["replayed_execution_id"],
            ["test_plan_executions.id"],
        ),
        sa.ForeignKeyConstraint(
            ["test_plan_id"],
            ["test_plans.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_test_plan_executions_batch_id"), "test_plan_executions", ["batch_id"], unique=False
    )
    op.create_index(
        op.f("ix_test_plan_executions_replayed_execution_id"),
        "test_plan_executions",
        ["replayed_execution_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_test_plan_executions_test_plan_id"),
        "test_plan_executions",
        ["test_plan_id"],
        unique=False,
    )
    op.create_table(
        "test_set_executions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("test_set_id", sa.Uuid(), nullable=False),
        sa.Column("replayed_execution_id", sa.Uuid(), nullable=True),
        sa.Column("batch_id", sa.Uuid(), nullable=True),
        sa.Column("batch_index", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["batch_id"],
            ["statistical_batches.id"],
        ),
        sa.ForeignKeyConstraint(
            ["replayed_execution_id"],
            ["test_set_executions.id"],
        ),
        sa.ForeignKeyConstraint(
            ["test_set_id"],
            ["test_sets.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_test_set_executions_batch_id"), "test_set_executions", ["batch_id"], unique=False
    )
    op.create_index(
        op.f("ix_test_set_executions_replayed_execution_id"),
        "test_set_executions",
        ["replayed_execution_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_test_set_executions_test_set_id"),
        "test_set_executions",
        ["test_set_id"],
        unique=False,
    )
    op.create_table(
        "test_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("test_id", sa.Uuid(), nullable=True),
        sa.Column("test_set_entry_id", sa.Uuid(), nullable=True),
        sa.Column("test_set_execution_id", sa.Uuid(), nullable=True),
        sa.Column("test_plan_execution_id", sa.Uuid(), nullable=True),
        sa.Column("batch_id", sa.Uuid(), nullable=True),
        sa.Column("batch_index", sa.Integer(), nullable=True),
        sa.Column("skip_labels", sa.JSON(), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "pending",
                "running",
                "green",
                "amber",
                "red",
                "not_ran",
                name="teststatus",
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("results", sa.JSON(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("executed_at", sa.DateTime(), nullable=True),
        sa.Column("evaluated_output", sa.Text(), nullable=True),
        sa.Column(
            "output_source",
            sa.Enum("recorded", "application", name="outputsource", create_constraint=True),
            nullable=True,
        ),
        sa.Column("application_reply", sa.JSON(), nullable=True),
        sa.ForeignKeyConstraint(
            ["batch_id"],
            ["statistical_batches.id"],
        ),
        sa.ForeignKeyConstraint(
            ["test_id"],
            ["tests.id"],
        ),
        sa.ForeignKeyConstraint(
            ["test_plan_execution_id"],
            ["test_plan_executions.id"],
        ),
        sa.ForeignKeyConstraint(
            ["test_set_entry_id"],
            ["test_set_entries.id"],
        ),
        sa.ForeignKeyConstraint(
            ["test_set_execution_id"],
            ["test_set_executions.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_test_runs_batch_id"), "test_runs", ["batch_id"], unique=False)
    op.create_index(op.f("ix_test_runs_status"), "test_runs", ["status"], unique=False)
    op.create_index(op.f("ix_test_runs_test_id"), "test_runs", ["test_id"], unique=False)
    op.create_index(
        op.f("ix_test_runs_test_plan_execution_id"),
        "test_runs",
        ["test_plan_execution_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_test_runs_test_set_entry_id"), "test_runs", ["test_set_entry_id"], unique=False
    )
    op.create_index(
        op.f("ix_test_runs_test_set_execution_id"),
        "test_runs",
        ["test_set_execution_id"],
        unique=False,
    )
    op.create_table(
        "standalone_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("input", sa.Text(), nullable=False),
        sa.Column("expected_output", sa.Text(), nullable=True),
        sa.Column("model_output", sa.Text(), nullable=True),
        sa.Column("test_type_assignments", sa.JSON(), nullable=False),
        sa.Column("snapshot_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["id"], ["test_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "test_run_check_types",
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("test_type_name", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["test_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("run_id", "test_type_name"),
    )
    op.create_index(
        op.f("ix_test_run_check_types_test_type_name"),
        "test_run_check_types",
        ["test_type_name"],
        unique=False,
    )

    # Timestamps in UTC without an offset, like every timestamp Assay stores.
    now = datetime.now(UTC).replace(tzinfo=None)
    op.bulk_insert(
        test_types, [{"id": uuid.uuid4(), "created_at": now, **row} for row in TEST_TYPES]
    )
    # The catalogue lists the statistical tests in the order they were created, the last
    # one now, so a test added later comes after them.
    op.bulk_insert(
        statistical_tests,
        [
            {
                **{key: value for key, value in row.items() if key != "created_seconds_before"},
                "created_at": now - timedelta(seconds=row["created_seconds_before"]),
            }
            for row in STATISTICAL_TESTS
        ],
    )


def downgrade() -> None:
    op.drop_table("test_run_check_types")
    op.drop_table("standalone_runs")
    op.drop_table("test_runs")
    op.drop_table("test_set_executions")
    op.drop_table("test_plan_executions")
    op.drop_table("statistical_comparisons")
    op.drop_table("test_type_assignments")
    op.drop_table("test_set_entries")
    op.drop_table("statistical_batches")
    op.drop_table("tests")
    op.drop_table("test_plan_entries")
    op.drop_table("dataset_rows")
    op.drop_table("test_types")
    op.drop_table("test_sets")
    op.drop_table("test_plans")
    op.drop_table("target_checks")
    op.drop_table("statistical_tests")
    op.drop_table("settings")
    op.drop_table("judge_checks")
    op.drop_table("datasets")
    # PostgreSQL keeps an enum's type after its table is gone
    bind = op.get_bind()
    for name in [
        "batchstatus",
        "comparison",
        "judgecheckstatus",
        "outputsource",
        "settingssection",
        "targetcheckstatus",
        "teststatus",
        "testtypes",
        "testtypescost",
    ]:
        sa.Enum(name=name).drop(bind, checkfirst=True)
