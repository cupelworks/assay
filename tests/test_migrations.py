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
    threshold_scored = {"ROUGE", "BLEU", "METEOR", "BERTScore", "Cosine Similarity"}
    assert {name for name, row in rows.items() if row["comparison"] == "gte"} == threshold_scored
    assert all(row["comparison"] is None for name, row in rows.items()
               if name not in threshold_scored)
    # the same set is exactly the set of types that declare a threshold field
    assert {name for name, row in rows.items() if _threshold_range(row)} == threshold_scored


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
                "VALUES ('judge', '{}', '2026-01-01')"
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

    command.upgrade(config, "head")

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
    command.upgrade(config, "head")
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
    command.upgrade(config, "head")

    command.downgrade(config, "5fd1796fb435")

    rows = _test_types(db_path)
    assert len(rows) == 13
    assert not set(_VARIANTS) & set(rows)
