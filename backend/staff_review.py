"""Phase 5 — staff review of location-aware evidence. Disabled unless Settings.staff_review_enabled is true.
Staff-only. Never changes an NC decision: corrections are metadata (files.classification.staff, ncs.scope method=staff) that Phase 3
assessments and Phase 4 PDFs read. Source photos and upload history are never altered or deleted.
Photo corrections reuse PUT /api/evidence/{fid}/classification (photo_class.py) and PUT /api/evidence/{fid}/pdf-include (server.py)."""
import re
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from auth import require
from core import User, audit, db, get_settings, now_iso, oid

router = APIRouter(prefix="/api")
staff = require("admin", "employee")
FIELDS = ("location_id", "location_label", "location_group", "location_uncertain", "component", "stage", "relevant", "dup_of", "source")


async def _on():
    if not (await get_settings()).staff_review_enabled:
        raise HTTPException(409, "Staff evidence review is not enabled")


async def review(nc_id: str) -> dict:
    from coverage import needs_verification, verify
    nc = await db.ncs.find_one({"_id": oid(nc_id)})
    files = await db.files.find({"nc_id": nc_id, "kind": "photo", "status": {"$in": ["draft", "submitted"]}},
                                {"classification": 1, "pdf_include": 1, "stage": 1, "stage_source": 1, "status": 1, "kind": 1, "checklist_item": 1}).sort("server_received_at", 1).to_list(500)
    sc = nc.get("scope")
    if not sc:  # preview of the rule-based scope only (no AI, nothing stored)
        from smart_pdf import scope_for
        sc = scope_for(nc)
    photos = []
    names = {l["id"]: l["label"] for l in sc.get("locations") or []}
    for f in files:
        c = f.get("classification") or {}
        photos.append({"id": str(f["_id"]), "classified": bool(c), "staff": sorted(k for k in (c.get("staff") or {}) if k not in ("by", "at")),
                       "pdf_include": f.get("pdf_include"), "checklist_item": f.get("checklist_item"), **{k: c.get(k) for k in FIELDS}})
        if photos[-1]["location_id"] and not photos[-1]["location_label"]:
            photos[-1]["location_label"] = names.get(photos[-1]["location_id"])  # label from the (previewed) scope
    return {"enabled": True, "scope": sc, "coverage": verify(sc, files, needs_verification(nc)), "photos": photos,
            "unclassified": sum(1 for p in photos if not p["classified"])}


@router.get("/ncs/{nc_id}/evidence-review")
async def get_review(nc_id: str, u: User = Depends(staff)):
    from server import get_nc
    await get_nc(nc_id, u)
    if not (await get_settings()).staff_review_enabled:
        return {"enabled": False}
    return await review(nc_id)


class ClassifyIn(BaseModel):
    force: bool = False  # re-run even when every photo is already tagged (staff fields are still kept)


@router.post("/ncs/{nc_id}/evidence-review/classify")
async def classify_on_demand(nc_id: str, body: ClassifyIn, u: User = Depends(staff)):
    """Explicit staff action for existing photos (no installer re-upload). Skips the AI when nothing is new unless forced."""
    import photo_class
    from scope import ensure_scope
    from server import get_nc
    await get_nc(nc_id, u)
    await _on()
    cur = await review(nc_id)
    if not cur["photos"]:
        raise HTTPException(400, "No photos to classify")
    ran = False
    if cur["unclassified"] or body.force:
        nc = await db.ncs.find_one({"_id": oid(nc_id)}, {"scope": 1})
        if not nc.get("scope"):
            await ensure_scope(nc_id, force=True)  # AI scope call only when the wording gives no count
        res = await photo_class.classify_nc(nc_id, loader=photo_class._load, ai=photo_class.ai_classify)
        if not res:
            raise HTTPException(503, "Photo classification is unavailable right now. Existing tags were kept.")
        ran = True
        await audit("nc.photos_classified", u.id, f"nc:{nc_id}", {"on_demand": True, "force": body.force})
    return {**await review(nc_id), "ran": ran}


class ScopeIn(BaseModel):
    expected_count: int = Field(ge=1, le=60)
    labels: List[str] = Field(default=[], max_length=60)
    basis: str = Field(min_length=5, max_length=300)  # where the count comes from (NC wording / TrustMark report page)


@router.put("/ncs/{nc_id}/scope")
async def staff_scope(nc_id: str, body: ScopeIn, u: User = Depends(staff)):
    """Staff-confirmed expected locations. Kept by every later extraction (method=staff); the replaced scope is kept as history."""
    from scope import LABEL, nc_key
    from server import get_nc
    await get_nc(nc_id, u)
    await _on()
    labels = [re.sub(r"\s+", " ", x).strip()[:60] for x in body.labels if x.strip()]
    if labels and len(labels) != body.expected_count:
        raise HTTPException(400, "Number of location names must match the expected count")
    if len(set(x.lower() for x in labels)) != len(labels):
        raise HTTPException(400, "Location names must be unique")
    nc = await db.ncs.find_one({"_id": oid(nc_id)})
    old = nc.get("scope") or {}
    stype = old.get("scope_type") or "other"
    labels = labels or [old["locations"][i]["label"] if i < len(old.get("locations") or []) else f"{LABEL.get(stype, 'Location')} {i + 1}"
                        for i in range(body.expected_count)]
    t = " ".join(x for x in [nc.get("original_text") or "", nc.get("description") or "", nc.get("required_work") or ""] if x)
    sc = {"v": 1, "nc_key": nc_key(nc.get("original_text") or nc.get("description") or ""), "scope_type": stype, "quantifier": "explicit",
          "expected_count": body.expected_count, "locations": [{"id": f"loc-{i + 1}", "label": lab, "source": "staff"} for i, lab in enumerate(labels)],
          "scope_status": "confirmed", "basis": [body.basis.strip()], "sources": [{"type": "staff", "id": u.id}], "method": "staff", "model": None,
          "extracted_at": now_iso(), "text_hash": nc_key(t), "by": u.id,
          "history": ((old.get("history") or []) + [{k: v for k, v in old.items() if k != "history"}])[-10:] if old else []}
    await db.ncs.update_one({"_id": nc["_id"]}, {"$set": {"scope": sc}})
    await audit("nc.scope_confirmed", u.id, f"nc:{nc_id}", {"expected_count": body.expected_count, "labels": labels, "basis": body.basis.strip()})
    return await review(nc_id)
