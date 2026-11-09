"""Admin bulk TrustMark PDF import: upload many reports, preview, then create Unassigned projects.
Exact duplicates (same PDF or same project reference) are skipped automatically. A report at the same property address with a
different reference is flagged for Admin review (create anyway / skip). Existing projects and NCs are never changed."""
import asyncio
import hashlib

from bson import ObjectId
from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel, Field

from auth import require
from core import User, audit, db, now_iso, oid
from report_import import find_duplicate, norm_ref
from storage import SYSTEM, storage

router = APIRouter(prefix="/api/admin/bulk-imports", tags=["bulk-import"])
admin = require("admin")
MAX_FILES = 50
_parse_slots = asyncio.Semaphore(2)  # bounded background reading
SKIP_REASONS = ("Same PDF already in this upload", "Same PDF already imported", "Same project reference")


def _is_exact(dup: dict | None) -> bool:
    """Exact duplicate -> auto-skip. Anything else (address-only match) needs an Admin decision."""
    return bool(dup) and (dup["reason"] in SKIP_REASONS or dup["reason"].startswith("Same reference as"))


async def _batch(bid: str) -> dict:
    b = await db.bulk_imports.find_one({"_id": oid(bid)})
    if not b:
        raise HTTPException(404, "Bulk import not found")
    return b


@router.post("")
async def create_batch(u: User = Depends(admin)):
    r = await db.bulk_imports.insert_one({"created_by": u.id, "created_at": now_iso(), "confirmed_at": None})
    return {"id": str(r.inserted_id)}


async def _parse(iid: str, data: bytes):
    from server import _parse_pdf
    async with _parse_slots:
        await _parse_pdf(iid, data, rules_first=True)


@router.post("/{bid}/files")
async def add_file(bid: str, bg: BackgroundTasks, file: UploadFile = File(...), u: User = Depends(admin)):
    await _batch(bid)
    if await db.imports.count_documents({"batch_id": bid}) >= MAX_FILES:
        raise HTTPException(400, f"A bulk import can hold up to {MAX_FILES} PDFs")
    data = await file.read()
    if not data.startswith(b"%PDF"):
        raise HTTPException(400, "Not a PDF file")
    if len(data) > 40 * 1024 * 1024:
        raise HTTPException(413, "PDF too large (max 40 MB)")
    sha = hashlib.sha256(data).hexdigest()
    base = {"batch_id": bid, "filename": file.filename, "size": len(data), "sha256": sha, "uploaded_by": u.id, "created_at": now_iso()}
    dup = None
    if await db.imports.find_one({"batch_id": bid, "sha256": sha}, {"_id": 1}):
        dup = {"reason": "Same PDF already in this upload"}
    else:
        p = await db.projects.find_one({"source_report.sha256": sha}, {"ref": 1, "address": 1, "postcode": 1, "deleted": 1})
        if p:
            dup = {"project_id": str(p["_id"]), "ref": p["ref"], "address": p.get("address", ""), "postcode": p.get("postcode", ""),
                   "deleted": bool(p.get("deleted")), "reason": "Same PDF already imported"}
    if dup:
        r = await db.imports.insert_one({**base, "status": "skipped", "duplicate": dup})
        return {"id": str(r.inserted_id), "status": "skipped"}
    iid = ObjectId()
    path = await storage.aput(f"{SYSTEM}/imports/{iid}/report.pdf", data, "application/pdf")
    await db.imports.insert_one({"_id": iid, **base, "status": "processing", "path": path})
    await audit("import.uploaded", u.id, f"import:{iid}", {"filename": file.filename, "size": len(data), "batch_id": bid})
    bg.add_task(_parse, str(iid), data)
    return {"id": str(iid), "status": "processing"}


def _item(d: dict) -> dict:
    dr = d.get("draft") or {}
    return {"id": str(d["_id"]), "filename": d.get("filename"), "status": d["status"], "method": d.get("method"),
            "ref": dr.get("ref"), "address": dr.get("address"), "postcode": dr.get("postcode"), "nc_count": len(dr.get("ncs") or []),
            "duplicate": d.get("duplicate"), "error": d.get("error"), "project_id": d.get("project_id"), "decision": d.get("decision")}


@router.get("/{bid}")
async def get_batch(bid: str, u: User = Depends(admin)):
    b = await _batch(bid)
    items = [_item(d) for d in await db.imports.find({"batch_id": bid}, {"page_images": 0}).sort("created_at", 1).to_list(MAX_FILES)]
    refs: dict = {}
    addrs: dict = {}
    for it in items:  # same reference twice in one upload: only the first is created; same address: Admin review
        if it["status"] != "ready" or it["duplicate"]:
            continue
        k = norm_ref(it["ref"]) if it["ref"] else None
        a = _addr_key(it)
        if k and k in refs:
            it["duplicate"] = {"reason": f"Same reference as {refs[k]} in this upload"}
        elif a and a in addrs:
            it["duplicate"] = {"reason": f"Same property address as {addrs[a]} in this upload"}
        if k:
            refs.setdefault(k, it["filename"])
        if a:
            addrs.setdefault(a, it["filename"])
    for it in items:
        it["review"] = it["status"] == "ready" and bool(it["duplicate"]) and not _is_exact(it["duplicate"])
    return {"id": bid, "confirmed_at": b.get("confirmed_at"), "items": items}


