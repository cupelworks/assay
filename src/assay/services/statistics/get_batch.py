# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Francesco Campanile
"""Read one batch: its progress while it runs, its result once every run has
finished — computed and stored by the first read that finds them all done."""
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from assay.schemas.statistics import BatchDetails
from assay.services.statistics._batches import describe, find_batch_or_404, refresh


async def get_batch(batch_id: uuid.UUID, series: bool, session: AsyncSession) -> BatchDetails:
    """Raises: HTTPException 404 for an unknown batch."""
    batch = await find_batch_or_404(batch_id, session)
    await refresh(batch, session)
    return await describe(batch, session, series=series)
