from fastapi import APIRouter
from .auth import router as auth_router
from .entities import router as entities_router
from .insights import router as insights_router
from .dashboard import router as dashboard_router
from .sources import router as sources_router
from .alerts import router as alerts_router
from .reports import router as reports_router
from .pipeline import router as pipeline_router
from .billing import router as billing_router
from .me import router as me_router
from .competitive_groups import router as competitive_groups_router
from .prospects import router as prospects_router
from .linkedin import router as linkedin_router

api_router = APIRouter()

api_router.include_router(auth_router)
api_router.include_router(entities_router)
api_router.include_router(insights_router)
api_router.include_router(dashboard_router)
api_router.include_router(sources_router)
api_router.include_router(alerts_router)
api_router.include_router(reports_router)
api_router.include_router(pipeline_router)
api_router.include_router(billing_router)
api_router.include_router(me_router)
api_router.include_router(competitive_groups_router)
api_router.include_router(prospects_router)
api_router.include_router(linkedin_router)
