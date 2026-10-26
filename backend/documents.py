"""Master Documents Library (Knowledge Index Phase 2): admin-only technical guidance PDFs (PAS, TrustMark, Building Regulations,
manufacturer instructions). Separate from staff photo-selection profiles (checklist.nc_profiles).

db.tech_docs       one record per uploaded revision: {key (lineage), rev, title, publisher, version, pub_date, category, status
                   draft|approved|superseded|retired, file_id, sha256, size, pages, text_pages, extract_status, replaces, history[]}
db.tech_doc_pages  {doc_id, page (1-based), text}: text extracted on upload with PyMuPDF (no AI). Mongo text index for retrieval.
ncs.doc_refs       {at, method keyword|ai, passages: [{doc_id, key, title, publisher, version, category, page, clause, excerpt, score}], flags: []}

Only APPROVED revisions are retrieved for NC assessment / checklist drafting. Clause numbers are only ever read from the page text
(never generated); measurements found nowhere in the NC wording or approved text are flagged for staff, never assumed.
Retrieval runs only when the Location-Aware Evidence switch is ON (staff_review_enabled)."""
import hashlib
import json
import logging
import os
import re
import uuid
from typing import List, Optional

from bson import ObjectId
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from auth import require
from core import User, audit, db, file_url, get_settings, now_iso, oid
from storage import SYSTEM, storage

log = logging.getLogger("documents")
router = APIRouter(prefix="/api")
admin = require("admin")
staff = require("admin", "employee")
CATEGORIES = ("pas", "trustmark", "building_regs", "manufacturer", "other")
DATE_RX = r"^(|\d{4}(-\d{2}(-\d{2})?)?)$"
MAX_BYTES, MAX_PAGES = 60 * 1024 * 1024, 2000
STOP = set("""a an and are as at be been being by can for from has have in into is it its no not of on or that the their there these this
to was were which with will within where when shall should must may also any all such than then other per each been was area areas
noncompliance noncompliances non compliance compliances regarding observed property installer installation installed works work""".split())
CLAUSE_RX = re.compile(r"^\s*(?:(?:clause|section|annex)\s+)?((?:[A-Z]\.?)?\d{1,2}(?:\.\d{1,3}){1,4}|[A-Z]\d{1,2}(?:\.\d{1,3})*)\b", re.I | re.M)
MEAS_RX = re.compile(r"(\d+(?:\.\d+)?)\s?(mm|cm|m3/h|l/s|m²k|w/m²k|w/m2k|pa)\b", re.I)
_indexed = False


async def _index():
    global _indexed
    if not _indexed:
        await db.tech_doc_pages.create_index([("text", "text")], default_language="english")
        await db.tech_doc_pages.create_index([("doc_id", 1), ("page", 1)])
        _indexed = True


def extract_pages(data: bytes) -> List[str]:
    import pymupdf
    doc = pymupdf.open(stream=data, filetype="pdf")
    if doc.page_count > MAX_PAGES:
        raise ValueError(f"PDF has more than {MAX_PAGES} pages")
    return [p.get_text()[:30000] for p in doc]


def _pub(d: dict) -> dict:
    out = {k: d.get(k) for k in ("key", "rev", "title", "publisher", "version", "pub_date", "category", "status", "pages", "text_pages",
                                 "extract_status", "replaces", "size", "created_at", "approved_at", "notes")}
    out["id"] = str(d["_id"])
    out["file_url"] = file_url(d["file_id"], "original") if d.get("file_id") else None
    return out


async def _doc(did: str) -> dict:
    d = await db.tech_docs.find_one({"_id": oid(did)})
    if not d:
        raise HTTPException(404, "Document not found")
    return d


