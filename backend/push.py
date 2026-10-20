"""Emergent managed push (SuprSend relay). Push text is deliberately generic: no addresses, refs or customer data
reach lock screens. In-app notifications (db.notifications) remain the source of truth."""
import asyncio
import logging
import os
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from auth import current_user
from core import User, db, oid

log = logging.getLogger("push")
PUSH_BASE_URL = "https://integrations.emergentagent.com"
router = APIRouter(prefix="/api")

_ASSIGNED = ("New work assigned", "You have new work in RemedialFlow.")
_ACTION = ("Action required", "A job needs your attention in RemedialFlow.")
_REVIEW = ("Review needed", "Evidence is waiting for review in RemedialFlow.")
PUSH_TEXT = {"project_assigned": _ASSIGNED, "assigned": _ASSIGNED, "more_evidence": _ACTION,
             "rejected_internal": _ACTION, "rejected_trustmark": _ACTION, "escalated": _REVIEW, "review_needed": _REVIEW}


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(base_url=PUSH_BASE_URL, headers={"X-Push-Key": os.environ.get("EMERGENT_PUSH_KEY", "placeholder")}, timeout=10.0)


class RegisterPushBody(BaseModel):
    platform: str = Field(pattern="^(android|ios)$")
    device_token: str = Field(min_length=10, max_length=4096)


@router.post("/register-push", status_code=201)
async def register_push(body: RegisterPushBody, u: User = Depends(current_user)):
    """The device is registered for the AUTHENTICATED user only (user_id is never taken from the client)."""
    async with _client() as c:
        resp = await c.post("/api/v1/push/users/register", json={"user_id": u.id, "platform": body.platform, "device_token": body.device_token})
    if resp.status_code == 401:
        raise HTTPException(500, "Push is not configured")
    if resp.status_code >= 500:
        raise HTTPException(502, "Push provider unavailable")
    resp.raise_for_status()
    await db.users.update_one({"_id": oid(u.id)}, {"$set": {"push_active": True}})
    return {"status": "registered"}


@router.post("/unregister-push")
async def unregister_push(u: User = Depends(current_user)):
    """Called on logout: this user gets no more pushes until they sign in again on a device."""
    await db.users.update_one({"_id": oid(u.id)}, {"$set": {"push_active": False}})
    return {"ok": True}


async def send_push(recipients: list, data: dict, idempotency_key: Optional[str] = None, _attempt: int = 0) -> None:
    if not recipients:
        return
    payload: dict = {"recipients": recipients[:100], "data": data}
    if idempotency_key:
        payload["$idempotency_key"] = idempotency_key
    async with _client() as c:
        resp = await c.post("/api/v1/push/trigger", json=payload)
    code = resp.json().get("code", "") if resp.headers.get("content-type", "").startswith("application/json") else ""
    if code == "idempotency_key_in_progress" and idempotency_key and _attempt < 3:
        await asyncio.sleep(3)
        return await send_push(recipients, data, idempotency_key, _attempt + 1)
    if code in ("insufficient_credits", "integration_disabled", "daily_limit_reached"):
        log.warning("Push paused: %s (%s)", code, resp.status_code)
        return
    resp.raise_for_status()


async def push_for(user_id: str, kind: str, key: str):
    """Fire-and-forget; only active users who are signed in on a device; never blocks the caller."""
    text = PUSH_TEXT.get(kind)
    if not text:
        return
    try:
        if not await db.users.find_one({"_id": oid(user_id), "active": True, "push_active": True}, {"_id": 1}):
            return
        await send_push([user_id], {"title": text[0], "message": text[1], "action_url": "/notifications"}, idempotency_key=key)
    except Exception as e:
        log.warning("Push failed (non-blocking): %s", type(e).__name__)