def _addr_key(it: dict) -> str | None:
    from report_import import _house_no
    pc, no = norm_ref(it.get("postcode")), _house_no(it.get("address"))
    return f"{pc}|{no}" if pc and no else None


class DecisionIn(BaseModel):
    decision: str = Field(pattern="^(create|skip)$")


@router.put("/{bid}/items/{iid}/decision")
async def set_decision(bid: str, iid: str, body: DecisionIn, u: User = Depends(admin)):
    """Admin decision for a report flagged as a possible duplicate (same address, different reference)."""
    d = await db.imports.find_one({"_id": oid(iid), "batch_id": bid, "status": "ready"})
    if not d:
        raise HTTPException(404, "Report not found or already processed")
    await db.imports.update_one({"_id": d["_id"]}, {"$set": {"decision": body.decision, "decision_by": u.id, "decision_at": now_iso()}})
    await audit("import.review_decision", u.id, f"import:{iid}", {"decision": body.decision})
    return {"ok": True}


@router.post("/{bid}/confirm")
async def confirm_batch(bid: str, bg: BackgroundTasks, u: User = Depends(admin)):
    """Creates an Unassigned project for every ready, non-duplicate report. Safe to call again: created items are skipped."""
    from server import NCIn, ProjectIn, create_project
    await _batch(bid)
    created, skipped, waiting = [], [], []
    for d in await db.imports.find({"batch_id": bid, "status": "ready", "project_id": None}).sort("created_at", 1).to_list(MAX_FILES):
        claimed = await db.imports.find_one_and_update({"_id": d["_id"], "status": "ready", "project_id": None}, {"$set": {"status": "creating"}})
        if not claimed:
            continue
        dr = d.get("draft") or {}
        dup = await find_duplicate(dr)  # fresh check also catches earlier items created in this run
        if dup and not _is_exact(dup) and d.get("decision") != "create":
            if d.get("decision") == "skip":
                reason = "Skipped by Admin"
            else:  # flagged for Admin review: leave untouched until a decision is made
                await db.imports.update_one({"_id": d["_id"]}, {"$set": {"status": "ready", "duplicate": dup}})
                waiting.append({"id": str(d["_id"]), "filename": d.get("filename"), "reason": dup["reason"]})
                continue
        elif dup and _is_exact(dup):
            reason = dup["reason"]
        else:
            reason = ("Reference or address missing from the report" if not dr.get("ref") or not dr.get("address")
                      else "No NCs found in the report" if not dr.get("ncs") else "Skipped by Admin" if d.get("decision") == "skip" else None)
        if reason:
            await db.imports.update_one({"_id": d["_id"]}, {"$set": {"status": "skipped", "duplicate": dup if dup and reason == dup["reason"] else {**(dup or {}), "reason": reason}}})
            skipped.append({"id": str(d["_id"]), "filename": d.get("filename"), "reason": reason})
            continue
        try:
            body = ProjectIn(ref=dr["ref"].strip(), address=dr["address"].strip(), postcode=dr.get("postcode") or "", installer_id=None,
                             import_id=str(d["_id"]), ncs=[NCIn(description=n["description"] or n.get("original_text") or "NC",
                                                                required_work=n.get("required_work") or "", safety_critical=bool(n.get("safety_critical")),
                                                                original_text=n.get("original_text") or "", pages=n.get("pages") or [])
                                                           for n in dr["ncs"]])
            res = await create_project(body, bg, u)
        except HTTPException as e:
            await db.imports.update_one({"_id": d["_id"]}, {"$set": {"status": "skipped", "duplicate": {"reason": e.detail}}})
            skipped.append({"id": str(d["_id"]), "filename": d.get("filename"), "reason": e.detail})
            continue
        except Exception:
            await db.imports.update_one({"_id": d["_id"]}, {"$set": {"status": "ready"}})
            raise
        await db.imports.update_one({"_id": d["_id"]}, {"$set": {"status": "created", "project_id": res["id"]}})
        created.append({"id": str(d["_id"]), "project_id": res["id"], "ref": body.ref})
    await db.bulk_imports.update_one({"_id": oid(bid)}, {"$set": {"confirmed_at": now_iso()}})
    await audit("import.bulk_confirmed", u.id, f"bulk_import:{bid}", {"created": len(created), "skipped": len(skipped), "waiting": len(waiting)})
    return {"created": created, "skipped": skipped, "waiting_review": waiting}