async def _store(data: bytes, meta: dict, u: User, key: Optional[str] = None, rev: int = 1, replaces: Optional[str] = None) -> dict:
    if not data.startswith(b"%PDF"):
        raise HTTPException(400, "Please upload a PDF document")
    if len(data) > MAX_BYTES:
        raise HTTPException(413, "PDF is larger than 60 MB")
    sha = hashlib.sha256(data).hexdigest()
    dup = await db.tech_docs.find_one({"sha256": sha, "status": {"$ne": "retired"}}, {"title": 1, "rev": 1})
    if dup:
        raise HTTPException(409, f"This exact PDF is already in the library ({dup['title']}, revision {dup['rev']})")
    try:
        pages = await run_in_threadpool(extract_pages, data)
    except Exception as e:
        raise HTTPException(400, f"Could not read this PDF: {str(e)[:120]}")
    await _index()
    did, fid, key = ObjectId(), ObjectId(), key or uuid.uuid4().hex[:12]
    path = await storage.aput(f"{SYSTEM}/documents/{key}/{did}.pdf", data, "application/pdf")
    await db.files.insert_one({"_id": fid, "kind": "techdoc", "doc_id": str(did), "project_id": None, "nc_id": None, "status": "submitted",
                               "mime": "application/pdf", "paths": {"original": path}, "uploaded_by": u.id, "server_received_at": now_iso()})
    text_pages = sum(1 for t in pages if len(t.strip()) >= 40)
    ts = now_iso()
    doc = {"_id": did, "key": key, "rev": rev, **meta, "status": "draft", "file_id": str(fid), "sha256": sha, "size": len(data),
           "pages": len(pages), "text_pages": text_pages,
           "extract_status": "ok" if pages and text_pages * 2 >= len(pages) else "little_text",  # scanned PDFs: no OCR
           "replaces": replaces, "created_by": u.id, "created_at": ts, "approved_by": None, "approved_at": None,
           "history": [{"at": ts, "by": u.id, "change": "replacement uploaded" if replaces else "uploaded"}]}
    if pages:
        await db.tech_doc_pages.insert_many([{"doc_id": str(did), "page": i + 1, "text": t} for i, t in enumerate(pages)])
    await db.tech_docs.insert_one(doc)
    await audit("document.replaced" if replaces else "document.uploaded", u.id, f"document:{did}",
                {"title": meta["title"], "version": meta["version"], "rev": rev, "pages": len(pages), "replaces": replaces})
    return doc


def _meta(title, publisher, version, pub_date, category, notes) -> dict:
    title, version = (title or "").strip(), (version or "").strip()
    if not title or not version:
        raise HTTPException(422, "Title and version are required")
    if category not in CATEGORIES:
        raise HTTPException(422, "Unknown document category")
    if not re.match(DATE_RX, pub_date or ""):
        raise HTTPException(422, "Publication date must be YYYY, YYYY-MM or YYYY-MM-DD")
    return {"title": title[:200], "publisher": (publisher or "").strip()[:120], "version": version[:60], "pub_date": pub_date or "",
            "category": category, "notes": (notes or "").strip()[:1000]}


# ---------------- admin: library ----------------
@router.post("/admin/documents")
async def upload(file: UploadFile = File(...), title: str = Form(...), version: str = Form(...), publisher: str = Form(""),
                 pub_date: str = Form(""), category: str = Form("other"), notes: str = Form(""), u: User = Depends(admin)):
    meta = _meta(title, publisher, version, pub_date, category, notes)
    return _pub(await _store(await file.read(), meta, u))


@router.post("/admin/documents/{did}/replace")
async def replace(did: str, file: UploadFile = File(...), version: str = Form(...), title: str = Form(""), publisher: str = Form(""),
                  pub_date: str = Form(""), category: str = Form(""), notes: str = Form(""), u: User = Depends(admin)):
    """New revision in the same lineage, starting as draft. The current approved revision keeps being used until the new one is approved."""
    old = await _doc(did)
    meta = _meta(title or old["title"], publisher or old.get("publisher"), version, pub_date or old.get("pub_date"),
                 category or old["category"], notes)
    last = await db.tech_docs.find_one({"key": old["key"]}, sort=[("rev", -1)])
    return _pub(await _store(await file.read(), meta, u, key=old["key"], rev=last["rev"] + 1, replaces=did))


