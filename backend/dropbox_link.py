"""Admin-only Dropbox connection (OAuth 2 authorization code + PKCE, offline refresh token) and chunked uploads.

The refresh token is obtained server-side, encrypted at rest and never returned to any client. Clients never
receive Dropbox links: all file access stays behind the existing signed, role-checked /api/files endpoints.
"""
import base64
import hashlib
import os
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode

import requests
from cryptography.fernet import Fernet, InvalidToken
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import Headers, UploadFile

from core import db, now_iso, audit, User
from auth import current_user
from storage import storage

router = APIRouter(prefix="/api")


def _admin(u: User):
    if u.role != "admin":
        raise HTTPException(403, "Admins only")
CHUNK_MAX = 10 * 1024 * 1024  # each request stays far below the ~100 MB ingress limit
UPLOAD_DIR = Path(os.environ.get("UPLOAD_TMP_DIR", "/tmp/rf_uploads"))


def _fernet() -> Fernet:
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(("dropbox-token-v1:" + os.environ["JWT_SECRET"]).encode()).digest()))


async def load_dropbox():
    d = await db.secure_settings.find_one({"_id": "dropbox"})
    if d and d.get("refresh_enc"):
        try:
            storage.set_refresh(_fernet().decrypt(d["refresh_enc"].encode()).decode())
        except InvalidToken:
            storage.set_refresh(None)


@router.get("/admin/dropbox/status")
async def dropbox_status(u: User = Depends(current_user)):
    _admin(u)
    d = await db.secure_settings.find_one({"_id": "dropbox"}, {"refresh_enc": 0})
    return {"connected": storage.dropbox_ready, "configured": bool(os.environ.get("DROPBOX_APP_KEY")),
            "connected_at": (d or {}).get("connected_at"), "connected_by": (d or {}).get("connected_by_name"),
            "redirect_uri": os.environ.get("DROPBOX_REDIRECT_URI")}


@router.post("/admin/dropbox/connect")
async def dropbox_connect(u: User = Depends(current_user)):
    _admin(u)
    if not os.environ.get("DROPBOX_APP_KEY") or not os.environ.get("DROPBOX_REDIRECT_URI"):
        raise HTTPException(503, "Dropbox app is not configured on the server")
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(32)
    await db.dropbox_states.insert_one({"_id": state, "verifier": verifier, "user_id": u.id, "user_name": u.name,
                                        "expires": datetime.now(timezone.utc) + timedelta(minutes=10)})
    q = urlencode({"client_id": os.environ["DROPBOX_APP_KEY"], "response_type": "code", "token_access_type": "offline",
                   "code_challenge": challenge, "code_challenge_method": "S256", "state": state,
                   "redirect_uri": os.environ["DROPBOX_REDIRECT_URI"],
                   "scope": "files.metadata.read files.content.read files.content.write", "force_reapprove": "true"})
    return {"authorize_url": "https://www.dropbox.com/oauth2/authorize?" + q}


def _page(msg: str, code: int = 200) -> HTMLResponse:
    return HTMLResponse(f"<!doctype html><meta name=viewport content='width=device-width'><body style='font-family:sans-serif;padding:32px'>"
                        f"<h2>RemedialFlow</h2><p>{msg}</p><p>You can close this window and return to the app.</p></body>", code)


@router.get("/admin/dropbox/callback")
async def dropbox_callback(state: str = "", code: str = "", error: str = ""):
    row = await db.dropbox_states.find_one_and_delete({"_id": state}) if state else None
    if not row or row["expires"].replace(tzinfo=timezone.utc) < datetime.now(timezone.utc):
        return _page("This connection link has expired. Start again from Admin → Settings.", 400)
    if error or not code:
        return _page("Dropbox was not connected (permission was not granted).", 400)
    r = await run_in_threadpool(requests.post, "https://api.dropboxapi.com/oauth2/token", data={
        "code": code, "grant_type": "authorization_code", "client_id": os.environ["DROPBOX_APP_KEY"],
        "redirect_uri": os.environ["DROPBOX_REDIRECT_URI"], "code_verifier": row["verifier"]}, timeout=30)
    tok = r.json() if r.ok else {}
    if not tok.get("refresh_token"):
        return _page("Dropbox did not return a long-lived token. Please try again.", 400)
    await db.secure_settings.replace_one({"_id": "dropbox"}, {
        "_id": "dropbox", "refresh_enc": _fernet().encrypt(tok["refresh_token"].encode()).decode(),
        "account_id": tok.get("account_id"), "connected_at": now_iso(), "connected_by": row["user_id"],
        "connected_by_name": row["user_name"]}, upsert=True)
    storage.set_refresh(tok["refresh_token"])
    await audit("storage.dropbox_connected", row["user_id"], "storage:dropbox", {"account_id": tok.get("account_id")})
    return _page("Dropbox is connected. New evidence will now be stored in your Dropbox app folder.")


