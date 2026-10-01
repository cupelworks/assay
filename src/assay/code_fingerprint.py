"""A fingerprint of the code this process runs, taken when it starts.

A worker keeps running the code it started with until it's restarted, while
the API reloads on every change. Comparing a worker's fingerprint with the
API's tells whether the worker runs the current code — including a task it
has whose code has since changed, which a list of task names can't show.

The fingerprint is a short hash of every `.py` file in the `assay` package
except the code only the API runs (the routes, the API's services, the app
and its middleware, its async database engine), so a change there doesn't
mark the workers out of date. It's computed once, on import — at start-up in
every process — so it describes the code loaded then, not what's on disk
later. Two images built from the same commit share it.
"""
import hashlib
from pathlib import Path

from assay import __version__

_PACKAGE = Path(__file__).resolve().parent
# Code only the API process runs: top-level directories and files
API_ONLY = frozenset({"api", "services", "main.py", "middleware.py", "exception_handlers.py",
                      "db.py"})


def compute_fingerprint(package: Path = _PACKAGE) -> str:
    """The hash of the package's worker-relevant source, 12 hex characters."""
    digest = hashlib.sha256()
    for path in sorted(package.rglob("*.py")):
        relative = path.relative_to(package)
        if relative.parts[0] in API_ONLY:
            continue
        digest.update(relative.as_posix().encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()[:12]


CODE_FINGERPRINT = compute_fingerprint()
CODE_VERSION = __version__
