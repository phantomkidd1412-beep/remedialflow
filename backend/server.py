import io
import os
import re
import json
import zipfile
import hashlib
import asyncio
import logging
import secrets
import tempfile
from pathlib import Path
from typing import Optional, List

from bson import ObjectId
from pymongo.errors import DuplicateKeyError
from fastapi import FastAPI, APIRouter, Depends, HTTPException, UploadFile, File, Form, BackgroundTasks, Query, Request
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, EmailStr, Field
from starlette.concurrency import run_in_threadpool
from starlette.middleware.cors import CORSMiddleware

from core import (db, client, User, Project, NC, EvidenceFile, Submission, AIResult, Settings, get_settings,
                  now_iso, oid, file_url, signed_query, verify_signed, audit, clean)
from auth import router as auth_router, current_user, require, ph, revoke_all, create_reset_token, anonymise_user
from storage import storage, evidence_path, SYSTEM, property_folder_name, media_path, export_path
from media import process_photo, video_frames, make_playback, demo_trustmark_image, pdf_text_and_images
from ai import get_assessor, AssessmentInput, get_pdf_extractor
from guidance import match_guidance, guidance_text, suggest_from_review
from scope import ensure_scope, coverage as scope_coverage, gate_reason as scope_gate
from coverage import evaluate as coverage_evaluate, installer_request as coverage_request
from checklist import gate_reasons as checklist_gate
from documents import router as documents_router, refs_for_assessment as docs_refs, refs_text as docs_text, gate_reasons as docs_gate
from recommend import router as recommend_router
from photo_class import schedule as schedule_classification
from report_import import hex_hash, close, find_duplicate, match_ncs, clean_project_report_photos, known_logos, learn_logos

from security import PRODUCTION, check_production_config, install_log_redaction, security_headers

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
log = logging.getLogger("server")
install_log_redaction()
check_production_config()

# API docs / schema are development-only
app = FastAPI(title="RemedialFlow", docs_url=None if PRODUCTION else "/docs", redoc_url=None, openapi_url=None if PRODUCTION else "/openapi.json")
app.middleware("http")(security_headers)
api = APIRouter(prefix="/api")
staff = require("admin", "employee")
admin = require("admin")
DONE = {"approved"}
OUTSTANDING = {"open", "rejected", "more_evidence"}  # NC still needs (new) installer evidence
REVIEW = {"employee_review", "admin_review"}
LIFECYCLE = {"unassigned", "in_progress", "completed", "trustmark_resolved"}
MAX_UPLOAD = 300 * 1024 * 1024  # photos / reference images
MAX_VIDEO_MB = int(os.environ.get("MAX_VIDEO_MB", "500"))  # configurable per-video size limit (no duration limit)
MEDIA_CACHE = Path(os.environ.get("MEDIA_CACHE_DIR", "/tmp/rf_media"))
_transcode_lock = asyncio.Semaphore(1)  # one video conversion at a time keeps the API responsive


@app.get("/health", include_in_schema=False)
@app.head("/health", include_in_schema=False)
async def health():
    return {"status": "ok"}


# ---------------- helpers ----------------
async def get_project(pid: str, u: User) -> Project:
    p = Project.from_mongo(await db.projects.find_one({"_id": oid(pid)}))
    if not p or p.deleted:
        raise HTTPException(404, "Project not found")
    if u.role == "installer" and not await db.ncs.find_one({"project_id": pid, "assigned_installer_id": u.id}, {"_id": 1}):
        raise HTTPException(403, "Not assigned to this project")
    return p


async def get_nc(nc_id: str, u: User):
    nc = NC.from_mongo(await db.ncs.find_one({"_id": oid(nc_id)}))
    if not nc:
        raise HTTPException(404, "NC not found")
    if u.role == "installer" and nc.assigned_installer_id != u.id:
        raise HTTPException(403, "This NC is not assigned to you")
    return nc, await get_project(nc.project_id, u)


def file_public(f: EvidenceFile) -> dict:
    d = f.model_dump(exclude={"paths"})
    d["urls"] = {v: file_url(f.id, v) for v in f.paths}
    return d


async def recompute_project(pid: str):
    """Lifecycle: unassigned | in_progress | completed | trustmark_resolved, plus needs_review flag."""
    ncs = await db.ncs.find({"project_id": pid}, {"status": 1, "assigned_installer_id": 1}).to_list(500)
    proj = await db.projects.find_one({"_id": oid(pid)}, {"completed_at": 1, "completion_history": 1, "resolved": 1,
                                                          "resolution_history": 1, "status": 1, "ready_notified_at": 1})
    total = len(ncs)
    done = sum(1 for n in ncs if n["status"] in DONE)
    review = sum(1 for n in ncs if n["status"] in REVIEW)
    upd: dict = {"nc_total": total, "nc_done": done, "nc_review": review, "needs_review": review > 0, "updated_at": now_iso()}
    if total and done == total:
        status = "trustmark_resolved" if proj.get("resolved") else "completed"
        if not proj.get("completed_at"):
            upd["completed_at"] = now_iso()
        if proj.get("status") not in ("completed", "trustmark_resolved"):
            upd["completion_history"] = (proj.get("completion_history") or []) + [now_iso()]
    else:
        if proj.get("resolved"):  # reopened after resolution: keep history, clear current resolution
            upd["resolved"] = None
            await audit("project.reopened", None, f"project:{pid}", {"previous_resolution": proj["resolved"]})
        status = "in_progress" if any(n.get("assigned_installer_id") for n in ncs) else "unassigned"
    upd["status"] = status
    # Ready for review = every NC has server-confirmed submitted evidence (none open/rejected/needing more),
    # and not all approved yet. Notified once; re-armed if any NC goes back to outstanding.
    all_submitted = bool(total) and all(n["status"] not in OUTSTANDING for n in ncs)
    notify_ready = all_submitted and done < total and not proj.get("ready_notified_at")
    if notify_ready:
        upd["ready_notified_at"] = now_iso()
    elif not all_submitted and proj.get("ready_notified_at"):
        upd["ready_notified_at"] = None
    await db.projects.update_one({"_id": oid(pid)}, {"$set": upd})
    if "completion_history" in upd:  # project just became fully resolved (every NC approved)
        await queue_auto_pack(pid, len(upd["completion_history"]))
    if notify_ready:
        await notify_project_ready(pid)
    await refresh_notifications(pid)
    if "completion_history" in upd:
        async for st in db.users.find({"role": {"$in": ["employee", "admin"]}, "active": True}, {"_id": 1}):
            await notify(str(st["_id"]), "completed", pid)
    await settle_earnings(pid)


async def notify_project_ready(pid: str):
    p = await db.projects.find_one({"_id": oid(pid)}, {"ref": 1, "deleted": 1})
    if not p or p.get("deleted"):
        return
    # Audit only: reviewers already get the project's single "Ready for review" notification (category "review").
    await audit("project.ready_for_review", None, f"project:{pid}", {"ref": p["ref"]})


async def live_project(pid: str) -> dict:
    """Raw project doc; deleted (recycle bin) projects behave as not found for normal operations."""
    p = await db.projects.find_one({"_id": oid(pid)})
    if not p or p.get("deleted"):
        raise HTTPException(404, "Project not found")
    return p


# ---------------- project-level notifications ----------------
# One entry per (user, project, category). Further NCs of the same category update that entry (never a new row);
# its summary is recomputed from live NC data whenever the project changes, so counts stay accurate.
LIVE_CATS = {"review", "escalated", "action", "approved", "assigned"}  # summaries derived from current NC states
CAT_TITLE = {"review": "Ready for review", "escalated": "Escalated to Admin", "action": "Work rejected - action needed",
             "approved": "Work approved", "assigned": "New work assigned", "project_ready": "Ready for review",
             "completed": "Project completed", "rate_changed": "Rate updated", "deadline": "Site visit tomorrow"}


def _n(k: int, word: str = "NC") -> str:
    return f"{k} {word}{'' if k == 1 else 's'}"


async def _summary(cat: str, pid: str, uid: str, ncs: list) -> tuple:
    """(active, summary text) for one user's notification in one category on one project."""
    mine = [n for n in ncs if n.get("assigned_installer_id") == uid]
    if cat == "review":
        k = sum(1 for n in ncs if n["status"] == "employee_review")
        return k > 0, f"{_n(k)} ready for review"
    if cat == "escalated":
        k = sum(1 for n in ncs if n["status"] == "admin_review")
        return k > 0, f"{_n(k)} awaiting Admin review"
    if cat == "action":
        k = sum(1 for n in mine if n["status"] in ("rejected", "more_evidence"))
        return k > 0, f"{_n(k)} need more work"
    if cat == "approved":
        k = sum(1 for n in mine if n["status"] in DONE)
        return k > 0, f"{k} of {_n(len(mine))} approved"
    if cat == "assigned":
        k = sum(1 for n in mine if n["status"] not in DONE)
        return k > 0, f"{_n(k)} assigned to you"
    if cat == "project_ready":
        return True, "All NC evidence submitted. Ready for review."
    if cat == "completed":
        return True, "All NCs approved. Evidence pack is being saved."
    if cat == "deadline":
        return True, "You have a site visit scheduled here tomorrow."
    return True, ""


async def notify(user_id: Optional[str], cat: str, project_id: str, extra: str = ""):
    """Creates or updates the user's single notification for this project + category and marks it unread."""
    if not user_id:
        return
    u = await db.users.find_one({"_id": oid(user_id), "active": True}, {"role": 1})
    if not u:
        return  # disabled accounts receive nothing new
    p = await db.projects.find_one({"_id": oid(project_id)}, {"ref": 1, "address": 1, "postcode": 1, "deleted": 1})
    if not p or p.get("deleted"):
        return  # deleted projects never notify
    ncs = await db.ncs.find({"project_id": project_id}, {"status": 1, "assigned_installer_id": 1}).to_list(500)
    if u["role"] == "installer" and not any(n.get("assigned_installer_id") == user_id for n in ncs):
        return  # installers only hear about projects they are assigned to
    active, summary = await _summary(cat, project_id, user_id, ncs)
    if not active:
        return
    key = f"{cat}:{project_id}"
    addr = f"{p['address']}{', ' + p['postcode'] if p.get('postcode') else ''}"
    body = summary + (f"\n{extra}" if extra else "")
    old = await db.notifications.find_one({"user_id": user_id, "key": key}, {"body": 1, "hidden": 1})
    if old and old.get("body") == body and not old.get("hidden"):
        return  # same event re-processed (refresh/save/re-assessment): no duplicate alert, in-app or push
    prev = await db.notifications.find_one_and_update(
        {"user_id": user_id, "key": key},
        {"$set": {"kind": cat, "title": CAT_TITLE.get(cat, cat), "address": addr, "ref": p["ref"], "summary": summary, "body": body,
                  "project_id": project_id, "nc_id": None, "read": False, "hidden": False, "created_at": now_iso()},
         "$setOnInsert": {"user_id": user_id, "key": key}}, upsert=True)
    if not prev or prev.get("read"):  # push only when it newly becomes unread (updates of an unread one stay silent)
        from push import push_for
        asyncio.create_task(push_for(user_id, cat, f"{key}:{now_iso()}"))


async def refresh_notifications(pid: str):
    """Keeps every project notification's summary true after approvals, rejections, reopening or reassignment.
    Entries whose condition no longer applies are marked read; installers no longer on the project lose theirs."""
    rows = await db.notifications.find({"project_id": pid, "kind": {"$in": list(LIVE_CATS)}, "hidden": {"$ne": True}}).to_list(1000)
    if not rows:
        return
    ncs = await db.ncs.find({"project_id": pid}, {"status": 1, "assigned_installer_id": 1}).to_list(500)
    roles = {str(d["_id"]): d["role"] for d in await db.users.find({"_id": {"$in": [ObjectId(r["user_id"]) for r in rows]}}, {"role": 1}).to_list(1000)}
    for r in rows:
        uid = r["user_id"]
        if roles.get(uid) == "installer" and not any(n.get("assigned_installer_id") == uid for n in ncs):
            await db.notifications.update_one({"_id": r["_id"]}, {"$set": {"hidden": True, "read": True}})
            continue
        active, summary = await _summary(r["kind"], pid, uid, ncs)
        upd = {"summary": summary, "body": summary + (r["body"][len(r.get("summary") or ""):] if r.get("summary") and r["body"].startswith(r["summary"]) else "")}
        if not active:
            upd["read"] = True
        await db.notifications.update_one({"_id": r["_id"]}, {"$set": upd})


async def notify_reviewers(nc_id: str):
    nc = await db.ncs.find_one({"_id": oid(nc_id)}, {"project_id": 1})
    async for e in db.users.find({"role": "employee", "active": True}, {"_id": 1}):
        await notify(str(e["_id"]), "review", nc["project_id"])


async def nc_notify(nc: dict, user_id: Optional[str], cat: str, extra: str = ""):
    await notify(user_id, cat, nc["project_id"], f"Latest: {nc['number']}" + (f" - {extra}" if extra else ""))


async def agreed_rate(pid: str, installer_id: str) -> float:
    """Freeze the per-job rate for an installer on first assignment to a project."""
    p = await db.projects.find_one({"_id": oid(pid)}, {"rates": 1, "rate": 1, "rate_override": 1})
    if installer_id in (p.get("rates") or {}):
        return p["rates"][installer_id]
    u = await db.users.find_one({"_id": oid(installer_id)}, {"default_rate": 1})
    s = await get_settings()
    rate = p["rate"] if p.get("rate_override") else ((u or {}).get("default_rate") or s.default_job_rate)
    await db.projects.update_one({"_id": oid(pid)}, {"$set": {f"rates.{installer_id}": rate}})
    return rate


async def settle_earnings(pid: str):
    """Per-job installers earn once when all NCs assigned to them on this project are internally approved.
    Daily-wage users never get job earnings. Existing earned/paid records are never removed."""
    ncs = await db.ncs.find({"project_id": pid}, {"status": 1, "assigned_installer_id": 1}).to_list(500)
    scope: dict = {}
    for n in ncs:
        if n.get("assigned_installer_id"):
            scope.setdefault(n["assigned_installer_id"], []).append(n["status"])
    p = await db.projects.find_one({"_id": oid(pid)}, {"ref": 1, "address": 1, "deleted": 1})
    if not p or p.get("deleted"):
        return  # deleted projects never generate earnings
    s = await get_settings()
    for iid, sts in scope.items():
        if not all(x in DONE for x in sts):
            continue
        u = await db.users.find_one({"_id": oid(iid)}, {"pay_type": 1})
        if not u or u.get("pay_type", "per_job") != "per_job":
            continue
        if await db.earnings.find_one({"project_id": pid, "installer_id": iid}):
            continue
        amount = await agreed_rate(pid, iid)
        try:  # unique index on (project_id, installer_id) makes concurrent approvals idempotent
            await db.earnings.insert_one({"installer_id": iid, "project_id": pid, "ref": p["ref"], "address": p["address"],
                                          "amount": amount, "currency": s.currency, "status": "earned", "earned_at": now_iso(), "paid_at": None})
        except DuplicateKeyError:
            continue
        await audit("earning.created", None, f"project:{pid}", {"installer_id": iid, "amount": amount})



