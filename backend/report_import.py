"""TrustMark report import helpers: logo/decoration filter, duplicate project detection, NC matching."""
import difflib
import io
import logging
import re
from collections import Counter
from typing import List, Optional, Tuple

from PIL import Image

from core import db
from storage import storage

log = logging.getLogger("report_import")

MATCH, AMBIGUOUS = 0.85, 0.5  # NC text similarity thresholds


def hex_hash(data: bytes) -> str:
    """256-bit difference hash: fine enough that different site photos with similar overlays don't collide."""
    img = Image.open(io.BytesIO(data)).convert("L").resize((17, 16))
    px = list(img.getdata())
    v = sum(1 << i for i in range(256) if px[(i // 16) * 17 + i % 16] > px[(i // 16) * 17 + i % 16 + 1])
    return f"{v:064x}"


def close(a: str, b: str, dist: int = 16) -> bool:
    return bin(int(a, 16) ^ int(b, 16)).count("1") <= dist


def photo_like(data: bytes) -> bool:
    """True unless the image sits on a dominant pure white/black background like a printed logo or header.
    Photographs (incl. flat walls, annotated defect and measurement photos) never qualify as decorations."""
    img = Image.open(io.BytesIO(data)).convert("RGB")
    px = [(r // 16, g // 16, b // 16) for r, g, b in img.resize((64, 64)).getdata()]
    (top, n), = Counter(px).most_common(1)
    background = all(v >= 14 for v in top) or all(v <= 1 for v in top)
    return not (background and n / len(px) > 0.5)


def aspect(data: bytes) -> float:
    w, h = Image.open(io.BytesIO(data)).size
    return round(w / max(1, h), 3)


def same_shape(a: float, b: float) -> bool:
    return abs(a - b) / max(a, b) < 0.12


def classify(items: List[Tuple[str, int, bytes]], known: Optional[List[Tuple[str, float]]] = None) -> Tuple[dict, List[Tuple[str, float]]]:
    """items: (key, page, bytes). Returns ({key: reason}, new_logo_hashes).

    Conservative by design - an image is only hidden when it is a page decoration:
      * 'repeated'  : the same non-photographic image on 3+ pages and at least half the pages with images
                      (running logo / header / footer), or
      * 'known_logo': it matches a logo fingerprint learned from an earlier report.
    Anything appearing once - technical diagrams, annotated defect photos, measurement close-ups - is kept,
    whatever its shape or colours. Staff can still hide/restore manually.
    """
    known = known or []
    hashes = []
    for key, page, data in items:
        try:
            hashes.append((key, page, hex_hash(data), photo_like(data), aspect(data)))
        except Exception:
            continue
    pages = len({x[1] for x in hashes})
    out, learned = {}, []
    for key, page, h, photo, ar in hashes:
        if photo:
            continue
        # strict match + same shape: plain walls/ceilings also have near-flat hashes, so shape must agree too
        if any(close(h, k, 10) and same_shape(ar, ka) for k, ka in known):
            out[key] = "known_logo"
            continue
        n = len({page} | {p for _, p, oh, _, oa in hashes if close(h, oh) and same_shape(ar, oa)})
        if n >= 3 and n * 2 >= pages:
            out[key] = "repeated"
            if not any(close(h, x) for x, _ in learned):
                learned.append((h, ar))
    return out, learned


async def known_logos() -> List[Tuple[str, float]]:
    return [(d["_id"], d["aspect"]) for d in await db.decorations.find({"aspect": {"$exists": True}}).to_list(500)]


async def learn_logos(hashes: List[Tuple[str, float]]):
    """Logo fingerprints learned from running headers, so a logo printed once in a later report is still skipped."""
    for h, ar in hashes:
        await db.decorations.update_one({"_id": h}, {"$set": {"aspect": ar}, "$setOnInsert": {"learned_at": __import__("core").now_iso()}}, upsert=True)


async def clean_project_report_photos(pid: str, reset: bool = False) -> int:
    """Re-applies the filter to a project's existing PDF photos. Files stay in storage; manual choices are kept.
    reset=True first un-hides earlier automatic exclusions (used when the rules change)."""
    if reset:
        await db.files.update_many({"project_id": pid, "kind": "trustmark", "source": "pdf_import", "exclude_reason": {"$nin": ["manual", None]}},
                                   {"$set": {"excluded": False, "exclude_reason": None}})
    files = await db.files.find({"project_id": pid, "kind": "trustmark", "source": "pdf_import"}).to_list(2000)
    items = []
    for f in files:
        path = f.get("paths", {}).get("display") or f.get("paths", {}).get("original")
        if not path:
            continue
        try:
            data, _ = await storage.aget(path)
        except Exception:
            log.warning("clean skip %s", f["_id"])
            continue
        m = re.search(r"p(\d+)", f.get("original_name", ""))
        items.append((str(f["_id"]), int(m.group(1)) if m else 0, data))
        if not f.get("dhash"):
            try:
                await db.files.update_one({"_id": f["_id"]}, {"$set": {"dhash": hex_hash(data)}})
            except Exception:
                pass
    # the same report page attached to several NCs is one image, not a repeat
    seen, uniq = set(), []
    for key, page, data in items:
        k = (page, hex_hash(data))
        if k not in seen:
            seen.add(k)
            uniq.append((key, page, data))
    flagged, learned = classify(uniq, await known_logos())
    await learn_logos(learned)
    by_hash = {}
    for key, page, data in items:
        by_hash.setdefault((page, hex_hash(data)), []).append(key)
    n = 0
    for (page, h), keys in by_hash.items():
        reason = next((flagged[k] for k in keys if k in flagged), None)
        if not reason:
            continue
        for k in keys:
            f = next(x for x in files if str(x["_id"]) == k)
            if f.get("manual_keep") or f.get("excluded"):
                continue
            await db.files.update_one({"_id": f["_id"]}, {"$set": {"excluded": True, "exclude_reason": reason}})
            n += 1
    return n


def norm_ref(r: Optional[str]) -> str:
    return re.sub(r"[^A-Z0-9]", "", (r or "").upper())


def norm_text(t: Optional[str]) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", (t or "").lower()))


def similarity(a: Optional[str], b: Optional[str]) -> float:
    """Max of sequence ratio and (slightly discounted) word containment, so reworded/shortened NCs still pair up."""
    a, b = norm_text(a), norm_text(b)
    if not a or not b:
        return 0.0
    ta, tb = set(a.split()), set(b.split())
    return max(difflib.SequenceMatcher(None, a, b).ratio(), 0.9 * len(ta & tb) / min(len(ta), len(tb)))


def nc_similarity(d: dict, e: dict) -> float:
    return max(similarity(x, y) for x in (d.get("original_text"), d.get("description")) for y in (e.get("original_text"), e.get("description")))


def _house_no(addr: Optional[str]) -> Optional[str]:
    """First house/flat number in an address ("280 Dallow Road" -> "280", "Flat 2b, ..." -> "2B")."""
    m = re.search(r"\b(\d+[A-Za-z]?)\b", addr or "")
    return m.group(1).upper() if m else None


async def find_duplicate(draft: dict) -> Optional[dict]:
    ref, pc = norm_ref(draft.get("ref")), norm_ref(draft.get("postcode"))
    num = _house_no(draft.get("address"))
    best = None
    async for p in db.projects.find({}, {"ref": 1, "address": 1, "postcode": 1, "deleted": 1}):
        hit = {"project_id": str(p["_id"]), "ref": p["ref"], "address": p.get("address", ""), "postcode": p.get("postcode", ""),
               "deleted": bool(p.get("deleted"))}
        if ref and norm_ref(p["ref"]) == ref:
            return {**hit, "reason": "Same project reference"}
        other = _house_no(p.get("address"))
        if num and other and num != other:
            continue  # neighbouring properties on the same street/postcode are different jobs
        if not best and pc and norm_ref(p.get("postcode")) == pc and similarity(p.get("address"), draft.get("address")) >= 0.6:
            best = {**hit, "reason": "Same postcode and similar address"}
    return best


def match_ncs(draft_ncs: List[dict], existing: List[dict]) -> List[dict]:
    """Greedy best-first pairing of report NCs to existing NCs by wording similarity."""
    pairs = sorted(((nc_similarity(d, e), i, j)
                    for i, d in enumerate(draft_ncs) for j, e in enumerate(existing)), reverse=True)
    used_d, used_e, best = set(), set(), {}
    for score, i, j in pairs:
        if score < AMBIGUOUS or i in used_d or j in used_e:
            continue
        used_d.add(i)
        used_e.add(j)
        best[i] = (j, score)
    out = []
    for i, d in enumerate(draft_ncs):
        j, score = best.get(i, (None, 0.0))
        e = existing[j] if j is not None else None
        out.append({"index": i, "draft": d, "score": round(score, 2),
                    "kind": "new" if e is None else ("match" if score >= MATCH else "ambiguous"),
                    "nc": {"id": str(e["_id"]), "number": e["number"], "original_text": e.get("original_text") or e.get("description"),
                           "required_work": e.get("required_work", ""), "status": e["status"]} if e else None})
    return out
