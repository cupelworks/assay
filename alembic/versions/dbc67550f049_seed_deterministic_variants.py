"""seed deterministic variants: case-insensitive, strict, full match and negated checks

Revision ID: dbc67550f049
Revises: 5fd1796fb435
Create Date: 2026-09-29 10:00:00.000000

Seven new catalogue rows, each an existing engine (exact_match, contains,
regex) with different settings, so none of them needs new scoring code:

- Exact Match (case-insensitive), Exact Match (strict)
- Contains (case-insensitive)
- Regex Full Match
- Does Not Contain, Does Not Contain (case-insensitive), Regex Must Not Match
  — the contains and regex engines' `negate` setting, which flips the
  outcome of the same matching

Every row is deterministic, very fast, has no comparison and takes the same
per-assignment field as the check it derives from (the expected output, a
substring or a pattern). The downgrade deletes them by name; it fails, as
it should, while a test still has one of them assigned.

"""
import uuid
from collections.abc import Sequence
from datetime import datetime

import sqlalchemy as sa

from alembic import op

revision: str = 'dbc67550f049'
down_revision: str | None = '5fd1796fb435'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_REFERENCE = [{"key": "reference", "label": "Expected output", "kind": "reference",
               "required": True}]
_REQUIRED_SUBSTRING = [{"key": "substring", "label": "Required substring", "kind": "multiline",
                        "required": True}]
_FORBIDDEN_SUBSTRING = [{"key": "substring", "label": "Forbidden substring",
                         "kind": "multiline", "required": True}]
_PATTERN = [{"key": "pattern", "label": "Regex pattern", "kind": "multiline", "required": True}]
_FORBIDDEN_PATTERN = [{"key": "pattern", "label": "Forbidden pattern", "kind": "multiline",
                       "required": True}]

_ROWS = [
    {
        "name": "Exact Match (case-insensitive)",
        "description": "Checks if the output matches the expected string exactly, ignoring "
                       "letter case and surrounding whitespace.",
        "best_for": "Short answers where capitalisation doesn't matter: country codes, "
                    "yes/no answers, labels.",
        "limitations": "Still fails on any other difference — punctuation, inner spacing, "
                       "wording.",
        "config_fields": _REFERENCE,
        "engine": "exact_match",
        "engine_settings": {"trim": True, "case_sensitive": False},
    },
    {
        "name": "Exact Match (strict)",
        "description": "Checks if the output equals the expected string character for "
                       "character, including case and surrounding whitespace.",
        "best_for": "Outputs read by a machine where every character counts: IDs, codes, "
                    "exact formats.",
        "limitations": "Fails on a trailing newline or space that the plain Exact Match "
                       "forgives.",
        "config_fields": _REFERENCE,
        "engine": "exact_match",
        "engine_settings": {"trim": False, "case_sensitive": True},
    },
    {
        "name": "Contains (case-insensitive)",
        "description": "Checks if the output contains a specific substring, ignoring letter "
                       "case.",
        "best_for": "Required keywords whose capitalisation varies: product names, terms at "
                    "the start of a sentence.",
        "limitations": "Doesn't validate context; a keyword can appear in a wrong or "
                       "misleading sentence.",
        "config_fields": _REQUIRED_SUBSTRING,
        "engine": "contains",
        "engine_settings": {"case_sensitive": False},
    },
    {
        "name": "Regex Full Match",
        "description": "Checks if the whole output matches a regular expression pattern, not "
                       "just part of it.",
        "best_for": "Outputs that must be exactly one structured value: a date, an ID, a "
                    "single code.",
        "limitations": "The pattern must describe the entire answer; any extra word or "
                       "punctuation fails it.",
        "config_fields": _PATTERN,
        "engine": "regex",
        "engine_settings": {"mode": "fullmatch", "timeout_seconds": 1},
    },
    {
        "name": "Does Not Contain",
        "description": "Checks that the output does not contain a specific substring.",
        "best_for": "Guardrails: no \"As an AI language model\", no leaked system prompt, no "
                    "competitor names.",
        "limitations": "Only catches the exact text; a paraphrase of the forbidden content "
                       "passes.",
        "config_fields": _FORBIDDEN_SUBSTRING,
        "engine": "contains",
        "engine_settings": {"case_sensitive": True, "negate": True},
    },
    {
        "name": "Does Not Contain (case-insensitive)",
        "description": "Checks that the output does not contain a specific substring, "
                       "ignoring letter case.",
        "best_for": "Guardrails where the forbidden text may appear in any capitalisation.",
        "limitations": "Only catches the exact text; a paraphrase of the forbidden content "
                       "passes.",
        "config_fields": _FORBIDDEN_SUBSTRING,
        "engine": "contains",
        "engine_settings": {"case_sensitive": False, "negate": True},
    },
    {
        "name": "Regex Must Not Match",
        "description": "Checks that no part of the output matches a regular expression "
                       "pattern.",
        "best_for": "Guardrails on formats: no card numbers, no email addresses, no internal "
                    "URLs.",
        "limitations": "Requires writing and maintaining regex patterns; only catches what "
                       "the pattern describes.",
        "config_fields": _FORBIDDEN_PATTERN,
        "engine": "regex",
        "engine_settings": {"mode": "search", "timeout_seconds": 1, "negate": True},
    },
]


def upgrade() -> None:
    now = datetime.now().astimezone()
    op.bulk_insert(
        sa.table(
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
        ),
        [
            {
                "id": uuid.uuid4(),
                "category": "deterministic",
                "cost": "very_fast",
                "comparison": None,
                "is_active": True,
                "created_at": now,
                **row,
            }
            for row in _ROWS
        ],
    )


def downgrade() -> None:
    names = [row["name"] for row in _ROWS]
    op.execute(
        sa.table("test_types", sa.column("name", sa.Text()))
        .delete()
        .where(sa.column("name").in_(names))
    )