# ---------------- admin: users ----------------
class UserCreate(BaseModel):
    email: EmailStr
    name: str = Field(min_length=1)
    role: str = Field(pattern="^(admin|employee|installer)$")
    password: Optional[str] = Field(default=None, min_length=8)


class UserPatch(BaseModel):
    name: Optional[str] = None
    role: Optional[str] = Field(default=None, pattern="^(admin|employee|installer)$")
    active: Optional[bool] = None
    pay_type: Optional[str] = Field(default=None, pattern="^(per_job|daily)$")
    default_rate: Optional[float] = Field(default=None, ge=0)


@api.get("/admin/users")
async def list_users(u: User = Depends(admin)):
    return [User.from_mongo(d).public() for d in await db.users.find({"deleted_account": {"$ne": True}}).sort("created_at", -1).to_list(1000)]


@api.post("/admin/users")
async def create_user(body: UserCreate, u: User = Depends(admin)):
    email = body.email.lower()
    if await db.users.find_one({"email": email}):
        raise HTTPException(409, "Email already exists")
    nu = User(email=email, name=body.name, role=body.role, password_hash=ph.hash(body.password) if body.password else "")
    res = await db.users.insert_one(nu.to_mongo())
    nu.id = str(res.inserted_id)
    code = None if body.password else await create_reset_token(nu.id, hours=72)
    await audit("user.created", u.id, f"user:{nu.id}", {"role": nu.role})
    return {"user": nu.public(), "setup_code": code}


@api.patch("/admin/users/{uid}")
async def patch_user(uid: str, body: UserPatch, u: User = Depends(admin)):
    upd = {k: v for k, v in body.model_dump().items() if v is not None}
    if uid == u.id and (body.active is False or (body.role and body.role != "admin")):
        raise HTTPException(400, "You cannot deactivate or demote yourself")
    q = {"$set": upd}
    if body.active is False or body.role:
        q["$inc"] = {"token_version": 1}  # invalidates live access tokens immediately
    r = await db.users.find_one_and_update({"_id": oid(uid), "deleted_account": {"$ne": True}}, q, return_document=True)
    if not r:
        raise HTTPException(404, "User not found")
    if body.active is False:
        await revoke_all(uid)
    await audit("user.updated", u.id, f"user:{uid}", upd)
    return User.from_mongo(r).public()


@api.delete("/admin/users/{uid}")
async def admin_delete_user(uid: str, u: User = Depends(admin)):
    if uid == u.id:
        raise HTTPException(400, "Use Account → Delete account to delete your own account")
    await anonymise_user(uid, u.id, "admin")
    return {"ok": True}


@api.post("/admin/users/{uid}/reset")
async def admin_reset(uid: str, u: User = Depends(admin)):
    if not await db.users.find_one({"_id": oid(uid), "deleted_account": {"$ne": True}}):
        raise HTTPException(404, "User not found")
    code = await create_reset_token(uid, hours=72)
    await audit("user.reset_issued", u.id, f"user:{uid}")
    return {"reset_code": code}


@api.get("/installers")
async def installers(u: User = Depends(staff)):
    docs = await db.users.find({"role": "installer", "active": True}, {"name": 1, "email": 1}).to_list(1000)
    return [{"id": str(d["_id"]), "name": d["name"], "email": d["email"]} for d in docs]


# ---------------- settings ----------------
@api.get("/settings")
async def read_settings(u: User = Depends(staff)):
    d = (await get_settings()).model_dump()
    if u.role != "admin":  # payment configuration is admin-only
        for k in ("default_job_rate", "currency", "payment_terms"):
            d.pop(k, None)
    return d


@api.put("/settings")
async def write_settings(body: Settings, u: User = Depends(admin)):
    cur = await get_settings()  # the location-aware flags are only changed by their own audited switch below
    body = body.model_copy(update={k: getattr(cur, k) for k in LOCATION_FLAGS})
    await db.settings.update_one({"_id": "global"}, {"$set": body.model_dump()}, upsert=True)
    await audit("settings.updated", u.id, "settings", body.model_dump())
    return body.model_dump()


LOCATION_FLAGS = ("location_scope_enabled", "photo_classification_enabled", "coverage_check_enabled", "smart_pdf_enabled", "staff_review_enabled")


class LocationEvidenceIn(BaseModel):
    enabled: bool


@api.get("/settings/location-evidence")
async def read_location_evidence(u: User = Depends(admin)):
    s = await get_settings()
    on = [k for k in LOCATION_FLAGS if getattr(s, k)]
    return {"state": "on" if len(on) == len(LOCATION_FLAGS) else "off" if not on else "partial", "flags": {k: getattr(s, k) for k in LOCATION_FLAGS}}


@api.put("/settings/location-evidence")
async def write_location_evidence(body: LocationEvidenceIn, u: User = Depends(admin)):
    """One admin switch for all five location-aware features (Phases 1-5). Applies immediately; audited."""
    await db.settings.update_one({"_id": "global"}, {"$set": {k: body.enabled for k in LOCATION_FLAGS}}, upsert=True)
    await audit("settings.location_evidence_" + ("enabled" if body.enabled else "disabled"), u.id, "settings", {k: body.enabled for k in LOCATION_FLAGS})
    return await read_location_evidence(u)


# ---------------- dashboard / projects ----------------
@api.get("/dashboard")
async def dashboard(u: User = Depends(staff)):
    # Lifecycle counters are mutually exclusive; awaiting_review is the attention flag (projects, not NCs).
    out = {k: await db.projects.count_documents({"status": k, "deleted": None}) for k in LIFECYCLE}
    out["awaiting_review"] = await db.projects.count_documents({"needs_review": True, "deleted": None})
    return out


@api.post("/projects/{pid}/resolve")
async def resolve_project(pid: str, u: User = Depends(staff)):
    p = Project.from_mongo(await live_project(pid))
    if not p:
        raise HTTPException(404, "Project not found")
    if p.status != "completed":
        raise HTTPException(400, "Only internally completed projects can be marked TrustMark Resolved")
    res = {"at": now_iso(), "by": u.id, "by_name": u.name}
    await db.projects.update_one({"_id": oid(pid)}, {"$set": {"resolved": res, "status": "trustmark_resolved"},
                                                    "$push": {"resolution_history": res}})
    await audit("project.trustmark_resolved", u.id, f"project:{pid}", res)
    return {"ok": True, "resolved": res}


@api.get("/projects")
async def list_projects(search: str = "", status: str = "", page: int = 1, limit: int = Query(20, le=100),
                        u: User = Depends(current_user)):
    q: dict = {"deleted": None}
    if u.role == "installer":
        q["_id"] = {"$in": [ObjectId(x) for x in await db.ncs.distinct("project_id", {"assigned_installer_id": u.id})]}
    if status == "awaiting_review":
        q["needs_review"] = True
    elif status:
        q["status"] = status
    if search:
        rx = {"$regex": re.escape(search), "$options": "i"}
        q["$or"] = [{"ref": rx}, {"address": rx}, {"postcode": rx}]
    total = await db.projects.count_documents(q)
    docs = await db.projects.find(q).sort("updated_at", -1).skip((page - 1) * limit).limit(limit).to_list(limit)
    items = [Project.from_mongo(d).model_dump() for d in docs]
    ids = list({i["installer_id"] for i in items if i["installer_id"]})
    names = {str(d["_id"]): d["name"] for d in await db.users.find({"_id": {"$in": [ObjectId(i) for i in ids]}}, {"name": 1}).to_list(500)}
    # Installer names per project = union of NC-level assignees.
    pids = [i["id"] for i in items]
    per: dict = {}
    for n in await db.ncs.find({"project_id": {"$in": pids}, "assigned_installer_id": {"$ne": None}}, {"project_id": 1, "assigned_installer_id": 1}).to_list(5000):
        per.setdefault(n["project_id"], set()).add(n["assigned_installer_id"])
    all_ids = set(ids) | {x for s in per.values() for x in s}
    names = {str(d["_id"]): d["name"] for d in await db.users.find({"_id": {"$in": [ObjectId(i) for i in all_ids]}}, {"name": 1}).to_list(500)}
    if u.role == "installer":
        mine = await db.ncs.find({"project_id": {"$in": pids}, "assigned_installer_id": u.id}, {"project_id": 1, "status": 1, "number": 1}).to_list(5000)
        for i in items:
            sts = [n["status"] for n in mine if n["project_id"] == i["id"]]
            i["nc_total"], i["nc_done"] = len(sts), sum(1 for x in sts if x in DONE)
            i["my_statuses"] = sts
            i["my_nc_numbers"] = sorted(n.get("number", "") for n in mine if n["project_id"] == i["id"])
            i["my_schedule"] = (i.get("schedules") or {}).get(u.id)
    for i in items:
        if u.role != "admin":
            i.pop("rates", None)
            i.pop("rate", None)
            i.pop("rate_history", None)
        if u.role == "installer":
            i.pop("schedules", None)
    for i in items:
        i["installer_name"] = names.get(i["installer_id"] or "", None)
        i["installer_names"] = sorted(names[x] for x in per.get(i["id"], set()) if x in names)
    return {"items": items, "total": total, "page": page, "has_more": page * limit < total}


class NCIn(BaseModel):
    description: str = Field(min_length=1)
    required_work: str = ""  # optional: imported TrustMark guidance kept as data, not shown on the form
    safety_critical: bool = False
    original_text: str = ""  # verbatim TrustMark wording (defaults to description)
    pages: List[int] = []  # PDF pages whose photos become TrustMark references


class ProjectIn(BaseModel):
    ref: str = Field(min_length=1)
    address: str = Field(min_length=1)
    postcode: str = ""
    installer_id: Optional[str] = None
    rate: Optional[float] = None
    ncs: List[NCIn] = []
    import_id: Optional[str] = None
    require_video: bool = True  # default ON: a relevant video is mandatory for every new NC


# ---------------- PDF import (async) ----------------
async def _parse_pdf(import_id: str, data: bytes):
    import tempfile
    try:
        text, images, photo_stats = await run_in_threadpool(pdf_text_and_images, data, 60, await known_logos())
        await learn_logos(photo_stats.pop("learned", []))
        page_images = {}
        for page, imgs in images.items():
            paths = []
            for k, b in enumerate(imgs):
                paths.append(await storage.aput(f"{SYSTEM}/imports/{import_id}/page{page}-{k}.jpg", b, "image/jpeg"))
            page_images[str(page)] = paths
        with tempfile.NamedTemporaryFile(suffix=".pdf") as tf:
            tf.write(data)
            tf.flush()
            draft = await get_pdf_extractor().extract(tf.name, text)
        for n in draft["ncs"]:
            n["photo_count"] = sum(len(page_images.get(str(p), [])) for p in n["pages"])
        await db.imports.update_one({"_id": ObjectId(import_id)}, {"$set": {
            "status": "ready", "draft": draft, "page_images": page_images, "page_count": text.count("--- page "),
            "photo_stats": photo_stats, "duplicate": await find_duplicate(draft), "finished_at": now_iso()}})
    except Exception as e:
        log.exception("pdf import failed")
        await db.imports.update_one({"_id": ObjectId(import_id)}, {"$set": {"status": "failed", "error": str(e)[:200]}})


@api.post("/projects/import-pdf")
async def import_pdf(bg: BackgroundTasks, file: UploadFile = File(...), u: User = Depends(staff)):
    data = await file.read()
    if not data.startswith(b"%PDF"):
        raise HTTPException(400, "Please choose a PDF file")
    if len(data) > 40 * 1024 * 1024:
        raise HTTPException(413, "PDF too large (max 40 MB)")
    iid = ObjectId()
    path = await storage.aput(f"{SYSTEM}/imports/{iid}/report.pdf", data, "application/pdf")
    await db.imports.insert_one({"_id": iid, "status": "processing", "path": path, "sha256": hashlib.sha256(data).hexdigest(),
                                 "filename": file.filename, "size": len(data), "uploaded_by": u.id, "created_at": now_iso()})
    await audit("import.uploaded", u.id, f"import:{iid}", {"filename": file.filename, "size": len(data)})
    bg.add_task(_parse_pdf, str(iid), data)
    return {"id": str(iid), "status": "processing"}


@api.get("/imports/{iid}")
async def get_import(iid: str, u: User = Depends(staff)):
    d = await db.imports.find_one({"_id": oid(iid)}, {"page_images": 0})
    if not d:
        raise HTTPException(404, "Import not found")
    if d.get("duplicate"):  # deleted/restored state may have changed since parsing
        dp = await db.projects.find_one({"_id": oid(d["duplicate"]["project_id"])}, {"deleted": 1})
        d["duplicate"]["deleted"] = bool(dp and dp.get("deleted"))
    return clean(d)


async def _attach_pdf_photos(import_id: str, nc_pages: List[tuple]):
    imp = await db.imports.find_one({"_id": ObjectId(import_id)})
    pi = (imp or {}).get("page_images", {})
    sys_user = User(id=imp["uploaded_by"], email="import", name="import", role="employee")
    for nc_id, pages in nc_pages:
        nc = NC.from_mongo(await db.ncs.find_one({"_id": ObjectId(nc_id)}))
        p = Project.from_mongo(await db.projects.find_one({"_id": oid(nc.project_id)}))
        have = [d["dhash"] for d in await db.files.find({"nc_id": nc_id, "kind": "trustmark", "dhash": {"$exists": True}}, {"dhash": 1}).to_list(500)]
        for page in pages:
            for path in pi.get(str(page), []):
                try:
                    data, _ = await storage.aget(path)
                    h = hex_hash(data)
                    if any(close(h, x) for x in have):  # already on this NC (e.g. re-imported report)
                        continue
                    have.append(h)
                    bg = BackgroundTasks()
                    up = UploadFile(io.BytesIO(data), filename=f"report-p{page}.jpg", headers={"content-type": "image/jpeg"})
                    f = await store_upload(nc, p, sys_user, up, {}, "trustmark", "pdf_import", None, bg)
                    await db.files.update_one({"_id": oid(f.id)}, {"$set": {"dhash": h}})
                    await db.ncs.update_one({"_id": oid(nc.id)}, {"$push": {"trustmark_ids": f.id}})
                    await bg()
                except Exception:
                    log.exception("attach pdf photo failed")


class ProjectPatch(BaseModel):
    installer_id: Optional[str] = None
    rate: Optional[float] = None
    require_video: Optional[bool] = None


async def add_nc(pid: str, body: NCIn, idx: int, installer_id: Optional[str] = None) -> str:
    d = body.model_dump(exclude={"pages"})
    d["original_text"] = d.get("original_text") or d["description"]
    nc = NC(project_id=pid, number=f"NC-{idx:02d}", assigned_installer_id=installer_id, requires_stages=True, **d)
    r = await db.ncs.insert_one(nc.to_mongo())
    return str(r.inserted_id)


