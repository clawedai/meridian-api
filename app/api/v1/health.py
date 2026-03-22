"""Health check API endpoints."""

from datetime import datetime, timezone

from fastapi import APIRouter, status
from fastapi.responses import JSONResponse

from app.services.health import health_service

router = APIRouter(prefix="/health", tags=["health"])


@router.get(
    "",
    summary="Basic liveness check",
    description="Returns 200 if the application is running. Use this for basic health checks.",
)
async def health_check() -> dict:
    """
    Basic liveness probe.

    Returns:
        dict: Simple status indicating the app is alive.
    """
    return {
        "status": "healthy",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "message": "Application is running",
    }


@router.get(
    "/ready",
    summary="Readiness check",
    description="Runs all health checks and returns overall status. Returns 200 if all healthy, 503 if any unhealthy.",
    responses={
        200: {"description": "All health checks passed"},
        503: {"description": "One or more health checks failed"},
    },
)
async def readiness_check() -> JSONResponse:
    """
    Readiness probe that runs all dependency health checks.

    Returns:
        JSONResponse: 200 if all healthy, 503 if any unhealthy.
    """
    result = await health_service.check_all()

    if result["status"] == "unhealthy":
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content=result,
        )

    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content=result,
    )


@router.get(
    "/detailed",
    summary="Detailed health status",
    description="Returns full health status with complete component breakdown including messages and timestamps.",
    responses={
        200: {"description": "Detailed health status"},
        503: {"description": "One or more health checks failed"},
    },
)
async def detailed_health_check() -> JSONResponse:
    """
    Detailed health check with full component breakdown.

    Returns:
        JSONResponse: Full health status with all check details.
    """
    result = await health_service.check_all()

    if result["status"] == "unhealthy":
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content=result,
        )

    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content=result,
    )