@router.get("/admin/documents")
async def list_docs(u: User = Depends(admin)):
    return [_pub(d) for d in await db.tech_docs.find().sort([("updated_at", -1), ("created_at", -1)]).to_list(2000)]


@router.get("/admin/documents/{did}")
async def get_doc(did: str, u: User = Depends(admin)):
    d = await _doc(did)
    names = {str(x["_id"]): x["name"] for x in await db.users.find(
        {"_id": {"$in": [ObjectId(h["by"]) for h in d.get("history") or [] if ObjectId.is_valid(h.get("by") or "")]}}, {"name": 1}).to_list(100)}
    revs = await db.tech_docs.find({"key": d["key"]}).sort("rev", -1).to_list(200)
    return {**_pub(d), "history": [{**{k: h.get(k) for k in ("at", "change")}, "by_name": names.get(h.get("by"))} for h in d.get("history") or []][::-1],
            "revisions": [{k: x.get(k) for k in ("rev", "version", "status", "created_at")} | {"id": str(x["_id"])} for x in revs]}


class MetaIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    publisher: str = Field(default="", max_length=120)
    version: str = Field(min_length=1, max_length=60)
    pub_date: str = Field(default="", pattern=DATE_RX)
    category: str = Field(pattern="^(" + "|".join(CATEGORIES) + ")$")
    notes: str = Field(default="", max_length=1000)


@router.put("/admin/documents/{did}")
async def edit_meta(did: str, body: MetaIn, u: User = Depends(admin)):
    d = await _doc(did)
    new = _meta(**body.model_dump())
    changed = {k: [d.get(k), v] for k, v in new.items() if d.get(k) != v}
    if changed:
        await db.tech_docs.update_one({"_id": d["_id"]}, {"$set": {**new, "updated_at": now_iso()},
                                      "$push": {"history": {"at": now_iso(), "by": u.id, "change": "details edited: " + ", ".join(changed)}}})
        await audit("document.edited", u.id, f"document:{did}", {"changed": changed})
    return _pub(await _doc(did))


class DocStatusIn(BaseModel):
    status: str = Field(pattern="^(draft|approved|retired)$")


@router.put("/admin/documents/{did}/status")
async def set_status(did: str, body: DocStatusIn, u: User = Depends(admin)):
    d = await _doc(did)
    if d["status"] == body.status:
        return _pub(d)
    if body.status == "approved" and d.get("extract_status") != "ok" and not d.get("text_pages"):
        raise HTTPException(400, "No readable text was found in this PDF, so it cannot be used for retrieval")
    ts = now_iso()
    if body.status == "approved":  # one approved revision per document; earlier ones are kept as superseded
        for o in await db.tech_docs.find({"key": d["key"], "status": "approved", "_id": {"$ne": d["_id"]}}, {"_id": 1}).to_list(50):
            await db.tech_docs.update_one({"_id": o["_id"]}, {"$set": {"status": "superseded", "updated_at": ts},
                                          "$push": {"history": {"at": ts, "by": u.id, "change": f"superseded by revision {d['rev']}"}}})
            await audit("document.superseded", u.id, f"document:{o['_id']}", {"by": did})
    upd = {"status": body.status, "updated_at": ts} | ({"approved_by": u.id, "approved_at": ts} if body.status == "approved" else {})
    await db.tech_docs.update_one({"_id": d["_id"]}, {"$set": upd, "$push": {"history": {"at": ts, "by": u.id, "change": body.status}}})
    await audit(f"document.{body.status}", u.id, f"document:{did}", {"title": d["title"], "version": d["version"], "rev": d["rev"]})
    return _pub(await _doc(did))


