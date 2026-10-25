"""Phase 2 — AI photo classification per NC: location, component, stage, relevance, duplicate grouping.
Disabled unless Settings.photo_classification_enabled. Metadata only: never deletes photos, never changes pdf_include,
PDF selection, AI approval decisions or thresholds. Staff-confirmed fields always win over AI.

files.classification (optional):
{ "v": 1, "source": "ai"|"staff"|"mixed",
  "location_id": "loc-3"|null,        # set ONLY when the photo can be tied to a scope location (visible label / note / staff)
  "location_label": "Window 3"|null, "location_group": "grp-2"|null,  # grp = AI says these photos show the same physical spot
  "location_uncertain": bool, "location_confidence": 0-1,
  "component": "window sill end cap"|null,
  "stage": before|during|after|verification|uncertain, "stage_confidence": 0-1,
  "relevant": true|false|null, "relevance_note": str,
  "dup_group": "dup-1"|null, "dup_of": file_id|null,  # redundant copy of dup_of (kept, flagged only)
  "phash": 16-hex dHash, "model": str, "at": iso,
  "staff": {field: value, ..., "by": uid, "at": iso} }  # staff-confirmed overrides (priority)
files.location_id mirrors classification.location_id (feeds Phase 1 coverage)."""
import base64
import io
import json
import logging
import os
import re
import uuid
from typing import Callable, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from PIL import Image
from pydantic import BaseModel, Field

from auth import require
from core import User, audit, db, get_settings, now_iso, oid

log = logging.getLogger("photo_class")
router = APIRouter(prefix="/api")
staff = require("admin", "employee")
STAGES = ("before", "during", "after", "verification", "uncertain")
DUP_HAMMING = 6  # of 64 bits; near-identical framing only
BATCH = 16
MAX_REFS = 6  # reference photos from earlier batches sent with each later batch
STAFF_FIELDS = ("location_id", "location_label", "stage", "component", "relevant", "dup_of", "not_duplicate")


def dhash(data: bytes) -> str:
    img = Image.open(io.BytesIO(data)).convert("L").resize((9, 8), Image.LANCZOS)
    px = list(img.getdata())
    bits = "".join("1" if px[r * 9 + c] > px[r * 9 + c + 1] else "0" for r in range(8) for c in range(8))
    return f"{int(bits, 2):016x}"


def hamming(a: Optional[str], b: Optional[str]) -> int:
    return 64 if not a or not b else bin(int(a, 16) ^ int(b, 16)).count("1")


SYSTEM = """You classify installer photos for ONE building non-compliance (NC). Photos are numbered 0..n-1.
For EACH photo decide, using the image content, the NC wording, the installer note and the listed scope locations:
- location_label: ONLY a scope location label that is visibly identified in the photo (e.g. a written label, room sign, unmistakable
  context such as a bathroom suite for "Bathroom") or stated for that photo in the installer note. Otherwise null. NEVER invent
  room names or window numbers, and never assign a number just because photos are in order.
- same_location_as: indexes of OTHER photos that certainly show the SAME physical location/component (same window, same vent),
  judged from surroundings (frame, wall, fixtures, background). Visually similar installations at different places are NOT the same.
  If unsure, leave it out.
- component: short noun phrase for what is shown (e.g. "window sill end cap", "trickle vent", "extractor fan"), or null.
- stage: before (original defect) | during (work in progress / concealed work) | after (completed) | verification (test,
  measurement, label, certificate) | uncertain. Do not say "after" unless completion is visible. Do not rely on photo order.
- relevant: true if it is useful evidence for this NC, false if clearly unrelated/unusable, null if unsure.
- confidence values 0-1 for location and stage.
Respond with ONLY JSON: {"photos": [{"i": 0, "location_label": null, "location_confidence": 0.0, "same_location_as": [],
"component": null, "stage": "uncertain", "stage_confidence": 0.0, "relevant": null, "note": ""}]}"""


