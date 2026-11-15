"""
Hello-World & Health Handlers for Permit Orchestrator.

Provides standard readiness/liveness health checks and a public hello-world
endpoint reporting service operational status and UTC timestamp.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict

from fastapi import APIRouter, status

router = APIRouter(tags=["health"])


@router.get("/health", status_code=status.HTTP_200_OK)
async def health() -> Dict[str, str]:
    """Liveness and health check endpoint."""
    return {"status": "healthy"}


@router.get("/hello", status_code=status.HTTP_200_OK)
async def hello() -> Dict[str, Any]:
    """Public hello-world endpoint returning service status and timestamp."""
    return {
        "status": "healthy",
        "message": "Hello, World! Permit Orchestrator is operational.",
        "service": "permit-orchestrator",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
