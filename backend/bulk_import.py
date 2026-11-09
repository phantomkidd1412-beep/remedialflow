"""Admin bulk TrustMark PDF import: upload many reports, preview, then create Unassigned projects.
Duplicates (same PDF, same reference, or same postcode + similar address) are skipped, never merged."""
import asyncio
import hashlib

from bson import ObjectId
from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, UploadFile

from auth import require
from core import User, audit, db, now_iso, oid
from report_import import find_duplicate, norm_ref
from storage import SYSTEM, storage

router = APIRouter(prefix="/api/admin/bulk-imports", tags=["bulk-import"])
admin = require("admin")
MAX_FILES = 50
_parse_slots = asyncio.Semaphore(2)  # bounded background reading


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
            "duplicate": d.get("duplicate"), "error": d.get("error"), "project_id": d.get("project_id")}


@router.get("/{bid}")
async def get_batch(bid: str, u: User = Depends(admin)):
    b = await _batch(bid)
    items = [_item(d) for d in await db.imports.find({"batch_id": bid}, {"page_images": 0}).sort("created_at", 1).to_list(MAX_FILES)]
    seen: dict = {}
    for it in items:  # same reference twice in one upload: only the first is created
        if it["status"] == "ready" and not it["duplicate"] and it["ref"]:
            k = norm_ref(it["ref"])
            if k in seen:
                it["duplicate"] = {"reason": f"Same reference as {seen[k]} in this upload"}
            seen.setdefault(k, it["filename"])
    return {"id": bid, "confirmed_at": b.get("confirmed_at"), "items": items}


@router.post("/{bid}/confirm")
async def confirm_batch(bid: str, bg: BackgroundTasks, u: User = Depends(admin)):
    """Creates an Unassigned project for every ready, non-duplicate report. Safe to call again: created items are skipped."""
    from server import NCIn, ProjectIn, create_project
    await _batch(bid)
    created, skipped = [], []
    for d in await db.imports.find({"batch_id": bid, "status": "ready", "project_id": None}).sort("created_at", 1).to_list(MAX_FILES):
        claimed = await db.imports.find_one_and_update({"_id": d["_id"], "status": "ready", "project_id": None}, {"$set": {"status": "creating"}})
        if not claimed:
            continue
        dr = d.get("draft") or {}
        dup = d.get("duplicate") or await find_duplicate(dr)  # fresh check also catches earlier items created in this run
        reason = dup["reason"] if dup else ("Reference or address missing from the report" if not dr.get("ref") or not dr.get("address")
                                            else "No NCs found in the report" if not dr.get("ncs") else None)
        if reason:
            await db.imports.update_one({"_id": d["_id"]}, {"$set": {"status": "skipped", "duplicate": dup or {"reason": reason}}})
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
    await audit("import.bulk_confirmed", u.id, f"bulk_import:{bid}", {"created": len(created), "skipped": len(skipped)})
    return {"created": created, "skipped": skipped}
