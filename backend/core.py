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
    pay_type: str = "per_job"  # per_job | daily
    default_rate: Optional[float] = None
    created_at: str = Field(default_factory=now_iso)

    def public(self) -> dict:
        return {"id": self.id, "email": self.email, "name": self.name, "role": self.role,
                "pay_type": self.pay_type, "default_rate": self.default_rate,
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
    needs_review: bool = False  # attention flag, independent of lifecycle status
    completed_at: Optional[str] = None  # first internal completion (never overwritten)
    completion_history: List[str] = []
    resolved: Optional[dict] = None  # current TrustMark resolution {at, by, by_name}
    resolution_history: List[dict] = []
    source_report: Optional[dict] = None
    rates: dict = {}  # installer_id -> agreed per-job rate (frozen at assignment)
    schedules: dict = {}  # installer_id -> scheduled visit date YYYY-MM-DD (optional, staff-set)
    rate_history: List[dict] = []  # admin overrides {installer_id, old, new, reason, by, at}
    rate_override: bool = False
    deleted: Optional[dict] = None  # soft delete {at, by, by_name, by_role}; data is never erased
    deletion_history: List[dict] = []
    created_at: str = Field(default_factory=now_iso)
    updated_at: str = Field(default_factory=now_iso)


class AIResult(BaseModel):
    outcome: str  # ai_verified | more_evidence_required | manual_review_required | potential_noncompliance
    confidence: Optional[float] = None  # None = no AI result (never shown as 0%)
    status: str = "pending"  # ai_approved | ai_verified_signoff | more_evidence | employee_review | potential_noncompliance | pending | unavailable
    method_demonstrated: Optional[bool] = None
    requests: List[str] = []  # precise evidence requests sent to the installer
    works_description: str = ""  # AI draft of the remedial works (from recorded info only)
    best: dict = {}  # stage -> file id chosen for the PDF
    blocked: List[str] = []  # why automatic approval did not happen (shown to staff)
    feedback: str = ""
    concerns: List[str] = []
    model: str = ""
    risk: str = "standard"  # low | standard | safety
    at: str = Field(default_factory=now_iso)


class NC(BaseDocument):
    project_id: str
    number: str
    description: str
    required_work: str
    safety_critical: bool = False
    original_text: str = ""  # exact TrustMark wording, never altered
    assigned_installer_id: Optional[str] = None
    assignment_history: List[dict] = []
    # open|assessing|more_evidence|employee_review|admin_review|approved|rejected
    status: str = "open"
    approved_by: Optional[str] = None  # "ai" or user id
    approved_at: Optional[str] = None
    rejection_source: Optional[str] = None  # employee | admin | trustmark
    reopen_history: List[dict] = []
    text_history: List[dict] = []  # earlier wording replaced by a re-imported report
    trustmark_ids: List[str] = []
    draft_submission_id: Optional[str] = None
    active_video_id: Optional[str] = None
    ai: Optional[AIResult] = None
    review_note: str = ""
    export_note: str = ""  # "Remedial works carried out" text on the NC PDF (AI-drafted, staff-editable)
    export_note_source: str = ""  # "" | ai | staff
    installer_note: str = ""  # installer's short description of the work done
    requires_stages: bool = False  # new NCs: pre/mid/final photos + a video are mandatory (historic NCs unaffected)
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
    stage: Optional[str] = None  # pre | mid | final (photos; None for historic evidence)
    stage_source: Optional[str] = None  # installer | staff | ai (who set the stage; staff overrides AI in the PDF)
    pdf_include: Optional[bool] = None  # staff override of the PDF photo selection (None = automatic)
    client_id: Optional[str] = None  # idempotency key from device queue
    processing: str = "pending"  # pending | ready | failed
    playback: Optional[str] = None  # videos only: pending | ready | failed (universal H.264 copy for in-app playback)
    video_codec: Optional[str] = None
    excluded: bool = False  # hidden logo/decoration from a PDF import (file kept in storage)
    exclude_reason: Optional[str] = None  # banner | graphic | repeated | manual
    manual_keep: bool = False  # staff restored it; automatic cleanup leaves it alone


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
    gallery_policy: str = "accept"  # manual_review | accept
    auto_approve_low_risk: bool = True  # AI may approve eligible NCs automatically
    auto_approve_risks: List[str] = ["low", "standard"]  # eligible categories (safety-critical never)
    human_signoff_required: bool = False  # AI-verified NCs still need an employee sign-off
    default_job_rate: float = 150.0
    currency: str = "GBP"
    payment_terms: str = "Paid monthly for completed jobs"


async def get_settings() -> Settings:
    doc = await db.settings.find_one({"_id": "global"})
    return Settings(**{k: v for k, v in (doc or {}).items() if k != "_id"})


# ---------- Signed temporary URLs ----------
# Dedicated URL-signing key (separate from JWT signing). Falls back to a key derived from JWT_SECRET (domain-separated).
URL_SIGNING_KEY = os.environ.get("URL_SIGNING_SECRET") or hmac.new(SECRET.encode(), b"remedialflow-url-signing-v1", hashlib.sha256).hexdigest()


def sign(payload: str) -> str:
    return hmac.new(URL_SIGNING_KEY.encode(), payload.encode(), hashlib.sha256).hexdigest()[:40]


def signed_query(payload: str, ttl_minutes: int = 60, stable: bool = True) -> str:
    # stable=True: expiry rounded to a 15-min bucket so image URLs stay cache-friendly (valid 60-75 min).
    # stable=False: exact short expiry for one-off downloads (exports/reports).
    now = int(datetime.now(timezone.utc).timestamp())
    exp = ((now // 900) * 900 + 900 if stable else now) + ttl_minutes * 60
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
    # actor identity comes from the authenticated user id; name/role are snapshotted server-side
    actor = await db.users.find_one({"_id": ObjectId(actor_id)}, {"name": 1, "role": 1}) if actor_id and ObjectId.is_valid(actor_id) else None
    async with _audit_lock:
        last = await db.audit.find_one(sort=[("seq", -1)])
        seq = (last["seq"] + 1) if last else 1
        prev = last["hash"] if last else "GENESIS"
        entry = {"seq": seq, "event": event, "actor_id": actor_id, "target": target,
                 "data": data or {}, "at": now_iso(), "prev_hash": prev}
        if actor:
            entry["actor_name"], entry["actor_role"] = actor.get("name"), actor.get("role")
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
