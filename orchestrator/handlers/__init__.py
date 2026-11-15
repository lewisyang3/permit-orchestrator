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
from orchestrator.handlers.ui import (
    FALLBACK_HTML,
    get_assets_dir,
    get_dist_dir,
    get_index_path,
    health,
    hello,
    router as ui_router,
    serve_asset,
    serve_index,
    spa_fallback,
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
    "ui_router",
    "FALLBACK_HTML",
    "get_assets_dir",
    "get_dist_dir",
    "get_index_path",
    "health",
    "hello",
    "serve_asset",
    "serve_index",
    "spa_fallback",
]
