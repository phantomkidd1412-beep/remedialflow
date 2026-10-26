"""Knowledge Index Phase 3 - AI photo recommendations from staff-approved NC profiles. Staff-triggered only; cached.
Behind the Location-Aware Evidence switch (staff_review_enabled). Staff/admin only; installer app unchanged.

ncs.photo_recs = {at, fp (cache fingerprint), profile {id,key,version} | None, ai_used, items: {item_id: {file_id, source rule|ai|none,
                  reason, uncertain, state pending|accepted|rejected|replaced, candidates}}}
A recommendation is only a suggestion: it never marks a photo, never changes PDFs/coverage until staff ACCEPT it (which stores a normal
staff marking), and never counts as proof that a requirement or measurement is met. Staff-marked photos always win.
Corrections are learning examples; 'Propose profile update' makes a NEW proposed profile version (good + bad examples) that only
influences future NCs after an admin approves it."""
import base64
import hashlib
import json
import logging
import os
import re
import uuid
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from auth import require
from core import User, audit, db, now_iso, oid
from storage import storage

log = logging.getLogger("recommend")
router = APIRouter(prefix="/api")
staff = require("admin", "employee")
MAX_PHOTOS, MAX_EXAMPLES = 12, 4


async def _photos(nc_id: str) -> List[dict]:
    fs = await db.files.find({"nc_id": nc_id, "kind": "photo", "status": {"$in": ["draft", "submitted"]}}).sort("server_received_at", 1).to_list(500)
    return [f for f in fs if f.get("pdf_include") is not False]


async def _profile(nc: dict) -> Optional[dict]:
    """The APPROVED profile for this NC (its checklist's profile if still approved, else the best approved match)."""
    from checklist import match_profile
    from smart_pdf import scope_for
    ref = (nc.get("checklist") or {}).get("profile") or {}
    if ref.get("id"):
        p = await db.nc_profiles.find_one({"_id": oid(ref["id"]), "status": "approved"})
        if p:
            return p
        p = await db.nc_profiles.find_one({"key": ref.get("key"), "status": "approved"})
        if p:
            return p
    m = await match_profile(nc, scope_for(nc))
    return m["profile"] if m else None


def _fp(cl: dict, photos: List[dict], prof: Optional[dict]) -> str:
    """Cache key: checklist version, photos AND their current tags (staff stage/location corrections invalidate the cache)."""
    tags = sorted((str(f["_id"]), _stage(f), (f.get("classification") or {}).get("location_id"), (f.get("classification") or {}).get("relevant"),
                   (f.get("classification") or {}).get("dup_of")) for f in photos)
    raw = json.dumps([cl.get("v"), [i["id"] for i in cl["items"]], sorted(cl.get("held") or []), tags, prof and [str(prof["_id"]), prof.get("version")]], default=str)
    return hashlib.sha256(raw.encode()).hexdigest()[:20]


async def _rejected(nc_id: str) -> dict:
    out = {}
    async for e in db.learning_examples.find({"nc_id": nc_id, "action": {"$in": ["reject_rec", "replace_rec"]}}):
        if e.get("rejected_file_id"):
            out.setdefault(e["item_id"], set()).add(e["rejected_file_id"])
    return out


def _stage(f: dict) -> Optional[str]:
    c = f.get("classification") or {}
    return (c.get("staff") or {}).get("stage") or c.get("stage")


def rule_pick(item: dict, photos: List[dict], rejected: set) -> dict:
    """Free rules: unmarked, relevant, non-duplicate photos whose stored stage/location match the requirement."""
    cands = []
    for f in photos:
        c = f.get("classification") or {}
        if f.get("checklist_item") or str(f["_id"]) in rejected or c.get("relevant") is False or c.get("dup_of"):
            continue
        if _stage(f) == item["stage"] and (not item.get("locations") or c.get("location_id") in item["locations"]):
            cands.append(str(f["_id"]))
    if len(cands) == 1:
        return {"file_id": cands[0], "source": "rule", "reason": "Only unmarked photo classified for this stage" + (" and location" if item.get("locations") else ""),
                "uncertain": False, "candidates": cands}
    return {"file_id": None, "source": "none", "reason": "", "uncertain": True, "candidates": cands}


