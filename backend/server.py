import io
import os
import re
import json
import zipfile
import hashlib
import asyncio
import logging
from typing import Optional, List

from bson import ObjectId
from fastapi import FastAPI, APIRouter, Depends, HTTPException, UploadFile, File, Form, BackgroundTasks, Query
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, EmailStr, Field
from starlette.concurrency import run_in_threadpool
from starlette.middleware.cors import CORSMiddleware

from core import (db, client, User, Project, NC, EvidenceFile, Submission, AIResult, Settings, get_settings,
                  now_iso, oid, file_url, signed_query, verify_signed, audit, clean)
from auth import router as auth_router, current_user, require, ph, revoke_all, create_reset_token
from storage import storage, evidence_path, APP_NAME
from media import process_photo, video_frames, demo_trustmark_image, pdf_text_and_images
from ai import get_assessor, AssessmentInput, get_pdf_extractor

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
log = logging.getLogger("server")

app = FastAPI(title="RemedialFlow")
api = APIRouter(prefix="/api")
staff = require("admin", "employee")
admin = require("admin")
DONE = {"approved"}
REVIEW = {"employee_review", "admin_review"}
LIFECYCLE = {"unassigned", "in_progress", "completed", "trustmark_resolved"}
MAX_UPLOAD = 300 * 1024 * 1024


@app.get("/health", include_in_schema=False)
@app.head("/health", include_in_schema=False)
async def health():
    return {"status": "ok"}


# ---------------- helpers ----------------
async def get_project(pid: str, u: User) -> Project:
    p = Project.from_mongo(await db.projects.find_one({"_id": oid(pid)}))
    if not p:
        raise HTTPException(404, "Project not found")
    if u.role == "installer" and p.installer_id != u.id:
        raise HTTPException(403, "Not assigned to this project")
    return p


async def get_nc(nc_id: str, u: User):
    nc = NC.from_mongo(await db.ncs.find_one({"_id": oid(nc_id)}))
    if not nc:
        raise HTTPException(404, "NC not found")
    return nc, await get_project(nc.project_id, u)


def file_public(f: EvidenceFile) -> dict:
    d = f.model_dump(exclude={"paths"})
    d["urls"] = {v: file_url(f.id, v) for v in f.paths}
    return d


async def recompute_project(pid: str):
    """Lifecycle: unassigned | in_progress | completed | trustmark_resolved, plus needs_review flag."""
    ncs = await db.ncs.find({"project_id": pid}, {"status": 1, "assigned_installer_id": 1}).to_list(500)
    proj = await db.projects.find_one({"_id": oid(pid)}, {"completed_at": 1, "completion_history": 1, "resolved": 1,
                                                          "resolution_history": 1, "status": 1})
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
    await db.projects.update_one({"_id": oid(pid)}, {"$set": upd})
    if status == "completed":
        p = Project.from_mongo(await db.projects.find_one({"_id": oid(pid)}))
        if p.installer_id and not await db.earnings.find_one({"project_id": pid}):
            s = await get_settings()
            await db.earnings.insert_one({"installer_id": p.installer_id, "project_id": pid, "ref": p.ref,
                                          "address": p.address, "amount": p.rate, "currency": s.currency,
                                          "status": "earned", "earned_at": now_iso(), "paid_at": None})
            await audit("earning.created", None, f"project:{pid}", {"amount": p.rate})


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


@api.get("/admin/users")
async def list_users(u: User = Depends(admin)):
    return [User.from_mongo(d).public() for d in await db.users.find().sort("created_at", -1).to_list(1000)]


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
    r = await db.users.find_one_and_update({"_id": oid(uid)}, q, return_document=True)
    if not r:
        raise HTTPException(404, "User not found")
    if body.active is False:
        await revoke_all(uid)
    await audit("user.updated", u.id, f"user:{uid}", upd)
    return User.from_mongo(r).public()


@api.post("/admin/users/{uid}/reset")
async def admin_reset(uid: str, u: User = Depends(admin)):
    if not await db.users.find_one({"_id": oid(uid)}):
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
    return (await get_settings()).model_dump()


@api.put("/settings")
async def write_settings(body: Settings, u: User = Depends(admin)):
    await db.settings.update_one({"_id": "global"}, {"$set": body.model_dump()}, upsert=True)
    await audit("settings.updated", u.id, "settings", body.model_dump())
    return body.model_dump()


