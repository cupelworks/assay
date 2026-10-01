"""Migrations that hand-write data changes, run against a scratch SQLite
database — the one kind of contract the mocked-session unit tests can't see.

The Alembic Config is built in code rather than from alembic.ini: reading
the ini would run logging.fileConfig, which disables every existing logger
(the assay.* ones included) for the rest of the test session and silently
breaks the caplog tests that run after this file. env.py reads the database
URL from the already-imported settings singleton, so that is what gets
pointed at the scratch file — an environment variable set now would come
too late to reach it.
"""
import json
import re
import sqlite3
from pathlib import Path

import pytest
from alembic.config import Config

from alembic import command
from assay.config import settings

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def scratch(tmp_path, monkeypatch):
    db_path = tmp_path / "scratch.db"
    monkeypatch.setattr(settings, "database_url", f"sqlite+aiosqlite:///{db_path}")
    config = Config()
    config.set_main_option("script_location", str(ROOT / "alembic"))
    return config, db_path


def _test_types(db_path: Path) -> dict[str, dict]:
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        return {row["name"]: dict(row) for row in connection.execute("SELECT * FROM test_types")}


def _threshold_range(row: dict) -> tuple[float, float] | None:
    for field in json.loads(row["config_fields"]):
        if field["key"] == "threshold":
            return field["min"], field["max"]
    return None


def _columns(db_path: Path) -> list[str]:
    with sqlite3.connect(db_path) as connection:
        return [row[1] for row in connection.execute("PRAGMA table_info(test_types)")]


# --- b4e1c9d27a58: engine, engine_settings, comparison, native threshold ranges ---


def test_upgrade_seeds_an_engine_for_every_catalogue_row(scratch):
    config, db_path = scratch

    # the thirteen original rows, as this migration left them
    command.upgrade(config, "b4e1c9d27a58")

    rows = _test_types(db_path)
    assert len(rows) == 13
    assert all(row["engine"] for row in rows.values())
    assert {row["engine"] for row in rows.values()} == {
        "exact_match", "contains", "regex", "rouge", "bleu", "meteor",
        "bertscore", "embedding_cosine", "llm_judge",
    }
    # engine_settings is JSON on every row, even when it's empty
    assert all(isinstance(json.loads(row["engine_settings"]), dict) for row in rows.values())
    assert json.loads(rows["ROUGE"]["engine_settings"]) == {
        "variant": "rougeL", "measure": "f1", "stemmer": True,
    }
    assert json.loads(rows["METEOR"]["engine_settings"]) == {}
    assert json.loads(rows["Toxicity"]["engine_settings"]).keys() == {"default_rubric"}


def test_upgrade_sets_comparison_only_on_threshold_scored_types(scratch):
    config, db_path = scratch

    command.upgrade(config, "head")

    rows = _test_types(db_path)
    # exactly the types that declare a threshold field have a comparison —
    # the five original metrics and every row added on their engines since
    threshold_scored = {name for name, row in rows.items() if _threshold_range(row)}
    assert {"ROUGE", "BLEU", "METEOR", "BERTScore", "Cosine Similarity"} <= threshold_scored
    assert {name for name, row in rows.items() if row["comparison"] == "gte"} == threshold_scored
    assert all(row["comparison"] is None for name, row in rows.items()
               if name not in threshold_scored)


def test_upgrade_moves_bleu_and_cosine_to_their_native_ranges_only(scratch):
    config, db_path = scratch

    command.upgrade(config, "head")

    rows = _test_types(db_path)
    assert _threshold_range(rows["BLEU"]) == (0.0, 100.0)
    assert _threshold_range(rows["Cosine Similarity"]) == (-1.0, 1.0)
    for name in ("ROUGE", "METEOR", "BERTScore"):
        assert _threshold_range(rows[name]) == (0.0, 1.0)


def test_upgrade_leaves_no_server_default_on_engine(scratch):
    # A future seed migration that forgets engine must fail the NOT NULL,
    # not silently insert '' — the temporary default is dropped after seeding.
    config, db_path = scratch

    command.upgrade(config, "head")

    with sqlite3.connect(db_path) as connection:
        defaults = {row[1]: row[4] for row in connection.execute("PRAGMA table_info(test_types)")}
        with pytest.raises(sqlite3.IntegrityError, match="NOT NULL constraint failed"):
            connection.execute(
                "INSERT INTO test_types (id, name, category, config_fields, is_active) "
                "VALUES (X'00', 'No Engine', 'deterministic', '[]', 1)"
            )
        with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint failed"):
            connection.execute(
                "INSERT INTO test_types (id, name, category, config_fields, is_active, "
                "engine, comparison) "
                "VALUES (X'01', 'Bad Comparison', 'deterministic', '[]', 1, 'x', 'eq')"
            )
    assert defaults["engine"] is None
    assert defaults["engine_settings"] == "'{}'"


def test_downgrade_removes_the_columns_and_restores_the_0_1_bounds(scratch):
    config, db_path = scratch
    command.upgrade(config, "head")

    command.downgrade(config, "f0a9d5ed2c65")

    assert not {"engine", "engine_settings", "comparison"} & set(_columns(db_path))
    rows = _test_types(db_path)
    assert _threshold_range(rows["BLEU"]) == (0.0, 1.0)
    assert _threshold_range(rows["Cosine Similarity"]) == (0.0, 1.0)
    assert len(rows) == 13

    # and the round trip is clean
    command.upgrade(config, "head")
    assert {"engine", "engine_settings", "comparison"} <= set(_columns(db_path))


# --- c8f2a7d11e94: evaluated_output and output_source on test_runs ---


def _run_columns(db_path: Path) -> list[str]:
    with sqlite3.connect(db_path) as connection:
        return [row[1] for row in connection.execute("PRAGMA table_info(test_runs)")]


def test_upgrade_adds_nullable_evaluated_output_and_a_constrained_output_source(scratch):
    config, db_path = scratch

    command.upgrade(config, "head")

    assert {"evaluated_output", "output_source"} <= set(_run_columns(db_path))
    with sqlite3.connect(db_path) as connection:
        # both nullable: a Pending run has neither
        connection.execute(
            "INSERT INTO test_runs (id, status, created_at) VALUES (X'02', 'pending', '2026-01-01')"
        )
        connection.execute(
            "INSERT INTO test_runs (id, status, created_at, evaluated_output, output_source) "
            "VALUES (X'03', 'green', '2026-01-01', 'hi', 'application')"
        )
        with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint failed"):
            connection.execute(
                "INSERT INTO test_runs (id, status, created_at, output_source) "
                "VALUES (X'04', 'green', '2026-01-01', 'guessed')"
            )


