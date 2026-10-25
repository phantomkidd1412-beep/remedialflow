"""AI Evidence Guidance Library: admin-approved contextual instructions given to Gemini per NC type.
This does not retrain the model; only APPROVED entries are used, never overriding safety/sign-off rules."""
import io
import re
from typing import List, Optional

from bson import ObjectId
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from PIL import Image, ImageOps
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from auth import require
from core import User, audit, db, file_url, now_iso, oid
from storage import SYSTEM, storage

router = APIRouter(prefix="/api")
admin = require("admin")
CONTENT = ("title", "measure", "keywords", "defect", "evidence_required", "photos_to_collect", "photo_details",
           "video_guidance", "insufficient_examples", "request_example", "references", "review_notes", "scope_type")


class GuidanceIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)  # NC category / defect type
    measure: str = Field(default="", max_length=100)  # EWI, loft insulation, ventilation, heating, condensate...
    keywords: List[str] = Field(default=[], max_length=30)
    defect: str = Field(default="", max_length=2000)
    evidence_required: str = Field(default="", max_length=3000)
    photos_to_collect: str = Field(default="", max_length=3000)
    photo_details: str = Field(default="", max_length=2000)
    video_guidance: str = Field(default="", max_length=1000)
    insufficient_examples: str = Field(default="", max_length=2000)
    request_example: str = Field(default="", max_length=1000)
    references: str = Field(default="", max_length=2000)
    review_notes: str = Field(default="", max_length=2000)
    # Location-aware scope hint (optional; "" = not set). Used only when Settings.location_scope_enabled.
    scope_type: str = Field(default="", pattern="^(|window|habitable_room|wet_room|elevation|junction|component|other|single)$")


def _clean(body: GuidanceIn) -> dict:
    d = body.model_dump()
    d["keywords"] = sorted({k.strip().lower()[:60] for k in d["keywords"] if k.strip()})
    return {k: (v.strip() if isinstance(v, str) else v) for k, v in d.items()}


async def _names(ids) -> dict:
    ids = [ObjectId(i) for i in ids if i and ObjectId.is_valid(i)]
    return {str(u["_id"]): u["name"] for u in await db.users.find({"_id": {"$in": ids}}, {"name": 1}).to_list(200)}


def _public(g: dict, names: dict) -> dict:
    _id = g.pop("_id")
    g = {**g, "id": str(_id)}
    g.pop("history", None)
    for k in ("created_by", "approved_by", "updated_by"):
        g[f"{k}_name"] = names.get(g.get(k) or "")
    g["example_photos"] = [{"id": i, "url": file_url(i, "display")} for i in g.get("example_photo_ids") or []]
    return g


async def _get(gid: str) -> dict:
    g = await db.guidance.find_one({"_id": oid(gid), "deleted": {"$ne": True}})
    if not g:
        raise HTTPException(404, "Guidance entry not found")
    return g


@router.get("/admin/guidance")
async def list_guidance(status: Optional[str] = None, u: User = Depends(admin)):
    q: dict = {"deleted": {"$ne": True}}
    if status in ("draft", "approved", "disabled"):
        q["status"] = status
    items = await db.guidance.find(q).sort("updated_at", -1).to_list(1000)
    names = await _names({x.get(k) for x in items for k in ("created_by", "approved_by", "updated_by")})
    return [_public(x, names) for x in items]


@router.get("/admin/guidance/{gid}")
async def get_guidance(gid: str, u: User = Depends(admin)):
    g = await _get(gid)
    hist = g.get("history") or []
    names = await _names({g.get("created_by"), g.get("approved_by"), g.get("updated_by"), *[h.get("by") for h in hist]})
    out = _public(dict(g), names)
    out["history"] = [{"version": h["version"], "at": h["at"], "by_name": names.get(h.get("by") or ""), "change": h.get("change")} for h in hist][::-1]
    return out


