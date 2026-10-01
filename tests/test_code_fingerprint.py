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
    assert _report_code(None) == {"fingerprint": CODE_FINGERPRINT, "version": "0.10.0"}