async def assign_unassigned_ncs(pid: str, installer_id: Optional[str], actor: User):
    """Project-level assignment defaults every unassigned NC to that installer."""
    if not installer_id:
        return
    ncs = await db.ncs.find({"project_id": pid, "assigned_installer_id": None}, {"_id": 1}).to_list(500)
    if ncs:
        await agreed_rate(pid, installer_id)
        p = await db.projects.find_one({"_id": oid(pid)}, {"ref": 1, "address": 1})
        await notify(installer_id, "assigned", pid)
    for n in ncs:
        await db.ncs.update_one({"_id": n["_id"]}, {"$set": {"assigned_installer_id": installer_id},
                                "$push": {"assignment_history": {"to": installer_id, "from": None, "by": actor.id, "at": now_iso()}}})
        await audit("nc.assigned", actor.id, f"nc:{n['_id']}", {"to": installer_id})


@api.post("/projects")
async def create_project(body: ProjectIn, bg: BackgroundTasks, u: User = Depends(staff)):
    if await db.projects.find_one({"ref": body.ref}):
        if await db.projects.find_one({"ref": body.ref, "deleted": {"$ne": None}}, {"_id": 1}):
            raise HTTPException(409, "This project reference exists in Deleted Projects. Restore it to continue.")
        raise HTTPException(409, "Project reference already exists")
    s = await get_settings()
    p = Project(ref=body.ref, address=body.address, postcode=body.postcode.upper(), installer_id=body.installer_id or None,
                rate=body.rate if body.rate is not None else s.default_job_rate, rate_override=body.rate is not None, require_video=body.require_video)
    doc = p.to_mongo()
    if body.import_id:
        imp = await db.imports.find_one({"_id": oid(body.import_id)}, {"path": 1, "sha256": 1, "filename": 1})
        if imp:
            doc["source_report"] = {"import_id": body.import_id, "path": imp["path"], "sha256": imp["sha256"], "filename": imp.get("filename")}
    r = await db.projects.insert_one(doc)
    pid = str(r.inserted_id)
    nc_pages = []
    for i, n in enumerate(body.ncs, 1):
        nid = await add_nc(pid, n, i, p.installer_id)
        if n.pages:
            nc_pages.append((nid, n.pages))
    if p.installer_id:
        await agreed_rate(pid, p.installer_id)
        await notify(p.installer_id, "assigned", pid)
    await recompute_project(pid)
    if body.import_id:
        await db.imports.update_one({"_id": oid(body.import_id)}, {"$set": {"project_id": pid}})
        if nc_pages:
            bg.add_task(_attach_pdf_photos, body.import_id, nc_pages)
    await audit("project.created", u.id, f"project:{pid}", {"ref": p.ref, "import_id": body.import_id})
    return {"id": pid}


@api.patch("/projects/{pid}")
async def patch_project(pid: str, body: ProjectPatch, u: User = Depends(staff)):
    await live_project(pid)
    upd = {k: v for k, v in body.model_dump().items() if v is not None}
    if "installer_id" in upd and upd["installer_id"] == "":
        upd["installer_id"] = None
    await db.projects.update_one({"_id": oid(pid)}, {"$set": upd})
    if upd.get("installer_id"):
        await assign_unassigned_ncs(pid, upd["installer_id"], u)
        await recompute_project(pid)
    await audit("project.updated", u.id, f"project:{pid}", upd)
    return {"ok": True}


# ---------------- re-imported reports: merge into an existing project ----------------
async def _ready_import(iid: str) -> dict:
    imp = await db.imports.find_one({"_id": oid(iid)})
    if not imp or imp.get("status") != "ready":
        raise HTTPException(400, "Import is not ready")
    return imp


@api.get("/imports/{iid}/merge-preview")
async def merge_preview(iid: str, project_id: str, u: User = Depends(staff)):
    imp = await _ready_import(iid)
    p = await db.projects.find_one({"_id": oid(project_id)}, {"ref": 1, "address": 1, "postcode": 1, "deleted": 1})
    if not p:
        raise HTTPException(404, "Project not found")
    if p.get("deleted"):
        raise HTTPException(409, "This project is in Deleted Projects. Restore it to continue the import.")
    existing = await db.ncs.find({"project_id": project_id}).sort("number", 1).to_list(500)
    items = match_ncs(imp["draft"]["ncs"], existing)
    matched = {i["nc"]["id"] for i in items if i["nc"]}
    return {"project": {"id": project_id, "ref": p["ref"], "address": p["address"], "postcode": p.get("postcode", "")},
            "items": items, "photo_stats": imp.get("photo_stats"),
            "untouched": [{"id": str(e["_id"]), "number": e["number"], "description": e.get("description", "")} for e in existing if str(e["_id"]) not in matched]}


class MergeItem(BaseModel):
    index: int
    action: str = Field(pattern="^(update|new|skip)$")
    nc_id: Optional[str] = None


class MergeIn(BaseModel):
    import_id: str
    items: List[MergeItem]


@api.post("/projects/{pid}/merge-import")
async def merge_import(pid: str, body: MergeIn, bg: BackgroundTasks, u: User = Depends(staff)):
    """Applies a re-imported report: updates matched NCs (old wording kept in text_history), adds new NCs.
    Existing NCs, statuses, evidence and history are never removed."""
    imp = await _ready_import(body.import_id)
    proj = await live_project(pid)
    drafts = imp["draft"]["ncs"]
    if any(it.index < 0 or it.index >= len(drafts) for it in body.items):
        raise HTTPException(400, "Unknown NC in report")
    counts, nc_pages, ts = {"update": 0, "new": 0, "skip": 0}, [], now_iso()
    n_existing = await db.ncs.count_documents({"project_id": pid})
    for it in body.items:
        d = drafts[it.index]
        counts[it.action] += 1
        if it.action == "update":
            nc = await db.ncs.find_one({"_id": oid(it.nc_id or ""), "project_id": pid})
            if not nc:
                raise HTTPException(400, f"NC to update not found for report item {it.index + 1}")
            new = {"original_text": d.get("original_text") or d["description"], "description": d["description"],
                   "required_work": d["required_work"], "safety_critical": bool(d.get("safety_critical"))}
            old = {k: nc.get(k) for k in new}
            if old != new:
                await db.ncs.update_one({"_id": nc["_id"]}, {"$set": {**new, "updated_at": ts},
                                        "$push": {"text_history": {**old, "replaced_at": ts, "by": u.id, "import_id": body.import_id}}})
            await audit("nc.updated_from_report", u.id, f"nc:{it.nc_id}", {"import_id": body.import_id, "changed": old != new})
            if d.get("pages"):
                nc_pages.append((it.nc_id, d["pages"]))
        elif it.action == "new":
            n_existing += 1
            nid = await add_nc(pid, NCIn(**{k: d.get(k) for k in ("description", "required_work", "original_text") if d.get(k)},
                                         safety_critical=bool(d.get("safety_critical"))), n_existing, proj.get("installer_id"))
            await audit("nc.created", u.id, f"nc:{nid}", {"import_id": body.import_id})
            if d.get("pages"):
                nc_pages.append((nid, d["pages"]))
    upd = {"$set": {"source_report": {"import_id": body.import_id, "path": imp["path"], "sha256": imp["sha256"], "filename": imp.get("filename")}}}
    if proj.get("source_report"):
        upd["$push"] = {"report_history": {**proj["source_report"], "replaced_at": ts, "by": u.id}}
    await db.projects.update_one({"_id": oid(pid)}, upd)
    await db.imports.update_one({"_id": oid(body.import_id)}, {"$set": {"project_id": pid, "merged_at": ts}})
    if counts["new"] and proj.get("installer_id"):
        await notify(proj["installer_id"], "assigned", pid, f"{counts['new']} new NC(s) added from updated report")
    await recompute_project(pid)
    if nc_pages:
        bg.add_task(_attach_pdf_photos, body.import_id, nc_pages)
    await audit("project.merged_import", u.id, f"project:{pid}", {"import_id": body.import_id, **counts})
    return {"ok": True, **counts}


@api.post("/projects/{pid}/clean-report-photos")
async def clean_report_photos(pid: str, u: User = Depends(staff)):
    n = await clean_project_report_photos(pid)
    await audit("project.report_photos_cleaned", u.id, f"project:{pid}", {"hidden": n})
    return {"hidden": n}


class ExcludeIn(BaseModel):
    excluded: bool


@api.post("/files/{fid}/exclude")
async def exclude_file(fid: str, body: ExcludeIn, u: User = Depends(staff)):
    """Hide/restore a TrustMark reference image. The file always stays in storage."""
    f = await db.files.find_one({"_id": oid(fid), "kind": "trustmark"})
    if not f:
        raise HTTPException(404, "Reference photo not found")
    await db.files.update_one({"_id": f["_id"]}, {"$set": {"excluded": body.excluded, "manual_keep": not body.excluded,
                                                         "exclude_reason": "manual" if body.excluded else None}})
    await audit("evidence.excluded" if body.excluded else "evidence.restored", u.id, f"nc:{f['nc_id']}", {"file_id": fid})
    return {"ok": True}


@api.post("/projects/{pid}/ncs")
async def create_nc(pid: str, body: NCIn, u: User = Depends(staff)):
    await get_project(pid, u)
    n = await db.ncs.count_documents({"project_id": pid})
    proj = await db.projects.find_one({"_id": oid(pid)}, {"installer_id": 1})
    nid = await add_nc(pid, body, n + 1, proj.get("installer_id"))
    await recompute_project(pid)
    await audit("nc.created", u.id, f"nc:{nid}")
    return {"id": nid}


@api.get("/projects/{pid}")
async def project_detail(pid: str, u: User = Depends(current_user)):
    p = await get_project(pid, u)
    nq = {"project_id": pid, **({"assigned_installer_id": u.id} if u.role == "installer" else {})}
    ncs = [NC.from_mongo(d) for d in await db.ncs.find(nq).sort("number", 1).to_list(500)]
    files = [EvidenceFile.from_mongo(d) for d in await db.files.find(
        {"project_id": pid, "nc_id": {"$in": [n.id for n in ncs]}, "status": {"$in": ["draft", "submitted", "superseded"]}}).sort("server_received_at", 1).to_list(20000)]
    by_nc: dict = {}
    for f in files:
        by_nc.setdefault(f.nc_id, []).append(f)
    out = []
    for n in ncs:
        fs = by_nc.get(n.id, [])
        trust = [file_public(f) for f in fs if f.kind == "trustmark" and not f.excluded]
        hidden = [file_public(f) for f in fs if f.kind == "trustmark" and f.excluded] if u.role != "installer" else []
        photos = [file_public(f) for f in fs if f.kind == "photo" and f.status != "superseded"]
        videos = [file_public(f) for f in fs if f.kind == "video"]  # every video, incl. ones previously marked superseded
        d = n.model_dump()
        if u.role == "installer":
            d.pop("scope", None)  # staff-only analysis
            d.pop("coverage", None)
            d.pop("checklist", None)
        d.update(trustmark=trust, trustmark_hidden=hidden, photos=photos, videos=videos, video=videos[-1] if videos else None,
                 video_versions=len(videos), video_required=bool(n.requires_stages and p.require_video is not False),
                 draft_count=sum(1 for f in fs if f.status == "draft" and f.kind in ("photo", "video")))
        out.append(d)
    pd = p.model_dump()
    uids = {n.assigned_installer_id for n in ncs if n.assigned_installer_id} | ({p.installer_id} if p.installer_id else set())
    names = {str(d["_id"]): d["name"] for d in await db.users.find({"_id": {"$in": [ObjectId(i) for i in uids]}}, {"name": 1}).to_list(100)}
    for d in out:
        d["assigned_installer_name"] = names.get(d.get("assigned_installer_id") or "")
    pd["installer_name"] = names.get(p.installer_id or "")
    pd["installer_names"] = sorted({names[n.assigned_installer_id] for n in ncs if n.assigned_installer_id in names})
    if u.role == "installer":
        pd.pop("rate_history", None)
        pd["rates"] = {k: v for k, v in (p.rates or {}).items() if k == u.id}
        pd["schedules"] = {k: v for k, v in (p.schedules or {}).items() if k == u.id}
    elif u.role == "admin":
        pd["rate_rows"] = await rate_rows(pid, p, uids)
    else:  # employees: operational data only, no payment information
        pd.pop("rate_history", None)
        pd.pop("rates", None)
        pd.pop("rate", None)
    if u.role != "installer":
        pd["schedule_rows"] = sorted(({"installer_id": i, "name": names[i], "date": (p.schedules or {}).get(i)}
                                      for i in uids if i in names), key=lambda r: r["name"])
    return {"project": pd, "ncs": out}


async def rate_rows(pid: str, p: Project, uids: set) -> list:
    """One row per installer on the project: pay type, agreed rate, earning state."""
    us = {str(d["_id"]): d for d in await db.users.find({"_id": {"$in": [ObjectId(i) for i in uids]}}, {"name": 1, "pay_type": 1}).to_list(100)}
    earns = {e["installer_id"]: e for e in await db.earnings.find({"project_id": pid}).to_list(100)}
    rows = []
    for iid, d in us.items():
        e = earns.get(iid)
        rows.append({"installer_id": iid, "name": d["name"], "pay_type": d.get("pay_type", "per_job"),
                     "rate": (p.rates or {}).get(iid), "earning": {"status": e["status"], "amount": e["amount"]} if e else None})
    return sorted(rows, key=lambda r: r["name"])


class ScheduleIn(BaseModel):
    date: Optional[str] = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")


@api.put("/projects/{pid}/schedule/{iid}")
async def set_schedule(pid: str, iid: str, body: ScheduleIn, u: User = Depends(staff)):
    """Optional scheduled visit date for one installer on a project (null clears it). Never invented."""
    from datetime import date as _d
    p = await live_project(pid)
    if p.get("installer_id") != iid and not await db.ncs.find_one({"project_id": pid, "assigned_installer_id": iid}, {"_id": 1}):
        raise HTTPException(400, "Installer is not assigned to this project")
    if body.date:
        try:
            _d.fromisoformat(body.date)
        except ValueError:
            raise HTTPException(422, "Invalid date")
        await db.projects.update_one({"_id": p["_id"]}, {"$set": {f"schedules.{iid}": body.date}})
    else:
        await db.projects.update_one({"_id": p["_id"]}, {"$unset": {f"schedules.{iid}": ""}})
    await audit("project.scheduled", u.id, f"project:{pid}", {"installer_id": iid, "date": body.date})
    return {"ok": True, "date": body.date}


class RateIn(BaseModel):
    rate: float = Field(ge=0, le=100000)
    reason: Optional[str] = Field(default=None, max_length=300)


