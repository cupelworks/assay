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
    reachable: bool = Field(description="Whether the API could connect to the message broker.")
    error: str | None = Field(
        None, description="Why it couldn't, when it couldn't; null when reachable.",
    )


class WorkerStatus(BaseModel):
    name: str = Field(description="The worker's node name, e.g. `celery@host`.")
    missing_tasks: list[str] = Field(
        description="Tasks the API sends that this worker doesn't have, by name — empty "
                    "when it's up to date. A worker missing one is running older code: "
                    "what it receives of that task is lost, so a run or a check sent to "
                    "it stays waiting.",
    )


class WorkerHealth(BaseModel):
    """Whether runs and checks sent now will be executed."""
    status: WorkerHealthStatus = Field(
        description="`ready`: the broker is reachable and every worker that answered has "
                    "every task. `no_worker`: the broker is reachable but no worker "
                    "answered — runs and checks wait in the queue until one starts. "
                    "`outdated`: at least one worker lacks a task (see `workers`) — "
                    "restart it on the current code. `broker_unreachable`: runs can't be "
                    "sent at all; they stay Pending until the reconciliation scan "
                    "re-sends them, and checks complete at once as not sent.",
    )
    broker: BrokerStatus
    workers: list[WorkerStatus] = Field(
        description="Every worker that answered, by name. Empty when none did, and when "
                    "the broker is unreachable.",
    )
    checked_at: datetime = Field(description="When this was checked.")
