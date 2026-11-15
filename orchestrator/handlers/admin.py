"""
Admin fault forwarding endpoints for orchestrator (Task T5).

Allows inspecting and controlling fault modes across all four municipal departments
(zoning, fire, water, structural) via:
- POST /admin/fault
- GET /admin/fault
- POST /admin/fault/reset
- POST /admin/fault/{department}
- GET /admin/fault/{department}
- POST /admin/fault/{department}/reset
"""

from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator, Dict, List, Optional, Tuple

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, status

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

DEPARTMENT_MAP: Dict[str, DepartmentName] = {
    dept.value: dept for dept in ALL_DEPARTMENTS
}


def parse_department_name(dept_str: str) -> DepartmentName:
    """Parse and validate department name string, raising 404 on unknown department."""
    clean = dept_str.strip().lower()
    dept = DEPARTMENT_MAP.get(clean)
    if dept is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unknown department '{dept_str}'",
        )
    return dept


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


async def _forward_fault_injection(
    client: httpx.AsyncClient,
    dept: DepartmentName,
    req: FaultControlRequest,
    endpoints: Dict[str, str],
) -> FaultControlResponse:
    """Forward fault control configuration to target department's /fault endpoint."""
    dept_key = dept.value
    dept_url = endpoints.get(dept_key)
    if not dept_url:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unknown department '{dept_key}'",
        )

    fault_url = resolve_fault_url(dept_url)
    payload = req.model_dump(mode="json")
    if not payload.get("department"):
        payload["department"] = dept_key

    try:
        resp = await client.post(fault_url, json=payload)
        resp.raise_for_status()
        data = resp.json()
        if not isinstance(data, dict):
            raise ValueError(f"Unexpected response format: {data}")
        if not data.get("department"):
            data["department"] = dept_key
        if not data.get("message") and data.get("error_message"):
            data["message"] = data["error_message"]
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


async def _reset_single_department(
    client: httpx.AsyncClient,
    dept: DepartmentName,
    endpoints: Dict[str, str],
) -> Tuple[str, FaultControlResponse]:
    """Reset fault mode to NONE on a single department microservice."""
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
        resp = await client.post(fault_url, json=payload)
        if resp.status_code == 200:
            data = resp.json()
            if isinstance(data, dict):
                if not data.get("department"):
                    data["department"] = dept_key
                return dept_key, FaultControlResponse.model_validate(data)
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
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Department is required for admin fault injection",
        )

    dept = (
        req.department
        if isinstance(req.department, DepartmentName)
        else parse_department_name(str(req.department))
    )
    return await _forward_fault_injection(client, dept, req, endpoints)


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
    "/admin/fault/reset",
    response_model=AdminFaultResponse,
    summary="Reset fault mode to NONE for all departments",
)
@router.post(
    "/fault/reset",
    response_model=AdminFaultResponse,
    include_in_schema=False,
)
async def admin_reset_all_faults(
    client: httpx.AsyncClient = Depends(get_http_client),
    endpoints: Dict[str, str] = Depends(get_endpoints_dep),
) -> AdminFaultResponse:
    """Forward fault reset command (mode=NONE) to all 4 department microservices."""
    tasks = [
        _reset_single_department(client, dept, endpoints)
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
    "/admin/fault/{department}/reset",
    response_model=FaultControlResponse,
    summary="Reset fault mode to NONE for a specific department",
)
@router.post(
    "/fault/{department}/reset",
    response_model=FaultControlResponse,
    include_in_schema=False,
)
async def admin_reset_department_fault(
    department: str,
    client: httpx.AsyncClient = Depends(get_http_client),
    endpoints: Dict[str, str] = Depends(get_endpoints_dep),
) -> FaultControlResponse:
    """Reset fault mode to NONE for a specific department."""
    dept = parse_department_name(department)
    req = FaultControlRequest(department=dept, mode=FaultMode.NONE)
    return await _forward_fault_injection(client, dept, req, endpoints)


@router.post(
    "/admin/fault/{department}",
    response_model=FaultControlResponse,
    summary="Inject fault into a target department by path parameter",
)
@router.post(
    "/fault/{department}",
    response_model=FaultControlResponse,
    include_in_schema=False,
)
async def admin_set_department_fault(
    department: str,
    req: Optional[FaultControlRequest] = None,
    client: httpx.AsyncClient = Depends(get_http_client),
    endpoints: Dict[str, str] = Depends(get_endpoints_dep),
) -> FaultControlResponse:
    """Forward fault injection configuration to target department specified in path."""
    dept = parse_department_name(department)
    if req is None:
        req = FaultControlRequest(department=dept, mode=FaultMode.NONE)
    else:
        if req.department is not None and req.department != dept:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Department mismatch: path '{dept.value}' vs body '{req.department.value}'",
            )
        req.department = dept

    return await _forward_fault_injection(client, dept, req, endpoints)


@router.get(
    "/admin/fault/{department}",
    response_model=FaultControlResponse,
    summary="Get active fault configuration for a target department",
)
@router.get(
    "/fault/{department}",
    response_model=FaultControlResponse,
    include_in_schema=False,
)
async def admin_get_department_fault(
    department: str,
    client: httpx.AsyncClient = Depends(get_http_client),
    endpoints: Dict[str, str] = Depends(get_endpoints_dep),
) -> FaultControlResponse:
    """Query fault configuration for a single department microservice."""
    dept = parse_department_name(department)
    _, response = await _query_department_fault(client, dept, endpoints)
    return response
