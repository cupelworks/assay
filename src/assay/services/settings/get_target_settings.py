from sqlalchemy.ext.asyncio import AsyncSession

from assay.schemas import TargetSettingsRead
from assay.services.settings._common import _find_target_row
from assay.target_settings import resolve_target_settings


async def get_target_settings(session: AsyncSession) -> TargetSettingsRead:
    """The application-under-test settings in effect, and where they come
    from: the saved row when there is one, else the environment. Header
    values are returned exactly as stored — a ${NAME} reference is never
    expanded.

    Args:
        session: Active async database session.

    Raises:
        pydantic.ValidationError: the saved row no longer validates — a bug
            (a manual edit, or a release tightening a rule without a
            migration), surfaced as a 500 rather than papered over.
    """
    return resolve_target_settings(await _find_target_row(session))