def test_downgrade_removes_the_two_run_columns_only(scratch):
    config, db_path = scratch
    command.upgrade(config, "c8f2a7d11e94")

    command.downgrade(config, "-1")

    assert not {"evaluated_output", "output_source"} & set(_run_columns(db_path))
    assert {"engine", "engine_settings", "comparison"} <= set(_columns(db_path))


# --- a0f4ff4fd23a / 5fd1796fb435: settings and checks ---


def _tables(db_path: Path) -> set[str]:
    with sqlite3.connect(db_path) as connection:
        return {row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )}


def test_upgrade_creates_an_empty_settings_table_keyed_by_a_constrained_section(scratch):
    config, db_path = scratch

    command.upgrade(config, "head")

    with sqlite3.connect(db_path) as connection:
        # empty on purpose: no row means the environment's settings apply
        assert connection.execute("SELECT COUNT(*) FROM settings").fetchone() == (0,)
        connection.execute(
            "INSERT INTO settings (section, value, updated_at) "
            "VALUES ('target', '{}', '2026-01-01')"
        )
        with pytest.raises(sqlite3.IntegrityError, match="UNIQUE constraint failed"):
            connection.execute(
                "INSERT INTO settings (section, value, updated_at) "
                "VALUES ('target', '{}', '2026-01-01')"
            )
        with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint failed"):
            connection.execute(
                "INSERT INTO settings (section, value, updated_at) "
                "VALUES ('unknown', '{}', '2026-01-01')"
            )


def test_upgrade_creates_target_checks_with_a_constrained_status(scratch):
    config, db_path = scratch

    command.upgrade(config, "head")

    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "INSERT INTO target_checks (id, created_at, status, input, settings) "
            "VALUES (X'01', '2026-01-01', 'pending', 'q', '{}')"
        )
        with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint failed"):
            connection.execute(
                "INSERT INTO target_checks (id, created_at, status, input, settings) "
                "VALUES (X'02', '2026-01-01', 'lost', 'q', '{}')"
            )


def test_downgrading_both_removes_the_two_tables_only(scratch):
    config, db_path = scratch
    command.upgrade(config, "head")

    command.downgrade(config, "c8f2a7d11e94")

    assert not {"settings", "target_checks"} & _tables(db_path)
    assert {"evaluated_output", "output_source"} <= set(_run_columns(db_path))


# --- dbc67550f049: deterministic variants ---

_VARIANTS = {
    "Exact Match (case-insensitive)": ("exact_match", {"trim": True, "case_sensitive": False},
                                       "reference"),
    "Exact Match (strict)": ("exact_match", {"trim": False, "case_sensitive": True},
                             "reference"),
    "Contains (case-insensitive)": ("contains", {"case_sensitive": False}, "substring"),
    "Regex Full Match": ("regex", {"mode": "fullmatch", "timeout_seconds": 1}, "pattern"),
    "Does Not Contain": ("contains", {"case_sensitive": True, "negate": True}, "substring"),
    "Does Not Contain (case-insensitive)": ("contains",
                                            {"case_sensitive": False, "negate": True},
                                            "substring"),
    "Regex Must Not Match": ("regex", {"mode": "search", "timeout_seconds": 1, "negate": True},
                             "pattern"),
}


def test_upgrade_seeds_the_deterministic_variants_on_existing_engines(scratch):
    config, db_path = scratch

    command.upgrade(config, "dbc67550f049")

    rows = _test_types(db_path)
    assert len(rows) == 20
    for name, (engine, engine_settings, field) in _VARIANTS.items():
        row = rows[name]
        assert (row["engine"], json.loads(row["engine_settings"])) == (engine,
                                                                        engine_settings), name
        assert (row["category"], row["cost"], row["comparison"]) == (
            "deterministic", "very_fast", None), name
        assert row["is_active"] == 1, name
        assert [f["key"] for f in json.loads(row["config_fields"])] == [field], name


def test_every_seeded_variant_scores_through_the_registry(scratch):
    from assay.models import TestTypesModel
    from assay.schemas import TestTypeAssignment
    from assay.worker.evaluators import evaluate

    config, db_path = scratch
    command.upgrade(config, "dbc67550f049")
    rows = _test_types(db_path)

    def outcome(name, answer, reference="Paris", **config_values):
        row = rows[name]
        catalogue_row = TestTypesModel(name=name, engine=row["engine"],
                                       engine_settings=json.loads(row["engine_settings"]),
                                       comparison=None)
        entry = type("Entry", (), {"input": "q", "expected_output": reference})()
        return evaluate(TestTypeAssignment(name=name, config=config_values or None),
                        catalogue_row, entry, answer).passed

    assert outcome("Exact Match (case-insensitive)", " paris\n") is True
    assert outcome("Exact Match (strict)", "Paris\n") is False
    assert outcome("Contains (case-insensitive)", "the REFUND is on its way",
                   substring="refund") is True
    assert outcome("Regex Full Match", "2026-09-29", pattern=r"\d{4}-\d{2}-\d{2}") is True
    assert outcome("Regex Full Match", "on 2026-09-29", pattern=r"\d{4}-\d{2}-\d{2}") is False
    assert outcome("Does Not Contain", "As an AI, I can't", substring="As an AI") is False
    assert outcome("Does Not Contain", "Here you go", substring="As an AI") is True
    assert outcome("Does Not Contain (case-insensitive)", "as an ai, I can't",
                   substring="As an AI") is False
    assert outcome("Regex Must Not Match", "Card 4111 1111 1111 1111",
                   pattern=r"\b(?:\d[ -]?){13,16}\b") is False


def test_downgrade_removes_the_variants_only(scratch):
    config, db_path = scratch
    command.upgrade(config, "dbc67550f049")

    command.downgrade(config, "5fd1796fb435")

    rows = _test_types(db_path)
    assert len(rows) == 13
    assert not set(_VARIANTS) & set(rows)


# --- 5e3f74c874ae: Exact Match (strict) renamed ---

_STRICT = "Exact Match (strict)"
_WHITESPACE = "Exact Match (whitespace-sensitive)"


_TEST_ID, _ENTRY_ID = "a1" + "0" * 30, "e1" + "0" * 30


