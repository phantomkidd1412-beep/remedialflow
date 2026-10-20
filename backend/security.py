"""Production hardening helpers: shared rate limits, security headers, secret-redacting logs, config checks."""
import hashlib
import logging
import os
import re
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, Request
from pymongo import ReturnDocument

from core import db

PRODUCTION = os.environ.get("APP_ENV", "development") == "production"
DEMO_EMAILS = {"admin@remedialflow.dev", "employee@remedialflow.dev", "installer@remedialflow.dev", "installer2@remedialflow.dev"}


def client_ip(req: Request) -> str:
    return (req.headers.get("x-forwarded-for") or (req.client.host if req.client else "")).split(",")[0].strip()


async def rate_limit(req: Request, bucket: str, ident: str, limit: int, seconds: int, check_only: bool = False):
    """Atomic fixed-window limiter stored in Mongo (shared by all workers). ident is hashed, never stored raw.
    check_only=True: reject if the window is already over the limit, without counting this request."""
    key = f"{bucket}:" + hashlib.sha256(f"{client_ip(req)}|{ident.strip().lower()}".encode()).hexdigest()
    now = datetime.now(timezone.utc)
    if check_only:
        row = await db.rate_limits.find_one({"_id": key, "expires_at": {"$gt": now}})
        if row and row["count"] >= limit:
            raise HTTPException(429, "Too many attempts. Please try again later.", headers={"Retry-After": str(seconds)})
        return
    row = await db.rate_limits.find_one_and_update({"_id": key, "expires_at": {"$gt": now}}, {"$inc": {"count": 1}},
                                                   return_document=ReturnDocument.AFTER)
    if row is None:
        await db.rate_limits.update_one({"_id": key}, {"$set": {"count": 1, "expires_at": now + timedelta(seconds=seconds)}}, upsert=True)
        count = 1
    else:
        count = row["count"]
    if count > limit:
        raise HTTPException(429, "Too many attempts. Please try again later.", headers={"Retry-After": str(seconds)})


def origin_allowed(origin: str | None) -> bool:
    """None = same-origin/non-browser request (browsers always send Origin on cross-site POSTs)."""
    if not origin:
        return True
    allowed = {o.strip().rstrip("/") for o in os.environ.get("CORS_ORIGINS", "").split(",") if o.strip()}
    if os.environ.get("APP_PUBLIC_URL"):
        allowed.add(os.environ["APP_PUBLIC_URL"].rstrip("/"))
    rx = os.environ.get("CORS_ORIGIN_REGEX")
    return origin.rstrip("/") in allowed or bool(rx and re.fullmatch(rx, origin))


SECURITY_HEADERS = {
    "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
    # API never serves pages: block scripts/framing for anything returned (incl. stored PDFs/images).
    "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'; sandbox",
}


async def security_headers(request: Request, call_next):
    resp = await call_next(request)
    if request.url.path.startswith("/api"):
        for k, v in SECURITY_HEADERS.items():
            resp.headers.setdefault(k, v)
    return resp


_SECRET_RX = re.compile(r"((?:sig|token|refresh_token|password|access_token|exp)=)[^&\s\"']+", re.I)
_BEARER_RX = re.compile(r"(Bearer\s+)[A-Za-z0-9._\-]+")


class RedactSecrets(logging.Filter):
    """Strips signed-URL signatures, tokens and passwords from every log line (incl. uvicorn access logs)."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:
            return True
        clean = _BEARER_RX.sub(r"\1[redacted]", _SECRET_RX.sub(r"\1[redacted]", msg))
        if clean != msg:
            record.msg, record.args = clean, ()
        return True


def install_log_redaction():
    f = RedactSecrets()
    for name in ("", "uvicorn", "uvicorn.access", "uvicorn.error", "server", "auth", "email"):
        lg = logging.getLogger(name)
        lg.addFilter(f)
        for hd in lg.handlers:
            hd.addFilter(f)


def check_production_config():
    """Refuse to boot a production server with development settings."""
    if not PRODUCTION:
        return
    problems = []
    if os.environ.get("DEMO_SEED") == "true":
        problems.append("DEMO_SEED must be false")
    if os.environ.get("EXPOSE_RESET_CODES") == "true":
        problems.append("EXPOSE_RESET_CODES must be unset")
    if len(os.environ.get("JWT_SECRET", "")) < 32:
        problems.append("JWT_SECRET must be at least 32 characters")
    if not os.environ.get("APP_PUBLIC_URL", "").startswith("https://"):
        problems.append("APP_PUBLIC_URL must be the https app URL")
    if "*" in os.environ.get("CORS_ORIGINS", ""):
        problems.append("CORS_ORIGINS must not contain *")
    if problems:
        raise RuntimeError("Unsafe production configuration: " + "; ".join(problems))