# ---------------- dashboard / projects ----------------
@api.get("/dashboard")
async def dashboard(u: User = Depends(staff)):
    # Lifecycle counters are mutually exclusive; awaiting_review is the attention flag (projects, not NCs).
    out = {k: await db.projects.count_documents({"status": k}) for k in LIFECYCLE}
    out["awaiting_review"] = await db.projects.count_documents({"needs_review": True})
    return out


@api.post("/projects/{pid}/resolve")
async def resolve_project(pid: str, u: User = Depends(staff)):
    p = Project.from_mongo(await db.projects.find_one({"_id": oid(pid)}))
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
    q: dict = {}
    if u.role == "installer":
        q["installer_id"] = u.id
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
    for i in items:
        i["installer_name"] = names.get(i["installer_id"] or "", None)
        i["installer_names"] = sorted(names[x] for x in per.get(i["id"], set()) if x in names)
    return {"items": items, "total": total, "page": page, "has_more": page * limit < total}


class NCIn(BaseModel):
    description: str = Field(min_length=1)
    required_work: str = Field(min_length=1)
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


# ---------------- PDF import (async) ----------------
async def _parse_pdf(import_id: str, data: bytes):
    import tempfile
    try:
        text, images = await run_in_threadpool(pdf_text_and_images, data)
        page_images = {}
        for page, imgs in images.items():
            paths = []
            for k, b in enumerate(imgs):
                paths.append(await storage.aput(f"{APP_NAME}/imports/{import_id}/page{page}-{k}.jpg", b, "image/jpeg"))
            page_images[str(page)] = paths
        with tempfile.NamedTemporaryFile(suffix=".pdf") as tf:
            tf.write(data)
            tf.flush()
            draft = await get_pdf_extractor().extract(tf.name, text)
        for n in draft["ncs"]:
            n["photo_count"] = sum(len(page_images.get(str(p), [])) for p in n["pages"])
        await db.imports.update_one({"_id": ObjectId(import_id)}, {"$set": {
            "status": "ready", "draft": draft, "page_images": page_images, "page_count": text.count("--- page "),
            "finished_at": now_iso()}})
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
    path = await storage.aput(f"{APP_NAME}/imports/{iid}/report.pdf", data, "application/pdf")
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
    return clean(d)


async def _attach_pdf_photos(import_id: str, nc_pages: List[tuple]):
    imp = await db.imports.find_one({"_id": ObjectId(import_id)})
    pi = (imp or {}).get("page_images", {})
    sys_user = User(id=imp["uploaded_by"], email="import", name="import", role="employee")
    for nc_id, pages in nc_pages:
        nc = NC.from_mongo(await db.ncs.find_one({"_id": ObjectId(nc_id)}))
        p = Project.from_mongo(await db.projects.find_one({"_id": oid(nc.project_id)}))
        for page in pages:
            for path in pi.get(str(page), []):
                try:
                    data, _ = await storage.aget(path)
                    bg = BackgroundTasks()
                    up = UploadFile(io.BytesIO(data), filename=f"report-p{page}.jpg", headers={"content-type": "image/jpeg"})
                    f = await store_upload(nc, p, sys_user, up, {}, "trustmark", "pdf_import", None, bg)
                    await db.ncs.update_one({"_id": oid(nc.id)}, {"$push": {"trustmark_ids": f.id}})
                    await bg()
                except Exception:
                    log.exception("attach pdf photo failed")


class ProjectPatch(BaseModel):
    installer_id: Optional[str] = None
    rate: Optional[float] = None


async def add_nc(pid: str, body: NCIn, idx: int, installer_id: Optional[str] = None) -> str:
    d = body.model_dump(exclude={"pages"})
    d["original_text"] = d.get("original_text") or d["description"]
    nc = NC(project_id=pid, number=f"NC-{idx:02d}", assigned_installer_id=installer_id, **d)
    r = await db.ncs.insert_one(nc.to_mongo())
    return str(r.inserted_id)


async def assign_unassigned_ncs(pid: str, installer_id: Optional[str], actor: User):
    """Project-level assignment defaults every unassigned NC to that installer."""
    if not installer_id:
        return
    ncs = await db.ncs.find({"project_id": pid, "assigned_installer_id": None}, {"_id": 1}).to_list(500)
    for n in ncs:
        await db.ncs.update_one({"_id": n["_id"]}, {"$set": {"assigned_installer_id": installer_id},
                                "$push": {"assignment_history": {"to": installer_id, "from": None, "by": actor.id, "at": now_iso()}}})
        await audit("nc.assigned", actor.id, f"nc:{n['_id']}", {"to": installer_id})


