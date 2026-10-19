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


def decorative_reason(data: bytes) -> Optional[str]:
    """'banner' for strips/headers, 'graphic' for flat-colour logos and icons; None for real photos."""
    img = Image.open(io.BytesIO(data)).convert("RGB")
    w, h = img.size
    if max(w, h) / max(1, min(w, h)) > 3:
        return "banner"
    px = [(r // 16, g // 16, b // 16) for r, g, b in img.resize((64, 64)).getdata()]
    counts = Counter(px)
    top4 = sum(c for _, c in counts.most_common(4)) / len(px)
    if top4 > 0.6 and len(counts) < 60:
        return "graphic"
    return None


def classify(items: List[Tuple[str, int, bytes]]) -> dict:
    """items: (key, page, bytes). Returns {key: reason} for images to exclude (decorative, or a running header/footer)."""
    out, hashes = {}, []
    for key, page, data in items:
        try:
            reason = decorative_reason(data)
            h = hex_hash(data)
        except Exception:
            continue
        if reason:
            out[key] = reason
        hashes.append((key, page, h))
    pages = len({p for _, p, _ in hashes})
    for key, page, h in hashes:  # running header/footer: on 3+ pages AND at least half the pages with images
        n = len({page} | {p for _, p, oh in hashes if close(h, oh)})
        if key not in out and n >= 3 and n * 2 >= pages:
            out[key] = "repeated"
    return out


async def clean_project_report_photos(pid: str) -> int:
    """Re-applies the filter to a project's existing PDF photos. Files stay in storage; manual choices are kept."""
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
    flagged = classify(uniq)
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


async def find_duplicate(draft: dict) -> Optional[dict]:
    ref, pc = norm_ref(draft.get("ref")), norm_ref(draft.get("postcode"))
    best = None
    async for p in db.projects.find({}, {"ref": 1, "address": 1, "postcode": 1}):
        hit = {"project_id": str(p["_id"]), "ref": p["ref"], "address": p.get("address", ""), "postcode": p.get("postcode", "")}
        if ref and norm_ref(p["ref"]) == ref:
            return {**hit, "reason": "Same project reference"}
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
