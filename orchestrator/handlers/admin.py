"""
Admin fault forwarding endpoints for orchestrator (Task T5).

Allows inspecting and controlling fault modes across all four municipal departments
(zoning, fire, water, structural) via POST /admin/fault, GET /admin/fault, and POST /admin/reset.
"""

from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator, Dict, List, Optional, Tuple

import httpx
from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request, status

from contract.models import (
    AdminFaultResponse,
    DepartmentName,
    FaultControlRequest,
    FaultControlResponse,
    FaultMode,
)
from orchestrator.dispatcher import get_default_endpoints, resolve_endpoint_url

router = APIRouter(tags=["admin"])

ALL_DEPARTMENTS: List[DepartmentName] = [
    DepartmentName.ZONING,
    DepartmentName.FIRE,
    DepartmentName.WATER,
    DepartmentName.STRUCTURAL,
]


def resolve_fault_url(base_or_eval_url: str) -> str:
    """Resolve department base URL or evaluate URL into the /fault endpoint URL."""
    eval_url = resolve_endpoint_url(base_or_eval_url)
    if eval_url.endswith("/evaluate"):
        return eval_url[:-len("/evaluate")] + "/fault"
    return f"{eval_url.rstrip('/')}/fault"


async def get_http_client(request: Request) -> AsyncIterator[httpx.AsyncClient]:
    """Provide httpx.AsyncClient from app.state if available, or create temporary one."""
    app_client = getattr(request.app.state, "http_client", None)
    if app_client is None:
        orch = getattr(request.app.state, "orchestrator", None)
        if orch is not None:
            app_client = getattr(orch, "http_client", None)
    if app_client is not None:
        yield app_client
        return

    async with httpx.AsyncClient(timeout=5.0) as client:
        yield client


def get_endpoints_dep(request: Request) -> Dict[str, str]:
    """Retrieve department endpoint dictionary, with support for request.app.state overrides."""
    custom = getattr(request.app.state, "department_endpoints", None)
    if custom is None:
        orch = getattr(request.app.state, "orchestrator", None)
        if orch is not None:
            custom = getattr(orch, "endpoints", None)
    if custom is not None:
        return custom
    return get_default_endpoints()


async def _query_department_fault(
    client: httpx.AsyncClient,
    dept: DepartmentName,
    endpoints: Dict[str, str],
) -> Tuple[str, FaultControlResponse]:
    """Query fault configuration from a single department microservice."""
    dept_key = dept.value
    dept_url = endpoints.get(dept_key)
    if not dept_url:
        return dept_key, FaultControlResponse(
            department=dept,
            mode=FaultMode.NONE,
            delay_ms=3000,
            message="Endpoint unconfigured",
        )

    fault_url = resolve_fault_url(dept_url)
    try:
        resp = await client.get(fault_url, timeout=3.0)
        if resp.status_code == 200:
            data = resp.json()
            if not isinstance(data, dict):
                return dept_key, FaultControlResponse(
                    department=dept,
                    mode=FaultMode.NONE,
                    delay_ms=3000,
                    message=f"Invalid JSON response: {data}",
                )
            if not data.get("department"):
                data["department"] = dept.value
            return dept_key, FaultControlResponse.model_validate(data)
        else:
            return dept_key, FaultControlResponse(
                department=dept,
                mode=FaultMode.NONE,
                delay_ms=3000,
                message=f"HTTP {resp.status_code}",
            )
    except Exception as exc:
        return dept_key, FaultControlResponse(
            department=dept,
            mode=FaultMode.NONE,
            delay_ms=3000,
            message=f"Offline / Unreachable: {exc}",
        )


async def _reset_department_fault(
    client: httpx.AsyncClient,
    dept: DepartmentName,
    endpoints: Dict[str, str],
) -> Tuple[str, FaultControlResponse]:
    """Reset fault configuration on a single department microservice to NONE."""
    dept_key = dept.value
    dept_url = endpoints.get(dept_key)
    if not dept_url:
        return dept_key, FaultControlResponse(
            department=dept,
            mode=FaultMode.NONE,
            delay_ms=3000,
            message="Endpoint unconfigured",
        )

    fault_url = resolve_fault_url(dept_url)
    payload = {"department": dept_key, "mode": "none", "delay_ms": 3000}
    try:
        resp = await client.post(fault_url, json=payload, timeout=3.0)
        if resp.status_code == 200:
            data = resp.json()
            if not isinstance(data, dict):
                return dept_key, FaultControlResponse(
                    department=dept,
                    mode=FaultMode.NONE,
                    delay_ms=3000,
                    message=f"Invalid JSON response: {data}",
                )
            if not data.get("department"):
                data["department"] = dept.value
            return dept_key, FaultControlResponse.model_validate(data)
        else:
            return dept_key, FaultControlResponse(
                department=dept,
                mode=FaultMode.NONE,
                delay_ms=3000,
                message=f"HTTP {resp.status_code}",
            )
    except Exception as exc:
        return dept_key, FaultControlResponse(
            department=dept,
            mode=FaultMode.NONE,
            delay_ms=3000,
            message=f"Offline / Unreachable: {exc}",
        )


