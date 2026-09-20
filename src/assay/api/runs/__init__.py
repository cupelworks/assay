from fastapi import APIRouter

from assay.api.runs.all import router as all_router
from assay.api.runs.standalone import router as standalone_router
from assay.api.runs.test_plans import router as test_plans_router
from assay.api.runs.test_sets import router as test_sets_router

router = APIRouter()

router.include_router(all_router)
router.include_router(standalone_router)
router.include_router(test_sets_router)
router.include_router(test_plans_router)