def _assign_everywhere(db_path: Path, name: str) -> None:
    """A test with the type assigned, and a test set entry whose copy lists it."""
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "INSERT INTO tests (id, name, input, created_at) "
            "VALUES (?, 'Uses it', 'q', '2026-01-01')",
            (_TEST_ID,),
        )
        connection.execute(
            "INSERT INTO test_type_assignments (test_id, test_type_name) VALUES (?, ?)",
            (_TEST_ID, name),
        )
        connection.execute(
            "INSERT INTO test_set_entries (id, test_id, input, snapshot_at, name, "
            "test_type_assignments) VALUES (?, ?, 'q', '2026-01-01', 'Uses it', ?)",
            (_ENTRY_ID, _TEST_ID, json.dumps([{"name": name, "config": None},
                                              {"name": "Contains",
                                               "config": {"substring": "x"}}])),
        )


def _where_it_is_used(db_path: Path) -> tuple[list[str], list[str]]:
    with sqlite3.connect(db_path) as connection:
        assigned = [r[0] for r in connection.execute(
            "SELECT test_type_name FROM test_type_assignments")]
        (copy,) = connection.execute(
            "SELECT test_type_assignments FROM test_set_entries").fetchone()
    return assigned, [item["name"] for item in json.loads(copy)]


def test_upgrade_renames_strict_with_clearer_texts_and_the_same_engine(scratch):
    config, db_path = scratch

    command.upgrade(config, "5e3f74c874ae")

    rows = _test_types(db_path)
    assert _STRICT not in rows
    renamed = rows[_WHITESPACE]
    assert (renamed["engine"], json.loads(renamed["engine_settings"])) == (
        "exact_match", {"trim": False, "case_sensitive": True})
    assert "spaces, tabs or newlines at the start or end" in renamed["description"]
    assert len(rows) == 20


def test_upgrade_moves_assignments_and_entry_copies_to_the_new_name(scratch):
    config, db_path = scratch
    command.upgrade(config, "dbc67550f049")
    _assign_everywhere(db_path, _STRICT)

    command.upgrade(config, "5e3f74c874ae")

    assert _where_it_is_used(db_path) == ([_WHITESPACE], [_WHITESPACE, "Contains"])


def test_downgrade_restores_the_old_name_everywhere(scratch):
    config, db_path = scratch
    command.upgrade(config, "5e3f74c874ae")
    _assign_everywhere(db_path, _WHITESPACE)

    command.downgrade(config, "dbc67550f049")

    rows = _test_types(db_path)
    assert _WHITESPACE not in rows
    assert rows[_STRICT]["description"].startswith("Checks if the output equals the expected "
                                                   "string character for character")
    assert _where_it_is_used(db_path) == ([_STRICT], [_STRICT, "Contains"])


# --- 43a7467fc3bf: JSON checks ---

_JSON_CHECKS = {
    "Is Valid JSON": ("valid", []),
    "Matches JSON Schema": ("schema", ["schema"]),
    "JSON Field Equals": ("field", ["path", "value"]),
}


def test_upgrade_seeds_the_json_checks_on_the_json_engine(scratch):
    config, db_path = scratch

    command.upgrade(config, "43a7467fc3bf")

    rows = _test_types(db_path)
    assert len(rows) == 23
    for name, (check, fields) in _JSON_CHECKS.items():
        row = rows[name]
        assert (row["engine"], json.loads(row["engine_settings"])) == (
            "json", {"check": check, "strip_fences": True}), name
        assert (row["category"], row["cost"], row["comparison"]) == (
            "deterministic", "very_fast", None), name
        assert [f["key"] for f in json.loads(row["config_fields"])] == fields, name


def test_every_json_check_scores_through_the_registry(scratch):
    from assay.models import TestTypesModel
    from assay.schemas import TestTypeAssignment
    from assay.worker.evaluators import evaluate

    config, db_path = scratch
    command.upgrade(config, "43a7467fc3bf")
    rows = _test_types(db_path)

    def outcome(name, answer, **config_values):
        row = rows[name]
        catalogue_row = TestTypesModel(name=name, engine=row["engine"],
                                       engine_settings=json.loads(row["engine_settings"]),
                                       comparison=None)
        entry = type("Entry", (), {"input": "q", "expected_output": None})()
        return evaluate(TestTypeAssignment(name=name, config=config_values or None),
                        catalogue_row, entry, answer).passed

    fenced = '```json\n{"status": "approved"}\n```'
    schema = '{"type": "object", "required": ["status"]}'
    assert outcome("Is Valid JSON", fenced) is True
    assert outcome("Is Valid JSON", "not json") is False
    assert outcome("Matches JSON Schema", fenced, schema=schema) is True
    assert outcome("Matches JSON Schema", "{}", schema=schema) is False
    assert outcome("JSON Field Equals", fenced, path="$.status", value='"approved"') is True
    assert outcome("JSON Field Equals", fenced, path="$.status", value='"rejected"') is False


def test_downgrade_removes_the_json_checks_only(scratch):
    config, db_path = scratch
    command.upgrade(config, "43a7467fc3bf")

    command.downgrade(config, "5e3f74c874ae")

    rows = _test_types(db_path)
    assert len(rows) == 20
    assert not set(_JSON_CHECKS) & set(rows)


# --- fc7d91b00c18: length limits ---

_LENGTH_LIMITS = {"Word Count Limit": "words", "Character Count Limit": "characters"}


def test_upgrade_seeds_the_length_limits_on_the_length_engine(scratch):
    config, db_path = scratch

    command.upgrade(config, "fc7d91b00c18")

    rows = _test_types(db_path)
    assert len(rows) == 25
    for name, unit in _LENGTH_LIMITS.items():
        row = rows[name]
        assert (row["engine"], json.loads(row["engine_settings"])) == ("length", {"unit": unit})
        assert (row["category"], row["cost"], row["comparison"]) == (
            "deterministic", "very_fast", None)
        fields = {f["key"]: f for f in json.loads(row["config_fields"])}
        assert (fields["max"]["required"], fields["min"]["required"]) == (True, False)
        assert all((f["kind"], f["min"], f["max"]) == ("numeric", 0.0, None)
                   for f in fields.values())


def test_every_length_limit_scores_through_the_registry(scratch):
    from assay.models import TestTypesModel
    from assay.schemas import TestTypeAssignment
    from assay.worker.evaluators import evaluate

    config, db_path = scratch
    command.upgrade(config, "fc7d91b00c18")
    rows = _test_types(db_path)

    def outcome(name, answer, **config_values):
        row = rows[name]
        catalogue_row = TestTypesModel(name=name, engine=row["engine"],
                                       engine_settings=json.loads(row["engine_settings"]),
                                       comparison=None)
        entry = type("Entry", (), {"input": "q", "expected_output": None})()
        return evaluate(TestTypeAssignment(name=name, config=config_values),
                        catalogue_row, entry, answer).passed

    assert outcome("Word Count Limit", "one two three", max="3") is True
    assert outcome("Word Count Limit", "one two three", max="5", min="4") is False
    assert outcome("Character Count Limit", "x" * 160, max="160") is True
    assert outcome("Character Count Limit", "x" * 161, max="160") is False


