from __future__ import annotations

from typing import NoReturn

from fastapi import APIRouter, HTTPException


router = APIRouter(tags=["reserved"])


def mcp_placeholder() -> NoReturn:
    raise HTTPException(status_code=501, detail="Hosted MCP not implemented")


router.add_api_route(
    "/mcp",
    mcp_placeholder,
    methods=["GET"],
    status_code=501,
    response_model=None,
    responses={501: {"description": "Hosted MCP not implemented"}},
    description="Reserved placeholder; no hosted MCP implementation in B2.",
)
router.add_api_route(
    "/mcp",
    mcp_placeholder,
    methods=["POST"],
    status_code=501,
    response_model=None,
    responses={501: {"description": "Hosted MCP not implemented"}},
    description="Reserved placeholder; no hosted MCP implementation in B2.",
)
router.add_api_route(
    "/mcp",
    mcp_placeholder,
    methods=["DELETE"],
    status_code=501,
    response_model=None,
    responses={501: {"description": "Hosted MCP not implemented"}},
    description="Reserved placeholder; no hosted MCP implementation in B2.",
)