@router.post(
    "/admin/fault",
    response_model=FaultControlResponse,
    summary="Inject fault into a target department",
)
@router.post(
    "/fault",
    response_model=FaultControlResponse,
    include_in_schema=False,
)
async def admin_set_fault(
    req: FaultControlRequest,
    client: httpx.AsyncClient = Depends(get_http_client),
    endpoints: Dict[str, str] = Depends(get_endpoints_dep),
) -> FaultControlResponse:
    """Forward fault control configuration to target department's /fault endpoint."""
    if not req.department:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Department is required for admin fault injection",
        )

    dept_key = req.department.value if isinstance(req.department, DepartmentName) else str(req.department)
    dept_url = endpoints.get(dept_key)
    if not dept_url:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unknown department '{dept_key}'",
        )

    fault_url = resolve_fault_url(dept_url)
    payload = req.model_dump(mode="json")

    try:
        resp = await client.post(fault_url, json=payload, timeout=5.0)
        resp.raise_for_status()
        data = resp.json()
        if not isinstance(data, dict):
            raise ValueError(f"Unexpected response format: {data}")
        if not data.get("department"):
            data["department"] = dept_key
        return FaultControlResponse.model_validate(data)
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=exc.response.status_code,
            detail=f"Department fault injection failed: {exc.response.text}",
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to communicate with department service: {exc}",
        )


@router.get(
    "/admin/fault",
    response_model=AdminFaultResponse,
    summary="Get active fault configuration for all departments",
)
@router.get(
    "/fault",
    response_model=AdminFaultResponse,
    include_in_schema=False,
)
async def admin_get_fault(
    client: httpx.AsyncClient = Depends(get_http_client),
    endpoints: Dict[str, str] = Depends(get_endpoints_dep),
) -> AdminFaultResponse:
    """Query current fault status from all four departments concurrently."""
    tasks = [
        _query_department_fault(client, dept, endpoints)
        for dept in ALL_DEPARTMENTS
    ]
    results = dict(await asyncio.gather(*tasks))

    return AdminFaultResponse(
        zoning=results[DepartmentName.ZONING.value],
        fire=results[DepartmentName.FIRE.value],
        water=results[DepartmentName.WATER.value],
        structural=results[DepartmentName.STRUCTURAL.value],
    )


@router.post(
    "/admin/reset",
    response_model=AdminFaultResponse,
    summary="Reset fault configurations across departments",
)
@router.post(
    "/reset",
    response_model=AdminFaultResponse,
    include_in_schema=False,
)
async def admin_reset(
    req: Optional[FaultControlRequest] = Body(None),
    department: Optional[str] = Query(None),
    client: httpx.AsyncClient = Depends(get_http_client),
    endpoints: Dict[str, str] = Depends(get_endpoints_dep),
) -> AdminFaultResponse:
    """Reset fault mode to NONE across departments (or for a specific target department)."""
    target: Optional[DepartmentName] = None
    if req is not None and req.department is not None:
        target = req.department
    elif department:
        dept_str = department.strip().lower()
        if dept_str not in ("all", "*", ""):
            try:
                target = DepartmentName(dept_str)
            except ValueError:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Unknown department '{department}'",
                )

    if target is not None:
        dept_key = target.value if isinstance(target, DepartmentName) else str(target)
        dept_url = endpoints.get(dept_key)
        if not dept_url:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Unknown department '{dept_key}'",
            )
        fault_url = resolve_fault_url(dept_url)
        payload = {"department": dept_key, "mode": "none", "delay_ms": 3000}
        try:
            resp = await client.post(fault_url, json=payload, timeout=5.0)
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise HTTPException(
                status_code=exc.response.status_code,
                detail=f"Department fault reset failed: {exc.response.text}",
            )
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Failed to communicate with department service: {exc}",
            )

        # After resetting target department, query all departments to reflect updated state
        tasks = [
            _query_department_fault(client, d, endpoints)
            for d in ALL_DEPARTMENTS
        ]
        results = dict(await asyncio.gather(*tasks))
        return AdminFaultResponse(
            zoning=results[DepartmentName.ZONING.value],
            fire=results[DepartmentName.FIRE.value],
            water=results[DepartmentName.WATER.value],
            structural=results[DepartmentName.STRUCTURAL.value],
        )

    # Reset all departments concurrently
    tasks = [
        _reset_department_fault(client, dept, endpoints)
        for dept in ALL_DEPARTMENTS
    ]
    results = dict(await asyncio.gather(*tasks))

    return AdminFaultResponse(
        zoning=results[DepartmentName.ZONING.value],
        fire=results[DepartmentName.FIRE.value],
        water=results[DepartmentName.WATER.value],
        structural=results[DepartmentName.STRUCTURAL.value],
    )