def test_downgrade_removes_the_length_limits_only(scratch):
    config, db_path = scratch
    command.upgrade(config, "fc7d91b00c18")

    command.downgrade(config, "43a7467fc3bf")

    rows = _test_types(db_path)
    assert len(rows) == 23
    assert not set(_LENGTH_LIMITS) & set(rows)


# --- 812acbc349ad: field kinds, placeholders and hints ---


def _fields(db_path: Path, name: str) -> dict[str, dict]:
    return {f["key"]: f for f in json.loads(_test_types(db_path)[name]["config_fields"])}


def test_upgrade_gives_the_json_fields_their_kinds_and_every_new_field_a_hint(scratch):
    config, db_path = scratch

    command.upgrade(config, "812acbc349ad")

    schema = _fields(db_path, "Matches JSON Schema")["schema"]
    path, value = (_fields(db_path, "JSON Field Equals")[key] for key in ("path", "value"))
    assert (schema["kind"], path["kind"], value["kind"]) == ("json", "jsonpath", "json")
    assert (path["placeholder"], value["placeholder"]) == ("$.status", '"approved"')
    assert value["hint"].startswith("A JSON value: strings in double quotes")
    maximum = _fields(db_path, "Word Count Limit")["max"]
    assert (maximum["kind"], maximum["placeholder"], maximum["min"]) == ("numeric", "100", 0.0)
    assert _fields(db_path, "Character Count Limit")["min"]["hint"].startswith("Optional.")
    # rows this migration doesn't name are untouched
    assert "hint" not in _fields(db_path, "Regex Match")["pattern"]


def test_downgrade_restores_the_fields_as_seeded(scratch):
    config, db_path = scratch
    command.upgrade(config, "812acbc349ad")

    command.downgrade(config, "fc7d91b00c18")

    path = _fields(db_path, "JSON Field Equals")["path"]
    assert path == {"key": "path", "label": "JSONPath", "kind": "multiline", "required": True}
    assert "placeholder" not in _fields(db_path, "Word Count Limit")["max"]


# --- 954995a8255b: ROUGE variants and threshold hints ---

_ROUGE_VARIANTS = {
    "ROUGE-1": ("rouge1", "f1"),
    "ROUGE-2": ("rouge2", "f1"),
    "ROUGE-L Recall": ("rougeL", "recall"),
    "ROUGE-L Precision": ("rougeL", "precision"),
}


def test_upgrade_seeds_the_rouge_variants_with_their_threshold_hints(scratch):
    config, db_path = scratch

    command.upgrade(config, "954995a8255b")

    rows = _test_types(db_path)
    assert len(rows) == 29
    for name, (variant, measure) in _ROUGE_VARIANTS.items():
        row = rows[name]
        assert (row["engine"], json.loads(row["engine_settings"])) == (
            "rouge", {"variant": variant, "measure": measure, "stemmer": True}), name
        assert (row["category"], row["cost"], row["comparison"]) == (
            "nlp_metric", "fast", "gte"), name
        reference, threshold = json.loads(row["config_fields"])
        assert reference["kind"] == "reference", name
        assert (threshold["key"], threshold["min"], threshold["max"]) == ("threshold", 0.0, 1.0)
        assert threshold["placeholder"] and threshold["hint"].startswith("0 to 1:"), name
    assert _fields(db_path, "ROUGE")["threshold"]["placeholder"] == "0.5"
    # the other metrics' thresholds are untouched
    assert "hint" not in _fields(db_path, "BLEU")["threshold"]


def test_every_rouge_variant_scores_through_the_registry(scratch):
    from assay.models import Comparison, TestTypesModel
    from assay.schemas import TestTypeAssignment
    from assay.worker.evaluators import evaluate

    config, db_path = scratch
    command.upgrade(config, "954995a8255b")
    rows = _test_types(db_path)
    reference = "Reset your password from Settings, then Security."
    covers_and_adds = ("To reset your password, open Settings, go to Security, and choose "
                       "Reset password. You'll get an email to confirm.")

    def score(name):
        row = rows[name]
        catalogue_row = TestTypesModel(name=name, engine=row["engine"],
                                       engine_settings=json.loads(row["engine_settings"]),
                                       comparison=Comparison.gte)
        entry = type("Entry", (), {"input": "q", "expected_output": reference})()
        return evaluate(TestTypeAssignment(name=name, config={"threshold": "0"}),
                        catalogue_row, entry, covers_and_adds).score

    # an answer that covers everything and adds a lot: high recall, low precision
    assert score("ROUGE-L Recall") == 0.7143
    assert score("ROUGE-L Precision") == 0.25
    assert score("ROUGE-1") > score("ROUGE-2")


def test_downgrade_removes_the_variants_and_restores_rouges_threshold(scratch):
    config, db_path = scratch
    command.upgrade(config, "954995a8255b")

    command.downgrade(config, "812acbc349ad")

    rows = _test_types(db_path)
    assert not set(_ROUGE_VARIANTS) & set(rows)
    assert "placeholder" not in _fields(db_path, "ROUGE")["threshold"]


# --- 1aac7a522b8c: hints without the range ---

_RANGE_FIRST = re.compile(r"^-?\d+(\.\d+)? to -?\d+(\.\d+)?")


def test_upgrade_starts_the_rouge_hints_at_the_explanation(scratch):
    config, db_path = scratch

    command.upgrade(config, "head")

    assert _fields(db_path, "ROUGE-1")["threshold"]["hint"] == (
        "Shared words, in any order. A close paraphrase scores about 0.6, an unrelated "
        "answer about 0.2."
    )
    assert _fields(db_path, "ROUGE-L Recall")["threshold"]["hint"].startswith(
        "How much of the expected text the answer covers.")


def test_no_hint_restates_its_fields_range(scratch):
    # the FE shows the range on the label, from min/max: a hint explains the
    # value instead of repeating it
    config, db_path = scratch

    command.upgrade(config, "head")

    hints = {
        (name, field["key"]): field["hint"]
        for name, row in _test_types(db_path).items()
        for field in json.loads(row["config_fields"]) if field.get("hint")
    }
    assert hints
    assert not {where: hint for where, hint in hints.items() if _RANGE_FIRST.match(hint)}


