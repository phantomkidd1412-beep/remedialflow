"""Phase 1 — location-aware NC scope (foundation). Disabled unless Settings.location_scope_enabled is true.

`ncs.scope` (optional, backward compatible; absent on all existing NCs):
{
  "v": 1, "nc_key": sha1(normalised NC wording)        # stable id for the future Unique NC Knowledge Index
  "scope_type": window|habitable_room|wet_room|elevation|junction|component|other|single,
  "quantifier": explicit|all|single|unknown,           # how the NC states its extent
  "expected_count": int|null,                          # null = cannot be established reliably
  "locations": [{"id": "loc-1", "label": "Window 1", "source": "nc_text|report|ai|guidance|staff"}],
  "scope_status": confirmed|uncertain,                 # is the extent itself known?
  "basis": ["quoted phrase(s) the scope was derived from"],
  "sources": [{"type": "nc_text"}, {"type": "report_page", "import_id": ..., "page": n}, {"type": "guidance", "id": ..., "version": n}],
  "method": "rules"|"rules+ai", "model": str|null, "extracted_at": iso, "text_hash": str
}
Coverage (computed, never stored as a decision): complete | incomplete | unverified. Photos are matched to locations through
`files.location_id` (assigned in Phase 2; untagged photos => unverified). Sources use generic {type, id} references so the future
Master Documents Library plugs in as type "document" without a second system."""
import hashlib
import json
import logging
import os
import re
import uuid
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException

from auth import require
from core import User, audit, db, get_settings, now_iso, oid

log = logging.getLogger("scope")
router = APIRouter(prefix="/api")
staff = require("admin", "employee")
SCOPE_TYPES = ("window", "habitable_room", "wet_room", "elevation", "junction", "component", "other", "single")
LABEL = {"window": "Window", "habitable_room": "Habitable room", "wet_room": "Wet room", "elevation": "Elevation",
         "junction": "Junction", "component": "Component", "other": "Location"}
NUM = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
       "eleven": 11, "twelve": 12, "both": 2, "single": 1}
N = r"(\d{1,2}|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|both)"
ALL = r"\b(all|every|each|throughout|any)\b"

HABITABLE = ["living room", "lounge", "dining room", "sitting room", "family room", "study", "kitchen diner"]
WET = ["bathroom", "en-suite", "ensuite", "shower room", "utility room", "utility", "kitchen", "wc", "cloakroom"]
ELEV = ["front", "rear", "back", "side", "left", "right", "gable", "north", "south", "east", "west"]
# (scope_type, regex for the noun) - order = precedence when several match
NOUNS = [
    ("window", r"windows?|window sills?|sills?|end caps?|window reveals?|trickle vents? in (?:the )?windows?"),
    ("habitable_room", r"habitable rooms?|bedrooms?|living rooms?|lounges?|dining rooms?"),
    ("wet_room", r"wet rooms?|bathrooms?|en-?suites?|shower rooms?|utility rooms?|kitchens?|wcs?\b"),
    ("elevation", r"elevations?|gable walls?|gables?"),
    ("junction", r"junctions?|eaves|verges?|abutments?|roof ?lines?|wall plates?"),
    ("component", r"brackets?|meter boxes?|pipes?|penetrations?|flues?|vents?|fans?|extractors?|downpipes?|ducts?"),
]


