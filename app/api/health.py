"""Health, readiness and Prometheus metrics endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Request, Response, status
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from app.core.db import check_database
from app.core.settings import get_settings

router = APIRouter(tags=["Health & Observability"])


@router.get("/health/live")
async def liveness() -> dict[str, str]:
    """Liveness probe: verifies process is alive."""
    return {"status": "alive"}


@router.get("/health/ready")
async def readiness(request: Request) -> Response:
    """Readiness probe: checks DB and downstream services."""
    settings = get_settings()
    engine = getattr(request.app.state, "engine", None)

    db_ok = await check_database(engine) if engine else True
    llm_ok = True
    llm_client = getattr(request.app.state, "llm_client", None)
    if llm_client is not None:
        try:
            llm_ok = await llm_client.health_check()
        except Exception:  # noqa: BLE001
            llm_ok = False

    is_ready = db_ok and (llm_ok or settings.llm_provider == "deterministic")

    status_code = status.HTTP_200_OK if is_ready else status.HTTP_503_SERVICE_UNAVAILABLE
    import json

    content = json.dumps(
        {
            "status": "ready" if is_ready else "unready",
            "database": "connected" if db_ok else "disconnected",
            "llm": "connected" if llm_ok else "disconnected",
            "app_env": settings.app_env,
        }
    )
    return Response(content=content, media_type="application/json", status_code=status_code)


@router.get("/metrics")
async def metrics() -> Response:
    """Prometheus metrics endpoint."""
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)
