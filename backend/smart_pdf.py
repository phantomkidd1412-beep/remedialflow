"""Smart PDF photo plan (location -> stage), built ONLY from stored metadata (Phase 1 scope, Phase 2 tags, staff corrections).
No AI calls. Used when Settings.smart_pdf_enabled is true and every photo is tagged; otherwise the existing layout is used.

Concise-pack rules (shared with the existing layout, see server.pdf_photo_groups):
- ONE photo per distinct evidence step: per (location, stage) for confirmed multi-location NCs, per stage otherwise.
  The caller picks the clearest candidate (evidence_pack.best). Timestamps stay burned into the stamped copy.
- Staff controls win: pdf_include False = never shown; staff-picked photos (pdf_include True) bypass this planner entirely;
  staff stage corrections override the AI stage.
- Confirmed duplicates are never candidates. Different confirmed locations are never merged.
- External wording only: stage headings, location names and short factual captions. No coverage status, confidence,
  uncertainty or review wording is ever printed (that stays in the staff review panel).
"""
from typing import List, Optional, Tuple

ORDER = ("before", "during", "after", "verification", "uncertain")
STAFF_STAGE = {"pre": "before", "mid": "during", "final": "after"}
HEAD = {"before": "BEFORE REMEDIATION", "during": "DURING REMEDIATION", "after": "COMPLETED WORK", "verification": "MEASUREMENT / TEST EVIDENCE",
        "uncertain": "SUPPORTING PHOTOGRAPH"}

Slot = Tuple[List[dict], str]  # (candidate files - one is printed, caption)


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


def _t(f: dict) -> str:
    return str(f.get("captured_at") or f.get("server_received_at") or "")


def _component(fs: List[dict]) -> str:
    comp = next(((f.get("classification") or {}).get("component") or "" for f in fs if (f.get("classification") or {}).get("component")), "").strip()
    return comp[:1].upper() + comp[1:60] if comp else ""


def plan(nc: dict, photos: List[dict], scope: Optional[dict]) -> List[Tuple[str, List[Slot]]]:
    """-> [(heading, [slot])]. photos = candidates (superseded / staff-excluded already removed)."""
    ids = {str(f["_id"]) for f in photos}
    keep = [f for f in photos if not ((f["classification"].get("dup_of") or "") in ids and f["classification"]["dup_of"] != str(f["_id"]))]
    locs = (scope or {}).get("locations") or []
    exp = (scope or {}).get("expected_count")
    multi = bool(exp and exp > 1)
    label = {l["id"]: l["label"] for l in locs}
    order = {l["id"]: i for i, l in enumerate(locs)}
    located: dict = {}    # (loc_id, stage) -> files
    unlocated: dict = {}  # stage -> {group: files}
    for f in sorted(keep, key=_t):
        c, st = f["classification"], _stage(f)
        lid = c.get("location_id") if multi and c.get("location_id") in label else None
        if lid:
            located.setdefault((lid, st), []).append(f)
        else:
            # one slot per AI location group only when the NC has several locations; otherwise one slot per stage
            g = (c.get("location_group") or "_") if multi and exp else "_"
            unlocated.setdefault(st, {}).setdefault(g, []).append(f)
    out = []
    for (lid, st), fs in sorted(located.items(), key=lambda kv: (order[kv[0][0]], ORDER.index(kv[0][1]))):
        cap = _component(fs) if st == "verification" else ""  # the location is already in the heading
        out.append((f"{label[lid].upper()} - {HEAD[st]}", [(fs, cap)]))
    for st in ORDER:
        if st in unlocated:
            out.append((HEAD[st], [(fs, _component(fs) if st == "verification" else "") for fs in unlocated[st].values()]))
    return out


def scope_for(nc: dict) -> Optional[dict]:
    """Stored scope, else the rule-based reading of the NC wording (no AI, nothing stored)."""
    if nc.get("scope"):
        return nc["scope"]
    from scope import merge, rule_extract
    t = " ".join(x for x in [nc.get("original_text") or "", nc.get("description") or "", nc.get("required_work") or ""] if x)
    return {**merge(rule_extract(t), None, t, None), "method": "rules_preview"} if t.strip() else None
