"""NC evidence checklist, staff best-photo marking, learning examples and reusable NC-type profiles (Knowledge Index Phase 1).
Behind the existing "Location-Aware Evidence" switch (staff_review_enabled / coverage_check_enabled / smart_pdf_enabled).
Staff/admin only. Never approves an NC: a checklist can only add reasons that send an NC to staff review.

ncs.checklist = {status: draft|confirmed|auto, source: ai|profile|staff, profile: {key, version, id}, match: {rating, score, signals},
                 items: [{id, title, stage, locations: [loc ids], measurement, criterion, sources: [{guidance_id, doc_id, doc_version,
                 clause, page, title}]}], history: [...]}
files.checklist_item = item id staff marked this photo as the best evidence for (reference only; the photo is never altered).
db.learning_examples = every staff marking/correction.  db.nc_profiles = versioned profiles (proposed -> approved -> superseded/retired).
"""
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

log = logging.getLogger("checklist")

router = APIRouter(prefix="/api")
staff = require("admin", "employee")
admin = require("admin")
STAGES = ("before", "during", "after", "verification")
COMPONENTS = {"gas pipe": r"gas (?:delivery )?pipe|entry tee|\btee\b|gas meter", "vent cover": r"vent(?:ilation)? cover|vent panel",
              "extractor fan": r"extract(?:or)? fan|extract vent|extract", "trickle vent": r"trickle vents?", "window sill": r"window sills?|\bsills?\b",
              "end cap": r"end caps?", "threshold": r"thresholds?", "door": r"\bdoors?\b", "loft hatch": r"loft hatch", "loft insulation": r"loft insulation|loft",
              "ewi": r"\bewi\b|external wall insulation|render|beads?|insulation boards?", "cavity": r"cavity", "airbrick": r"air ?bricks?",
              "duct": r"duct(?:ing)?", "eaves": r"eaves|soffit|fascia", "flue": r"flues?", "window": r"windows?|reveals?"}
MEASURES = {"gas pipe": "gas", "vent cover": "ventilation", "extractor fan": "ventilation", "trickle vent": "ventilation", "airbrick": "ventilation",
            "duct": "ventilation", "window sill": "windows", "end cap": "ewi", "window": "windows", "threshold": "doors", "door": "doors",
            "loft hatch": "loft insulation", "loft insulation": "loft insulation", "ewi": "ewi", "cavity": "cavity wall", "eaves": "ewi", "flue": "heating"}
DEFECTS = r"missing|detached|gaps?|clearance|seal(?:ed|ing)?|continuity|damaged?|poor|incorrect|not (?:installed|fitted|provided)|blocked|insufficient"
REQS = r"\d+\s?(?:mm|m3/h|l/s|m)\b|clearance|airflow|flow rate|test(?:ed|ing)?|certificat\w*|commission\w*|u-value|depth"


def _text(nc: dict) -> str:
    return " ".join(x for x in [nc.get("original_text") or "", nc.get("description") or "", nc.get("required_work") or ""] if x)


def signals(nc: dict, scope: Optional[dict]) -> dict:
    """Five rule-based signals (no AI): defect type, component, technical requirement, measure, location context."""
    low = _text(nc).lower()
    comp = sorted(k for k, rx in COMPONENTS.items() if re.search(rx, low))
    return {"component": comp, "defect": sorted({m.rstrip("sd") if m.startswith(("gap", "seal")) else m for m in re.findall(DEFECTS, low)}),
            "requirement": sorted({re.sub(r"\s", "", m) for m in re.findall(REQS, low)}), "measure": sorted({MEASURES[c] for c in comp if c in MEASURES}),
            "location": [(scope or {}).get("scope_type") or "other"]}


def rate(a: dict, b: dict) -> tuple:
    """-> (rating, score). Similar wording alone never matches: the component must overlap plus other signals."""
    hits = {k: bool(set(a.get(k) or []) & set(b.get(k) or [])) for k in ("component", "defect", "requirement", "measure", "location")}
    score = sum(hits.values())
    if not hits["component"]:
        return "none", score
    return ("confident" if score >= 4 else "uncertain" if score >= 2 else "none"), score


async def match_profile(nc: dict, scope: Optional[dict]) -> Optional[dict]:
    sig = signals(nc, scope)
    best = None
    async for p in db.nc_profiles.find({"status": "approved"}):
        r, sc = rate(sig, p.get("signals") or {})
        if r != "none" and (not best or sc > best["score"]):
            best = {"profile": p, "rating": r, "score": sc}
    return best and {**best, "signals": sig}