async def ai_classify(nc_text: str, note: str, labels: List[str], images: List[bytes], refs: Optional[List[dict]] = None) -> Optional[list]:
    """refs (later batches only): [{"image": bytes, "component": str, "stage": str}] already-classified photos from earlier batches,
    sent FIRST as R0..Rk so the AI can link to them; returned indices are for the new photos only (0..n-1), links to refs as "R<k>"."""
    from emergentintegrations.llm.chat import ImageContent, LlmChat, UserMessage
    refs = refs or []
    try:
        chat = LlmChat(api_key=os.environ["EMERGENT_LLM_KEY"], session_id=f"photo-class-{uuid.uuid4()}", system_message=SYSTEM).with_model(
            os.environ.get("AI_PROVIDER", "gemini"), os.environ.get("AI_MODEL", "gemini-3-flash-preview"))
        ref_txt = ""
        if refs:
            ref_txt = (f"The FIRST {len(refs)} images are REFERENCE photos already classified (do NOT return items for them): "
                       + "; ".join(f"R{k} = {r.get('component') or 'unknown'} ({r.get('stage')})" for k, r in enumerate(refs))
                       + ". If a new photo certainly shows the same physical location/component as a reference, put \"R<k>\" in its"
                         " same_location_as and reuse that reference's component name exactly. The new photos after them are numbered 0..n-1.\n")
        msg = UserMessage(text=f"NC: {nc_text[:1500]}\nInstaller note: {note or '(none)'}\nScope locations: {', '.join(labels) or '(not established)'}\n"
                               f"{ref_txt}{len(images)} new photos follow in order 0..{len(images) - 1}.",
                          file_contents=[ImageContent(image_base64=base64.b64encode(b).decode()) for b in [r["image"] for r in refs] + images])
        out = await chat.send_message(msg)
        m = re.search(r"\{.*\}", out if isinstance(out, str) else str(out), re.S)
        return (json.loads(m.group(0)) if m else {}).get("photos")
    except Exception as e:
        log.warning("photo classification failed: %s", type(e).__name__)
        return None


def build(items: list, ids: List[str], hashes: List[str], scope_locs: List[dict], existing: dict) -> dict:
    """Pure: AI items + hashes -> {file_id: classification}. Deterministic grouping/duplicate rules; staff fields win."""
    n = len(ids)
    by_i = {it.get("i"): it for it in (items or []) if isinstance(it, dict) and isinstance(it.get("i"), int) and 0 <= it["i"] < n}
    label_to_id = {l["label"].lower(): l["id"] for l in scope_locs}
    # union-find over mutual/declared same-location links (AI-declared only; similarity alone never merges)
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    for i, it in by_i.items():
        for j in it.get("same_location_as") or []:
            if isinstance(j, int) and 0 <= j < n and j != i:
                parent[find(i)] = find(j)
    out = {}
    for i in range(n):
        it = by_i.get(i, {})
        lab = it.get("location_label") if isinstance(it.get("location_label"), str) else None
        loc_id = label_to_id.get(lab.lower()) if lab else None  # label must be one of the scope locations
        stage = it.get("stage") if it.get("stage") in STAGES else "uncertain"
        conf = float(it.get("location_confidence") or 0)
        out[ids[i]] = {"v": 1, "source": "ai", "location_id": loc_id if conf >= 0.6 else None, "location_label": lab if loc_id and conf >= 0.6 else None,
                       "location_group": None, "location_uncertain": not (loc_id and conf >= 0.6), "location_confidence": round(conf, 2),
                       "component": (str(it.get("component"))[:80] if it.get("component") else None), "stage": stage,
                       "stage_confidence": round(float(it.get("stage_confidence") or 0), 2), "relevant": it.get("relevant") if isinstance(it.get("relevant"), bool) else None,
                       "relevance_note": str(it.get("note") or "")[:200], "dup_group": None, "dup_of": None, "phash": hashes[i]}
    groups: dict = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)
    for gi, (_, members) in enumerate(sorted(groups.items(), key=lambda kv: min(kv[1]))):
        if len(members) > 1:
            for m in members:
                out[ids[m]]["location_group"] = f"grp-{gi + 1}"
            # group inherits a confirmed location only if members don't disagree
            locs = {out[ids[m]]["location_id"] for m in members} - {None}
            if len(locs) == 1:
                lid = locs.pop()
                for m in members:
                    c = out[ids[m]]
                    if c["location_id"] is None:
                        c.update(location_id=lid, location_label=next(l["label"] for l in scope_locs if l["id"] == lid), location_uncertain=False)
            elif len(locs) > 1:  # contradictory: keep everyone uncertain, and never treat them as duplicates
                for m in members:
                    out[ids[m]].update(location_id=None, location_label=None, location_uncertain=True, conflict=True)
    # staff-confirmed fields win
    for fid, c in out.items():
        st = (existing.get(fid) or {}).get("staff") or {}
        if st:
            for k in STAFF_FIELDS:
                if k in st:
                    if k == "not_duplicate" and st[k]:
                        c.update(dup_of=None, dup_group=None)
                    elif k != "not_duplicate":
                        c[k] = st[k]
            if "location_id" in st:
                c["location_uncertain"] = st["location_id"] is None
            c["staff"], c["source"] = st, "mixed"
    # duplicates: same location (same group or same confirmed location) + same stage + same component + near-identical image
    d = 0
    for a in range(n):
        ca = out[ids[a]]
        if ca["dup_of"] or ca["stage"] == "uncertain" or ca.get("conflict"):
            continue
        for b in range(a + 1, n):
            cb = out[ids[b]]
            same_loc = (ca["location_group"] and ca["location_group"] == cb["location_group"]) or (ca["location_id"] and ca["location_id"] == cb["location_id"])
            if ca.get("conflict") or cb.get("conflict") or (ca.get("staff") or {}).get("not_duplicate") or (cb.get("staff") or {}).get("not_duplicate"):
                continue
            if ca["location_id"] and cb["location_id"] and ca["location_id"] != cb["location_id"]:
                continue  # different confirmed locations (incl. staff moves) are never duplicates
            if cb["dup_of"] or not same_loc or ca["stage"] != cb["stage"] or (ca["component"] or "").lower() != (cb["component"] or "").lower():
                continue
            if hamming(ca["phash"], cb["phash"]) <= DUP_HAMMING:
                if not ca["dup_group"]:
                    d += 1
                    ca["dup_group"] = f"dup-{d}"
                cb.update(dup_group=ca["dup_group"], dup_of=ids[a])
    for c in out.values():  # explicit staff duplicate decision is final
        st = c.get("staff") or {}
        if st.get("dup_of"):
            c["dup_of"] = st["dup_of"]
        c.pop("conflict", None)
    return out


