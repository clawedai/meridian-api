from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
import uvicorn
import asyncio
import logging

from app.core.config import settings
from app.core.logging import setup_logging
from app.core.request_logging import RequestLoggingMiddleware
from app.core.sentry import init_sentry
from app.core.rate_limit import get_rate_limiter
from app.api.v1 import api_router
from app.api.v1.health import router as health_router

# APScheduler for the Alert Engine
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.events import EVENT_JOB_ERROR, EVENT_JOB_EXECUTED
from app.services.alert_engine import AlertEngine

logger = logging.getLogger(__name__)

# Global scheduler instance (lifespan-scoped)
_scheduler: AsyncIOScheduler | None = None


def _on_job_completed(event):
    """Log successful alert engine runs."""
    if event.job_id == "alert_engine_cycle":
        logger.info("ALERT ENGINE: Job completed successfully at %s", event.scheduled_run_time)


def _on_job_error(event):
    """Log failed alert engine runs."""
    if event.job_id == "alert_engine_cycle":
        logger.error("ALERT ENGINE: Job failed with exception: %s", event.exception)


def start_alert_scheduler() -> AsyncIOScheduler:
    """Create and start the APScheduler with the alert engine job (every 6 hours)."""
    scheduler = AsyncIOScheduler(timezone="UTC")
    scheduler.add_listener(_on_job_completed, EVENT_JOB_EXECUTED)
    scheduler.add_listener(_on_job_error, EVENT_JOB_ERROR)

    engine = AlertEngine()
    scheduler.add_job(
        engine.run_cycle,
        "interval",
        hours=6,
        id="alert_engine_cycle",
        name="Alert Engine Score Cycle",
        replace_existing=True,
        misfire_grace_time=60 * 15,  # 15-minute grace for missed runs
    )

    scheduler.start()
    logger.info("ALERT ENGINE: Scheduler started — alert cycle every 6 hours")
    return scheduler


# Setup structured logging
setup_logging()

# Initialize Sentry if DSN is configured
if settings.SENTRY_DSN:
    init_sentry()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler for startup/shutdown events."""
    global _scheduler

    # Startup
    rate_limiter = get_rate_limiter()
    rate_limiter.start()

    # Start the Alert Engine scheduler
    _scheduler = start_alert_scheduler()

    yield

    # Shutdown: stop rate limiter cleanup
    if rate_limiter._cleanup_task:
        rate_limiter._cleanup_task.cancel()
        try:
            await rate_limiter._cleanup_task
        except asyncio.CancelledError:
            pass

    # Shutdown: stop Alert Engine scheduler
    if _scheduler is not None and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("ALERT ENGINE: Scheduler shut down")


app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    openapi_url=f"{settings.API_V1_STR}/openapi.json",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

# Request logging middleware (add first to log all requests)
app.add_middleware(RequestLoggingMiddleware)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.BACKEND_CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include API routes
app.include_router(api_router, prefix=settings.API_V1_STR)

# Health check routes (outside /api/v1 prefix for simplicity)
app.include_router(health_router)

@app.get("/")
async def root():
    return {
        "name": settings.PROJECT_NAME,
        "version": settings.VERSION,
        "status": "running"
    }

if __name__ == "__main__":
    import os
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=port,
        reload=True,
    )