@router.get("/admin/documents/{did}/search")
async def search_doc(did: str, q: str, u: User = Depends(admin)):
    """Admin: find pages in ONE document (any status) - used to choose pages for drafting guidance."""
    await _doc(did)
    await _index()
    terms = query_terms(q)
    if not terms:
        return []
    pages = await db.tech_doc_pages.find({"doc_id": did, "$text": {"$search": " ".join(terms)}}, {"score": {"$meta": "textScore"}, "page": 1, "text": 1}) \
        .sort([("score", {"$meta": "textScore"})]).to_list(20)
    return [{"page": p["page"], **_passage(p["text"], terms)} for p in pages]


# ---------------- page view (staff inspect the source) ----------------
@router.get("/documents/{did}/pages/{page}")
async def page_text(did: str, page: int, u: User = Depends(staff)):
    d = await _doc(did)
    p = await db.tech_doc_pages.find_one({"doc_id": did, "page": page})
    if not p:
        raise HTTPException(404, "Page not found")
    return {"doc": _pub(d), "page": page, "text": p["text"]}


# ---------------- retrieval ----------------
def query_terms(text: str) -> List[str]:
    words = re.findall(r"[a-z][a-z0-9\-]{2,}|\d+(?:\.\d+)?\s?mm", (text or "").lower())
    seen, out = set(), []
    for w in words:
        w = w.replace(" ", "")
        if w not in STOP and w not in seen and len(w) >= 3:
            seen.add(w)
            out.append(w)
    return out[:40]


def _stem(w: str) -> str:
    return w[:-1] if len(w) > 4 and w.endswith("s") else w


def _passage(text: str, terms: List[str]) -> dict:
    """Best 3-line window on the page + the nearest preceding clause number printed in the text (None if none is printed)."""
    lines = [l for l in text.splitlines() if l.strip()]
    stems = {_stem(t) for t in terms}
    best, bi = -1, 0
    for i in range(len(lines)):
        win = " ".join(lines[i:i + 4]).lower()
        sc = sum(1 for s in stems if s in win)
        if sc > best:
            best, bi = sc, i
    excerpt = re.sub(r"\s+", " ", " ".join(lines[bi:bi + 4])).strip()[:600]
    clause = None
    for l in reversed(lines[:bi + 1]):
        m = CLAUSE_RX.match(l)
        if m:
            clause = m.group(1)
            break
    return {"excerpt": excerpt, "clause": clause, "hits": max(best, 0)}


async def retrieve(text: str, limit: int = 4, candidates: int = 30) -> List[dict]:
    """Keyword (Mongo text index) retrieval over APPROVED documents only. No AI."""
    terms = query_terms(text)
    approved = {str(d["_id"]): d for d in await db.tech_docs.find({"status": "approved"}).to_list(2000)}
    if not terms or not approved:
        return []
    await _index()
    pages = await db.tech_doc_pages.find({"doc_id": {"$in": list(approved)}, "$text": {"$search": " ".join(terms)}},
                                         {"score": {"$meta": "textScore"}, "doc_id": 1, "page": 1, "text": 1}) \
        .sort([("score", {"$meta": "textScore"})]).to_list(candidates)
    out = []
    for p in pages:
        ps = _passage(p["text"], terms)
        if ps["hits"] < 2:  # one shared word is not a relevant passage
            continue
        d = approved[p["doc_id"]]
        out.append({"doc_id": p["doc_id"], "key": d["key"], "title": d["title"], "publisher": d.get("publisher", ""), "version": d["version"],
                    "category": d["category"], "page": p["page"], "clause": ps["clause"], "excerpt": ps["excerpt"],
                    "score": round(float(p["score"]) + ps["hits"], 2), "_text": p["text"]})
    out.sort(key=lambda x: x["score"], reverse=True)
    per_doc, res = {}, []
    for x in out:  # spread across documents: max 2 passages per document
        if per_doc.get(x["doc_id"], 0) < 2:
            per_doc[x["doc_id"]] = per_doc.get(x["doc_id"], 0) + 1
            res.append(x)
    return res[:limit]