@api.post("/projects")
async def create_project(body: ProjectIn, bg: BackgroundTasks, u: User = Depends(staff)):
    if await db.projects.find_one({"ref": body.ref}):
        raise HTTPException(409, "Project reference already exists")
    s = await get_settings()
    p = Project(ref=body.ref, address=body.address, postcode=body.postcode.upper(), installer_id=body.installer_id or None,
                rate=body.rate if body.rate is not None else s.default_job_rate)
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
    await recompute_project(pid)
    if body.import_id:
        await db.imports.update_one({"_id": oid(body.import_id)}, {"$set": {"project_id": pid}})
        if nc_pages:
            bg.add_task(_attach_pdf_photos, body.import_id, nc_pages)
    await audit("project.created", u.id, f"project:{pid}", {"ref": p.ref, "import_id": body.import_id})
    return {"id": pid}


@api.patch("/projects/{pid}")
async def patch_project(pid: str, body: ProjectPatch, u: User = Depends(staff)):
    upd = {k: v for k, v in body.model_dump().items() if v is not None}
    if "installer_id" in upd and upd["installer_id"] == "":
        upd["installer_id"] = None
    await db.projects.update_one({"_id": oid(pid)}, {"$set": upd})
    if upd.get("installer_id"):
        await assign_unassigned_ncs(pid, upd["installer_id"], u)
        await recompute_project(pid)
    await audit("project.updated", u.id, f"project:{pid}", upd)
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
    ncs = [NC.from_mongo(d) for d in await db.ncs.find({"project_id": pid}).sort("number", 1).to_list(500)]
    files = [EvidenceFile.from_mongo(d) for d in await db.files.find(
        {"project_id": pid, "status": {"$in": ["draft", "submitted", "superseded"]}}).sort("server_received_at", 1).to_list(5000)]
    by_nc: dict = {}
    for f in files:
        by_nc.setdefault(f.nc_id, []).append(f)
    out = []
    for n in ncs:
        fs = by_nc.get(n.id, [])
        trust = [file_public(f) for f in fs if f.kind == "trustmark"]
        photos = [file_public(f) for f in fs if f.kind == "photo" and f.status != "superseded"]
        video = next((file_public(f) for f in fs if f.id == n.active_video_id), None)
        d = n.model_dump()
        d.update(trustmark=trust, photos=photos, video=video,
                 video_versions=sum(1 for f in fs if f.kind == "video"),
                 draft_count=sum(1 for f in fs if f.status == "draft" and f.kind in ("photo", "video")))
        out.append(d)
    pd = p.model_dump()
    uids = {n.assigned_installer_id for n in ncs if n.assigned_installer_id} | ({p.installer_id} if p.installer_id else set())
    names = {str(d["_id"]): d["name"] for d in await db.users.find({"_id": {"$in": [ObjectId(i) for i in uids]}}, {"name": 1}).to_list(100)}
    for d in out:
        d["assigned_installer_name"] = names.get(d.get("assigned_installer_id") or "")
    pd["installer_name"] = names.get(p.installer_id or "")
    pd["installer_names"] = sorted({names[n.assigned_installer_id] for n in ncs if n.assigned_installer_id in names})
    return {"project": pd, "ncs": out}


@api.get("/projects/{pid}/report-link")
async def report_link(pid: str, u: User = Depends(staff)):
    p = await db.projects.find_one({"_id": oid(pid)}, {"source_report": 1})
    if not p or not p.get("source_report"):
        raise HTTPException(404, "No source report")
    return {"url": f"/api/reports/{pid}?{signed_query(f'report:{pid}', 1)}"}


