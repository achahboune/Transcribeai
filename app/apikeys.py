"""
API keys for paying (Unlimited) users.

- A key looks like  tai_<random>  and is shown ONCE at creation.
- Only its SHA-256 hash is stored in Supabase (table public.api_keys).
- Keys are rate limited (per key, in memory) and tied to the owner's plan:
  if the owner is no longer on Unlimited, the key stops working.
"""
import asyncio
import hashlib
import logging
import secrets
import time
import uuid
from collections import deque
from datetime import datetime, timezone

import httpx
from fastapi import HTTPException

from .config import settings

log = logging.getLogger("transcribeai.apikeys")

KEY_PREFIX = "tai_"
MAX_KEYS_PER_USER = 5
RATE_LIMIT_PER_MIN = 60
TOUCH_EVERY_SECONDS = 300

_rate: dict[str, deque] = {}
_last_touch: dict[str, float] = {}


def hash_key(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def generate_key() -> str:
    return KEY_PREFIX + secrets.token_urlsafe(30)


def is_api_key(token: str) -> bool:
    return token.startswith(KEY_PREFIX)


def _headers(extra: dict | None = None) -> dict:
    h = {
        "apikey": settings.supabase_service_key,
        "Authorization": f"Bearer {settings.supabase_service_key}",
        "Content-Type": "application/json",
    }
    if extra:
        h.update(extra)
    return h


async def _request(method: str, params: dict, **kwargs) -> httpx.Response:
    async with httpx.AsyncClient(timeout=10) as client:
        return await client.request(
            method,
            f"{settings.supabase_url}/rest/v1/api_keys",
            params=params,
            headers=_headers(kwargs.pop("prefer", None)),
            **kwargs,
        )


def _public(row: dict) -> dict:
    return {
        "id": row["id"],
        "name": row.get("name"),
        "prefix": row.get("key_prefix"),
        "created_at": row.get("created_at"),
        "last_used_at": row.get("last_used_at"),
    }


async def list_keys(user_id: str) -> list[dict]:
    resp = await _request(
        "GET",
        {
            "user_id": f"eq.{user_id}",
            "revoked_at": "is.null",
            "select": "id,name,key_prefix,created_at,last_used_at",
            "order": "created_at.desc",
        },
    )
    if resp.status_code != 200:
        log.error("list_keys failed: %s %s", resp.status_code, resp.text[:200])
        raise HTTPException(status_code=500, detail="Could not load API keys.")
    return [_public(r) for r in resp.json()]


async def create_key(user_id: str, name: str) -> dict:
    existing = await list_keys(user_id)
    if len(existing) >= MAX_KEYS_PER_USER:
        raise HTTPException(
            status_code=400,
            detail=f"You can have up to {MAX_KEYS_PER_USER} active API keys. Revoke one first.",
        )
    key = generate_key()
    row = {
        "user_id": user_id,
        "name": name,
        "key_hash": hash_key(key),
        "key_prefix": key[:10],
    }
    resp = await _request("POST", {}, json=row, prefer={"Prefer": "return=representation"})
    if resp.status_code not in (200, 201):
        log.error("create_key failed: %s %s", resp.status_code, resp.text[:200])
        raise HTTPException(status_code=500, detail="Could not create the API key.")
    created = resp.json()[0]
    out = _public(created)
    out["key"] = key  # shown once, never stored in clear
    return out


async def revoke_key(user_id: str, key_id: str) -> None:
    try:
        uuid.UUID(key_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid key id.")
    resp = await _request(
        "PATCH",
        {"id": f"eq.{key_id}", "user_id": f"eq.{user_id}", "revoked_at": "is.null"},
        json={"revoked_at": datetime.now(timezone.utc).isoformat()},
        prefer={"Prefer": "return=representation"},
    )
    if resp.status_code != 200:
        log.error("revoke_key failed: %s %s", resp.status_code, resp.text[:200])
        raise HTTPException(status_code=500, detail="Could not revoke the API key.")
    if not resp.json():
        raise HTTPException(status_code=404, detail="API key not found.")


async def find_key(key: str) -> dict | None:
    resp = await _request(
        "GET",
        {
            "key_hash": f"eq.{hash_key(key)}",
            "revoked_at": "is.null",
            "select": "id,user_id,name",
        },
    )
    if resp.status_code != 200:
        log.error("find_key failed: %s %s", resp.status_code, resp.text[:200])
        raise HTTPException(status_code=503, detail="API temporarily unavailable.")
    rows = resp.json()
    return rows[0] if rows else None


def check_rate(key_id: str) -> None:
    now = time.monotonic()
    q = _rate.setdefault(key_id, deque())
    while q and now - q[0] > 60:
        q.popleft()
    if len(q) >= RATE_LIMIT_PER_MIN:
        retry = max(1, int(60 - (now - q[0])) + 1)
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit exceeded: {RATE_LIMIT_PER_MIN} requests per minute per API key.",
            headers={"Retry-After": str(retry)},
        )
    q.append(now)


async def _touch(key_id: str) -> None:
    try:
        await _request(
            "PATCH",
            {"id": f"eq.{key_id}"},
            json={"last_used_at": datetime.now(timezone.utc).isoformat()},
            prefer={"Prefer": "return=minimal"},
        )
    except Exception:  # never break a request over a timestamp
        pass


def touch_soon(key_id: str) -> None:
    now = time.monotonic()
    if now - _last_touch.get(key_id, 0) < TOUCH_EVERY_SECONDS:
        return
    _last_touch[key_id] = now
    try:
        asyncio.get_running_loop().create_task(_touch(key_id))
    except RuntimeError:
        pass