RERANK_SYSTEM = """You select which numbered passages from approved technical documents are relevant to ONE building non-compliance (NC).
Use only the passages given. Do not add or rewrite any text. Respond with ONLY JSON: {"relevant": [passage numbers, most relevant first, max 4]}"""


async def ai_rerank(nc_text: str, cands: List[dict]) -> Optional[List[dict]]:
    """Optional meaning-based selection, staff-triggered only: ONE AI call that may only choose among keyword candidates."""
    if not cands:
        return []
    from emergentintegrations.llm.chat import LlmChat, UserMessage
    try:
        chat = LlmChat(api_key=os.environ["EMERGENT_LLM_KEY"], session_id=f"docs-{uuid.uuid4()}", system_message=RERANK_SYSTEM).with_model(
            os.environ.get("AI_PROVIDER", "gemini"), os.environ.get("AI_MODEL", "gemini-3-flash-preview"))
        listing = "\n".join(f"[{i + 1}] {c['title']} p.{c['page']}: {c['excerpt'][:400]}" for i, c in enumerate(cands))
        out = await chat.send_message(UserMessage(text=f"NC: {nc_text[:2000]}\n\nPassages:\n{listing}"))
        m = re.search(r"\{.*\}", out if isinstance(out, str) else str(out), re.S)
        idx = [int(i) - 1 for i in (json.loads(m.group(0)) if m else {}).get("relevant") or [] if str(i).isdigit()]
        return [cands[i] for i in dict.fromkeys(idx) if 0 <= i < len(cands)][:4]
    except Exception as e:
        log.warning("doc rerank failed: %s", type(e).__name__)
        return None


def ver(v: str) -> str:
    return v if (v or "").lower().startswith("v") else f"v{v}"


def _vals(text: str) -> dict:
    out = {}
    for v, unit in MEAS_RX.findall(text or ""):
        out.setdefault(unit.lower(), set()).add(float(v))
    return out


def flags_for(nc_text: str, passages: List[dict], checklist: Optional[dict] = None) -> List[dict]:
    """Missing / conflicting guidance, for staff review. Rule-based; never resolves a conflict itself."""
    flags = []
    if not passages:
        flags.append({"type": "missing", "text": "No approved technical document matched this NC. Staff to confirm the requirement."})
    by_doc = {}
    for p in passages:
        for unit, vs in _vals(p["excerpt"]).items():
            by_doc.setdefault(unit, {}).setdefault(p["key"], (set(), p))[0].update(vs)
    for unit, docs in by_doc.items():
        single = {k: (vs, p) for k, (vs, p) in docs.items() if len(vs) == 1}
        if len({next(iter(vs)) for vs, _ in single.values()}) > 1:
            parts = [f"{next(iter(vs)):g}{unit} ({p['title']} {ver(p['version'])}, p.{p['page']})" for vs, p in single.values()]
            flags.append({"type": "conflict", "text": "Approved documents give different values: " + " vs ".join(parts) + ". Staff to decide."})
    pool = (nc_text or "") + " " + " ".join(p.get("_text") or p["excerpt"] for p in passages)
    have = _vals(pool)
    for it in (checklist or {}).get("items") or []:
        for v, unit in MEAS_RX.findall(it.get("measurement") or ""):
            if float(v) not in have.get(unit.lower(), set()):
                flags.append({"type": "missing", "text": f"Measurement '{v}{unit}' for '{it['title']}' is not in the NC wording or approved documents. Staff to confirm."})
    return flags


def nc_text(nc: dict) -> str:
    return " ".join(x for x in [nc.get("original_text") or "", nc.get("description") or "", nc.get("required_work") or ""] if x)


async def refresh_refs(nc_id: str, use_ai: bool = False) -> dict:
    nc = await db.ncs.find_one({"_id": oid(nc_id)})
    text = nc_text(nc)
    method = "keyword"
    passages = await retrieve(text, limit=12 if use_ai else 4)
    if use_ai:
        picked = await ai_rerank(text, passages)
        if picked is None:
            raise HTTPException(503, "AI search is unavailable right now; keyword results are unchanged")
        passages, method = picked, "ai"
    refs = {"at": now_iso(), "method": method, "passages": [{k: v for k, v in p.items() if k != "_text"} for p in passages],
            "flags": flags_for(text, passages, nc.get("checklist"))}
    await db.ncs.update_one({"_id": nc["_id"]}, {"$set": {"doc_refs": refs}})
    return refs


