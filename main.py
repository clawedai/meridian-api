from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
import uvicorn
import asyncio

from app.core.config import settings
from app.core.logging import setup_logging
from app.core.request_logging import RequestLoggingMiddleware
from app.core.sentry import init_sentry
from app.core.rate_limit import get_rate_limiter
from app.api.v1 import api_router
from app.api.v1.health import router as health_router

# Setup structured logging
setup_logging()

# Initialize Sentry if DSN is configured
if settings.SENTRY_DSN:
    init_sentry()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler for startup/shutdown events."""
    # Startup: Start the rate limiter
    rate_limiter = get_rate_limiter()
    rate_limiter.start()

    yield

    # Shutdown: Stop the rate limiter cleanup task
    if rate_limiter._cleanup_task:
        rate_limiter._cleanup_task.cancel()
        try:
            await rate_limiter._cleanup_task
        except asyncio.CancelledError:
            pass


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