def item_evidence(item: dict, photos: List[dict]) -> dict:
    """staff-marked photos first; otherwise photos whose stored stage/location confidently match the item."""
    ok = [f for f in photos if f.get("pdf_include") is not False and f.get("status") != "superseded"]
    marked = [f for f in ok if f.get("checklist_item") == item["id"]]
    if marked:
        return {"state": "staff", "files": marked}
    auto = []
    for f in ok:
        c = f.get("classification") or {}
        if f.get("checklist_item") or c.get("relevant") is False or c.get("dup_of") or item["id"] in (f.get("rejected_items") or []):
            continue
        st = (c.get("staff") or {}).get("stage") or c.get("stage")
        if st == item["stage"] and (not item.get("locations") or c.get("location_id") in item["locations"]):
            auto.append(f)
    return {"state": "ai" if auto else "missing", "files": auto}


def coverage(cl: Optional[dict], photos: List[dict]) -> List[dict]:
    return [{"id": it["id"], "title": it["title"], "stage": it["stage"], "state": item_evidence(it, photos)["state"]}
            for it in (cl or {}).get("items") or []]


# ---------------- creation ----------------
DRAFT_SYSTEM = """You draft a short evidence checklist for ONE building non-compliance (NC) remediation, for a TrustMark evidence pack.
Use ONLY the NC wording, the TrustMark/required-work text and the approved guidance given. Never invent measurements or methods.
Each item = one distinct piece of photographic evidence. stage: before | during | after | verification (verification = measurement,
test or certificate). Include a verification item only when the NC/guidance requires a measurement, test or certificate.
locations: names from the given scope list only (or []). 2-6 items. measurement: only a value written in the NC or the document
extracts, else "". refs: numbers of the [D#] document extracts the item is based on (or []); never cite an extract that does not say it.
Respond with ONLY JSON: {"items": [{"title": "...", "stage": "...", "measurement": "", "criterion": "...", "locations": [], "refs": []}]}"""


async def ai_draft(nc: dict, scope: Optional[dict], guides: List[dict], passages: Optional[List[dict]] = None) -> Optional[List[dict]]:
    from emergentintegrations.llm.chat import LlmChat, UserMessage
    from documents import refs_text
    from guidance import guidance_text
    try:
        chat = LlmChat(api_key=os.environ["EMERGENT_LLM_KEY"], session_id=f"checklist-{uuid.uuid4()}", system_message=DRAFT_SYSTEM).with_model(
            os.environ.get("AI_PROVIDER", "gemini"), os.environ.get("AI_MODEL", "gemini-3-flash-preview"))
        locs = [l["label"] for l in (scope or {}).get("locations") or []]
        out = await chat.send_message(UserMessage(text=f"NC: {_text(nc)[:2500]}\nScope locations: {', '.join(locs) or '(none)'}\n"
                                                       f"Approved guidance:\n{guidance_text(guides)[:2500] or '(none)'}\n"
                                                       f"{refs_text({'passages': passages or []})[:3000] or 'Document extracts: (none)'}"))
        m = re.search(r"\{.*\}", out if isinstance(out, str) else str(out), re.S)
        return (json.loads(m.group(0)) if m else {}).get("items") or None
    except Exception as e:
        log.warning("checklist draft failed: %s", type(e).__name__)
        return None


def norm_items(raw: List[dict], scope: Optional[dict], sources: List[dict]) -> List[dict]:
    ids = {l["label"].lower(): l["id"] for l in (scope or {}).get("locations") or []}
    known = set(ids.values())
    out = []
    for k, it in enumerate(raw[:12]):
        st = it.get("stage") if it.get("stage") in STAGES else None
        title = re.sub(r"\s+", " ", str(it.get("title") or "")).strip()[:120]
        if not st or not title:
            continue
        locs = [ids.get(str(x).lower(), x) for x in it.get("locations") or []]
        out.append({"id": it.get("id") or f"it-{uuid.uuid4().hex[:6]}", "title": title, "stage": st, "locations": [x for x in locs if x in known],
                    "measurement": str(it.get("measurement") or "")[:120], "criterion": str(it.get("criterion") or "")[:200],
                    "sources": it.get("sources") if isinstance(it.get("sources"), list) else sources})
    return out


