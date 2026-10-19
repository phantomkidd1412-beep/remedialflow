"""Shared DB handle, models and helpers."""
import os
import hmac
import hashlib
import json
import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Any, Optional, List

from bson import ObjectId
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient
from pydantic import BaseModel, BeforeValidator, ConfigDict, Field

load_dotenv(Path(__file__).parent / ".env")

client = AsyncIOMotorClient(os.environ["MONGO_URL"])
db = client[os.environ["DB_NAME"]]
SECRET = os.environ["JWT_SECRET"]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def oid(v: str) -> ObjectId:
    try:
        return ObjectId(v)
    except Exception:
        from fastapi import HTTPException
        raise HTTPException(404, "Not found")


PyObjectId = Annotated[str, BeforeValidator(lambda v: str(v) if isinstance(v, ObjectId) else v)]


class BaseDocument(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")
    id: Optional[PyObjectId] = Field(default=None, alias="_id")

    @classmethod
    def from_mongo(cls, doc: Optional[dict]):
        return cls.model_validate(doc) if doc else None

    def to_mongo(self) -> dict:
        d = self.model_dump(by_alias=True, exclude={"id"})
        d.pop("_id", None)
        if self.id:
            d["_id"] = ObjectId(self.id)
        return d


class User(BaseDocument):
    email: str
    name: str
    role: str  # admin | employee | installer
    password_hash: str = ""
    active: bool = True
    token_version: int = 0
    created_at: str = Field(default_factory=now_iso)

    def public(self) -> dict:
        return {"id": self.id, "email": self.email, "name": self.name, "role": self.role,
                "active": self.active, "created_at": self.created_at}


class Project(BaseDocument):
    ref: str
    address: str
    postcode: str
    installer_id: Optional[str] = None
    rate: float = 0
    status: str = "active"  # active | awaiting_review | completed
    nc_total: int = 0
    nc_done: int = 0
    nc_review: int = 0
    source_report: Optional[dict] = None
    created_at: str = Field(default_factory=now_iso)
    updated_at: str = Field(default_factory=now_iso)


class AIResult(BaseModel):
    outcome: str  # ai_verified | more_evidence_required | manual_review_required
    confidence: float = 0
    feedback: str = ""
    concerns: List[str] = []
    model: str = ""
    at: str = Field(default_factory=now_iso)


class NC(BaseDocument):
    project_id: str
    number: str
    description: str
    required_work: str
    safety_critical: bool = False
    status: str = "open"  # open|assessing|ai_verified|more_evidence|manual_review|approved|rejected
    trustmark_ids: List[str] = []
    draft_submission_id: Optional[str] = None
    active_video_id: Optional[str] = None
    ai: Optional[AIResult] = None
    review_note: str = ""
    reviewed_by: Optional[str] = None
    updated_at: str = Field(default_factory=now_iso)


class EvidenceFile(BaseDocument):
    project_id: str
    nc_id: str
    submission_id: Optional[str] = None
    kind: str  # photo | video | trustmark | frame
    source: str  # in_app_camera | gallery | employee_upload | system
    paths: dict = {}  # variant -> storage path (original, stamped, display, thumb)
    sha256: str
    size: int
    mime: str
    original_name: str = ""
    captured_at: Optional[str] = None  # device-reported ISO
    tz: Optional[str] = None
    tz_offset_min: Optional[int] = None
    lat: Optional[float] = None
    lng: Optional[float] = None
    accuracy_m: Optional[float] = None
    location_verified: bool = False
    duration_s: Optional[float] = None
    uploaded_by: str
    server_received_at: str = Field(default_factory=now_iso)
    status: str = "draft"  # draft | submitted | superseded
    client_id: Optional[str] = None  # idempotency key from device queue
    processing: str = "pending"  # pending | ready | failed


class Submission(BaseDocument):
    project_id: str
    nc_id: str
    installer_id: str
    status: str = "draft"  # draft | submitted
    photo_ids: List[str] = []
    video_id: Optional[str] = None
    submitted_at: Optional[str] = None
    created_at: str = Field(default_factory=now_iso)


class Settings(BaseModel):
    auto_verify: bool = True
    confidence_threshold: float = 0.8
    location_policy: str = "accept_flag"  # accept_flag | reject | accept
    gallery_policy: str = "manual_review"  # manual_review | accept
    default_job_rate: float = 150.0
    currency: str = "GBP"
    payment_terms: str = "Paid monthly for completed jobs"


async def get_settings() -> Settings:
    doc = await db.settings.find_one({"_id": "global"})
    return Settings(**{k: v for k, v in (doc or {}).items() if k != "_id"})


# ---------- Signed temporary URLs ----------
def sign(payload: str) -> str:
    return hmac.new(SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()[:40]


def signed_query(payload: str, ttl_hours: int = 2) -> str:
    # Rounded to the hour so URLs stay stable (cache-friendly) within a window.
    now = int(datetime.now(timezone.utc).timestamp())
    exp = (now // 3600 + ttl_hours) * 3600
    return f"exp={exp}&sig={sign(f'{payload}:{exp}')}"


def verify_signed(payload: str, exp: int, sig: str) -> bool:
    if exp < int(datetime.now(timezone.utc).timestamp()):
        return False
    return hmac.compare_digest(sign(f"{payload}:{exp}"), sig)


def file_url(file_id: str, variant: str) -> str:
    return f"/api/files/{file_id}/{variant}?{signed_query(f'{file_id}:{variant}')}"


# ---------- Immutable audit (hash chained, append-only) ----------
_audit_lock = asyncio.Lock()


async def audit(event: str, actor_id: Optional[str], target: str, data: Optional[dict] = None):
    async with _audit_lock:
        last = await db.audit.find_one(sort=[("seq", -1)])
        seq = (last["seq"] + 1) if last else 1
        prev = last["hash"] if last else "GENESIS"
        entry = {"seq": seq, "event": event, "actor_id": actor_id, "target": target,
                 "data": data or {}, "at": now_iso(), "prev_hash": prev}
        entry["hash"] = hashlib.sha256((prev + json.dumps(entry, sort_keys=True, default=str)).encode()).hexdigest()
        await db.audit.insert_one(entry)


def clean(doc: Any) -> Any:
    if isinstance(doc, dict):
        return {("id" if k == "_id" else k): clean(v) for k, v in doc.items()}
    if isinstance(doc, list):
        return [clean(x) for x in doc]
    if isinstance(doc, ObjectId):
        return str(doc)
    return doc