REC_SYSTEM = """You help staff choose evidence photos for ONE building non-compliance (NC) remediation. For each checklist requirement,
choose the uploaded photo (by number) that best shows it, or null if none clearly does. EXAMPLE photos come from staff-approved past NCs and
only illustrate the kind of view wanted; BAD EXAMPLE photos show views staff rejected. Examples are NOT evidence for this NC.
Visual similarity to an example does not prove the requirement is met. Never state or estimate a measurement; never say work is compliant.
Use the document extracts only as context. reason: <= 18 words describing what the chosen photo shows. uncertain: true when the photo
only partly shows the requirement or you are unsure.
Respond with ONLY JSON: {"picks": [{"item": "<item id>", "photo": <number or null>, "reason": "...", "uncertain": true|false}]}"""


async def _bytes(f: dict) -> Optional[bytes]:
    p = (f.get("paths") or {})
    path = p.get("display") or p.get("stamped") or p.get("original")
    if not path:
        return None
    try:
        return (await storage.aget(path))[0]
    except Exception:
        return None


async def ai_pick(nc: dict, items: List[dict], photos: List[dict], examples: List[tuple], docs_text: str) -> Optional[dict]:
    """ONE AI call for the requirements the rules could not settle. examples: [(label, bytes)]. -> {item_id: pick}"""
    from emergentintegrations.llm.chat import ImageContent, LlmChat, UserMessage
    try:
        imgs, lines = [], []
        for i, f in enumerate(photos):
            b = await _bytes(f)
            if b:
                imgs.append(b)
                lines.append(f"Photo {i}: stage tag {_stage(f) or 'unknown'}, captured {f.get('captured_at') or f.get('server_received_at') or 'unknown'}")
        if not imgs:
            return None
        ex_lines = [f"Image {len(imgs) + k + 1} is {lab}" for k, (lab, _) in enumerate(examples)]
        chat = LlmChat(api_key=os.environ["EMERGENT_LLM_KEY"], session_id=f"recs-{uuid.uuid4()}", system_message=REC_SYSTEM).with_model(
            os.environ.get("AI_PROVIDER", "gemini"), os.environ.get("AI_MODEL", "gemini-3-flash-preview"))
        req = "\n".join(f"- id {it['id']}: [{it['stage']}] {it['title']}" + (f" (measurement shown: {it['measurement']})" if it.get("measurement") else "") for it in items)
        text = (f"NC: {' '.join(x for x in [nc.get('original_text') or nc.get('description') or '', nc.get('required_work') or ''] if x)[:2000]}\n"
                f"Requirements:\n{req}\nThe first {len(imgs)} images are this NC's uploaded photos:\n" + "\n".join(lines) + "\n"
                + ("\n".join(ex_lines) + "\n" if ex_lines else "") + (docs_text[:2500] if docs_text else ""))
        out = await chat.send_message(UserMessage(text=text, file_contents=[ImageContent(image_base64=base64.b64encode(b).decode()) for b in imgs + [b for _, b in examples]]))
        m = re.search(r"\{.*\}", out if isinstance(out, str) else str(out), re.S)
        res = {}
        for p in (json.loads(m.group(0)) if m else {}).get("picks") or []:
            n = p.get("photo")
            res[str(p.get("item"))] = {"index": n if isinstance(n, int) and 0 <= n < len(photos) else None,
                                       "reason": " ".join(str(p.get("reason") or "").split()[:20]), "uncertain": bool(p.get("uncertain"))}
        return res
    except Exception as e:
        log.warning("photo recommendation failed: %s", type(e).__name__)
        return None


async def _examples(prof: Optional[dict], items: List[dict]) -> List[tuple]:
    """Up to MAX_EXAMPLES images from the APPROVED profile: one good example per requirement first, then bad examples."""
    if not prof:
        return []
    out = []
    for kind, label in (("examples", "an approved EXAMPLE of"), ("bad_examples", "a BAD EXAMPLE (rejected by staff) for")):
        for it in items:
            for fid in ((prof.get(kind) or {}).get(it["id"]) or [])[:1]:
                if len(out) >= MAX_EXAMPLES:
                    return out
                f = await db.files.find_one({"_id": oid(fid)})
                b = f and await _bytes(f)
                if b:
                    out.append((f"{label} requirement {it['id']}", b))
    return out


def flags(item: dict, rec: dict, marked: bool, prof: Optional[dict]) -> List[str]:
    """Reasons this requirement still needs staff attention (shown in the panel; recommendations never approve anything)."""
    out = []
    if marked:
        return out
    if not rec.get("file_id"):
        out.append("No suitable photo found")
    elif rec.get("uncertain"):
        out.append("AI is unsure this photo shows the requirement")
    if item["stage"] == "verification" or item.get("measurement"):
        out.append("Measurement/test must be checked by staff on the photo")
    if not prof or not (prof.get("examples") or {}).get(item["id"]):
        out.append("No approved example photo for this requirement")
    return out