async def ensure_checklist(nc_id: str, ai=None) -> Optional[dict]:
    """Existing checklist, else confident/uncertain profile match (no AI), else ONE AI draft. Never raises."""
    from guidance import match_guidance
    from smart_pdf import scope_for
    nc = await db.ncs.find_one({"_id": oid(nc_id)})
    if nc.get("checklist"):
        return nc["checklist"]
    sc = scope_for(nc)
    m = await match_profile(nc, sc)
    if m and m["rating"] in ("confident", "uncertain"):
        p = m["profile"]
        cl = {"status": "auto" if m["rating"] == "confident" else "draft", "source": "profile",
              "profile": {"key": p["key"], "version": p["version"], "id": str(p["_id"])},
              "match": {"rating": m["rating"], "score": m["score"], "signals": m["signals"]}, "items": norm_items(p["items"], sc, [])}
    else:
        import inspect
        import documents
        guides = await match_guidance(_text(nc))
        passages = await documents.retrieve(_text(nc))  # approved documents only; no AI
        fn = ai or ai_draft
        raw = await (fn(nc, sc, guides, passages) if len(inspect.signature(fn).parameters) >= 4 else fn(nc, sc, guides))
        if not raw:
            return None
        srcs = [{"guidance_id": g["id"], "title": g.get("title", ""), "doc_id": None, "doc_version": None, "clause": None, "page": None} for g in guides]
        known = documents._vals(_text(nc) + " " + " ".join(p["_text"] for p in passages))
        notes = []
        for it in raw:
            cited = [passages[int(r) - 1] for r in it.get("refs") or [] if str(r).isdigit() and 0 < int(r) <= len(passages)]
            it["sources"] = srcs + [documents.source_of(p) for p in cited]
            meas = str(it.get("measurement") or "")
            if any(float(v) not in known.get(u.lower(), set()) for v, u in documents.MEAS_RX.findall(meas)):
                notes.append(f"AI-suggested measurement '{meas}' removed from '{it.get('title')}': not stated in the NC or approved documents")
                it["measurement"] = ""
        cl = {"status": "draft", "source": "ai", "profile": None, "match": {"rating": "none", "score": m["score"] if m else 0,
              "signals": (m or {}).get("signals") or signals(nc, sc)}, "items": norm_items(raw, sc, srcs), "notes": notes[:10]}
        if not cl["items"]:
            return None
    cl.update(v=1, created_at=now_iso(), history=[])
    await db.ncs.update_one({"_id": nc["_id"], "checklist": {"$exists": False}}, {"$set": {"checklist": cl}})
    return (await db.ncs.find_one({"_id": nc["_id"]}, {"checklist": 1}))["checklist"]


async def gate_reasons(nc_id: str) -> List[str]:
    """Assessment hook (switch ON): reasons that send an NC to staff review. Never approves anything.
    An unconfirmed AI draft is stored for staff but not used to judge the NC (the existing checks decide)."""
    cl = await ensure_checklist(nc_id)
    if not cl or (cl["status"] == "draft" and cl.get("source") == "ai"):
        return []
    out = []
    if cl["status"] == "draft":
        out.append("Evidence checklist needs staff confirmation (uncertain profile match)")
    photos = await db.files.find({"nc_id": nc_id, "kind": "photo", "status": {"$in": ["draft", "submitted"]}}).to_list(2000)
    cov = coverage(cl, photos)
    miss_v = [c["title"] for c in cov if c["state"] == "missing" and c["stage"] == "verification"]
    miss = [c["title"] for c in cov if c["state"] == "missing" and c["stage"] != "verification"]
    if miss_v:
        out.append("Required measurement/test evidence not identified: " + "; ".join(miss_v)[:300])
    if miss:
        out.append("Checklist items without evidence: " + "; ".join(miss)[:300])
    return out


# ---------------- staff endpoints ----------------
async def _nc(nc_id: str, u: User) -> dict:
    from server import get_nc
    from staff_review import _on
    await get_nc(nc_id, u)
    await _on()
    return await db.ncs.find_one({"_id": oid(nc_id)})


async def view(nc_id: str) -> dict:
    nc = await db.ncs.find_one({"_id": oid(nc_id)}, {"checklist": 1})
    photos = await db.files.find({"nc_id": nc_id, "kind": "photo", "status": {"$in": ["draft", "submitted"]}}).to_list(2000)
    cl = nc.get("checklist")
    return {"checklist": cl and {k: v for k, v in cl.items() if k != "history"}, "coverage": coverage(cl, photos)}


class DraftIn(BaseModel):
    force: bool = False


