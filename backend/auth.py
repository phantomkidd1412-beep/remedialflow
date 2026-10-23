"""JWT access tokens (short-lived) + rotating refresh tokens with server-side sessions."""
import os
import hashlib
import logging
import secrets
from datetime import datetime, timedelta, timezone
from html import escape
from typing import Optional

import jwt
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pwdlib import PasswordHash
from pydantic import BaseModel, EmailStr, Field

from core import db, User, SECRET, now_iso, oid, audit
from emailer import send_email
from security import DEMO_EMAILS, PRODUCTION, origin_allowed, rate_limit

log = logging.getLogger("auth")
NO_SEND_DOMAINS = {"example.com", "example.org", "example.net", "test.com", "test.local"}

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


def cookie_session(req: Request) -> Optional[str]:
    """CSRF guard for the only cookie-authenticated endpoints (web refresh/logout).
    Requires the custom X-Client: web header (forces a CORS preflight cross-site) AND, when the browser sends one,
    an Origin on the allowlist. SameSite=Strict on the cookie is the third layer. Native apps send the refresh
    token in the body and never use the cookie, so they are unaffected. All other APIs use Bearer tokens."""
    raw = req.cookies.get(COOKIE)
    if not raw:
        return None
    if req.headers.get("x-client") != "web" or not origin_allowed(req.headers.get("origin")):
        raise HTTPException(403, "Request blocked (CSRF protection)")
    return raw


