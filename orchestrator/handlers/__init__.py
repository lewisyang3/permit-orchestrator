"""Orchestrator route handlers package."""

from orchestrator.handlers.admin import (
    router as admin_router,
)
from orchestrator.handlers.stream import (
    get_live_browser_page,
    get_permit_status,
    router as stream_router,
    stream_permit,
)
from orchestrator.handlers.submit import (
    EventBroadcaster,
    broadcaster,
    get_broadcaster,
    get_permit_event,
    permit_events,
    permit_tasks,
    router as submit_router,
    run_permit_saga,
    submit_permit,
)

__all__ = [
    "admin_router",
    "EventBroadcaster",
    "broadcaster",
    "get_broadcaster",
    "get_live_browser_page",
    "get_permit_event",
    "get_permit_status",
    "permit_events",
    "permit_tasks",
    "run_permit_saga",
    "stream_permit",
    "stream_router",
    "submit_permit",
    "submit_router",
]
