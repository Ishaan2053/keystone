from __future__ import annotations

import os
from dataclasses import dataclass, field
from urllib.parse import urlparse

from api.github import GitHubWriteError, validate_repo


API_MODES = ("fixtures", "live")
_TRUE = frozenset({"1", "true", "yes", "on"})
_FALSE = frozenset({"0", "false", "no", "off"})


@dataclass(frozen=True)
class Settings:
    tenants: tuple[str, ...] = ("falkordb",)
    cors_origins: tuple[str, ...] = ("http://localhost:3001",)
    api_mode: str = "fixtures"
    # B6 apply service configuration (constructed by the app, not the request).
    sandbox_repo: str | None = None
    dry_run: bool = True
    public_base_url: str | None = None
    # B7 webhook receiver configuration (constructed by the app, not the request).
    # The secret is excluded from repr so it can never appear in logs.
    webhook_secret: str | None = field(default=None, repr=False)
    bot_login: str | None = None
    # B10 hosted MCP: tenant slugs served publicly over HTTP at /mcp. load_settings
    # defaults it to every configured tenant (each is a public org); empty serves none.
    public_mcp_tenants: tuple[str, ...] = ()


def _split_csv(value: str) -> tuple[str, ...]:
    seen: set[str] = set()
    items: list[str] = []
    for raw in value.split(","):
        item = raw.strip()
        if item and item not in seen:
            seen.add(item)
            items.append(item)
    return tuple(items)


def _normalize_origin(origin: str) -> str:
    parsed = urlparse(origin)
    if origin == "*" or parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("CORS_ORIGINS must contain exact http(s) origins")
    if parsed.username or parsed.password:
        raise ValueError("CORS_ORIGINS cannot contain userinfo")
    if parsed.path not in {"", "/"} or parsed.params or parsed.query or parsed.fragment:
        raise ValueError("CORS_ORIGINS must not contain paths or query strings")
    return origin.rstrip("/")


def _validate_origin(origin: str) -> None:
    _normalize_origin(origin)


def _parse_bool(name: str, value: str, default: bool) -> bool:
    text = value.strip().lower()
    if not text:
        return default
    if text in _TRUE:
        return True
    if text in _FALSE:
        return False
    raise ValueError(f"{name} must be a boolean")


def _parse_base_url(value: str) -> str | None:
    text = value.strip()
    if not text:
        return None
    parsed = urlparse(text)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("PUBLIC_BASE_URL must be an exact http(s) base URL")
    if parsed.username or parsed.password:
        raise ValueError("PUBLIC_BASE_URL cannot contain userinfo")
    if parsed.query or parsed.fragment:
        raise ValueError("PUBLIC_BASE_URL must not contain a query string or fragment")
    return text.rstrip("/")


def _parse_sandbox_repo(value: str) -> str | None:
    text = value.strip()
    if not text:
        return None
    try:
        return validate_repo(text)
    except GitHubWriteError as exc:
        raise ValueError(f"SANDBOX_REPO must be a strict owner/name: {exc}") from exc


def _parse_optional(value: str) -> str | None:
    """Return a stripped value, or ``None`` when it is blank."""
    text = value.strip()
    return text or None


def load_settings() -> Settings:
    tenants = _split_csv(os.getenv("TENANTS", "falkordb")) or ("falkordb",)
    raw_origins = _split_csv(os.getenv("CORS_ORIGINS", "http://localhost:3001"))
    api_mode = os.getenv("API_MODE", "fixtures").strip() or "fixtures"
    # Blank means every configured tenant: they are all public orgs (spec: hosted MCP).
    public_mcp_tenants = _split_csv(os.getenv("PUBLIC_MCP_TENANTS", "")) or tenants

    if api_mode not in API_MODES:
        raise ValueError(f"API_MODE must be one of {', '.join(API_MODES)}")

    unknown_public = [slug for slug in public_mcp_tenants if slug not in tenants]
    if unknown_public:
        raise ValueError(
            f"PUBLIC_MCP_TENANTS must be a subset of TENANTS: {', '.join(unknown_public)}"
        )

    seen: set[str] = set()
    cors_origins: list[str] = []
    for origin in raw_origins:
        normalized = _normalize_origin(origin)
        if normalized not in seen:
            seen.add(normalized)
            cors_origins.append(normalized)

    return Settings(
        tenants=tenants,
        cors_origins=tuple(cors_origins),
        api_mode=api_mode,
        sandbox_repo=_parse_sandbox_repo(os.getenv("SANDBOX_REPO", "")),
        dry_run=_parse_bool("DRY_RUN", os.getenv("DRY_RUN", ""), default=True),
        public_base_url=_parse_base_url(os.getenv("PUBLIC_BASE_URL", "")),
        webhook_secret=_parse_optional(os.getenv("GITHUB_WEBHOOK_SECRET", "")),
        bot_login=_parse_optional(os.getenv("KEYSTONE_BOT_LOGIN", "")),
        public_mcp_tenants=public_mcp_tenants,
    )