async def build(nc_id: str, force: bool = False, ai=None) -> dict:
    nc = await db.ncs.find_one({"_id": oid(nc_id)})
    cl = nc.get("checklist") or {}
    if cl.get("status") not in ("confirmed", "auto"):
        raise HTTPException(400, "Confirm the evidence checklist first")
    photos = await _photos(nc_id)
    if not photos:
        raise HTTPException(400, "No photos to recommend from")
    prof = await _profile(nc)
    fp = _fp(cl, photos, prof)
    cur = nc.get("photo_recs")
    if cur and cur.get("fp") == fp and not force:
        return cur  # cached: no AI call
    rejected = await _rejected(nc_id)
    marked = {f.get("checklist_item") for f in photos if f.get("checklist_item")}
    keep = {k: v for k, v in ((cur or {}).get("items") or {}).items() if v.get("state") in ("accepted", "replaced")}
    items, todo = {}, []
    held = set(cl.get("held") or [])
    for it in cl["items"]:
        if it["id"] in marked or it["id"] in keep:
            items[it["id"]] = keep.get(it["id"]) or {"file_id": None, "source": "staff", "reason": "Marked by staff", "uncertain": False, "state": "accepted", "candidates": []}
            continue
        if it["id"] in held:  # staff rejected the suggestion: no new suggestion until staff choose a photo
            items[it["id"]] = {"file_id": None, "source": "none", "reason": "Rejected by staff; choose a photo", "uncertain": False, "state": "rejected", "candidates": []}
            continue
        r = rule_pick(it, photos, rejected.get(it["id"], set()))
        items[it["id"]] = {**r, "state": "pending"}
        if not r["file_id"]:
            todo.append(it)
    ai_used = False
    if todo:
        pool = [f for f in photos if not f.get("checklist_item")][:MAX_PHOTOS]
        if pool:
            docs = ""
            try:
                import documents
                docs = documents.refs_text({"passages": await documents.retrieve(documents.nc_text(nc))})
            except Exception:
                pass
            picks = await (ai or ai_pick)(nc, todo, pool, await _examples(prof, todo), docs)
            ai_used = picks is not None
            for it in todo:
                p = (picks or {}).get(it["id"])
                if p and p["index"] is not None and str(pool[p["index"]]["_id"]) not in rejected.get(it["id"], set()):
                    items[it["id"]].update(file_id=str(pool[p["index"]]["_id"]), source="ai", reason=p["reason"] or "Suggested by AI", uncertain=p["uncertain"])
                elif picks is None:
                    items[it["id"]]["reason"] = "AI unavailable; choose a photo manually"
    recs = {"at": now_iso(), "fp": fp, "ai_used": ai_used, "items": items,
            "profile": prof and {"id": str(prof["_id"]), "key": prof["key"], "version": prof["version"], "name": prof.get("name")}}
    await db.ncs.update_one({"_id": nc["_id"]}, {"$set": {"photo_recs": recs}})
    return recs


async def view(nc_id: str) -> dict:
    nc = await db.ncs.find_one({"_id": oid(nc_id)}, {"checklist": 1, "photo_recs": 1})
    cl, recs = nc.get("checklist") or {}, nc.get("photo_recs")
    photos = await _photos(nc_id)
    staff_m = {}
    for f in photos:
        if f.get("checklist_item"):
            staff_m.setdefault(f["checklist_item"], str(f["_id"]))
    prof = None
    if recs and recs.get("profile"):
        prof = await db.nc_profiles.find_one({"_id": oid(recs["profile"]["id"])})
    out = []
    for it in cl.get("items") or []:
        r = dict(((recs or {}).get("items") or {}).get(it["id"]) or {"file_id": None, "source": "none", "reason": "", "uncertain": True, "state": "pending"})
        if it["id"] in staff_m:  # staff marking always wins over any recommendation
            st = r.get("state") if r.get("state") in ("accepted", "replaced") else "accepted"
            r.update(file_id=staff_m[it["id"]], source="staff", state=st, uncertain=False,
                     reason="Chosen by staff" if st == "replaced" else "Accepted by staff" if r.get("state") == "accepted" and r.get("source") in ("rule", "ai") else "Marked by staff")
        out.append({"item_id": it["id"], "title": it["title"], "stage": it["stage"], "measurement": it.get("measurement") or "",
                    **{k: r.get(k) for k in ("file_id", "source", "reason", "uncertain", "state")},
                    "flags": flags(it, r, it["id"] in staff_m, prof)})
    return {"available": cl.get("status") in ("confirmed", "auto"), "at": (recs or {}).get("at"), "ai_used": (recs or {}).get("ai_used", False),
            "profile": (recs or {}).get("profile"), "items": out,
            "photos": [{"id": str(f["_id"]), "stage": _stage(f), "checklist_item": f.get("checklist_item")} for f in photos]}