@router.post("/ncs/{nc_id}/checklist/draft")
async def draft(nc_id: str, body: DraftIn, u: User = Depends(staff)):
    nc = await _nc(nc_id, u)
    if nc.get("checklist") and not body.force:
        return await view(nc_id)
    if nc.get("checklist"):
        await db.ncs.update_one({"_id": nc["_id"]}, {"$unset": {"checklist": ""}, "$push": {"checklist_history": {**nc["checklist"], "replaced_at": now_iso()}}})
    if not await ensure_checklist(nc_id):
        raise HTTPException(503, "The checklist could not be drafted right now")
    await audit("nc.checklist_drafted", u.id, f"nc:{nc_id}", {"force": body.force})
    return await view(nc_id)


class ItemIn(BaseModel):
    id: Optional[str] = None
    title: str = Field(min_length=3, max_length=120)
    stage: str = Field(pattern=r"^(before|during|after|verification)$")
    locations: List[str] = []
    measurement: str = Field(default="", max_length=120)
    criterion: str = Field(default="", max_length=200)
    sources: List[dict] = Field(default=[], max_length=10)


class ChecklistIn(BaseModel):
    items: List[ItemIn] = Field(min_length=1, max_length=12)


@router.put("/ncs/{nc_id}/checklist")
async def confirm(nc_id: str, body: ChecklistIn, u: User = Depends(staff)):
    """Staff edit + confirm. Previous version kept in history; staff confirmation also accepts an uncertain profile match."""
    from smart_pdf import scope_for
    nc = await _nc(nc_id, u)
    old = nc.get("checklist") or {"source": "staff", "profile": None, "match": None, "v": 0, "history": []}
    items = norm_items([i.model_dump() for i in body.items], scope_for(nc), [])
    hist = (old.get("history") or []) + ([{"items": old.get("items"), "status": old.get("status"), "at": now_iso()}] if old.get("items") else [])
    cl = {**old, "items": items, "status": "confirmed", "confirmed_by": u.id, "confirmed_at": now_iso(), "v": (old.get("v") or 0) + 1, "history": hist[-20:]}
    await db.ncs.update_one({"_id": nc["_id"]}, {"$set": {"checklist": cl}})
    valid = {i["id"] for i in items}
    await db.files.update_many({"nc_id": nc_id, "checklist_item": {"$nin": list(valid) + [None]}}, {"$set": {"checklist_item": None}})
    await audit("nc.checklist_confirmed", u.id, f"nc:{nc_id}", {"items": [i["title"] for i in items], "version": cl["v"]})
    return await view(nc_id)


@router.get("/ncs/{nc_id}/checklist")
async def get_checklist(nc_id: str, u: User = Depends(staff)):
    await _nc(nc_id, u)
    return await view(nc_id)


class MarkIn(BaseModel):
    item_id: Optional[str] = None  # None = clear the marking


@router.put("/evidence/{fid}/checklist-item")
async def mark(fid: str, body: MarkIn, u: User = Depends(staff)):
    """Staff mark a photo as the best evidence for a checklist item. Stores a reference + a learning example; the photo is untouched."""
    f = await db.files.find_one({"_id": oid(fid)}, {"nc_id": 1, "kind": 1, "checklist_item": 1, "classification": 1})
    if not f or f["kind"] != "photo":
        raise HTTPException(404, "Photo not found")
    nc = await _nc(f["nc_id"], u)
    cl = nc.get("checklist") or {}
    if body.item_id and body.item_id not in {i["id"] for i in cl.get("items") or []}:
        raise HTTPException(400, "Unknown checklist item")
    await db.files.update_one({"_id": f["_id"]}, {"$set": {"checklist_item": body.item_id}})
    item = next((i for i in cl.get("items") or [] if i["id"] == body.item_id), None)
    await db.learning_examples.insert_one({"nc_id": f["nc_id"], "file_id": fid, "item_id": body.item_id, "item_title": item and item["title"],
                                           "item_stage": item and item["stage"], "previous_item_id": f.get("checklist_item"),
                                           "ai_stage": (f.get("classification") or {}).get("stage"), "profile": cl.get("profile"),
                                           "action": "mark" if body.item_id else "clear", "by": u.id, "at": now_iso()})
    await audit("evidence.checklist_marked", u.id, f"nc:{f['nc_id']}", {"file_id": fid, "item_id": body.item_id})
    return await view(f["nc_id"])


class ProfileIn(BaseModel):
    name: str = Field(min_length=3, max_length=120)


