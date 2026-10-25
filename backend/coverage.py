"""Phase 3 — evidence coverage verification (Steps D/E of memory/AI_KNOWLEDGE_BASE_PLAN.md).
Disabled unless Settings.coverage_check_enabled is true. Combines Phase 1 scope (ncs.scope) with Phase 2 photo tags
(files.classification) and decides complete | incomplete | unverified. It never approves anything by itself.

Counting rules (deterministic, no AI):
- A location is DEMONSTRATED only by a relevant photo whose stage is "after" or "verification". Before/during photos show the
  location, not the completed work.
- Photos of one location count once: same confirmed location_id, or same Phase 2 location_group. Duplicates add nothing.
- Untagged photos (location uncertain) may belong to any location, so they can only raise the MAXIMUM possible coverage.
  complete   = every expected location demonstrated by tagged evidence (+ verification evidence when the NC needs it)
  incomplete = even the most generous reading of the evidence covers fewer locations than the confirmed expected count
  unverified = anything in between, unknown expected count, or no classification available
"""
import re
from typing import List, Optional

from fastapi import APIRouter, Depends

from auth import require
from core import User, db, get_settings

router = APIRouter(prefix="/api")
staff = require("admin", "employee")
DONE = ("after", "verification")
VERIF_RE = re.compile(r"\b(test(?:ed|ing|s)?|certificat\w*|commission\w*|measur\w*|clearances?|readings?|calibrat\w*|flow rates?|air ?flow)\b", re.I)
NOUN = {"window": ("window", "windows"), "habitable_room": ("room", "rooms"), "wet_room": ("room", "rooms"), "elevation": ("elevation", "elevations"),
        "junction": ("junction", "junctions"), "component": ("location", "locations")}


def needs_verification(nc: dict) -> bool:
    return bool(VERIF_RE.search(" ".join(x for x in [nc.get("original_text") or "", nc.get("description") or "", nc.get("required_work") or ""] if x)))


def verify(scope: Optional[dict], files: List[dict], verification_required: bool = False) -> dict:
    photos = [f for f in files if f.get("kind") == "photo" and f.get("status") in ("draft", "submitted") and f.get("pdf_include") is not False]
    base = {"covered": None, "max_possible": None, "expected": None, "missing": [], "not_completed": [], "verification_required": verification_required,
            "verification_found": False}
    if not scope or scope.get("scope_status") != "confirmed" or not scope.get("expected_count"):
        return {**base, "status": "unverified", "reason": "Number of affected locations could not be confirmed"}
    exp, locs = scope["expected_count"], scope.get("locations") or []
    cls = [(f, f.get("classification")) for f in photos]
    if not photos:
        return {**base, "status": "incomplete", "reason": "No evidence photos", "covered": 0, "max_possible": 0, "expected": exp,
                "missing": [l["label"] for l in locs]}
    if any(c is None for _, c in cls):
        return {**base, "status": "unverified", "reason": "Photos not yet classified by location", "expected": exp}
    use = [(f, c) for f, c in cls if c.get("relevant") is not False and not c.get("dup_of")]
    verif = any(c.get("stage") == "verification" for _, c in use)
    demonstrated, shown, units = set(), set(), {}
    for f, c in use:
        lid, st = c.get("location_id"), c.get("stage")
        if lid:
            (demonstrated if st in DONE else shown).add(lid)
        elif st in DONE + ("uncertain",):  # untagged: could be any location
            units.setdefault(c.get("location_group") or f"single-{f.get('_id')}", True)
    shown -= demonstrated
    if exp == 1:
        done = any(c.get("stage") in DONE for _, c in use)
        r = {**base, "expected": 1, "covered": int(done), "max_possible": 1, "verification_found": verif}
        if not done:
            return {**r, "status": "unverified", "reason": "No photo clearly shows the completed work"}
        if verification_required and not verif:
            return {**r, "status": "unverified", "reason": "Required test, measurement or certificate evidence not identified"}
        return {**r, "status": "complete", "reason": ""}
    known_ids = {l["id"] for l in locs}
    demonstrated &= known_ids
    max_possible = min(exp, len(demonstrated) + len(units))
    missing = [l["label"] for l in locs if l["id"] not in demonstrated]
    not_completed = [l["label"] for l in locs if l["id"] in shown]
    r = {**base, "expected": exp, "covered": len(demonstrated), "max_possible": max_possible, "not_completed": not_completed,
         "verification_found": verif}
    if not missing:
        if verification_required and not verif:
            return {**r, "status": "unverified", "reason": "Required test, measurement or certificate evidence not identified"}
        return {**r, "status": "complete", "reason": ""}
    if max_possible < exp:
        # names are only reliable when no untagged photo could belong to one of the "missing" locations
        return {**r, "status": "incomplete", "missing": missing if not units else [], "missing_count": exp - max_possible,
                "reason": f"Completed-work evidence for at most {max_possible} of {exp} locations"}
    return {**r, "status": "unverified", "reason": f"Evidence for {len(demonstrated)} of {exp} locations confirmed; other photos not matched to locations"}


def installer_request(scope: dict, cov: dict) -> str:
    """Plain wording for the existing 'More evidence required' flow."""
    one, many = NOUN.get(scope.get("scope_type"), ("location", "locations"))
    n, exp = cov.get("missing_count") or len(cov.get("missing") or []), cov.get("expected")
    what = one if n == 1 else many
    if cov.get("missing"):
        return (f"Please add a clear photo of the completed work for {n} more {what} ({', '.join(cov['missing'][:10])}), "
                f"so all {exp} {many} in this NC are evidenced.")
    return (f"Your photos show completed work for at most {cov.get('max_possible')} of the {exp} {many} in this NC. Please add a clear "
            f"photo of the completed work at each of the remaining {n} {what}, showing where each one is.")


async def evaluate(nc_id: str) -> dict:
    """Assessment hook: makes sure scope + photo tags exist (both are metadata only), then verifies coverage and stores it."""
    from photo_class import classify_nc
    from scope import ensure_scope
    from core import oid
    sc = await ensure_scope(nc_id, force=True)
    await classify_nc(nc_id)  # synchronous here: the decision needs the tags; AI unavailable => untagged => unverified
    nc = await db.ncs.find_one({"_id": oid(nc_id)})
    files = await db.files.find({"nc_id": nc_id, "kind": "photo"}, {"kind": 1, "status": 1, "pdf_include": 1, "classification": 1}).to_list(5000)
    cov = verify(sc, files, needs_verification(nc))
    await db.ncs.update_one({"_id": nc["_id"]}, {"$set": {"coverage": cov}})
    return {"scope": sc, "coverage": cov}


@router.get("/ncs/{nc_id}/coverage")
async def get_coverage(nc_id: str, u: User = Depends(staff)):
    """Read-only, staff only: recomputed from stored scope + tags (no AI call, nothing written)."""
    from server import get_nc
    from core import oid
    await get_nc(nc_id, u)
    nc = await db.ncs.find_one({"_id": oid(nc_id)})
    files = await db.files.find({"nc_id": nc_id, "kind": "photo"}, {"kind": 1, "status": 1, "pdf_include": 1, "classification": 1}).to_list(5000)
    return {"enabled": bool((await get_settings()).coverage_check_enabled), "coverage": verify(nc.get("scope"), files, needs_verification(nc))}
