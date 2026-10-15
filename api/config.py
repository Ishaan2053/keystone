from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import urlparse


API_MODES = ("fixtures", "live")


@dataclass(frozen=True)
class Settings:
    tenants: tuple[str, ...] = ("falkordb",)
    cors_origins: tuple[str, ...] = ("http://localhost:3001",)
    api_mode: str = "fixtures"


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


def load_settings() -> Settings:
    tenants = _split_csv(os.getenv("TENANTS", "falkordb")) or ("falkordb",)
    raw_origins = _split_csv(os.getenv("CORS_ORIGINS", "http://localhost:3001"))
    api_mode = os.getenv("API_MODE", "fixtures").strip() or "fixtures"

    if api_mode not in API_MODES:
        raise ValueError(f"API_MODE must be one of {', '.join(API_MODES)}")

    seen: set[str] = set()
    cors_origins: list[str] = []
    for origin in raw_origins:
        normalized = _normalize_origin(origin)
        if normalized not in seen:
            seen.add(normalized)
            cors_origins.append(normalized)

    return Settings(tenants=tenants, cors_origins=tuple(cors_origins), api_mode=api_mode)