# ---------------- staff endpoints ----------------
async def _nc(nc_id: str, u: User) -> dict:
    from checklist import _nc as nc_ok
    return await nc_ok(nc_id, u)


@router.get("/ncs/{nc_id}/recommendations")
async def get_recs(nc_id: str, u: User = Depends(staff)):
    await _nc(nc_id, u)
    return await view(nc_id)


class RunIn(BaseModel):
    force: bool = False


@router.post("/ncs/{nc_id}/recommendations")
async def run_recs(nc_id: str, body: RunIn, u: User = Depends(staff)):
    await _nc(nc_id, u)
    recs = await build(nc_id, body.force)
    await audit("nc.photos_recommended", u.id, f"nc:{nc_id}", {"ai_used": recs["ai_used"], "force": body.force})
    return await view(nc_id)


class ActIn(BaseModel):
    action: str = Field(pattern="^(accept|reject|replace)$")
    file_id: Optional[str] = None  # replace: the photo staff chose instead


async def _mark(fid: str, item_id: Optional[str]):
    """One photo per requirement: marking a photo for an item clears that item from the NC's other photos."""
    if item_id:
        f = await db.files.find_one({"_id": oid(fid)}, {"nc_id": 1})
        await db.files.update_many({"nc_id": f["nc_id"], "checklist_item": item_id, "_id": {"$ne": oid(fid)}}, {"$set": {"checklist_item": None}})
    await db.files.update_one({"_id": oid(fid)}, {"$set": {"checklist_item": item_id}})


@router.put("/ncs/{nc_id}/recommendations/{item_id}")
async def act(nc_id: str, item_id: str, body: ActIn, u: User = Depends(staff)):
    """Accept = store a normal staff marking. Reject = never suggest this photo again for this requirement. Replace = staff choose
    another photo (the suggested one is recorded as a rejected example). All are learning examples; profiles change only via proposal."""
    nc = await _nc(nc_id, u)
    cl = nc.get("checklist") or {}
    item = next((i for i in cl.get("items") or [] if i["id"] == item_id), None)
    if not item:
        raise HTTPException(404, "Unknown checklist item")
    recs = nc.get("photo_recs") or {"items": {}}
    rec = (recs.get("items") or {}).get(item_id) or {}
    photo_ids = {str(f["_id"]) for f in await _photos(nc_id)}
    suggested = rec.get("file_id")
    if body.action in ("accept", "reject") and suggested not in photo_ids:
        raise HTTPException(400, "There is no recommendation to " + body.action)
    if body.action == "replace" and body.file_id not in photo_ids:
        raise HTTPException(400, "Choose one of this NC's photos")
    ex = {"nc_id": nc_id, "item_id": item_id, "item_title": item["title"], "item_stage": item["stage"], "profile": recs.get("profile"),
          "rec_source": rec.get("source"), "by": u.id, "at": now_iso()}
    if body.action == "accept":
        await _mark(suggested, item_id)
        rec.update(state="accepted")
        ex.update(action="accept_rec", file_id=suggested)
    elif body.action == "reject":
        if (await db.files.find_one({"_id": oid(suggested)}, {"checklist_item": 1})).get("checklist_item") == item_id:
            await _mark(suggested, None)
        rec.update(state="rejected", file_id=None, reason="Rejected by staff")
        ex.update(action="reject_rec", file_id=None, rejected_file_id=suggested)
    else:
        await _mark(body.file_id, item_id)
        if suggested and suggested != body.file_id:
            ex["rejected_file_id"] = suggested
        rec.update(state="replaced", file_id=body.file_id, source="staff", reason="Chosen by staff", uncertain=False)
        ex.update(action="replace_rec", file_id=body.file_id)
    await db.learning_examples.insert_one(ex)
    if body.action == "reject":  # keep the requirement unfulfilled until staff explicitly choose a photo
        await db.ncs.update_one({"_id": nc["_id"]}, {"$addToSet": {"checklist.held": item_id}})
    else:
        await db.ncs.update_one({"_id": nc["_id"]}, {"$pull": {"checklist.held": item_id}})
    if ex.get("rejected_file_id"):  # staff said "not this photo for this requirement": PDF/coverage never auto-use it for it
        await db.files.update_one({"_id": oid(ex["rejected_file_id"])}, {"$addToSet": {"rejected_items": item_id}})
    if ex.get("file_id"):
        await db.files.update_one({"_id": oid(ex["file_id"])}, {"$pull": {"rejected_items": item_id}})
    await db.ncs.update_one({"_id": nc["_id"]}, {"$set": {f"photo_recs.items.{item_id}": rec}})
    await audit(f"nc.recommendation_{body.action}", u.id, f"nc:{nc_id}", {"item_id": item_id, "file_id": ex.get("file_id"), "rejected": ex.get("rejected_file_id")})
    return await view(nc_id)