def norm(t: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", (t or "").lower()))


def nc_key(text: str) -> str:
    return hashlib.sha1(norm(text).encode()).hexdigest()[:20]


def _num(tok: str) -> Optional[int]:
    tok = tok.lower()
    return int(tok) if tok.isdigit() else NUM.get(tok)


def rule_extract(text: str) -> dict:
    """Deterministic first pass. Only claims a count when the wording states it (number, numbered range or named list)."""
    t = " " + " ".join((text or "").split()) + " "
    low = t.lower()
    stype, noun = "other", None
    for st, rx in NOUNS:
        m = re.search(r"\b(" + rx + r")", low)
        if m:
            stype, noun = st, rx
            break
    out = {"scope_type": stype, "quantifier": "unknown", "expected_count": None, "labels": [], "basis": []}
    if noun is None:
        return out
    # 1) numbered ranges, e.g. W1-W8 / windows 1 to 8 / bedrooms 1-3
    m = re.search(r"\b(?:w|window|windows|bedroom|bedrooms|room|rooms)\s?(\d{1,2})\s?(?:-|to|–)\s?(?:w|window|bedroom|room)?\s?(\d{1,2})\b", low)
    if m and 0 < int(m.group(2)) - int(m.group(1)) < 40:
        a, b = int(m.group(1)), int(m.group(2))
        out.update(quantifier="explicit", expected_count=b - a + 1, labels=[f"{LABEL.get(stype, 'Location')} {i}" for i in range(a, b + 1)], basis=[m.group(0).strip()])
        return out
    # 3) named rooms / elevations listed explicitly ("bedroom 1, bedroom 2 and the bathroom", "front and rear elevations")
    named: List[str] = []
    if stype in ("habitable_room", "wet_room"):
        for r_ in re.findall(r"\b(bedroom\s?\d{1,2}|" + "|".join(map(re.escape, HABITABLE + WET)) + r")\b", low):
            lab = r_.strip().title()
            if lab not in named:
                named.append(lab)
    elif stype == "elevation":
        for e in re.findall(r"\b(" + "|".join(ELEV) + r")\b(?=[^.]*\belevations?\b)", low):
            lab = f"{e.title()} elevation"
            if lab not in named:
                named.append(lab)
    if re.search(ALL + r"[^.]{0,40}\b(" + noun + r")", low):
        out.update(quantifier="all", basis=[re.search(ALL + r"[^.]{0,40}\b(" + noun + r")", low).group(0).strip()])
        if named:  # "all habitable rooms (living room, bedroom 1, bedroom 2)" - list given, still flagged as 'all'
            out.update(labels=named)
        return out
    if len(named) > 1:
        out.update(quantifier="explicit", expected_count=len(named), labels=named, basis=[", ".join(named)])
        return out
    # 2) "8 windows", "two bedrooms", "both bathrooms" (also "8 no. windows", "eight (8) windows")
    m = re.search(r"(?<!bedroom )(?<!room )(?<!window )(?<!w)\b" + N + r"\s*(?:\(\d+\)\s*)?(?:no\.?\s*|x\s*)?(?:of the\s+)?(?:\w+\s){0,2}?(" + noun + r")\b", low)
    if m and _num(m.group(1)):
        n = _num(m.group(1))
        out.update(quantifier="explicit", expected_count=n, labels=[f"{LABEL.get(stype, 'Location')} {i}" for i in range(1, n + 1)], basis=[m.group(0).strip()])
        return out
    # 4) single location ("the kitchen window", "the rear elevation", "a bracket") - plural noun without number => unknown
    m = re.search(r"\b(the|a|an|one|single)\s+(?:\w+\s){0,2}?(" + noun + r")\b", low)
    nouns_found = [x.group(1) for x in re.finditer(r"\b(" + noun + r")\b", low)]
    singular = nouns_found and not any(w.endswith("s") and not w.endswith("ss") for w in nouns_found) and len(named) <= 1
    if singular and (m or stype != "junction"):  # bare singular noun ("Condensate pipe not insulated") = one location
        lab = named[0] if named else LABEL.get(stype, "Location") + " 1"
        out.update(quantifier="single", expected_count=1, labels=[lab], basis=[(m.group(0) if m else nouns_found[0]).strip()])
    return out


AI_SYSTEM = """You determine the SCOPE of ONE building non-compliance (NC): which locations or components need remedial evidence.
Use ONLY the NC wording and report extract provided. Never guess a number. If the total number of affected locations is not stated
or clearly listed, return "expected_count": null. Every location label must come from the text; every claim needs an exact quote.
scope_type one of: window, habitable_room, wet_room, elevation, junction, component, other, single.
Respond with ONLY JSON: {"scope_type": "...", "expected_count": 8|null, "locations": ["Bedroom 1", ...], "basis": ["exact quote", ...], "confidence": 0.0-1.0}"""


async def ai_extract(nc_text: str, report_text: str) -> Optional[dict]:
    try:
        from emergentintegrations.llm.chat import LlmChat, UserMessage
        chat = LlmChat(api_key=os.environ["EMERGENT_LLM_KEY"], session_id=f"nc-scope-{uuid.uuid4()}", system_message=AI_SYSTEM).with_model(
            os.environ.get("AI_PROVIDER", "gemini"), os.environ.get("AI_MODEL", "gemini-3-flash-preview"))
        out = await chat.send_message(UserMessage(text=f"NC wording:\n{nc_text[:2500]}\n\nReport extract:\n{report_text[:4000] or '(none)'}"))
        m = re.search(r"\{.*\}", out if isinstance(out, str) else str(out), re.S)
        return json.loads(m.group(0)) if m else None
    except Exception as e:
        log.warning("scope AI extraction failed: %s", type(e).__name__)
        return None


def merge(rules: dict, ai: Optional[dict], src_text: str, guide: Optional[dict]) -> dict:
    """Combine rules + AI. A count is CONFIRMED only when stated in the wording (rules) or when the AI's quoted basis is
    literally present in the source text and agrees with any rule count. Everything else stays uncertain."""
    stype, qty, count, labels, basis, src = rules["scope_type"], rules["quantifier"], rules["expected_count"], rules["labels"], list(rules["basis"]), "nc_text"
    if guide and guide.get("scope_type") in SCOPE_TYPES and stype == "other":
        stype = guide["scope_type"]
    if ai:
        ai_type = ai.get("scope_type") if ai.get("scope_type") in SCOPE_TYPES else None
        if stype == "other" and ai_type:
            stype = ai_type
        quotes = [q for q in (ai.get("basis") or []) if isinstance(q, str) and q.strip() and norm(q) in norm(src_text)]
        ai_count = ai.get("expected_count") if isinstance(ai.get("expected_count"), int) and 0 < ai.get("expected_count") <= 60 else None
        if count is None and ai_count and quotes:
            ai_labels = [str(x)[:60] for x in (ai.get("locations") or []) if str(x).strip()][:60]
            count, labels, src = ai_count, (ai_labels if len(ai_labels) == ai_count else [f"{LABEL.get(stype, 'Location')} {i}" for i in range(1, ai_count + 1)]), "ai"
            qty = "explicit"  # the count is stated in the source (quote verified), even if the NC itself says "all"
            basis += quotes
        elif count is not None and ai_count is not None and ai_count != count:
            basis.append(f"AI read {ai_count}, wording states {count} - kept wording")
    confirmed = count is not None and qty in ("explicit", "single")
    if stype == "single" or (count == 1 and qty == "single"):
        stype = stype if stype != "other" else "single"
    return {"scope_type": stype, "quantifier": qty, "expected_count": count if confirmed else None,
            "locations": [{"id": f"loc-{i + 1}", "label": lab, "source": src} for i, lab in enumerate(labels)],
            "scope_status": "confirmed" if confirmed else "uncertain", "basis": basis[:6]}


def coverage(scope: Optional[dict], files: List[dict]) -> dict:
    """complete | incomplete | unverified. Never 'complete' from a single representative photo of a multi-location NC."""
    photos = [f for f in files if f.get("kind") == "photo" and f.get("status") not in ("superseded", "draft") and f.get("pdf_include") is not False]
    if not scope:
        return {"status": "unverified", "reason": "Scope not identified", "covered": 0, "expected": None, "missing": []}
    exp = scope.get("expected_count")
    if scope.get("scope_status") != "confirmed" or not exp:
        return {"status": "unverified", "reason": "Number of affected locations could not be confirmed", "covered": None, "expected": None, "missing": []}
    if exp == 1:
        ok = bool(photos)
        return {"status": "complete" if ok else "incomplete", "reason": "" if ok else "No evidence photos", "covered": int(ok), "expected": 1,
                "missing": [] if ok else [scope["locations"][0]["label"]] if scope.get("locations") else []}
    tagged = {f.get("location_id") for f in photos if f.get("location_id")}
    if not tagged or any(not f.get("location_id") for f in photos):
        return {"status": "unverified", "reason": f"{exp} locations in scope; photos not yet matched to locations", "covered": None, "expected": exp, "missing": []}
    missing = [loc["label"] for loc in scope.get("locations", []) if loc["id"] not in tagged]
    return {"status": "incomplete" if missing else "complete", "reason": f"Evidence for {exp - len(missing)} of {exp} locations" if missing else "",
            "covered": exp - len(missing), "expected": exp, "missing": missing}


async def report_extract(nc: dict) -> tuple:
    """Text around this NC in the project's imported TrustMark report (best effort; '' when unavailable)."""
    imp = await db.imports.find_one({"project_id": nc["project_id"], "status": {"$in": ["done", "finished", "complete", "completed"]}}, sort=[("created_at", -1)]) \
        or await db.imports.find_one({"project_id": nc["project_id"]}, sort=[("created_at", -1)])
    if not imp or not imp.get("path"):
        return "", None
    try:
        import fitz
        from starlette.concurrency import run_in_threadpool
        from storage import storage
        data = (await storage.aget(imp["path"]))[0]
        needle = norm(nc.get("original_text") or nc.get("description") or "")[:60]

        def find():  # PDF parsing is CPU-bound: keep it off the event loop
            doc = fitz.open(stream=data, filetype="pdf")
            for i, page in enumerate(doc):
                txt = page.get_text()
                if needle and needle[:40] in norm(txt):
                    nxt = doc[i + 1].get_text() if i + 1 < len(doc) else ""
                    return (txt + "\n" + nxt)[:4000], i + 1
            return "", None
        txt, page = await run_in_threadpool(find)
        if page:
            return txt, {"type": "report_page", "import_id": str(imp["_id"]), "page": page}
    except Exception as e:
        log.warning("report extract failed: %s", type(e).__name__)
    return "", None


async def extract_scope(nc: dict, use_ai: bool = True) -> dict:
    from guidance import match_guidance
    text = " ".join(x for x in [nc.get("original_text") or "", nc.get("description") or "", nc.get("required_work") or ""] if x)
    rep, rep_src = await report_extract(nc)
    guides = await match_guidance(text, 1)
    rules = rule_extract(text)
    ai = await ai_extract(text, rep) if use_ai and rules["expected_count"] is None else None
    sc = merge(rules, ai, text + " " + rep, guides[0] if guides else None)
    sources = [{"type": "nc_text"}] + ([rep_src] if rep_src else []) + [{"type": "guidance", "id": g["id"], "version": g.get("version", 1)} for g in guides]
    return {"v": 1, "nc_key": nc_key(nc.get("original_text") or nc.get("description") or ""), **sc, "sources": sources,
            "method": "rules+ai" if ai else "rules", "model": os.environ.get("AI_MODEL", "gemini-3-flash-preview") if ai else None,
            "extracted_at": now_iso(), "text_hash": nc_key(text)}


async def enabled() -> bool:
    return bool((await get_settings()).location_scope_enabled)


async def ensure_scope(nc_id: str) -> Optional[dict]:
    """Assessment hook: (re)extract when enabled and the NC wording changed. Never raises; staff overrides are kept."""
    if not await enabled():
        return None
    try:
        nc = await db.ncs.find_one({"_id": oid(nc_id)})
        cur = nc.get("scope")
        text = " ".join(x for x in [nc.get("original_text") or "", nc.get("description") or "", nc.get("required_work") or ""] if x)
        if cur and (cur.get("method") == "staff" or cur.get("text_hash") == nc_key(text)):
            return cur
        sc = await extract_scope(nc)
        await db.ncs.update_one({"_id": nc["_id"]}, {"$set": {"scope": sc}})
        return sc
    except Exception as e:
        log.warning("scope extraction skipped: %s", type(e).__name__)
        return None


def gate_reason(scope: Optional[dict], cov: dict) -> Optional[str]:
    """Blocks AUTOMATIC approval only (routes to employee review). Never rejects or requests evidence by itself."""
    if not scope:
        return None
    if cov["status"] == "complete":
        return None
    if cov["status"] == "incomplete":
        return f"Location coverage incomplete: {cov['reason']} (missing: {', '.join(cov['missing'][:6])})"
    multi = scope.get("scope_status") != "confirmed" or (scope.get("expected_count") or 0) > 1
    return f"Location coverage unverified: {cov['reason']}" if multi else None


@router.get("/ncs/{nc_id}/scope")
async def get_scope(nc_id: str, u: User = Depends(staff)):
    from server import get_nc
    await get_nc(nc_id, u)
    nc = await db.ncs.find_one({"_id": oid(nc_id)}, {"scope": 1})
    files = await db.files.find({"nc_id": nc_id}, {"kind": 1, "status": 1, "pdf_include": 1, "location_id": 1}).to_list(5000)
    return {"enabled": await enabled(), "scope": nc.get("scope"), "coverage": coverage(nc.get("scope"), files)}


@router.post("/ncs/{nc_id}/scope/extract")
async def run_extract(nc_id: str, u: User = Depends(staff)):
    from server import get_nc
    await get_nc(nc_id, u)
    if not await enabled():
        raise HTTPException(409, "Location-aware scope is not enabled")
    nc = await db.ncs.find_one({"_id": oid(nc_id)})
    sc = await extract_scope(nc)
    await db.ncs.update_one({"_id": nc["_id"]}, {"$set": {"scope": sc}})
    await audit("nc.scope_extracted", u.id, f"nc:{nc_id}", {"scope_type": sc["scope_type"], "expected_count": sc["expected_count"], "status": sc["scope_status"]})
    files = await db.files.find({"nc_id": nc_id}, {"kind": 1, "status": 1, "pdf_include": 1, "location_id": 1}).to_list(5000)
    return {"enabled": True, "scope": sc, "coverage": coverage(sc, files)}