@router.post("/admin/guidance")
async def create_guidance(body: GuidanceIn, u: User = Depends(admin)):
    ts = now_iso()
    doc = {**_clean(body), "status": "draft", "version": 1, "example_photo_ids": [], "source": {"type": "admin"},
           "created_by": u.id, "created_at": ts, "updated_by": u.id, "updated_at": ts, "approved_by": None, "approved_at": None,
           "history": [{"version": 1, "at": ts, "by": u.id, "change": "created"}]}
    r = await db.guidance.insert_one(doc)
    await audit("guidance.created", u.id, f"guidance:{r.inserted_id}", {"title": doc["title"]})
    return {"id": str(r.inserted_id)}


@router.put("/admin/guidance/{gid}")
async def edit_guidance(gid: str, body: GuidanceIn, u: User = Depends(admin)):
    g = await _get(gid)
    new = _clean(body)
    if all((g.get(k) if g.get(k) is not None else ("" if k == "scope_type" else None)) == new[k] for k in CONTENT):
        return {"ok": True, "version": g.get("version", 1)}
    v, ts = g.get("version", 1) + 1, now_iso()
    snapshot = {k: g.get(k) for k in CONTENT}  # previous content kept for audit
    await db.guidance.update_one({"_id": g["_id"]}, {"$set": {**new, "version": v, "updated_by": u.id, "updated_at": ts},
                                 "$push": {"history": {"version": v, "at": ts, "by": u.id, "change": "edited", "previous": snapshot}}})
    await audit("guidance.edited", u.id, f"guidance:{gid}", {"version": v})
    return {"ok": True, "version": v}


class StatusIn(BaseModel):
    status: str = Field(pattern="^(draft|approved|disabled)$")


@router.post("/admin/guidance/{gid}/status")
async def set_status(gid: str, body: StatusIn, u: User = Depends(admin)):
    g = await _get(gid)
    if g.get("status") == body.status:
        return {"ok": True, "status": body.status}
    ts = now_iso()
    upd = {"status": body.status, "updated_by": u.id, "updated_at": ts}
    if body.status == "approved":
        upd.update(approved_by=u.id, approved_at=ts)
    await db.guidance.update_one({"_id": g["_id"]}, {"$set": upd, "$push": {"history": {"version": g.get("version", 1), "at": ts, "by": u.id, "change": body.status}}})
    await audit(f"guidance.{body.status}", u.id, f"guidance:{gid}", {"version": g.get("version", 1)})
    return {"ok": True, "status": body.status}


@router.delete("/admin/guidance/{gid}")
async def delete_guidance(gid: str, u: User = Depends(admin)):
    """Removed from the library and from AI use; the record is retained so past AI decisions stay auditable."""
    g = await _get(gid)
    await db.guidance.update_one({"_id": g["_id"]}, {"$set": {"deleted": True, "status": "disabled", "deleted_by": u.id, "deleted_at": now_iso()}})
    await audit("guidance.deleted", u.id, f"guidance:{gid}", {"title": g.get("title")})
    return {"ok": True}


def _jpeg(data: bytes) -> bytes:
    img = ImageOps.exif_transpose(Image.open(io.BytesIO(data))).convert("RGB")  # re-encode: strips EXIF/GPS
    img.thumbnail((1280, 1280))
    out = io.BytesIO()
    img.save(out, "JPEG", quality=85)
    return out.getvalue()


@router.post("/admin/guidance/{gid}/photos")
async def add_photo(gid: str, file: UploadFile = File(...), u: User = Depends(admin)):
    g = await _get(gid)
    if len(g.get("example_photo_ids") or []) >= 6:
        raise HTTPException(400, "Up to 6 example photos per entry")
    raw = await file.read()
    if len(raw) > 25 * 1024 * 1024:
        raise HTTPException(413, "File too large")
    try:
        jpg = await run_in_threadpool(_jpeg, raw)
    except Exception:
        raise HTTPException(400, "Please upload a JPG, PNG or WEBP photo")
    fid = ObjectId()
    path = await storage.aput(f"{SYSTEM}/guidance/{gid}/{fid}.jpg", jpg, "image/jpeg")
    await db.files.insert_one({"_id": fid, "kind": "guidance", "guidance_id": gid, "project_id": None, "nc_id": None, "status": "submitted",
                               "mime": "image/jpeg", "paths": {"original": path, "display": path}, "uploaded_by": u.id, "server_received_at": now_iso()})
    await db.guidance.update_one({"_id": g["_id"]}, {"$push": {"example_photo_ids": str(fid)}, "$set": {"updated_at": now_iso(), "updated_by": u.id}})
    await audit("guidance.photo_added", u.id, f"guidance:{gid}", {"file_id": str(fid)})
    return {"id": str(fid), "url": file_url(str(fid), "display")}