def test_downgrade_puts_the_range_back(scratch):
    config, db_path = scratch
    command.upgrade(config, "head")

    command.downgrade(config, "954995a8255b")

    assert _fields(db_path, "ROUGE-2")["threshold"]["hint"] == (
        "0 to 1: shared word pairs, so lower than ROUGE-1. A close paraphrase scores about "
        "0.3, an unrelated answer 0."
    )


# --- 5ff0acda2b2c: answer_path and application_reply ---


def _assignment_columns(db_path: Path) -> list[str]:
    with sqlite3.connect(db_path) as connection:
        return [row[1] for row in connection.execute("PRAGMA table_info(test_type_assignments)")]


def test_upgrade_adds_answer_path_and_application_reply_both_nullable(scratch):
    config, db_path = scratch

    command.upgrade(config, "head")

    assert "answer_path" in _assignment_columns(db_path)
    assert "application_reply" in _run_columns(db_path)
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "INSERT INTO test_runs (id, status, created_at) VALUES (X'05', 'pending', '2026-01-01')"
        )


def test_downgrade_removes_both_columns(scratch):
    config, db_path = scratch
    command.upgrade(config, "head")

    command.downgrade(config, "1aac7a522b8c")

    assert "answer_path" not in _assignment_columns(db_path)
    assert "application_reply" not in _run_columns(db_path)


# --- 1d1fddd1a247: ROUGE's description ---


def test_upgrade_describes_rouge_as_rouge_l_f1(scratch):
    config, db_path = scratch

    command.upgrade(config, "head")

    assert _test_types(db_path)["ROUGE"]["description"] == (
        "Measures the longest sequence of words the output shares with the expected text, "
        "in order (ROUGE-L F1)."
    )


def test_downgrade_restores_rouges_old_description(scratch):
    config, db_path = scratch
    command.upgrade(config, "head")

    command.downgrade(config, "5ff0acda2b2c")

    assert _test_types(db_path)["ROUGE"]["description"] == (
        "Measures n-gram overlap between output and expected text."
    )


# --- ad28dd68006e: BLEU's settings and BLEU (case-insensitive) ---


def test_upgrade_gives_bleu_sacrebleus_settings_and_seeds_the_case_insensitive_row(scratch):
    config, db_path = scratch

    command.upgrade(config, "ad28dd68006e")

    rows = _test_types(db_path)
    assert json.loads(rows["BLEU"]["engine_settings"]) == {"smooth_method": "exp",
                                                           "lowercase": False}
    variant = rows["BLEU (case-insensitive)"]
    assert json.loads(variant["engine_settings"]) == {"smooth_method": "exp", "lowercase": True}
    assert (variant["category"], variant["cost"], variant["engine"], variant["comparison"]) == (
        "nlp_metric", "fast", "bleu", "gte")
    for name in ("BLEU", "BLEU (case-insensitive)"):
        threshold = _fields(db_path, name)["threshold"]
        assert (threshold["min"], threshold["max"], threshold["placeholder"]) == (0.0, 100.0, "20")
        assert threshold["hint"].startswith("Shared wording in runs of up to four words.")


def test_both_bleu_rows_score_through_the_registry(scratch):
    from assay.models import Comparison, TestTypesModel
    from assay.schemas import TestTypeAssignment
    from assay.worker.evaluators import evaluate

    config, db_path = scratch
    command.upgrade(config, "ad28dd68006e")
    rows = _test_types(db_path)
    reference = "The refund for order 4471 has been issued and will arrive in five days."

    def score(name):
        row = rows[name]
        catalogue_row = TestTypesModel(name=name, engine=row["engine"],
                                       engine_settings=json.loads(row["engine_settings"]),
                                       comparison=Comparison.gte)
        entry = type("Entry", (), {"input": "q", "expected_output": reference})()
        return evaluate(TestTypeAssignment(name=name, config={"threshold": "0"}),
                        catalogue_row, entry, reference.upper()).score

    # the same answer in capitals: a different text to BLEU, the same one lowercased
    assert score("BLEU") == 3.1252
    assert score("BLEU (case-insensitive)") == 100.0


def test_downgrade_removes_the_variant_and_restores_bleu(scratch):
    config, db_path = scratch
    command.upgrade(config, "ad28dd68006e")

    command.downgrade(config, "1d1fddd1a247")

    rows = _test_types(db_path)
    assert "BLEU (case-insensitive)" not in rows
    assert json.loads(rows["BLEU"]["engine_settings"]) == {"smoothing": True}
    assert rows["BLEU"]["description"] == (
        "Measures n-gram precision between output and reference text.")
    assert "hint" not in _fields(db_path, "BLEU")["threshold"]


# --- 6774a3279590: METEOR's settings and texts ---


def test_upgrade_gives_meteor_nltks_parameters_and_a_threshold_hint(scratch):
    config, db_path = scratch

    command.upgrade(config, "6774a3279590")

    row = _test_types(db_path)["METEOR"]
    assert json.loads(row["engine_settings"]) == {"alpha": 0.9, "beta": 3.0, "gamma": 0.5}
    assert row["description"].startswith("Checks how much of the expected text the answer "
                                          "covers")
    threshold = _fields(db_path, "METEOR")["threshold"]
    assert (threshold["min"], threshold["max"], threshold["placeholder"]) == (0.0, 1.0, "0.5")
    assert threshold["hint"].startswith("Shared words, counting other word forms and synonyms.")


def test_upgrade_seeds_meteor_balanced_on_the_same_engine(scratch):
    config, db_path = scratch

    command.upgrade(config, "6774a3279590")

    row = _test_types(db_path)["METEOR (balanced)"]
    assert json.loads(row["engine_settings"]) == {"alpha": 0.5, "beta": 3.0, "gamma": 0.5}
    assert (row["category"], row["cost"], row["engine"], row["comparison"]) == (
        "nlp_metric", "fast", "meteor", "gte")
    assert "extra content lower the score equally" in row["description"]
    assert _fields(db_path, "METEOR (balanced)")["threshold"]["placeholder"] == "0.5"


def test_downgrade_restores_meteor(scratch):
    config, db_path = scratch
    command.upgrade(config, "6774a3279590")

    command.downgrade(config, "ad28dd68006e")

    rows = _test_types(db_path)
    assert "METEOR (balanced)" not in rows
    row = rows["METEOR"]
    assert json.loads(row["engine_settings"]) == {}
    assert row["description"].startswith("Measures alignment between output and reference")
    assert "hint" not in _fields(db_path, "METEOR")["threshold"]


# --- 1b6c140a6035: BERTScore's and Cosine Similarity's settings and texts ---