@api.put("/projects/{pid}/rates/{iid}")
async def override_rate(pid: str, iid: str, body: RateIn, u: User = Depends(admin)):
    """Admin override of one installer's agreed per-job rate on a project.
    An earned-but-unpaid amount follows the new rate; paid earnings are never changed."""
    p = await db.projects.find_one({"_id": oid(pid), "deleted": None}, {"rates": 1, "ref": 1, "installer_id": 1})
    if not p:
        raise HTTPException(404, "Project not found")
    inst = await db.users.find_one({"_id": oid(iid), "role": "installer"}, {"pay_type": 1, "name": 1})
    if not inst:
        raise HTTPException(404, "Installer not found")
    if p.get("installer_id") != iid and not await db.ncs.find_one({"project_id": pid, "assigned_installer_id": iid}):
        raise HTTPException(400, "Installer is not assigned to this project")
    if inst.get("pay_type", "per_job") == "daily":
        raise HTTPException(400, "Daily-wage installers are paid outside the app")
    e = await db.earnings.find_one({"project_id": pid, "installer_id": iid})
    if e and e["status"] == "paid":
        raise HTTPException(409, "This job has already been paid; the rate can no longer be changed")
    old = (p.get("rates") or {}).get(iid)
    rate = round(body.rate, 2)
    ts = now_iso()
    await db.projects.update_one({"_id": p["_id"]}, {"$set": {f"rates.{iid}": rate}, "$push": {"rate_history": {
        "installer_id": iid, "old": old, "new": rate, "reason": (body.reason or "").strip() or None, "by": u.id, "at": ts}}})
    if e:
        await db.earnings.update_one({"_id": e["_id"]}, {"$set": {"amount": rate, "overridden": True},
                                                         "$push": {"amount_history": {"old": e["amount"], "new": rate, "by": u.id, "at": ts}}})
    await audit("project.rate_overridden", u.id, f"project:{pid}", {"installer_id": iid, "old": old, "new": rate, "reason": body.reason, "earning_updated": bool(e)})
    await notify(iid, "rate_changed", pid, f"Your agreed rate is now £{rate:g}")
    return {"ok": True, "rate": rate, "earning_updated": bool(e)}


@api.get("/projects/{pid}/report-link")
async def report_link(pid: str, u: User = Depends(staff)):
    p = await db.projects.find_one({"_id": oid(pid), "deleted": None}, {"source_report": 1})
    if not p or not p.get("source_report"):
        raise HTTPException(404, "No source report")
    return {"url": f"/api/reports/{pid}?{signed_query(f'report:{pid}', 10, stable=False)}"}


@api.get("/reports/{pid}")
async def serve_report(pid: str, exp: int, sig: str):
    if not verify_signed(f"report:{pid}", exp, sig):
        raise HTTPException(403, "Link expired or invalid")
    p = await db.projects.find_one({"_id": oid(pid)}, {"source_report": 1, "deleted": 1})
    if not p or p.get("deleted") or not p.get("source_report"):
        raise HTTPException(404, "Report not found")
    data, _ = await storage.aget(p["source_report"]["path"])
    fname = re.sub(r'[^A-Za-z0-9._ -]', "_", p["source_report"].get("filename") or "report.pdf")
    return Response(data, media_type="application/pdf", headers={"Content-Disposition": f'attachment; filename="{fname}"'})


@api.get("/projects/{pid}/audit")
async def project_audit(pid: str, u: User = Depends(staff)):
    nc_ids = [f"nc:{str(d['_id'])}" for d in await db.ncs.find({"project_id": pid}, {"_id": 1}).to_list(500)]
    docs = await db.audit.find({"target": {"$in": [f"project:{pid}"] + nc_ids}}, {"_id": 0}).sort("seq", -1).to_list(200)
    return docs


# ---------------- evidence ----------------
async def _process_file(fid: str, data: bytes, meta: Optional[dict]):
    f = EvidenceFile.from_mongo(await db.files.find_one({"_id": ObjectId(fid)}))
    try:
        if f.kind in ("photo", "trustmark"):
            outs = await run_in_threadpool(process_photo, data, meta)
        else:
            frames = await run_in_threadpool(video_frames, data, f.duration_s, 1)
            outs = await run_in_threadpool(process_photo, frames[0], None) if frames else {}
            outs.pop("display", None)
        paths = dict(f.paths)
        names = list(outs)
        # thumbnail/display/stamped copies upload in parallel instead of one Dropbox round-trip after another
        stored = await asyncio.gather(*[storage.aput(evidence_path(f.project_id, f.nc_id, f.submission_id or "reference", fid, v, "jpg"),
                                                     outs[v], "image/jpeg") for v in names])
        paths.update(dict(zip(names, stored)))
        await db.files.update_one({"_id": ObjectId(fid)}, {"$set": {f"paths.{k}": v for k, v in paths.items()} | {"processing": "ready"}})
    except Exception as e:
        log.exception("processing failed %s", fid)
        await db.files.update_one({"_id": ObjectId(fid)}, {"$set": {"processing": "failed", "processing_error": str(e)[:200]}})
    if f.kind == "video":
        await _make_playback(fid, data)


async def _make_playback(fid: str, data: bytes):
    """Builds the universal H.264 playback copy. Original stays untouched (export + audit use the original)."""
    f = EvidenceFile.from_mongo(await db.files.find_one({"_id": ObjectId(fid)}))
    async with _transcode_lock:
        try:
            with tempfile.TemporaryDirectory() as td:
                src, dst = os.path.join(td, "src"), os.path.join(td, "play.mp4")
                with open(src, "wb") as fh:
                    fh.write(data)
                info = await run_in_threadpool(make_playback, src, dst)
                with open(dst, "rb") as fh:
                    out = fh.read()
            path = await storage.aput(evidence_path(f.project_id, f.nc_id, f.submission_id or "reference", fid, "playback", "mp4"), out, "video/mp4")
            upd = {"paths.playback": path, "playback": "ready", "video_codec": info["video"], "playback_mode": info["mode"]}
            if not f.duration_s and info.get("duration"):
                upd["duration_s"] = round(info["duration"], 1)
            await db.files.update_one({"_id": ObjectId(fid)}, {"$set": upd})
        except Exception as e:
            log.warning("playback copy failed %s: %s", fid, e)
            await db.files.update_one({"_id": ObjectId(fid)}, {"$set": {"playback": "failed", "playback_error": str(e)[:200]}})


async def backfill_playback():
    """One-off: videos uploaded before playback copies existed get one (originals untouched)."""
    async for d in db.files.find({"kind": "video", "playback": None, "status": {"$ne": "discarded"}}, {"paths": 1}):
        try:
            await db.files.update_one({"_id": d["_id"]}, {"$set": {"playback": "pending"}})
            data, _ = await storage.aget(d["paths"]["original"])
            await _make_playback(str(d["_id"]), data)
        except Exception:
            log.exception("playback backfill failed %s", d["_id"])