async def refs_for_assessment(nc_id: str) -> dict:
    """Assessment hook (switch ON only, caller checks). Never raises."""
    try:
        return await refresh_refs(nc_id)
    except Exception as e:
        log.warning("doc retrieval failed: %s", type(e).__name__)
        return {"passages": [], "flags": []}


def refs_text(refs: dict) -> str:
    ps = (refs or {}).get("passages") or []
    if not ps:
        return ""
    head = ("APPROVED TECHNICAL DOCUMENT EXTRACTS (quoted text; rely only on what is written here, do not infer requirements, "
            "measurements or clause numbers that are not stated):")
    return head + "\n" + "\n".join(f"[D{i}] {p['title']} {ver(p['version'])}, page {p['page']}" + (f", clause {p['clause']}" if p.get("clause") else "")
                                   + f": {p['excerpt']}" for i, p in enumerate(ps, 1))


def gate_reasons(refs: dict) -> List[str]:
    """Only conflicting guidance sends an NC to staff review; missing guidance is shown to staff but does not block."""
    return [f["text"][:300] for f in (refs or {}).get("flags") or [] if f["type"] == "conflict"]


def source_of(p: dict) -> dict:
    """Phase 1 checklist source fields."""
    return {"guidance_id": None, "doc_id": p["doc_id"], "doc_version": p["version"], "clause": p.get("clause"), "page": p["page"], "title": p["title"]}


# ---------------- staff: NC references ----------------
async def _nc_ok(nc_id: str, u: User):
    from server import get_nc
    from staff_review import _on
    await get_nc(nc_id, u)
    await _on()


async def _view(nc_id: str) -> dict:
    nc = await db.ncs.find_one({"_id": oid(nc_id)}, {"doc_refs": 1})
    refs = nc.get("doc_refs") or {"at": None, "method": None, "passages": [], "flags": []}
    live = {str(d["_id"]): d["status"] for d in await db.tech_docs.find(
        {"_id": {"$in": [ObjectId(p["doc_id"]) for p in refs["passages"]]}}, {"status": 1}).to_list(50)}
    for p in refs["passages"]:
        p["doc_status"] = live.get(p["doc_id"], "missing")
    return refs


@router.get("/ncs/{nc_id}/references")
async def get_refs(nc_id: str, u: User = Depends(staff)):
    await _nc_ok(nc_id, u)
    nc = await db.ncs.find_one({"_id": oid(nc_id)}, {"doc_refs": 1})
    if not nc.get("doc_refs"):
        await refresh_refs(nc_id)
    return await _view(nc_id)


class RefreshIn(BaseModel):
    ai: bool = False


@router.post("/ncs/{nc_id}/references/refresh")
async def refresh(nc_id: str, body: RefreshIn, u: User = Depends(staff)):
    await _nc_ok(nc_id, u)
    await refresh_refs(nc_id, body.ai)
    await audit("nc.references_refreshed", u.id, f"nc:{nc_id}", {"ai": body.ai})
    return await _view(nc_id)


# ---------------- admin: AI-drafted guidance from a document ----------------
DRAFT_SYSTEM = """You draft ONE evidence-guidance entry for TrustMark remediation evidence from the technical document pages given.
Use ONLY the given page text. Copy measurements and clause numbers exactly as printed; if the pages do not state something, leave it "".
Never invent requirements, measurements, methods or clause numbers.
Respond with ONLY JSON: {"title": "NC type", "measure": "", "keywords": ["3-8 lowercase terms"], "defect": "", "evidence_required": "",
"photos_to_collect": "", "photo_details": "", "insufficient_examples": "", "review_notes": "", "clauses": ["clause numbers used"]}"""


