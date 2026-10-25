"""Phase 4 — smart PDF photo plan (location -> stage), built ONLY from stored metadata (Phase 1 scope, Phase 2 tags,
Phase 3 coverage rules). No AI calls. Disabled unless Settings.smart_pdf_enabled is true; if any candidate photo has no
classification the caller keeps the existing PDF behaviour.

Rules:
- No per-NC photo cap.
- Staff controls win: pdf_include False = never shown; pdf_include True = always shown (never dropped as a duplicate);
  staff stage labels (pre/mid/final) override the AI stage.
- A photo is left out ONLY when Phase 2 marked it a duplicate (same location/group + same component + same stage + near-identical
  image) AND the photo it duplicates is itself shown. Staff "not a duplicate" clears the mark (handled in Phase 2 metadata).
- Different locations are never merged; untagged photos are kept (in an "other photographs" section), never excluded.
- Captions use only stored labels (location, component, stage). Nothing about methods, measurements or compliance is added.
"""
from typing import List, Optional, Tuple

from coverage import NOUN, needs_verification, verify

ORDER = ("before", "during", "after", "verification", "uncertain")
STAFF_STAGE = {"pre": "before", "mid": "during", "final": "after"}
HEAD = {"before": "BEFORE REMEDIATION", "during": "DURING REMEDIATION", "after": "COMPLETED WORK", "verification": "MEASUREMENT / TEST EVIDENCE",
        "uncertain": "SUPPORTING PHOTOGRAPHS"}
CAP = {"before": "before remediation", "during": "during remediation", "after": "completed", "verification": "measurement / test",
       "uncertain": ""}


def usable(photos: List[dict]) -> bool:
    return bool(photos) and all(isinstance(f.get("classification"), dict) for f in photos)


def _stage(f: dict) -> str:
    c = f.get("classification") or {}
    if (c.get("staff") or {}).get("stage") in ORDER:  # Phase 5 staff stage correction wins
        return c["staff"]["stage"]
    if f.get("stage_source") == "staff" and f.get("stage") in STAFF_STAGE:
        return STAFF_STAGE[f["stage"]]
    st = c.get("stage")
    return st if st in ORDER else "uncertain"


def _caption(label: Optional[str], f: dict, st: str) -> str:
    comp = ((f.get("classification") or {}).get("component") or "").strip()
    parts = [p for p in (label, comp[:1].upper() + comp[1:] if comp else "", CAP[st]) if p]
    return " - ".join(parts)[:90]


def plan(nc: dict, photos: List[dict], scope: Optional[dict]) -> Tuple[List[Tuple[str, List[Tuple[dict, str]]]], str]:
    """-> ([(heading, [(file, caption)])], coverage_note). photos = candidate photos (superseded / staff-excluded already removed)."""
    shown_ids = {str(f["_id"]) for f in photos}
    keep = []
    for f in photos:
        c = f["classification"]
        dup = c.get("dup_of")
        if dup and f.get("pdf_include") is not True and dup in shown_ids and dup != str(f["_id"]):
            continue  # confident duplicate of a photo that is shown
        keep.append(f)
    locs = (scope or {}).get("locations") or []
    single = (scope or {}).get("expected_count") == 1
    label = {l["id"]: l["label"] for l in locs}
    sections: dict = {}
    for f in keep:
        c = f["classification"]
        lid = c.get("location_id")
        key = ("loc", lid) if lid else (("grp", c["location_group"]) if c.get("location_group") else ("other", None))
        sections.setdefault(key, []).append(f)
    loc_order = {l["id"]: i for i, l in enumerate(locs)}
    grp_n = {k: i + 1 for i, k in enumerate(sorted({k for k in sections if k[0] == "grp"}, key=lambda k: min(str(f.get("captured_at") or f.get("server_received_at") or "") for f in sections[k])))}

    def sort_key(k):
        return (0, loc_order.get(k[1], 999), str(k[1])) if k[0] == "loc" else ((1, grp_n[k], "") if k[0] == "grp" else (2, 0, ""))
    multi_heads = not single and len(sections) > 1
    out = []
    for k in sorted(sections, key=sort_key):
        if k[0] == "loc":
            name = label.get(k[1]) or sections[k][0]["classification"].get("location_label") or "Location"
        elif k[0] == "grp":
            name = f"Location not identified ({grp_n[k]})" if multi_heads else None
        else:
            name = "Other photographs" if multi_heads else None
        cap_label = name if k[0] == "loc" and not single else None
        by_stage: dict = {}
        for f in sorted(sections[k], key=lambda f: str(f.get("captured_at") or f.get("server_received_at") or "")):
            by_stage.setdefault(_stage(f), []).append(f)
        for st in ORDER:
            if st in by_stage:
                head = HEAD[st] if not name or single else f"{name.upper()} - {HEAD[st]}"
                out.append((head, [(f, _caption(cap_label, f, st)) for f in by_stage[st]]))
    return out, coverage_note(nc, scope, photos)


def scope_for(nc: dict) -> Optional[dict]:
    """Stored scope, else the rule-based reading of the NC wording (no AI, nothing stored)."""
    if nc.get("scope"):
        return nc["scope"]
    from scope import merge, rule_extract
    t = " ".join(x for x in [nc.get("original_text") or "", nc.get("description") or "", nc.get("required_work") or ""] if x)
    return {**merge(rule_extract(t), None, t, None), "method": "rules_preview"} if t.strip() else None


NOT_ASSESSED = "Evidence coverage has not been assessed."
NOT_CONFIRMED = "Evidence coverage has not been confirmed."


def coverage_note(nc: dict, scope: Optional[dict], photos: List[dict]) -> str:
    """One factual line (Location-Aware Evidence ON, every PDF path). Complete single-location -> no line.
    Untagged photos -> 'not assessed' (never a shortfall). Missing locations are only named from Phase 3 rules."""
    if not scope or any(not isinstance(f.get("classification"), dict) for f in photos):
        return NOT_ASSESSED
    cov = verify(scope, [{**f, "status": "submitted"} for f in photos], needs_verification(nc))
    if cov["status"] == "unverified":
        return NOT_CONFIRMED
    exp = cov["expected"]
    if exp == 1:
        return "" if cov["status"] == "complete" else "Evidence of the completed work has not been provided."
    one, many = NOUN.get(scope.get("scope_type"), ("location", "locations"))
    if cov["status"] == "complete":
        return f"Evidence shown for all {exp} {many}."
    names = f" Outstanding: {', '.join(cov['missing'][:12])}." if cov.get("missing") else ""
    return f"Evidence of completed work shown for {cov['max_possible']} of {exp} {many}.{names}"
