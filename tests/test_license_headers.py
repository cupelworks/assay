# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Francesco Campanile
"""Every source file says its license and copyright in its first two lines,
the migration template included, so a new migration starts with them too."""
from pathlib import Path

ROOT = Path(__file__).parents[1]
HEADER = "# SPDX-License-Identifier: AGPL-3.0-only\n# Copyright (C) 2026 Francesco Campanile\n"


def _sources() -> list[Path]:
    files = [path for folder in ("src", "tests", "alembic")
             for path in (ROOT / folder).rglob("*.py")]
    return [*files, ROOT / "alembic" / "script.py.mako"]


def test_every_source_file_starts_with_the_license_header():
    sources = _sources()

    missing = [str(path.relative_to(ROOT)) for path in sources
               if not path.read_text().startswith(HEADER)]

    assert len(sources) > 300
    assert missing == []
