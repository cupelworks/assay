# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Francesco Campanile
"""Read one stored comparison."""
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from assay.schemas.statistics import ComparisonDetails
from assay.services.statistics._comparisons import describe_many, find_comparison_or_404


async def get_comparison(comparison_id: uuid.UUID, series: bool,
                         session: AsyncSession) -> ComparisonDetails:
    """Raises: HTTPException 404 for an unknown comparison."""
    comparison = await find_comparison_or_404(comparison_id, session)
    (described,) = await describe_many([comparison], session, with_result=True, series=series)
    return described