class ProposeIn(BaseModel):
    name: str = Field(default="", max_length=120)


@router.post("/ncs/{nc_id}/recommendations/propose-profile")
async def propose(nc_id: str, body: ProposeIn, u: User = Depends(staff)):
    """NEW proposed profile version = current approved examples + this NC's staff-confirmed photos (good) and rejected suggestions (bad).
    The approved version is untouched; nothing influences future NCs until an admin approves the proposal."""
    from checklist import signals
    from smart_pdf import scope_for
    nc = await _nc(nc_id, u)
    cl = nc.get("checklist") or {}
    if cl.get("status") not in ("confirmed", "auto"):
        raise HTTPException(400, "Confirm the evidence checklist first")
    base = await _profile(nc)
    name = body.name.strip() or (base or {}).get("name") or ""
    if len(name) < 3:
        raise HTTPException(400, "Give the profile a name")
    marked = await db.files.find({"nc_id": nc_id, "kind": "photo", "checklist_item": {"$ne": None}}, {"checklist_item": 1}).to_list(500)
    bad = await _rejected(nc_id)
    if not marked and not bad:
        raise HTTPException(400, "Accept, replace or reject at least one photo first")
    ids = [i["id"] for i in cl["items"]]
    merge = lambda old, new: list(dict.fromkeys(new + (old or [])))[:3]
    examples = {i: merge(((base or {}).get("examples") or {}).get(i), [str(f["_id"]) for f in marked if f["checklist_item"] == i]) for i in ids}
    bad_ex = {i: merge(((base or {}).get("bad_examples") or {}).get(i), sorted(bad.get(i, set()))) for i in ids}
    pending = await db.nc_profiles.find_one({"status": "proposed", "source_nc": nc_id, "change": "staff photo corrections"})
    key = (pending or {}).get("key") or (base or {}).get("key") or (cl.get("profile") or {}).get("key") or uuid.uuid4().hex[:12]
    last = await db.nc_profiles.find_one({"key": key}, sort=[("version", -1)])
    doc = {"key": key, "version": (last or {}).get("version", 0) + 1, "status": "proposed", "name": name[:120],
           "signals": (base or {}).get("signals") or signals(nc, scope_for(nc)),
           "items": [{k: v for k, v in i.items() if k != "locations"} | {"locations": []} for i in cl["items"]],
           "examples": examples, "bad_examples": {k: v for k, v in bad_ex.items() if v},
           "guidance_ids": sorted({s.get("guidance_id") for i in cl["items"] for s in i.get("sources") or [] if s.get("guidance_id")}),
           "source_nc": nc_id, "based_on": base and {"id": str(base["_id"]), "version": base["version"]}, "change": "staff photo corrections",
           "learning_examples": await db.learning_examples.count_documents({"nc_id": nc_id}), "created_by": u.id, "created_at": now_iso()}
    if pending:  # pressing again updates this NC's still-unreviewed proposal instead of piling up new ones
        doc["version"] = pending["version"]
        await db.nc_profiles.replace_one({"_id": pending["_id"]}, doc)
        pid = str(pending["_id"])
    else:
        pid = str((await db.nc_profiles.insert_one(doc)).inserted_id)
    await audit("profile.proposed", u.id, f"profile:{pid}", {"key": key, "version": doc["version"], "nc_id": nc_id, "from": "recommendations"})
    return {"id": pid, "key": key, "version": doc["version"], "status": "proposed"}