def token_payload(resp: Response, req: Request, u: User, refresh: str) -> dict:
    out = {"access_token": access_token(u), "user": u.public(), "expires_in": ACCESS_MIN * 60}
    if req.headers.get("x-client") == "web":
        resp.set_cookie(COOKIE, refresh, httponly=True, secure=True, samesite="strict",
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
    token: str = Field(min_length=8, max_length=256)
    password: str = Field(min_length=8, max_length=128)


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
    await rate_limit(req, "login-fail-ip", "", 30, 900, check_only=True)  # 30 failed logins per IP / 15 min
    if await _attempts(email) >= 8:
        raise HTTPException(429, "Too many attempts. Try again in 15 minutes.")
    u = User.from_mongo(await db.users.find_one({"email": email}))
    ok = ph.verify(body.password, u.password_hash) if u and u.password_hash else ph.verify(body.password, DUMMY) and False
    if PRODUCTION and email in DEMO_EMAILS:
        ok = False  # demo accounts never work in production
    if not u or not ok:
        await db.login_attempts.insert_one({"email": email, "at": now_iso()})
        await rate_limit(req, "login-fail-ip", "", 10**9, 900)  # count the failure
        raise HTTPException(401, "Incorrect email or password")
    if not u.active:
        raise HTTPException(403, "Account deactivated")
    await db.login_attempts.delete_many({"email": email})
    refresh = await new_session(u)
    await audit("auth.login", u.id, f"user:{u.id}")
    return token_payload(resp, req, u, refresh)


@router.post("/refresh")
async def refresh(body: RefreshIn, req: Request, resp: Response):
    raw = body.refresh_token or cookie_session(req)
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
    raw = body.refresh_token or cookie_session(req)
    if raw:
        await db.sessions.update_one({"refresh_hash": h(raw)}, {"$set": {"revoked": True}})
    resp.delete_cookie(COOKIE, path="/api/auth")
    return {"ok": True}


@router.get("/me")
async def me(u: User = Depends(current_user)):
    return u.public()


async def create_reset_token(user_id: str, hours: int = 24) -> str:
    # 16 chars from a 31-symbol unambiguous alphabet (~79 bits); uppercase-only so codes stay easy to type.
    raw = "".join(secrets.choice("ABCDEFGHJKLMNPQRSTUVWXYZ23456789") for _ in range(16))
    await db.reset_tokens.insert_one({"user_id": user_id, "token_hash": h(raw), "used": False,
                                      "expires_at": (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat()})
    return raw


@router.post("/forgot")
async def forgot(body: ForgotIn, req: Request, bg: BackgroundTasks):
    """Always the same answer (no account enumeration). Emails a single-use link valid RESET_LINK_MINUTES."""
    email = body.email.lower()
    await rate_limit(req, "forgot", email, 3, 900)
    await rate_limit(req, "forgot-ip", "", 20, 3600)
    u = User.from_mongo(await db.users.find_one({"email": email}))
    out = {"message": "If an account exists for that email, we've sent a password reset link. It expires in 30 minutes."}
    base = os.environ.get("APP_PUBLIC_URL", "").rstrip("/")
    if u and u.active and not (PRODUCTION and email in DEMO_EMAILS):
        if email.endswith("@remedialflow.dev") or email.split("@")[-1] in NO_SEND_DOMAINS:
            return out  # demo/test addresses: never send real email (no cost, no bounces)
        if not base.startswith("https://"):
            log.warning("APP_PUBLIC_URL not configured: reset email not sent")
            return out
        raw = secrets.token_urlsafe(32)
        mins = int(os.environ.get("RESET_LINK_MINUTES", "30"))
        await db.reset_tokens.insert_one({"user_id": u.id, "token_hash": h(raw), "used": False, "kind": "link",
                                          "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=mins)).isoformat()})
        await audit("auth.reset_requested", u.id, f"user:{u.id}")
        bg.add_task(_send_reset, u.email, u.name, f"{base}/reset?token={raw}", mins)
    return out


async def _send_reset(to: str, name: str, link: str, mins: int):
    brand = escape(os.environ.get("EMAIL_FROM_NAME", "RemedialFlow"))
    html = (f'<table role="presentation" width="100%"><tr><td style="padding:24px;font-family:Arial,sans-serif;color:#111">'
            f'<p>Hi {escape(name)},</p><p>We received a request to reset your {brand} password.</p>'
            f'<p><a href="{escape(link)}" style="display:inline-block;background:#FF6B00;color:#000;padding:12px 20px;'
            f'text-decoration:none;font-weight:bold">Reset password</a></p>'
            f'<p>This link works once and expires in {mins} minutes. If you did not ask for this, ignore this email; your password stays the same.</p>'
            f'<p style="font-size:12px;color:#888">Sent by {brand}. We never ask for your password by email.</p></td></tr></table>')
    try:
        await send_email(to=to, subject=f"Reset your {brand} password", html=html)
    except Exception:
        log.warning("reset email not delivered")  # no recipient/link in logs


@router.post("/reset")
async def reset(body: ResetIn, req: Request):
    await rate_limit(req, "reset", "", 10, 900)
    raw = body.token.strip()
    now = now_iso()
    # single use: claim atomically before changing the password (emailed link token, or admin-issued setup code)
    t = None
    for cand in {raw, raw.upper()}:
        t = await db.reset_tokens.find_one_and_update({"token_hash": h(cand), "used": False, "expires_at": {"$gt": now}},
                                                      {"$set": {"used": True, "used_at": now}})
        if t:
            break
    if not t:
        raise HTTPException(400, "This reset link or code is invalid, expired or already used")
    await db.users.update_one({"_id": oid(t["user_id"])},
                              {"$set": {"password_hash": ph.hash(body.password)}, "$inc": {"token_version": 1}})
    await db.reset_tokens.update_many({"user_id": t["user_id"], "used": False}, {"$set": {"used": True, "superseded": True}})
    await revoke_all(t["user_id"])
    await audit("auth.password_reset", t["user_id"], f"user:{t['user_id']}")
    return {"ok": True}


DELETED_NAME = "Deleted user"


async def anonymise_user(uid: str, actor_id: str, mode: str) -> None:
    """Account deletion (Apple 5.1.1(v)): personal details removed and the account closed for good.
    The user _id is kept so evidence, reviews and audit history stay linked ("Deleted user"). Dropbox untouched."""
    old = await db.users.find_one_and_update(
        {"_id": oid(uid), "deleted_account": {"$ne": True}},
        {"$set": {"deleted_account": True, "active": False, "name": DELETED_NAME, "email": f"deleted-{uid}@deleted.invalid",
                  "password_hash": "", "phone": None, "push_active": False, "default_rate": None, "deleted_at": now_iso()},
         "$inc": {"token_version": 1}})  # live access tokens die immediately
    if not old:
        raise HTTPException(404, "User not found")
    await revoke_all(uid)
    await db.sessions.delete_many({"user_id": uid})
    await db.reset_tokens.delete_many({"user_id": uid})
    await db.login_attempts.delete_many({"email": old.get("email")})
    await audit("user.account_deleted", actor_id, f"user:{uid}", {"mode": mode, "role": old.get("role")})  # no name/email


async def last_admin(uid: str) -> bool:
    return await db.users.count_documents({"role": "admin", "active": True, "_id": {"$ne": oid(uid)}}) == 0


class DeleteAccountIn(BaseModel):
    password: str = Field(min_length=1, max_length=256)


@router.post("/delete-account")
async def delete_account(body: DeleteAccountIn, req: Request, resp: Response, u: User = Depends(current_user)):
    await rate_limit(req, "delete-account", u.id, 10, 900)
    if not u.password_hash or not ph.verify(body.password, u.password_hash):
        raise HTTPException(400, "Password is incorrect")
    if u.role == "admin" and await last_admin(u.id):
        raise HTTPException(400, "You are the only active admin. Make another user an admin before deleting your account.")
    await anonymise_user(u.id, u.id, "self")
    resp.delete_cookie(COOKIE, path="/api/auth")
    return {"ok": True}


@router.post("/change-password")
async def change_password(body: ChangeIn, req: Request, resp: Response, u: User = Depends(current_user)):
    await rate_limit(req, "change-pw", u.id, 10, 900)
    if not ph.verify(body.current_password, u.password_hash):
        raise HTTPException(400, "Current password is incorrect")
    # evict every existing session (incl. a stolen one), then keep THIS device signed in with a fresh session
    await db.users.update_one({"_id": oid(u.id)}, {"$set": {"password_hash": ph.hash(body.new_password)}, "$inc": {"token_version": 1}})
    await revoke_all(u.id)
    await audit("auth.password_changed", u.id, f"user:{u.id}")
    fresh = User.from_mongo(await db.users.find_one({"_id": oid(u.id)}))
    return {"ok": True, **token_payload(resp, req, fresh, await new_session(fresh))}
