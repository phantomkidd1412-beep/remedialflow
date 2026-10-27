"""Explicit staff evidence selection per NC (ordered). ncs.evidence_selection = {ids: [file ids in PDF order], ai_ids: [...], seq, at, by}.
Field ABSENT = never selected -> legacy PDF behaviour unchanged. ids == [] = deliberately empty -> no evidence photos in the PDF.
Saving never calls AI. Auto Select (switch ON only) reuses the cached Phase 3 recommendations (at most one AI call)."""
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from auth import require
from core import User, audit, db, now_iso, oid

router = APIRouter(prefix="/api")
staff = require("admin", "employee")


async def _nc(nc_id: str, u: User) -> dict:
    from server import get_nc
    await get_nc(nc_id, u)
    return await db.ncs.find_one({"_id": oid(nc_id)})


async def _photo_ids(nc_id: str) -> dict:
    fs = await db.files.find({"nc_id": nc_id, "kind": "photo", "status": {"$ne": "superseded"}}, {"checklist_item": 1}).to_list(5000)
    return {str(f["_id"]): f for f in fs}


class SelectionIn(BaseModel):
    ids: List[str] = Field(max_length=500)
    seq: int = 0  # client save counter: an older, late-arriving save never overwrites a newer one


async def _learn(nc: dict, before: dict, after: List[str], photos: dict, u: User):
    """Feedback for future recommendations (no AI): AI picks staff removed, photos staff added beyond the AI picks."""
    ai = set(before.get("ai_ids") or [])
    old = set(before.get("ids") or [])
    prof = ((nc.get("photo_recs") or {}).get("profile"))
    rows = []
    for fid in old - set(after):
        if fid in ai:
            item = (photos.get(fid) or {}).get("checklist_item")
            rows.append({"action": "deselect_ai", "rejected_file_id": fid, "file_id": None, "item_id": item})
    for fid in set(after) - old - ai:
        rows.append({"action": "select_staff", "file_id": fid, "item_id": (photos.get(fid) or {}).get("checklist_item")})
    if rows:
        ts = now_iso()
        await db.learning_examples.insert_many([{**r, "nc_id": str(nc["_id"]), "profile": prof, "by": u.id, "at": ts, "source": "evidence_selection"} for r in rows])


@router.put("/ncs/{nc_id}/evidence-selection")
async def save_selection(nc_id: str, body: SelectionIn, u: User = Depends(staff)):
    """Replace the whole ordered selection (idempotent, safe to retry). Unknown/superseded photo ids are dropped."""
    nc = await _nc(nc_id, u)
    cur = nc.get("evidence_selection") or {}
    if cur and body.seq and body.seq < (cur.get("seq") or 0):
        return {**cur, "stale": True}
    photos = await _photo_ids(nc_id)
    ids = [i for i in dict.fromkeys(body.ids) if i in photos]
    sel = {"ids": ids, "ai_ids": [i for i in cur.get("ai_ids") or [] if i in ids], "seq": max(body.seq, cur.get("seq") or 0), "at": now_iso(), "by": u.id}
    await db.ncs.update_one({"_id": nc["_id"]}, {"$set": {"evidence_selection": sel}})
    removed = [i for i in cur.get("ids") or [] if i not in ids]
    if removed:  # a deselected photo is no longer evidence for a checklist requirement
        await db.files.update_many({"_id": {"$in": [oid(i) for i in removed]}, "checklist_item": {"$ne": None}}, {"$set": {"checklist_item": None}})
        recs = (nc.get("photo_recs") or {}).get("items") or {}
        for item_id, r in recs.items():  # deselected AI suggestion = rejected for that requirement (not suggested again)
            if r.get("file_id") in removed and r.get("file_id") in (cur.get("ai_ids") or []):
                await db.files.update_one({"_id": oid(r["file_id"])}, {"$addToSet": {"rejected_items": item_id}})
                await db.ncs.update_one({"_id": nc["_id"]}, {"$set": {f"photo_recs.items.{item_id}.state": "rejected", f"photo_recs.items.{item_id}.file_id": None,
                                                                      f"photo_recs.items.{item_id}.reason": "Removed by staff"}})
    await _learn(nc, cur, ids, photos, u)
    if set(cur.get("ids") or []) != set(ids) or not cur:
        await audit("evidence.selection", u.id, f"nc:{nc_id}", {"count": len(ids), "added": len(set(ids) - set(cur.get("ids") or [])), "removed": len(removed)})
    return sel


class AutoIn(BaseModel):
    mode: str = Field(default="replace", pattern="^(replace|add)$")


@router.post("/ncs/{nc_id}/evidence-selection/auto")
async def auto_select(nc_id: str, body: AutoIn, u: User = Depends(staff)):
    """One strong photo per checklist requirement from the (cached) recommendations; missing requirements are listed,
    never filled with unsuitable photos. Never approves anything. Staff-marked requirements keep their staff photo."""
    import checklist
    import recommend
    from staff_review import _on
    nc = await _nc(nc_id, u)
    await _on()
    cl = await checklist.ensure_checklist(nc_id)  # approved profile = no AI; otherwise one AI draft
    if not cl:
        raise HTTPException(503, "Could not prepare an evidence checklist for this NC")
    recs = await recommend.build(nc_id, allow_draft=True)
    view = await recommend.view(nc_id)
    picks, missing = [], []
    for it in view["items"]:
        if it["file_id"]:
            picks.append((it["item_id"], it["file_id"], it["source"]))
        else:
            missing.append(it["title"])
    for item_id, fid, src in picks:  # mark like an Accept so checklist coverage agrees with the selection
        if src != "staff":
            await recommend._mark(fid, item_id)
            await db.ncs.update_one({"_id": nc["_id"]}, {"$set": {f"photo_recs.items.{item_id}.state": "accepted"}})
    cur = nc.get("evidence_selection") or {}
    new = [fid for _, fid, _ in picks]
    ids = list(dict.fromkeys((cur.get("ids") or []) + new)) if body.mode == "add" else list(dict.fromkeys(new))
    ai = [fid for _, fid, src in picks if src in ("rule", "ai")]
    sel = {"ids": ids, "ai_ids": list(dict.fromkeys([i for i in cur.get("ai_ids") or [] if i in ids] + ai)), "seq": (cur.get("seq") or 0) + 1, "at": now_iso(), "by": u.id}
    await db.ncs.update_one({"_id": nc["_id"]}, {"$set": {"evidence_selection": sel}})
    await audit("evidence.auto_select", u.id, f"nc:{nc_id}", {"mode": body.mode, "count": len(ids), "missing": len(missing), "ai_used": recs.get("ai_used")})
    flags = sorted({f for it in view["items"] for f in it["flags"] if "Measurement" in f})
    return {"selection": sel, "missing": missing, "checklist_status": cl.get("status"), "ai_used": bool(recs.get("ai_used")),
            "review_notes": (["Safety-critical NC: staff must review the evidence"] if nc.get("safety_critical") else []) + flags}


def selected_photos(n: dict, photos: List[dict]) -> Optional[List[dict]]:
    """None = no explicit selection (legacy behaviour). [] = deliberately empty. Otherwise photos in the staff order."""
    sel = n.get("evidence_selection")
    if not isinstance(sel, dict) or "ids" not in sel:
        return None
    by = {str(f["_id"]): f for f in photos if f.get("kind") == "photo" and f.get("status") != "superseded"}
    return [by[i] for i in sel["ids"] if i in by]