class DraftGuidanceIn(BaseModel):
    pages: List[int] = Field(min_length=1, max_length=6)
    focus: str = Field(default="", max_length=200)


@router.post("/admin/documents/{did}/draft-guidance")
async def draft_guidance(did: str, body: DraftGuidanceIn, u: User = Depends(admin)):
    """ONE AI call on admin-chosen pages -> DRAFT guidance entry (existing AI Guidance Library). Not used until an admin approves it."""
    d = await _doc(did)
    pages = await db.tech_doc_pages.find({"doc_id": did, "page": {"$in": sorted(set(body.pages))}}).sort("page", 1).to_list(6)
    if not pages:
        raise HTTPException(400, "Choose pages that exist in this document")
    text = "\n\n".join(f"--- page {p['page']} ---\n{p['text'][:6000]}" for p in pages)
    j = await _draft_ai(d, text, body.focus)
    if j is None:
        raise HTTPException(503, "The guidance could not be drafted right now")
    clauses = [c for c in (str(x).strip() for x in j.get("clauses") or []) if c and c in text][:10]  # only clauses printed on the pages
    s = lambda k, n: re.sub(r"\s+", " ", str(j.get(k) or "")).strip()[:n]
    ref = f"{d['title']} {ver(d['version'])}" + (f" ({d['publisher']})" if d.get("publisher") else "") + \
          f", page{'s' if len(pages) > 1 else ''} {', '.join(str(p['page']) for p in pages)}" + (f", clause {', '.join(clauses)}" if clauses else "")
    ts = now_iso()
    g = {"title": s("title", 200) or d["title"], "measure": s("measure", 100), "keywords": sorted({str(k).strip().lower()[:60] for k in j.get("keywords") or [] if str(k).strip()})[:30],
         "defect": s("defect", 2000), "evidence_required": s("evidence_required", 3000), "photos_to_collect": s("photos_to_collect", 3000),
         "photo_details": s("photo_details", 2000), "video_guidance": "", "insufficient_examples": s("insufficient_examples", 2000), "request_example": "",
         "references": ref[:2000], "review_notes": s("review_notes", 2000), "scope_type": "", "status": "draft", "version": 1, "example_photo_ids": [],
         "source": {"type": "document", "doc_id": did, "doc_key": d["key"], "doc_version": d["version"], "pages": [p["page"] for p in pages], "clauses": clauses},
         "created_by": u.id, "created_at": ts, "updated_by": u.id, "updated_at": ts, "approved_by": None, "approved_at": None,
         "history": [{"version": 1, "at": ts, "by": u.id, "change": f"AI-drafted from document {d['title']} v{d['version']}"}]}
    gid = str((await db.guidance.insert_one(g)).inserted_id)
    await audit("guidance.drafted_from_document", u.id, f"guidance:{gid}", {"doc_id": did, "pages": g["source"]["pages"]})
    return {"id": gid, "status": "draft"}


async def _draft_ai(d: dict, text: str, focus: str) -> Optional[dict]:
    from emergentintegrations.llm.chat import LlmChat, UserMessage
    try:
        chat = LlmChat(api_key=os.environ["EMERGENT_LLM_KEY"], session_id=f"docguide-{uuid.uuid4()}", system_message=DRAFT_SYSTEM).with_model(
            os.environ.get("AI_PROVIDER", "gemini"), os.environ.get("AI_MODEL", "gemini-3-flash-preview"))
        out = await chat.send_message(UserMessage(text=f"Document: {d['title']} v{d['version']}\nFocus: {focus or '(none)'}\n\n{text[:30000]}"))
        m = re.search(r"\{.*\}", out if isinstance(out, str) else str(out), re.S)
        return json.loads(m.group(0)) if m else None
    except Exception as e:
        log.warning("guidance draft failed: %s", type(e).__name__)
        return None


async def switch_on() -> bool:
    return (await get_settings()).staff_review_enabled
