"""The Swagger example of one execution read whole, for a test set's and a
test plan's alike: the two differ only in the key naming their scope."""
from assay.api._standing_examples import run_counts


def execution_details(scope_key: str, scope_name: str) -> dict:
    """An execution of three runs, one Green, one Amber and one that couldn't
    run; `scope_key` is `test_set_id` or `test_plan_id`, `scope_name` the set's
    or plan's name."""
    return {
        "id": "e5f6a7b8-c9d0-1234-ef56-7890abcdef12",
        "name": scope_name,
        "created_at": "2026-07-15T16:44:30.163355Z",
        "batch_id": None,
        "batch_index": None,
        scope_key: {"id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890"},
        "run_count": 3,
        "replayed_execution_id": None,
        "runs": run_counts(Green=1, Amber=1, NotRan=1),
        "checks": {
            "met": 3,
            "not_met": [{"run_id": "f6a7b8c9-d0e1-2345-fa67-890abcdef123",
                         "test_name": "Promo code format", "label": "Regex Match"}],
            "not_ran": [{"run_id": "a7b8c9d0-1234-5abc-def6-789012345bcd",
                         "test_name": "Refund window", "checks": 2,
                         "error": "The application under test didn't answer within 60 s"}],
        },
    }
