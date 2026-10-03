import ast
from pathlib import Path

import assay
from assay.code_fingerprint import API_ONLY, CODE_FINGERPRINT, compute_fingerprint
from assay.worker.celery_app import CODE_COMMAND, _report_code


def _package(tmp_path, files):
    for name, text in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return tmp_path


def test_the_fingerprint_is_short_and_the_same_for_the_same_code(tmp_path):
    package = _package(tmp_path, {"worker/a.py": "x = 1\n", "schemas.py": "y = 2\n"})

    assert compute_fingerprint(package) == compute_fingerprint(package)
    assert len(compute_fingerprint(package)) == 12
    assert len(CODE_FINGERPRINT) == 12


def test_a_change_to_code_the_worker_runs_changes_it(tmp_path):
    package = _package(tmp_path, {"worker/a.py": "x = 1\n", "schemas.py": "y = 2\n"})
    before = compute_fingerprint(package)

    (package / "schemas.py").write_text("y = 3\n")

    assert compute_fingerprint(package) != before


def test_a_change_to_code_only_the_api_runs_doesnt(tmp_path):
    package = _package(tmp_path, {"worker/a.py": "x = 1\n", "api/routes.py": "r = 1\n",
                                  "services/s.py": "s = 1\n", "main.py": "m = 1\n"})
    before = compute_fingerprint(package)

    for name in ("api/routes.py", "services/s.py", "main.py"):
        (package / name).write_text("changed = True\n")

    assert compute_fingerprint(package) == before
    assert {"api", "services", "main.py"} <= API_ONLY


def test_a_renamed_file_changes_it(tmp_path):
    package = _package(tmp_path, {"worker/a.py": "x = 1\n"})
    before = compute_fingerprint(package)

    (package / "worker/a.py").rename(package / "worker/b.py")

    assert compute_fingerprint(package) != before


def test_a_worker_reports_its_fingerprint_and_version():
    assert CODE_COMMAND == "assay_code"
    assert _report_code(None) == {"fingerprint": CODE_FINGERPRINT, "version": "1.1.0"}


# --- the rule the fingerprint relies on ---

PACKAGE = Path(assay.__file__).resolve().parent


def _module_name(entry: str) -> str:
    return entry.removesuffix(".py")


def _imported_modules(path: Path) -> set[str]:
    """Every assay module path imports, anywhere in it — inside functions too."""
    modules = set()
    for node in ast.walk(ast.parse(path.read_text(), filename=str(path))):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
            if node.module == "assay":  # from assay import services
                modules.update(f"assay.{alias.name}" for alias in node.names)
    return {module for module in modules if module.split(".")[0] == "assay"}


def test_every_api_only_name_still_exists():
    # a rename would otherwise leave a stale name here and fingerprint the moved code
    for entry in API_ONLY:
        assert (PACKAGE / entry).exists(), f"{entry} is listed as API-only but doesn't exist"


def test_no_fingerprinted_module_imports_api_only_code():
    """The fingerprint leaves out what only the API runs, so it's only right
    while nothing it covers — the code a worker can load — imports that code.
    A worker importing from services/, say, would run code whose changes the
    fingerprint can't see: move that code out of the API-only part instead."""
    api_only = {f"assay.{_module_name(entry)}" for entry in API_ONLY}
    offenders = []
    for path in sorted(PACKAGE.rglob("*.py")):
        relative = path.relative_to(PACKAGE)
        if relative.parts[0] in API_ONLY:
            continue
        for module in _imported_modules(path):
            if any(module == name or module.startswith(name + ".") for name in api_only):
                offenders.append(f"{relative} imports {module}")

    assert offenders == []


def test_the_rule_catches_an_import_from_api_only_code(tmp_path):
    module = tmp_path / "worker_thing.py"
    module.write_text("def f():\n    from assay.services.runs import _common\n"
                      "from assay import api\n")

    assert _imported_modules(module) == {"assay.services.runs", "assay", "assay.api"}