def test_upgrade_gives_bertscore_its_layer_and_rescaling_baseline(scratch):
    config, db_path = scratch

    command.upgrade(config, "1b6c140a6035")

    settings_ = json.loads(_test_types(db_path)["BERTScore"]["engine_settings"])
    assert (settings_["model"], settings_["layer"], settings_["measure"]) == (
        "distilbert-base-uncased", 5, "f1")
    assert set(settings_["baseline"]) == {"precision", "recall", "f1"}
    assert "rescale" not in settings_
    threshold = _fields(db_path, "BERTScore")["threshold"]
    assert (threshold["min"], threshold["max"], threshold["placeholder"]) == (0.0, 1.0, "0.6")


def test_upgrade_seeds_cosine_similarity_multilingual_on_the_same_engine(scratch):
    config, db_path = scratch

    command.upgrade(config, "1b6c140a6035")

    rows = _test_types(db_path)
    variant = rows["Cosine Similarity (multilingual)"]
    assert json.loads(variant["engine_settings"]) == {
        "model": "paraphrase-multilingual-MiniLM-L12-v2"}
    assert (variant["category"], variant["engine"], variant["comparison"]) == (
        "nlp_metric", "embedding_cosine", "gte")
    for name in ("Cosine Similarity", "Cosine Similarity (multilingual)"):
        threshold = _fields(db_path, name)["threshold"]
        assert (threshold["min"], threshold["max"], threshold["placeholder"]) == (
            -1.0, 1.0, "0.7")
        assert "not correctness" in rows[name]["limitations"]


def test_downgrade_restores_both_rows_and_removes_the_variant(scratch):
    config, db_path = scratch
    command.upgrade(config, "1b6c140a6035")

    command.downgrade(config, "6774a3279590")

    rows = _test_types(db_path)
    assert "Cosine Similarity (multilingual)" not in rows
    assert json.loads(rows["BERTScore"]["engine_settings"]) == {
        "model": "distilbert-base-uncased", "measure": "f1", "rescale": False}
    assert rows["Cosine Similarity"]["best_for"] == "RAG evaluation and semantic search tasks."
    assert "hint" not in _fields(db_path, "BERTScore")["threshold"]


# --- 1e4b81f30147: the judge settings group, judge_checks, the judge rows ---


def test_upgrade_accepts_a_judge_settings_row_and_creates_judge_checks(scratch):
    config, db_path = scratch

    command.upgrade(config, "1e4b81f30147")

    with sqlite3.connect(db_path) as connection:
        connection.execute("INSERT INTO settings (section, value, updated_at) "
                           "VALUES ('judge', '{}', '2026-01-01')")
        connection.execute(
            "INSERT INTO judge_checks (id, created_at, status, settings) "
            "VALUES (X'01', '2026-01-01', 'pending', '{}')"
        )
        with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint failed"):
            connection.execute(
                "INSERT INTO judge_checks (id, created_at, status, settings) "
                "VALUES (X'02', '2026-01-01', 'lost', '{}')"
            )


def test_upgrade_says_which_judges_see_the_reference_and_explains_the_rubric(scratch):
    config, db_path = scratch

    command.upgrade(config, "1e4b81f30147")

    rows = _test_types(db_path)
    sees_reference = {
        name: json.loads(row["engine_settings"])["reference"]
        for name, row in rows.items() if row["engine"] == "llm_judge"
    }
    assert sees_reference == {"Correctness": True, "Hallucination": True, "Relevance": False,
                              "Bias": False, "Toxicity": False}
    rubric = _fields(db_path, "Toxicity")["rubric"]
    assert rubric["placeholder"] == json.loads(
        rows["Toxicity"]["engine_settings"])["default_rubric"]
    assert rubric["hint"].startswith("Optional. Replaces the default rubric")
    assert rows["Toxicity"]["description"].startswith("Asks an AI judge")


def test_downgrade_restores_the_judge_rows_and_narrows_the_section_again(scratch):
    config, db_path = scratch
    command.upgrade(config, "1e4b81f30147")
    with sqlite3.connect(db_path) as connection:
        connection.execute("INSERT INTO settings (section, value, updated_at) "
                           "VALUES ('target', '{}', '2026-01-01')")
        connection.execute("INSERT INTO settings (section, value, updated_at) "
                           "VALUES ('judge', '{}', '2026-01-01')")

    command.downgrade(config, "1b6c140a6035")

    rows = _test_types(db_path)
    assert "reference" not in json.loads(rows["Correctness"]["engine_settings"])
    assert rows["Correctness"]["description"] == (
        "Uses an LLM to evaluate factual correctness of the output.")
    assert "hint" not in _fields(db_path, "Correctness")["rubric"]
    with sqlite3.connect(db_path) as connection:
        # the saved target settings survive; the judge's are gone with their group
        assert connection.execute("SELECT section FROM settings").fetchall() == [("target",)]
        assert "judge_checks" not in {name for (name,) in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint failed"):
            connection.execute("INSERT INTO settings (section, value, updated_at) "
                               "VALUES ('judge', '{}', '2026-01-01')")


# --- 66000c4b71ab: the rubric hint of the judges that never see the reference ---


def test_upgrade_warns_the_judges_without_a_reference_that_a_rubric_cant_compare(scratch):
    config, db_path = scratch

    command.upgrade(config, "66000c4b71ab")

    for name in ("Relevance", "Bias", "Toxicity"):
        assert _fields(db_path, name)["rubric"]["hint"].endswith(
            "This type never sees the expected output, so the rubric can't compare with it.")
    for name in ("Correctness", "Hallucination"):
        assert "never sees" not in _fields(db_path, name)["rubric"]["hint"]


def test_downgrade_restores_the_shared_rubric_hint(scratch):
    config, db_path = scratch
    command.upgrade(config, "66000c4b71ab")

    command.downgrade(config, "1e4b81f30147")

    assert _fields(db_path, "Bias")["rubric"]["hint"] == (
        "Optional. Replaces the default rubric, shown as the example; say what the answer "
        "must do to pass.")


# --- b795f3711490: the judge's URL is the full endpoint ---


def _judge_values(db_path: Path) -> tuple[list[dict], list[dict]]:
    with sqlite3.connect(db_path) as connection:
        saved = [json.loads(value) for (value,) in connection.execute(
            "SELECT value FROM settings WHERE section = 'judge'")]
        checks = [json.loads(value) for (value,) in connection.execute(
            "SELECT settings FROM judge_checks ORDER BY id")]
    return saved, checks


def test_upgrade_turns_base_url_into_the_full_endpoint_it_called(scratch):
    config, db_path = scratch
    command.upgrade(config, "66000c4b71ab")
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "INSERT INTO settings (section, value, updated_at) VALUES ('judge', ?, '2026-01-01')",
            (json.dumps({"provider": "openai", "model": "m",
                         "base_url": "http://localhost:11434/v1/"}),))
        for n, value in enumerate([
            {"provider": "anthropic", "model": "m", "base_url": "https://proxy.test"},
            {"provider": "anthropic", "model": "m", "base_url": None},
            {"provider": None, "model": None, "base_url": None},
        ]):
            connection.execute(
                "INSERT INTO judge_checks (id, created_at, status, settings) "
                "VALUES (?, '2026-01-01', 'completed', ?)", (f"{n + 1:032x}", json.dumps(value)))

    command.upgrade(config, "b795f3711490")

    saved, checks = _judge_values(db_path)
    assert saved == [{"provider": "openai", "model": "m",
                      "url": "http://localhost:11434/v1/chat/completions"}]
    assert [check["url"] for check in checks] == ["https://proxy.test/v1/messages", None, None]
    assert all("base_url" not in check for check in checks)


