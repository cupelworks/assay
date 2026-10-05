# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Francesco Campanile
"""Advancing a batch that runs until there's an answer: after each wave, run
the next one or stop."""
import logging
import uuid
from collections.abc import Callable

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from assay import batch_waves, sequential
from assay.models import StatisticalBatchModel, TestRunModel
from assay.timestamps import utc_now

logger = logging.getLogger(__name__)


def batch_to_advance(run_id: uuid.UUID, session: Session) -> uuid.UUID | None:
    """The batch a just-finished run belongs to, when that run completed its
    wave: a batch that runs in waves, still releasing them, with nothing left
    pending or running. None otherwise."""
    batch_id = session.scalar(select(TestRunModel.batch_id).where(TestRunModel.id == run_id))
    if batch_id is None:
        return None
    batch = session.get(StatisticalBatchModel, batch_id)
    if batch is None or batch.engine not in batch_waves.SEQUENTIAL_ENGINES \
            or batch.waves_closed_at is not None:
        return None
    return None if batch_waves.in_flight(batch_id, session) else batch_id


def advance_batch(batch_id: uuid.UUID, session: Session,
                  publish: Callable[[uuid.UUID], object]) -> None:
    """Once a wave has finished: stop when every judged check has its answer
    or the last look is reached, else release the next wave and publish its
    runs. Safe to call twice for one wave — releasing and closing are
    conditional updates on the wave count, so a second call does nothing —
    and safe against a hand Stop, which closes the batch first.

    Args:
        batch_id: The batch.
        session: Active sync SQLAlchemy session (assay.worker.db).
        publish: Publishes execute_run for one run id — passed in by the task,
            so this module never imports the tasks package.
    """
    batch = session.get(StatisticalBatchModel, batch_id)
    if batch is None or batch.engine not in batch_waves.SEQUENTIAL_ENGINES \
            or batch.waves_closed_at is not None or batch.waves_released is None:
        return
    if batch_waves.in_flight(batch_id, session):
        return
    released = batch.waves_released
    looks = batch.plan["waves"]["looks"]
    checks = batch_waves.judged_checks(batch, session)
    if batch.engine == "sequential_t":
        plan = sequential.WavePlan.from_json(batch.plan["waves"])
        found = batch_waves.scores(batch_id, session)
        undecided = [
            check for check in checks
            if sequential.decide_mean(found.get((check.entry_id, check.label), []), plan,
                                      check.threshold, check.higher_is_better,
                                      through_look=released)[0] is None]
    else:
        found = batch_waves.outcomes(batch_id, session)
        undecided = [
            check for check in checks
            if sequential.decide(found.get((check.entry_id, check.label), []),
                                 batch_waves.wave_plan_for(batch, check.target), check.target,
                                 through_look=released,
                                 agreement=check.agreement).verdict is None]

    if not undecided or released >= len(looks):
        closed = session.execute(
            update(StatisticalBatchModel)
            .where(StatisticalBatchModel.id == batch_id,
                   StatisticalBatchModel.waves_released == released,
                   StatisticalBatchModel.waves_closed_at.is_(None))
            .values(waves_closed_at=utc_now()))
        session.commit()
        if closed.rowcount:
            why = "every check has its answer" if not undecided else "the maximum is reached"
            logger.info(
                "Batch %s stopped after %d of %d waves (%d times): %s",
                batch_id, released, len(looks), looks[released - 1], why,
                extra={"batch_id": batch_id, "waves": released, "times": looks[released - 1],
                       "undecided": len(undecided)})
        return

    moved = session.execute(
        update(StatisticalBatchModel)
        .where(StatisticalBatchModel.id == batch_id,
               StatisticalBatchModel.waves_released == released,
               StatisticalBatchModel.waves_closed_at.is_(None))
        .values(waves_released=released + 1))
    if not moved.rowcount:
        session.rollback()
        return
    executions, runs = batch_waves.next_wave(batch, session, looks[released - 1] + 1,
                                             looks[released])
    session.add_all(executions)
    session.add_all(runs)
    session.commit()
    logger.info(
        "Batch %s released wave %d of %d: times %d to %d, %d runs, %d checks undecided",
        batch_id, released + 1, len(looks), looks[released - 1] + 1, looks[released],
        len(runs), len(undecided),
        extra={"batch_id": batch_id, "wave": released + 1, "run_count": len(runs),
               "undecided": len(undecided)})
    for run in runs:
        publish(run.id)


def stalled_batches(session: Session) -> list[uuid.UUID]:
    """Batches that run in waves, still releasing them, with nothing in flight
    — a wave whose advance was lost (a worker died between its last run and
    the advance). The reconciliation scan advances them; advancing one that
    isn't stalled does nothing."""
    open_batches = session.scalars(
        select(StatisticalBatchModel.id).where(
            StatisticalBatchModel.engine.in_(batch_waves.SEQUENTIAL_ENGINES),
            StatisticalBatchModel.waves_closed_at.is_(None),
            StatisticalBatchModel.waves_released.is_not(None))).all()
    return [batch_id for batch_id in open_batches if not batch_waves.in_flight(batch_id, session)]
