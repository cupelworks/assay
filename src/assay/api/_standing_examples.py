"""Swagger example fragments for how a test, test set or test plan stands,
written once and spread into every example that shows one, so the lists, the
details and the reverse lookups can't show it differently."""


def run_counts(**counts: int) -> dict:
    """Runs by status, every status present, as `RunCounts` returns them."""
    return {status: counts.get(status, 0)
            for status in ("Pending", "Running", "Green", "Amber", "Red", "NotRan")}


LATEST_BATCH = {"id": "b2c3d4e5-f6a7-8901-bcde-f23456789012", "status": "Passed",
                "created_at": "2026-10-02T15:30:00"}

LATEST_EXECUTION = {"id": "e5f6a7b8-c9d0-1234-ef56-7890abcdef12",
                    "created_at": "2026-10-01T09:00:00", "replayed": False,
                    "runs": run_counts(Green=11, Amber=1)}

SCOPE_RAN = {"latest_batch": LATEST_BATCH, "latest_execution": LATEST_EXECUTION,
             "execution_count": 3, "has_runs": True}
"""A test set or plan run three times outside a batch, and once with statistics."""

SCOPE_NEVER_RAN = {"latest_batch": None, "latest_execution": None, "execution_count": 0,
                   "has_runs": False}

TEST_STANDING = {"created_at": "2026-09-30T10:00:00",
                 "dataset_row_id": "c3d4e5f6-a7b8-9012-cdef-345678901234",
                 "dataset_row_number": 7, "latest_batch": None,
                 "latest_run": {"id": "d4e5f6a7-b8c9-0123-def4-56789012345a",
                                "status": "Green", "created_at": "2026-10-01T08:45:00"},
                 "has_runs": True, "copy_count": 2, "test_set_count": 1}
"""A test made from a dataset's row 7, run on its own, copied into two set
entries of which one is still in a set."""