def test_downgrade_takes_the_appended_path_back_off(scratch):
    config, db_path = scratch
    command.upgrade(config, "b795f3711490")
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "INSERT INTO settings (section, value, updated_at) VALUES ('judge', ?, '2026-01-01')",
            (json.dumps({"provider": "anthropic", "model": "m",
                         "url": "https://proxy.test/v1/messages"}),))

    command.downgrade(config, "66000c4b71ab")

    saved, _ = _judge_values(db_path)
    assert saved == [{"provider": "anthropic", "model": "m", "base_url": "https://proxy.test"}]


# --- 3f8cecd2afff: normalize look-alike characters in the text checks ---

_TEXT_ENGINES = {"exact_match", "contains", "regex"}


def _settings_by_name(db_path: Path) -> dict[str, tuple[str, dict]]:
    return {name: (row["engine"], json.loads(row["engine_settings"]))
            for name, row in _test_types(db_path).items()}


def test_upgrade_turns_normalize_lookalikes_on_for_every_text_check_but_one(scratch):
    config, db_path = scratch
    command.upgrade(config, "b795f3711490")
    before = _settings_by_name(db_path)

    command.upgrade(config, "3f8cecd2afff")

    after = _settings_by_name(db_path)
    text_checks = {name for name, (engine, _) in after.items() if engine in _TEXT_ENGINES}
    assert len(text_checks) == 10
    for name in text_checks:
        expected = name != "Exact Match (whitespace-sensitive)"
        assert after[name][1] == {**before[name][1], "normalize_lookalikes": expected}, name
    for name in set(after) - text_checks:
        assert after[name] == before[name], name


def test_upgrade_says_the_whitespace_sensitive_row_tells_look_alikes_apart(scratch):
    config, db_path = scratch

    command.upgrade(config, "3f8cecd2afff")

    row = _test_types(db_path)["Exact Match (whitespace-sensitive)"]
    assert "look-alike characters" in row["description"]
    assert "curly quote" in row["limitations"]


def test_every_text_check_scores_a_look_alike_answer_through_the_registry(scratch):
    from assay.models import TestTypesModel
    from assay.schemas import TestTypeAssignment
    from assay.worker.evaluators import evaluate

    config, db_path = scratch
    command.upgrade(config, "3f8cecd2afff")
    rows = _settings_by_name(db_path)

    def outcome(name, answer, reference="It's ready", **config_values):
        engine, engine_settings = rows[name]
        catalogue_row = TestTypesModel(name=name, engine=engine,
                                       engine_settings=engine_settings, comparison=None)
        entry = type("Entry", (), {"input": "q", "expected_output": reference})()
        return evaluate(TestTypeAssignment(name=name, config=config_values or None),
                        catalogue_row, entry, answer).passed

    curly = "It’s ready"
    assert outcome("Exact Match", curly) is True
    assert outcome("Exact Match (case-insensitive)", "it’s READY") is True
    assert outcome("Exact Match (whitespace-sensitive)", curly) is False
    assert outcome("Contains", curly, substring="It's") is True
    assert outcome("Does Not Contain", curly, substring="It's") is False
    assert outcome("Regex Match", curly, pattern="It's") is True
    assert outcome("Regex Must Not Match", curly, pattern="It's") is False


def test_downgrade_removes_the_setting_and_restores_the_texts(scratch):
    config, db_path = scratch
    command.upgrade(config, "b795f3711490")
    before = _test_types(db_path)
    command.upgrade(config, "3f8cecd2afff")

    command.downgrade(config, "b795f3711490")

    after = _test_types(db_path)
    assert after == before


# --- 45dabe26c054: regex and json_schema kinds, whole-number length bounds ---


def test_upgrade_gives_patterns_and_schemas_their_kinds_and_bounds_integer(scratch):
    config, db_path = scratch
    command.upgrade(config, "3f8cecd2afff")
    before = _test_types(db_path)

    command.upgrade(config, "45dabe26c054")

    after = _test_types(db_path)
    fields = {name: {f["key"]: f for f in json.loads(row["config_fields"])}
              for name, row in after.items()}
    for name in ("Regex Match", "Regex Full Match", "Regex Must Not Match"):
        assert fields[name]["pattern"]["kind"] == "regex", name
    assert fields["Matches JSON Schema"]["schema"]["kind"] == "json_schema"
    assert fields["JSON Field Equals"]["value"]["kind"] == "json"     # a value, not a schema
    for name in ("Word Count Limit", "Character Count Limit"):
        assert fields[name]["max"]["integer"] is True, name
        assert fields[name]["min"]["integer"] is True, name
    changed = {"Regex Match", "Regex Full Match", "Regex Must Not Match",
               "Matches JSON Schema", "Word Count Limit", "Character Count Limit"}
    for name in set(after) - changed:
        assert after[name] == before[name], name


def test_no_hint_says_whole_number_once_the_descriptor_does(scratch):
    config, db_path = scratch

    command.upgrade(config, "45dabe26c054")

    for name, row in _test_types(db_path).items():
        for field in json.loads(row["config_fields"]):
            if field.get("integer"):
                assert "whole number" not in (field.get("hint") or "").lower(), name


def test_downgrade_restores_the_fields_as_they_were(scratch):
    config, db_path = scratch
    command.upgrade(config, "3f8cecd2afff")
    before = _test_types(db_path)
    command.upgrade(config, "45dabe26c054")

    command.downgrade(config, "3f8cecd2afff")

    assert _test_types(db_path) == before