def _sniff(b: bytes) -> Optional[str]:
    if b[:3] == b"\xff\xd8\xff":
        return "jpg"
    if b[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if b[:4] == b"RIFF" and b[8:12] == b"WEBP":
        return "webp"
    if b[4:8] == b"ftyp":
        brand = b[8:12]
        if brand in (b"heic", b"heix", b"mif1", b"msf1", b"heis", b"hevc"):
            return "heic"
        return "mov" if brand == b"qt  " else "mp4"
    return None


async def property_folder(pid: str) -> str:
    """Dropbox property folder name, fixed on first use and stored on the project, so later address
    corrections never split or move a property's files. Mongo keeps every file's project/NC association."""
    d = await db.projects.find_one({"_id": oid(pid)}, {"address": 1, "postcode": 1, "dropbox_folder": 1})
    if d.get("dropbox_folder"):
        return d["dropbox_folder"]
    name = property_folder_name(d.get("address", ""), d.get("postcode", ""))
    await db.projects.update_one({"_id": d["_id"], "dropbox_folder": None}, {"$set": {"dropbox_folder": name}})
    return (await db.projects.find_one({"_id": d["_id"]}, {"dropbox_folder": 1}))["dropbox_folder"]


async def store_upload(nc: NC, p: Project, u: User, file: UploadFile, meta: dict, kind: str, source: str,
                       submission_id: Optional[str], bg: BackgroundTasks) -> EvidenceFile:
    data = await file.read()
    if not data:
        raise HTTPException(400, "Empty file")
    limit = MAX_VIDEO_MB * 1024 * 1024 if kind == "video" else MAX_UPLOAD
    if len(data) > limit:
        raise HTTPException(413, f"File too large (maximum {MAX_VIDEO_MB} MB per video)" if kind == "video" else "File too large")
    expected = meta.get("size_bytes")
    if isinstance(expected, (int, float)) and expected > 0 and int(expected) != len(data):
        # interrupted transfer: never store/mark a partial file as uploaded; the device keeps it and retries
        raise HTTPException(422, "Upload incomplete - will retry")
    # validate the real file type from its bytes (client content-type/filename are not trusted)
    sniff = _sniff(data)
    allowed = {"video": {"mp4", "mov"}, "photo": {"jpg", "png", "heic", "webp"}, "trustmark": {"jpg", "png", "heic", "webp"}}.get(kind, {"jpg", "png"})
    if sniff not in allowed:
        raise HTTPException(415, "Unsupported file type")
    fid = ObjectId()
    mime = {"jpg": "image/jpeg", "png": "image/png", "heic": "image/heic", "webp": "image/webp", "mp4": "video/mp4", "mov": "video/quicktime"}[sniff]
    ext = sniff  # server-chosen extension: no user-controlled path parts
    sha = hashlib.sha256(data).hexdigest()
    if kind in ("photo", "video"):  # originals: the property's Media folder (shared by projects at the same address)
        dest = media_path(await property_folder(p.id), p.ref, nc.number, kind, str(fid), ext, now_iso())
    else:  # TrustMark reference images are app-generated copies of the report
        dest = evidence_path(p.id, nc.id, submission_id or "reference", str(fid), "original", ext)
    path = await storage.aput(dest, data, mime)
    lat, lng, acc = meta.get("lat"), meta.get("lng"), meta.get("accuracy_m")
    loc_ok = source == "in_app_camera" and lat is not None and lng is not None and acc is not None and float(acc) <= 50
    f = EvidenceFile(id=str(fid), project_id=p.id, nc_id=nc.id, submission_id=submission_id, kind=kind, source=source,
                     paths={"original": path}, sha256=sha, size=len(data), mime=mime, original_name=file.filename or "",
                     captured_at=meta.get("captured_at"), tz=meta.get("tz"), tz_offset_min=meta.get("tz_offset_min"),
                     lat=lat, lng=lng, accuracy_m=acc, location_verified=loc_ok, duration_s=meta.get("duration_s"),
                     uploaded_by=u.id, client_id=meta.get("client_id"), playback="pending" if kind == "video" else None,
                     status="submitted" if kind == "trustmark" else "draft")
    await db.files.insert_one(f.to_mongo())
    stamp_meta = None
    if kind == "photo":
        stamp_meta = {**meta, "location_verified": loc_ok, "project_ref": p.ref, "postcode": p.postcode,
                      "nc_number": nc.number, "source": source}
    bg.add_task(_process_file, str(fid), data, stamp_meta)
    await audit("evidence.uploaded", u.id, f"nc:{nc.id}",
                {"file_id": str(fid), "kind": kind, "source": source, "sha256": sha, "size": len(data),
                 "location_verified": loc_ok, "captured_at": f.captured_at, "server_received_at": f.server_received_at})
    return f


@api.post("/ncs/{nc_id}/evidence")
async def upload_evidence(nc_id: str, bg: BackgroundTasks, file: UploadFile = File(...), meta: str = Form("{}"),
                          u: User = Depends(current_user)):
    nc, p = await get_nc(nc_id, u)
    m = json.loads(meta or "{}")
    if m.get("client_id"):
        m["client_id"] = str(m["client_id"])[:64]
        dup = await db.files.find_one({"client_id": m["client_id"], "nc_id": nc_id})
        if dup:  # idempotent retry from the device queue
            return file_public(EvidenceFile.from_mongo(dup))
    if nc.status in DONE:
        raise HTTPException(409, "This NC is already approved")
    kind = "video" if m.get("kind") == "video" else "photo"
    source = "employee_upload" if u.role != "installer" else ("gallery" if m.get("source") == "gallery" else "in_app_camera")
    s = await get_settings()
    has_loc = m.get("lat") is not None and m.get("accuracy_m") is not None and float(m["accuracy_m"]) <= 50
    if source == "in_app_camera" and kind == "photo" and not has_loc and s.location_policy == "reject":
        raise HTTPException(422, "Location is required by your company's evidence policy. Enable GPS and retake.")
    sub_id = nc.draft_submission_id
    if not sub_id:
        r = await db.submissions.insert_one(Submission(project_id=p.id, nc_id=nc.id, installer_id=u.id).to_mongo())
        sub_id = str(r.inserted_id)
        await db.ncs.update_one({"_id": oid(nc_id)}, {"$set": {"draft_submission_id": sub_id}})
    f = await store_upload(nc, p, u, file, m, kind, source, sub_id, bg)
    if kind == "video":  # multiple videos per NC: a new video never replaces or hides an earlier one
        await db.ncs.update_one({"_id": oid(nc_id)}, {"$set": {"active_video_id": f.id}})
    elif m.get("stage") in STAGES:
        await db.files.update_one({"_id": oid(f.id)}, {"$set": {"stage": m["stage"], "stage_source": "staff" if m.get("source") == "employee_upload" else "installer"}})
        f.stage = m["stage"]
    return file_public(f)


STAGES = ("pre", "mid", "final")
SAFETY_RE = re.compile(r"\b(gas|electric\w*|wiring|fuse|consumer unit|fire|flue|combustion|carbon monoxide|CO alarm|boiler|structural|lintel|scaffold)\b", re.I)
STAGE_NAME = {"pre": "pre-remediation", "mid": "mid-remediation", "final": "final-remediation"}


async def project_requires_video(pid: str) -> bool:
    p = await db.projects.find_one({"_id": oid(pid)}, {"require_video": 1})
    return (p or {}).get("require_video", True) is not False  # older projects: ON


async def missing_mandatory(nc_id: str, require_video: bool = True) -> List[str]:
    """Mandatory evidence for new NCs: at least one photo, plus a video when the project requires one."""
    have = await db.files.find({"nc_id": nc_id, "status": {"$in": ["draft", "submitted"]}, "kind": {"$in": ["photo", "video"]}},
                               {"kind": 1, "stage": 1}).to_list(5000)
    # photos are arranged by AI at PDF time; only the remediation video is mandatory (plus at least one photo)
    miss = [] if any(f["kind"] == "photo" for f in have) else ["at least one photo"]
    if require_video and not any(f["kind"] == "video" for f in have):
        miss.append("a video of this NC's remediation")
    return miss


class SafetyIn(BaseModel):
    safety_critical: bool


@api.put("/ncs/{nc_id}/safety")
async def set_safety(nc_id: str, body: SafetyIn, u: User = Depends(staff)):
    """Staff flag; safety-critical NCs always need human sign-off (enforced in run_assessment)."""
    nc, _ = await get_nc(nc_id, u)
    if nc.safety_critical != body.safety_critical:
        await db.ncs.update_one({"_id": oid(nc_id)}, {"$set": {"safety_critical": body.safety_critical, "updated_at": now_iso()}})
        await audit("nc.safety_critical", u.id, f"nc:{nc_id}", {"from": nc.safety_critical, "to": body.safety_critical})
    return {"ok": True, "safety_critical": body.safety_critical}


class ExportNoteIn(BaseModel):
    note: str = Field(default="", max_length=600)


@api.put("/ncs/{nc_id}/export-note")
async def set_export_note(nc_id: str, body: ExportNoteIn, u: User = Depends(staff)):
    await get_nc(nc_id, u)
    note = body.note.strip()
    await db.ncs.update_one({"_id": oid(nc_id)}, {"$set": {"export_note": note, "export_note_source": "staff", "updated_at": now_iso()}})
    await audit("nc.export_note", u.id, f"nc:{nc_id}", {"length": len(note)})
    return {"ok": True, "export_note": note}


@api.post("/ncs/{nc_id}/trustmark")
async def upload_trustmark(nc_id: str, bg: BackgroundTasks, file: UploadFile = File(...), u: User = Depends(staff)):
    nc, p = await get_nc(nc_id, u)
    f = await store_upload(nc, p, u, file, {}, "trustmark", "employee_upload", None, bg)
    await db.ncs.update_one({"_id": oid(nc_id)}, {"$push": {"trustmark_ids": f.id}})
    return file_public(f)


@api.delete("/evidence/{fid}")
async def discard_evidence(fid: str, u: User = Depends(current_user)):
    f = EvidenceFile.from_mongo(await db.files.find_one({"_id": oid(fid)}))
    if not f or f.status != "draft":
        raise HTTPException(400, "Only unsubmitted drafts can be removed")
    await get_nc(f.nc_id, u)  # NC-level: installers may only touch drafts on their own NC
    if u.role == "installer" and f.uploaded_by != u.id:
        raise HTTPException(403, "You can only remove your own drafts")
    # Soft-remove from the submission; the original object and audit record are retained.
    await db.files.update_one({"_id": oid(fid)}, {"$set": {"status": "discarded"}})
    if f.kind == "video":
        latest = await db.files.find_one({"nc_id": f.nc_id, "kind": "video", "status": {"$in": ["draft", "submitted", "superseded"]}},
                                         {"_id": 1}, sort=[("server_received_at", -1)])
        await db.ncs.update_one({"_id": oid(f.nc_id), "active_video_id": fid}, {"$set": {"active_video_id": str(latest["_id"]) if latest else None}})
    await audit("evidence.discarded", u.id, f"nc:{f.nc_id}", {"file_id": fid})
    return {"ok": True}


async def run_assessment(nc_id: str, sub_id: str):
    try:
        for _ in range(60):  # wait for async image processing
            if not await db.files.find_one({"submission_id": sub_id, "processing": "pending"}):
                break
            await asyncio.sleep(1)
        nc = NC.from_mongo(await db.ncs.find_one({"_id": ObjectId(nc_id)}))
        s = await get_settings()
        sub = Submission.from_mongo(await db.submissions.find_one({"_id": ObjectId(sub_id)}))
        photos = [EvidenceFile.from_mongo(d) for d in await db.files.find({"_id": {"$in": [ObjectId(i) for i in sub.photo_ids]}}).to_list(100)]
        trust = [EvidenceFile.from_mongo(d) for d in await db.files.find({"nc_id": nc_id, "kind": "trustmark", "excluded": {"$ne": True}}).to_list(10)]

        async def img(f, variant="display"):
            path = f.paths.get(variant) or f.paths.get("original")
            return (await storage.aget(path))[0]

        refs = [await img(t) for t in trust[:3]]
        staged = [EvidenceFile.from_mongo(d) for d in await db.files.find(
            {"nc_id": nc_id, "kind": "photo", "status": {"$in": ["draft", "submitted"]}, "stage": {"$in": list(STAGES)}}).sort("server_received_at", -1).to_list(500)]
        chosen = [f for st in STAGES for f in [x for x in staged if x.stage == st][:3]] if staged else photos[:6]
        labels = [STAGE_NAME.get(f.stage, "remediation").upper() + " photo" for f in chosen]
        evid = [await img(f) for f in chosen]
        video = None
        if sub.video_id:
            video = EvidenceFile.from_mongo(await db.files.find_one({"_id": ObjectId(sub.video_id)}))
            vbytes = (await storage.aget(video.paths["original"]))[0]
            frames = await run_in_threadpool(video_frames, vbytes, video.duration_s, 4)
            for i, fr in enumerate(frames):
                p = await storage.aput(evidence_path(nc.project_id, nc_id, sub_id, video.id, f"frame{i}", "jpg"), fr, "image/jpeg")
                await db.files.update_one({"_id": ObjectId(video.id)}, {"$set": {f"paths.frame{i}": p}})
            evid += frames
            labels += ["VIDEO FRAME"] * len(frames)
        reasons: List[str] = []
        video_req = nc.requires_stages and await project_requires_video(nc.project_id)
        guides = await match_guidance(" ".join([nc.original_text or "", nc.description or "", nc.required_work or ""]))
        doc_refs = await docs_refs(nc_id) if s.staff_review_enabled else None  # approved technical documents (keyword, no AI)
        gimgs = []
        for g in guides:
            for gid in (g.get("example_photo_ids") or [])[:1]:
                gf = await db.files.find_one({"_id": oid(gid), "kind": "guidance"}, {"paths": 1})
                if gf and len(gimgs) < 2:
                    gimgs.append((await storage.aget(gf["paths"].get("display") or gf["paths"]["original"]))[0])
        try:
            res = await get_assessor().assess(AssessmentInput(nc.number, nc.description, nc.required_work,
                                                             nc.safety_critical, refs, evid, nc.original_text, labels,
                                                             nc.installer_note, nc.requires_stages, video_req,
                                                             "\n\n".join(x for x in [guidance_text(guides), docs_text(doc_refs)] if x), gimgs))
        except Exception as e:
            log.exception("AI failed")
            res = None
            reasons.append(f"AI unavailable: {str(e)[:80]}")
        if res is None:  # AI down / rate-limited: never a fake 0% or a "failed" verdict
            ai = AIResult(outcome="manual_review_required", status="unavailable", feedback="AI assessment unavailable. Sent to an employee for review.", concerns=reasons)
        else:
            best = {st: chosen[i].id for st, i in res.best.items() if st in STAGES and isinstance(i, int) and 0 <= i < len(chosen) and chosen[i].stage == st}
            ai = AIResult(outcome=res.outcome, confidence=res.confidence, feedback=res.feedback, concerns=res.concerns,
                          model=res.model, risk=res.risk, method_demonstrated=res.method_demonstrated,
                          requests=res.requests, works_description=res.works_description, best=best)
        ai.video_required = video_req
        ai.guidance = [{"id": g["id"], "version": g.get("version", 1), "title": g.get("title", "")} for g in guides]
        media = photos + ([video] if video else [])
        # Flags are recorded but do not escalate on their own.
        flags = []
        if any(not f.location_verified for f in photos):
            flags.append("Some photos have no verified GPS")
        if any(f.source == "gallery" for f in media):
            flags.append("Includes gallery uploads")
        if not refs:
            flags.append("No TrustMark reference photo")
        cov3 = None
        if s.coverage_check_enabled:  # Phase 3: scope + photo tags -> coverage; never approves on its own
            try:
                ev = await coverage_evaluate(nc_id)
                cov3, sc3 = ev["coverage"], ev["scope"]
            except Exception as e:
                log.warning("coverage check failed: %s", type(e).__name__)
                cov3, sc3 = {"status": "unverified", "reason": "Coverage check unavailable"}, None
        status = {"ai_verified": "approved", "more_evidence_required": "more_evidence"}.get(ai.outcome, "employee_review")
        if res is not None:
            ai.status = {"more_evidence_required": "more_evidence", "potential_noncompliance": "potential_noncompliance"}.get(ai.outcome, "employee_review")
        # deterministic safety net: AI risk labels can vary between runs, so gas/electrical/fire/flue/CO/structural
        # wording in the NC itself always forces human review
        safety = nc.safety_critical or ai.risk == "safety" or bool(SAFETY_RE.search(" ".join([nc.original_text or "", nc.description or "", nc.required_work or ""])))
        if safety:
            # Safety-critical work always needs a competent human decision (incl. AI "more evidence" opinions).
            status = "employee_review"
            if ai.status == "more_evidence":
                ai.status = "employee_review"
            reasons.append("Safety-critical: competent human review required")
            if ai.outcome == "ai_verified":
                ai.status = "ai_verified_signoff"
        if status == "approved":
            if not (s.auto_verify and s.auto_approve_low_risk):
                reasons.append("Automatic approval is switched off in Admin settings")
            if ai.risk not in s.auto_approve_risks:
                reasons.append(f"'{ai.risk}' NCs are not eligible for automatic approval")
            if ai.method_demonstrated is False:
                reasons.append("Corrective method not demonstrated")
            if nc.requires_stages and video_req and await missing_mandatory(nc_id, True):
                reasons.append("Mandatory remediation video missing (required for this project)")
            if (ai.confidence or 0) < s.confidence_threshold:
                reasons.append(f"Confidence {ai.confidence:.2f} below {s.confidence_threshold:.2f}")
            if s.location_policy == "reject" and any(not f.location_verified for f in photos):
                reasons.append("Location not verified")
            if s.gallery_policy == "manual_review" and any(f.source == "gallery" for f in media):
                reasons.append("Gallery uploads need review")
            if cov3 is not None:  # Phase 3 supersedes the Phase 1 gate when enabled
                if cov3["status"] != "complete":
                    reasons.append(f"Location coverage {cov3['status']}: {cov3['reason']}")
                try:  # evidence checklist: can only send the NC to staff review
                    reasons += await checklist_gate(nc_id)
                    reasons += docs_gate(doc_refs)  # conflicting approved guidance -> staff decide
                except Exception as e:
                    log.warning("checklist gate failed: %s", type(e).__name__)
                    reasons.append("Evidence checklist could not be checked")
            elif s.location_scope_enabled:  # Phase 1: multi-location / uncertain scope -> employee review (never rejection)
                sc = await ensure_scope(nc_id)
                cov = scope_coverage(sc, await db.files.find({"nc_id": nc_id}, {"kind": 1, "status": 1, "pdf_include": 1, "location_id": 1}).to_list(5000))
                gr = scope_gate(sc, cov)
                if gr:
                    reasons.append(gr)
            if reasons:
                status = "employee_review"
            elif s.human_signoff_required:
                status, ai.status = "employee_review", "ai_verified_signoff"
                reasons.append("AI technically verified - human sign-off required by policy")
            else:
                ai.status = "ai_approved"
        if cov3 is not None and cov3["status"] == "incomplete" and sc3 and not safety and ai.outcome in ("ai_verified", "more_evidence_required"):
            # confirmed missing locations -> existing "More evidence required" flow (safety / uncertain AI verdicts stay with an employee)
            status = ai.status = "more_evidence"
            ai.requests = list(ai.requests or []) + [coverage_request(sc3, cov3)]
            reasons = [r for r in reasons if not r.startswith("Location coverage")]
        elif cov3 is not None and cov3["status"] != "complete" and status == "employee_review" and not any(r.startswith("Location coverage") for r in reasons):
            reasons.append(f"Location coverage {cov3['status']}: {cov3['reason']}")  # shown to the reviewer
        if status == "employee_review" and res is not None and not reasons and ai.outcome != "ai_verified":
            reasons.append({"manual_review_required": "Technical uncertainty: AI could not confirm the work",
                            "potential_noncompliance": "AI found possible non-compliance"}.get(ai.outcome, "AI assessment incomplete"))
        ai.blocked = [r for r in reasons if r != "Location not verified"] if status == "employee_review" else []
        if status == "more_evidence" and ai.requests:
            ai.feedback = " ".join(ai.requests)[:600]
        ai.concerns = ai.concerns + reasons + flags
        extra = {"approved_by": "ai", "approved_at": now_iso()} if status == "approved" else {}
        cur = await db.ncs.find_one({"_id": ObjectId(nc_id)}, {"export_note_source": 1})
        if ai.works_description and cur.get("export_note_source") != "staff":  # AI draft; a staff edit is never overwritten
            extra.update({"export_note": ai.works_description, "export_note_source": "ai"})
        await db.ncs.update_one({"_id": ObjectId(nc_id)}, {"$set": {"status": status, "ai": ai.model_dump(), "updated_at": now_iso(), **extra}})
        if status == "employee_review":
            await notify_reviewers(nc_id)
        elif status == "approved":
            await nc_notify(await db.ncs.find_one({"_id": ObjectId(nc_id)}), nc.assigned_installer_id, "approved")
        elif status == "more_evidence":  # precise request goes straight to the installer, not to an employee
            await nc_notify(await db.ncs.find_one({"_id": ObjectId(nc_id)}), nc.assigned_installer_id, "action", "more evidence needed: " + ai.feedback[:160])
        if s.photo_classification_enabled and cov3 is None:  # Phase 2: metadata-only tagging in the background; decisions above unaffected
            schedule_classification(nc_id)
        await audit("ai.assessed", None, f"nc:{nc_id}", {"submission_id": sub_id, **ai.model_dump(), "final_status": status})
        await recompute_project(nc.project_id)
    except Exception:
        log.exception("assessment crashed")
        await db.ncs.update_one({"_id": ObjectId(nc_id)}, {"$set": {"status": "employee_review",
                                "ai": AIResult(outcome="manual_review_required", status="unavailable", feedback="AI assessment unavailable. Sent to an employee for review.").model_dump()}})
        await notify_reviewers(nc_id)


class SubmitIn(BaseModel):
    client_id: Optional[str] = Field(default=None, max_length=64)  # device idempotency key
    note: Optional[str] = Field(default=None, max_length=500)  # installer's short description of the work
    expect: List[str] = Field(default=[], max_length=200)  # device client ids that must have arrived first


@api.post("/ncs/{nc_id}/submit")
async def submit(nc_id: str, bg: BackgroundTasks, body: Optional[SubmitIn] = None, u: User = Depends(current_user)):
    body = body or SubmitIn()
    nc, p = await get_nc(nc_id, u)
    if body.client_id:  # retried submit whose first response was lost: never re-run, never duplicate
        prev = await db.submissions.find_one({"nc_id": nc_id, "submit_client_id": body.client_id}, {"_id": 1})
        if prev:
            return {"ok": True, "submission_id": str(prev["_id"]), "status": nc.status, "duplicate": True}
    if body.expect:  # AI assessment must not start until every piece of evidence has uploaded
        have = await db.files.distinct("client_id", {"nc_id": nc_id, "client_id": {"$in": [str(x)[:64] for x in body.expect]},
                                                     "status": {"$in": ["draft", "submitted", "superseded"]}})
        if len(set(have)) < len(set(body.expect)):
            raise HTTPException(409, "Some evidence has not finished uploading")
    if not nc.draft_submission_id:
        raw = await db.ncs.find_one({"_id": oid(nc_id)}, {"last_submit_client_id": 1})
        if body.client_id and raw and raw.get("last_submit_client_id") == body.client_id:
            return {"ok": True, "status": nc.status, "duplicate": True}  # same device request already claimed this draft
        raise HTTPException(400, "No new evidence to submit")
    drafts = await db.files.find({"submission_id": nc.draft_submission_id, "status": "draft"}).to_list(5000)
    photo_ids = [str(d["_id"]) for d in drafts if d["kind"] == "photo"]
    video_ids = [str(d["_id"]) for d in drafts if d["kind"] == "video"]
    if not photo_ids and not video_ids and not nc.active_video_id:
        raise HTTPException(400, "Add at least one photo or video")
    raw_nc = await db.ncs.find_one({"_id": oid(nc_id)}, {"requires_stages": 1})
    if raw_nc.get("requires_stages"):
        miss = await missing_mandatory(nc_id, p.require_video is not False)
        if miss:
            raise HTTPException(400, "Before submitting, please add " + ", ".join(miss) + ".")
    if body.note is not None and body.note.strip():
        await db.ncs.update_one({"_id": oid(nc_id)}, {"$set": {"installer_note": body.note.strip()}})
    sid = nc.draft_submission_id
    # atomic claim: two concurrent submits for the same draft cannot both start an assessment
    claimed = await db.ncs.update_one({"_id": oid(nc_id), "draft_submission_id": sid},
                                      {"$set": {"status": "assessing", "draft_submission_id": None, "updated_at": now_iso(),
                                               "last_submit_client_id": body.client_id}})
    if not claimed.modified_count:
        return {"ok": True, "submission_id": sid, "status": "assessing", "duplicate": True}
    await db.files.update_many({"submission_id": sid, "status": "draft"}, {"$set": {"status": "submitted"}})
    await db.submissions.update_one({"_id": oid(sid)}, {"$set": {"status": "submitted", "photo_ids": photo_ids, "submit_client_id": body.client_id,
                                    "video_id": video_ids[-1] if video_ids else nc.active_video_id, "video_ids": video_ids, "submitted_at": now_iso()}})
    await recompute_project(p.id)
    await audit("evidence.submitted", u.id, f"nc:{nc_id}", {"submission_id": sid, "photos": len(photo_ids), "video": nc.active_video_id})
    bg.add_task(run_assessment, nc_id, sid)
    return {"ok": True, "submission_id": sid, "status": "assessing"}


class ReviewIn(BaseModel):
    action: str = Field(pattern="^(approve|reject|more_evidence|escalate|return|trustmark_reject)$")
    note: str = ""
    reason: str = Field(default="", max_length=1000)  # internal: why the decision was made (optional)
    suggest_guidance: bool = False  # create a DRAFT AI guidance entry for admin review


@api.post("/ncs/{nc_id}/review")
async def review(nc_id: str, body: ReviewIn, u: User = Depends(staff)):
    nc, p = await get_nc(nc_id, u)
    if body.action == "escalate" and u.role != "employee":
        raise HTTPException(403, "Only employees can escalate to Admin")
    if body.action == "return" and (u.role != "admin" or nc.status != "admin_review"):
        raise HTTPException(403, "Only Admin can return an escalated NC")
    if nc.status == "admin_review" and u.role != "admin":
        raise HTTPException(403, "This NC is awaiting Admin review")
    if body.action == "trustmark_reject" and nc.status != "approved":
        raise HTTPException(400, "Only internally approved NCs can be rejected by TrustMark")
    status = {"approve": "approved", "reject": "rejected", "more_evidence": "more_evidence",
              "escalate": "admin_review", "return": "employee_review", "trustmark_reject": "rejected"}[body.action]
    st = {"status": status, "review_note": body.note, "reviewed_by": u.id, "updated_at": now_iso()}
    if status == "approved":
        st.update(approved_by=u.id, approved_at=now_iso())
    elif status == "rejected":
        st["rejection_source"] = "trustmark" if body.action == "trustmark_reject" else u.role
    upd = {"$set": st}
    if body.action == "trustmark_reject":  # reopen: evidence and earlier approval kept in history
        upd["$push"] = {"reopen_history": {"at": now_iso(), "by": u.id, "source": "trustmark", "note": body.note,
                                           "previous_approved_at": nc.approved_at, "previous_approved_by": nc.approved_by}}
    await db.ncs.update_one({"_id": oid(nc_id)}, upd)
    raw = await db.ncs.find_one({"_id": oid(nc_id)})
    if status == "rejected":
        src = "TrustMark" if body.action == "trustmark_reject" else u.role.capitalize()
        await nc_notify(raw, nc.assigned_installer_id, "action", f"rejected by {src}" + (f": {body.note}" if body.note else ""))
    elif status == "more_evidence":
        await nc_notify(raw, nc.assigned_installer_id, "action", "more evidence needed" + (f": {body.note}" if body.note else ""))
    elif status == "approved":
        await nc_notify(raw, nc.assigned_installer_id, "approved")
    elif status == "admin_review":
        for a in await db.users.find({"role": "admin", "active": True}, {"_id": 1}).to_list(50):
            await nc_notify(raw, str(a["_id"]), "escalated", f"escalated by {u.name}" + (f": {body.note}" if body.note else ""))
    await recompute_project(p.id)
    await audit("nc.reviewed", u.id, f"nc:{nc_id}", {"action": body.action, "note": body.note, "reason": body.reason, "role": u.role})
    gid = await suggest_from_review(raw, body.action, body.reason or body.note, u) if body.suggest_guidance else None
    return {"ok": True, "status": status, "guidance_draft_id": gid}


class AssignIn(BaseModel):
    installer_id: Optional[str] = None


@api.post("/ncs/{nc_id}/assign")
async def assign_nc(nc_id: str, body: AssignIn, u: User = Depends(staff)):
    nc, p = await get_nc(nc_id, u)
    to = body.installer_id or None
    if to and not await db.users.find_one({"_id": oid(to), "role": "installer", "active": True}):
        raise HTTPException(400, "Installer not found")
    if to == nc.assigned_installer_id:
        return {"ok": True}
    await db.ncs.update_one({"_id": oid(nc_id)}, {"$set": {"assigned_installer_id": to, "updated_at": now_iso()},
                            "$push": {"assignment_history": {"from": nc.assigned_installer_id, "to": to, "by": u.id, "at": now_iso()}}})
    if to:
        await agreed_rate(p.id, to)
        await nc_notify(await db.ncs.find_one({"_id": oid(nc_id)}), to, "assigned")
    await recompute_project(p.id)
    await audit("nc.reassigned", u.id, f"nc:{nc_id}", {"from": nc.assigned_installer_id, "to": to})
    return {"ok": True}


# ---------------- notifications ----------------
@api.get("/notifications")
async def notifications(u: User = Depends(current_user)):
    items = clean(await db.notifications.find({"user_id": u.id, "hidden": {"$ne": True}}).sort("created_at", -1).to_list(100))
    return {"items": items, "unread": sum(1 for i in items if not i["read"])}


@api.post("/notifications/{nid}/read")
async def read_notification(nid: str, u: User = Depends(current_user)):
    await db.notifications.update_one({"_id": oid(nid), "user_id": u.id}, {"$set": {"read": True, "read_at": now_iso()}})
    return {"ok": True}


@api.post("/notifications/read-all")
async def read_all(u: User = Depends(current_user)):
    await db.notifications.update_many({"user_id": u.id, "read": False}, {"$set": {"read": True, "read_at": now_iso()}})
    return {"ok": True}


def _review_statuses(u: User) -> List[str]:
    return ["admin_review"] if u.role == "admin" else ["employee_review"]


@api.get("/review-groups")
async def review_groups(search: str = "", u: User = Depends(staff)):
    """Pending reviews grouped by project (admin: Admin Review queue; employee: Employee Review)."""
    pipe = [{"$match": {"status": {"$in": _review_statuses(u)}, "project_deleted": {"$ne": True}}},
            {"$group": {"_id": "$project_id", "pending": {"$sum": 1}, "safety": {"$max": "$safety_critical"}, "oldest": {"$min": "$updated_at"}}}]
    groups = await db.ncs.aggregate(pipe).to_list(2000)
    projs = {str(d["_id"]): d for d in await db.projects.find({"_id": {"$in": [ObjectId(g["_id"]) for g in groups]}}, {"ref": 1, "address": 1, "postcode": 1}).to_list(2000)}
    s = search.lower().strip()
    out = []
    for g in groups:
        p = projs.get(g["_id"])
        if not p or (s and s not in f"{p['ref']} {p['address']} {p.get('postcode', '')}".lower()):
            continue
        out.append({"project_id": g["_id"], "ref": p["ref"], "address": p["address"], "postcode": p.get("postcode"),
                    "pending": g["pending"], "safety": bool(g["safety"]), "oldest": g["oldest"]})
    return sorted(out, key=lambda x: x["oldest"] or "")


@api.get("/review-groups/{pid}")
async def review_group(pid: str, u: User = Depends(staff)):
    ids = [str(d["_id"]) for d in await db.ncs.find({"project_id": pid, "status": {"$in": _review_statuses(u)}, "project_deleted": {"$ne": True}}, {"_id": 1}).sort("number", 1).to_list(500)]
    return {"nc_ids": ids}


@api.get("/review-queue")
async def review_queue(u: User = Depends(staff)):
    ncs = await db.ncs.find({"status": {"$in": ["employee_review", "admin_review", "assessing"]}, "project_deleted": {"$ne": True}}).sort("updated_at", 1).to_list(200)
    pids = list({n["project_id"] for n in ncs})
    projs = {str(d["_id"]): d for d in await db.projects.find({"_id": {"$in": [ObjectId(i) for i in pids]}}).to_list(500)}
    return [{**NC.from_mongo(n).model_dump(), "project_ref": projs.get(n["project_id"], {}).get("ref"),
             "address": projs.get(n["project_id"], {}).get("address")} for n in ncs]


# ---------------- earnings ----------------
@api.get("/earnings")
async def earnings(u: User = Depends(current_user)):
    from datetime import datetime, timedelta
    if u.role == "employee":
        raise HTTPException(403, "Payment information is restricted to Admins")
    q = {} if u.role == "admin" else {"installer_id": u.id}
    items = clean(await db.earnings.find(q).sort("earned_at", -1).to_list(2000))
    s = await get_settings()
    me = await db.users.find_one({"_id": oid(u.id)}, {"pay_type": 1, "default_rate": 1})
    pending = []
    if u.role == "installer":
        paid_pids = {i["project_id"] for i in items}
        mine: dict = {}
        async for n in db.ncs.find({"assigned_installer_id": u.id, "project_deleted": {"$ne": True}}, {"project_id": 1, "number": 1, "status": 1}):
            mine.setdefault(n["project_id"], []).append(n)
        for pid, ns in mine.items():
            if pid in paid_pids:
                continue
            p = await db.projects.find_one({"_id": oid(pid), "deleted": None}, {"ref": 1, "address": 1, "rates": 1})
            if p:  # estimate only: becomes earned when all of THIS installer's NCs here are internally approved
                pending.append({"project_id": pid, "ref": p["ref"], "address": p["address"], "status": "potential",
                                "ncs": sorted(n["number"] for n in ns), "ncs_done": sum(1 for n in ns if n["status"] in DONE),
                                "amount": (p.get("rates") or {}).get(u.id, (me or {}).get("default_rate") or s.default_job_rate)})
        for i in items:  # display only: this installer's own NC numbers on each earned/paid job
            i["ncs"] = sorted(n["number"] for n in mine.get(i["project_id"], []))
    names = {}
    if u.role == "admin":
        names = {str(d["_id"]): d["name"] for d in await db.users.find({"role": "installer"}, {"name": 1}).to_list(1000)}
    weeks: dict = {}
    for i in items:
        i["installer_name"] = names.get(i["installer_id"])
        d = datetime.fromisoformat(i["earned_at"]).date()
        wk = (d - timedelta(days=d.weekday())).isoformat()
        i["week"] = wk
        w = weeks.setdefault(wk, {"week": wk, "earned": 0, "paid": 0, "jobs": 0})
        w["jobs"] += 1
        w["earned" if i["status"] == "earned" else "paid"] += i["amount"]
    daily = (me or {}).get("pay_type") == "daily"
    return {"currency": s.currency, "payment_terms": s.payment_terms, "items": items, "pending": [] if daily else pending,
            "weeks": sorted(weeks.values(), key=lambda w: w["week"], reverse=True),
            "pay_type": (me or {}).get("pay_type", "per_job"), "default_rate": (me or {}).get("default_rate"),
            "earned": sum(i["amount"] for i in items if i["status"] == "earned"),
            "paid": sum(i["amount"] for i in items if i["status"] == "paid"),
            "in_progress": 0 if daily else sum(p["amount"] for p in pending), "jobs_in_progress": 0 if daily else len(pending)}


@api.get("/admin/payments")
async def payments(u: User = Depends(admin)):
    """Earnings grouped by installer and week (Monday)."""
    from datetime import datetime, timedelta
    names = {str(d["_id"]): d["name"] for d in await db.users.find({"role": "installer"}, {"name": 1}).to_list(1000)}
    groups: dict = {}
    for e in await db.earnings.find({}).sort("earned_at", -1).to_list(5000):
        d = datetime.fromisoformat(e["earned_at"]).date()
        wk = (d - timedelta(days=d.weekday())).isoformat()
        g = groups.setdefault((e["installer_id"], wk), {"installer_id": e["installer_id"], "installer_name": names.get(e["installer_id"], "?"),
                                                          "week": wk, "unpaid": 0, "paid": 0, "jobs": []})
        g["unpaid" if e["status"] == "earned" else "paid"] += e["amount"]
        g["jobs"].append({"ref": e["ref"], "amount": e["amount"], "status": e["status"], "overridden": bool(e.get("overridden"))})
    return sorted(groups.values(), key=lambda g: (g["unpaid"] == 0, g["week"]), reverse=False)


class PayWeekIn(BaseModel):
    installer_id: str
    week: str


@api.post("/admin/payments/mark-paid")
async def pay_week(body: PayWeekIn, u: User = Depends(admin)):
    from datetime import date, timedelta
    start = date.fromisoformat(body.week)
    end = (start + timedelta(days=7)).isoformat()
    q = {"installer_id": body.installer_id, "status": "earned", "earned_at": {"$gte": start.isoformat(), "$lt": end}}
    ids = [d["_id"] for d in await db.earnings.find(q, {"_id": 1}).to_list(1000)]
    await db.earnings.update_many({"_id": {"$in": ids}}, {"$set": {"status": "paid", "paid_at": now_iso(), "paid_by": u.id}})
    await audit("earning.week_paid", u.id, f"user:{body.installer_id}", {"week": body.week, "count": len(ids)})
    return {"ok": True, "count": len(ids)}


@api.post("/earnings/{eid}/paid")
async def mark_paid(eid: str, u: User = Depends(admin)):
    await db.earnings.update_one({"_id": oid(eid)}, {"$set": {"status": "paid", "paid_at": now_iso()}})
    await audit("earning.paid", u.id, f"earning:{eid}")
    return {"ok": True}


# ---------------- files (signed, temporary URLs) ----------------
async def _local_copy(fid: str, variant: str, spath: str) -> Path:
    """Disk cache of a stored video so range requests don't re-download the whole object each time."""
    MEDIA_CACHE.mkdir(parents=True, exist_ok=True)
    p = MEDIA_CACHE / f"{fid}_{variant}"
    if not p.exists():
        data, _ = await storage.aget(spath)
        tmp = MEDIA_CACHE / f"{fid}_{variant}.{secrets.token_hex(4)}.part"
        tmp.write_bytes(data)
        tmp.replace(p)
        files = sorted((x for x in MEDIA_CACHE.iterdir() if not x.name.endswith(".part")), key=lambda x: x.stat().st_mtime)
        total = sum(x.stat().st_size for x in files)
        while files and total > 3 * 1024 ** 3:  # keep the cache under ~3 GB
            old = files.pop(0)
            total -= old.stat().st_size
            if old != p:
                old.unlink(missing_ok=True)
    return p


def _range_response(path: Path, ct: str, range_header: Optional[str]):
    """HTTP byte ranges (206) - required by iOS AVPlayer and used by Android/web players for seeking/streaming."""
    size = path.stat().st_size
    start, end, status = 0, size - 1, 200
    if range_header:
        m = re.match(r"bytes=(\d*)-(\d*)$", range_header.strip())
        if not m or (not m.group(1) and not m.group(2)):
            return Response(status_code=416, headers={"Content-Range": f"bytes */{size}"})
        if m.group(1):
            start = int(m.group(1))
            end = min(int(m.group(2)), size - 1) if m.group(2) else size - 1
        else:  # suffix range: last N bytes
            start = max(0, size - int(m.group(2)))
        if start >= size or start > end:
            return Response(status_code=416, headers={"Content-Range": f"bytes */{size}"})
        status = 206
    length = end - start + 1

    def body():
        with open(path, "rb") as fh:
            fh.seek(start)
            left = length
            while left > 0:
                chunk = fh.read(min(1024 * 1024, left))
                if not chunk:
                    break
                left -= len(chunk)
                yield chunk

    headers = {"Accept-Ranges": "bytes", "Content-Length": str(length), "Cache-Control": "private, max-age=3600"}
    if status == 206:
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"
    return StreamingResponse(body(), status_code=status, media_type=ct, headers=headers)


@api.get("/files/{fid}/{variant}")
async def serve_file(fid: str, variant: str, exp: int, sig: str, request: Request):
    if not verify_signed(f"{fid}:{variant}", exp, sig):
        raise HTTPException(403, "Link expired or invalid")
    f = await db.files.find_one({"_id": oid(fid)}, {"paths": 1, "mime": 1, "project_id": 1})
    if not f or variant not in f.get("paths", {}):
        raise HTTPException(404, "Not found")
    if f.get("project_id") and await db.projects.find_one({"_id": oid(f["project_id"]), "deleted": True}, {"_id": 1}):
        raise HTTPException(404, "Not found")  # links stop working as soon as a project is deleted
    if variant == "playback" or (variant == "original" and (f.get("mime") or "").startswith("video/")):
        ct = "video/mp4" if variant == "playback" else f["mime"]
        path = await _local_copy(fid, variant, f["paths"][variant])
        return _range_response(path, ct, request.headers.get("range"))
    data, ct = await storage.aget(f["paths"][variant])
    if variant == "original":
        ct = f.get("mime") or ct
    return Response(data, media_type=ct, headers={"Cache-Control": "private, max-age=3600"})


@api.get("/evidence/{fid}/play")
async def play_video(fid: str, u: User = Depends(current_user)):
    """Fresh, short-lived playback URL for an authorised viewer (installers: only their own NCs)."""
    d = await db.files.find_one({"_id": oid(fid), "kind": "video", "status": {"$ne": "discarded"}})
    if not d:
        raise HTTPException(404, "Video not found")
    await get_nc(d["nc_id"], u)
    f = EvidenceFile.from_mongo(d)
    ready = "playback" in f.paths
    return {"state": "ready" if ready else (f.playback or "pending"), "url": file_url(fid, "playback") if ready else None,
            "original_url": file_url(fid, "original"), "original_mime": f.mime, "codec": f.video_codec, "duration_s": f.duration_s}


@api.get("/config/media")
async def media_config(u: User = Depends(current_user)):
    return {"max_video_mb": MAX_VIDEO_MB}


# ---------------- export ----------------
@api.get("/export/link")
async def export_link(project_id: Optional[str] = None, u: User = Depends(staff)):
    if project_id:
        await live_project(project_id)
        return {"url": f"/api/export/project/{project_id}.zip?{signed_query(f'export:{project_id}', 10, stable=False)}"}
    if u.role != "admin":
        raise HTTPException(403, "Admin only")
    return {"url": f"/api/export/all.json?{signed_query('export:all', 10, stable=False)}"}


async def project_manifest(pid: str) -> dict:
    p = clean(await db.projects.find_one({"_id": oid(pid)}))
    ncs = clean(await db.ncs.find({"project_id": pid}).to_list(500))
    files = clean(await db.files.find({"project_id": pid}).to_list(5000))
    subs = clean(await db.submissions.find({"project_id": pid}).to_list(2000))
    targets = [f"project:{pid}"] + [f"nc:{n['id']}" for n in ncs]
    aud = await db.audit.find({"target": {"$in": targets}}, {"_id": 0}).sort("seq", 1).to_list(10000)
    return {"exported_at": now_iso(), "project": p, "ncs": ncs, "submissions": subs, "files": files, "audit": aud}


@api.get("/export/project/{pid}.zip")
async def export_project(pid: str, exp: int, sig: str):
    """TrustMark Evidence Pack: NC PDFs (before/after) + Videos/. Excluded photos stay in storage."""
    if not verify_signed(f"export:{pid}", exp, sig):
        raise HTTPException(403, "Link expired or invalid")
    p = await db.projects.find_one({"_id": oid(pid)})
    if not p or p.get("deleted"):
        raise HTTPException(404, "Project not found")
    data, _ = await cached_pack(pid, p)
    await audit("export.evidence_pack_downloaded", None, f"project:{pid}", {"size": len(data)})
    return Response(data, media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="{p["ref"]}_Evidence_Pack.zip"'})


async def pack_fingerprint(pid: str, p: dict) -> str:
    """Changes whenever anything that appears in the evidence pack changes (NC text/status/notes, files, address)."""
    ncs = await db.ncs.find({"project_id": pid}, {"number": 1, "status": 1, "original_text": 1, "export_note": 1, "title": 1}).sort("number", 1).to_list(500)
    files = await db.files.find({"project_id": pid}, {"status": 1, "kind": 1, "excluded": 1, "nc_id": 1, "stage": 1, "pdf_include": 1}).sort("_id", 1).to_list(20000)
    raw = json.dumps([p.get("ref"), p.get("address"), p.get("postcode"),
                      [[str(n["_id"]), n.get("number"), n.get("status"), n.get("original_text"), n.get("export_note"), n.get("title")] for n in ncs],
                      [[str(f["_id"]), f.get("nc_id"), f.get("status"), f.get("kind"), bool(f.get("excluded")), f.get("stage"), f.get("pdf_include")] for f in files]], default=str)
    if (await get_settings()).smart_pdf_enabled:  # Phase 4/5: tag + scope corrections change the PDF, so they make a new pack version
        tags = await db.files.find({"project_id": pid, "kind": "photo"}, {"classification": 1, "checklist_item": 1}).sort("_id", 1).to_list(20000)
        sc = await db.ncs.find({"project_id": pid}, {"scope.expected_count": 1, "scope.locations": 1, "checklist.items": 1}).sort("number", 1).to_list(500)
        raw += json.dumps([[str(f["_id"]), f.get("checklist_item"), {k: (f.get("classification") or {}).get(k) for k in ("location_id", "location_group", "stage", "dup_of", "component")}]
                           for f in tags] + [[n.get("scope"), n.get("checklist")] for n in sc], default=str)
    return hashlib.sha256(raw.encode()).hexdigest()[:20]


PACK_DIR = Path(os.environ.get("MEDIA_CACHE_DIR", "/tmp/rf_media")) / "packs"


async def cached_pack(pid: str, p: dict) -> tuple:
    """(zip bytes, fingerprint): reuses the last generated pack while its content is unchanged."""
    fp = await pack_fingerprint(pid, p)
    path = PACK_DIR / f"{pid}-{fp}.zip"
    if path.exists():
        return path.read_bytes(), fp
    data, _ = await build_evidence_pack(pid, p)
    PACK_DIR.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(data)
    tmp.replace(path)
    return data, fp


def _dbx_location(path: str) -> Optional[str]:
    try:
        return storage.client().files_get_metadata(path[len("dbx:id:"):] if path.startswith("dbx:id:") else path).path_display
    except Exception:
        return None


@api.post("/projects/{pid}/export-dropbox")
async def export_to_dropbox(pid: str, u: User = Depends(staff)):
    """Saves the current evidence pack to the project's Dropbox folder once per unique content.
    Repeated clicks/retries reuse the existing export; changed evidence creates a new dated version (never overwrites)."""
    p = clean(await live_project(pid))
    if not storage.dropbox_ready:
        raise HTTPException(409, "Dropbox is not connected. Ask an Admin to connect it in Settings.")
    fp = await pack_fingerprint(pid, p)
    done = await db.dropbox_exports.find_one({"project_id": pid, "fingerprint": fp, "status": "saved"}) or \
        await db.auto_packs.find_one({"project_id": pid, "fingerprint": fp, "status": "saved"})
    if done:
        loc = await run_in_threadpool(_dbx_location, done["path"])
        return {"status": "already_exported", "location": loc, "saved_at": done.get("created_at") or done.get("saved_at")}
    key = f"{pid}:{fp}"
    try:  # claim: concurrent requests for the same unchanged pack collapse into one upload
        await db.dropbox_exports.insert_one({"_id": key, "project_id": pid, "ref": p["ref"], "fingerprint": fp, "status": "running",
                                            "by": u.id, "created_at": now_iso()})
    except DuplicateKeyError:
        cur = await db.dropbox_exports.find_one({"_id": key})
        if cur.get("status") == "running":
            raise HTTPException(409, "This export is already in progress.")
        await db.dropbox_exports.update_one({"_id": key}, {"$set": {"status": "running", "by": u.id, "created_at": now_iso()}})
    try:
        data, _ = await cached_pack(pid, p)
        ts = now_iso()
        path = await storage.aput(export_path(await property_folder(pid), p["ref"], ts, fp[:6]), data, "application/zip")
        if not path.startswith("dbx:"):
            raise RuntimeError("Pack was not stored in Dropbox")
    except Exception as e:
        await db.dropbox_exports.update_one({"_id": key}, {"$set": {"status": "failed", "error": str(e)[:200]}})
        await audit("export.dropbox_failed", u.id, f"project:{pid}", {"error": str(e)[:200]})
        raise HTTPException(502, "Dropbox export failed. Please try again.")
    await db.dropbox_exports.update_one({"_id": key}, {"$set": {"status": "saved", "path": path, "size": len(data), "created_at": ts}})
    await audit("export.saved_to_dropbox", u.id, f"project:{pid}", {"path": path, "size": len(data), "fingerprint": fp})
    return {"status": "exported", "location": await run_in_threadpool(_dbx_location, path), "saved_at": ts}


# ---------------- automatic evidence pack on full project resolution ----------------
async def queue_auto_pack(pid: str, cycle: int):
    """One pack per completion cycle: the deterministic _id makes simultaneous triggers a no-op."""
    try:
        await db.auto_packs.insert_one({"_id": f"{pid}:{cycle}", "project_id": pid, "cycle": cycle, "status": "pending",
                                        "attempts": 0, "created_at": now_iso()})
    except DuplicateKeyError:
        return
    asyncio.create_task(run_auto_pack(f"{pid}:{cycle}"))


async def run_auto_pack(key: str):
    from datetime import datetime, timedelta, timezone
    stale = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat()
    job = await db.auto_packs.find_one_and_update(  # claim: only one worker runs a given pack
        {"_id": key, "$or": [{"status": {"$in": ["pending", "failed"]}}, {"status": "running", "started_at": {"$lt": stale}}]},
        {"$set": {"status": "running", "started_at": now_iso()}, "$inc": {"attempts": 1}}, return_document=True)
    if not job:
        return
    pid = job["project_id"]
    try:
        p = await db.projects.find_one({"_id": oid(pid)})
        if not p or p.get("deleted") or p.get("status") not in ("completed", "trustmark_resolved"):
            await db.auto_packs.update_one({"_id": key}, {"$set": {"status": "skipped", "error": "Project no longer fully resolved"}})
            return
        if not storage.dropbox_ready:
            raise RuntimeError("Dropbox is not connected")
        data, fp = await cached_pack(pid, p)
        n = None
        ts = now_iso()
        path = await storage.aput(export_path(await property_folder(pid), p["ref"], ts, f"v{job['cycle']}-{secrets.token_hex(3)}"),
                                  data, "application/zip")
        if not path.startswith("dbx:"):
            raise RuntimeError("Pack was not stored in Dropbox")
        await db.auto_packs.update_one({"_id": key}, {"$set": {"status": "saved", "path": path, "size": len(data), "fingerprint": fp,
                                                              "saved_at": ts, "error": None}})
        await audit("export.auto_pack_saved", None, f"project:{pid}", {"cycle": job["cycle"], "path": path, "size": len(data)})
    except Exception as e:
        log.exception("auto evidence pack failed %s", key)
        await db.auto_packs.update_one({"_id": key}, {"$set": {"status": "failed", "error": str(e)[:200], "failed_at": now_iso()}})
        await audit("export.auto_pack_failed", None, f"project:{pid}", {"cycle": job["cycle"], "error": str(e)[:200]})


@api.get("/projects/{pid}/evidence-packs")
async def list_auto_packs(pid: str, u: User = Depends(staff)):
    return clean(await db.auto_packs.find({"project_id": pid}).sort("cycle", -1).to_list(100))


@api.post("/projects/{pid}/evidence-packs/retry")
async def retry_auto_packs(pid: str, u: User = Depends(staff)):
    keys = [d["_id"] for d in await db.auto_packs.find({"project_id": pid, "status": {"$in": ["failed", "pending"]}}, {"_id": 1}).to_list(100)]
    for k in keys:
        await run_auto_pack(k)
    await audit("export.auto_pack_retry", u.id, f"project:{pid}", {"count": len(keys)})
    return clean(await db.auto_packs.find({"project_id": pid}).sort("cycle", -1).to_list(100))


async def deadline_reminders():
    """Hourly: installers get one reminder the day before a scheduled visit (deduplicated by notify)."""
    from datetime import datetime, timedelta, timezone
    while True:
        try:
            tomorrow = (datetime.now(timezone.utc) + timedelta(days=1)).date().isoformat()
            async for p in db.projects.find({"deleted": {"$ne": True}, "schedules": {"$exists": True}}, {"schedules": 1, "status": 1}):
                if p.get("status") in ("completed", "trustmark_resolved"):
                    continue
                for iid, d in (p.get("schedules") or {}).items():
                    if d == tomorrow:
                        await notify(iid, "deadline", str(p["_id"]), f"Visit date: {d}")
        except Exception:
            log.exception("deadline reminders failed")
        await asyncio.sleep(3600)


async def retry_pending_packs():
    """Startup safety net: packs interrupted by a restart or a Dropbox outage get one more attempt."""
    for d in await db.auto_packs.find({"status": {"$in": ["pending", "failed", "running"]}}, {"_id": 1}).to_list(500):
        await run_auto_pack(d["_id"])


async def pdf_photo_groups(n: dict, files: list, get) -> list:
    """[(heading, [jpeg])] for one NC. Order of authority: staff include/exclude + staff stage labels, then Gemini's
    arrangement of the photos (cached per photo set), then a plain fallback. A stage is only labelled when known;
    original TrustMark photos are handled separately and never labelled as installer evidence."""
    from evidence_pack import select_photos, readable, best, distinct
    from ai import arrange_photos
    photos = [f for f in files if f["kind"] == "photo" and f["status"] != "superseded" and f.get("pdf_include") is not False]

    async def load(fs):
        return await run_in_threadpool(readable, [b for f in fs for b in [await get(f, "stamped", "display", "original")] if b])
    picked = [f for f in photos if f.get("pdf_include") is True]
    pool = picked or photos
    if not pool:
        return []

    async def slots_out(sections):  # ONE clearest photo per slot (stamped copy keeps its timestamp)
        out = []
        for head, slots in sections:
            pairs = []
            for cands, cap in slots:
                imgs = await load(cands)
                if imgs:
                    pairs.append((await run_in_threadpool(best, imgs), cap))
            if pairs:
                out.append((head, pairs))
        return out
    if (await get_settings()).smart_pdf_enabled and (n.get("checklist") or {}).get("items"):
        from checklist import checklist_plan  # one photo per checklist requirement, staff-marked first
        sections = checklist_plan(n, photos)
        if sections:
            out = await slots_out(sections)
            if out:
                return out
    if (await get_settings()).smart_pdf_enabled and not picked:
        from smart_pdf import plan, scope_for, usable
        if usable(photos):  # every photo already tagged (Phase 2): no AI call here; otherwise the existing layout below
            return await slots_out(plan(n, photos, scope_for(n)))
    staff_staged = {st: [f for f in pool if f.get("stage") == st and f.get("stage_source") == "staff"] for st in STAGES}
    chosen = {st: v for st, v in staff_staged.items() if v}
    extra = []
    if picked and not chosen:  # staff hand-picked photos without stages: show exactly those
        return [("REMEDIATION PHOTOGRAPHS", await load(picked))]
    if len(chosen) < 3:
        cand = sorted(pool, key=lambda f: f.get("captured_at") or f.get("server_received_at") or "")[:12]
        key = hashlib.sha1(",".join(str(f["_id"]) for f in cand).encode()).hexdigest()[:16]
        arr = (n.get("pdf_ai") or {}) if (n.get("pdf_ai") or {}).get("key") == key else None
        if arr is None:
            imgs = [b for f in cand for b in [await get(f, "display", "stamped", "original")]]
            ok = [i for i, b in enumerate(imgs) if b]
            res = await arrange_photos(n.get("original_text") or n.get("description", ""), n.get("required_work", ""), n.get("installer_note", ""),
                                       n.get("export_note", ""), [imgs[i] for i in ok],
                                       [str(cand[i].get("captured_at") or "") for i in ok])
            if res is not None:
                m = lambda v: str(cand[ok[v]]["_id"]) if v is not None else None
                arr = {"key": key, "pre": m(res["pre"]), "mid": m(res["mid"]), "final": m(res["final"]),
                       "extra": [m(x) for x in res["extra"]], "uncertain": res["uncertain"], "description": res["description"], "at": now_iso()}
                await db.ncs.update_one({"_id": n["_id"]}, {"$set": {"pdf_ai": arr}})
                n["pdf_ai"] = arr
        if arr:
            by = {str(f["_id"]): f for f in cand}
            for st in STAGES:
                if st not in chosen and by.get(arr.get(st)):
                    chosen[st] = [by[arr[st]]]
            extra = [by[i] for i in arr.get("extra") or [] if i in by]
        elif not chosen:  # AI unavailable: no stage labels are guessed
            return [("REMEDIATION PHOTOGRAPHS", await run_in_threadpool(select_photos, await load(pool), 3))]
    out, shown = [], []
    for st in STAGES:  # ONE photo per stage; staff-picked photos of a stage are all kept
        if st not in chosen:
            continue
        forced = [f for f in chosen[st] if f.get("pdf_include") is True]
        imgs = await load(forced) if forced else await load(chosen[st])
        if imgs and not forced:
            imgs = [await run_in_threadpool(best, imgs)]
        if imgs:
            out.append((STAGE_NAME[st].upper() + " PHOTOGRAPH", imgs))
            shown += imgs
    if extra:  # AI "extra" photos only when they show something not already shown
        ex = await run_in_threadpool(distinct, await load(extra), shown)
        if ex:
            out.append(("ADDITIONAL SUPPORTING EVIDENCE", ex))
    return out


class PdfIncludeIn(BaseModel):
    include: Optional[bool] = None  # True = always in PDF, False = never, None = automatic
    stage: Optional[str] = Field(default=None, pattern=r"^(pre|mid|final|auto)$")  # staff classification ("auto" = let AI decide)


@api.put("/evidence/{fid}/pdf-include")
async def set_pdf_include(fid: str, body: PdfIncludeIn, u: User = Depends(staff)):
    f = await db.files.find_one({"_id": oid(fid)}, {"nc_id": 1, "kind": 1})
    if not f or f["kind"] != "photo":
        raise HTTPException(404, "Photo not found")
    await get_nc(f["nc_id"], u)
    upd = {"pdf_include": body.include}
    if body.stage:
        upd.update({"stage": None, "stage_source": None} if body.stage == "auto" else {"stage": body.stage, "stage_source": "staff"})
    await db.files.update_one({"_id": f["_id"]}, {"$set": upd})
    await audit("evidence.pdf_selection", u.id, f"nc:{f['nc_id']}", {"file_id": fid, **upd})
    return {"ok": True, **upd}


async def build_evidence_pack(pid: str, p: dict):
    from evidence_pack import slug, select_photos, nc_pdf, build_zip, repeated_hashes, drop_repeated, readable, distinct
    ncs = await db.ncs.find({"project_id": pid}).sort("number", 1).to_list(500)
    entries, vcount, prepared = [], {}, []

    async def get(f, *variants):
        for v in variants:
            if f.get("paths", {}).get(v):
                try:
                    return (await storage.aget(f["paths"][v]))[0]
                except Exception:
                    log.warning("pack skip %s", f["_id"])
        return None

    for n in ncs:
        nid = str(n["_id"])
        files = await db.files.find({"nc_id": nid, "status": {"$in": ["submitted", "draft", "superseded"]}}).sort("server_received_at", 1).to_list(5000)
        trust = [b for f in files if f["kind"] == "trustmark" and not f.get("excluded") for b in [await get(f, "display", "original")] if b]
        trust = await run_in_threadpool(readable, trust)
        prepared.append((n, files, trust, await pdf_photo_groups(n, files, get)))
    rep = await run_in_threadpool(repeated_hashes, [t for _, _, t, _ in prepared]) if len(prepared) > 1 else set()
    for n, files, trust, rem in prepared:
        trust = await run_in_threadpool(drop_repeated, trust, rep)
        trust = await run_in_threadpool(distinct, trust, [], 4)  # near-identical copies of the same TrustMark photo shown once
        name = f"{n['number']}_{slug(n.get('description', ''))}"
        nvid = sum(1 for f in files if f["kind"] == "video" and f["status"] != "superseded")
        if not (n.get("export_note") or "").strip():  # draft the works description instead of printing an empty section
            desc = ((n.get("pdf_ai") or {}).get("description") or (n.get("ai") or {}).get("works_description") or "").strip()
            if desc:
                await db.ncs.update_one({"_id": n["_id"], "export_note_source": {"$ne": "staff"}}, {"$set": {"export_note": desc, "export_note_source": "ai"}})
            n["export_note"] = desc or (n.get("installer_note") or "").strip()
        pdf = await run_in_threadpool(nc_pdf, clean(p), clean(n), trust[:6], rem, nvid)
        entries.append((f"{name}.pdf", pdf))
        for f in files:
            if f["kind"] == "video":
                data = await get(f, "original")
                if data:
                    vcount[name] = vcount.get(name, 0) + 1
                    suffix = "" if vcount[name] == 1 else f"_{vcount[name]}"
                    entries.append((f"Videos/{name}{suffix}.mp4", data))
    return await run_in_threadpool(build_zip, entries), len(entries)


@api.get("/export/all.json")
async def export_all(exp: int, sig: str):
    if not verify_signed("export:all", exp, sig):
        raise HTTPException(403, "Link expired or invalid")
    pids = [str(d["_id"]) for d in await db.projects.find({}, {"_id": 1}).to_list(10000)]
    data = {"exported_at": now_iso(), "projects": [await project_manifest(p) for p in pids]}
    return Response(json.dumps(data, default=str), media_type="application/json",
                    headers={"Content-Disposition": 'attachment; filename="remedialflow-metadata.json"'})


# ---------------- startup / seed ----------------
DEMO_USERS = [("admin@remedialflow.dev", "Admin Demo", "admin", "AdminDemo!2026"),
              ("employee@remedialflow.dev", "Emma Employee", "employee", "EmployeeDemo!2026"),
              ("installer@remedialflow.dev", "Ian Installer", "installer", "InstallerDemo!2026"),
              ("installer2@remedialflow.dev", "Sam Fitter", "installer", "InstallerDemo!2026")]
DEMO_PROJECTS = [
    ("EF-1024", "14 Station Road, Luton", "LU1 2AB", 2, [
        ("Loft insulation not extended over eaves; gaps visible near hatch.", "Lay insulation to full depth over gaps and eaves; fit draught strip to hatch.", False),
        ("External wall vent partially blocked by render.", "Clear render from vent and fit a clean, unobstructed vent cover.", False),
        ("Extractor fan in bathroom not ducted externally.", "Duct fan to external terminal and seal penetration.", True)]),
    ("EF-1025", "7 Mill Lane, Dunstable", "LU6 3QT", 2, [
        ("Cavity wall injection holes not made good.", "Fill and colour-match all drill holes to brickwork.", False),
        ("Missing pipe lagging in loft.", "Lag all exposed water pipes in loft space.", False)]),
    ("EF-1026", "22 Church Street, Harpenden", "AL5 2TF", 3, [
        ("Window trickle vents not installed.", "Fit trickle vents to all replacement windows in habitable rooms.", False)]),
]


async def seed():
    ids = {}
    for email, name, role, pw in DEMO_USERS:
        d = await db.users.find_one({"email": email})
        if not d:
            r = await db.users.insert_one(User(email=email, name=name, role=role, password_hash=ph.hash(pw)).to_mongo())
            ids[email] = str(r.inserted_id)
        else:
            # Never overwrite a password that was changed after seeding (security audit SEC-002).
            ids[email] = str(d["_id"])
    users = [u[0] for u in DEMO_USERS]
    for ref, addr, pc, ui, ncs in DEMO_PROJECTS:
        if await db.projects.find_one({"ref": ref}):
            continue
        r = await db.projects.insert_one(Project(ref=ref, address=addr, postcode=pc, installer_id=ids[users[ui]], rate=150).to_mongo())
        pid = str(r.inserted_id)
        for i, (desc, work, sc) in enumerate(ncs, 1):
            await add_nc(pid, NCIn(description=desc, required_work=work, safety_critical=sc), i, ids[users[ui]])
        await recompute_project(pid)
    # TrustMark reference photos for demo NCs (once).
    sys_user = User(id=ids[users[1]], email=users[1], name="seed", role="employee")
    for n in await db.ncs.find({"trustmark_ids": {"$size": 0}}).to_list(100):
        nc = NC.from_mongo(n)
        p = Project.from_mongo(await db.projects.find_one({"_id": oid(nc.project_id)}))
        if p.ref not in [x[0] for x in DEMO_PROJECTS]:
            continue
        data = await run_in_threadpool(demo_trustmark_image, f"{p.ref} {nc.number}", hash(nc.id) % 1000)
        bg = BackgroundTasks()
        up = UploadFile(io.BytesIO(data), filename="trustmark.jpg", headers={"content-type": "image/jpeg"})
        f = await store_upload(nc, p, sys_user, up, {}, "trustmark", "system", None, bg)
        await db.ncs.update_one({"_id": oid(nc.id)}, {"$push": {"trustmark_ids": f.id}})
        await bg()


@app.on_event("startup")
async def startup():
    await db.users.create_index("email", unique=True)
    await db.sessions.create_index("refresh_hash")
    await db.files.create_index([("nc_id", 1), ("client_id", 1)])
    await db.files.create_index([("project_id", 1), ("status", 1)])
    await db.files.create_index("submission_id")
    await db.ncs.create_index("project_id")
    await db.ncs.create_index("assigned_installer_id")
    await db.projects.create_index("status")
    await db.earnings.create_index([("project_id", 1), ("installer_id", 1)], unique=True)
    await db.audit.create_index("seq", unique=True)
    await db.audit.create_index("target")
    await db.notifications.create_index([("user_id", 1), ("key", 1)], unique=True)
    # Expired rate-limit rows are ignored by query (expires_at > now) and reused per key; no auto-deleting TTL index.
    try:
        await db.rate_limits.drop_index("expires_at_1")
    except Exception:
        pass
    from migrations import run_migrations
    await run_migrations()
    if os.environ.get("DEMO_SEED") == "true":
        asyncio.create_task(_seed_safe())
    from dropbox_link import load_dropbox as _ld
    await _ld()
    asyncio.create_task(backfill_playback())
    asyncio.create_task(retry_pending_packs())
    asyncio.create_task(deadline_reminders())


async def _seed_safe():
    try:
        await seed()
        log.info("seed complete")
    except Exception:
        log.exception("seed failed")


@app.on_event("shutdown")
async def shutdown():
    client.close()


app.include_router(auth_router)
app.include_router(api)
from phase10 import router as p10_router  # noqa: E402  (imports server helpers lazily)
app.include_router(p10_router)
from guidance import router as guidance_router  # noqa: E402
app.include_router(guidance_router)
from scope import router as scope_router  # noqa: E402
app.include_router(scope_router)
from coverage import router as coverage_router  # noqa: E402
app.include_router(coverage_router)
from staff_review import router as staff_review_router  # noqa: E402
app.include_router(staff_review_router)
from checklist import router as checklist_router  # noqa: E402
app.include_router(checklist_router)
app.include_router(documents_router)
app.include_router(recommend_router)
from photo_class import router as photo_class_router  # noqa: E402
app.include_router(photo_class_router)
from privacy_page import router as privacy_router  # noqa: E402
app.include_router(privacy_router)
from push import router as push_router  # noqa: E402
from dropbox_link import router as dropbox_router, load_dropbox  # noqa: E402
app.include_router(dropbox_router)
app.include_router(push_router)
app.add_middleware(CORSMiddleware, allow_credentials=True,
                   allow_origins=[o.strip() for o in os.environ.get("CORS_ORIGINS", "").split(",") if o.strip()],
                   allow_methods=["*"], allow_headers=["*"])
