"""Whether the parts of Assay outside the API can do their work."""
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class WorkerHealthStatus(StrEnum):
    ready = "ready"
    no_worker = "no_worker"
    outdated = "outdated"
    broker_unreachable = "broker_unreachable"


class BrokerStatus(BaseModel):
    reachable: bool = Field(
        description="Whether the API could connect to the message broker (Redis), the "
                    "queue runs and checks travel through to the workers.",
    )
    error: str | None = Field(
        None, description="Why it couldn't, when it couldn't; null when reachable.",
    )


class WorkerStatus(BaseModel):
    """One worker that answered, and whether it can be trusted with work."""
    name: str = Field(description="The worker's node name, e.g. `celery@worker1`.")
    version: str | None = Field(
        description="The Assay version the worker runs, e.g. `2.0.0`. Null for a worker "
                    "started on code older than this report.",
    )
    code_fingerprint: str | None = Field(
        description="A short hash of the code the worker runs, taken when it started. "
                    "Null for a worker started on code older than this report.",
    )
    current_code: bool = Field(
        description="Whether the worker runs the same code as the API: its "
                    "`code_fingerprint` equals the response's. False means it started "
                    "before the latest code change and must be restarted.",
    )
    missing_tasks: list[str] = Field(
        description="Kinds of job (tasks) the API sends that this worker doesn't know, by "
                    "name — empty when it knows them all. A job of a kind it doesn't know "
                    "is dropped, so that run or check waits forever.",
    )
    problem: str | None = Field(
        description="What's wrong with this worker and what to do, in one sentence to "
                    "show as it is; null when it's fine.",
    )


class WorkerHealth(BaseModel):
    """Whether runs and checks sent now will be executed."""
    status: WorkerHealthStatus = Field(
        description="`ready`: the broker is reachable and every worker that answered "
                    "runs the current code — runs and checks will be executed. "
                    "`no_worker`: the broker is reachable but no worker answered — runs "
                    "and checks wait in the queue until one starts. `outdated`: at least "
                    "one worker runs older code (see each worker's `problem`) — restart "
                    "it; meanwhile a run or check it picks up can fail or wait. "
                    "`broker_unreachable`: nothing can be sent — runs stay Pending until "
                    "the reconciliation scan re-sends them, and checks complete at once "
                    "as not sent.",
    )
    broker: BrokerStatus
    workers: list[WorkerStatus] = Field(
        description="Every worker that answered, by name. Empty when none did, and when "
                    "the broker is unreachable.",
    )
    version: str = Field(description="The Assay version the API runs.")
    code_fingerprint: str = Field(
        description="The fingerprint of the code the API runs — what each worker's "
                    "`code_fingerprint` is compared with.",
    )
    checked_at: datetime = Field(description="When this was checked.")