@api.get("/reports/{pid}")
async def serve_report(pid: str, exp: int, sig: str):
    if not verify_signed(f"report:{pid}", exp, sig):
        raise HTTPException(403, "Link expired or invalid")
    p = await db.projects.find_one({"_id": oid(pid)}, {"source_report": 1})
    data, _ = await storage.aget(p["source_report"]["path"])
    return Response(data, media_type="application/pdf", headers={"Content-Disposition": f'inline; filename="{p["source_report"].get("filename") or "report.pdf"}"'})


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
        for variant, b in outs.items():
            paths[variant] = await storage.aput(
                evidence_path(f.project_id, f.nc_id, f.submission_id or "reference", fid, variant, "jpg"), b, "image/jpeg")
        await db.files.update_one({"_id": ObjectId(fid)}, {"$set": {"paths": paths, "processing": "ready"}})
    except Exception as e:
        log.exception("processing failed %s", fid)
        await db.files.update_one({"_id": ObjectId(fid)}, {"$set": {"processing": "failed", "processing_error": str(e)[:200]}})


async def store_upload(nc: NC, p: Project, u: User, file: UploadFile, meta: dict, kind: str, source: str,
                       submission_id: Optional[str], bg: BackgroundTasks) -> EvidenceFile:
    data = await file.read()
    if not data:
        raise HTTPException(400, "Empty file")
    if len(data) > MAX_UPLOAD:
        raise HTTPException(413, "File too large")
    fid = ObjectId()
    mime = file.content_type or ("video/mp4" if kind == "video" else "image/jpeg")
    ext = (file.filename or "").rsplit(".", 1)[-1].lower() if "." in (file.filename or "") else ("mp4" if kind == "video" else "jpg")
    sha = hashlib.sha256(data).hexdigest()
    path = await storage.aput(evidence_path(p.id, nc.id, submission_id or "reference", str(fid), "original", ext), data, mime)
    lat, lng, acc = meta.get("lat"), meta.get("lng"), meta.get("accuracy_m")
    loc_ok = source == "in_app_camera" and lat is not None and lng is not None and acc is not None and float(acc) <= 50
    f = EvidenceFile(id=str(fid), project_id=p.id, nc_id=nc.id, submission_id=submission_id, kind=kind, source=source,
                     paths={"original": path}, sha256=sha, size=len(data), mime=mime, original_name=file.filename or "",
                     captured_at=meta.get("captured_at"), tz=meta.get("tz"), tz_offset_min=meta.get("tz_offset_min"),
                     lat=lat, lng=lng, accuracy_m=acc, location_verified=loc_ok, duration_s=meta.get("duration_s"),
                     uploaded_by=u.id, client_id=meta.get("client_id"),
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
    if kind == "video":
        if nc.active_video_id:
            await db.files.update_one({"_id": oid(nc.active_video_id)}, {"$set": {"status": "superseded"}})
        await db.ncs.update_one({"_id": oid(nc_id)}, {"$set": {"active_video_id": f.id}})
    return file_public(f)


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
    await get_project(f.project_id, u)
    # Soft-remove from the submission; the original object and audit record are retained.
    await db.files.update_one({"_id": oid(fid)}, {"$set": {"status": "discarded"}})
    if f.kind == "video":
        await db.ncs.update_one({"_id": oid(f.nc_id), "active_video_id": fid}, {"$set": {"active_video_id": None}})
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
        trust = [EvidenceFile.from_mongo(d) for d in await db.files.find({"nc_id": nc_id, "kind": "trustmark"}).to_list(10)]

        async def img(f, variant="display"):
            path = f.paths.get(variant) or f.paths.get("original")
            return (await storage.aget(path))[0]

        refs = [await img(t) for t in trust[:3]]
        evid = [await img(f) for f in photos[:6]]
        video = None
        if sub.video_id:
            video = EvidenceFile.from_mongo(await db.files.find_one({"_id": ObjectId(sub.video_id)}))
            vbytes = (await storage.aget(video.paths["original"]))[0]
            frames = await run_in_threadpool(video_frames, vbytes, video.duration_s, 4)
            for i, fr in enumerate(frames):
                p = await storage.aput(evidence_path(nc.project_id, nc_id, sub_id, video.id, f"frame{i}", "jpg"), fr, "image/jpeg")
                await db.files.update_one({"_id": ObjectId(video.id)}, {"$set": {f"paths.frame{i}": p}})
            evid += frames
        reasons: List[str] = []
        try:
            res = await get_assessor().assess(AssessmentInput(nc.number, nc.description, nc.required_work,
                                                             nc.safety_critical, refs, evid))
        except Exception as e:
            log.exception("AI failed")
            res = None
            reasons.append(f"AI unavailable: {str(e)[:80]}")
        if res is None:
            ai = AIResult(outcome="manual_review_required", feedback="Sent to an employee for review.", concerns=reasons)
        else:
            ai = AIResult(outcome=res.outcome, confidence=res.confidence, feedback=res.feedback, concerns=res.concerns, model=res.model)
        status = {"ai_verified": "approved", "more_evidence_required": "more_evidence"}.get(ai.outcome, "employee_review")
        if status == "approved":
            media = photos + ([video] if video else [])
            if not s.auto_verify:
                reasons.append("Auto-verification disabled")
            if ai.confidence < s.confidence_threshold:
                reasons.append(f"Confidence {ai.confidence:.2f} below {s.confidence_threshold:.2f}")
            if nc.safety_critical:
                reasons.append("Safety-critical NC")
            if not refs:
                reasons.append("No TrustMark reference photo to compare")
            if s.location_policy == "accept_flag" and any(not f.location_verified for f in photos):
                reasons.append("Location not verified on some photos")
            if s.gallery_policy == "manual_review" and any(f.source == "gallery" for f in media):
                reasons.append("Includes gallery uploads")
            if reasons:
                status = "employee_review"
                ai.concerns = ai.concerns + reasons
        extra = {"approved_by": "ai", "approved_at": now_iso()} if status == "approved" else {}
        await db.ncs.update_one({"_id": ObjectId(nc_id)}, {"$set": {"status": status, "ai": ai.model_dump(), "updated_at": now_iso(), **extra}})
        await audit("ai.assessed", None, f"nc:{nc_id}", {"submission_id": sub_id, **ai.model_dump(), "final_status": status})
        await recompute_project(nc.project_id)
    except Exception:
        log.exception("assessment crashed")
        await db.ncs.update_one({"_id": ObjectId(nc_id)}, {"$set": {"status": "employee_review",
                                "ai": AIResult(outcome="manual_review_required", feedback="Sent to an employee for review.").model_dump()}})


@api.post("/ncs/{nc_id}/submit")
async def submit(nc_id: str, bg: BackgroundTasks, u: User = Depends(current_user)):
    nc, p = await get_nc(nc_id, u)
    if not nc.draft_submission_id:
        raise HTTPException(400, "No new evidence to submit")
    drafts = await db.files.find({"submission_id": nc.draft_submission_id, "status": "draft"}).to_list(500)
    photo_ids = [str(d["_id"]) for d in drafts if d["kind"] == "photo"]
    if not photo_ids and not nc.active_video_id:
        raise HTTPException(400, "Add at least one photo or video")
    sid = nc.draft_submission_id
    await db.files.update_many({"submission_id": sid, "status": "draft"}, {"$set": {"status": "submitted"}})
    await db.submissions.update_one({"_id": oid(sid)}, {"$set": {"status": "submitted", "photo_ids": photo_ids,
                                    "video_id": nc.active_video_id, "submitted_at": now_iso()}})
    await db.ncs.update_one({"_id": oid(nc_id)}, {"$set": {"status": "assessing", "draft_submission_id": None, "updated_at": now_iso()}})
    await recompute_project(p.id)
    await audit("evidence.submitted", u.id, f"nc:{nc_id}", {"submission_id": sid, "photos": len(photo_ids), "video": nc.active_video_id})
    bg.add_task(run_assessment, nc_id, sid)
    return {"ok": True, "submission_id": sid, "status": "assessing"}


class ReviewIn(BaseModel):
    action: str = Field(pattern="^(approve|reject|more_evidence)$")
    note: str = ""


@api.post("/ncs/{nc_id}/review")
async def review(nc_id: str, body: ReviewIn, u: User = Depends(staff)):
    nc, p = await get_nc(nc_id, u)
    status = {"approve": "approved", "reject": "rejected", "more_evidence": "more_evidence"}[body.action]
    st = {"status": status, "review_note": body.note, "reviewed_by": u.id, "updated_at": now_iso()}
    if status == "approved":
        st.update(approved_by=u.id, approved_at=now_iso())
    elif status == "rejected":
        st["rejection_source"] = u.role
    await db.ncs.update_one({"_id": oid(nc_id)}, {"$set": st})
    await recompute_project(p.id)
    await audit("nc.reviewed", u.id, f"nc:{nc_id}", {"action": body.action, "note": body.note})
    return {"ok": True, "status": status}


@api.get("/review-queue")
async def review_queue(u: User = Depends(staff)):
    ncs = await db.ncs.find({"status": {"$in": ["employee_review", "admin_review", "assessing"]}}).sort("updated_at", 1).to_list(200)
    pids = list({n["project_id"] for n in ncs})
    projs = {str(d["_id"]): d for d in await db.projects.find({"_id": {"$in": [ObjectId(i) for i in pids]}}).to_list(500)}
    return [{**NC.from_mongo(n).model_dump(), "project_ref": projs.get(n["project_id"], {}).get("ref"),
             "address": projs.get(n["project_id"], {}).get("address")} for n in ncs]


# ---------------- earnings ----------------
@api.get("/earnings")
async def earnings(u: User = Depends(current_user)):
    q = {} if u.role == "admin" else {"installer_id": u.id}
    items = clean(await db.earnings.find(q).sort("earned_at", -1).to_list(500))
    s = await get_settings()
    pending = await db.projects.find({"installer_id": u.id, "status": {"$ne": "completed"}}, {"rate": 1}).to_list(500) if u.role == "installer" else []
    if u.role == "admin":
        names = {str(d["_id"]): d["name"] for d in await db.users.find({"role": "installer"}, {"name": 1}).to_list(1000)}
        for i in items:
            i["installer_name"] = names.get(i["installer_id"])
    return {"currency": s.currency, "payment_terms": s.payment_terms, "items": items,
            "earned": sum(i["amount"] for i in items if i["status"] == "earned"),
            "paid": sum(i["amount"] for i in items if i["status"] == "paid"),
            "in_progress": sum(p.get("rate", 0) for p in pending), "jobs_in_progress": len(pending)}


@api.post("/earnings/{eid}/paid")
async def mark_paid(eid: str, u: User = Depends(admin)):
    await db.earnings.update_one({"_id": oid(eid)}, {"$set": {"status": "paid", "paid_at": now_iso()}})
    await audit("earning.paid", u.id, f"earning:{eid}")
    return {"ok": True}


# ---------------- files (signed, temporary URLs) ----------------
@api.get("/files/{fid}/{variant}")
async def serve_file(fid: str, variant: str, exp: int, sig: str):
    if not verify_signed(f"{fid}:{variant}", exp, sig):
        raise HTTPException(403, "Link expired or invalid")
    f = await db.files.find_one({"_id": oid(fid)}, {"paths": 1, "mime": 1})
    if not f or variant not in f.get("paths", {}):
        raise HTTPException(404, "Not found")
    data, ct = await storage.aget(f["paths"][variant])
    if variant == "original":
        ct = f.get("mime") or ct
    return Response(data, media_type=ct, headers={"Cache-Control": "private, max-age=3600"})


# ---------------- export ----------------
@api.get("/export/link")
async def export_link(project_id: Optional[str] = None, u: User = Depends(staff)):
    if project_id:
        return {"url": f"/api/export/project/{project_id}.zip?{signed_query(f'export:{project_id}', 1)}"}
    if u.role != "admin":
        raise HTTPException(403, "Admin only")
    return {"url": f"/api/export/all.json?{signed_query('export:all', 1)}"}


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
    if not verify_signed(f"export:{pid}", exp, sig):
        raise HTTPException(403, "Link expired or invalid")
    man = await project_manifest(pid)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("manifest.json", json.dumps(man, indent=2, default=str))
        for f in man["files"]:
            for variant in ("original", "stamped"):
                path = f.get("paths", {}).get(variant)
                if path:
                    try:
                        data, _ = await storage.aget(path)
                        z.writestr(path.replace(f"{APP_NAME}/", ""), data)
                    except Exception:
                        log.warning("export skip %s", path)
    ref = (man["project"] or {}).get("ref", pid)
    return Response(buf.getvalue(), media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="{ref}-evidence.zip"'})


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
    await db.audit.create_index("seq", unique=True)
    await db.audit.create_index("target")
    from migrations import run_migrations
    await run_migrations()
    if os.environ.get("DEMO_SEED") == "true":
        asyncio.create_task(_seed_safe())


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
app.add_middleware(CORSMiddleware, allow_credentials=True,
                   allow_origins=[o.strip() for o in os.environ.get("CORS_ORIGINS", "").split(",") if o.strip()],
                   allow_methods=["*"], allow_headers=["*"])
