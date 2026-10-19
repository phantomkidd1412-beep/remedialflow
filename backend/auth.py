"""JWT access tokens (short-lived) + rotating refresh tokens with server-side sessions."""
import os
import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

import jwt
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pwdlib import PasswordHash
from pydantic import BaseModel, EmailStr, Field

from core import db, User, SECRET, now_iso, oid, audit

ph = PasswordHash.recommended()
ACCESS_MIN = int(os.environ.get("ACCESS_TOKEN_MINUTES", "15"))
REFRESH_DAYS = int(os.environ.get("REFRESH_TOKEN_DAYS", "30"))
COOKIE = "rf_refresh"
bearer = HTTPBearer(auto_error=False)
router = APIRouter(prefix="/api/auth")
DUMMY = ph.hash("dummy-password-for-timing")


def h(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def access_token(u: User) -> str:
    n = datetime.now(timezone.utc)
    return jwt.encode({"sub": u.id, "role": u.role, "tv": u.token_version, "iat": n,
                       "exp": n + timedelta(minutes=ACCESS_MIN), "typ": "access"}, SECRET, algorithm="HS256")


async def new_session(u: User, family: Optional[str] = None) -> str:
    raw = secrets.token_urlsafe(48)
    await db.sessions.insert_one({"user_id": u.id, "refresh_hash": h(raw), "family": family or secrets.token_hex(8),
                                  "revoked": False, "created_at": now_iso(),
                                  "expires_at": (datetime.now(timezone.utc) + timedelta(days=REFRESH_DAYS)).isoformat()})
    return raw


async def revoke_all(user_id: str):
    await db.sessions.update_many({"user_id": user_id}, {"$set": {"revoked": True}})


async def current_user(cred: HTTPAuthorizationCredentials = Depends(bearer)) -> User:
    err = HTTPException(401, "Invalid or expired token")
    if not cred:
        raise err
    try:
        c = jwt.decode(cred.credentials, SECRET, algorithms=["HS256"])
        if c.get("typ") != "access":
            raise err
    except jwt.PyJWTError:
        raise err
    u = User.from_mongo(await db.users.find_one({"_id": oid(c["sub"])}))
    # DB check on every request => deactivation / revocation takes effect immediately.
    if not u or not u.active or u.token_version != c.get("tv"):
        raise err
    return u


def require(*roles):
    async def dep(u: User = Depends(current_user)) -> User:
        if u.role not in roles:
            raise HTTPException(403, "Insufficient role")
        return u
    return dep


def token_payload(resp: Response, req: Request, u: User, refresh: str) -> dict:
    out = {"access_token": access_token(u), "user": u.public(), "expires_in": ACCESS_MIN * 60}
    if req.headers.get("x-client") == "web":
        resp.set_cookie(COOKIE, refresh, httponly=True, secure=True, samesite="lax",
                        max_age=REFRESH_DAYS * 86400, path="/api/auth")
    else:
        out["refresh_token"] = refresh
    return out


class LoginIn(BaseModel):
    email: EmailStr
    password: str


class RefreshIn(BaseModel):
    refresh_token: Optional[str] = None


class ResetIn(BaseModel):
    token: str
    password: str = Field(min_length=8)


class ForgotIn(BaseModel):
    email: EmailStr


class ChangeIn(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8)


async def _attempts(email: str) -> int:
    since = (datetime.now(timezone.utc) - timedelta(minutes=15)).isoformat()
    return await db.login_attempts.count_documents({"email": email, "at": {"$gte": since}})


@router.post("/login")
async def login(body: LoginIn, req: Request, resp: Response):
    email = body.email.lower()
    if await _attempts(email) >= 8:
        raise HTTPException(429, "Too many attempts. Try again in 15 minutes.")
    u = User.from_mongo(await db.users.find_one({"email": email}))
    ok = ph.verify(body.password, u.password_hash) if u and u.password_hash else ph.verify(body.password, DUMMY) and False
    if not u or not ok:
        await db.login_attempts.insert_one({"email": email, "at": now_iso()})
        raise HTTPException(401, "Incorrect email or password")
    if not u.active:
        raise HTTPException(403, "Account deactivated")
    await db.login_attempts.delete_many({"email": email})
    refresh = await new_session(u)
    await audit("auth.login", u.id, f"user:{u.id}")
    return token_payload(resp, req, u, refresh)


@router.post("/refresh")
async def refresh(body: RefreshIn, req: Request, resp: Response):
    raw = body.refresh_token or req.cookies.get(COOKIE)
    if not raw:
        raise HTTPException(401, "No session")
    s = await db.sessions.find_one({"refresh_hash": h(raw)})
    if not s:
        raise HTTPException(401, "Invalid session")
    if s["revoked"]:
        # Reuse of a rotated token => possible theft: kill the whole family.
        await db.sessions.update_many({"family": s["family"]}, {"$set": {"revoked": True}})
        raise HTTPException(401, "Session revoked")
    if s["expires_at"] < now_iso():
        raise HTTPException(401, "Session expired")
    u = User.from_mongo(await db.users.find_one({"_id": oid(s["user_id"])}))
    if not u or not u.active:
        raise HTTPException(401, "Account deactivated")
    await db.sessions.update_one({"_id": s["_id"]}, {"$set": {"revoked": True, "rotated_at": now_iso()}})
    new_raw = await new_session(u, s["family"])
    return token_payload(resp, req, u, new_raw)


@router.post("/logout")
async def logout(body: RefreshIn, req: Request, resp: Response):
    raw = body.refresh_token or req.cookies.get(COOKIE)
    if raw:
        await db.sessions.update_one({"refresh_hash": h(raw)}, {"$set": {"revoked": True}})
    resp.delete_cookie(COOKIE, path="/api/auth")
    return {"ok": True}


@router.get("/me")
async def me(u: User = Depends(current_user)):
    return u.public()


async def create_reset_token(user_id: str, hours: int = 24) -> str:
    raw = secrets.token_urlsafe(9).replace("-", "x").replace("_", "y")[:12].upper()
    await db.reset_tokens.insert_one({"user_id": user_id, "token_hash": h(raw), "used": False,
                                      "expires_at": (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat()})
    return raw


@router.post("/forgot")
async def forgot(body: ForgotIn):
    u = User.from_mongo(await db.users.find_one({"email": body.email.lower()}))
    out = {"message": "If the account exists, a reset code has been issued. Contact your administrator if you do not receive it."}
    if u and u.active:
        code = await create_reset_token(u.id, hours=1)
        await audit("auth.reset_requested", u.id, f"user:{u.id}")
        # No email provider configured yet: expose the code only in demo/dev mode.
        if os.environ.get("DEMO_MODE") == "true":
            out["dev_reset_code"] = code
    return out


@router.post("/reset")
async def reset(body: ResetIn):
    t = await db.reset_tokens.find_one({"token_hash": h(body.token.strip().upper()), "used": False})
    if not t or t["expires_at"] < now_iso():
        raise HTTPException(400, "Invalid or expired reset code")
    await db.reset_tokens.update_one({"_id": t["_id"]}, {"$set": {"used": True}})
    await db.users.update_one({"_id": oid(t["user_id"])},
                              {"$set": {"password_hash": ph.hash(body.password)}, "$inc": {"token_version": 1}})
    await revoke_all(t["user_id"])
    await audit("auth.password_reset", t["user_id"], f"user:{t['user_id']}")
    return {"ok": True}


@router.post("/change-password")
async def change_password(body: ChangeIn, u: User = Depends(current_user)):
    if not ph.verify(body.current_password, u.password_hash):
        raise HTTPException(400, "Current password is incorrect")
    await db.users.update_one({"_id": oid(u.id)}, {"$set": {"password_hash": ph.hash(body.new_password)}})
    await audit("auth.password_changed", u.id, f"user:{u.id}")
    return {"ok": True}