async def _load(f: dict) -> bytes:
    from storage import storage
    p = f.get("paths") or {}
    return (await storage.aget(p.get("display") or p.get("original")))[0]


async def classify_nc(nc_id: str, loader: Callable = _load, ai: Callable = ai_classify) -> dict:
    nc = await db.ncs.find_one({"_id": oid(nc_id)})
    files = await db.files.find({"nc_id": nc_id, "kind": "photo", "status": {"$in": ["draft", "submitted"]}}).sort("server_received_at", 1).to_list(500)
    if not files:
        return {}
    scope = nc.get("scope")
    if not scope:
        from scope import merge, rule_extract
        t = " ".join(x for x in [nc.get("original_text") or "", nc.get("description") or ""] if x)
        scope = merge(rule_extract(t), None, t, None)  # in-memory only; Phase 1 storage stays gated by its own flag
    locs = scope.get("locations") or []
    imgs, hashes = [], []
    for f in files:
        b = await loader(f)
        imgs.append(b)
        hashes.append(dhash(b))
    items: list = []
    for s0 in range(0, len(files), BATCH):
        # later batches: one reference photo per distinct (component, stage) seen so far, so links/names carry across batches
        ref_idx, seen = [], set()
        for it in items:
            k = ((it.get("component") or "").lower(), it.get("stage"))
            if it.get("relevant") is not False and k[0] and k not in seen and len(ref_idx) < MAX_REFS:
                seen.add(k)
                ref_idx.append(it["i"])
        args = (" ".join([nc.get("original_text") or "", nc.get("description") or ""]), nc.get("installer_note") or "",
                [l["label"] for l in locs], imgs[s0:s0 + BATCH])
        refs = [{"image": imgs[g], **{k: next(x for x in items if x["i"] == g).get(k) for k in ("component", "stage")}} for g in ref_idx]
        part = await (ai(*args, refs=refs) if refs else ai(*args))
        if part is None:  # AI unavailable: keep every earlier tag rather than wiping them
            log.warning("classification aborted for nc %s: AI unavailable", nc_id)
            return {}
        for it in part:
            if isinstance(it, dict) and isinstance(it.get("i"), int) and 0 <= it["i"] < min(BATCH, len(files) - s0):
                links = []
                for j in it.get("same_location_as") or []:
                    if isinstance(j, int):
                        links.append(j + s0)
                    elif isinstance(j, str) and re.fullmatch(r"R\d{1,2}", j) and int(j[1:]) < len(ref_idx):
                        links.append(ref_idx[int(j[1:])])  # link to a photo from an earlier batch
                items.append({**it, "i": it["i"] + s0, "same_location_as": links})
    ids = [str(f["_id"]) for f in files]
    res = build(items, ids, hashes, locs, {str(f["_id"]): f.get("classification") for f in files})
    ts, model = now_iso(), os.environ.get("AI_MODEL", "gemini-3-flash-preview")
    for fid, c in res.items():
        c.update(model=model, at=ts)
        await db.files.update_one({"_id": oid(fid)}, {"$set": {"classification": c, "location_id": c["location_id"]}})
    return res


