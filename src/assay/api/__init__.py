from fastapi import APIRouter

from assay.api.datasets import router as datasets_router
from assay.api.meta import router as meta_router
from assay.api.runs import router as runs_router
from assay.api.stats import router as stats_router
from assay.api.test import router as test_router
from assay.api.test_plan import router as test_plan_router
from assay.api.test_sets import router as test_sets_router

router = APIRouter()

router.include_router(datasets_router)
router.include_router(test_router)
router.include_router(test_sets_router)
router.include_router(test_plan_router)
router.include_router(runs_router)
router.include_router(stats_router)
router.include_router(meta_router)