# --- d649f666f728: correct stale run data ---

_STUB_ERA = {"passed": True, "score": 1.0, "detail": None}


def _insert_run(db_path: Path, n: int, status: str, results=None, error=None) -> str:
    run_id = f"{n:032x}"
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "INSERT INTO test_runs (id, status, created_at, executed_at, results, error, "
            "evaluated_output, output_source) VALUES (?, ?, '2026-01-01', ?, ?, ?, ?, ?)",
            (run_id, status, "2026-01-02" if results is not None else None,
             json.dumps(results) if results is not None else None, error,
             "Paris" if results is not None else None,
             "recorded" if results is not None else None))
    return run_id


def _run(db_path: Path, run_id: str) -> dict:
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        row = dict(connection.execute("SELECT * FROM test_runs WHERE id = ?",
                                      (run_id,)).fetchone())
    row["results"] = json.loads(row["results"]) if row["results"] else None
    return row


def test_upgrade_gives_every_result_the_current_shape_and_drops_deterministic_scores(scratch):
    config, db_path = scratch
    command.upgrade(config, "45dabe26c054")
    run_id = _insert_run(db_path, 1, "green", results={
        "Exact Match": {**_STUB_ERA},                          # stub era: no engine recorded
        "Contains": {"passed": False, "score": 0.0, "detail": "required substring not found",
                     "engine": "contains", "engine_settings": {"case_sensitive": True},
                     "answer_path": None},
        "ROUGE": {"passed": True, "score": 0.81, "detail": None, "engine": "rouge",
                  "engine_settings": {"variant": "rougeL"}, "answer_path": None},
    })

    command.upgrade(config, "d649f666f728")

    results = _run(db_path, run_id)["results"]
    keys = ["passed", "score", "detail", "engine", "engine_settings", "answer_path", "rubric",
            "judge"]
    assert all(list(result) == keys for result in results.values())
    assert results["Exact Match"]["score"] is None          # deterministic by the catalogue
    assert results["Contains"]["score"] is None             # deterministic by its engine
    assert results["ROUGE"]["score"] == 0.81                # a metric keeps its score
    assert results["Contains"]["detail"] == "Required substring not found"
    assert results["Contains"]["engine_settings"] == {"normalize_lookalikes": False,
                                                      "case_sensitive": True}
    assert results["ROUGE"]["engine_settings"] == {"variant": "rougeL"}


def test_upgrade_renames_results_still_keyed_by_exact_match_strict(scratch):
    config, db_path = scratch
    command.upgrade(config, "45dabe26c054")
    run_id = _insert_run(db_path, 2, "red", results={"Exact Match (strict)": {
        "passed": False, "score": 0.0, "detail": "differs from the expected output"}})

    command.upgrade(config, "d649f666f728")

    assert _run(db_path, run_id)["results"] == {"Exact Match (whitespace-sensitive)": {
        "passed": False, "score": None, "detail": "Differs from the expected output",
        "engine": None, "engine_settings": None, "answer_path": None, "rubric": None,
        "judge": None}}


@pytest.mark.parametrize("old,new", [
    ("no application configured: ASSAY_TARGET_URL is unset",
     "No application configured: no URL is set"),
    ("ASSAY_TARGET_HEADERS references ${API_KEY} but API_KEY is not set",
     "A header references ${API_KEY} but API_KEY is not set on this server"),
    ("nothing found at ASSAY_TARGET_OUTPUT_PATH '$.answer' in the application's reply",
     "Nothing found at output path '$.answer' in the application's reply"),
    ("application answered HTTP 503 after 3 attempt(s)",
     "Application answered HTTP 503 after 3 attempt(s)"),
    ("Already current", "Already current"),
])
def test_upgrade_brings_run_errors_to_todays_wording(scratch, old, new):
    config, db_path = scratch
    command.upgrade(config, "45dabe26c054")
    run_id = _insert_run(db_path, 3, "not_ran", error=old)

    command.upgrade(config, "d649f666f728")

    assert _run(db_path, run_id)["error"] == new


def test_upgrade_capitalizes_check_errors(scratch):
    config, db_path = scratch
    command.upgrade(config, "45dabe26c054")
    with sqlite3.connect(db_path) as connection:
        for table in ("target_checks", "judge_checks"):
            connection.execute(
                f"INSERT INTO {table} (id, created_at, status, settings, input, ok, error) "
                "VALUES (?, '2026-01-01', 'completed', '{}', 'q', 0, 'could not be sent to "
                "a worker')" if table == "target_checks" else
                f"INSERT INTO {table} (id, created_at, status, settings, ok, error) "
                "VALUES (?, '2026-01-01', 'completed', '{}', 0, 'could not be sent to a "
                "worker')", (f"{7:032x}",))

    command.upgrade(config, "d649f666f728")

    with sqlite3.connect(db_path) as connection:
        for table in ("target_checks", "judge_checks"):
            (error,) = connection.execute(f"SELECT error FROM {table}").fetchone()
            assert error == "Could not be sent to a worker", table


def test_upgrade_sends_runs_the_judge_bug_spoiled_back_to_pending(scratch):
    config, db_path = scratch
    command.upgrade(config, "45dabe26c054")
    spoiled = _insert_run(db_path, 4, "amber", results={
        "Toxicity": {"passed": False, "score": None, "detail": "'str' object is not callable",
                     "engine": "llm_judge", "engine_settings": {}, "answer_path": None},
        "Contains": {"passed": True, "score": None, "detail": None, "engine": "contains",
                     "engine_settings": {"case_sensitive": True}, "answer_path": None},
    })
    honest = _insert_run(db_path, 5, "red", results={
        "Toxicity": {"passed": False, "score": None, "detail": "No judge configured: choose "
                     "a provider and model under Settings", "engine": "llm_judge",
                     "engine_settings": {}, "answer_path": None}})

    command.upgrade(config, "d649f666f728")

    run = _run(db_path, spoiled)
    assert run["status"] == "pending"
    assert [run[k] for k in ("results", "error", "executed_at", "evaluated_output",
                             "output_source", "application_reply")] == [None] * 6
    assert _run(db_path, honest)["status"] == "red"


def test_upgrade_leaves_runs_without_results_alone_and_downgrade_does_nothing(scratch):
    config, db_path = scratch
    command.upgrade(config, "45dabe26c054")
    pending = _insert_run(db_path, 6, "pending")
    before = _run(db_path, pending)

    command.upgrade(config, "d649f666f728")
    assert _run(db_path, pending) == before
    command.downgrade(config, "45dabe26c054")
    assert _run(db_path, pending) == before