async def enabled() -> bool:
    return bool((await get_settings()).photo_classification_enabled)


_TASKS: set = set()


def schedule(nc_id: str) -> None:
    import asyncio
    t = asyncio.create_task(classify_background(nc_id))
    _TASKS.add(t)  # keep a reference so the task is not garbage-collected mid-run
    t.add_done_callback(_TASKS.discard)


async def classify_background(nc_id: str) -> None:
    try:
        if await enabled():
            await classify_nc(nc_id)
    except Exception as e:
        log.warning("background classification skipped: %s", type(e).__name__)


def _summary(files: list) -> list:
    return [{"id": str(f["_id"]), **{k: (f.get("classification") or {}).get(k) for k in
             ("location_id", "location_label", "location_group", "location_uncertain", "component", "stage", "relevant", "dup_group", "dup_of", "source")}}
            for f in files]


@router.get("/ncs/{nc_id}/photos/classification")
async def get_classification(nc_id: str, u: User = Depends(staff)):
    from server import get_nc
    await get_nc(nc_id, u)
    files = await db.files.find({"nc_id": nc_id, "kind": "photo", "status": {"$in": ["draft", "submitted"]}}, {"classification": 1}).sort("server_received_at", 1).to_list(500)
    return {"enabled": await enabled(), "photos": _summary(files)}


@router.post("/ncs/{nc_id}/photos/classify")
async def run_classify(nc_id: str, u: User = Depends(staff)):
    from server import get_nc
    await get_nc(nc_id, u)
    if not await enabled():
        raise HTTPException(409, "Photo classification is not enabled")
    await classify_nc(nc_id)
    await audit("nc.photos_classified", u.id, f"nc:{nc_id}", {})
    return await get_classification(nc_id, u)


class StaffClassIn(BaseModel):
    location_id: Optional[str] = Field(default=None, max_length=40)
    location_label: Optional[str] = Field(default=None, max_length=80)
    stage: Optional[str] = Field(default=None, pattern="^(before|during|after|verification|uncertain)$")
    component: Optional[str] = Field(default=None, max_length=80)
    relevant: Optional[bool] = None
    dup_of: Optional[str] = Field(default=None, max_length=40)
    not_duplicate: Optional[bool] = None


@router.put("/evidence/{fid}/classification")
async def staff_classify(fid: str, body: StaffClassIn, u: User = Depends(staff)):
    """Staff-confirmed fields (only those sent) take priority over AI on every future run."""
    from server import get_nc
    f = await db.files.find_one({"_id": oid(fid), "kind": "photo"})
    if not f:
        raise HTTPException(404, "Photo not found")
    await get_nc(f["nc_id"], u)
    upd = body.model_dump(exclude_unset=True)
    if not upd:
        raise HTTPException(400, "Nothing to change")
    if upd.get("dup_of") is not None:
        if upd["dup_of"] == fid or not await db.files.find_one({"_id": oid(upd["dup_of"]), "nc_id": f["nc_id"], "kind": "photo"}):
            raise HTTPException(400, "dup_of must be another photo of the same NC")
    if upd.get("location_id") is not None:
        if not re.fullmatch(r"loc-\d{1,3}", upd["location_id"]):
            raise HTTPException(400, "Invalid location id")
        if "location_label" not in upd:
            nc = await db.ncs.find_one({"_id": oid(f["nc_id"])}, {"scope": 1})
            upd["location_label"] = next((l["label"] for l in ((nc or {}).get("scope") or {}).get("locations", []) if l["id"] == upd["location_id"]), None)
    elif "location_id" in upd:
        upd["location_label"] = None
    c = f.get("classification") or {"v": 1, "source": "staff"}
    st = {**(c.get("staff") or {}), **upd, "by": u.id, "at": now_iso()}
    for k, v in upd.items():
        if k == "not_duplicate":
            if v:
                c.update(dup_of=None, dup_group=None)
        else:
            c[k] = v
    if "location_id" in upd:
        c["location_uncertain"] = upd["location_id"] is None
        if "dup_of" not in upd:  # moved photo is no longer a duplicate of a photo at its old location
            c.update(dup_of=None, dup_group=None)
    c.update(staff=st, source="mixed" if c.get("source") in ("ai", "mixed") else "staff")
    await db.files.update_one({"_id": f["_id"]}, {"$set": {"classification": c, "location_id": c.get("location_id")}})
    await audit("evidence.classification_confirmed", u.id, f"file:{fid}", {k: v for k, v in upd.items()})
    return {"ok": True, "classification": {k: c.get(k) for k in ("location_id", "location_label", "stage", "component", "relevant", "dup_of", "source")}}