# ---------------- chunked uploads (large videos) ----------------
class InitIn(BaseModel):
    nc_id: str
    size: int = Field(gt=0)
    mime: str = Field(max_length=100)
    client_id: str = Field(min_length=4, max_length=64)


@router.post("/uploads")
async def upload_init(body: InitIn, u: User = Depends(current_user)):
    from server import get_nc, MAX_VIDEO_MB, MAX_UPLOAD
    await get_nc(body.nc_id, u)  # same NC-level authorisation as direct uploads
    if body.size > max(MAX_VIDEO_MB * 1024 * 1024, MAX_UPLOAD):
        raise HTTPException(413, f"File too large (maximum {MAX_VIDEO_MB} MB per video)")
    s = await db.upload_sessions.find_one({"client_id": body.client_id, "user_id": u.id, "nc_id": body.nc_id})
    if not s:
        r = await db.upload_sessions.insert_one({"client_id": body.client_id, "user_id": u.id, "nc_id": body.nc_id,
                                                 "size": body.size, "mime": body.mime, "created_at": now_iso()})
        s = {"_id": r.inserted_id}
    p = UPLOAD_DIR / str(s["_id"])
    return {"upload_id": str(s["_id"]), "offset": p.stat().st_size if p.exists() else 0}


async def _session(uid: str, u: User) -> dict:
    from bson import ObjectId
    try:
        s = await db.upload_sessions.find_one({"_id": ObjectId(uid), "user_id": u.id})
    except Exception:
        s = None
    if not s:
        raise HTTPException(404, "Upload session not found")
    return s


@router.put("/uploads/{uid}/chunk")
async def upload_chunk(uid: str, offset: int, request: Request, u: User = Depends(current_user)):
    s = await _session(uid, u)
    data = await request.body()
    if not data or len(data) > CHUNK_MAX:
        raise HTTPException(413, "Chunk must be 1 byte to 10 MB")
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    p = UPLOAD_DIR / uid
    cur = p.stat().st_size if p.exists() else 0
    if offset + len(data) <= cur:  # retry of a chunk we already have: idempotent
        return {"offset": cur}
    if offset != cur:
        raise HTTPException(409, f"offset:{cur}")
    if cur + len(data) > s["size"]:
        raise HTTPException(400, "More data than declared")
    with open(p, "ab") as fh:
        fh.write(data)
    return {"offset": cur + len(data)}


class CompleteIn(BaseModel):
    meta: str = "{}"


@router.post("/uploads/{uid}/complete")
async def upload_complete(uid: str, body: CompleteIn, bg: BackgroundTasks, u: User = Depends(current_user)):
    from server import upload_evidence
    s = await _session(uid, u)
    p = UPLOAD_DIR / uid
    if not p.exists() or p.stat().st_size != s["size"]:
        raise HTTPException(422, "Upload incomplete - will retry")
    with open(p, "rb") as fh:
        f = UploadFile(file=fh, filename="upload", headers=Headers({"content-type": s["mime"]}))
        out = await upload_evidence(s["nc_id"], bg, file=f, meta=body.meta, u=u)
    p.unlink(missing_ok=True)  # local temp copy only; the stored evidence is never deleted
    await db.upload_sessions.update_one({"_id": s["_id"]}, {"$set": {"completed_at": now_iso(), "file_id": out.get("id")}})
    return out