@router.delete("/admin/guidance/{gid}/photos/{fid}")
async def remove_photo(gid: str, fid: str, u: User = Depends(admin)):
    g = await _get(gid)
    await db.guidance.update_one({"_id": g["_id"]}, {"$pull": {"example_photo_ids": fid}, "$set": {"updated_at": now_iso(), "updated_by": u.id}})
    await audit("guidance.photo_removed", u.id, f"guidance:{gid}", {"file_id": fid})  # stored file retained
    return {"ok": True}


# ---------------- used by the assessment + review flows ----------------
def _hits(term: str, text: str) -> bool:
    return bool(term) and re.search(r"\b" + re.escape(term) + r"\b", text, re.I) is not None


async def match_guidance(nc_text: str, limit: int = 3) -> List[dict]:
    """Approved entries whose keywords (or, without keywords, measure) appear in the NC wording. Best 3."""
    scored = []
    for g in await db.guidance.find({"status": "approved", "deleted": {"$ne": True}}).to_list(1000):
        kw = sum(1 for k in g.get("keywords") or [] if _hits(k, nc_text))
        measure = _hits((g.get("measure") or "").strip(), nc_text)
        if kw or (measure and not g.get("keywords")):
            scored.append((kw * 2 + int(measure), g.get("approved_at") or "", g))
    scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
    return [{**g, "id": str(g["_id"])} for _, _, g in scored[:limit]]


def guidance_text(guides: List[dict]) -> str:
    parts = []
    labels = [("measure", "Measure"), ("defect", "Defect"), ("evidence_required", "Evidence required"),
              ("photos_to_collect", "Photographs to collect"), ("photo_details", "Must be visible in photographs"),
              ("video_guidance", "When video is useful/necessary"), ("insufficient_examples", "Insufficient evidence examples"),
              ("request_example", "Example request when insufficient"), ("references", "Technical references"),
              ("review_notes", "Employee review required when")]
    for i, g in enumerate(guides, 1):
        lines = [f"[Guidance {i}: {g.get('title', '')} (v{g.get('version', 1)})]"]
        lines += [f"{lab}: {g[k]}" for k, lab in labels if g.get(k)]
        parts.append("\n".join(lines))
    return "\n\n".join(parts)


async def suggest_from_review(nc: dict, action: str, reason: str, u: User) -> str:
    """Employee feedback -> DRAFT guidance entry. Never used by the AI until an admin approves it.
    Only the NC wording and the reviewer's reason are copied (no customer address or evidence)."""
    ts = now_iso()
    text = (nc.get("original_text") or nc.get("description") or "").strip()
    doc = {"title": (nc.get("description") or "Suggested guidance")[:200], "measure": "", "keywords": [],
           "defect": text[:2000], "evidence_required": "", "photos_to_collect": "", "photo_details": "", "video_guidance": "",
           "insufficient_examples": reason[:2000] if action in ("reject", "more_evidence") else "",
           "request_example": (nc.get("review_note") or "")[:1000] if action == "more_evidence" else "",
           "references": "", "review_notes": reason[:2000] if action not in ("reject", "more_evidence") else "",
           "status": "draft", "version": 1, "example_photo_ids": [],
           "source": {"type": "employee_feedback", "nc_id": str(nc["_id"]), "action": action, "reason": reason[:1000]},
           "created_by": u.id, "created_at": ts, "updated_by": u.id, "updated_at": ts, "approved_by": None, "approved_at": None,
           "history": [{"version": 1, "at": ts, "by": u.id, "change": f"suggested from review ({action})"}]}
    r = await db.guidance.insert_one(doc)
    await audit("guidance.suggested", u.id, f"guidance:{r.inserted_id}", {"nc_id": str(nc["_id"]), "action": action})
    return str(r.inserted_id)