@router.post("/ncs/{nc_id}/checklist/save-profile")
async def save_profile(nc_id: str, body: ProfileIn, u: User = Depends(staff)):
    """Propose a reusable NC-type profile from a confirmed checklist. From a profile-based NC this proposes a NEW version
    (the approved version is never changed automatically). Needs admin approval before reuse."""
    from smart_pdf import scope_for
    nc = await _nc(nc_id, u)
    cl = nc.get("checklist") or {}
    if cl.get("status") != "confirmed":
        raise HTTPException(400, "Confirm the checklist before saving it as a profile")
    marked = await db.files.find({"nc_id": nc_id, "checklist_item": {"$ne": None}, "kind": "photo"}, {"checklist_item": 1}).to_list(500)
    key = (cl.get("profile") or {}).get("key") or uuid.uuid4().hex[:12]
    last = await db.nc_profiles.find_one({"key": key}, sort=[("version", -1)])
    doc = {"key": key, "version": (last or {}).get("version", 0) + 1, "status": "proposed", "name": body.name.strip(),
           "signals": signals(nc, scope_for(nc)), "items": [{k: v for k, v in i.items() if k != "locations"} | {"locations": []} for i in cl["items"]],
           "examples": {i["id"]: [str(f["_id"]) for f in marked if f["checklist_item"] == i["id"]] for i in cl["items"]},
           "guidance_ids": sorted({s.get("guidance_id") for i in cl["items"] for s in i.get("sources") or [] if s.get("guidance_id")}),
           "source_nc": nc_id, "learning_examples": await db.learning_examples.count_documents({"nc_id": nc_id}),
           "created_by": u.id, "created_at": now_iso()}
    pid = str((await db.nc_profiles.insert_one(doc)).inserted_id)
    await audit("profile.proposed", u.id, f"profile:{pid}", {"key": key, "version": doc["version"], "nc_id": nc_id})
    return {"id": pid, "key": key, "version": doc["version"], "status": "proposed"}


# ---------------- admin: profiles ----------------
def _pub(p: dict) -> dict:
    return {"id": str(p["_id"]), **{k: p.get(k) for k in ("key", "version", "status", "name", "signals", "items", "examples", "bad_examples", "guidance_ids",
                                                         "source_nc", "learning_examples", "created_at", "approved_at", "change", "based_on")}}


@router.get("/admin/profiles")
async def list_profiles(u: User = Depends(admin)):
    return [_pub(p) for p in await db.nc_profiles.find().sort([("key", 1), ("version", -1)]).to_list(1000)]


class ProfileStatusIn(BaseModel):
    status: str = Field(pattern=r"^(approved|retired)$")


@router.put("/admin/profiles/{pid}/status")
async def profile_status(pid: str, body: ProfileStatusIn, u: User = Depends(admin)):
    p = await db.nc_profiles.find_one({"_id": oid(pid)})
    if not p:
        raise HTTPException(404, "Profile not found")
    if body.status == "approved":  # earlier approved version of the same profile is kept as 'superseded'
        await db.nc_profiles.update_many({"key": p["key"], "status": "approved", "_id": {"$ne": p["_id"]}}, {"$set": {"status": "superseded"}})
    await db.nc_profiles.update_one({"_id": p["_id"]}, {"$set": {"status": body.status, f"{body.status}_at": now_iso(), f"{body.status}_by": u.id}})
    await audit(f"profile.{body.status}", u.id, f"profile:{pid}", {"key": p["key"], "version": p["version"]})
    return _pub(await db.nc_profiles.find_one({"_id": p["_id"]}))


# ---------------- PDF ----------------
def checklist_plan(nc: dict, photos: List[dict]) -> Optional[list]:
    """[(heading, [(candidates, caption)])]: ONE slot per checklist requirement (staff-marked first), stage order, caption = the
    requirement. Requirements without a photo are left out silently. None when no requirement has a photo (caller falls back)."""
    from smart_pdf import HEAD, ORDER
    items = (nc.get("checklist") or {}).get("items") or []
    used, out = set(), []
    for st in ORDER:
        slots = []
        for it in [i for i in items if i["stage"] == st]:
            fs = [f for f in item_evidence(it, photos)["files"] if str(f["_id"]) not in used]
            if fs:
                slots.append((fs, it["title"]))
                used.update(str(f["_id"]) for f in fs)
        if slots:
            out.append((HEAD[st], slots))
    if not out:
        return None
    extra = [f for f in photos if f.get("pdf_include") is True and str(f["_id"]) not in used]
    if extra:  # staff 'always include' photos are never dropped
        out.append((HEAD["uncertain"], [([f], "") for f in extra]))
    return out
