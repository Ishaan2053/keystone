from __future__ import annotations

from fastapi import APIRouter


# B10: the hosted MCP transport is mounted by ``api.main.create_app`` at the app
# root, not registered here; no placeholder route is needed. The empty router is
# kept so the app's router wiring (and its import) is unchanged.
router = APIRouter(tags=["reserved"])
